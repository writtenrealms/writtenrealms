from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from spawns.models import GameEventOutbox, ScheduledTriggerRun
from system.control_jobs import (
    CACHE_SECONDS,
    MAX_TASKS_PER_WORKER,
    SNAPSHOT_KEY,
    SNAPSHOT_LOCK_KEY,
    StaffJobsView,
    _broker_connection,
    _durable_work,
    _enqueue_action,
    _probe_broker,
    _probe_workers,
)
from worlds.models import InstanceRun
from tests.base import WorldTestCase


class StaffJobsPermissionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.staff = get_user_model().objects.create_user('jobs-staff@example.com', is_staff=True)
        self.member = get_user_model().objects.create_user('jobs-player@example.com')

    def request(self, user=None, data=None):
        request = self.factory.get('/staff/jobs/') if data is None else self.factory.post('/staff/jobs/', data, format='json')
        if user:
            force_authenticate(request, user=user)
        return StaffJobsView.as_view()(request)

    @patch('system.control_jobs._build_snapshot')
    @patch('system.control_jobs._enqueue_action')
    def test_anonymous_and_nonstaff_cannot_inspect_or_run_jobs(self, enqueue, snapshot):
        for user in (None, self.member):
            self.assertIn(self.request(user).status_code, (401, 403))
            self.assertIn(self.request(user, {'action': 'recover_triggers', 'confirmed': True}).status_code, (401, 403))
        enqueue.assert_not_called()
        snapshot.assert_not_called()

    @patch('system.control_jobs._build_snapshot', return_value={'generated_at': 'snapshot'})
    def test_snapshot_is_shared_and_cached(self, snapshot):
        first = self.request(self.staff)
        second = self.request(self.staff)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, second.data)
        snapshot.assert_called_once_with()
        self.assertEqual(cache.get(SNAPSHOT_KEY), first.data)
        self.assertIsNone(cache.get(SNAPSHOT_LOCK_KEY))
        self.assertEqual(CACHE_SECONDS, 15)

    @patch('system.control_jobs._build_snapshot')
    def test_concurrent_inspection_does_not_fan_out(self, snapshot):
        cache.set(SNAPSHOT_LOCK_KEY, True, 45)
        self.assertEqual(self.request(self.staff).status_code, 503)
        snapshot.assert_not_called()

    @patch('system.control_jobs._enqueue_action')
    def test_actions_require_explicit_confirmation_and_fixed_allowlist(self, enqueue):
        for data in (
            {'action': 'recover_triggers'},
            {'action': 'recover_triggers', 'confirmed': False},
            {'action': 'recover_triggers', 'confirmed': 'true'},
            {'action': 'arbitrary.task', 'confirmed': True},
            {'action': 'recover_triggers', 'confirmed': True, 'kwargs': {'limit': 999999}},
        ):
            self.assertEqual(self.request(self.staff, data).status_code, 400, data)
        enqueue.assert_not_called()

    @patch('system.control_jobs._enqueue_action')
    def test_enqueue_is_audited_and_duplicate_actions_are_throttled(self, enqueue):
        from system.models import StaffAuditEvent
        cache.set(SNAPSHOT_KEY, {'old': True})
        response = self.request(self.staff, {'action': 'recover_triggers', 'confirmed': True})
        self.assertEqual(response.status_code, 202)
        enqueue.assert_called_once_with('recover_triggers', response.data['task_id'])
        event = StaffAuditEvent.objects.get(action='job.enqueued')
        self.assertEqual(event.actor_id, self.staff.id)
        self.assertEqual(event.target, 'recover_triggers')
        self.assertEqual(event.details, {'task_id': response.data['task_id'], 'limit': 100})
        self.assertIsNone(cache.get(SNAPSHOT_KEY))
        self.assertEqual(self.request(self.staff, {'action': 'recover_triggers', 'confirmed': True}).status_code, 429)
        enqueue.assert_called_once()

    @patch('system.control_jobs._enqueue_action', side_effect=RuntimeError('amqp://private:secret@host'))
    def test_publication_failure_is_explicit_and_does_not_expose_connection_secrets(self, enqueue):
        from system.models import StaffAuditEvent
        response = self.request(self.staff, {'action': 'recover_actions', 'confirmed': True})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', str(response.data))
        self.assertEqual(StaffAuditEvent.objects.get().action, 'job.enqueue_unconfirmed')


class JobsProbeTests(SimpleTestCase):
    @patch('system.control_jobs.app.connection_for_read')
    def test_broker_reads_have_finite_socket_timeouts(self, connection):
        _broker_connection()
        arguments = connection.call_args.kwargs
        self.assertEqual(arguments['connect_timeout'], 1.0)
        self.assertEqual(arguments['transport_options']['read_timeout'], 1.0)
        self.assertEqual(arguments['transport_options']['write_timeout'], 1.0)

    @patch('system.control_jobs.app')
    @patch('system.control_jobs._broker_connection')
    def test_broker_passively_observes_queues_without_inventing_unacked_counts(self, connect, app):
        connection = connect.return_value.__enter__.return_value
        connection.transport.driver_type = 'amqp'
        app.amqp.queues = {'celery': object()}
        channel = connection.channel.return_value.__enter__.return_value
        channel.queue_declare.return_value = SimpleNamespace(message_count=8, consumer_count=2)
        result = _probe_broker()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['queues'], [{'name': 'celery', 'ready': 8, 'consumers': 2, 'unacknowledged': None}])
        channel.queue_declare.assert_called_once_with(queue='celery', passive=True)
        connection.ensure_connection.assert_called_once_with(max_retries=0)

    @patch('system.control_jobs._broker_connection', side_effect=RuntimeError('amqp://private:secret@host'))
    def test_unavailable_broker_and_workers_do_not_report_false_zeroes(self, connect):
        for result in (_probe_broker(), _probe_workers()):
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn('secret', str(result))

    @patch('system.control_jobs.app')
    @patch('system.control_jobs._broker_connection')
    def test_worker_task_details_are_bounded_and_exclude_payloads(self, connect, app):
        inspector = app.control.inspect.return_value
        task = {'id': 'test', 'name': 'safe.task', 'args': ['private:secret'], 'kwargs': {'token': 'secret'}, 'result': 'secret', 'time_start': 42}
        inspector.active.return_value = {'worker@one': [task] * (MAX_TASKS_PER_WORKER + 1)}
        inspector.reserved.return_value = {'worker@one': []}
        inspector.scheduled.return_value = {'worker@one': []}
        result = _probe_workers()
        self.assertEqual(result['status'], 'ok')
        worker = result['items'][0]
        self.assertEqual(worker['active'], MAX_TASKS_PER_WORKER + 1)
        self.assertEqual(len(worker['tasks']), MAX_TASKS_PER_WORKER)
        self.assertTrue(worker['tasks_truncated'])
        self.assertNotIn('secret', str(result))
        self.assertEqual(set(worker['tasks'][0]), {'id', 'name', 'state', 'eta', 'started_at'})
        self.assertEqual(app.control.inspect.call_args.kwargs['timeout'], 1.0)

    @patch('system.control_jobs.app')
    @patch('system.control_jobs._broker_connection')
    def test_missing_inspect_reply_is_null_not_zero(self, connect, app):
        inspector = app.control.inspect.return_value
        inspector.active.return_value = {'worker@one': []}
        inspector.reserved.return_value = None
        inspector.scheduled.return_value = {'worker@one': [{'eta': '2026-10-01T12:00:00Z', 'request': {'id': 'scheduled', 'name': 'safe.task', 'args': ['secret']}}]}
        result = _probe_workers()
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['items'][0]['reserved'])
        self.assertEqual(result['items'][0]['scheduled'], 1)
        self.assertEqual(result['items'][0]['tasks'][0]['eta'], '2026-10-01T12:00:00Z')
        self.assertNotIn('secret', str(result))

    @patch('system.control_jobs.app')
    @patch('system.control_jobs._broker_connection')
    def test_recovery_enqueue_uses_fixed_batch_expiry_and_no_retry(self, connect, app):
        _enqueue_action('recover_triggers', 'task-id')
        connect.assert_called_once_with(write=True)
        app.send_task.assert_called_once_with(
            'spawns.tasks.run_scheduled_trigger_steps',
            kwargs={'limit': 100}, task_id='task-id', expires=60,
            ignore_result=True, retry=False,
            connection=connect.return_value.__enter__.return_value,
        )


class DurableJobsTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()

    def trigger_run(self, **overrides):
        values = {
            'runtime_world': self.spawn_world,
            'room': self.room,
            'actor_type': 'player',
            'actor_id': self.player.id,
            'actor_key': self.player.key,
            'next_run_ts': self.now - timedelta(minutes=1),
        }
        values.update(overrides)
        return ScheduledTriggerRun.objects.create(**values)

    def test_durable_totals_use_fixed_query_count_and_safe_recent_failures(self):
        self.trigger_run()
        self.trigger_run(next_run_ts=self.now + timedelta(minutes=1))
        failed = self.trigger_run(status='cancelled', failure_code='step_exception', last_error='password=secret', completed_ts=self.now)
        old = self.trigger_run(status='cancelled', failure_code='old')
        ScheduledTriggerRun.objects.filter(pk=old.pk).update(modified_ts=self.now - timedelta(days=2))
        self.trigger_run(status='completed')
        GameEventOutbox.objects.create(event_type='test', attempt_count=2, available_ts=self.now - timedelta(minutes=1))
        GameEventOutbox.objects.create(event_type='test', available_ts=self.now + timedelta(minutes=1))
        with self.assertNumQueries(9):
            result = _durable_work(self.now)
        triggers = result['scheduled_triggers']
        self.assertEqual((triggers['pending'], triggers['due'], triggers['failed']), (2, 1, 1))
        self.assertEqual(triggers['recent_failures'][0]['id'], failed.id)
        self.assertNotIn('secret', str(result))
        outbox = result['event_outbox']
        self.assertEqual((outbox['pending'], outbox['due'], outbox['retrying']), (2, 1, 1))

    def test_due_backlog_excludes_controlled_instances(self):
        self.trigger_run()
        InstanceRun.objects.create(
            base_world=self.world, template_world=self.world,
            spawned_world=self.spawn_world, ref='paused',
            single_player=True, time_control=True, time_paused=True,
        )
        result = _durable_work(self.now)
        self.assertEqual(result['scheduled_triggers']['pending'], 1)
        self.assertEqual(result['scheduled_triggers']['due'], 0)

    def test_hundreds_of_failed_jobs_keep_query_cost_and_payload_bounded(self):
        ScheduledTriggerRun.objects.bulk_create([
            ScheduledTriggerRun(
                runtime_world=self.spawn_world, room=self.room,
                actor_type='player', actor_id=self.player.id,
                actor_key=self.player.key, next_run_ts=self.now,
                status='cancelled', failure_code='step_exception',
                last_error='private payload', completed_ts=self.now,
            )
            for _ in range(505)
        ])
        with self.assertNumQueries(9):
            result = _durable_work(self.now)
        self.assertEqual(result['scheduled_triggers']['failed'], 505)
        self.assertEqual(len(result['scheduled_triggers']['recent_failures']), 10)
        self.assertNotIn('private payload', str(result))

    def test_outbox_due_excludes_claims_and_dependent_batches(self):
        from uuid import uuid4
        GameEventOutbox.objects.create(event_type='leased', available_ts=self.now, claimed_until=self.now + timedelta(minutes=1))
        GameEventOutbox.objects.create(event_type='dependent', available_ts=self.now, depends_on_batch_id=uuid4())
        result = _durable_work(self.now)['event_outbox']
        self.assertEqual(result['pending'], 2)
        self.assertEqual(result['due'], 0)
        self.assertIsNone(result['oldest_due_at'])
