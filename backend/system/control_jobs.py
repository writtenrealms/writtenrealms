"""Bounded, staff-only operational snapshots and explicit recovery actions.

These are observations, not a Celery result/event history. Inspect replies can be
partial and broker-unacknowledged totals require a separate management service.
"""

from datetime import timedelta
from uuid import uuid4

from django.core.cache import cache
from django.db.models import Count, Min, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.celery import app
from core.permissions import IsStaffUser
from spawns.instance_clock import live_worlds
from spawns.models import GameEventOutbox, PreparedGameAction, ScheduledTriggerRun


CACHE_SECONDS = 15
PROBE_TIMEOUT = 1.0
MAX_QUEUES = 20
MAX_WORKERS = 50
MAX_TASKS_PER_WORKER = 100
SNAPSHOT_KEY = 'staff:jobs:snapshot:v1'
SNAPSHOT_LOCK_KEY = 'staff:jobs:snapshot-lock:v1'
MANUAL_ACTIONS = {
    'recover_triggers': {
        'label': 'Process due triggers',
        'description': 'Queue one bounded recovery pass for due trigger steps and quest interactions.',
        'task': 'spawns.tasks.run_scheduled_trigger_steps',
    },
    'recover_actions': {
        'label': 'Process due actions',
        'description': 'Queue one bounded recovery pass for pending door actions.',
        'task': 'spawns.tasks.run_due_prepared_game_actions',
    },
    'recover_combat': {
        'label': 'Recover due combat',
        'description': 'Queue one bounded recovery pass for combat encounters and reconciliation.',
        'task': 'spawns.tasks.run_due_combat_encounters',
    },
}


def _broker_connection(*, write=False):
    # No connection retry loop and finite socket reads/writes: a down broker must
    # not hold an HTTP worker indefinitely. Works with the supported AMQP broker.
    connection_factory = app.connection_for_write if write else app.connection_for_read
    return connection_factory(
        connect_timeout=PROBE_TIMEOUT,
        transport_options={
            **(app.conf.broker_transport_options or {}),
            'read_timeout': PROBE_TIMEOUT,
            'write_timeout': PROBE_TIMEOUT,
        },
    )


def _probe_broker():
    result = {
        'status': 'unavailable',
        'queues': [],
        'detail': 'Broker inspection unavailable.',
    }
    try:
        with _broker_connection() as connection:
            if connection.transport.driver_type != 'amqp':
                result['detail'] = 'Passive queue inspection is supported for AMQP brokers only.'
                return result
            connection.ensure_connection(max_retries=0)
            names = sorted(app.amqp.queues.keys())[:MAX_QUEUES]
            for name in names:
                queue = {
                    'name': name,
                    'ready': None,
                    'consumers': None,
                    'unacknowledged': None,
                }
                try:
                    # Passive declaration observes existing queues without
                    # creating, consuming, purging, or changing broker state.
                    with connection.channel() as channel:
                        observed = channel.queue_declare(queue=name, passive=True)
                        queue['ready'] = observed.message_count
                        queue['consumers'] = observed.consumer_count
                except Exception:
                    # Never return exception text: broker errors can contain
                    # credentials and host addresses from connection URLs.
                    queue['detail'] = 'Queue unavailable or not declared.'
                result['queues'].append(queue)
            available = sum(row['ready'] is not None for row in result['queues'])
            result['status'] = ('ok' if available == len(names) else 'partial') if available else 'unavailable'
            result['detail'] = (
                'Ready messages and consumers are broker observations. '
                'Unacknowledged totals require RabbitMQ management metrics and are unavailable here. '
                f'At most {MAX_QUEUES} configured queues are shown.'
            )
    except Exception:
        pass
    return result


def _safe_task(task, state):
    request = task.get('request', {}) if state == 'scheduled' else task
    if not isinstance(request, dict):
        request = {}
    # Explicit allowlist. Args, kwargs, delivery routing, raw exceptions and
    # result payloads may contain credentials or player text.
    return {
        'id': str(request.get('id') or '')[:100],
        'name': str(request.get('name') or request.get('type') or '')[:255],
        'state': state,
        'eta': task.get('eta') if state == 'scheduled' else None,
        'started_at': request.get('time_start') if state == 'active' else None,
    }


def _probe_workers():
    observations = {}
    unavailable = []
    try:
        with _broker_connection() as connection:
            connection.ensure_connection(max_retries=0)
            inspector = app.control.inspect(timeout=PROBE_TIMEOUT, connection=connection)
            for state in ('active', 'reserved', 'scheduled'):
                try:
                    replies = getattr(inspector, state)()
                except Exception:
                    replies = None
                if not isinstance(replies, dict) or not replies:
                    unavailable.append(state)
                    continue
                observations[state] = replies
    except Exception:
        unavailable = ['active', 'reserved', 'scheduled']
    names = sorted({name for replies in observations.values() for name in replies})[:MAX_WORKERS]
    workers = []
    for name in names:
        worker = {'name': name, 'tasks': []}
        for state in ('active', 'reserved', 'scheduled'):
            tasks = observations.get(state, {}).get(name)
            worker[state] = len(tasks) if isinstance(tasks, list) else None
            if isinstance(tasks, list):
                remaining = MAX_TASKS_PER_WORKER - len(worker['tasks'])
                worker['tasks'].extend(
                    _safe_task(task, state) for task in tasks[:remaining]
                    if isinstance(task, dict)
                )
        worker['tasks_truncated'] = sum(worker[state] or 0 for state in ('active', 'reserved', 'scheduled')) > len(worker['tasks'])
        workers.append(worker)
    partial = unavailable or any(worker[state] is None for worker in workers for state in ('active', 'reserved', 'scheduled'))
    return {
        'status': ('partial' if partial else 'ok') if workers else 'unavailable',
        'items': workers,
        'detail': (
            'Only workers responding to inspection are shown; missing replies do not establish that a worker is offline. '
            f'Counts are separate observations, with at most {MAX_WORKERS} workers and {MAX_TASKS_PER_WORKER} task details per worker. '
            'This is not completed-job or failure history.'
        ),
    }


def _durable_work(now):
    result = {}
    for key, model, pending_status, deadline in (
        ('scheduled_triggers', ScheduledTriggerRun, 'active', 'next_run_ts'),
        ('prepared_actions', PreparedGameAction, 'pending', 'run_at'),
    ):
        pending = model.objects.filter(status=pending_status)
        due = live_worlds(pending, world_field='runtime_world_id').filter(**{f'{deadline}__lte': now})
        # Recent failures are indexed by status/modified_ts. Cancelled work is
        # terminal and is deliberately never reset/replayed by the recovery UI.
        failures = model.objects.filter(
            status='cancelled', modified_ts__gte=now - timedelta(hours=24),
        ).exclude(failure_code='')
        result[key] = {
            'pending': pending.count(),
            'due': due.count(),
            'failed': failures.count(),
            'recent_failures': [
                {
                    'id': row['id'],
                    'world_id': row['runtime_world_id'],
                    'failure_code': row['failure_code'],
                    'completed_at': row['completed_ts'],
                }
                for row in failures.order_by('-modified_ts', '-id').values(
                    'id', 'runtime_world_id', 'failure_code', 'completed_ts',
                )[:10]
            ],
        }
    ready = Q(sequence=0, available_ts__lte=now, depends_on_batch_id__isnull=True)
    ready &= Q(claimed_until__isnull=True) | Q(claimed_until__lte=now)
    result['event_outbox'] = GameEventOutbox.objects.aggregate(
        pending=Count('pk'),
        due=Count('pk', filter=ready),
        retrying=Count('pk', filter=Q(attempt_count__gt=0)),
        oldest_due_at=Min('available_ts', filter=ready),
    )
    result['detail'] = (
        'Failures are cancelled work with a failure code in the last 24 hours; normal cancellations may be included. '
        'Due trigger/action counts exclude paused instances. '
        'Outbox pending counts event rows; due counts eligible batch heads. '
        'Recovery processes still-pending work only; terminal work is never replayed.'
    )
    return result


def _periodic_jobs():
    manual_ids = {value['task']: key for key, value in MANUAL_ACTIONS.items()}
    return [
        {
            'id': name,
            'task': entry['task'],
            'schedule': (f"Every {entry['schedule']:g} seconds" if isinstance(entry['schedule'], (int, float)) else str(entry['schedule'])),
            'manual_action': manual_ids.get(entry['task']),
        }
        for name, entry in sorted((app.conf.beat_schedule or {}).items())
    ]


def _build_snapshot():
    now = timezone.now()
    return {
        'generated_at': now.isoformat(),
        'cache_seconds': CACHE_SECONDS,
        'broker': _probe_broker(),
        'workers': _probe_workers(),
        'periodic': _periodic_jobs(),
        'durable': _durable_work(now),
        'actions': [{'id': name, 'label': value['label'], 'description': value['description']} for name, value in MANUAL_ACTIONS.items()],
    }


def _enqueue_action(action, task_id):
    with _broker_connection(write=True) as connection:
        connection.ensure_connection(max_retries=0)
        app.send_task(
            MANUAL_ACTIONS[action]['task'],
            kwargs={'limit': 100},
            task_id=task_id,
            expires=60,
            ignore_result=True,
            retry=False,
            connection=connection,
        )


class JobActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=tuple(MANUAL_ACTIONS))
    confirmed = serializers.BooleanField()

    def validate(self, attrs):
        if self.initial_data.get('confirmed') is not True:
            raise serializers.ValidationError({'confirmed': 'Confirm the selected recovery action.'})
        if set(self.initial_data) - {'action', 'confirmed'}:
            raise serializers.ValidationError('Only action and confirmed fields are accepted.')
        return attrs


class StaffJobsView(APIView):
    permission_classes = (IsAuthenticated, IsStaffUser)

    def get(self, request):
        snapshot = cache.get(SNAPSHOT_KEY)
        if snapshot is None:
            if not cache.add(SNAPSHOT_LOCK_KEY, True, timeout=45):
                return Response({'detail': 'A jobs snapshot is already being collected. Retry shortly.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            try:
                snapshot = _build_snapshot()
                cache.set(SNAPSHOT_KEY, snapshot, timeout=CACHE_SECONDS)
            finally:
                cache.delete(SNAPSHOT_LOCK_KEY)
        return Response(snapshot)

    def post(self, request):
        serializer = JobActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']
        # A small shared cooldown prevents double-clicks and concurrent staff
        # from turning a repair action into unbounded queue fan-out.
        if not cache.add(f'staff:jobs:action:{action}', True, timeout=30):
            return Response({'detail': 'This recovery action was recently requested. Wait 30 seconds before retrying.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        from system.control_access import record_staff_action
        task_id = str(uuid4())
        audit = record_staff_action(
            request.user, 'job.requested', target=action,
            details={'task_id': task_id, 'limit': 100},
        )
        try:
            _enqueue_action(action, task_id)
        except Exception:
            # Publication may have reached the broker before the connection
            # failed. Preserve the task ID and report uncertainty explicitly.
            audit.action = 'job.enqueue_unconfirmed'
            audit.save(update_fields=['action'])
            return Response({'detail': 'Broker delivery could not be confirmed. Check the jobs snapshot before retrying.', 'task_id': task_id}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        audit.action = 'job.enqueued'
        audit.save(update_fields=['action'])
        cache.delete(SNAPSHOT_KEY)
        return Response({'action': action, 'task_id': task_id}, status=status.HTTP_202_ACCEPTED)
