"""Resolve user-supplied locations to coordinates, preferring zero-network lookups.

Accepted inputs, tried in this order:
  * "lat,lon"            -> parsed directly                   (0 API calls)
  * "City, ST" / "City, State" -> bundled Census gazetteer      (0 API calls)
  * anything else (street address, ZIP, landmark) -> ORS geocoder (1 API call)
"""

import re
from dataclasses import dataclass

from routing.exceptions import LocationError

from . import ors
from .geo import in_usa
from .places import US_STATES, lookup_place, normalize_state

_LATLON = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$')
# Inputs like "Toronto, ON" must not be fuzzy-matched to a US namesake (Toronto, OH).
_FOREIGN_REGIONS = {
    'AB',
    'BC',
    'MB',
    'NB',
    'NL',
    'NS',
    'NT',
    'NU',
    'ON',
    'PE',
    'QC',
    'SK',
    'YT',  # provinces
    'CANADA',
    'MEXICO',
    'MX',
}


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lon: float
    source: str  # 'coordinates' | 'gazetteer' | 'geocoder'

    @property
    def point(self) -> tuple[float, float]:
        return self.lat, self.lon

    def as_dict(self) -> dict:
        return {
            'query': self.query,
            'label': self.label,
            'lat': round(self.lat, 6),
            'lon': round(self.lon, 6),
            'resolved_by': self.source,
        }


def _split_city_state(text: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) >= 2:
        state = parts[-1]
        # "Dallas, TX 75201" / "Dallas, Texas, USA"
        if state.upper() in {'USA', 'US', 'UNITED STATES'} and len(parts) >= 3:
            state = parts[-2]
            parts = parts[:-1]
        state = re.sub(r'\s+\d{5}(-\d{4})?$', '', state)
        if normalize_state(state) and len(parts) == 2:
            return parts[0], state
    # "Dallas TX"
    m = re.match(r'^(.*?)\s+([A-Za-z]{2})$', text.strip())
    if m and m.group(2).upper() in US_STATES:
        return m.group(1), m.group(2)
    return None


def resolve(text: str, *, allow_remote: bool = True) -> tuple[Location, int]:
    """Return (Location, number of external API calls made)."""
    text = (text or '').strip()
    if not text:
        raise LocationError('Location must not be empty.', status_code=400)

    m = _LATLON.match(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise LocationError(f'"{text}" is not a valid "lat,lon" pair.', status_code=400)
        loc = Location(text, f'{lat:.5f}, {lon:.5f}', lat, lon, 'coordinates')
        return _check_usa(loc), 0

    last = text.rsplit(',', 1)[-1].strip().upper() if ',' in text else ''
    if last in _FOREIGN_REGIONS:
        raise LocationError(
            f'"{text}" is outside the USA. Start and finish must both be in the USA.', code='outside_usa'
        )

    city_state = _split_city_state(text)
    if city_state:
        hit = lookup_place(*city_state)
        if hit:
            lat, lon, name = hit
            return Location(text, f'{name}, {normalize_state(city_state[1])}', lat, lon, 'gazetteer'), 0

    if not allow_remote:
        raise LocationError(f'Could not find "{text}". Use "City, ST" or "lat,lon".')
    hit = ors.geocode(text)
    if not hit:
        raise LocationError(f'Could not find a US location matching "{text}".')
    return _check_usa(Location(text, hit[2], hit[0], hit[1], 'geocoder')), 1


def _check_usa(loc: Location) -> Location:
    if not in_usa(loc.lat, loc.lon):
        raise LocationError(
            f'"{loc.query}" is outside the USA. Start and finish must both be in the USA.', code='outside_usa'
        )
    return loc
