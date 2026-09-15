import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.base import WorldTestCase
from worlds.models import InstanceClearRecord, World


class TestBuilderPlayerCompletions(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('builder-player-completions', args=[self.world.pk, self.player.pk])

    def record(self, *, player=None, completed_at=None, **fields):
        completed_at = completed_at or timezone.now()
        return InstanceClearRecord.objects.create(
            attempt_id=uuid.uuid4(),
            template_name='A Persian Outpost',
            template_slug='persian-outpost',
            started_at=completed_at - timedelta(milliseconds=1125309),
            completed_at=completed_at,
            clear_time_ms=1125309,
            participants=[{'player_id': (player or self.player).pk, 'name': 'Historical Name', 'role': 'member',
                           'exited_at': (completed_at - timedelta(minutes=1)).isoformat()}],
            **fields,
        )

    def test_history_uses_snapshot_even_without_a_live_run_or_template(self):
        record = self.record(time_control=True)
        self.record(player=self.create_player('Other'))
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['next'])
        self.assertEqual(len(response.data['results']), 1)
        result = response.data['results'][0]
        self.assertEqual(result['id'], record.pk)
        self.assertEqual(result['template_name'], 'A Persian Outpost')
        self.assertEqual(result['clear_time_ms'], 1125309)
        self.assertTrue(result['time_control'])
        self.assertNotIn('participants', result)
        self.assertNotIn('goal_spec', result)

    def test_history_is_bounded_and_orders_equal_completion_times_stably(self):
        timestamp = timezone.now()
        records = [self.record(completed_at=timestamp) for _ in range(23)]
        newest = self.record(completed_at=timestamp + timedelta(minutes=1))
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(self.endpoint)
        history_queries = [query['sql'] for query in queries if 'worlds_instanceclearrecord' in query['sql']]
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data['results']), 20)
        self.assertEqual(first.data['results'][0]['id'], newest.pk)
        next_url = urlsplit(first.data['next'])
        second = self.client.get(f'{next_url.path}?{next_url.query}')
        self.assertEqual(second.status_code, 200)
        self.assertIsNone(second.data['next'])
        self.assertEqual(
            [row['id'] for page in [first, second] for row in page.data['results']],
            [newest.pk, *[record.pk for record in reversed(records)]],
        )
        self.assertEqual(len(history_queries), 1)
        self.assertIn('LIMIT 21', history_queries[0])
        self.assertIn('@>', history_queries[0])

    def test_empty_history(self):
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])

    def test_requires_builder_access_and_scopes_the_player_to_the_world(self):
        other_user = self.create_user('other-completions@example.com')
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get(self.endpoint).status_code, 403)
        self.client.force_authenticate(self.user)
        other_world = World.objects.new_world(name='Elsewhere', author=self.user)
        endpoint = reverse('builder-player-completions', args=[other_world.pk, self.player.pk])
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.endpoint).status_code, 401)

    def test_history_endpoint_is_read_only(self):
        record = self.record()
        self.assertEqual(self.client.post(self.endpoint, {}).status_code, 405)
        self.assertEqual(self.client.delete(self.endpoint).status_code, 405)
        self.assertTrue(InstanceClearRecord.objects.filter(pk=record.pk).exists())
