"""Minimal OpenRouteService client (https://openrouteservice.org, free tier).

One directions call returns the full route geometry plus distance and duration,
which is all the planner needs. A pooled `requests.Session` keeps the TLS
connection warm between requests.
"""

import logging
import threading

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from routing.exceptions import UpstreamError

from .geo import METERS_PER_MILE

logger = logging.getLogger(__name__)

_session = None
_session_lock = threading.Lock()


def _get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                # Retry transient failures only (connection errors, 502/503/504); never 4xx.
                retry = Retry(
                    total=2,
                    backoff_factor=0.3,
                    status_forcelist=(502, 503, 504),
                    allowed_methods=frozenset({'GET', 'POST'}),
                )
                s.mount('https://', HTTPAdapter(max_retries=retry, pool_maxsize=10))
                _session = s
    return _session


def _headers():
    key = settings.OPENROUTESERVICE_API_KEY
    if not key:
        raise UpstreamError(
            'OPENROUTESERVICE_API_KEY is not configured.', status_code=503, code='routing_not_configured'
        )
    return {'Authorization': key, 'Accept': 'application/json, application/geo+json'}


def _request(method: str, path: str, **kwargs) -> dict:
    url = f'{settings.OPENROUTESERVICE_BASE_URL}{path}'
    try:
        resp = _get_session().request(
            method, url, headers=_headers(), timeout=settings.ROUTING_TIMEOUT_SECONDS, **kwargs
        )
    except requests.RequestException as exc:
        logger.exception('Routing API unreachable')
        raise UpstreamError(f'Routing service unreachable: {exc.__class__.__name__}') from exc

    if resp.status_code == 200:
        return resp.json()
    try:
        message = resp.json().get('error', {})
        message = message.get('message') if isinstance(message, dict) else message
    except ValueError:
        message = resp.text[:200]
    logger.warning('Routing API %s %s -> %s: %s', method, path, resp.status_code, message)
    if resp.status_code in (400, 404):
        # ORS uses these for "no route between points" / "point not routable".
        raise UpstreamError(f'No drivable route found: {message}', status_code=422, code='no_route')
    if resp.status_code == 429:
        raise UpstreamError(
            'Routing API rate limit reached, try again shortly.', status_code=503, code='routing_rate_limited'
        )
    if resp.status_code in (401, 403):
        raise UpstreamError('Routing API rejected the API key.', status_code=503, code='routing_auth')
    raise UpstreamError(f'Routing service error ({resp.status_code}).')


def directions(start: tuple[float, float], finish: tuple[float, float], profile: str | None = None) -> dict:
    """Route between two (lat, lon) points.

    Returns {'coordinates': [[lon, lat], ...], 'distance_miles': float, 'duration_seconds': float}.
    """
    profile = profile or settings.ROUTING_PROFILE
    body = {
        'coordinates': [[start[1], start[0]], [finish[1], finish[0]]],
        'instructions': False,
        'preference': 'recommended',
        'radiuses': [5000, 5000],  # snap up to 5 km to the nearest road (city centroids)
    }
    data = _request('POST', f'/v2/directions/{profile}/geojson', json=body)
    try:
        feature = data['features'][0]
        summary = feature['properties']['summary']
        return {
            'coordinates': feature['geometry']['coordinates'],
            'distance_miles': summary['distance'] / METERS_PER_MILE,
            'duration_seconds': summary.get('duration', 0.0),
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise UpstreamError('Unexpected response from routing service.') from exc


def geocode(text: str) -> tuple[float, float, str] | None:
    """Free-form US address -> (lat, lon, label) or None. Used only when offline lookup fails."""
    data = _request(
        'GET',
        '/geocode/search',
        params={
            'text': text,
            'boundary.country': 'US',
            'size': 1,
            'layers': 'address,venue,street,locality,localadmin,neighbourhood,postalcode,county',
        },
    )
    features = data.get('features') or []
    if not features:
        return None
    lon, lat = features[0]['geometry']['coordinates'][:2]
    return lat, lon, features[0]['properties'].get('label', text)
