"""Orchestrates a trip plan: locations -> route (1 API call) -> corridor stations -> optimal stops.

The expensive, external part (the route and the stations along it) is cached per
start/finish pair. The optimisation itself takes a few milliseconds and runs on
every request, so changing `stop_cost` or `start_full` never costs another API call.
"""

import hashlib
import time

import numpy as np
from django.conf import settings
from django.core.cache import cache

from routing.exceptions import LocationError, PlannerError

from . import geocode, ors
from .geo import haversine, resample, thin
from .optimizer import InfeasibleRouteError, plan_fuel_stops
from .stations import get_index

CACHE_VERSION = 'v3'


def _cfg(name):
    return settings.FUEL_PLANNER[name]


def _route_cache_key(start: geocode.Location, finish: geocode.Location) -> str:
    # The station-data version is part of the key: cached corridors hold station indexes and prices.
    raw = (
        f'{start.lat:.4f},{start.lon:.4f}|{finish.lat:.4f},{finish.lon:.4f}|'
        f'{settings.ROUTING_PROFILE}|{_cfg("CORRIDOR_MILES")}|{get_index().version}'
    )
    return f'route:{CACHE_VERSION}:' + hashlib.sha1(raw.encode()).hexdigest()


def plan_trip(start: str, finish: str, start_full: bool = False, stop_cost: float | None = None) -> dict:
    """Return the JSON-serialisable trip plan. Raises PlannerError subclasses on failure."""
    t0 = time.perf_counter()
    start, calls_a = geocode.resolve(start)
    finish, calls_b = geocode.resolve(finish)
    api_calls = calls_a + calls_b

    if haversine(start.lat, start.lon, finish.lat, finish.lon) < 0.5:
        raise LocationError('Start and finish are the same place.', status_code=400, code='same_location')

    key = _route_cache_key(start, finish)
    route = cache.get(key)
    cached = route is not None
    if not cached:
        route = _prepare_route(ors.directions(start.point, finish.point))
        api_calls += 1
        cache.set(key, route)

    stop_cost = _cfg('STOP_COST_USD') if stop_cost is None else float(stop_cost)
    result = _optimise(start, finish, route, start_full, stop_cost)
    result['meta'] = {
        'stations_in_corridor': len(route['corridor']),
        'corridor_miles': _cfg('CORRIDOR_MILES'),
        'route_cached': cached,
        'external_api_calls': api_calls,
        'response_time_ms': round((time.perf_counter() - t0) * 1000, 1),
    }
    return result


def _prepare_route(route: dict) -> dict:
    """Reduce a raw routing response to what planning needs: distance, geometry, corridor stations."""
    coords = np.asarray(route['coordinates'], dtype=float)
    lons, lats = coords[:, 0], coords[:, 1]
    s_lat, s_lon, s_mile = resample(lats, lons, _cfg('ROUTE_SAMPLE_MILES'))

    # Scale our geometric mileage to the routing engine's road distance so mile markers line up.
    total_miles = route['distance_miles'] or float(s_mile[-1])
    if s_mile[-1] > 0:
        s_mile = s_mile * (total_miles / s_mile[-1])

    corridor = get_index().corridor(s_lat, s_lon, s_mile, _cfg('CORRIDOR_MILES'))
    geometry = thin(route['coordinates'], _cfg('MAX_RESPONSE_GEOMETRY_POINTS'))
    return {
        'distance_miles': total_miles,
        'duration_seconds': route['duration_seconds'],
        'geometry': [[round(x, 5), round(y, 5)] for x, y in geometry],
        'corridor': [(c.index, c.mile, c.offset, c.price) for c in corridor],
    }


def _optimise(
    start: geocode.Location, finish: geocode.Location, route: dict, start_full: bool, stop_cost: float
) -> dict:
    rng = _cfg('VEHICLE_RANGE_MILES')
    mpg = _cfg('MILES_PER_GALLON')
    corridor = route['corridor']
    total_miles = route['distance_miles']

    try:
        plan = plan_fuel_stops(
            [(mile, price) for _, mile, _, price in corridor],
            total_miles,
            rng,
            start_full=start_full,
            origin_reserve_miles=_cfg('ORIGIN_RESERVE_MILES'),
            stop_penalty=stop_cost * mpg,  # optimiser works in price x miles
        )
    except InfeasibleRouteError as exc:
        raise PlannerError(str(exc), code='route_not_fuelable') from exc

    records = get_index().records
    stops = []
    for n, stop in enumerate(plan.stops, start=1):
        idx, mile, offset, price = corridor[stop.station]
        rec = records[idx]
        gallons = stop.miles_of_fuel / mpg
        stops.append(
            {
                'stop_number': n,
                'opis_id': rec['opis_id'],
                'name': rec['name'],
                'address': rec['address'],
                'city': rec['city'],
                'state': rec['state'],
                'lat': rec['lat'],
                'lon': rec['lon'],
                'price_per_gallon': round(price, 3),
                'mile_marker': round(mile, 1),
                'distance_from_route_miles': round(offset, 1),
                'gallons_purchased': round(gallons, 2),
                'cost': round(gallons * price, 2),
            }
        )

    notes = []
    reserve = _cfg('ORIGIN_RESERVE_MILES')
    if not start_full and stops and stops[0]['mile_marker'] > reserve:
        notes.append(
            f'The price list has no fuel station within {reserve:.0f} miles of the origin. The vehicle is '
            f'assumed to carry enough fuel to reach the first listed station (mile {stops[0]["mile_marker"]}); '
            f'that fuel is priced at that station so every mile of the trip is costed.'
        )

    total_gallons = sum(s.miles_of_fuel for s in plan.stops) / mpg
    total_cost = plan.cost / mpg
    return {
        'start': start.as_dict(),
        'finish': finish.as_dict(),
        'route': {
            'distance_miles': round(total_miles, 1),
            'duration_hours': round(route['duration_seconds'] / 3600, 2),
            'geometry': {'type': 'LineString', 'coordinates': route['geometry']},
        },
        'vehicle': {'range_miles': rng, 'miles_per_gallon': mpg, 'starts_with_full_tank': start_full},
        'fuel_summary': {
            'number_of_stops': len(stops),
            'total_gallons': round(total_gallons, 2),
            'total_fuel_cost': round(total_cost, 2),
            'average_price_per_gallon': round(total_cost / total_gallons, 3) if total_gallons else None,
            'stop_cost_used': stop_cost,
            'currency': 'USD',
        },
        'fuel_stops': stops,
        'notes': notes,
    }
