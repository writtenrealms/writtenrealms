from unittest.mock import patch

from django.utils import timezone

from builders.models import Loader, MobTemplate, Rule
from config import constants as api_consts
from config.exceptions import ServiceError
from spawns.models import Item, Mob
from spawns.tasks import enter_world, exit_current_world
from spawns.services import WorldGate
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages
from worlds.services import WorldSmith
from worlds.tasks import monitor_worlds, run_world_loaders
from rest_framework import serializers


class TestStartWorld(WorldTestCase):

    def test_start_world(self):
        spawn_world = self.world.create_spawn_world()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_NEW)
        service = WorldSmith(spawn_world)
        service.start()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_RUNNING)

    def test_start_world_runs_initial_loaders(self):
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name="a sentinel",
        )
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
        )
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room,
            num_copies=2,
        )
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        self.assertEqual(
            Mob.objects.filter(world=spawn_world, rule=rule).count(),
            2,
        )
        spawn_world.refresh_from_db()
        loader.refresh_from_db()
        self.assertIsNotNone(spawn_world.last_loader_run_ts)
        self.assertIsNotNone(loader.last_processing_ts)

    def test_loader_task_after_start_does_not_duplicate_initial_load(self):
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name="a sentinel",
        )
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0,
        )
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room,
            num_copies=2,
        )
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()
        run_world_loaders()

        self.assertEqual(
            Mob.objects.filter(world=spawn_world, rule=rule).count(),
            2,
        )

    @patch('spawns.loading.run_loaders')
    def test_start_failure_recovers_to_stopped(self, mock_run_loaders):
        mock_run_loaders.side_effect = serializers.ValidationError(
            "broken loader")
        spawn_world = self.world.create_spawn_world()

        with self.assertRaises(ServiceError) as error:
            WorldSmith(spawn_world).start()

        self.assertIn("broken loader", str(error.exception))
        spawn_world.refresh_from_db()
        self.assertEqual(
            spawn_world.lifecycle,
            api_consts.WORLD_LIFECYCLE_STOPPED,
        )

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

    def test_enter_world_notifies_other_online_players(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        watcher = self.create_player(
            "Jane",
            user=self.create_user("jane@example.com"),
        )
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            enter_world(self.player.id, self.spawn_world.id)

        notification = next(
            (
                msg
                for msg in messages
                if msg["message"].get("type") == "notification.world.enter"
            ),
            None,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["player_key"], watcher.key)
        self.assertEqual(notification["message"]["text"], "John has entered the world.")
        self.assertEqual(notification["message"]["data"]["actor"]["key"], self.player.key)
        self.assertFalse(
            any(msg["player_key"] == self.player.key for msg in messages)
        )


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

    @patch("spawns.tasks.notify_exit_world")
    def test_exit_current_world_uses_wr2_exit_path(self, mock_notify_exit_world):
        result = exit_current_world(self.player.id)

        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)
        self.assertEqual(result, {"exited": True, "world_id": self.spawn_world.id})
        mock_notify_exit_world.assert_called_once_with(
            player_id=self.player.id,
            world_id=self.spawn_world.id,
            exit_to=self.spawn_world.context.id,
        )

    @patch("spawns.tasks.notify_exit_world")
    def test_exit_current_world_notifies_other_online_players(self, mock_notify_exit_world):
        watcher = self.create_player(
            "Jane",
            user=self.create_user("jane@example.com"),
        )
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            exit_current_world(self.player.id)

        notification = next(
            (
                msg
                for msg in messages
                if msg["message"].get("type") == "notification.world.leave"
            ),
            None,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["player_key"], watcher.key)
        self.assertEqual(notification["message"]["text"], "Joe has left the world.")
        self.assertEqual(notification["message"]["data"]["actor"]["key"], self.player.key)
        self.assertFalse(
            any(msg["player_key"] == self.player.key for msg in messages)
        )
