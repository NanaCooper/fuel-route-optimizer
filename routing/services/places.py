"""Place-name normalisation and the offline US place gazetteer.

The gazetteer (`routing/data/us_places.csv`, built from the US Census Gazetteer
by `manage.py build_datasets`) turns "City, ST" into coordinates and powers city
autocomplete, both with zero network calls.
"""

import csv
import heapq
import re
import unicodedata
from bisect import bisect_left
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
PLACES_FILE = DATA_DIR / 'us_places.csv'

US_STATES = {
    'AL': 'Alabama',
    'AK': 'Alaska',
    'AZ': 'Arizona',
    'AR': 'Arkansas',
    'CA': 'California',
    'CO': 'Colorado',
    'CT': 'Connecticut',
    'DE': 'Delaware',
    'DC': 'District of Columbia',
    'FL': 'Florida',
    'GA': 'Georgia',
    'HI': 'Hawaii',
    'ID': 'Idaho',
    'IL': 'Illinois',
    'IN': 'Indiana',
    'IA': 'Iowa',
    'KS': 'Kansas',
    'KY': 'Kentucky',
    'LA': 'Louisiana',
    'ME': 'Maine',
    'MD': 'Maryland',
    'MA': 'Massachusetts',
    'MI': 'Michigan',
    'MN': 'Minnesota',
    'MS': 'Mississippi',
    'MO': 'Missouri',
    'MT': 'Montana',
    'NE': 'Nebraska',
    'NV': 'Nevada',
    'NH': 'New Hampshire',
    'NJ': 'New Jersey',
    'NM': 'New Mexico',
    'NY': 'New York',
    'NC': 'North Carolina',
    'ND': 'North Dakota',
    'OH': 'Ohio',
    'OK': 'Oklahoma',
    'OR': 'Oregon',
    'PA': 'Pennsylvania',
    'RI': 'Rhode Island',
    'SC': 'South Carolina',
    'SD': 'South Dakota',
    'TN': 'Tennessee',
    'TX': 'Texas',
    'UT': 'Utah',
    'VT': 'Vermont',
    'VA': 'Virginia',
    'WA': 'Washington',
    'WV': 'West Virginia',
    'WI': 'Wisconsin',
    'WY': 'Wyoming',
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

# Legal/statistical descriptors the Census appends to place names
# ("Abbeville city", "Abanda CDP", "Nashville-Davidson metropolitan government").
_DESCRIPTOR = re.compile(
    r'\s+(city and borough|charter township|consolidated government|metropolitan government|'
    r'metro government|unified government|urban county|city|town|village|borough|cdp|municipality|township|'
    r'plantation|ccd|comunidad|zona urbana)$'
)
_ABBREVIATIONS = [
    (re.compile(r'\bste\.?\s'), 'sainte '),
    (re.compile(r'\bst\.?\s'), 'saint '),
    (re.compile(r'\bft\.?\s'), 'fort '),
    (re.compile(r'\bmt\.?\s'), 'mount '),
    (re.compile(r'^n\.?\s'), 'north '),
    (re.compile(r'^s\.?\s'), 'south '),
    (re.compile(r'^e\.?\s'), 'east '),
    (re.compile(r'^w\.?\s'), 'west '),
]


def normalize_place(name: str) -> str:
    """Return a comparison key for a place name.

    Spaces and punctuation are removed entirely so spelling variants collapse:
    "Mc Calla" == "McCalla", "La Salle" == "LaSalle", "Winston-Salem" == "Winston Salem".
    """
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    s = re.sub(r'\(.*?\)', '', s.lower()).strip()
    for _ in range(2):
        s = _DESCRIPTOR.sub('', s).strip()
    s = s + ' '
    for pattern, repl in _ABBREVIATIONS:
        s = pattern.sub(repl, s)
    return re.sub(r'[^a-z0-9]', '', s)


_LEADING_ABBR = re.compile(r'^(st|ste|ft|mt) ')
_LEADING_FULL = {'st': 'saint ', 'ste': 'sainte ', 'ft': 'fort ', 'mt': 'mount '}


def _expand(key):
    return _LEADING_ABBR.sub(lambda m: _LEADING_FULL[m.group(1)], key)


def _search_key(text):
    s = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s)).strip()


def normalize_state(state: str) -> str | None:
    """Accept 'TX', 'tx' or 'Texas'; return the 2-letter code or None."""
    s = state.strip()
    if s.upper() in US_STATES:
        return s.upper()
    return _STATE_BY_NAME.get(s.lower())


@cache
def _read_places_file():
    if not PLACES_FILE.exists():
        return []
    with open(PLACES_FILE, encoding='utf-8', newline='') as f:
        return [
            (row['name'], row['state'], float(row['lat']), float(row['lon']), int(row.get('population') or 0))
            for row in csv.DictReader(f)
        ]


@cache
def load_places() -> dict[tuple[str, str], tuple[float, float, str]]:
    """{(normalized_name, STATE): (lat, lon, display_name)} from the bundled gazetteer."""
    places = {}
    # Most populous first, so name clashes within a state resolve to the bigger place.
    for name, state, lat, lon, _ in sorted(_read_places_file(), key=lambda r: -r[4]):
        places.setdefault((normalize_place(name), state), (lat, lon, name))
    return places


@cache
def _search_index():
    """Places sorted by search key, for prefix lookups with bisect."""
    entries = []
    for name, state, lat, lon, pop in _read_places_file():
        key = _search_key(name)
        entries.append((key, name, state, lat, lon, pop))
        expanded = _expand(key)
        if expanded != key:  # "saint louis" also finds "St. Louis"
            entries.append((expanded, name, state, lat, lon, pop))
    entries.sort()
    return entries, [e[0] for e in entries]


def search_places(query: str, limit: int = 8) -> list[dict]:
    """Autocomplete: places whose name starts with `query`, most populous first.

    "chi" -> Chicago, IL; Chico, CA; ...    "spring, mo" -> Springfield, MO; ...
    """
    name_part, _, state_part = query.partition(',')
    prefix = _search_key(name_part)
    state_part = state_part.strip().lower()
    if not prefix:
        return []

    def state_ok(code):
        if not state_part:
            return True
        if len(state_part) <= 2:  # "mo" means Missouri, not Montana
            return code.lower().startswith(state_part)
        return US_STATES[code].lower().startswith(state_part)

    entries, keys = _search_index()
    seen, matches = set(), []

    def add(entry, tier):
        _, name, state, lat, lon, pop = entry
        if (name, state) not in seen and state_ok(state):
            seen.add((name, state))
            matches.append((tier, pop, name, state, lat, lon))

    for p in dict.fromkeys([prefix, _expand(prefix)]):  # "ft wo" also searches "fort wo"
        # Prefix range via bisect; "\x7f" sorts after every character a key can contain.
        for entry in entries[bisect_left(keys, p) : bisect_left(keys, p + '\x7f')]:
            add(entry, tier=1)  # name starts with the query
    if len(matches) < limit and len(prefix) >= 3:
        needle = ' ' + prefix  # a later word does: "vegas" -> Las Vegas
        for entry in entries:
            if needle in ' ' + entry[0]:
                add(entry, tier=0)

    top = heapq.nlargest(limit, matches, key=lambda m: (m[0], m[1], -len(m[2])))
    return [
        {'label': f'{name}, {state}', 'name': name, 'state': state, 'lat': lat, 'lon': lon, 'population': pop}
        for _, pop, name, state, lat, lon in top
    ]


def lookup_place(city: str, state: str) -> tuple[float, float, str] | None:
    """Resolve a US city/state pair offline. Returns (lat, lon, display_name) or None."""
    code = normalize_state(state)
    if not code:
        return None
    return load_places().get((normalize_place(city), code))


def warm_up() -> None:
    """Build the lookup and autocomplete indexes ahead of the first request."""
    load_places()
    _search_index()
