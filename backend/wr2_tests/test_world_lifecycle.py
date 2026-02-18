from config import constants as api_consts
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
        service = WorldSmith(spawn_world)
        service.stop()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_STOPPED)


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