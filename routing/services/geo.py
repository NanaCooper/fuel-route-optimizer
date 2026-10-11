"""Vectorised geometry helpers (numpy). All distances are in statute miles."""

import json
from functools import cache
from pathlib import Path

import numpy as np

BOUNDARY_FILE = Path(__file__).resolve().parent.parent / 'data' / 'us_boundary.json'
EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance; accepts scalars or broadcastable numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cumulative_miles(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Cumulative distance along a polyline, starting at 0."""
    seg = haversine(lats[:-1], lons[:-1], lats[1:], lons[1:])
    return np.concatenate(([0.0], np.cumsum(seg)))


def resample(lats: np.ndarray, lons: np.ndarray, step_miles: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points every `step_miles` along the polyline -> (lats, lons, miles)."""
    cum = cumulative_miles(lats, lons)
    total = cum[-1]
    if total == 0:
        return lats[:1], lons[:1], np.zeros(1)
    marks = np.append(np.arange(0.0, total, step_miles), total)
    # Linear interpolation in lat/lon is accurate at sub-mile segment lengths.
    return np.interp(marks, cum, lats), np.interp(marks, cum, lons), marks


def thin(coords: list, max_points: int) -> list:
    """Downsample a coordinate list to at most `max_points`, always keeping both ends."""
    n = len(coords)
    if n <= max_points:
        return coords
    idx = np.unique(np.linspace(0, n - 1, max_points).round().astype(int))
    return [coords[i] for i in idx]


@cache
def _us_polygons():
    """[(bbox, ring ndarray)] from the Census US outline (states + territories, 1:20M)."""
    rings = json.loads(BOUNDARY_FILE.read_text(encoding='utf-8'))
    out = []
    for ring in rings:
        arr = np.asarray(ring, dtype=float)
        if not np.array_equal(arr[0], arr[-1]):
            arr = np.vstack([arr, arr[:1]])
        out.append(((arr[:, 0].min(), arr[:, 0].max(), arr[:, 1].min(), arr[:, 1].max()), arr))
    return out


def _in_ring(lon, lat, ring):
    """Even-odd ray casting, vectorised over the ring's edges."""
    x1, y1 = ring[:-1, 0], ring[:-1, 1]
    x2, y2 = ring[1:, 0], ring[1:, 1]
    crosses = (y1 > lat) != (y2 > lat)
    with np.errstate(divide='ignore', invalid='ignore'):
        x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
    return bool(np.count_nonzero(crosses & (lon < x_at)) % 2)


def in_usa(lat: float, lon: float, tolerance_miles: float = 3.0) -> bool:
    """True if the point is on US soil (or within `tolerance_miles` of the coastline/border polygon)."""
    pad_lat = tolerance_miles / 69.0
    pad_lon = tolerance_miles / (69.0 * max(np.cos(np.radians(lat)), 0.05))
    for (x0, x1, y0, y1), ring in _us_polygons():
        if not (x0 - pad_lon <= lon <= x1 + pad_lon and y0 - pad_lat <= lat <= y1 + pad_lat):
            continue
        if _in_ring(lon, lat, ring):
            return True
        # Coastal/border points just outside the 1:20M generalised outline.
        if tolerance_miles and np.min(haversine(lat, lon, ring[:, 1], ring[:, 0])) <= tolerance_miles:
            return True
    return False
