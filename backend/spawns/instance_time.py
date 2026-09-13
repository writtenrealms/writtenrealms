"""Owner-controlled, transactional advances of an entire instance simulation."""
from dataclasses import replace
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from spawns.actions.base import ActionError
from spawns.instance_clock import (
    SIMULATION_STEP_SECONDS, REACTIONS_DONE, capture_events, controlled_run,
    defer_work, gameplay_now, in_simulation, simulation_scope, time_control_run, clock_guard,
)
from worlds.models import InstanceRun


MAX_STEP_ACTORS = 512
MAX_STEP_WORK = 2048
# Inspection and personal presentation do not consume a simulation action.
# Subscription side effects of these events are still deferred until advance.
FREE_COMMANDS = frozenset({
    'state.sync', 'look', 'inspect', 'scan', 'inventory', 'equipment', 'stats',
    'who', 'help', 'history', 'instance', 'materials', 'recipes', 'recipe',
    'currencies', 'list', 'shop', 'offer', 'socials', 'say', 'yell', 'emote',
    'social', 'roll', 'alias', 'unalias', 'ability.hotkey',
    'advance', 'time_control', 'cancel_turn', 'pause', 'resume',
})


def snapshot(run):
    return {
        'enabled': bool(run.time_control), 'run_id': run.pk, 'world_id': run.spawned_world_id,
        'pause_in_combat': run.pause_in_combat, 'paused': run.time_paused,
        'clock_offset_seconds': run.clock_offset_seconds,
        'tick': run.simulation_tick, 'generation': run.time_generation,
        'pending_revision': run.pending_revision,
        'simulation_time': run.simulation_time.isoformat() if run.simulation_time else None,
        'step_seconds': SIMULATION_STEP_SECONDS,
        'pending_command': run.pending_command or None,
        'can_advance': run.time_paused and run.status in run.ACTIVE_STATUSES,
    }


def snapshot_for_player(player):
    run = time_control_run(player.world)
    return snapshot(run) if run and run.owner_id == player.pk else None


def state_event(run):
    from spawns.events import GameEvent
    return GameEvent('instance.time_control', snapshot(run),
                     [f'player.{run.owner_id}'] if run.owner_id else [])


def _owned_run(player_id, *, run_id=None):
    from spawns.models import Player
    location = Player.objects.filter(pk=player_id).values_list('world_id', flat=True).first()
    run = InstanceRun.objects.select_for_update().filter(
        spawned_world_id=location, time_control=True,
    ).first()
    if not run or run.owner_id != player_id or (run_id is not None and run.pk != run_id):
        raise ActionError('You do not control time in this instance.', code='time_control_unavailable')
    player = Player.objects.select_for_update().get(pk=player_id)
    if player.world_id != run.spawned_world_id or run.status not in run.ACTIVE_STATUSES:
        raise ActionError('This instance is no longer active.', code='instance_inactive')
    return run, player


def prepare_command(ctx, command_type, *, ability=None):
    if in_simulation(ctx.world) or command_type in FREE_COMMANDS:
        return False
    # Text parses aliases/chains first, then gates each concrete action.
    if command_type == 'text' and not ctx.payload.get('raw_text'):
        return False
    if command_type in {'ability.learn', 'ability.unlearn', 'quest'} and not ctx.payload.get('args'):
        return False
    if command_type == 'quest':
        from spawns.handlers.quests import QuestCommandHandler
        from quests.services.engine import QuestRuntimeError
        args = ctx.payload.get('args') or []
        try:
            if args and QuestCommandHandler()._resolve_subcommand(str(args[0])) in {'list', 'resolved', 'info'}:
                return False
        except QuestRuntimeError:
            return False  # The handler returns its normal parse error without mutation.
    run = controlled_run(ctx.world)
    if run is None:
        return False
    if ctx.script_source or ctx.actor_type != 'player' or ctx.builder_force:
        defer_work(run.spawned_world_id, 'command', {
            'command_type': command_type, 'actor_type': ctx.actor_type, 'actor_id': ctx.actor_id,
            'payload': ctx.payload, 'script_source': ctx.script_source,
            'issuer_type': ctx.issuer_type, 'issuer_id': ctx.issuer_id,
            'builder_force': ctx.builder_force,
        })
        return True
    with transaction.atomic():
        run, player = _owned_run(ctx.player.pk, run_id=run.pk)
        request_key = ctx.payload.get('_request_id')
        if request_key and (run.pending_command.get('payload') or {}).get('_request_id') == request_key and (
            (run.pending_command.get('payload') or {}).get('_request_segment') == ctx.payload.get('_request_segment')
        ):
            # Broker retry of a preparation cannot replace a newer revision.
            return True
        if command_type == 'text':
            from spawns.actions.abilities import resolve_ability_for_command, validate_ability_preparation
            # Reuse hotkey resolution. Ordinary timing exits above, so these
            # reads happen only when a player submits a paused instance action.
            ability = ability or resolve_ability_for_command(ctx.world, ctx.payload.get('command'))
            if ability is not None:
                try:
                    validate_ability_preparation(player, ability)
                except ActionError as exc:
                    ctx.publish({'type': 'cmd.ability.error', 'text': exc.message,
                                 'data': {'code': exc.code, 'error': exc.message, **exc.data}})
                    return True  # Preserve the previous action and revision.
        run.pending_command = {
            'command_type': command_type, 'payload': dict(ctx.payload),
            'label': ctx.payload.get('_hotkey_resolution') or ctx.payload.get('raw_text') or ctx.payload.get('text') or command_type,
        }
        run.pending_revision += 1
        run.save(update_fields=['pending_command', 'pending_revision'])
        from spawns.events import enqueue_game_events
        enqueue_game_events([state_event(run)])
    ctx.publish({'type': 'instance.time_control', 'data': snapshot(run)})
    ctx.publish_success('prepare_turn', {'pending_revision': run.pending_revision},
                        f'Action: {run.pending_command["label"]}.')
    return True


def configure(player_id, *, pause_in_combat, expected_generation=None, run_id=None):
    if not isinstance(pause_in_combat, bool):
        raise ActionError('Choose whether to pause during combat.', code='invalid_time_mode')
    with transaction.atomic():
        run, player = _owned_run(player_id, run_id=run_id)
        if expected_generation is not None and expected_generation != run.time_generation:
            raise ActionError('The time setting changed. Refresh and try again.', code='stale_time_control')
        run.pause_in_combat = pause_in_combat
        run.time_generation += 1
        run.save(update_fields=['pause_in_combat', 'time_generation'])
        from spawns.instance_clock_transitions import synchronize_combat_pause
        # Reuse the locked object in nested command/combat lookups.
        from spawns.instance_clock import guarded_run_scope
        with guarded_run_scope(run):
            synchronize_combat_pause(run)
            if not run.time_paused and run.pending_command:
                pending = run.pending_command
                run.pending_command = {}
                run.pending_revision += 1
                run.save(update_fields=['pending_command', 'pending_revision'])
                from spawns.handlers import dispatch_command
                dispatch_command(pending['command_type'], player_id=player.pk, payload=pending['payload'])
            from spawns.events import enqueue_game_events
            enqueue_game_events([state_event(run)])
        return snapshot(run)


def cancel(player_id, *, run_id=None, expected_generation=None, expected_pending_revision=None):
    with transaction.atomic():
        run, _ = _owned_run(player_id, run_id=run_id)
        if ((expected_generation is not None and expected_generation != run.time_generation)
            or (expected_pending_revision is not None and expected_pending_revision != run.pending_revision)):
            raise ActionError('The prepared action changed. Review it before cancelling.', code='stale_instance_turn')
        run.pending_command = {}
        run.pending_revision += 1
        run.save(update_fields=['pending_command', 'pending_revision'])
        from spawns.events import enqueue_game_events
        enqueue_game_events([state_event(run)])
        return snapshot(run)


def schedule_deferred_work(run, due_at=None):
    if run.time_paused:
        return
    args = {'run_id': run.pk, 'expected_generation': run.time_generation}
    delay = max(0, ((due_at or timezone.now()) - timezone.now()).total_seconds())
    def enqueue():
        from spawns.tasks import continue_instance_work
        continue_instance_work.apply_async(kwargs=args, countdown=delay)
    transaction.on_commit(enqueue, robust=True)


def _execute_work(work):
    from spawns.trigger_subscriptions import dispatch_trigger_subscriptions_for_event
    from quests.subscriptions import dispatch_quest_subscriptions_for_event
    from spawns.handlers import dispatch_command
    payload = dict(work.payload)
    if work.kind == 'trigger':
        dispatch_trigger_subscriptions_for_event(**payload)
    elif work.kind == 'quest':
        dispatch_quest_subscriptions_for_event(**payload)
    elif work.kind == 'command':
        dispatch_command(**payload)
    elif work.kind == 'tracker':
        from spawns.actions.mob_movement import ResolveTrackerChaseAction
        events = ResolveTrackerChaseAction().execute(**payload).events
        if not capture_events(events):
            from spawns.events import publish_events
            publish_events(events)
    elif work.kind == 'script_segments':
        from spawns.tasks import execute_trigger_script_segments
        execute_trigger_script_segments(**payload)
    elif work.kind == 'follow':
        from spawns.tasks import propagate_follow_movement
        propagate_follow_movement(**payload)


def _drain_reactions(state):
    from spawns.events import (
        SCRIPT_COMMAND_PROVENANCE_KEY, PRIVATE_CONTROL_EVENT_KEY,
        PLAYER_ROOM_ENTER_EMITTED_KEY, TRANSFER_ENTER_EVENT_TYPE,
        PLAYER_ROOM_ENTER_EVENT_TYPE, FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
    )
    from spawns.trigger_subscriptions import dispatch_trigger_subscriptions_for_event
    from quests.subscriptions import dispatch_quest_subscriptions_for_event
    from spawns.models import InstanceClockWork
    from spawns.handlers import dispatch_command
    def due_work():
        if not state.accept_deferred_work:
            return InstanceClockWork.objects.none()
        return InstanceClockWork.objects.filter(world_id=state.run.spawned_world_id).exclude(
            kind__in=['advance_receipt', 'trigger_gate']).filter(Q(due_at__isnull=True) | Q(due_at__lte=state.run.simulation_time))
    count = 0
    while state.events or due_work().exists():
        if count >= MAX_STEP_WORK:
            raise ActionError('This instance produced too much work in one turn.', code='instance_turn_budget')
        if state.events:
            event = state.events.popleft()
            data = event.data
            kind = event.type.lower()
            actor = (data.get('actor') or {}).get('key') if isinstance(data.get('actor'), dict) else None
            actor = actor or (event.recipients[0] if len(event.recipients) == 1 else None)
            if kind == 'private.combat.tracker_chase':
                from spawns.actions.mob_movement import ResolveTrackerChaseAction
                capture_events(ResolveTrackerChaseAction().execute(**data).events)
                count += 1
                continue
            if kind == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE:
                # Single-player entry clears player follows. NPC follows remain
                # scoped and are drained without a real-time broker wakeup.
                from spawns.following import propagate_follow_movement_batch
                after = 0
                while True:
                    result = propagate_follow_movement_batch(data, after_id=after)
                    count += 1
                    if count >= MAX_STEP_WORK:
                        raise ActionError('Too many followers in one turn.', code='instance_turn_budget')
                    if result.next_after_id is None:
                        break
                    after = result.next_after_id
                if data.get('_follow_outbox_event_id'):
                    from spawns.models import GameEventOutbox
                    GameEventOutbox.objects.filter(event_id=data['_follow_outbox_event_id']).delete()
                continue
            scripted = isinstance(data.get(SCRIPT_COMMAND_PROVENANCE_KEY), dict)
            private = data.get(PRIVATE_CONTROL_EVENT_KEY)
            if not private and not (kind == TRANSFER_ENTER_EVENT_TYPE and data.get(PLAYER_ROOM_ENTER_EMITTED_KEY)) and (
                not scripted or kind in {TRANSFER_ENTER_EVENT_TYPE, PLAYER_ROOM_ENTER_EVENT_TYPE}
            ):
                dispatch_trigger_subscriptions_for_event(event_type=event.type, event_data=data,
                                                         actor_key=actor, connection_id=event.connection_id)
            if not private and kind != PLAYER_ROOM_ENTER_EVENT_TYPE and (not scripted or kind == 'affect.transfer'):
                dispatch_quest_subscriptions_for_event(event_type=event.type, event_data=data,
                                                       actor_key=actor, connection_id=event.connection_id)
            state.output.append(replace(event, data={**data, REACTIONS_DONE: True}))
        else:
            work = due_work().order_by('pk').first()
            _execute_work(work)
            work.delete()
        count += 1


def _reconcile_rooms(run):
    from spawns.combat_reconciliation import reconcile
    from spawns.models import CombatRoomState
    for _ in range(64):
        ids = list(CombatRoomState.objects.filter(world_id=run.spawned_world_id,
                   next_run_ts__isnull=False).order_by('id').values_list('id', flat=True)[:32])
        if not ids:
            return
        for pk in ids:
            reconcile(pk)
    raise ActionError('This instance has too much combat admission work for one turn.', code='instance_turn_budget')


def advance(*, run_id, player_id=None, expected_tick=None, expected_generation=None,
            expected_pending_revision=None, request_id=None):
    from spawns.models import Player, Mob, CombatEncounter, InstanceClockWork
    with transaction.atomic():
        run = InstanceRun.objects.select_for_update().filter(pk=run_id, time_control=True).first()
        if not run or run.status not in run.ACTIVE_STATUSES:
            raise ActionError('This instance is no longer active.', code='instance_inactive')
        if run.owner_id != player_id:
            raise ActionError('Only this instance\'s owner may advance time.', code='not_instance_owner')
        if ((expected_tick is not None and expected_tick != run.simulation_tick)
            or (expected_generation is not None and expected_generation != run.time_generation)
            or (expected_pending_revision is not None and expected_pending_revision != run.pending_revision)):
            raise ActionError('The turn or prepared action changed. Review it before advancing.', code='stale_instance_turn')
        owner = Player.objects.select_for_update().filter(pk=run.owner_id, world_id=run.spawned_world_id).first()
        if owner is None or not owner.in_game:
            raise ActionError('Enter the instance before advancing time.', code='instance_owner_absent')
        if not run.time_paused:
            raise ActionError('Time only waits while combat is paused.', code='instance_not_paused')
        if request_id:
            _, created = InstanceClockWork.objects.get_or_create(
                dedupe_key=f'advance:{run.pk}:{request_id}',
                defaults={'world_id': run.spawned_world_id, 'kind': 'advance_receipt', 'payload': {}},
            )
            if not created:
                return snapshot(run)
        # Bound the transaction and fail atomically; never partially advance a
        # run or scan unrelated worlds. Other runs retain independent locks.
        if Mob.objects.filter(world_id=run.spawned_world_id, is_pending_deletion=False).count() > MAX_STEP_ACTORS:
            raise ActionError('This instance exceeds the supported 512 active mobs per turn.', code='instance_turn_budget')
        with simulation_scope(run) as state:
            pending = run.pending_command
            # The normal leave/reset commands remain an escape from broken
            # builder scripts, even when an earlier reaction exceeded budget.
            if not pending or pending['command_type'] not in {'leave', '/reset'}:
                _drain_reactions(state)
            run.pending_command = {}
            if pending:
                from spawns.handlers import dispatch_command
                dispatch_command(pending['command_type'], player_id=owner.pk, payload=pending['payload'])
                failure = next((event for event in state.events if event.type.startswith('cmd.')
                                and event.type.endswith('.error')), None)
                if failure:
                    raise ActionError(failure.text or 'The prepared action is no longer valid.',
                                      code=failure.data.get('code', 'invalid_prepared_action'))
                state.accept_deferred_work = Player.objects.filter(pk=owner.pk, world_id=run.spawned_world_id).exists()
                _drain_reactions(state)
            # Apply the action at the start of the interval, then process timers
            # through its end. A two-second delay created now completes in this
            # advance; a three-second delay completes in the following one.
            run.simulation_time += timedelta(seconds=SIMULATION_STEP_SECONDS)
            run.simulation_tick += 1
            run.pending_revision += 1
            run.save(update_fields=['simulation_time', 'simulation_tick', 'pending_command', 'pending_revision'])
            if state.accept_deferred_work:
                from spawns.instance_time_schedulers import drain_due_schedulers
                from spawns.loading import run_spawn_plans_for_world
                drain_due_schedulers(run, lambda: _drain_reactions(state))
                from config import game_settings
                spawn_period = max(1.0, float(game_settings.GAME_SPAWN_PLAN_INTERVAL_SECONDS))
                elapsed = run.simulation_tick * SIMULATION_STEP_SECONDS
                if int(elapsed // spawn_period) > int((elapsed - SIMULATION_STEP_SECONDS) // spawn_period):
                    run_spawn_plans_for_world(world=run.spawned_world)
                from spawns.merchants import process_due_merchant_restocks
                process_due_merchant_restocks(world_id=run.spawned_world_id, now=run.simulation_time)
                _drain_reactions(state)
                _reconcile_rooms(run)
                from spawns.combat_rounds import resolve
                ids = list(CombatEncounter.objects.filter(world_id=run.spawned_world_id,
                           status=CombatEncounter.STATUS_ACTIVE).order_by('pk').values_list('pk', flat=True))
                for encounter_id in ids:
                    result = resolve(encounter_id, auto_advance=False, durable_events=True)
                    capture_events(result.events)
                _drain_reactions(state)
                from spawns.tasks import run_game_heartbeat
                run_game_heartbeat()
                _drain_reactions(state)
                _reconcile_rooms(run)
                _drain_reactions(state)
                drain_due_schedulers(run, lambda: _drain_reactions(state))
            run.last_active_at = timezone.now()
            run.save(update_fields=['last_active_at'])
            synchronize_combat_pause(run, force=True)
            if Player.objects.filter(pk=owner.pk, world_id=run.spawned_world_id).exists():
                state.output.append(state_event(run))
            else:
                from spawns.events import GameEvent
                state.output.append(GameEvent('instance.time_control', {'enabled': False,
                    'world_id': run.spawned_world_id, 'run_id': run.pk}, [owner.key]))
            state.persisting = True
            from spawns.events import persist_follow_dependent_game_events
            persist_follow_dependent_game_events(state.output, force=True)
        return snapshot(run)


def resume_for_player(player):
    with clock_guard(player.world) as run:
        if run and run.owner_id == player.pk:
            synchronize_combat_pause(run)


def process_deferred_work(*, run_id, expected_generation=None, limit=64):
    from spawns.models import InstanceClockWork
    run = InstanceRun.objects.filter(pk=run_id, time_control=True).first()
    if not run:
        return 0
    with clock_guard(run.spawned_world_id) as run:
        if run.time_paused or (expected_generation is not None and expected_generation != run.time_generation):
            return 0
        count = 0
        while count < limit and not run.time_paused:
            work = InstanceClockWork.objects.filter(world_id=run.spawned_world_id).exclude(
                kind__in=['advance_receipt', 'trigger_gate']).filter(
                Q(due_at__isnull=True) | Q(due_at__lte=timezone.now())).order_by('id').first()
            if not work:
                break
            _execute_work(work)
            work.delete()
            count += 1
            synchronize_combat_pause(run)
        if count == limit and not run.time_paused:
            schedule_deferred_work(run)
        return count


def recover_due_instances(*, limit=100):
    from django.core.cache import cache
    from spawns.models import InstanceClockWork
    from spawns.tasks import continue_instance_work
    from django.db.models import Exists, OuterRef
    from spawns.models import CombatParticipant
    in_combat = CombatParticipant.objects.filter(
        player_id=OuterRef('owner_id'), player__world_id=OuterRef('spawned_world_id'),
        encounter__world_id=OuterRef('spawned_world_id'), encounter__status='active', is_active=True,
    )
    orphaned_pauses = InstanceRun.objects.filter(time_control=True, time_paused=True).exclude(
        Exists(in_combat),
    ).values_list('spawned_world_id', flat=True)[:limit]
    for world in orphaned_pauses:
        with clock_guard(world) as run:
            synchronize_combat_pause(run)
    rows = InstanceClockWork.objects.exclude(kind__in=['advance_receipt', 'trigger_gate']).filter(
        Q(due_at__isnull=True) | Q(due_at__lte=timezone.now()),
        world__instance_run__time_control=True, world__instance_run__time_paused=False,
    ).values_list('world__instance_run__pk', 'world__instance_run__time_generation').distinct()[:limit]
    count = 0
    for pk, generation in rows:
        key = f'instance-work-recovery:{pk}:{generation}'
        if not cache.add(key, 1, timeout=10):
            continue
        try:
            continue_instance_work.delay(run_id=pk, expected_generation=generation)
        except Exception:
            cache.delete(key)
            raise
        count += 1
    return count


from spawns.instance_clock_transitions import synchronize_combat_pause
