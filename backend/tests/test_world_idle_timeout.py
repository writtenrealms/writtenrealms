from unittest.mock import patch

from django.utils import timezone
from rest_framework.reverse import reverse

from builders.instance_templates import create_instance_template
from builders.serializers import WorldConfigSerializer
from builders.world_export import manifest_stream_to_yaml
from config import constants
from spawns.models import Player
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.models import World
from worlds.services import WorldSmith
from worlds.tasks import _world_idle_seconds, monitor_worlds


class WorldIdleTimeoutTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.lifecycle_change_ts = self.now - timezone.timedelta(days=1)
        self.spawn_world.save(update_fields=[
            'is_multiplayer', 'lifecycle', 'lifecycle_change_ts',
        ])
        self.player.in_game = True
        self.player.last_action_ts = self.now
        self.player.save(update_fields=['in_game', 'last_action_ts'])

    def _logout(self, player=None, *, seconds=0):
        player = player or self.player
        player.refresh_from_db()
        with patch('spawns.models.timezone.now', return_value=self.now + timezone.timedelta(seconds=seconds)):
            WorldGate(player=player, world=player.world).exit()

    def _monitor(self, seconds):
        with patch('worlds.tasks.timezone.now', return_value=self.now + timezone.timedelta(seconds=seconds)):
            monitor_worlds()

    def test_logout_gets_full_default_grace_with_missing_or_stale_world_activity(self):
        self._logout()
        for last_played in (None, self.now - timezone.timedelta(days=1)):
            with self.subTest(last_played=last_played):
                World.objects.filter(pk=self.spawn_world.pk).update(last_played_ts=last_played)
                with patch('worlds.tasks.WorldSmith.stop') as stop:
                    self._monitor(0)
                    self._monitor(299)
                    self._monitor(300)
                    stop.assert_not_called()
                    self._monitor(301)
                    stop.assert_called_once_with(reason='idle_expired')

    def test_last_of_multiple_players_starts_timer(self):
        other = self.create_player('Other')
        other.in_game = True
        other.last_action_ts = self.now
        other.save(update_fields=['in_game', 'last_action_ts'])
        self._logout()
        with patch('worlds.tasks.WorldSmith.stop') as stop:
            self._monitor(301)
            stop.assert_not_called()
            self._logout(other, seconds=310)
            self._monitor(609)
            stop.assert_not_called()
            self._monitor(611)
            stop.assert_called_once()

    def test_reconnect_and_logout_between_monitor_passes_restart_timer(self):
        self._logout()
        with patch('spawns.services.timezone.now', return_value=self.now + timezone.timedelta(seconds=200)):
            WorldGate(player=self.player, world=self.spawn_world).enter()
        self._logout(seconds=210)
        with patch('worlds.tasks.WorldSmith.stop') as stop:
            self._monitor(509)
            stop.assert_not_called()
            self._monitor(511)
            stop.assert_called_once()

    def test_duplicate_logout_does_not_extend_timeout(self):
        self._logout()
        self._logout(seconds=200)
        with patch('worlds.tasks.WorldSmith.stop') as stop:
            self._monitor(301)
            stop.assert_called_once()

    def test_disconnect_during_monitor_pass_gets_full_grace(self):
        def disconnect_during_monitor(world):
            self._logout()
            return 0

        with patch('worlds.tasks._disconnect_idle_players', side_effect=disconnect_during_monitor), patch(
            'worlds.tasks.WorldSmith.stop',
        ) as stop:
            self._monitor(0)
        stop.assert_not_called()

    def test_configured_timeout_changes_existing_runtime(self):
        self._logout()
        for seconds in (1, 60, 900):
            with self.subTest(seconds=seconds):
                self.world_config.idle_timeout_seconds = seconds
                self.world_config.save(update_fields=['idle_timeout_seconds'])
                with patch('worlds.tasks.WorldSmith.stop') as stop:
                    self._monitor(seconds)
                    stop.assert_not_called()
                    self._monitor(seconds + 1)
                    stop.assert_called_once()

    def test_never_played_and_restarted_world_gets_grace_from_start(self):
        self._logout(seconds=-3600)
        for last_played in (None, self.now - timezone.timedelta(days=1)):
            with self.subTest(last_played=last_played):
                World.objects.filter(pk=self.spawn_world.pk).update(last_played_ts=last_played)
                self.spawn_world.refresh_from_db()
                self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_STOPPED)
                with patch('worlds.models.timezone.now', return_value=self.now):
                    WorldSmith(self.spawn_world).start()
                with patch('worlds.tasks.WorldSmith.stop') as stop:
                    self._monitor(299)
                    stop.assert_not_called()
                    self._monitor(301)
                    stop.assert_called_once()

    def test_tier_three_world_remains_running(self):
        self.world.tier = 3
        self.world.save(update_fields=['tier'])
        self._logout()
        with patch('worlds.tasks.WorldSmith.stop') as stop:
            self._monitor(3600)
        stop.assert_not_called()

    def test_child_instance_logout_starts_base_timer_and_uses_local_timeout(self):
        self.world_config.idle_timeout_seconds = 900
        self.world_config.save(update_fields=['idle_timeout_seconds'])
        template = create_instance_template(
            base_world=self.world, author=self.user, name='Outpost',
        )
        template.config.idle_timeout_seconds = 60
        template.config.save(update_fields=['idle_timeout_seconds'])
        with patch('worlds.instances.timezone.now', return_value=self.now):
            instance = World.enter_instance(
                player=self.player, transfer_to_id=template.config.starting_room_id,
                transfer_from_id=self.room.pk,
            )
        # Even a long session in the instance counts as occupancy of the base.
        instance.instance_run.last_active_at = self.now - timezone.timedelta(days=1)
        instance.instance_run.save(update_fields=['last_active_at'])
        with patch('worlds.tasks.WorldSmith.stop') as stop:
            self._monitor(0)
        stop.assert_not_called()
        self._logout()
        self._monitor(59)
        self.assertTrue(World.objects.filter(pk=instance.pk).exists())
        self._monitor(61)
        self.assertFalse(World.objects.filter(pk=instance.pk).exists())
        # Instance cleanup returns the offline character to the base world;
        # their disconnect time still protects the base's longer grace period.
        self._monitor(899)
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.lifecycle, constants.WORLD_LIFECYCLE_RUNNING)
        self._monitor(901)
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.lifecycle, constants.WORLD_LIFECYCLE_STOPPED)

    def test_idle_lookup_query_count_does_not_grow_with_player_count(self):
        self._logout()
        self.spawn_world = World.objects.select_related('context', 'config').get(pk=self.spawn_world.pk)
        for count in (0, 100):
            Player.objects.bulk_create([
                Player(name=f'Offline{index}', user=self.user, world=self.spawn_world, room=self.room,
                       last_disconnection_ts=self.now - timezone.timedelta(hours=1))
                for index in range(count)
            ])
            with self.assertNumQueries(1):
                seconds = _world_idle_seconds(self.spawn_world, now=self.now + timezone.timedelta(seconds=30))
            self.assertEqual(seconds, 30)


class WorldIdleTimeoutConfigTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def _apply(self, spec):
        return self.client.post(reverse('builder-world-manifest-apply', args=[self.world.pk]), {
            'manifest': manifest_stream_to_yaml([{'kind': 'world', 'spec': spec}]),
        }, format='json')

    def test_default_export_partial_edit_and_instance_clone(self):
        response = self.client.get(reverse('builder-world-config', args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['config']['idle_timeout_seconds'], 300)
        self.assertEqual(response.data['manifest']['spec']['idle_timeout_seconds'], 300)
        response = self._apply({'idle_timeout_seconds': 900})
        self.assertEqual(response.status_code, 200, response.data)
        response = self._apply({'motd': 'Hello'})
        self.assertEqual(response.status_code, 200, response.data)
        self.world_config.refresh_from_db()
        self.assertEqual(self.world_config.idle_timeout_seconds, 900)
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.world.refresh_from_db()
        template = create_instance_template(base_world=self.world, author=self.user, name='Outpost')
        self.assertEqual(template.config.idle_timeout_seconds, 900)

    def test_manifest_and_config_api_validate_timeout(self):
        for invalid in (0, -1, True, 1.5, None, 2147483648):
            with self.subTest(invalid=invalid):
                response = self._apply({'idle_timeout_seconds': invalid})
                self.assertEqual(response.status_code, 400, response.data)
                self.world_config.refresh_from_db()
                self.assertEqual(self.world_config.idle_timeout_seconds, 300)
                serializer = WorldConfigSerializer(
                    self.world_config, data={'idle_timeout_seconds': invalid}, partial=True,
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn('idle_timeout_seconds', serializer.errors)
