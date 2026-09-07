"""Finish due gameplay timers and their immediate consequences before pausing."""

from spawns.actions.base import ActionError
from spawns.actions.doors import process_due_prepared_door_actions
from spawns.models import PreparedGameAction, ScheduledTriggerRun
from spawns.trigger_steps import process_due_trigger_runs


MAX_DUE_WORK_PER_ADVANCE = 2048
DUE_WORK_BATCH_SIZE = 100


def drain_due_schedulers(run, drain_reactions):
    """Drain a fixed point, sharing one timer budget across repeated phases.

    The run lock and simulation scope must be held by the caller. Every due
    step is processed before the final snapshot, including zero-delay steps
    scheduled by a reaction to an earlier step. Exhaustion raises inside the
    caller's transaction, so the entire advance rolls back instead of pausing
    with overdue work left behind.
    """
    from spawns.instance_clock import current_simulation, in_simulation

    if not in_simulation(run.spawned_world_id):
        raise ActionError("Instance timers require an active advance.", code="instance_clock_required")
    simulation = current_simulation()
    budget_key = "due_scheduler_work_processed"
    count = simulation.cache.get(budget_key, 0)
    processed_this_call = 0
    drain_reactions()
    while True:
        trigger_due = ScheduledTriggerRun.objects.filter(
            runtime_world_id=run.spawned_world_id,
            status=ScheduledTriggerRun.STATUS_ACTIVE,
            next_run_ts__lte=run.simulation_time,
        ).exists()
        door_due = PreparedGameAction.objects.filter(
            runtime_world_id=run.spawned_world_id,
            status=PreparedGameAction.STATUS_PENDING,
            run_at__lte=run.simulation_time,
        ).exists()
        if not trigger_due and not door_due:
            return {"processed": processed_this_call}
        remaining = MAX_DUE_WORK_PER_ADVANCE - count
        if remaining <= 0:
            raise ActionError(
                "This instance produced too much timed work in one turn.",
                code="instance_turn_budget",
            )
        processed = 0
        if trigger_due:
            result = process_due_trigger_runs(
                limit=min(DUE_WORK_BATCH_SIZE, remaining),
                now=run.simulation_time, world_id=run.spawned_world_id,
            )
            processed += result["processed"]
            remaining -= result["processed"]
        if door_due and remaining:
            result = process_due_prepared_door_actions(
                limit=min(DUE_WORK_BATCH_SIZE, remaining),
                now=run.simulation_time, world_id=run.spawned_world_id,
            )
            processed += result["processed"]
        if not processed:
            raise ActionError(
                "The instance could not finish its pending timers. Try advancing again.",
                code="instance_timers_busy",
            )
        count += processed
        processed_this_call += processed
        simulation.cache[budget_key] = count
        drain_reactions()
