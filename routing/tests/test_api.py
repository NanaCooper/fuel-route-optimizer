from itertools import pairwise
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from routing.exceptions import UpstreamError
from routing.models import FuelStation
from routing.services import geocode
from routing.services.geo import cumulative_miles, resample
from routing.services.stations import StationIndex, reset_index

# Rough I-80 corridor, Chicago -> Denver (lat, lon).
WAYPOINTS = [
    (41.8781, -87.6298),
    (41.5868, -93.6250),
    (41.2565, -95.9345),
    (40.8136, -96.7026),
    (41.1240, -100.7654),
    (39.7392, -104.9903),
]


def fake_route(waypoints=WAYPOINTS, points_per_leg=300):
    lats, lons = [], []
    for (a_lat, a_lon), (b_lat, b_lon) in pairwise(waypoints):
        lats.extend(np.linspace(a_lat, b_lat, points_per_leg, endpoint=False))
        lons.extend(np.linspace(a_lon, b_lon, points_per_leg, endpoint=False))
    lats.append(waypoints[-1][0])
    lons.append(waypoints[-1][1])
    miles = cumulative_miles(np.array(lats), np.array(lons))[-1]
    return {
        'coordinates': [[lo, la] for la, lo in zip(lats, lons, strict=True)],
        'distance_miles': float(miles) * 1.05,
        'duration_seconds': miles / 60 * 3600,
    }


@override_settings(OPENROUTESERVICE_API_KEY='test-key')
class RoutePlanApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_stations', verbosity=0)

    def setUp(self):
        cache.clear()
        reset_index()
        self.client = APIClient()

    @mock.patch('routing.services.ors.directions', return_value=fake_route())
    def test_plan_route_with_one_routing_call(self, directions):
        resp = self.client.post('/api/route/', {'start': 'Chicago, IL', 'finish': 'Denver, CO'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        directions.assert_called_once()
        self.assertEqual(body['meta']['external_api_calls'], 1)
        self.assertFalse(body['meta']['route_cached'])

        stops = body['fuel_stops']
        self.assertGreaterEqual(len(stops), 2)
        distance = body['route']['distance_miles']
        # Never drive more than the vehicle range between fuel-ups (or to the destination).
        markers = [0.0] + [s['mile_marker'] for s in stops] + [distance]
        self.assertTrue(all(b - a <= 500 + 1e-6 for a, b in pairwise(markers)))
        # Starting empty, every mile is paid for: gallons == distance / MPG.
        self.assertAlmostEqual(body['fuel_summary']['total_gallons'], distance / 10, delta=0.05)
        self.assertAlmostEqual(body['fuel_summary']['total_fuel_cost'], sum(s['cost'] for s in stops), delta=0.05)
        self.assertEqual(body['route']['geometry']['type'], 'LineString')
        self.assertIn('/api/route/map/?', body['map_url'])

    @mock.patch('routing.services.ors.directions', return_value=fake_route())
    def test_repeat_request_is_served_from_cache(self, directions):
        params = {'start': 'Chicago, IL', 'finish': 'Denver, CO'}
        self.client.get('/api/route/', params)
        resp = self.client.get('/api/route/', params)
        self.assertEqual(directions.call_count, 1)
        self.assertTrue(resp.json()['meta']['route_cached'])
        self.assertEqual(resp.json()['meta']['external_api_calls'], 0)

    @mock.patch('routing.services.ors.directions', return_value=fake_route())
    def test_reloading_station_prices_invalidates_cached_routes(self, directions):
        params = {'start': 'Chicago, IL', 'finish': 'Denver, CO'}
        self.client.get('/api/route/', params)
        FuelStation.objects.filter(state='NE').update(price=1.0)  # new price list loaded
        reset_index()
        resp = self.client.get('/api/route/', params).json()
        self.assertEqual(directions.call_count, 2)
        self.assertIn(1.0, [s['price_per_gallon'] for s in resp['fuel_stops']])

    @mock.patch('routing.services.ors.directions', return_value=fake_route())
    def test_changing_stop_cost_reuses_cached_route(self, directions):
        params = {'start': 'Chicago, IL', 'finish': 'Denver, CO'}
        cheap = self.client.get('/api/route/', {**params, 'stop_cost': 0}).json()
        practical = self.client.get('/api/route/', {**params, 'stop_cost': 25}).json()
        self.assertEqual(directions.call_count, 1)
        self.assertLessEqual(cheap['fuel_summary']['total_fuel_cost'], practical['fuel_summary']['total_fuel_cost'])
        self.assertGreaterEqual(cheap['fuel_summary']['number_of_stops'], practical['fuel_summary']['number_of_stops'])

    @mock.patch('routing.services.ors.directions', return_value=fake_route())
    def test_map_page_renders(self, directions):
        resp = self.client.get('/api/route/map/', {'start': 'Chicago, IL', 'finish': 'Denver, CO'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'plan-data')
        self.assertContains(resp, 'total fuel cost')
        # OSM tiles are blocked ("403 Access blocked") if the browser sends no Referer.
        self.assertEqual(resp.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_missing_fields_returns_400(self):
        resp = self.client.post('/api/route/', {'start': 'Chicago, IL'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'invalid_request')

    def test_location_outside_usa_returns_422(self):
        resp = self.client.post('/api/route/', {'start': '51.5074,-0.1278', 'finish': 'Denver, CO'}, format='json')
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()['error']['code'], 'outside_usa')

    def test_canadian_inputs_are_rejected_without_api_calls(self):
        for start in ['43.6532,-79.3832', 'Toronto, ON', 'Vancouver, BC']:
            with mock.patch('routing.services.ors.geocode') as g:
                resp = self.client.post('/api/route/', {'start': start, 'finish': 'Denver, CO'}, format='json')
            self.assertEqual(resp.status_code, 422, start)
            self.assertEqual(resp.json()['error']['code'], 'outside_usa')
            g.assert_not_called()

    @mock.patch('routing.services.ors.directions', side_effect=UpstreamError('Routing service unreachable'))
    def test_upstream_failure_returns_502(self, directions):
        resp = self.client.post('/api/route/', {'start': 'Chicago, IL', 'finish': 'Denver, CO'}, format='json')
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()['error']['code'], 'routing_unavailable')

    def test_health(self):
        resp = self.client.get('/api/health/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.json()['stations_loaded'], 6000)


class StationsNotLoadedTests(TestCase):
    def setUp(self):
        reset_index()

    def test_clear_error_when_load_stations_was_not_run(self):
        resp = APIClient().get('/api/health/')
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['error']['code'], 'stations_not_loaded')


class GeocodeTests(SimpleTestCase):
    def test_city_state_variants_resolve_offline(self):
        for text in [
            'Chicago, IL',
            'chicago, il',
            'Chicago, Illinois',
            'Chicago IL',
            'Chicago, IL 60601',
            'Chicago, IL, USA',
        ]:
            loc, calls = geocode.resolve(text, allow_remote=False)
            self.assertEqual(calls, 0, text)
            self.assertAlmostEqual(loc.lat, 41.84, delta=0.2)
            self.assertAlmostEqual(loc.lon, -87.68, delta=0.2)

    def test_tricky_names(self):
        for text, lat in [
            ('St. Louis, MO', 38.6),
            ('Winston-Salem, NC', 36.1),
            ('Nashville, TN', 36.2),
            ('New York, NY', 40.7),
            ('Indianapolis, IN', 39.8),
        ]:
            loc, _ = geocode.resolve(text, allow_remote=False)
            self.assertAlmostEqual(loc.lat, lat, delta=0.3, msg=text)

    def test_lat_lon_input(self):
        loc, calls = geocode.resolve('39.7392, -104.9903')
        self.assertEqual((calls, loc.source), (0, 'coordinates'))

    def test_unknown_place_falls_back_to_geocoder(self):
        with mock.patch('routing.services.ors.geocode', return_value=(40.75, -73.99, '350 5th Ave, NY')) as g:
            loc, calls = geocode.resolve('350 5th Ave, New York')
        g.assert_called_once()
        self.assertEqual((calls, loc.source), (1, 'geocoder'))


class CorridorTests(SimpleTestCase):
    def test_finds_only_stations_near_route(self):
        index = StationIndex(
            [
                {'opis_id': 1, 'price': 3.0, 'lat': 40.0, 'lon': -100.0},  # on the route
                {'opis_id': 2, 'price': 3.0, 'lat': 40.1, 'lon': -99.0},  # ~7 mi north of it
                {'opis_id': 3, 'price': 3.0, 'lat': 41.0, 'lon': -99.5},  # ~69 mi away
                {'opis_id': 4, 'price': 3.0, 'lat': 40.0, 'lon': -95.0},  # beyond the end
            ]
        )
        lats, lons, miles = resample(np.array([40.0, 40.0]), np.array([-100.5, -98.0]), 1.0)
        found = {index.records[c.index]['opis_id']: c for c in index.corridor(lats, lons, miles, 10)}
        self.assertEqual(set(found), {1, 2})
        self.assertLess(found[1].mile, found[2].mile)
        self.assertAlmostEqual(found[2].offset, 6.9, delta=0.3)
