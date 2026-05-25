from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from config import constants as api_consts
from spawns.models import Item, Mob
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.services import WorldSmith
from worlds.tasks import monitor_worlds
from rest_framework import serializers


class TestStartWorld(WorldTestCase):

    def test_start_world(self):
        spawn_world = self.world.create_spawn_world()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_NEW)
        service = WorldSmith(spawn_world)
        service.start()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_RUNNING)

    def test_stop_world(self):
        spawn_world = self.world.create_spawn_world()
        spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPING)
        spawn_room = spawn_world.context.config.starting_room
        mob = Mob.objects.create(
            world=spawn_world,
            room=spawn_room,
            name="Target",
            keywords="target",
        )
        service = WorldSmith(spawn_world)
        service.stop()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_STOPPED)
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())

    def test_reset_world(self):
        spawn_world = self.world.create_spawn_world()
        spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPED)
        spawn_room = spawn_world.context.config.starting_room

        mob = Mob.objects.create(
            world=spawn_world,
            room=spawn_room,
            name="Target",
            keywords="target",
        )
        ground_item = Item.objects.create(
            world=spawn_world,
            container=spawn_room,
            name="Rock",
        )
        held_item = Item.objects.create(
            world=spawn_world,
            container=self.player,
            name="Apple",
        )

        service = WorldSmith(spawn_world)
        service.reset()

        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_STOPPED)
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertFalse(Item.objects.filter(pk=ground_item.pk).exists())
        self.assertTrue(Item.objects.filter(pk=held_item.pk).exists())

    def test_reset_world_requires_stopped_lifecycle(self):
        spawn_world = self.world.create_spawn_world()
        service = WorldSmith(spawn_world)

        with self.assertRaises(serializers.ValidationError):
            service.reset()

    @override_settings(
        WR_AI_EVENT_FORWARD_URL="http://localhost:8071/v1/events",
        WR_AI_EVENT_TYPES="mob.destroyed",
    )
    @patch("spawns.tasks.forward_event_to_ai_sidecar.delay")
    def test_cleanup_enqueues_sidecar_destroy_signal_for_removed_mobs(self, mock_forward_delay):
        spawn_world = self.world.create_spawn_world()
        spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPED)
        spawn_room = spawn_world.context.config.starting_room
        mob = Mob.objects.create(
            world=spawn_world,
            room=spawn_room,
            name="Target",
            keywords="target",
        )

        with self.captureOnCommitCallbacks(execute=True):
            spawn_world.cleanup()

        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        mock_forward_delay.assert_called_once()
        kwargs = mock_forward_delay.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "mob.destroyed")
        self.assertEqual(kwargs["actor_key"], mob.key)
        self.assertEqual(kwargs["event_data"]["source"], "world.cleanup")
        self.assertEqual(kwargs["event_data"]["reason"], "world_stop")


class TestEnterWorld(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()
        WorldSmith(self.spawn_world).start()
        self.player = self.create_player('John')

    def test_enter_world(self):
        self.assertFalse(self.player.in_game)
        WorldGate(world=self.spawn_world, player=self.player).enter()
        self.assertTrue(self.player.in_game)


class TestMonitorWorldsIdlePlayers(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        WorldSmith(self.spawn_world).start()
        self.player.refresh_from_db()
        self.player.in_game = True
        self.player.last_action_ts = timezone.now()
        self.player.save(update_fields=["in_game", "last_action_ts"])

    def test_monitor_worlds_disconnects_timed_out_players_before_stopping_world(self):
        self.spawn_world.last_played_ts = timezone.now() - timezone.timedelta(minutes=6)
        self.spawn_world.save(update_fields=["last_played_ts"])
        self.player.last_action_ts = timezone.now() - timezone.timedelta(
            seconds=api_consts.IDLE_TIMEOUT + 1
        )
        self.player.save(update_fields=["last_action_ts"])

        disconnected_player_ids = []

        def disconnect_player(player, spawn_world):
            disconnected_player_ids.append(player.id)
            player.in_game = False
            player.save(update_fields=["in_game"])

        with patch("worlds.tasks._disconnect_idle_player", side_effect=disconnect_player), patch(
            "worlds.tasks.WorldSmith.stop"
        ) as mock_stop:
            monitor_worlds()

        self.assertEqual(disconnected_player_ids, [self.player.id])
        mock_stop.assert_called_once()

    def test_monitor_worlds_respects_multiplayer_idle_logout_opt_out(self):
        self.spawn_world.last_played_ts = timezone.now() - timezone.timedelta(minutes=6)
        self.spawn_world.save(update_fields=["last_played_ts"])
        self.player.config.idle_logout = False
        self.player.config.save(update_fields=["idle_logout"])
        self.player.last_action_ts = timezone.now() - timezone.timedelta(
            seconds=api_consts.IDLE_TIMEOUT + 1
        )
        self.player.save(update_fields=["last_action_ts"])

        with patch("worlds.tasks._disconnect_idle_player") as mock_disconnect, patch(
            "worlds.tasks.WorldSmith.stop"
        ) as mock_stop:
            monitor_worlds()

        mock_disconnect.assert_not_called()
        mock_stop.assert_not_called()

    def test_monitor_worlds_uses_builder_idle_timeout_for_builders(self):
        self.spawn_world.last_played_ts = timezone.now() - timezone.timedelta(minutes=6)
        self.spawn_world.save(update_fields=["last_played_ts"])
        self.player.is_builder = True
        self.player.last_action_ts = timezone.now() - timezone.timedelta(
            seconds=api_consts.IDLE_TIMEOUT + 1
        )
        self.player.save(update_fields=["is_builder", "last_action_ts"])

        with patch("worlds.tasks._disconnect_idle_player") as mock_disconnect, patch(
            "worlds.tasks.WorldSmith.stop"
        ) as mock_stop:
            monitor_worlds()

        mock_disconnect.assert_not_called()
        mock_stop.assert_not_called()

    @patch("worlds.tasks.notify_exit_world")
    def test_monitor_worlds_idle_logout_uses_wr2_exit_path(self, mock_notify_exit_world):
        self.spawn_world.last_played_ts = timezone.now()
        self.spawn_world.save(update_fields=["last_played_ts"])
        self.player.last_action_ts = timezone.now() - timezone.timedelta(
            seconds=api_consts.IDLE_TIMEOUT + 1
        )
        self.player.save(update_fields=["last_action_ts"])

        monitor_worlds()

        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)
        mock_notify_exit_world.assert_called_once_with(
            player_id=self.player.id,
            world_id=self.spawn_world.id,
            exit_to=self.spawn_world.context.id,
        )
