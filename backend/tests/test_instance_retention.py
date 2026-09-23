from unittest.mock import patch

from django.utils import timezone

from config import constants
from spawns.models import Mob
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.models import InstanceRun, World, WorldConfig
from worlds.tasks import monitor_worlds


class TestSinglePlayerInstanceRetention(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.config = WorldConfig.objects.create(instance_single_player=True)
        self.template = World.objects.new_world(
            name='Private Adventure', author=self.user, config=self.config,
            instance_of=self.world, is_multiplayer=True,
        )
        self.entry_room = self.template.config.starting_room
        self.now = timezone.now()
        self.instance = self._enter(self.player)
        self.run = self.instance.instance_run
        # A long play session must still get a full grace period on logout.
        self.run.last_active_at = self.now - timezone.timedelta(days=1)
        self.run.save(update_fields=['last_active_at'])

    def _enter(self, player):
        player.in_game = True
        player.last_action_ts = self.now
        player.save(update_fields=['in_game', 'last_action_ts'])
        instance = World.enter_instance(
            player=player, transfer_to_id=self.entry_room.pk,
            transfer_from_id=self.room.pk,
        )
        player.refresh_from_db()
        return instance

    def _disconnect(self, *, player=None, at=None):
        player = player or self.player
        with patch('spawns.models.timezone.now', return_value=at or self.now):
            WorldGate(player=player, world=player.world).exit()
        player.refresh_from_db()

    def _monitor(self, *, at):
        with patch('worlds.tasks.timezone.now', return_value=at):
            monitor_worlds()

    def test_logout_keeps_location_and_run_until_three_hours_then_cleans_up(self):
        inner_room = self.entry_room.create_at(constants.DIRECTION_NORTH)
        self.player.room = inner_room
        self.player.save(update_fields=['room'])
        mob = Mob.objects.create(name='Guard', world=self.instance, room=inner_room)
        self._disconnect()
        self.assertFalse(self.player.in_game)
        self.assertEqual(self.player.last_disconnection_ts, self.now)

        self._monitor(at=self.now + timezone.timedelta(hours=3, seconds=-1))

        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.instance.pk)
        self.assertEqual(self.player.room_id, inner_room.pk)
        self.assertTrue(Mob.objects.filter(pk=mob.pk).exists())
        self.assertTrue(InstanceRun.objects.filter(pk=self.run.pk).exists())

        self._monitor(at=self.now + timezone.timedelta(hours=3))

        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())
        self.assertFalse(InstanceRun.objects.filter(pk=self.run.pk).exists())
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        self.assertFalse(self.player.in_game)

    def test_reconnect_resumes_same_run_and_next_logout_gets_full_grace(self):
        self._disconnect()
        later = self.now + timezone.timedelta(hours=2)
        self._monitor(at=later)
        with patch('spawns.services.timezone.now', return_value=later):
            WorldGate(player=self.player, world=self.instance).enter()
        self.player.refresh_from_db()
        self.assertTrue(self.player.in_game)
        self.assertEqual(self.player.world_id, self.instance.pk)
        self.assertEqual(self.player.room_id, self.entry_room.pk)

        self._disconnect(at=later)
        self.assertEqual(self.player.last_disconnection_ts, later)
        self._monitor(at=self.now + timezone.timedelta(hours=4))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
        self._monitor(at=later + timezone.timedelta(hours=3))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_duplicate_logout_does_not_extend_grace(self):
        self._disconnect()
        self._disconnect(at=self.now + timezone.timedelta(hours=2))
        self.assertEqual(self.player.last_disconnection_ts, self.now)
        self._monitor(at=self.now + timezone.timedelta(hours=3))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_idle_logout_starts_grace_in_the_same_monitor_pass(self):
        self.player.last_action_ts = self.now - timezone.timedelta(
            seconds=constants.IDLE_TIMEOUT + 1,
        )
        self.player.save(update_fields=['last_action_ts'])
        with patch('worlds.tasks.notify_exit_world'):
            self._monitor(at=self.now)
        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)
        self.assertEqual(self.player.last_disconnection_ts, self.now)
        self.assertEqual(self.player.world_id, self.instance.pk)
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_time_control_runs_get_grace_whether_paused_or_running(self):
        self._disconnect()
        for paused in (False, True):
            with self.subTest(paused=paused):
                InstanceRun.objects.filter(pk=self.run.pk).update(
                    time_control=True, time_paused=paused,
                    simulation_time=self.now,
                )
                self._monitor(at=self.now + timezone.timedelta(hours=2))
                self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
        self._monitor(at=self.now + timezone.timedelta(hours=3))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_disconnect_during_monitor_pass_starts_grace(self):
        def disconnect_before_idle_check(spawn_world):
            self._disconnect()
            return 0

        with patch('worlds.tasks._disconnect_idle_players', side_effect=disconnect_before_idle_check):
            self._monitor(at=self.now)
        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_leaving_instance_uses_normal_five_minute_cleanup(self):
        self._disconnect()
        with patch('worlds.instances.timezone.now', return_value=self.now):
            World.leave_instance(player=self.player)
        self._monitor(at=self.now + timezone.timedelta(minutes=4))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
        self._monitor(at=self.now + timezone.timedelta(minutes=6))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_multiplayer_run_with_one_offline_player_gets_no_extended_grace(self):
        InstanceRun.objects.filter(pk=self.run.pk).update(single_player=False)
        self._disconnect()
        self._monitor(at=self.now + timezone.timedelta(minutes=6))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_template_edits_do_not_change_existing_run_retention(self):
        self.config.instance_single_player = False
        self.config.save(update_fields=['instance_single_player'])
        self._disconnect()
        self._monitor(at=self.now + timezone.timedelta(hours=2))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_ownerless_run_gets_no_extended_grace(self):
        self._disconnect()
        InstanceRun.objects.filter(pk=self.run.pk).update(owner=None)
        self._monitor(at=self.now + timezone.timedelta(minutes=6))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_online_owner_is_not_cleaned_after_previous_grace_expires(self):
        self.player.last_disconnection_ts = self.now - timezone.timedelta(hours=4)
        self.player.config.idle_logout = False
        self.player.config.save(update_fields=['idle_logout'])
        self.player.save(update_fields=['last_disconnection_ts'])
        self._monitor(at=self.now)
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_stopped_instance_retry_honors_grace(self):
        self._disconnect()
        World.objects.filter(pk=self.instance.pk).update(
            lifecycle=constants.WORLD_LIFECYCLE_STOPPED,
            lifecycle_change_ts=self.now - timezone.timedelta(minutes=6),
        )
        self._monitor(at=self.now + timezone.timedelta(hours=2))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
        self._monitor(at=self.now + timezone.timedelta(hours=3))
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)

    def test_retained_instances_add_no_per_instance_monitor_queries(self):
        self._disconnect()
        with self.assertNumQueries(3):
            self._monitor(at=self.now + timezone.timedelta(hours=2))

        for index in range(5):
            player = self.create_player(f'Owner{index}')
            self._enter(player)
            self._disconnect(player=player)

        with self.assertNumQueries(3):
            self._monitor(at=self.now + timezone.timedelta(hours=2))
