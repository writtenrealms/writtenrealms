"""Atomic combat pause boundaries and preservation of ordinary timer deadlines."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.db import transaction
from django.db.models import DateTimeField, ExpressionWrapper, F, Value
from django.utils import timezone

from spawns.instance_clock import in_simulation, time_control_run


_deferred_pause_world = ContextVar('deferred_combat_pause_world', default=None)


@contextmanager
def defer_combat_pause(run):
    """Finish room admission under the run lock before publishing its pause."""
    token = _deferred_pause_world.set(run.spawned_world_id)
    try:
        yield
    finally:
        _deferred_pause_world.reset(token)
    # Exceptions leave the enclosing clock-guard transaction to roll back;
    # only a completed admission boundary may expose the paused encounter.
    synchronize_combat_pause(run)


def owner_in_combat(run):
    from spawns.models import CombatParticipant
    return bool(run.owner_id and CombatParticipant.objects.filter(
        player_id=run.owner_id, player__world_id=run.spawned_world_id,
        is_active=True, encounter__world_id=run.spawned_world_id,
        encounter__status='active',
    ).exists())


def _shift(queryset, fields, delta):
    queryset.update(**{
        field: ExpressionWrapper(F(field) + Value(delta), output_field=DateTimeField())
        for field in fields
    })


def _rebase_timers(run, delta):
    from builders.models import SpawnPlanRun
    from quests.models import QuestOfferState
    from spawns.models import (
        ActiveEffect, CombatEncounter, CombatRoomState, InstanceClockWork,
        MerchantRuntime, PreparedGameAction, ScheduledTriggerRun,
    )
    from worlds.models import World, ZoneDoorResetSchedule

    world = run.spawned_world_id
    _shift(ActiveEffect.objects.filter(world_id=world), ['next_tick_ts', 'last_tick_ts'], delta)
    _shift(ScheduledTriggerRun.objects.filter(runtime_world_id=world, status='active'),
           ['next_run_ts', 'started_ts'], delta)
    _shift(PreparedGameAction.objects.filter(runtime_world_id=world, status='pending'), ['run_at'], delta)
    _shift(InstanceClockWork.objects.filter(world_id=world, due_at__isnull=False), ['due_at'], delta)
    _shift(MerchantRuntime.objects.filter(world_id=world), ['next_restock_ts', 'last_restocked_ts'], delta)
    _shift(ZoneDoorResetSchedule.objects.filter(world_id=world), ['next_reset_ts'], delta)
    _shift(SpawnPlanRun.objects.filter(spawn_world_id=world), ['last_reconciled_at'], delta)
    _shift(World.objects.filter(pk=world), ['last_spawn_plan_run_ts'], delta)
    _shift(CombatRoomState.objects.filter(world_id=world), ['next_run_ts', 'active_until'], delta)
    _shift(CombatEncounter.objects.filter(world_id=world, status__in=['active', 'paused']),
           ['next_resolution_ts', 'last_resolution_ts', 'npc_active_until'], delta)
    _shift(QuestOfferState.objects.filter(player_id=run.owner_id, player__world_id=world),
           ['last_resolved_at', 'cooldown_until', 'snoozed_until'], delta)

    # Merchant ordinal snapshots are presentation tokens, not authoritative
    # state. Apply their clock change after commit; a failed turn changes none.
    runtime_ids = list(MerchantRuntime.objects.filter(world_id=world).values_list('pk', flat=True))
    if runtime_ids:
        def shift_selections():
            from django.core.cache import cache
            from spawns.merchants import _controlled_merchant_selection_cache_key
            keys = [_controlled_merchant_selection_cache_key(pk, view)
                    for pk in runtime_ids for view in ('stock', 'offer')]
            rows = cache.get_many(keys)
            for value in rows.values():
                if isinstance(value, dict) and value.get('expires_at'):
                    value['expires_at'] += delta
            cache.set_many(rows, timeout=None)
        transaction.on_commit(shift_selections, robust=True)


def _fence_encounters(run, delta=None):
    from spawns.combat_encounters import current_context
    from spawns.models import CombatEncounter
    CombatEncounter.objects.filter(world_id=run.spawned_world_id, status__in=['active', 'paused']).update(
        schedule_generation=F('schedule_generation') + 1,
    )
    context = current_context()
    if context:
        for encounter in context.encounters.values():
            if encounter.world_id != run.spawned_world_id or encounter.status not in ('active', 'paused'):
                continue
            encounter.schedule_generation += 1
            if delta is not None:
                for field in ('next_resolution_ts', 'last_resolution_ts', 'npc_active_until'):
                    value = getattr(encounter, field)
                    if value is not None:
                        setattr(encounter, field, value + delta)


def resume_clock(run):
    """Caller holds the run lock. Resume real time without replaying thinking time."""
    if not run.time_paused:
        return
    now = timezone.now()
    delta = now - run.simulation_time
    _rebase_timers(run, delta)
    _fence_encounters(run, delta)
    run.time_paused = False
    run.simulation_time = now
    run.clock_offset_seconds += delta.total_seconds()
    run.time_generation += 1
    run.save(update_fields=['time_paused', 'simulation_time', 'clock_offset_seconds', 'time_generation'])
    run_id, generation = run.pk, run.time_generation
    def resume_jobs():
        from spawns.tasks import resume_instance_schedulers
        resume_instance_schedulers.delay(run_id=run_id, expected_generation=generation)
    transaction.on_commit(resume_jobs, robust=True)


def synchronize_combat_pause(run, *, force=False):
    """Run under the exclusive instance lock, including before an opening round."""
    if not run or not run.time_control or (in_simulation(run.spawned_world_id) and not force):
        return False
    if _deferred_pause_world.get() == run.spawned_world_id:
        return False
    should_pause = run.pause_in_combat and run.status in run.ACTIVE_STATUSES and owner_in_combat(run)
    if should_pause == run.time_paused:
        return False
    if should_pause:
        run.time_paused = True
        run.simulation_time = timezone.now()
        run.time_generation += 1
        run.save(update_fields=['time_paused', 'simulation_time', 'time_generation'])
        _fence_encounters(run)
    else:
        resume_clock(run)
    from spawns.instance_time import state_event
    from spawns.events import enqueue_game_events
    enqueue_game_events([state_event(run)])
    return True
