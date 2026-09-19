from unittest.mock import patch
from django.test import TestCase


class ReadinessTests(TestCase):
    def test_ready_uses_one_database_query(self):
        with self.assertNumQueries(1):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    @patch('core.health.cache.get', side_effect=ConnectionError('private address'))
    def test_dependency_failure_is_unavailable_without_details(self, cache):
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
