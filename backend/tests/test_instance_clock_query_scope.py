from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from spawns.instance_clock import clock_guard, controlled_run, time_control_run
from tests.base import WorldTestCase
from worlds.models import InstanceRun, World, WorldConfig


class TestInstanceClockQueryScope(WorldTestCase):
    def _instance_queries(self, captured):
        return [
            query for query in captured
            if 'FROM "worlds_instancerun"' in query['sql']
        ]

    def _template(self):
        return World.objects.new_world(
            name='Clocked instance', author=self.user, instance_of=self.world,
            config=WorldConfig.objects.create(
                instance_single_player=True, instance_time_control=True,
            ),
        )

    def _run(self, template, spawned):
        return InstanceRun.objects.create(
            base_world=self.world, template_world=template, spawned_world=spawned,
            ref=f'clock-{spawned.pk}', owner=self.player,
            single_player=True, time_control=True, time_paused=True,
            simulation_time=timezone.now(),
        )

    def test_ordinary_command_reuses_negative_lookup_for_objects_and_ids(self):
        with CaptureQueriesContext(connection) as queries:
            with clock_guard(self.spawn_world) as run:
                self.assertIsNone(run)
                for _ in range(10):
                    self.assertIsNone(time_control_run(self.spawn_world))
                    self.assertIsNone(controlled_run(self.spawn_world.pk))
                    with clock_guard(self.spawn_world.pk) as nested:
                        self.assertIsNone(nested)
        self.assertEqual(len(self._instance_queries(queries)), 1)

        # Reuse is local to the guard, never a worker-process/world cache.
        with CaptureQueriesContext(connection) as queries:
            self.assertIsNone(time_control_run(self.spawn_world.pk))
            self.assertIsNone(time_control_run(self.spawn_world.pk))
        self.assertEqual(len(self._instance_queries(queries)), 2)

    def test_nested_ordinary_world_scope_and_exception_restore_outer_context(self):
        other = self.world.create_spawn_world()
        with clock_guard(self.spawn_world):
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                with clock_guard(other):
                    with self.assertNumQueries(0):
                        self.assertIsNone(time_control_run(self.spawn_world.pk))
                        self.assertIsNone(time_control_run(other.pk))
                    raise RuntimeError('stop')
            with self.assertNumQueries(0):
                self.assertIsNone(time_control_run(self.spawn_world.pk))
            with CaptureQueriesContext(connection) as queries:
                self.assertIsNone(time_control_run(other.pk))
            self.assertEqual(len(self._instance_queries(queries)), 1)

    def test_nested_paused_instance_keeps_its_locked_clock(self):
        template = self._template()
        spawned = template.create_spawn_world()
        expected = self._run(template, spawned)
        with clock_guard(self.spawn_world):
            with clock_guard(spawned) as run:
                self.assertEqual(run.pk, expected.pk)
                self.assertTrue(run.time_paused)
                with self.assertNumQueries(0):
                    self.assertIs(controlled_run(spawned.pk), run)
                    with clock_guard(spawned.pk) as nested:
                        self.assertIs(nested, run)
                    self.assertIsNone(time_control_run(self.spawn_world.pk))
                run.time_paused = False
                self.assertIsNone(controlled_run(spawned.pk))

    def test_missing_instance_run_is_not_cached_when_created_during_guard(self):
        template = self._template()
        spawned = template.create_spawn_world()
        with clock_guard(self.spawn_world):
            with clock_guard(spawned) as absent:
                self.assertIsNone(absent)
                expected = self._run(template, spawned)
                self.assertEqual(time_control_run(spawned.pk).pk, expected.pk)
                with clock_guard(spawned) as run:
                    self.assertEqual(run.pk, expected.pk)
                    self.assertTrue(run.time_paused)
