"""One-time, offline build of the data files the API runs on.

The provided fuel CSV has no coordinates. Instead of calling a geocoding API for
~6,700 stations, each station's (city, state) is matched against public
gazetteers, in order of preference:

  1. US Census Gazetteer - places (incorporated places + CDPs)
  2. US Census Gazetteer - county subdivisions (townships etc.)
  3. GeoNames US populated places (unincorporated communities)

Outputs (committed to the repo, so nobody has to run this again):
  routing/data/stations_geocoded.csv  - one row per unique OPIS station
  routing/data/us_places.csv          - gazetteer for "City, ST" lookup and autocomplete
  routing/data/us_boundary.json       - US outline polygons for the "is this in the USA?" check

Source files:
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_cousubs_national.zip
  https://download.geonames.org/export/dump/US.zip
  https://www2.census.gov/geo/tiger/GENZ2024/kml/cb_2024_us_nation_20m.zip
"""

import csv
import json
import re
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from routing.services.geo import BOUNDARY_FILE
from routing.services.places import DATA_DIR, PLACES_FILE, US_STATES, normalize_place

FUEL_CSV = DATA_DIR / 'fuel-prices-for-be-assessment.csv'
STATIONS_FILE = DATA_DIR / 'stations_geocoded.csv'
# GeoNames feature codes for real populated places (skip historical/abandoned).
GEONAMES_CODES = {'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLC', 'PPLL', 'PPLS', 'PPLF', 'PPLG'}

_DISPLAY_DESCRIPTOR = re.compile(
    r'\s+(city and borough|consolidated government|metropolitan government|metro government|'
    r'unified government|urban county|city|town|village|borough|CDP|municipality)$'
)
_GOVERNMENT = re.compile(r'government$|urban county$')


def display_names(census_name: str) -> list[str]:
    """Human-friendly name(s) for a Census place, e.g.
    "Indianapolis city (balance)"                            -> ["Indianapolis"]
    "Louisville/Jefferson County metro government (balance)" -> ["Louisville"]
    "San Buenaventura (Ventura)"                             -> ["San Buenaventura", "Ventura"]
    "Kansas City city"                                       -> ["Kansas City"]
    """
    aliases = [a.strip() for a in re.findall(r'\((.*?)\)', census_name) if a.strip().lower() != 'balance']
    name = re.sub(r'\s*\(.*?\)', '', census_name).strip()
    consolidated = bool(_GOVERNMENT.search(name))
    name = _DISPLAY_DESCRIPTOR.sub('', name).strip()  # strip once: "Kansas City city" -> "Kansas City"
    if consolidated or (name.endswith(' County') and re.search(r'[-/]', name)):
        name = re.split(r'[-/]', name)[0].strip()  # "Athens-Clarke County" -> "Athens"
    return [name] + [a for a in aliases if a != name]


def read_census(path):
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='|')
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        for row in reader:
            yield {
                'name': row['NAME'].strip(),
                'state': row['USPS'].strip(),
                'lat': float(row['INTPTLAT']),
                'lon': float(row['INTPTLONG']),
                'weight': float(row['ALAND'] or 0),
            }


def read_census_places(path):
    """Census places under their display names; alternate names become alias rows."""
    for row in read_census(path):
        consolidated = bool(re.search(r'government|urban county', row['name'], re.IGNORECASE))
        for i, name in enumerate(display_names(row['name'])):
            yield {**row, 'name': name, 'alias': i > 0, 'consolidated': consolidated}


def read_geonames(path):
    with open(path, encoding='utf-8', newline='') as f:
        for cols in csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if len(cols) < 15 or cols[6] != 'P' or cols[7] not in GEONAMES_CODES:
                continue
            yield {
                'name': cols[1],
                'state': cols[10],
                'lat': float(cols[4]),
                'lon': float(cols[5]),
                'weight': float(cols[14] or 0),  # population
            }


def read_boundary_kml(path):
    """Outer rings of the Census cartographic boundary KML as [[lon, lat], ...] lists."""
    with open(path, encoding='utf-8') as f:
        kml = f.read()
    rings = []
    for block in re.findall(r'<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>', kml, re.DOTALL):
        ring = [[round(float(x), 4), round(float(y), 4)] for x, y, *_ in (pt.split(',') for pt in block.split())]
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _rank(row):
    # On name clashes prefer a real name over an alias, the city proper ("Louisville city") over a
    # county-wide consolidated government ("Louisville/Jefferson County metro government"), then size.
    return (not row.get('alias', False), not row.get('consolidated', False), row['weight'])


def build_index(rows):
    """{(normalized name, state): row}, keeping the most significant place on name clashes."""
    index = {}
    for row in rows:
        if row['state'] not in US_STATES:
            continue
        key = (normalize_place(row['name']), row['state'])
        if key not in index or _rank(row) > _rank(index[key]):
            index[key] = row
    return index


class Command(BaseCommand):
    help = 'Build the geocoded station list, the place gazetteer and the US outline from public data.'

    def add_arguments(self, parser):
        parser.add_argument('--places', required=True, help='Census *_Gaz_place_national.txt')
        parser.add_argument('--cousubs', required=True, help='Census *_Gaz_cousubs_national.txt')
        parser.add_argument('--geonames', help='GeoNames US.txt (optional: coverage + population ranking)')
        parser.add_argument('--boundary-kml', help='Census cb_*_us_nation_20m.kml (optional)')

    def handle(self, *args, **opts):
        if not FUEL_CSV.exists():
            raise CommandError(f'Missing {FUEL_CSV}')

        places = build_index(read_census_places(opts['places']))
        geonames = build_index(read_geonames(opts['geonames'])) if opts['geonames'] else {}
        sources = [
            ('census_place', places),
            ('census_cousub', build_index(read_census(opts['cousubs']))),
            ('geonames', geonames),
        ]
        stations, stats, unmatched = self._geocode_stations(sources)

        with open(STATIONS_FILE, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(next(iter(stations.values())).keys()))
            writer.writeheader()
            writer.writerows(sorted(stations.values(), key=lambda s: s['opis_id']))

        # Gazetteer for "City, ST" lookups and autocomplete; GeoNames population ranks big cities first.
        with open(PLACES_FILE, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['name', 'state', 'lat', 'lon', 'population'])
            rows = [
                [
                    p['name'],
                    p['state'],
                    round(p['lat'], 6),
                    round(p['lon'], 6),
                    int(geonames[key]['weight']) if key in geonames else 0,
                ]
                for key, p in places.items()
            ]
            writer.writerows(sorted(rows, key=lambda r: (r[1], r[0])))

        if opts['boundary_kml']:
            rings = read_boundary_kml(opts['boundary_kml'])
            BOUNDARY_FILE.write_text(json.dumps(rings, separators=(',', ':')), encoding='utf-8')
            self.stdout.write(f'Wrote {BOUNDARY_FILE.name} ({len(rings)} polygons)')

        us_rows = stats['rows'] - stats['non_us']
        missing = sum(unmatched.values())
        self.stdout.write(
            self.style.SUCCESS(
                f'{stats["rows"]} rows | {stats["non_us"]} non-US skipped | '
                f'{len(stations)} unique US stations geocoded | {stats["duplicates"]} duplicate rows merged'
            )
        )
        self.stdout.write(f'Match sources: {dict(Counter(s["geo_source"] for s in stations.values()))}')
        self.stdout.write(
            f'Unmatched US rows: {missing} ({100 * missing / max(us_rows, 1):.2f}%) -> {unmatched.most_common(25)}'
        )
        self.stdout.write(f'Wrote {STATIONS_FILE.name} and {PLACES_FILE.name} ({len(places)} names)')

    @staticmethod
    def _geocode_stations(sources):
        stations, stats, unmatched = {}, Counter(), Counter()
        with open(FUEL_CSV, encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                stats['rows'] += 1
                state = row['State'].strip()
                if state not in US_STATES:
                    stats['non_us'] += 1
                    continue
                opis_id = int(row['OPIS Truckstop ID'])
                price = float(row['Retail Price'])
                # Duplicate OPIS IDs are the same physical stop; keep the best price.
                if opis_id in stations:
                    stats['duplicates'] += 1
                    if stations[opis_id]['price'] <= price:
                        continue

                key = (normalize_place(row['City']), state)
                match = next(((src, idx[key]) for src, idx in sources if key in idx), None)
                if match is None:
                    unmatched[f'{row["City"].strip()}, {state}'] += 1
                    continue
                src, place = match
                stations[opis_id] = {
                    'opis_id': opis_id,
                    'name': row['Truckstop Name'].strip(),
                    'address': row['Address'].strip(),
                    'city': row['City'].strip(),
                    'state': state,
                    'rack_id': row['Rack ID'].strip(),
                    'price': price,
                    'lat': round(place['lat'], 6),
                    'lon': round(place['lon'], 6),
                    'geo_source': src,
                }
        return stations, stats, unmatched
