from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.utils import timezone

from spawns.instance_time import process_deferred_work, recover_due_instances
from spawns.models import InstanceClockWork
from spawns.tasks import resume_instance_schedulers, run_game_heartbeat, pulse_instance_heartbeat
from tests.base import WorldTestCase
from tests.utils import capture_game_messages
from worlds.models import InstanceRun


class TestInstanceClockRecovery(WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.enterContext(capture_game_messages())
        self.run = InstanceRun.objects.create(
            base_world=self.world, template_world=self.world, spawned_world=self.spawn_world,
            owner=self.player, ref='deferred-clock-work', single_player=True, time_control=True,
            simulation_time=timezone.now(),
        )
        self.work = InstanceClockWork.objects.create(
            world=self.spawn_world, kind='command', dedupe_key='one-command',
            payload={'command_type': 'look', 'player_id': self.player.pk, 'payload': {}},
            due_at=timezone.now() - timedelta(seconds=1),
        )

    def test_live_work_executes_once_and_duplicate_delivery_is_empty(self):
        self.assertEqual(process_deferred_work(run_id=self.run.pk, expected_generation=0), 1)
        self.assertEqual(process_deferred_work(run_id=self.run.pk, expected_generation=0), 0)
        self.assertFalse(InstanceClockWork.objects.filter(pk=self.work.pk).exists())

    def test_live_deferred_tracker_publishes_its_events(self):
        from spawns.actions.base import ActionResult
        from spawns.events import GameEvent
        event = GameEvent('test.tracker', {}, [])
        self.work.kind = 'tracker'
        self.work.payload = {}
        self.work.save(update_fields=['kind', 'payload'])
        with patch('spawns.actions.mob_movement.ResolveTrackerChaseAction.execute', return_value=ActionResult(events=[event])), patch('spawns.events.publish_events') as publish:
            self.assertEqual(process_deferred_work(run_id=self.run.pk), 1)
        publish.assert_called_once_with([event])

    def test_paused_or_stale_delivery_cannot_execute_live_work(self):
        self.run.time_paused = True
        self.run.save(update_fields=['time_paused'])
        self.assertEqual(process_deferred_work(run_id=self.run.pk, expected_generation=0), 0)
        self.run.time_paused = False
        self.run.time_generation = 1
        self.run.save(update_fields=['time_paused', 'time_generation'])
        self.assertEqual(process_deferred_work(run_id=self.run.pk, expected_generation=0), 0)
        self.assertTrue(InstanceClockWork.objects.filter(pk=self.work.pk).exists())

    def test_failed_live_work_rolls_back_and_can_be_retried(self):
        with patch('spawns.instance_time._execute_work', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                process_deferred_work(run_id=self.run.pk)
        self.assertTrue(InstanceClockWork.objects.filter(pk=self.work.pk).exists())
        self.assertEqual(process_deferred_work(run_id=self.run.pk), 1)

    def test_recovery_deduplicates_slow_delivery_and_releases_failed_enqueue_claim(self):
        with patch('spawns.tasks.continue_instance_work.delay') as enqueue:
            self.assertEqual(recover_due_instances(), 1)
            self.assertEqual(recover_due_instances(), 0)
            self.assertEqual(enqueue.call_count, 1)
        cache.clear()
        with patch('spawns.tasks.continue_instance_work.delay', side_effect=RuntimeError('broker unavailable')):
            with self.assertRaises(RuntimeError):
                recover_due_instances()
        with patch('spawns.tasks.continue_instance_work.delay') as enqueue:
            self.assertEqual(recover_due_instances(), 1)
            enqueue.assert_called_once()

    def test_old_resume_job_cannot_rearm_a_new_pause(self):
        self.run.time_paused = True
        self.run.time_generation = 1
        self.run.save(update_fields=['time_paused', 'time_generation'])
        with patch('spawns.instance_time.schedule_deferred_work') as enqueue:
            resume_instance_schedulers.run(self.run.pk, 0)
        enqueue.assert_not_called()

    def test_heartbeat_fanout_coalesces_busy_instances_and_excludes_paused_runs(self):
        from config import constants
        self.spawn_world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=['lifecycle'])
        with patch('spawns.tasks._run_game_heartbeat', return_value={}), patch('spawns.tasks.pulse_instance_heartbeat.apply_async') as enqueue:
            run_game_heartbeat(enqueue_instances=True)
            run_game_heartbeat(enqueue_instances=True)
            enqueue.assert_called_once()
            task_args = enqueue.call_args.kwargs['kwargs']
            self.assertEqual(task_args['world_id'], self.spawn_world.pk)
            self.assertIn('expires', enqueue.call_args.kwargs)
        self.run.time_paused = True
        self.run.time_generation = 1
        self.run.save(update_fields=['time_paused', 'time_generation'])
        with patch('spawns.tasks._run_game_heartbeat') as pulse:
            self.assertEqual(pulse_instance_heartbeat.run(**task_args), {'skipped': True})
            pulse.assert_not_called()
        self.assertIsNone(cache.get(f'instance-heartbeat:{self.spawn_world.pk}'))
        with patch('spawns.tasks._run_game_heartbeat', return_value={}), patch('spawns.tasks.pulse_instance_heartbeat.apply_async') as enqueue:
            run_game_heartbeat(enqueue_instances=True)
            enqueue.assert_not_called()

    def test_heartbeat_delivery_runs_once_in_its_own_world_scope(self):
        from spawns.instance_clock import scoped_world_id
        key = f'instance-heartbeat:{self.spawn_world.pk}'
        cache.set(key, 'pulse', timeout=30)
        scopes = []
        with patch('spawns.tasks._run_game_heartbeat', side_effect=lambda: scopes.append(scoped_world_id()) or {'players': 1}):
            self.assertEqual(pulse_instance_heartbeat.run(self.spawn_world.pk, 0, 'pulse'), {'players': 1})
            self.assertEqual(pulse_instance_heartbeat.run(self.spawn_world.pk, 0, 'pulse'), {'skipped': True})
        self.assertEqual(scopes, [self.spawn_world.pk])

    def test_heartbeat_enqueue_failure_releases_claim_for_the_next_pulse(self):
        from config import constants
        self.spawn_world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=['lifecycle'])
        with patch('spawns.tasks._run_game_heartbeat', return_value={}), patch('spawns.tasks.pulse_instance_heartbeat.apply_async', side_effect=RuntimeError('broker unavailable')):
            with self.assertRaises(RuntimeError):
                run_game_heartbeat(enqueue_instances=True)
        self.assertIsNone(cache.get(f'instance-heartbeat:{self.spawn_world.pk}'))
