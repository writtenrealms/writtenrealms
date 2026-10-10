from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.reverse import reverse

from config import constants
from spawns.models import Player, PlayerEvent
from system.control_overview import build_overview, OVERVIEW_CACHE_KEY
from tests.base import WorldTestCase
from worlds.models import World


class ControlOverviewTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.delete(OVERVIEW_CACHE_KEY)
        self.staff = self.create_user('operator@example.com', is_staff=True)
        self.spawn_world.lifecycle = constants.WORLD_STATE_RUNNING
        self.spawn_world.save(update_fields=['lifecycle'])
        Player.objects.filter(pk=self.player.pk).update(in_game=True, last_connection_ts=timezone.now())

    def test_all_staff_read_endpoints_reject_non_staff_and_anonymous(self):
        paths = [
            'staff-overview', 'staff-audit', 'staff_panel', 'staff-worlds',
            'staff-player-events', 'staff-playing', 'staff-signups',
            'staff-activity', 'staff-search',
        ]
        for user in (None, self.user):
            self.client.force_authenticate(user)
            for name in paths:
                with self.subTest(user=user, endpoint=name):
                    self.assertIn(self.client.get(reverse(name)).status_code, (401, 403))
            self.assertIn(self.client.get(reverse('staff-users', args=[self.staff.pk])).status_code, (401, 403))

    def test_counts_daily_unique_accounts_and_online_rows(self):
        PlayerEvent.objects.create(player=self.player, event=constants.PLAYER_EVENT_LOGIN)
        PlayerEvent.objects.create(player=self.player, event=constants.PLAYER_EVENT_LOGIN)
        older = PlayerEvent.objects.create(player=self.player, event=constants.PLAYER_EVENT_LOGIN)
        PlayerEvent.objects.filter(pk=older.pk).update(created_ts=timezone.now() - timedelta(days=3))
        temp = self.create_user('temporary@example.com')
        temp.is_temporary = True
        temp.save(update_fields=['is_temporary'])
        temp_player = self.create_player('Visitor', user=temp)
        Player.objects.filter(pk=temp_player.pk).update(in_game=True)
        PlayerEvent.objects.create(player=temp_player, event=constants.PLAYER_EVENT_LOGIN)
        data = build_overview()
        self.assertEqual(data['counts']['users'], 2)
        self.assertEqual(data['counts']['online_characters'], 1)
        self.assertEqual(data['counts']['online_users'], 1)
        self.assertEqual(data['counts']['active_users_24h'], 1)
        self.assertEqual(len(data['activity']), 14)
        self.assertEqual(data['activity'][-1]['active_users'], 1)
        self.assertEqual(sum(day['active_users'] for day in data['activity']), 2)
        self.assertEqual(data['online_worlds'][0]['online_characters'], 1)
        self.assertEqual(data['online_characters'][0]['root_world_id'], self.world.id)

    def test_query_cost_does_not_grow_and_payload_is_bounded(self):
        with self.assertNumQueries(10):
            build_overview()
        Player.objects.bulk_create([
            Player(name=f'Character {i}', user=self.user, world=self.spawn_world, in_game=True)
            for i in range(505)
        ])
        World.objects.bulk_create([
            World(name=f'Runtime {i}', context=self.world, lifecycle=constants.WORLD_STATE_RUNNING)
            for i in range(55)
        ])
        with self.assertNumQueries(10):
            data = build_overview()
        self.assertEqual(data['counts']['online_characters'], 506)
        self.assertEqual(len(data['online_characters']), 100)
        self.assertEqual(data['online_worlds'][0]['online_characters'], 506)
        self.assertEqual(data['counts']['running_worlds'], 56)
        self.assertEqual(len(data['online_worlds']), 50)

    def test_snapshot_is_reused_but_permissions_are_checked_on_every_request(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(reverse('staff-overview')).status_code, 200)
        with self.assertNumQueries(0):
            self.assertEqual(self.client.get(reverse('staff-overview')).status_code, 200)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse('staff-overview')).status_code, 403)

    def test_audit_order_bounds_and_actor_snapshot(self):
        from system.models import StaffAuditEvent
        StaffAuditEvent.objects.bulk_create([
            StaffAuditEvent(actor=self.staff, actor_email=self.staff.email, action='settings.updated', target=str(i))
            for i in range(55)
        ])
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse('staff-audit'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 55)
        self.assertEqual(len(response.data['results']), 50)
        self.assertEqual(response.data['results'][0]['actor'], self.staff.email)
        page = self.client.get(reverse('staff-audit'), {'offset': 50})
        self.assertEqual(len(page.data['results']), 5)
        self.assertFalse(set(row['id'] for row in page.data['results']) & set(row['id'] for row in response.data['results']))
        self.assertEqual(self.client.get(reverse('staff-audit'), {'offset': -1}).status_code, 400)
