"""Initial-population clear goals and durable, server-timed completion records.

Combat keeps its shared instance lifecycle lock: it only marks one cohort row
and writes to the existing outbox. Progress/completion takes the exclusive run
lock after combat commits, avoiding lock upgrades between simultaneous kills.
"""
from copy import deepcopy
from datetime import datetime
import re
import uuid

from django.db import transaction
from django.utils import timezone

from core.condition_dsl import ConditionContext, evaluate_condition, validate_condition_payload


MAX_COHORT_MOBS = 10000
DEFEAT_EVENT = 'private.instance.mob_defeated'
MOB_PATHS = {'event.mob.core_faction', 'event.mob.definition_slug'}


def normalize_instance_goal(value):
    if value == {}:
        return {}
    if not isinstance(value, dict) or set(value) != {'type', 'where'}:
        raise ValueError('instance_goal must be {} or a mapping with type and where.')
    if value['type'] != 'clear_initial_mobs':
        raise ValueError('instance_goal.type must be clear_initial_mobs.')
    condition = value['where']
    validate_condition_payload(condition, field_name='instance_goal.where')

    def validate(node):
        # A deliberately small context for the shared condition DSL. No ORM
        # traversal or per-mob queries are permitted during cohort selection.
        if isinstance(node, bool):
            return
        if not isinstance(node, dict) or len(node) != 1:
            raise ValueError('instance_goal.where requires a single DSL operator per mapping.')
        operator, operands = next(iter(node.items()))
        if operator in {'all', 'any'}:
            if not isinstance(operands, list) or not operands:
                raise ValueError(f'instance_goal.where.{operator} requires conditions.')
            for child in operands:
                validate(child)
        elif operator == 'not':
            validate(operands)
        elif operator == 'always' and isinstance(operands, bool):
            return
        elif operator in {'eq', 'ne', 'in'}:
            if not isinstance(operands, list) or len(operands) != 2:
                raise ValueError(f'instance_goal.where.{operator} requires two operands.')
            left, right = operands
            if not isinstance(left, str) or (
                left not in MOB_PATHS
                and not re.fullmatch(r'event\.mob\.factions\.[a-z0-9_-]+', left)
            ):
                raise ValueError('Instance goal conditions must select a supported event.mob path.')
            if operator == 'in' and not isinstance(right, list):
                raise ValueError('instance_goal.where.in requires a literal list on the right.')
            literals = right if isinstance(right, list) else [right]
            for literal in literals:
                if not isinstance(literal, (str, bool, int, float)):
                    raise ValueError('Instance goal operands must be scalar values or lists.')
                if isinstance(literal, str) and ('{' in literal or '}' in literal):
                    raise ValueError('Instance goal comparisons require literal right-hand values.')
        else:
            raise ValueError(f'Unsupported instance goal condition operator: {operator}.')

    validate(condition)
    return deepcopy(value)


def start_instance_goal(run, *, ranking_eligible=True):
    """Caller holds the run lock; population and first admission are ready."""
    from spawns.models import Mob
    from worlds.models import InstanceGoalMember

    if not run.goal_spec or run.progress.get('attempt_id'):
        return
    goal = normalize_instance_goal(run.goal_spec)
    mobs = list(Mob.objects.filter(
        world_id=run.spawned_world_id, is_pending_deletion=False, health__gt=0,
    ).only('id', 'definition_slug_snapshot').order_by('pk')[:MAX_COHORT_MOBS + 1])
    if len(mobs) > MAX_COHORT_MOBS:
        raise ValueError(f'Instance goals support at most {MAX_COHORT_MOBS} initial mobs.')
    # Runtime faction assignments are snapshots copied at spawn time. Fetch
    # the entire bounded population's factions once, not once per creature.
    from django.db.models import prefetch_related_objects
    prefetch_related_objects(mobs, 'faction_assignments__faction')
    from core.factions import faction_is_core
    members = []
    for mob in mobs:
        factions = [assignment.faction for assignment in mob.faction_assignments.all()]
        data = {'mob': {
            'definition_slug': mob.definition_slug_snapshot,
            'core_faction': next((f.code for f in factions if faction_is_core(f)), None),
            'factions': {f.code: True for f in factions},
        }}
        if evaluate_condition(goal['where'], context=ConditionContext(event_data=data)):
            members.append(InstanceGoalMember(run=run, mob_runtime_id=mob.pk))
    if not members:
        raise ValueError('The instance goal matches no living initial mobs; check its faction and spawn plans.')
    InstanceGoalMember.objects.bulk_create(members, batch_size=500)
    run.started_at = timezone.now()
    run.progress = {
        'attempt_id': str(uuid.uuid4()),
        'total': len(members),
        'remaining': len(members),
        'ranking_eligible': ranking_eligible and not run.participants.filter(player__is_builder=True).exists(),
    }
    run.save(update_fields=['started_at', 'progress'])


def record_instance_mob_defeat(mob):
    """Call inside the death transaction, before deleting the runtime mob.

    Administrative removal/reset is intentionally not a death. The conditional
    update also deduplicates repeated reward/death processing.
    """
    from spawns.combat_encounters import current_context
    from spawns.events import GameEvent, PRIVATE_CONTROL_EVENT_KEY, enqueue_game_events
    from worlds.models import InstanceGoalMember

    context = current_context()
    if context is not None and not any(
        run.spawned_world_id == mob.world_id and run.goal_spec
        for run in context.runs.values()
    ):
        return
    changed = InstanceGoalMember.objects.filter(
        mob_runtime_id=mob.pk, run__spawned_world_id=mob.world_id,
        defeated_at__isnull=True,
    ).update(defeated_at=timezone.now())
    if changed:
        enqueue_game_events([GameEvent(DEFEAT_EVENT, {
            'mob_id': mob.pk, 'world_id': mob.world_id,
            PRIVATE_CONTROL_EVENT_KEY: True,
        })])


@transaction.atomic
def process_instance_mob_defeat(data):
    """Idempotent outbox subscriber; event delivery time never sets clear time."""
    from spawns.events import GameEvent, enqueue_game_events
    from worlds.models import InstanceClearRecord, InstanceGoalMember, InstanceRun

    member = InstanceGoalMember.objects.filter(
        mob_runtime_id=data.get('mob_id'), run__spawned_world_id=data.get('world_id'),
        defeated_at__isnull=False, processed=False,
    ).first()
    if member is None:
        return
    run = InstanceRun.objects.select_for_update().filter(pk=member.run_id).first()
    if run is None:  # Runtime cleanup may have removed it after discovery.
        return
    # Reset deletes old cohort identities; stale queued events cannot count
    # toward the new attempt, even when it reuses the same runtime world.
    if run.status not in run.ACTIVE_STATUSES or not run.started_at or not run.progress.get('attempt_id'):
        return
    if not InstanceGoalMember.objects.filter(pk=member.pk, processed=False).update(processed=True):
        return
    progress = dict(run.progress)
    progress['remaining'] -= 1
    latest = datetime.fromisoformat(progress['last_defeated_at']) if progress.get('last_defeated_at') else run.started_at
    completed_at = max(latest, member.defeated_at)
    progress['last_defeated_at'] = completed_at.isoformat()
    run.progress = progress
    if progress['remaining']:
        run.save(update_fields=['progress'])
        return

    participant_rows = list(run.participants.select_related('player').order_by('player_id'))
    participants = [{
        'player_id': p.player_id, 'name': p.player.name, 'role': p.role,
        'is_builder': p.player.is_builder,
        'joined_at': p.joined_at.isoformat(),
        'exited_at': p.exited_at.isoformat() if p.exited_at else None,
    } for p in participant_rows]
    ranking_eligible = bool(run.progress.get('ranking_eligible')) and bool(participant_rows) and all(
        not p.player.is_builder
        for p in participant_rows
    )
    delta = completed_at - run.started_at
    clear_time_ms = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    record = InstanceClearRecord.objects.create(
        run=run, template_world_id=run.template_world_id,
        template_name=run.template_world.name, template_slug=run.template_world.instance_slug,
        attempt_id=progress['attempt_id'], started_at=run.started_at,
        completed_at=completed_at, clear_time_ms=clear_time_ms,
        time_control=run.time_control, goal_spec=deepcopy(run.goal_spec), participants=participants,
        single_player=run.single_player,
        ranking_eligible=ranking_eligible,
        ranking_player_id=participant_rows[0].player_id if run.single_player and len(participant_rows) == 1 else None,
    )
    run.completed_at = completed_at
    run.status = run.STATUS_COMPLETED
    run.outcome = {
        'resolution': 'completed', 'clear_record_id': record.pk,
        'started_at': run.started_at.isoformat(), 'completed_at': completed_at.isoformat(),
        'clear_time_ms': clear_time_ms, 'time_basis': 'wall', 'time_control': run.time_control,
        'participants': participants,
    }
    run.save(update_fields=['progress', 'completed_at', 'status', 'outcome'])
    # A final kill must not strand a time-controlled player in a paused world.
    if run.time_control:
        from spawns.instance_clock_transitions import synchronize_combat_pause
        synchronize_combat_pause(run)
    recipients = [f'player.{p.player_id}' for p in run.participants.filter(exited_at__isnull=True)]
    enqueue_game_events([GameEvent('instance.completed', {
        'run_id': run.pk, 'world_id': run.spawned_world_id, **run.outcome,
    }, recipients, f'{record.template_name} completed in {clear_time_ms / 1000:.3f} seconds.')])
