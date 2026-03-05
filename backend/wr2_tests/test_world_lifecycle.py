from unittest.mock import patch

from django.test import override_settings

from config import constants as api_consts
from spawns.models import Mob
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.services import WorldSmith


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
