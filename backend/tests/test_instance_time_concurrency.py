from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.instance_time import advance, configure
from spawns.models import Player, Mob
from tests.combat_fixtures import create_combat_encounter
from spawns.tasks import WR2_STANDING_REGEN_RATE
from tests.utils import apply_basic_stat_system
from worlds.instances import create_fresh_instance_run
from worlds.models import InstanceRun, World, WorldConfig


@skipUnless(connection.vendor == 'postgresql', 'Instance advancement row-lock tests require PostgreSQL.')
class TestInstanceTimeConcurrency(TransactionTestCase):
    """Use independent database connections and observed PostgreSQL lock waits."""

    def setUp(self):
        super().setUp()
        self.enterContext(patch('spawns.tasks.resume_instance_schedulers.delay'))
        self.enterContext(patch('spawns.tasks.reconcile_combat_room.apply_async'))
        self.enterContext(patch('spawns.events.publish_to_player'))
        self.user = get_user_model().objects.create_user('instance-clock-race@example.com', 'p')
        self.world = World.objects.new_world(
            name='Clock Concurrency', author=self.user,
            config=WorldConfig.objects.create(), is_multiplayer=True,
        )
        apply_basic_stat_system(self.world)
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=['default_roam_chance'])
        self.base_runtime = self.world.create_spawn_world()
        self.template = World.objects.new_world(
            name='Private Clocks', author=self.user, instance_of=self.world,
            is_multiplayer=True,
            config=WorldConfig.objects.create(instance_single_player=True, instance_time_control=True),
        )
        self.owner, self.run = self._make_run('First Owner')

    def _make_run(self, name):
        owner = Player.objects.create(
            name=name, user=self.user, world=self.base_runtime,
            room=self.world.config.starting_room,
            in_game=True, health=100, stamina=50,
        )
        run = create_fresh_instance_run(self.template, leader=owner)
        run.spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        run.spawned_world.save(update_fields=['lifecycle'])
        owner.world = run.spawned_world
        owner.room = self.template.config.starting_room
        owner.save(update_fields=['world', 'room'])
        mob = Mob.objects.create(name='Opponent', world=run.spawned_world, room=owner.room,
                                 health=10000, health_max=10000, fights_back=False)
        create_combat_encounter(world=run.spawned_world, room=owner.room, player=owner, mob=mob,
                                resolution_interval=2, next_resolution_ts=timezone.now() + timedelta(seconds=2))
        run.pause_in_combat = True
        run.time_paused = True
        run.save(update_fields=['pause_in_combat', 'time_paused'])
        return owner, run

    def _advance_in_connection(self, run_id, player_id, *, ready, barrier=None,
                               generation=0):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute('SELECT pg_backend_pid()')
                ready.put(cursor.fetchone()[0])
            if barrier is not None:
                barrier.wait(timeout=5)
            try:
                result = advance(
                    run_id=run_id, player_id=player_id,
                    expected_tick=0, expected_generation=generation,
                    expected_pending_revision=0,
                )
                return ('advanced', result['tick'])
            except ActionError as exc:
                return ('error', exc.code)
        finally:
            connection.close()

    def _wait_for_blocked_connection(self, backend_pid):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT cardinality(pg_blocking_pids(%s)) > 0', [backend_pid])
                if cursor.fetchone()[0]:
                    return
            sleep(0.01)
        self.fail(f'Backend {backend_pid} never reached the expected row-lock wait.')

    def test_two_simultaneous_advances_of_same_tick_commit_only_once(self):
        ready = Queue()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            with transaction.atomic():
                InstanceRun.objects.select_for_update().get(pk=self.run.pk)
                futures = [executor.submit(
                    self._advance_in_connection, self.run.pk, self.owner.pk,
                    ready=ready, barrier=barrier,
                ) for _ in range(2)]
                pids = [ready.get(timeout=5), ready.get(timeout=5)]
                for pid in pids:
                    self._wait_for_blocked_connection(pid)
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertCountEqual(outcomes, [('advanced', 1), ('error', 'stale_instance_turn')])
        self.run.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.run.simulation_tick, 1)
        self.assertEqual(self.owner.stamina, 50 + WR2_STANDING_REGEN_RATE)

    def test_resume_fences_an_advance_already_waiting_on_the_run_lock(self):
        ready = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                InstanceRun.objects.select_for_update().get(pk=self.run.pk)
                advancing = executor.submit(
                    self._advance_in_connection, self.run.pk, self.owner.pk, ready=ready,
                )
                self._wait_for_blocked_connection(ready.get(timeout=5))
                configure(self.owner.pk, pause_in_combat=False, expected_generation=0)
            outcome = advancing.result(timeout=15)
        self.assertEqual(outcome, ('error', 'stale_instance_turn'))
        self.run.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertFalse(self.run.time_paused)
        self.assertFalse(self.run.pause_in_combat)
        self.assertEqual(self.run.simulation_tick, 0)
        self.assertEqual(self.owner.stamina, 50)

    def test_one_locked_instance_does_not_block_an_unrelated_run(self):
        second_owner, second_run = self._make_run('Second Owner')
        first_ready = Queue()
        second_ready = Queue()
        with ThreadPoolExecutor(max_workers=2) as executor:
            with transaction.atomic():
                InstanceRun.objects.select_for_update().get(pk=self.run.pk)
                blocked = executor.submit(
                    self._advance_in_connection, self.run.pk, self.owner.pk,
                    ready=first_ready,
                )
                self._wait_for_blocked_connection(first_ready.get(timeout=5))
                unrelated = executor.submit(
                    self._advance_in_connection, second_run.pk, second_owner.pk,
                    ready=second_ready,
                )
                second_ready.get(timeout=5)
                # Completion must occur while the first run's lock remains
                # held, rather than merely after both workers eventually exit.
                self.assertEqual(unrelated.result(timeout=5), ('advanced', 1))
                self.assertFalse(blocked.done())
                second_run.refresh_from_db()
                self.assertEqual(second_run.simulation_tick, 1)
                self.assertEqual(InstanceRun.objects.get(pk=self.run.pk).simulation_tick, 0)
            self.assertEqual(blocked.result(timeout=15), ('advanced', 1))
        self.run.refresh_from_db()
        self.assertEqual(self.run.simulation_tick, 1)
