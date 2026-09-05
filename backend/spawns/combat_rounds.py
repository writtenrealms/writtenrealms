"""One participant round pipeline for PVE, duels, and NPC-only encounters."""
from __future__ import annotations

from datetime import timedelta
import random

from django.db.models import Q
from django.utils import timezone

from core.attack_routines import resolve_attack_routine
from spawns.actions.effects import next_character_effect_tick_ts
from spawns.ability_intents import prioritize_ready_interrupts
from spawns.combat_encounters import (
    MAX_EVENTS, MAX_EFFECTS, actor_key, current_context, eligible, transact,
)
from spawns.events import enqueue_game_events
from spawns.models import ActiveEffect, CombatEncounter, CombatParticipant, Mob, Player


def _combat():
    from spawns.actions import combat
    return combat


def target_for(context, participant, *, intent=True):
    members = context.members(context.encounters[participant.encounter_id])
    by_id = {p.pk: p for p in members}
    actor = context.actors.get(participant.actor_key)

    def valid(candidate, *, friendly=False):
        if candidate is None:
            return False
        target = context.actors.get(candidate.actor_key)
        return bool(eligible(target) and actor and
                    (target.world_id, target.room_id) == (actor.world_id, actor.room_id) and
                    (friendly or candidate.side_id != participant.side_id) and
                    (target is actor or not target.is_invisible))

    if intent and participant.pending_ability:
        ref = participant.pending_ability.get('target') or {}
        key = f'{ref.get("type")}.{ref.get("id")}'
        candidate = next((p for p in members if p.actor_key == key), None)
        if valid(candidate, friendly=True):
            return candidate
        # An explicitly queued ability must fail its own target validation;
        # it must not silently hit a different actor.
    candidate = by_id.get(participant.current_target_id)
    if valid(candidate):
        return candidate
    candidates = [p for p in members if valid(p)]
    candidate = min(candidates, key=lambda p: (
        -int(getattr(context.actors[p.actor_key], 'target_priority', 0)), p.pk,
    ), default=None)
    participant.current_target = candidate
    participant.save(update_fields=['current_target'])
    return candidate


def leave_participant(context, participant, *, reason, refund=True):
    if not participant.is_active:
        return
    actor = context.actors.get(participant.actor_key)
    if refund and actor and participant.pending_flee:
        actor.stamina += max(0, int(participant.pending_flee.get('movement_cost') or 0))
        actor.save(update_fields=['stamina'])
    participant.is_active = False
    participant.exit_reason = reason
    participant.pending_ability = {}
    participant.pending_flee = {}
    participant.current_target = None
    participant.intent_ready = False
    participant.save(update_fields=['is_active', 'exit_reason', 'pending_ability',
                                    'pending_flee', 'current_target', 'intent_ready'])
    from spawns.combat_reconciliation import request_reconciliation
    encounter = context.encounters[participant.encounter_id]
    # Capacity and assistance opportunities may change for actors that were
    # refused earlier. Revisit the room once, even if this actor is deleted.
    request_reconciliation(encounter.world_id, encounter.room_id)
    target_filter = Q(target_player_id=participant.player_id) if participant.player_id else Q(
        target_mob_id=participant.mob_id,
    )
    ActiveEffect.objects.filter(target_filter, scope=ActiveEffect.SCOPE_ENCOUNTER,
                                encounter_id=participant.encounter_id).delete()
    ActiveEffect.objects.filter(target_filter, scope=ActiveEffect.SCOPE_CHARACTER).update(
        next_tick_ts=next_character_effect_tick_ts(),
    )
    for other in context.participants:
        if other.current_target_id == participant.pk:
            other.current_target = None
            other.save(update_fields=['current_target'])


def finish_encounter(context, encounter):
    if encounter.status == CombatEncounter.STATUS_FINISHED:
        return
    for participant in context.members(encounter):
        leave_participant(context, participant, reason='finished')
    ActiveEffect.objects.filter(encounter=encounter, scope=ActiveEffect.SCOPE_ENCOUNTER).delete()
    encounter.status = CombatEncounter.STATUS_FINISHED
    encounter.next_resolution_ts = None
    encounter.schedule_generation += 1
    encounter.state_revision += 1
    encounter.save(update_fields=['status', 'next_resolution_ts', 'schedule_generation', 'state_revision'])


def detach_actor(context, key, *, reason='moved', refund=True):
    from spawns.combat_publication import snapshot_event
    from spawns.events import persist_follow_dependent_game_events

    participant = context.participant(key)
    if participant is None:
        return []
    encounter = context.encounters[participant.encounter_id]
    had_preparation = bool(participant.pending_ability)
    leave_participant(context, participant, reason=reason, refund=refund)
    if not _has_hostility(context, encounter):
        finish_encounter(context, encounter)
    else:
        encounter.state_revision += 1
        encounter.save(update_fields=['state_revision'])
    events = [snapshot_event(context, encounter)]
    if had_preparation and participant.player_id:
        from spawns.ability_prepare_state import ability_prepare_state_events_for_players
        events.extend(ability_prepare_state_events_for_players([participant.player_id]))
    persist_follow_dependent_game_events(events, force=True)
    return [encounter.pk]


def _has_hostility(context, encounter):
    return len({p.side_id for p in context.members(encounter)}) > 1


def _ability_turn(context, encounter, participant, target_participant, round_id):
    combat = _combat()
    actor = context.actors[participant.actor_key]
    target = context.actors[target_participant.actor_key]
    if isinstance(actor, Player):
        events, result = combat._execute_pending_player_ability(
            encounter=encounter, participant=participant, opponent=target_participant,
            player=actor, target_mob=target, room=encounter.room,
            round_id=round_id, player_health_max=actor.health_max,
            target_pending_ability=target_participant.pending_ability,
        )
    else:
        events, result = combat._execute_pending_mob_ability(
            encounter=encounter, participant=participant, opponent=target_participant,
            player=target, target_mob=actor, room=encounter.room,
            round_id=round_id, player_health_max=target.health_max,
        )
    if result.target_interrupted:
        target_participant.pending_ability = {}
    participant.save(update_fields=['pending_ability'])
    if result.target_interrupted:
        target_participant.save(update_fields=['pending_ability'])
    return events, result


def _defeat(context, encounter, participant, killer, round_id, *, prior_events=None):
    from spawns.combat_rewards import defeat_mob
    combat = _combat()
    actor = context.actors[participant.actor_key]
    leave_participant(context, participant, reason='defeated', refund=False)
    if isinstance(actor, Mob):
        return defeat_mob(context, encounter, participant, actor, killer)
    if encounter.duel_match_id:
        from spawns.actions.pvp import LockedPvpContext, _resolve_defeat
        match = context.matches[encounter.duel_match_id]
        duel_context = LockedPvpContext(context.runs[match.run_id], match, encounter,
                                       context.participants,
                                       {a.pk: a for a in context.actors.values() if isinstance(a, Player)})
        winner = killer if isinstance(killer, Player) else next(
            (a for a in context.actors.values() if isinstance(a, Player) and a.pk != actor.pk), None,
        )
        result = _resolve_defeat(context=duel_context, winner=winner, loser=actor,
                                 reason='defeat', prior_events=prior_events or [])
        if prior_events is not None:
            prior_events.clear()
        return result.events
    _, events = combat.apply_player_death(
        player=actor, origin_room=encounter.room, killer=killer,
        target_text='You have been slain.',
        room_text=f'{combat._combat_name(killer)} kills {actor.name}.',
        config=actor.world.effective_config,
        death_token=f'{round_id}:player:{actor.pk}', cause='combat',
    )
    return events


def _record_damage(context, actor, target, amount):
    if amount <= 0:
        return
    context.last_damage_source = {**getattr(context, 'last_damage_source', {}), target.key: actor}
    if not isinstance(target, Mob):
        return
    participant = context.participant(actor.key)
    if participant is None:
        return
    key = str(target.pk)
    participant.contributions = {**participant.contributions,
                                key: int(participant.contributions.get(key, 0)) + amount}
    participant.save(update_fields=['contributions'])


def _resolve_defeats(context, encounter, fallback, round_id, events):
    for victim in list(context.members(encounter)):
        actor = context.actors.get(victim.actor_key)
        if actor and actor.health <= 0:
            killer = getattr(context, 'last_damage_source', {}).get(victim.actor_key, fallback)
            events.extend(_defeat(context, encounter, victim, killer, round_id, prior_events=events))


def resolve_locked(context, encounter, *, auto_advance, expected_round=None,
                   expected_generation=None, durable_events=False, leading_events=None):
    from spawns.actions.effects import advance_character_effect_durations
    from spawns.combat_publication import snapshot_event
    combat = _combat()
    empty = lambda active=False: combat.CombatStepResult(None, [], active)
    if encounter.status != CombatEncounter.STATUS_ACTIVE:
        return empty()
    if (expected_round is not None and expected_round != encounter.round_number) or (
        expected_generation is not None and expected_generation != encounter.schedule_generation
    ):
        return empty(True)
    now = timezone.now()
    from config import constants
    if encounter.duel_match_id:
        match = context.matches.get(encounter.duel_match_id)
        if match is None or match.status != match.STATUS_ACTIVE or context.runs[match.run_id].status not in context.runs[match.run_id].ACTIVE_STATUSES:
            finish_encounter(context, encounter)
            return combat.CombatStepResult(None, [snapshot_event(context, encounter)], False)
    if encounter.world.lifecycle != constants.WORLD_LIFECYCLE_RUNNING:
        encounter.status = CombatEncounter.STATUS_PAUSED
        encounter.next_resolution_ts = None
        encounter.schedule_generation += 1
        encounter.state_revision += 1
        encounter.save(update_fields=['status', 'next_resolution_ts', 'schedule_generation', 'state_revision'])
        return combat.CombatStepResult(None, [snapshot_event(context, encounter)], False)
    if auto_advance and encounter.next_resolution_ts and encounter.next_resolution_ts > now:
        return empty(True)
    members = context.members(encounter)
    if not members:
        finish_encounter(context, encounter)
        return empty()
    for p in members:
        actor = context.actors.get(p.actor_key)
        if not eligible(actor) or (actor.world_id, actor.room_id) != (encounter.world_id, encounter.room_id):
            leave_participant(context, p, reason='unavailable')
    members = context.members(encounter)
    if not _has_hostility(context, encounter):
        finish_encounter(context, encounter)
        return combat.CombatStepResult(None, [snapshot_event(context, encounter)], False)
    players = [context.actors[p.actor_key] for p in members if p.player_id]
    from spawns.combat_publication import room_recipients
    if players or room_recipients(context, encounter):
        from spawns.combat_encounters import NPC_ACTIVITY_SECONDS
        encounter.npc_active_until = now + timedelta(seconds=NPC_ACTIVITY_SECONDS)
    if not players and (encounter.npc_active_until is None or encounter.npc_active_until <= now):
        encounter.status = CombatEncounter.STATUS_PAUSED
        encounter.next_resolution_ts = None
        encounter.schedule_generation += 1
        encounter.state_revision += 1
        encounter.save(update_fields=['status', 'next_resolution_ts', 'schedule_generation', 'state_revision'])
        return combat.CombatStepResult(None, [snapshot_event(context, encounter)], False)
    if encounter.resolution_interval == -1 and players and not (encounter.duel_match_id and not auto_advance) and not all(
        p.intent_ready for p in members if p.player_id and context.actors[p.actor_key].in_game
    ):
        return empty(True)
    encounter.round_number += 1
    encounter.last_resolution_ts = now
    round_id = f'encounter:{encounter.pk}:{encounter.round_number}'
    context.rng = random.Random(f'{encounter.random_seed}:{encounter.round_number}')
    events = list(leading_events or [])
    preparations_before = {p.player_id: dict(p.pending_ability) for p in members if p.player_id}
    chases = []
    skip = set()
    ordered = sorted(members, key=lambda p: (-p.initiative, p.pk))
    if encounter.round_number == 1:
        opening = {f'{r.get("type")}.{r.get("id")}' for r in encounter.opening_priority or []}
        ordered.sort(key=lambda p: p.actor_key not in opening)
    for p in ordered:
        actor = context.actors[p.actor_key]
        if isinstance(actor, Player):
            combat.stand_player(actor)
            stats = combat._player_combat_stats(actor)
            actor.health_max, actor.energy_max, actor.stamina_max = (
                stats.player_health_max, stats.player_energy_max, stats.player_stamina_max,
            )
        if p.pending_flee.get('status') == 'ready' and isinstance(actor, Player):
            context.fleeing_actor = actor.key
            if encounter.duel_match_id:
                from spawns.actions.pvp import LockedPvpContext, _complete_ready_flee
                match = context.matches[encounter.duel_match_id]
                duel_context = LockedPvpContext(context.runs[match.run_id], match, encounter,
                                                context.members(encounter),
                                                {a.pk: a for a in context.actors.values() if isinstance(a, Player)})
                _, _, flee_events = _complete_ready_flee(context=duel_context, participant=p,
                                                         player=actor, round_id=round_id)
                events.extend(flee_events)
                context.fleeing_actor = None
                skip.add(p.pk)
                continue
            outcome = combat._complete_flee(encounter=encounter, participant=p, player=actor, round_id=round_id)
            context.fleeing_actor = None
            p.save(update_fields=['pending_flee', 'pending_ability'])
            events.extend(outcome.events)
            if outcome.terminal_result:
                events.extend(outcome.terminal_result.events)
                if outcome.terminal_result.tracker_chase:
                    chases.append(outcome.terminal_result.tracker_chase)
            if not p.is_active or outcome.player_primary_consumed:
                skip.add(p.pk)
    for p in ordered:
        if not p.is_active:
            continue
        actor = context.actors[p.actor_key]
        outcome = combat._advance_character_periodic_effects(
            target_player=actor if isinstance(actor, Player) else None,
            target_mob=actor if isinstance(actor, Mob) else None,
            encounter=encounter, viewer=actor if isinstance(actor, Player) else None,
            round_id=round_id,
        )
        events.extend(outcome.events)
        if actor.health <= 0:
            events.extend(_defeat(context, encounter, p, outcome.killer, round_id, prior_events=events))
        if p.pending_flee.get('status') == 'preparing':
            p.pending_flee = {**p.pending_flee, 'status': 'ready'}
            p.pending_ability = {}
            p.save(update_fields=['pending_flee', 'pending_ability'])
            skip.add(p.pk)
            from spawns.events import GameEvent
            events.append(GameEvent('notification.combat.flee', {'status': 'preparing', 'round_id': round_id},
                                    [p.actor_key], 'You look for an opening to flee.'))
    for p in ordered:
        if not p.is_active or p.pk in skip:
            continue
        actor = context.actors[p.actor_key]
        target_p = target_for(context, p)
        if isinstance(actor, Mob) and actor.fights_back and not p.pending_ability and target_p:
            target = context.actors[target_p.actor_key]
            selection = combat._choose_mob_ability(mob=actor, player=target, room=encounter.room)
            if selection:
                kind, target_id = combat._mob_ability_target_ref(ability=selection.ability, mob=actor, player=target)
                p.pending_ability = combat._pending_ability_payload(
                    ability=selection.ability, command=selection.ability.slug, target_type=kind,
                    target_id=target_id, queued_round=encounter.round_number,
                    cooldown_override=selection.cooldown_override,
                )
    by_key = {(p.actor_key.split('.')[0], int(p.actor_key.split('.')[1])): p for p in ordered}
    keys = prioritize_ready_interrupts(
        list(by_key), pending_by_actor={k: p.pending_ability for k, p in by_key.items()},
    )
    exclusions = {}
    for key in keys:
        p = by_key[key]
        actor = context.actors[p.actor_key]
        if not p.is_active or p.pk in skip or p.first_eligible_round > encounter.round_number:
            continue
        if isinstance(actor, Mob) and not actor.fights_back:
            continue
        target_p = target_for(context, p)
        if target_p is None:
            continue
        target = context.actors[target_p.actor_key]
        if combat._consume_stun(encounter, target_type='player' if p.player_id else 'mob', target_id=actor.pk):
            p.pending_ability = {}
            p.save(update_fields=['pending_ability'])
            from spawns.events import GameEvent
            from spawns.combat_publication import room_recipients
            payload = combat._serialize_combat_char(actor)
            events.append(GameEvent('notification.combat.effect', {
                'target': payload, 'effect': 'stun', 'round_id': round_id,
                '_combat_narration': {actor.key: 'You are stunned and cannot act.'},
            }, room_recipients(context, encounter), f'{actor.name} is stunned and cannot act.'))
            continue
        ability_events, result = _ability_turn(context, encounter, p, target_p, round_id)
        events.extend(ability_events)
        exclusions[p.pk] = result.cooldown_exclude
        _resolve_defeats(context, encounter, actor, round_id, events)
        if not result.consumed_primary and p.is_active:
            target_p = target_for(context, p, intent=False)
            if target_p:
                target = context.actors[target_p.actor_key]
                for strike in resolve_attack_routine(actor=actor, target=target, world=actor.world):
                    strike_target = combat._combat_strike_target(
                        encounter=encounter, player=actor, target_mob=target, room=encounter.room,
                        actor=actor, strike=strike, default_target=target,
                    )
                    if strike_target is None or not p.is_active:
                        continue
                    outcome = combat._apply_combat_strike(
                        encounter=encounter, player=actor, target_mob=target, room=encounter.room,
                        actor=actor, target=strike_target, strike=strike, round_id=round_id,
                    )
                    events.extend(outcome.events)
                    _resolve_defeats(context, encounter, actor, round_id, events)
                    if not target_p.is_active:
                        break
        if len(events) > MAX_EVENTS:
            raise RuntimeError('Combat event budget exceeded before commit.')
    combat._advance_non_ticking_effect_durations(encounter)
    for p in ordered:
        if not p.is_active:
            continue
        actor = context.actors[p.actor_key]
        exclude = {exclusions[p.pk]} if exclusions.get(p.pk) else set()
        changed = (combat.decrement_ability_cooldowns(actor, exclude=exclude) if isinstance(actor, Player)
                   else combat._decrement_mob_ability_cooldowns(actor, exclude=exclude))
        if changed:
            actor.save(update_fields=['ability_cooldowns'])
        if isinstance(actor, Player) and (changed or exclude):
            from spawns.actions.abilities import ability_state_event
            events.append(ability_state_event(actor))
        advance_character_effect_durations(actor, current_round_id=round_id, encounter=encounter)
        p.intent_ready = False
        p.save(update_fields=['intent_ready', 'pending_ability'])
    from spawns.ability_prepare_state import ability_prepare_state_events_for_players
    preparation_changes = [p.player_id for p in members if p.player_id and
                           preparations_before.get(p.player_id) != p.pending_ability]
    events.extend(ability_prepare_state_events_for_players(preparation_changes))
    encounter.state_revision += 1
    encounter.schedule_generation += 1
    if not _has_hostility(context, encounter):
        finish_encounter(context, encounter)
    elif encounter.resolution_interval >= 0:
        encounter.next_resolution_ts = now + timedelta(seconds=encounter.resolution_interval)
    encounter.save(update_fields=['round_number', 'last_resolution_ts', 'state_revision',
                                 'schedule_generation', 'next_resolution_ts', 'npc_active_until'])
    events.append(snapshot_event(context, encounter))
    # The chase request shares the movement outbox. A failed broker enqueue
    # leaves the event available for retry, and the chase key deduplicates it.
    from spawns.events import GameEvent
    events.extend(GameEvent('private.combat.tracker_chase', chase) for chase in chases)
    return combat.CombatStepResult(players[0].key if players else None, events,
                                   encounter.status == CombatEncounter.STATUS_ACTIVE,
                                   tracker_chase=chases[0] if chases else None)


def resolve(encounter_id, *, auto_advance, expected_round=None, expected_generation=None,
            durable_events=False):
    def run(context):
        encounter = context.encounters.get(encounter_id)
        if encounter is None:
            return _combat().CombatStepResult(None, [], False)
        generation = encounter.schedule_generation
        if not auto_advance:
            for member in context.members(encounter):
                member.intent_ready = True
        result = resolve_locked(context, encounter, auto_advance=auto_advance,
                                expected_round=expected_round, expected_generation=expected_generation)
        if durable_events and result.events:
            from dataclasses import replace

            enqueue_game_events(result.events)
            result = replace(result, events=[])
        if generation != encounter.schedule_generation:
            from spawns.combat_commands import schedule

            schedule(encounter)
        return result
    return transact(run, encounter_ids=[encounter_id])
