"""Durable admission and accounting. No network I/O in game transactions."""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta, timezone as datetime_timezone
from functools import partial

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone

from inference.configuration import (
    PURPOSES, Unavailable, maximum_cost, micro, usage_cost,
    validate_configuration, validate_payload,
)
from inference import notifications
from inference.models import BudgetAllocation, BudgetBucket, InferenceControl, InferenceRequest
from inference.providers import Outcome
from users.models import User
from worlds.models import World

ACTIVE = (InferenceRequest.Status.QUEUED, InferenceRequest.Status.DISPATCHING)


@dataclass(frozen=True)
class Admission:
    request_id: uuid.UUID | None = None
    reason: str = ''


def _control():
    try:
        return InferenceControl.objects.select_for_update().get(pk=1)
    except InferenceControl.DoesNotExist:
        raise Unavailable('disabled') from None


def _available(control, now):
    if not control.enabled:
        raise Unavailable('disabled')
    if not settings.LLM_API_KEY:
        raise Unavailable('missing_credentials')
    if control.circuit_until and control.circuit_until > now:
        raise Unavailable('circuit_open')
    return validate_configuration(control.configuration)


@transaction.atomic
def configure(configuration):
    configuration = validate_configuration(configuration)
    InferenceControl.objects.get_or_create(pk=1)
    control = _control()
    control.configuration = configuration
    control.enabled = False
    control.generation += 1
    control.circuit_until = None
    control.circuit_reason = ''
    control.save()


@transaction.atomic
def set_enabled(enabled):
    if not enabled:
        InferenceControl.objects.get_or_create(pk=1)
    control = _control()
    if enabled:
        if control.circuit_reason == 'reservation_exceeded':
            raise Unavailable('configuration_review_required')
        validate_configuration(control.configuration)
        if not settings.LLM_API_KEY:
            raise Unavailable('missing_credentials')
    control.enabled = enabled
    control.generation += 1
    control.save(update_fields=['enabled', 'generation'])


def _bucket_specs(limits, purpose, owner, world_id, now):
    day = now.astimezone(datetime_timezone.utc).date()
    month = day.replace(day=1)
    allocation = 'conversation' if purpose == 'mob_conversation' else 'preparation'
    return [
        ('installation:month', month, micro(limits['monthly_usd']), None),
        ('installation:day', day, micro(limits['daily_usd']), None),
        (f'{allocation}:month', month, micro(limits[f'{allocation}_monthly_usd']), None),
        (f'{allocation}:day', day, micro(limits[f'{allocation}_daily_usd']), None),
        (f'world:{world_id}:month', month, micro(limits['world_monthly_usd']), None),
        (f'world:{world_id}:day', day, micro(limits['world_daily_usd']), None),
        (f'owner:{owner}:day', day, None, limits['owner_daily_requests']),
    ]


def submit(*, request_id, user_id, world_id, purpose, instructions, messages, profile='fast'):
    """Internal API, after the consumer's gameplay gates. A denial creates no job.

    Admission writes PostgreSQL, then notifies the worker after commit. A
    bounded recovery scan finds work if that best-effort notification is lost.
    """
    try:
        return _submit(request_id=request_id, user_id=user_id, world_id=world_id,
                       purpose=purpose, instructions=instructions, messages=messages, profile=profile)
    except Unavailable as error:
        return Admission(reason=str(error))
    except DatabaseError:
        return Admission(reason='accounting_unavailable')


def capacity(*, user_id, world_id, purpose, profile='fast'):
    """Free-greeting preflight using an authored base-world ID; submit is authoritative."""
    try:
        control = InferenceControl.objects.get(pk=1)
        now = timezone.now()
        configuration = _available(control, now)
        if purpose not in PURPOSES or profile not in configuration['profiles']:
            raise Unavailable('unsupported_purpose_or_profile')
        user = User.objects.only('id', 'link_id').get(pk=user_id)
        owner = f'link:{user.link_id}' if user.link_id else f'user:{user.pk}'
        limits = configuration['limits']
        if InferenceRequest.objects.filter(owner=owner, status__in=ACTIVE).exists():
            raise Unavailable('owner_busy')
        if InferenceRequest.objects.filter(status__in=ACTIVE).count() >= limits['pending_requests']:
            raise Unavailable('queue_full')
        selected = configuration['profiles'][profile]
        cost = maximum_cost(selected)
        specs = _bucket_specs(limits, purpose, owner, world_id, now)
        query = Q()
        for scope, period, _, _ in specs:
            query |= Q(scope=scope, period=period)
        buckets = {(row.scope, row.period): row for row in BudgetBucket.objects.filter(query)}
        for scope, period, cap, count_cap in specs:
            bucket = buckets.get((scope, period))
            occupied = bucket.spent_micro + bucket.reserved_micro if bucket else 0
            if cap is not None and occupied + cost > cap:
                raise Unavailable('budget_exhausted')
            if count_cap is not None and bucket and bucket.requests >= count_cap:
                raise Unavailable('owner_quota')
        return {'generation': control.generation, 'profile': selected, 'owner': owner}
    except InferenceControl.DoesNotExist:
        raise Unavailable('disabled') from None
    except DatabaseError:
        raise Unavailable('accounting_unavailable') from None


@transaction.atomic
def _submit(*, request_id, user_id, world_id, purpose, instructions, messages, profile):
    if (type(user_id) is not int or user_id <= 0 or type(world_id) is not int or world_id <= 0
            or not isinstance(profile, str)):
        raise Unavailable('invalid_identity')
    now = timezone.now()
    control = _control()
    configuration = _available(control, now)
    if purpose not in PURPOSES or profile not in configuration['profiles']:
        raise Unavailable('unsupported_purpose_or_profile')
    selected = configuration['profiles'][profile]
    payload = validate_payload(instructions, messages, selected)
    try:
        request_id = uuid.UUID(str(request_id))
    except ValueError:
        raise Unavailable('invalid_identity') from None
    fingerprint = hashlib.sha256(json.dumps(
        [user_id, world_id, purpose, profile, payload], sort_keys=True).encode()).hexdigest()
    previous = InferenceRequest.objects.filter(pk=request_id).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Unavailable('identity_conflict')
        return Admission(request_id=previous.pk)
    try:
        user = User.objects.only('id', 'link_id').get(pk=user_id)
        world = World.objects.values('id', 'context_id', 'instance_of_id',
                                     'context__instance_of_id', 'context__context_id').get(pk=world_id)
    except (User.DoesNotExist, World.DoesNotExist):
        raise Unavailable('invalid_owner_or_world') from None
    owner = f'link:{user.link_id}' if user.link_id else f'user:{user.pk}'
    if world['context__context_id']:
        raise Unavailable('invalid_world_scope')
    authored_world_id = (world['context__instance_of_id'] or world['context_id'] or
                         world['instance_of_id'] or world['id'])
    limits = configuration['limits']
    if InferenceRequest.objects.filter(owner=owner, status__in=ACTIVE).exists():
        raise Unavailable('owner_busy')
    if InferenceRequest.objects.filter(status__in=ACTIVE).count() >= limits['pending_requests']:
        raise Unavailable('queue_full')
    reservation = maximum_cost(selected)
    specs = _bucket_specs(limits, purpose, owner, authored_world_id, now)
    # The singleton lock serializes every ledger writer, including new buckets.
    # This is seven fixed scopes, independent of world/player population.
    BudgetBucket.objects.bulk_create([
        BudgetBucket(scope=scope, period=period) for scope, period, _, _ in specs
    ], ignore_conflicts=True)
    query = Q()
    for scope, period, _, _ in specs:
        query |= Q(scope=scope, period=period)
    buckets = {(bucket.scope, bucket.period): bucket for bucket in BudgetBucket.objects.filter(query)}
    for scope, period, cap, count_cap in specs:
        bucket = buckets[(scope, period)]
        if cap is not None and bucket.spent_micro + bucket.reserved_micro + reservation > cap:
            raise Unavailable('budget_exhausted')
        if count_cap is not None and bucket.requests >= count_cap:
            raise Unavailable('owner_quota')
        bucket.reserved_micro += reservation
        bucket.requests += 1
    BudgetBucket.objects.bulk_update(buckets.values(), ['reserved_micro', 'requests'])
    request = InferenceRequest.objects.create(
        id=request_id, fingerprint=fingerprint, purpose=purpose, owner=owner, user_id=user.pk,
        world_id=authored_world_id, provider=configuration['provider'], profile=selected,
        generation=control.generation, payload=payload, reservation_micro=reservation,
        expires_at=now + timedelta(seconds=90), enqueue_after=now)
    BudgetAllocation.objects.bulk_create([
        BudgetAllocation(request=request, bucket=bucket) for bucket in buckets.values()
    ])
    transaction.on_commit(partial(notifications.request_ready, str(request.pk)), robust=True)
    return Admission(request_id=request.pk)


def _cancel(request, reason, now):
    ids = request.allocations.values_list('bucket_id', flat=True)
    BudgetBucket.objects.filter(pk__in=ids).update(
        reserved_micro=F('reserved_micro') - request.reservation_micro, requests=F('requests') - 1)
    request.status = InferenceRequest.Status.CANCELLED
    request.error_code = reason
    request.completed_at = now
    request.payload = {}
    request.save()


def _stale(request, control, now):
    # Do not start yesterday's queued reservation against today's provider spend.
    return (request.generation != control.generation or request.expires_at <= now or
            request.created_at.astimezone(datetime_timezone.utc).date() !=
            now.astimezone(datetime_timezone.utc).date())


@transaction.atomic
def cancel(request_id):
    """Withdraw consumer interest; only never-dispatched work can be refunded."""
    _control()
    request = InferenceRequest.objects.filter(pk=request_id).first()
    if request is None:
        return
    now = timezone.now()
    if request.status == InferenceRequest.Status.QUEUED:
        _cancel(request, 'consumer_cancelled', now)
    else:
        request.expires_at = min(request.expires_at, now)
        request.result = None
        request.save(update_fields=['expires_at', 'result'])


def claim(request_id):
    if not settings.LLM_WORKER or not settings.LLM_NETWORK_ENABLED:
        return None
    purpose = InferenceRequest.objects.filter(pk=request_id).values_list('purpose', flat=True).first()
    if purpose == 'mob_conversation':
        from spawns.conversations.service import dispatch_guard
        with dispatch_guard(request_id) as allowed:
            if not allowed:
                cancel(request_id)
                return None
            return _claim(request_id)
    return _claim(request_id)


@transaction.atomic
def _claim(request_id):
    if not settings.LLM_WORKER or not settings.LLM_NETWORK_ENABLED:
        return None
    now = timezone.now()
    control = _control()
    request = InferenceRequest.objects.filter(pk=request_id, status=InferenceRequest.Status.QUEUED).first()
    if request is None:
        return None
    try:
        configuration = _available(control, now)
        if _stale(request, control, now):
            raise Unavailable('stale_request')
    except Unavailable as error:
        _cancel(request, str(error), now)
        return None
    if InferenceRequest.objects.filter(status=InferenceRequest.Status.DISPATCHING).count() >= configuration['limits']['concurrent_requests']:
        return None
    request.status = InferenceRequest.Status.DISPATCHING
    request.dispatch_token = uuid.uuid4()
    request.lease_until = now + timedelta(seconds=60)
    request.save(update_fields=['status', 'dispatch_token', 'lease_until'])
    return request


def _settle(control, request, outcome, now):
    late = request.status == InferenceRequest.Status.UNCERTAIN
    cost = usage_cost(request.profile, outcome.usage) if outcome.usage is not None else request.reservation_micro
    ids = request.allocations.values_list('bucket_id', flat=True)
    if late:
        BudgetBucket.objects.filter(pk__in=ids).update(spent_micro=F('spent_micro') + cost - request.charged_micro)
    else:
        BudgetBucket.objects.filter(pk__in=ids).update(
            reserved_micro=F('reserved_micro') - request.reservation_micro, spent_micro=F('spent_micro') + cost)
    request.charged_micro = cost
    request.usage = outcome.usage
    request.error_code = outcome.error
    request.result = None
    request.status = InferenceRequest.Status.INVALID
    if outcome.usage is None:
        request.status = InferenceRequest.Status.UNCERTAIN
        control.circuit_until = now + timedelta(minutes=5)
        control.circuit_reason = outcome.error or 'unknown_usage'
        control.save(update_fields=['circuit_until', 'circuit_reason'])
    elif cost > request.reservation_micro:
        # Bad operator pricing/bounds must never hide an invoice discrepancy.
        control.enabled = False
        control.generation += 1
        control.circuit_reason = 'reservation_exceeded'
        control.save(update_fields=['enabled', 'generation', 'circuit_reason'])
        request.error_code = 'reservation_exceeded'
    elif late or not control.enabled or _stale(request, control, now):
        request.status = InferenceRequest.Status.DISCARDED
        request.error_code = 'late_result' if late else 'stale_result'
    elif not outcome.error and outcome.result is not None:
        request.status = InferenceRequest.Status.SUCCEEDED
        request.result = outcome.result
    if request.status in (InferenceRequest.Status.INVALID, InferenceRequest.Status.UNCERTAIN) and outcome.rejection:
        request.result = {'rejection': outcome.rejection}
    request.payload = {}
    request.completed_at = now
    request.save()
    if request.purpose == 'mob_conversation':
        transaction.on_commit(partial(notifications.result_ready, str(request.pk)), robust=True)


@transaction.atomic
def settle(request_id, dispatch_token, outcome):
    control = _control()
    request = InferenceRequest.objects.get(pk=request_id)
    if request.dispatch_token != dispatch_token or request.status not in (
            InferenceRequest.Status.DISPATCHING, InferenceRequest.Status.UNCERTAIN):
        return
    # Repeated unknown outcomes must not move money or prolong retention.
    if request.status == InferenceRequest.Status.UNCERTAIN and outcome.usage is None:
        return
    _settle(control, request, outcome, timezone.now())


@transaction.atomic
def result_for_with_reason(request_id):
    """Read a still-usable result; the consumer must also recheck game state.

    Publication and its idempotency receipt belong in the consumer transaction.
    Call this there so the control lock fences disablement until publication.
    """
    try:
        control = _control()
        now = timezone.now()
        _available(control, now)
    except Unavailable as error:
        return None, str(error)
    request = InferenceRequest.objects.filter(pk=request_id).first()
    if request is None:
        return None, 'request_missing'
    if request.status != InferenceRequest.Status.SUCCEEDED:
        return None, request.error_code or f'request_{request.status}'
    if request.generation != control.generation:
        return None, 'changed_configuration'
    if request.expires_at <= now:
        return None, 'reply_timed_out'
    if request.created_at.astimezone(datetime_timezone.utc).date() != now.astimezone(datetime_timezone.utc).date():
        return None, 'request_day_changed'
    if request.result is None:
        return None, 'response_missing'
    return request.result, None


def result_for(request_id):
    return result_for_with_reason(request_id)[0]


@transaction.atomic
def dispatch_batch():
    """Bounded outbox scan, also recovering worker crashes. Returns IDs only."""
    now = timezone.now()
    try:
        control = _control()
    except Unavailable:
        return []
    for request in InferenceRequest.objects.filter(
            status=InferenceRequest.Status.DISPATCHING, lease_until__lte=now).order_by('lease_until')[:32]:
        _settle(control, request, Outcome(error='worker_lost'), now)
    try:
        _available(control, now)
        available = True
    except Unavailable:
        available = False
    ids = []
    for request in InferenceRequest.objects.filter(
            status=InferenceRequest.Status.QUEUED, enqueue_after__lte=now).order_by('enqueue_after')[:32]:
        if not available or _stale(request, control, now):
            _cancel(request, 'unavailable_or_expired', now)
        else:
            request.enqueue_after = now + timedelta(seconds=15)
            request.save(update_fields=['enqueue_after'])
            ids.append(str(request.pk))
    return ids


@transaction.atomic
def prune():
    """Keep aggregate buckets; retain idempotency receipts for 90 days."""
    try:
        _control()
    except Unavailable:
        return
    now = timezone.now()
    results = InferenceRequest.objects.filter(completed_at__lt=now - timedelta(days=3), result__isnull=False)
    ids = list(results.order_by('completed_at').values_list('pk', flat=True)[:500])
    InferenceRequest.objects.filter(pk__in=ids).update(result=None)
    old = InferenceRequest.objects.filter(completed_at__lt=now - timedelta(days=90)).exclude(status__in=ACTIVE)
    ids = list(old.order_by('completed_at').values_list('pk', flat=True)[:500])
    InferenceRequest.objects.filter(pk__in=ids).delete()
    owner_buckets = BudgetBucket.objects.filter(
        period__lt=(now - timedelta(days=90)).date(), scope__startswith='owner:',
        budgetallocation__isnull=True)
    ids = list(owner_buckets.order_by('period').values_list('pk', flat=True)[:500])
    BudgetBucket.objects.filter(pk__in=ids).delete()
