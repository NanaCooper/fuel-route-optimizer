"""WSGI config for fuel_route project."""

import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fuel_route.settings')

application = get_wsgi_application()

# Build the in-memory station index and gazetteer at boot so the first request is as fast as the rest.
try:
    from routing.services.places import warm_up
    from routing.services.stations import get_index

    warm_up()
    get_index()
except Exception:  # e.g. migrations not applied yet; the index is then built lazily
    logging.getLogger('routing').warning('Station index warm-up skipped', exc_info=True)
