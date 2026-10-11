import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation
from routing.services.places import DATA_DIR

STATIONS_FILE = DATA_DIR / 'stations_geocoded.csv'


def read_stations(path=STATIONS_FILE):
    with open(path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            yield FuelStation(
                opis_id=int(row['opis_id']),
                name=row['name'],
                address=row['address'],
                city=row['city'],
                state=row['state'],
                rack_id=row['rack_id'],
                price=row['price'],
                latitude=float(row['lat']),
                longitude=float(row['lon']),
                geo_source=row['geo_source'],
            )


class Command(BaseCommand):
    help = 'Load geocoded fuel stations (routing/data/stations_geocoded.csv) into the database.'

    def handle(self, *args, **opts):
        if not STATIONS_FILE.exists():
            raise CommandError(f'{STATIONS_FILE} not found - run `build_datasets` first.')
        stations = list(read_stations())
        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        if opts['verbosity']:
            self.stdout.write(self.style.SUCCESS(f'Loaded {len(stations)} fuel stations.'))
