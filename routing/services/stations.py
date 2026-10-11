"""In-memory spatial index of fuel stations and route-corridor search.

Stations are loaded once per process into numpy arrays and bucketed into a
uniform lat/lon grid. Finding the stations along a 2,800-mile route then costs
a few thousand dict lookups plus one vectorised distance computation; it
never touches the database on the request path.
"""

import hashlib
import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from routing.exceptions import PlannerError

from .geo import EARTH_RADIUS_MILES

logger = logging.getLogger(__name__)

CELL_DEG = 0.25  # ~17 mi of latitude; must exceed the corridor radius in degrees
MILES_PER_DEG_LAT = math.pi * EARTH_RADIUS_MILES / 180


@dataclass(frozen=True)
class CorridorStation:
    index: int  # position in StationIndex arrays
    mile: float  # distance along the route where the station is reached
    offset: float  # distance from the route, miles
    price: float


class StationsNotLoaded(PlannerError):
    status_code = 503
    default_code = 'stations_not_loaded'


class StationIndex:
    def __init__(self, records: list[dict]):
        """`records`: dicts with opis_id, name, address, city, state, price, lat, lon."""
        self.records = list(records)
        self.lat = np.array([r['lat'] for r in self.records], dtype=float)
        self.lon = np.array([r['lon'] for r in self.records], dtype=float)
        self.price = np.array([r['price'] for r in self.records], dtype=float)
        cells = defaultdict(list)
        for i, (la, lo) in enumerate(zip(self.lat, self.lon, strict=True)):
            cells[(math.floor(la / CELL_DEG), math.floor(lo / CELL_DEG))].append(i)
        self.cells = {k: np.array(v, dtype=np.int64) for k, v in cells.items()}
        # Fingerprint of the station data: cached routes hold station indexes and prices,
        # so reloading stations (e.g. new prices) must invalidate them.
        digest = hashlib.sha1()
        for r in self.records:
            digest.update(f'{r["opis_id"]}:{r["price"]}:{r["lat"]}:{r["lon"]};'.encode())
        self.version = digest.hexdigest()[:12]

    def __len__(self):
        return len(self.records)

    def corridor(
        self, route_lats: np.ndarray, route_lons: np.ndarray, route_miles: np.ndarray, radius_miles: float
    ) -> list[CorridorStation]:
        """Stations within `radius_miles` of the (densely resampled) route, sorted by mile."""
        if not len(self.records):
            return []
        max_lat = float(np.max(np.abs(route_lats)))
        lon_reach = radius_miles / (MILES_PER_DEG_LAT * max(math.cos(math.radians(max_lat)), 0.05))
        lat_cells = math.ceil((radius_miles / MILES_PER_DEG_LAT) / CELL_DEG)
        lon_cells = math.ceil(lon_reach / CELL_DEG)

        # Group route sample points by grid cell.
        ci = np.floor(route_lats / CELL_DEG).astype(np.int64)
        cj = np.floor(route_lons / CELL_DEG).astype(np.int64)
        by_cell = defaultdict(list)
        for k, key in enumerate(zip(ci.tolist(), cj.tolist(), strict=True)):
            by_cell[key].append(k)

        sample_parts, station_parts = [], []
        for (i, j), samples in by_cell.items():
            nearby = [
                self.cells[c]
                for di in range(-lat_cells, lat_cells + 1)
                for dj in range(-lon_cells, lon_cells + 1)
                if (c := (i + di, j + dj)) in self.cells
            ]
            if not nearby:
                continue
            st = np.concatenate(nearby)
            sm = np.asarray(samples, dtype=np.int64)
            sample_parts.append(np.repeat(sm, len(st)))
            station_parts.append(np.tile(st, len(sm)))
        if not sample_parts:
            return []

        s_idx = np.concatenate(sample_parts)
        t_idx = np.concatenate(station_parts)
        # Equirectangular approximation: < 0.1% error at corridor scale, far cheaper than haversine.
        dy = (self.lat[t_idx] - route_lats[s_idx]) * MILES_PER_DEG_LAT
        dx = (self.lon[t_idx] - route_lons[s_idx]) * MILES_PER_DEG_LAT * np.cos(np.radians(route_lats[s_idx]))
        dist = np.hypot(dx, dy)
        keep = dist <= radius_miles
        s_idx, t_idx, dist = s_idx[keep], t_idx[keep], dist[keep]
        if not len(t_idx):
            return []

        # For each station keep the route sample closest to it (ties: the earliest along the route).
        order = np.lexsort((route_miles[s_idx], dist, t_idx))
        t_sorted = t_idx[order]
        first = np.concatenate(([True], t_sorted[1:] != t_sorted[:-1]))
        best = order[first]

        result = [
            CorridorStation(index=int(t), mile=float(route_miles[s]), offset=float(d), price=float(self.price[t]))
            for t, s, d in zip(t_idx[best], s_idx[best], dist[best], strict=True)
        ]
        result.sort(key=lambda c: (c.mile, c.price))
        return result


_index = None
_lock = threading.Lock()


def _load_records():
    from routing.models import FuelStation

    rows = FuelStation.objects.values_list(
        'opis_id', 'name', 'address', 'city', 'state', 'price', 'latitude', 'longitude'
    )
    return [
        {
            'opis_id': r[0],
            'name': r[1],
            'address': r[2],
            'city': r[3],
            'state': r[4],
            'price': float(r[5]),
            'lat': r[6],
            'lon': r[7],
        }
        for r in rows
    ]


def get_index() -> StationIndex:
    """Process-wide singleton, built lazily on first use from the FuelStation table."""
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                records = _load_records()
                if not records:  # not cached, so it is picked up as soon as stations are loaded
                    raise StationsNotLoaded('No fuel stations loaded. Run `python manage.py load_stations`.')
                _index = StationIndex(records)
                logger.info('Station index ready: %d stations, %d grid cells', len(_index), len(_index.cells))
    return _index


def reset_index() -> None:
    global _index
    with _lock:
        _index = None
