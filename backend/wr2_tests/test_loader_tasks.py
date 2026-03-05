import mock

from config import constants as api_consts
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig
from worlds.tasks import run_world_loaders


class TestRunWorldLoadersTask(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)

    @mock.patch('worlds.tasks.run_loaders')
    def test_runs_for_running_spawn_worlds_only(self, mock_run_loaders):
        stopped_spawn = self.world.create_spawn_world()
        stopped_spawn.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPED)

        never_reload_config = WorldConfig.objects.create(never_reload=True)
        never_reload_root = World.objects.new_world(
            name='Never Reload Root',
            author=self.user,
            config=never_reload_config)
        never_reload_spawn = never_reload_root.create_spawn_world()
        never_reload_spawn.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)

        run_world_loaders()

        called_world_ids = {
            call.kwargs['world'].id
            for call in mock_run_loaders.call_args_list
        }
        self.assertIn(self.spawn_world.id, called_world_ids)
        self.assertNotIn(stopped_spawn.id, called_world_ids)
        self.assertNotIn(never_reload_spawn.id, called_world_ids)

    @mock.patch('worlds.tasks.run_loaders')
    def test_continues_after_individual_world_failure(self, mock_run_loaders):
        second_root = World.objects.new_world(
            name='Second Root',
            author=self.user,
            config=WorldConfig.objects.create())
        second_spawn = second_root.create_spawn_world()
        second_spawn.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)

        def side_effect(*args, **kwargs):
            world = kwargs['world']
            if world.id == self.spawn_world.id:
                raise RuntimeError('boom')
            return None

        mock_run_loaders.side_effect = side_effect

        run_world_loaders()

        called_world_ids = {
            call.kwargs['world'].id
            for call in mock_run_loaders.call_args_list
        }
        self.assertIn(self.spawn_world.id, called_world_ids)
        self.assertIn(second_spawn.id, called_world_ids)
