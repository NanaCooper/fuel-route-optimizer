from django.test import SimpleTestCase
from rest_framework.test import APIClient

from routing.management.commands.build_datasets import display_names
from routing.services.places import lookup_place, search_places


def labels(query, limit=8):
    return [r['label'] for r in search_places(query, limit)]


class DisplayNameTests(SimpleTestCase):
    def test_census_names_are_cleaned(self):
        self.assertEqual(display_names('Indianapolis city (balance)'), ['Indianapolis'])
        self.assertEqual(display_names('Louisville/Jefferson County metro government (balance)'), ['Louisville'])
        self.assertEqual(display_names('Athens-Clarke County unified government (balance)'), ['Athens'])
        self.assertEqual(display_names('Kansas City city'), ['Kansas City'])
        self.assertEqual(display_names('Winston-Salem city'), ['Winston-Salem'])
        self.assertEqual(display_names('Abanda CDP'), ['Abanda'])

    def test_alternate_names_become_aliases(self):
        self.assertEqual(display_names('San Buenaventura (Ventura)'), ['San Buenaventura', 'Ventura'])


class PlaceSearchTests(SimpleTestCase):
    def test_single_letter_lists_biggest_cities_first(self):
        self.assertEqual(labels('C', 3), ['Chicago, IL', 'Columbus, OH', 'Charlotte, NC'])

    def test_typing_narrows_results(self):
        results = labels('chi')
        self.assertEqual(results[0], 'Chicago, IL')
        self.assertTrue(all(r.lower().startswith('chi') for r in results))

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(labels('SAN FRAN', 1), ['San Francisco, CA'])
        self.assertEqual(labels('winston salem', 1), ['Winston-Salem, NC'])

    def test_abbreviations(self):
        self.assertEqual(labels('st lo', 1), ['St. Louis, MO'])
        self.assertEqual(labels('saint lou', 1), ['St. Louis, MO'])
        self.assertEqual(labels('ft wo', 1), ['Fort Worth, TX'])

    def test_state_filter_after_comma(self):
        self.assertEqual(labels('springfield, il'), ['Springfield, IL'])
        self.assertTrue(all(r.endswith(', MO') for r in labels('spring, mo')))  # not Montana
        self.assertEqual(labels('springfield, illi'), ['Springfield, IL'])  # full state name

    def test_matches_later_words(self):
        self.assertEqual(labels('vegas', 1), ['Las Vegas, NV'])

    def test_no_match(self):
        self.assertEqual(search_places('xqzv'), [])
        self.assertEqual(search_places('   '), [])

    def test_every_suggestion_resolves_offline(self):
        # Picking a suggestion must never cost a geocoding API call.
        for q in ['C', 'new y', 'louisv', 'indian', 'nashv', 'ventu', 'st lo', 'kansas c']:
            for r in search_places(q):
                name, state = r['label'].rsplit(', ', 1)
                self.assertIsNotNone(lookup_place(name, state), r['label'])


class PlaceSearchApiTests(SimpleTestCase):
    def test_endpoint(self):
        resp = APIClient().get('/api/places/', {'q': 'den', 'limit': 3})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['results'][0]['label'], 'Denver, CO')
        self.assertEqual(len(body['results']), 3)
        self.assertEqual(set(body['results'][0]), {'label', 'name', 'state', 'lat', 'lon', 'population'})
        self.assertIn('max-age', resp.headers['Cache-Control'])

    def test_missing_query_is_400(self):
        self.assertEqual(APIClient().get('/api/places/').status_code, 400)
