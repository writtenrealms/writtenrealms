"""Player commands against authoritative combat participants."""
from django.utils import timezone

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.combat_encounters import actor_key, after_combat_publication, engage_locked, transact
from spawns.combat_publication import snapshot_event
from spawns.combat_rounds import resolve_locked, target_for
from spawns.events import GameEvent, persist_follow_dependent_game_events
from spawns.models import CombatEncounter, CombatParticipant, Mob, Player


def _combat():
    from spawns.actions import combat
    return combat


def schedule(encounter):
    from spawns.instance_clock import is_time_controlled
    if is_time_controlled(encounter.world):
        return
    if encounter.status != CombatEncounter.STATUS_ACTIVE or encounter.next_resolution_ts is None:
        return
    from spawns.combat_encounters import current_context
    ctx = current_context()
    if ctx is not None:
        token = (encounter.pk, encounter.schedule_generation)
        scheduled = getattr(ctx, '_scheduled', set())
        if token in scheduled or ctx.initial_generations.get(encounter.pk) == encounter.schedule_generation:
            return
        scheduled.add(token)
        ctx._scheduled = scheduled
    encounter_id, generation, round_number = encounter.pk, encounter.schedule_generation, encounter.round_number
    delay = max(0, (encounter.next_resolution_ts - timezone.now()).total_seconds())
    def enqueue():
        from spawns.tasks import resolve_combat_encounter
        resolve_combat_encounter.apply_async(kwargs={
            'encounter_id': encounter_id, 'expected_generation': generation,
            'expected_round': round_number,
        }, countdown=delay)
    after_combat_publication(enqueue)


def _finish_command(context, encounter, events):
    from spawns.instance_clock import is_time_controlled
    if encounter.resolution_interval == -1 and not is_time_controlled(encounter.world):
        result = resolve_locked(context, encounter, auto_advance=False)
        events.extend(result.events)
        if not any(event.type == 'notification.combat.snapshot' for event in result.events):
            events.append(snapshot_event(context, encounter))
    else:
        events.append(snapshot_event(context, encounter))
    schedule(encounter)
    return ActionResult(events=persist_follow_dependent_game_events(events, force=True))


def _target_ref(player, selector):
    if not player.room_id:
        raise ActionError('You are nowhere.', code='no_room')
    if not str(selector or '').strip():
        participant = CombatParticipant.objects.filter(player=player, is_active=True).select_related(
            'current_target__mob', 'current_target__player',
        ).first()
        if participant and participant.current_target:
            return participant.current_target.actor
    return resolve_room_mob_target(
        player.room, selector, world=player.world, empty_error='Kill what?',
        not_found_error="You don't see them here.", allow_single_match_when_empty=True,
        allow_first_match_when_empty=True,
        empty_candidate_filter=lambda mob: mob.attackable and mob.health > 0 and not mob.is_pending_deletion,
    )


def kill(player_id, selector):
    from spawns.actions.pvp import try_execute_kill
    result = try_execute_kill(player_id, selector)
    if result is not None:
        return result
    player = Player.objects.select_related('world', 'room').get(pk=player_id)
    target = _target_ref(player, selector)
    keys = [player.key, actor_key(target)]
    def run(context):
        actor, target = context.actors[keys[0]], context.actors.get(keys[1])
        from spawns import duels
        reason = duels.duel_combat_block_reason(actor)
        if reason:
            raise ActionError(reason, code='duel_combat_disabled')
        encounter, participant, opponent, created = engage_locked(context, actor, target)
        events = _combat()._cancel_pending_door_for_physical_action(
            actor, message='You stop working with the door to fight.',
        )
        _combat().stand_player(actor)
        participant.current_target = opponent
        participant.intent_ready = True
        participant.revision += 1
        participant.save(update_fields=['current_target', 'intent_ready', 'revision'])
        encounter.state_revision += 1
        encounter.save(update_fields=['state_revision'])
        acknowledgment = _combat()._engage_events(player=actor, room=actor.room, mob=target)
        acknowledgment[0].data.update(encounter_id=encounter.pk, intent_revision=participant.revision)
        events.extend(acknowledgment)
        return _finish_command(context, encounter, events)
    return transact(run, keys=keys)


def flee(player_id):
    from spawns.actions.effects import preventing_action_effect
    from spawns.actions.pvp import try_execute_flee
    result = try_execute_flee(player_id)
    if result is not None:
        return result
    def run(context):
        player = context.actors[f'player.{player_id}']
        participant = context.participant(player.key)
        if not participant:
            raise ActionError('You are not in combat.', code='not_in_combat')
        encounter = context.encounters[participant.encounter_id]
        combat = _combat()
        if (player.world_id, player.room_id) != (encounter.world_id, encounter.room_id):
            from spawns.combat_rounds import detach_actor
            detach_actor(context, player.key, reason='moved')
            from spawns.ability_prepare_state import ability_prepare_state_events_for_players
            return ActionResult(events=persist_follow_dependent_game_events(
                [GameEvent('cmd.flee.error', {'code': 'combat_ended'}, [player.key], 'The fight has ended.'),
                 *ability_prepare_state_events_for_players([player.pk])], force=True))
        if participant.pending_flee.get('status') == 'ready' and encounter.resolution_interval == -1:
            participant.intent_ready = True
            participant.save(update_fields=['intent_ready'])
            return _finish_command(context, encounter, [])
        prevention = preventing_action_effect(player, 'flee', phase='before_action')
        if prevention:
            raise ActionError(combat._action_prevention_message(prevention, action='flee'),
                              code='action_prevented',
                              data=combat._action_prevention_data(prevention, action='flee'))
        cleared_ability = bool(participant.pending_ability)
        if not participant.pending_flee:
            destination = combat._choose_flee_destination(player)
            player.stamina -= destination.movement_cost
            player.save(update_fields=['stamina'])
            participant.pending_flee = {
                'status': 'preparing', 'queued_round': encounter.round_number,
                'direction': destination.direction, 'destination_room_id': destination.room_id,
                'movement_cost': destination.movement_cost,
            }
            participant.pending_ability = {}
        participant.intent_ready = True
        participant.revision += 1
        participant.save(update_fields=['pending_flee', 'pending_ability', 'intent_ready', 'revision'])
        events = combat._cancel_pending_door_for_physical_action(
            player, message='You stop working with the door to flee.',
        )
        if cleared_ability:
            from spawns.ability_prepare_state import ability_prepare_state_events_for_players
            events.extend(ability_prepare_state_events_for_players([player.pk]))
        events.append(GameEvent('cmd.flee.success', {**participant.pending_flee, 'status': 'queued'},
                                [player.key], 'You prepare to flee.'))
        return _finish_command(context, encounter, events)
    return transact(run, keys=[f'player.{player_id}'])


def disengage(player_id, selector=None):
    from spawns.combat_rounds import finish_encounter, leave_participant, _has_hostility

    def run(context):
        actor = context.actors[f'player.{player_id}']
        participant = context.participant(actor.key)
        if participant is None:
            raise ActionError('You are not in combat.', code='not_in_combat')
        encounter = context.encounters[participant.encounter_id]
        target_p = target_for(context, participant, intent=False)
        if selector:
            selected = _target_ref(actor, selector)
            target_p = context.participant(selected.key)
        if target_p is None or target_p.encounter_id != encounter.pk or target_p.side_id == participant.side_id:
            raise ActionError('That target is not an opponent in this fight.', code='target_invalid')
        target = context.actors[target_p.actor_key]
        if isinstance(target, Player) or target.fights_back:
            raise ActionError(f'You cannot disengage while {target.name} is fighting back.',
                              code='target_fights_back')
        for other in context.members(encounter):
            if other.pk in {participant.pk, target_p.pk}:
                continue
            pending_target = (other.pending_ability or {}).get('target') or {}
            if other.current_target_id == target_p.pk or f'{pending_target.get("type")}.{pending_target.get("id")}' == target_p.actor_key:
                raise ActionError('Someone else is still engaging that target.', code='target_busy')
        from spawns.combat_effects import actor_effects
        if any(e.scope == 'encounter' and e.is_hostile for e in actor_effects(target) or []):
            raise ActionError('A harmful effect is still engaging that target.', code='target_busy')
        events = _combat()._cancel_pending_door_for_physical_action(
            actor, message='You stop working with the door to disengage.',
        )
        participant.pending_ability = {}
        participant.save(update_fields=['pending_ability'])
        leave_participant(context, target_p, reason='disengaged')
        if not _has_hostility(context, encounter):
            finish_encounter(context, encounter)
        else:
            encounter.state_revision += 1
            encounter.save(update_fields=['state_revision'])
        events.extend([GameEvent('cmd.disengage.success', {
                                    'encounter_id': encounter.pk, 'still_in_combat': participant.is_active,
                                    'target': target_p.actor_snapshot,
                                    'actor': _combat().serialize_actor(actor, actor.room).model_dump(),
                                },
                                [actor.key], 'You disengage from combat.'),
                       snapshot_event(context, encounter)])
        return ActionResult(events=persist_follow_dependent_game_events(events, force=True))
    return transact(run, keys=[f'player.{player_id}'])


def _area_candidates(player, ability):
    """Freeze a bounded room candidate set before taking any actor lock."""
    if not any(str(c.get('target', '')).startswith('room.') for c in ability.components or []):
        return []
    from spawns.combat_encounters import MAX_PARTICIPANTS, CombatPolicy
    rows = []
    for model in (Player, Mob):
        query = model.objects.filter(world_id=player.world_id, room_id=player.room_id, health__gt=0)
        query = query.filter(in_game=True) if model is Player else query.filter(is_pending_deletion=False)
        batch = list(query.select_related('world', 'room', *(['definition'] if model is Mob else []))
                     .prefetch_related('faction_assignments__faction').order_by('pk')[:MAX_PARTICIPANTS + 1])
        if len(batch) > MAX_PARTICIPANTS:
            raise ActionError('There are too many possible area targets.', code='combat_capacity')
        rows.extend(batch)
    if len(rows) > MAX_PARTICIPANTS:
        raise ActionError('There are too many possible area targets.', code='combat_capacity')
    return [a.key for a in rows]


def ability(action, player_id, *, ability, command, args, connection_id=None):
    from spawns.actions.abilities import (
        _ability_ack, _pending_payload, _raise_if_ability_casting, validate_ability_ready,
    )
    player = Player.objects.select_related('world', 'room').get(pk=player_id)
    validate_ability_ready(player, ability)
    target_type = (ability.target or {}).get('type')
    self_target = target_type in {'self', 'ally'}
    selector = ' '.join(args).strip()
    target = player if self_target else _target_ref(player, selector)
    if target_type == 'ally' and selector and selector.lower() not in {'self', 'me'}:
        from spawns.actions.targeting import find_room_player_target
        target = find_room_player_target(player.room, selector, world=player.world) or _target_ref(player, selector)
    target_key = actor_key(target)
    keys = list(dict.fromkeys([player.key, target_key, *_area_candidates(player, ability)]))
    def run(context):
        actor, target = context.actors[keys[0]], context.actors.get(target_key)
        validate_ability_ready(actor, ability)
        from spawns.actions.doors import cancel_pending_player_door_action_durably
        cancel_pending_player_door_action_durably(
            player=actor, code='physical_action_replaced',
            message='You stop working with the door to use an ability.',
        )
        participant = context.participant(actor.key)
        if self_target and participant is None:
            if target.key != actor.key:
                raise ActionError('That ally must be in your fight.', code='combat_required')
            if not (ability.target or {}).get('allow_out_of_combat', True):
                raise ActionError('That ability requires combat.', code='combat_required')
            return action._resolve_self_utility(player=actor, ability=ability)
        if self_target:
            encounter = context.encounters[participant.encounter_id]
            ally = context.participant(target.key)
            if not ally or ally.encounter_id != encounter.pk or ally.side_id != participant.side_id:
                raise ActionError('That ally is not in your fight.', code='target_invalid')
        else:
            encounter, participant, opponent, _ = engage_locked(context, actor, target)
        if any(c.get('target') == 'room.hostiles' for c in ability.components or []):
            from spawns.combat_encounters import CombatPolicy
            context.policy = CombatPolicy(list(context.actors.values()))
            for candidate in list(context.actors.values()):
                if candidate.key != actor.key and not candidate.is_invisible and context.policy.relationship(actor, candidate) == 'hostile':
                    encounter, participant, _, _ = engage_locked(context, actor, candidate)
        _raise_if_ability_casting(participant.pending_ability)
        replaced = bool(participant.pending_ability)
        participant.pending_ability = _pending_payload(
            ability=ability, command=command,
            target_type='player' if isinstance(target, Player) else 'mob', target_id=target.pk,
            queued_round=encounter.round_number,
        )
        participant.intent_ready = True
        participant.revision += 1
        participant.save(update_fields=['pending_ability', 'intent_ready', 'revision'])
        encounter.state_revision += 1
        encounter.save(update_fields=['state_revision'])
        ack = _ability_ack(player=actor, ability=ability, replaced=replaced, target=target)
        ack.data.update(intent_revision=participant.revision, encounter_id=encounter.pk)
        return _finish_command(context, encounter, [ack])
    return transact(run, keys=keys)


def room_opener(player_id, *, ability, command, args, connection_id=None):
    from spawns.actions import abilities as actions
    from config import constants
    from worlds.models import Room

    player = Player.objects.select_related('world', 'room').get(pk=player_id)
    actions.validate_ability_ready(player, ability)
    direction, selector = actions._split_room_opener_args(args, ability=ability)
    if not direction and (ability.target or {}).get('range') == 'adjacent_room':
        raise ActionError('Use a direction and target, such as charge rabbit east.', code='invalid_args')
    destination_id = (actions.ResolveMoveAction().execute(player, direction, source='ability')
                      .data['context'].dest_room_id if direction else player.room_id)
    destination = Room.objects.get(pk=destination_id)
    target = resolve_room_mob_target(
        destination, selector, world=player.world,
        empty_error=f'Use {ability.name} on what?', not_found_error="You don't see them there.",
        allow_single_match_when_empty=True, allow_first_match_when_empty=True,
        empty_candidate_filter=actions._is_implicit_room_opener_target,
    )
    keys = [player.key, target.key]

    def run(ctx):
        actor, opponent = ctx.actors[keys[0]], ctx.actors.get(keys[1])
        actions.validate_ability_ready(actor, ability)
        if ctx.participant(actor.key):
            raise ActionError(f'{ability.name} can only be used out of combat.', code='combat_in_progress')
        if not (ability.target or {}).get('allow_out_of_combat'):
            raise ActionError(f'{ability.name} can only be used out of combat.', code='combat_required')
        events = []
        if direction:
            move = actions.ResolveMoveAction().execute(actor, direction, source='ability').data['context']
            if move.dest_room_id != destination_id:
                raise ActionError('The destination changed. Please try again.', code='combat_changed')
            for event in (constants.TRIGGER_EVENT_BEFORE_MOVE_EXIT, constants.TRIGGER_EVENT_BEFORE_MOVE_ENTER):
                policy = actions.evaluate_movement_policies(
                    actor=actor, event=event, direction=move.direction,
                    origin_room_id=move.origin_room_id, destination_room_id=move.dest_room_id,
                    world_id=move.trigger_world_id,
                )
                if not policy.allowed:
                    raise ActionError(policy.feedback or 'You cannot go that way.', code=policy.code)
            actions.ChangeRoomAction().execute(actor, destination_id)
            actions.AdjustStaminaAction().execute(actor, -move.movement_cost)
            actor.save(update_fields=['room', 'location_sequence', 'follow_move_sequence', 'stamina', 'last_action_ts'])
            actor.viewed_rooms.add(destination_id)
            events.extend(actions.BuildMoveEventsAction().execute(move).events)
        encounter, participant, target_p, _ = engage_locked(ctx, actor, opponent)
        events.extend(_combat()._cancel_pending_door_for_physical_action(
            actor, message='You stop working with the door to use an ability.',
        ))
        actions.stand_player(actor)
        participant.pending_ability = actions._pending_payload(
            ability=ability, command=command, target_type='mob', target_id=opponent.pk,
            queued_round=encounter.round_number,
        )
        participant.current_target = target_p
        participant.intent_ready = True
        if (ability.target or {}).get('opener_priority'):
            encounter.opening_priority = [{'type': 'player', 'id': actor.pk, 'source': ability.slug}]
            encounter.save(update_fields=['opening_priority'])
        participant.save(update_fields=['pending_ability', 'current_target', 'intent_ready', 'initiative'])
        events.append(actions._ability_ack(player=actor, ability=ability, replaced=False, target=opponent))
        # A movement opener earns the first turn immediately; subsequent
        # rounds retain the encounter's normal cadence and initiative.
        if encounter.resolution_interval != -1:
            events.extend(resolve_locked(ctx, encounter, auto_advance=False).events)
        return _finish_command(ctx, encounter, events)
    return transact(run, keys=keys)
