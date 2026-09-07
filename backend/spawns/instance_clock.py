"""Gameplay clocks and execution boundaries for owner-controlled instance runs.

Wall time wakes automatic runs; it never advances their gameplay directly.
Context is local to one transaction, not a process-wide/world-wide clock patch.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps
import uuid

from django.db.models import Subquery
from django.db import transaction
from django.utils import timezone


SIMULATION_STEP_SECONDS = 2.0
MAX_STEP_EVENTS = 2048
MAX_PENDING_REACTIONS = 256
_simulation = ContextVar('instance_simulation', default=None)
_guard = ContextVar('instance_clock_guard', default=None)
_world_scope = ContextVar('instance_world_scope', default=None)
_exclude_capable = ContextVar('exclude_time_capable_worlds', default=False)
REACTIONS_DONE = '_instance_reactions_done'


@dataclass
class Simulation:
    run: object
    events: deque = field(default_factory=deque)
    output: list = field(default_factory=list)
    persisting: bool = False
    cache: dict = field(default_factory=dict)
    round_actor_keys: set = field(default_factory=set)
    accept_deferred_work: bool = True


def world_id(world):
    return getattr(world, 'pk', world)


def current_simulation():
    return _simulation.get()


def in_simulation(world_or_id=None):
    state = _simulation.get()
    return state is not None and (
        world_or_id is None or state.run.spawned_world_id == world_id(world_or_id)
    )


def controlled_world_ids():
    from worlds.models import InstanceRun
    return InstanceRun.objects.filter(time_control=True, time_paused=True).values('spawned_world_id')


def time_control_run(world_or_id):
    key = world_id(world_or_id)
    if not key:
        return None
    state = _simulation.get()
    if state is not None:
        if key == state.run.spawned_world_id:
            return state.run
        if key in state.cache:
            return state.cache[key]
    guarded = _guard.get()
    if guarded is not None and guarded.spawned_world_id == key:
        return guarded
    from spawns.combat_encounters import current_context
    combat = current_context()
    if combat is not None:
        run = next((run for run in combat.runs.values() if run.spawned_world_id == key), None)
        if run is not None:
            return run if run.time_control and run.single_player else None
    # Ordinary authored/base worlds cannot be controlled; avoid a query there.
    if hasattr(world_or_id, 'context_id') and not world_or_id.context_id:
        return None
    from worlds.models import InstanceRun
    run = InstanceRun.objects.filter(spawned_world_id=key, time_control=True).first()
    if state is not None:
        state.cache[key] = run
    return run


def controlled_run(world_or_id):
    """The active pause authority; builder permission alone does not pause time."""
    run = time_control_run(world_or_id)
    return run if run and run.time_paused else None


@contextmanager
def guarded_run_scope(run):
    token = _guard.set(run)
    try:
        yield run
    finally:
        _guard.reset(token)


@contextmanager
def clock_guard(world_or_id):
    """Serialize eligible gameplay with pause/resume before locking its rows."""
    run = time_control_run(world_or_id)
    if run is None or in_simulation(world_or_id) or _guard.get() is run:
        yield run
        return
    from worlds.models import InstanceRun
    with transaction.atomic():
        run = InstanceRun.objects.select_for_update().get(pk=run.pk)
        token = _guard.set(run)
        try:
            yield run
        finally:
            _guard.reset(token)


def scoped_world_id():
    return _world_scope.get()


def serialized_world(resolve_world):
    """Keep a runtime job's existing rules inside its instance pause boundary."""
    def decorate(function):
        if isinstance(resolve_world, str):
            from inspect import signature
            parameters = signature(function)
        @wraps(function)
        def guarded(*args, **kwargs):
            world = (parameters.bind(*args, **kwargs).arguments.get(resolve_world)
                     if isinstance(resolve_world, str) else resolve_world(*args, **kwargs))
            with clock_guard(world):
                return function(*args, **kwargs)
        return guarded
    return decorate


@contextmanager
def world_scope(world_or_id):
    token = _world_scope.set(world_id(world_or_id))
    try:
        yield
    finally:
        _world_scope.reset(token)


@contextmanager
def exclude_time_capable_worlds():
    token = _exclude_capable.set(True)
    try:
        yield
    finally:
        _exclude_capable.reset(token)


def is_time_controlled(world_or_id):
    return controlled_run(world_or_id) is not None


def gameplay_now(world_or_id=None):
    state = _simulation.get()
    if state is not None and (world_or_id is None or in_simulation(world_or_id)):
        return state.run.simulation_time
    run = controlled_run(world_or_id) if world_or_id is not None else None
    return run.simulation_time if run and run.simulation_time else timezone.now()


def live_worlds(queryset, world_field='world_id'):
    """SQL exclusion prevents idle controlled actors filling background batches."""
    state = _simulation.get()
    if state is not None:
        return queryset.filter(**{world_field: state.run.spawned_world_id})
    if _world_scope.get() is not None:
        queryset = queryset.filter(**{world_field: _world_scope.get()})
    if _exclude_capable.get() and _world_scope.get() is None:
        from worlds.models import InstanceRun
        return queryset.exclude(**{f'{world_field}__in': Subquery(
            InstanceRun.objects.filter(time_control=True).values('spawned_world_id'))})
    return queryset.exclude(**{f'{world_field}__in': Subquery(controlled_world_ids())})


@contextmanager
def simulation_scope(run):
    state = Simulation(run)
    token = _simulation.set(state)
    try:
        yield state
    finally:
        _simulation.reset(token)


def capture_events(events):
    state = _simulation.get()
    if state is None or state.persisting:
        return False
    state.events.extend(events)
    if len(state.events) + len(state.output) > MAX_STEP_EVENTS:
        from spawns.actions.base import ActionError
        raise ActionError('This instance exceeded the event budget for one turn.', code='instance_turn_budget')
    return True


def capture_message(actor_key, message, connection_id=None):
    from spawns.events import GameEvent
    return capture_events([GameEvent(
        type=message['type'], data=message.get('data') or {},
        recipients=[actor_key], text=message.get('text'), group=message.get('group'),
        connection_id=connection_id,
    )])


def event_world_id(data, actor_key=None):
    for key in ('runtime_world_id', '_transfer_runtime_world_id', 'world_id'):
        value = data.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    actor = data.get('actor') or {}
    ref = actor.get('key') if isinstance(actor, dict) else None
    ref = ref or actor_key
    if ref and str(ref).startswith(('player.', 'mob.')):
        from spawns.models import Player, Mob
        kind, _, pk = str(ref).partition('.')
        if pk.isdigit():
            return (Player if kind == 'player' else Mob).objects.filter(
                pk=int(pk)).values_list('world_id', flat=True).first()
    return None


def defer_work(world_or_id, kind, payload, *, key=None, due_at=None, allow_live=False):
    """Persist a reaction that may only execute during its run's next advance."""
    run = time_control_run(world_or_id) if allow_live else controlled_run(world_or_id)
    if run is None:
        return False
    from spawns.models import InstanceClockWork
    from worlds.models import InstanceRun
    with transaction.atomic():
        # Serialize new external work with the run's advance boundary.
        InstanceRun.objects.select_for_update().get(pk=run.pk)
        if key and InstanceClockWork.objects.filter(dedupe_key=key).exists():
            return True
        pending = InstanceClockWork.objects.filter(world_id=run.spawned_world_id).exclude(
            kind__in=['advance_receipt', 'trigger_gate'],
        )
        if not in_simulation(run.spawned_world_id) and len(list(pending.values_list('pk', flat=True)[:MAX_PENDING_REACTIONS])) >= MAX_PENDING_REACTIONS:
            from spawns.actions.base import ActionError
            raise ActionError('Advance the instance before requesting more interactions.', code='instance_input_budget')
        InstanceClockWork.objects.get_or_create(
            dedupe_key=key or str(uuid.uuid4()),
            defaults={'world_id': run.spawned_world_id, 'kind': kind, 'payload': payload, 'due_at': due_at},
        )
        if allow_live and not run.time_paused:
            from spawns.instance_time import schedule_deferred_work
            schedule_deferred_work(run, due_at)
    return True


def persist_event_reactions(event, data):
    """Make entry and other reactions durable before exposing their event.

    Delivery latency must never decide whether a player's first advance sees
    a room-entry script. Called in the same transaction as the source event.
    """
    if data.get(REACTIONS_DONE) or in_simulation():
        return
    from spawns.trigger_subscriptions import _EVENT_SUBSCRIPTIONS as triggers
    from quests.subscriptions import _EVENT_SUBSCRIPTIONS as quests
    from spawns.events import (
        PRIVATE_CONTROL_EVENT_KEY, SCRIPT_COMMAND_PROVENANCE_KEY,
        PLAYER_ROOM_ENTER_EVENT_TYPE, TRANSFER_ENTER_EVENT_TYPE,
        PLAYER_ROOM_ENTER_EMITTED_KEY,
    )
    kind = event.type.lower()
    if kind not in triggers and kind not in quests:
        return
    if data.get(PRIVATE_CONTROL_EVENT_KEY):
        return
    actor = event.recipients[0] if len(event.recipients) == 1 else None
    runtime = event_world_id(data, actor)
    if not is_time_controlled(runtime):
        return
    scripted = isinstance(data.get(SCRIPT_COMMAND_PROVENANCE_KEY), dict)
    payload = {'event_type': event.type, 'event_data': dict(data),
               'actor_key': actor, 'connection_id': event.connection_id}
    if kind in triggers and not (kind == TRANSFER_ENTER_EVENT_TYPE and data.get(PLAYER_ROOM_ENTER_EMITTED_KEY)) and (
        not scripted or kind in {TRANSFER_ENTER_EVENT_TYPE, PLAYER_ROOM_ENTER_EVENT_TYPE}
    ):
        defer_work(runtime, 'trigger', payload, key=_subscription_key(runtime, 'trigger', event.type, data, actor, data['_event_id']))
    if kind in quests and kind != PLAYER_ROOM_ENTER_EVENT_TYPE and (not scripted or kind == 'affect.transfer'):
        defer_work(runtime, 'quest', payload, key=_subscription_key(runtime, 'quest', event.type, data, actor, data['_event_id']))
    data[REACTIONS_DONE] = True


def defer_subscription(kind, event_type, data, actor_key, connection_id):
    if data.get(REACTIONS_DONE):
        return True
    if in_simulation():
        return False
    runtime = event_world_id(data, actor_key)
    event_id = data.get('_event_id')
    return defer_work(runtime, kind, {
        'event_type': event_type, 'event_data': data, 'actor_key': actor_key,
        'connection_id': connection_id,
    }, key=_subscription_key(runtime, kind, event_type, data, actor_key, event_id))


def _subscription_key(runtime, kind, event_type, data, actor_key, event_id):
    if event_type in {'cmd.look.success', 'cmd.inspect.success', 'cmd.scan.success'}:
        # Observation can be repeated freely without accumulating equivalent
        # quest/entry checks while no gameplay has changed.
        import hashlib
        import json
        identity = {k: v for k, v in data.items() if not k.startswith('_') and k not in {'request_id', 'request_segment'}}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:32]
        return f'observe:{runtime}:{kind}:{actor_key}:{event_type}:{digest}'
    return f'{kind}:{event_id}' if event_id else None


def rebase_character_timers(player, origin_world_id, destination_world_id):
    """Carry remaining durations across clocks, never accumulated thinking time."""
    from spawns.models import ActiveEffect
    if not is_time_controlled(origin_world_id) and not is_time_controlled(destination_world_id):
        return
    before = gameplay_now(origin_world_id)
    after = gameplay_now(destination_world_id)
    if before == after:
        return
    changed = []
    for effect in ActiveEffect.objects.filter(target_player=player):
        if effect.next_tick_ts:
            effect.next_tick_ts = after + max(effect.next_tick_ts - before, timedelta())
            changed.append(effect)
    ActiveEffect.objects.bulk_update(changed, ['next_tick_ts'])
    from django.db.models import DateTimeField, ExpressionWrapper, F, Value
    from django.db.models.functions import Greatest
    from quests.models import QuestOfferState

    offers = QuestOfferState.objects.filter(player=player)
    # Anchors retain elapsed gameplay time (including a completed cooldown).
    # Deadlines retain remaining time, with already-due deadlines due on entry.
    offers.filter(last_resolved_at__isnull=False).update(
        last_resolved_at=ExpressionWrapper(
            F('last_resolved_at') + Value(after - before),
            output_field=DateTimeField(),
        ),
    )
    for field_name in ('cooldown_until', 'snoozed_until'):
        offers.filter(**{f'{field_name}__isnull': False}).update(**{
            field_name: ExpressionWrapper(
                Greatest(F(field_name), Value(before)) + Value(after - before),
                output_field=DateTimeField(),
            ),
        })
