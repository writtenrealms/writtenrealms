"""Bounded spatial combat membership and its shared transaction boundary.

Admission and commands discover membership before locking, lock in canonical
order, then revalidate it. A concurrent join/merge restarts the transaction;
it never extends an already-held lock set in reverse order.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import random

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from builders.models import FactionRelationship
from core.condition_dsl import ConditionContext, evaluate_condition
from core.factions import faction_is_core
from core.world_config import inherited_system_config
from spawns.actions.base import ActionError
from spawns.models import (
    ActiveEffect, CharacterState, CombatEncounter, CombatParticipant,
    CombatSide, CombatSideRelation, DuelMatch, DuelParticipant, Mob, MobState, Player,
)
from worlds.models import InstanceRun


MAX_PARTICIPANTS = 32
MAX_EFFECTS = 256
MAX_EVENTS = 1024
MAX_REACTIONS = 16
NPC_ACTIVITY_SECONDS = 30
_current: ContextVar[CombatContext | None] = ContextVar('combat_context', default=None)


class TopologyChanged(Exception):
    """Internal optimistic-discovery race; retry only outside the transaction."""


def actor_key(actor):
    return f'{"player" if isinstance(actor, Player) else "mob"}.{actor.pk}'


def actor_filter(keys):
    players, mobs = [], []
    for key in keys:
        kind, _, raw_id = key.partition('.')
        (players if kind == 'player' else mobs).append(int(raw_id))
    return Q(player_id__in=players) | Q(mob_id__in=mobs)


def actor_snapshot(actor):
    return {
        'key': actor_key(actor), 'kind': 'player' if isinstance(actor, Player) else 'mob',
        'id': actor.pk, 'name': actor.name,
        'definition_id': getattr(actor, 'definition_id', None),
        'factions': faction_snapshot(actor),
    }


def faction_snapshot(actor):
    factions = {}
    assignments = actor._prefetched_objects_cache.get('faction_assignments')
    if assignments is None:
        raise RuntimeError('Combat faction policy requires preloaded assignments.')
    for assignment in assignments:
        faction = assignment.faction
        factions['core' if faction_is_core(faction) else faction.code] = (
            faction.code if faction_is_core(faction) else assignment.value
        )
    if isinstance(actor, Player) and actor.core_faction_id:
        # New players store core identity directly, without an assignment row.
        # The canonical identity also wins over any older core assignment.
        factions['core'] = actor.core_faction.code
    return factions


@dataclass
class CombatContext:
    encounters: dict[int, CombatEncounter]
    participants: list[CombatParticipant]
    actors: dict[str, Player | Mob]
    matches: dict[int, DuelMatch] = field(default_factory=dict)
    runs: dict[int, InstanceRun] = field(default_factory=dict)
    rng: random.Random | None = None
    reactions: int = 0
    policy: CombatPolicy | None = None

    def participant(self, key, *, active=True):
        return next((p for p in self.participants if p.actor_key == key
                     and (p.is_active or not active)), None)

    def members(self, encounter):
        return [p for p in self.participants if p.encounter_id == encounter.pk and p.is_active]


def current_context():
    return _current.get()


@contextmanager
def locked_combat(*, keys=(), encounter_ids=()):
    keys = set(keys)
    encounter_ids = set(encounter_ids)
    existing = current_context()
    if existing is not None:
        if not keys.issubset(existing.actors) or not encounter_ids.issubset(existing.encounters):
            raise TopologyChanged()
        yield existing
        return

    with transaction.atomic():
        memberships = list(CombatParticipant.objects.filter(
            actor_filter(keys), is_active=True,
        ).values_list('encounter_id', flat=True)) if keys else []
        encounter_ids.update(memberships)
        refs = list(CombatEncounter.objects.filter(pk__in=encounter_ids).values(
            'id', 'duel_match_id', 'duel_match__run_id', 'world_id',
        ))
        match_ids = sorted({r['duel_match_id'] for r in refs if r['duel_match_id']})
        player_ids = [int(k.split('.')[1]) for k in keys if k.startswith('player.')]
        # The first attack has no encounter yet. Discover its active match
        # authority before locking any actor rows.
        match_ids = sorted(set(match_ids) | set(DuelParticipant.objects.filter(
            player_id__in=player_ids, match__status=DuelMatch.STATUS_ACTIVE,
        ).values_list('match_id', flat=True)))
        run_ids = sorted({r['duel_match__run_id'] for r in refs if r['duel_match__run_id']})
        run_ids = sorted(set(run_ids) | set(DuelMatch.objects.filter(pk__in=match_ids)
                                           .values_list('run_id', flat=True)))
        world_ids = {r['world_id'] for r in refs}
        for kind, model in [('player', Player), ('mob', Mob)]:
            ids = [int(k.split('.')[1]) for k in keys if k.startswith(kind + '.')]
            if ids:
                world_ids.update(model.objects.filter(pk__in=ids).values_list('world_id', flat=True))
        shared_run_ids = list(InstanceRun.objects.filter(spawned_world_id__in=world_ids)
                              .exclude(pk__in=run_ids).values_list('pk', flat=True))
        exclusive_ids = set(run_ids) | set(InstanceRun.objects.filter(
            pk__in=shared_run_ids, time_control=True,
        ).values_list('pk', flat=True))
        # Ordinary fights share a lifecycle lock. Closing/resetting the run
        # takes FOR UPDATE, while different rooms can resolve concurrently.
        with connection.cursor() as cursor:
            for run_id in sorted(set(run_ids) | set(shared_run_ids)):
                mode = 'UPDATE' if run_id in exclusive_ids else 'SHARE'
                cursor.execute(f'SELECT id FROM worlds_instancerun WHERE id = %s FOR {mode}', [run_id])
        runs = {r.pk: r for r in InstanceRun.objects.filter(pk__in=[*run_ids, *shared_run_ids])}
        from spawns.instance_clock import time_control_run
        for pk, run in list(runs.items()):
            if run.time_control:
                runs[pk] = time_control_run(run.spawned_world_id) or run
        matches = {m.pk: m for m in DuelMatch.objects.select_for_update().filter(
            pk__in=match_ids,
        ).order_by('pk')}
        encounters = {e.pk: e for e in CombatEncounter.objects.select_for_update(of=('self',))
                      .select_related('world', 'room__zone').filter(pk__in=encounter_ids)
                      .order_by('pk')}
        list(CombatSide.objects.select_for_update().filter(
            encounter_id__in=encounters,
        ).order_by('pk'))
        list(CombatSideRelation.objects.select_for_update(of=('self',)).filter(
            lower_side__encounter_id__in=encounters,
        ).order_by('pk'))
        participants = list(CombatParticipant.objects.select_for_update(of=('self',))
                            .filter(encounter_id__in=encounters, is_active=True).order_by('pk'))
        keys.update(p.actor_key for p in participants if p.is_active and p.actor_key)
        players = [int(k.split('.')[1]) for k in keys if k.startswith('player.')]
        mobs = [int(k.split('.')[1]) for k in keys if k.startswith('mob.')]
        player_rows = list(Player.objects.select_for_update(of=('self',))
                           .select_related('world', 'room__zone', 'equipment', 'core_faction')
                           .prefetch_related('faction_assignments__faction')
                           .filter(pk__in=players).order_by('pk'))
        mob_rows = list(Mob.objects.select_for_update(of=('self',))
                        .select_related('world', 'room__zone', 'definition')
                        .prefetch_related('faction_assignments__faction')
                        .filter(pk__in=mobs).order_by('pk'))
        live_ids = set(CombatParticipant.objects.filter(
            actor_filter(keys), is_active=True,
        ).values_list('encounter_id', flat=True)) if keys else set()
        if not live_ids.issubset(encounters):
            raise TopologyChanged()
        actors = {actor_key(a): a for a in [*player_rows, *mob_rows]}
        # An actor can enter another run during optimistic discovery. Restart
        # before reading or mutating that run without its lifecycle guard.
        locked_worlds = {run.spawned_world_id for run in runs.values()}
        actual_run_worlds = set(InstanceRun.objects.filter(
            spawned_world_id__in={a.world_id for a in actors.values()},
        ).values_list('spawned_world_id', flat=True))
        if not actual_run_worlds.issubset(locked_worlds):
            raise TopologyChanged()
        worlds = {e.world_id: e.world for e in encounters.values()}
        rooms = {e.room_id: e.room for e in encounters.values()}
        for actor in actors.values():
            actor.world = worlds.setdefault(actor.world_id, actor.world)
            if actor.room_id:
                actor.room = rooms.setdefault(actor.room_id, actor.room)
        for participant in participants:
            actor = actors.get(participant.actor_key)
            if actor is not None:
                participant._state.fields_cache['player' if participant.player_id else 'mob'] = actor
                participant._state.fields_cache['encounter'] = encounters[participant.encounter_id]
        context = CombatContext(encounters, participants, actors, matches, runs)
        token = _current.set(context)
        context.initial_generations = {pk: e.schedule_generation for pk, e in encounters.items()}
        try:
            # Player resource maxima are computed stats, unlike the stored mob fields.
            for actor in player_rows:
                from core.computations import compute_stats
                stats = compute_stats(actor.level, actor.archetype, char=actor, world=actor.world)
                for resource in ('health', 'energy', 'stamina'):
                    setattr(actor, resource + '_max', max(1, int(stats.get(resource + '_max') or 1)))
            yield context
            from spawns.instance_clock_transitions import synchronize_combat_pause
            for run in context.runs.values():
                if run.time_control:
                    synchronize_combat_pause(run)
        finally:
            _current.reset(token)


def transact(callback, *, keys=(), encounter_ids=()):
    if current_context() is not None:
        with locked_combat(keys=keys, encounter_ids=encounter_ids) as context:
            return callback(context)
    for attempt in range(3):
        try:
            with locked_combat(keys=keys, encounter_ids=encounter_ids) as context:
                return callback(context)
        except TopologyChanged:
            if attempt == 2:
                raise ActionError('The fight changed. Please try again.', code='combat_changed')
        except OperationalError as exc:
            code = getattr(exc.__cause__, 'sqlstate', None) or getattr(exc.__cause__, 'pgcode', None)
            if code not in {'40P01', '40001'} or attempt == 2:
                raise
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', '')
            if constraint not in {'combat_player_one_active', 'combat_mob_one_active'}:
                raise
            if attempt == 2:
                raise ActionError('The fight changed. Please try again.', code='combat_changed') from exc


class CombatPolicy:
    """Query-free decisions over one preloaded actor/diplomacy/state snapshot."""

    def __init__(self, actors):
        self.actors = {actor_key(a): a for a in actors}
        self.factions = {key: faction_snapshot(a) for key, a in self.actors.items()}
        faction_ids = {assignment.faction_id for a in actors
                       for assignment in a._prefetched_objects_cache['faction_assignments']}
        faction_ids.update(a.core_faction_id for a in actors
                           if isinstance(a, Player) and a.core_faction_id)
        self.diplomacy = {
            (source, target): ('hostile' if standing < 0 else 'allied' if standing > 0 else 'neutral')
            for source, target, standing in FactionRelationship.objects.filter(
                faction_id__in=faction_ids, towards_id__in=faction_ids,
            ).values_list('faction__code', 'towards__code', 'standing')
        }
        self.states = {f'player.{pk}': data for pk, data in CharacterState.objects.filter(
            player_id__in=[a.pk for a in actors if isinstance(a, Player)],
        ).values_list('player_id', 'data')}
        self.states.update({f'mob.{pk}': data for pk, data in MobState.objects.filter(
            mob_id__in=[a.pk for a in actors if isinstance(a, Mob)],
        ).values_list('mob_id', 'data')})

    def relationship(self, actor, target):
        left, right = self.factions[actor_key(actor)], self.factions[actor_key(target)]
        pair = (left.get('core'), right.get('core'))
        if pair in self.diplomacy:
            return self.diplomacy[pair]
        if all(pair):
            return 'allied' if pair[0] == pair[1] else 'hostile'
        if any(value > 0 and right.get(code, 0) < 0
               for code, value in left.items() if code != 'core'):
            return 'hostile'
        if actor.group_id and actor.group_id == target.group_id:
            return 'allied'
        return 'neutral'

    def allied(self, actor, target):
        return self.relationship(actor, target) == self.relationship(target, actor) == 'allied'

    def automatic_allowed(self, actor, target=None):
        if not isinstance(actor, Mob):
            return False
        condition = actor.definition.combat_engage_when if actor.definition_id else {}
        return not condition or evaluate_condition(condition, context=ConditionContext(
            actor=actor, player=target if isinstance(target, Player) else None,
            room=actor.room, world=actor.world,
            state_cache={'character': self.states.get(actor_key(actor), {})},
        ))


def eligible(actor):
    return bool(actor and actor.room_id and actor.health > 0 and (
        True if isinstance(actor, Player) else
        actor.attackable and not actor.is_pending_deletion
    ))


def _authorize_hostility(actor, target, match=None):
    if isinstance(actor, Player) and isinstance(target, Player):
        if match is None or match.status != DuelMatch.STATUS_ACTIVE:
            raise ActionError('Player combat is not authorized here.', code='pvp_disabled')
        teams = dict(DuelParticipant.objects.filter(
            match=match, role=DuelParticipant.ROLE_CONTESTANT,
            player_id__in=[actor.pk, target.pk],
        ).values_list('player_id', 'team'))
        if len(teams) != 2 or teams[actor.pk] == teams[target.pk]:
            raise ActionError('You cannot attack that contestant.', code='pvp_disabled')


def _limit():
    return max(2, min(MAX_PARTICIPANTS, int(getattr(settings, 'COMBAT_MAX_PARTICIPANTS', MAX_PARTICIPANTS))))


def initiative_roll(seed, key):
    return random.Random(f'{seed}:{key}').random() * 1_000_000


def _join(context, encounter, actor, side):
    key = actor_key(actor)
    participant = next((p for p in context.participants
                        if p.encounter_id == encounter.pk and p.actor_key == key), None)
    values = dict(
        side=side, team=side.position, is_active=True, exit_reason='',
        first_eligible_round=encounter.round_number + 1,
        actor_snapshot=actor_snapshot(actor),
        initiative=initiative_roll(encounter.random_seed, key),
    )
    if participant is None:
        participant = CombatParticipant.objects.create(
            encounter=encounter, **{'player' if isinstance(actor, Player) else 'mob': actor},
            **values,
        )
        context.participants.append(participant)
    else:
        for key, value in values.items():
            setattr(participant, key, value)
        participant.save(update_fields=list(values))
    return participant


def engage_locked(context, attacker, target, *, reason='attack', match=None, ally_key=None):
    if isinstance(target, Mob) and not target.attackable:
        raise ActionError('You cannot attack them.', code='not_attackable')
    if not eligible(attacker) or not eligible(target) or actor_key(attacker) == actor_key(target):
        raise ActionError('That combat target is unavailable.', code='target_invalid')
    if (attacker.world_id, attacker.room_id) != (target.world_id, target.room_id):
        raise ActionError('You do not see them here.', code='target_missing')
    if target.is_invisible:
        raise ActionError('You do not see them here.', code='target_missing')
    if any(run.spawned_world_id == attacker.world_id and run.status not in InstanceRun.ACTIVE_STATUSES
           for run in context.runs.values()):
        raise ActionError('This instance is no longer active.', code='combat_disabled')
    if reason in {'automatic', 'assist'}:
        from spawns.combat_reconciliation import _initiates
        policy = context.policy = CombatPolicy(list(context.actors.values()))
        allowed = _initiates(policy, attacker, target)
        if reason == 'assist':
            ally = context.actors.get(ally_key)
            member, opponent = context.participant(ally_key), context.participant(target.key)
            assist = attacker.definition.combat_assist if attacker.definition_id else 'none'
            allowed = bool(ally and member and opponent and member.encounter_id == opponent.encounter_id
                           and member.side_id != opponent.side_id and assist != 'none'
                           and policy.allied(attacker, ally) and policy.automatic_allowed(attacker, target)
                           and (assist != 'same_spawn_cohort' or attacker.group_id and attacker.group_id == ally.group_id))
        if not allowed:
            raise ActionError('Automatic engagement is no longer allowed.', code='engagement_ineligible')
    rules = inherited_system_config(attacker.world)
    if rules and not rules.allow_combat:
        raise ActionError('Combat is disabled here.', code='combat_disabled')
    _authorize_hostility(attacker, target, match)
    from spawns.combat_rounds import detach_actor
    for participant in list(context.participants):
        actor = context.actors.get(participant.actor_key)
        source = context.encounters[participant.encounter_id]
        if participant.is_active and (not eligible(actor) or
                (actor.world_id, actor.room_id) != (source.world_id, source.room_id)):
            detach_actor(context, participant.actor_key, reason='unavailable')
    a, b = context.participant(actor_key(attacker)), context.participant(actor_key(target))
    if a and b and a.encounter_id == b.encounter_id:
        if a.side_id == b.side_id:
            raise ActionError('You cannot attack an ally in this fight.', code='unsupported_combat_topology')
        encounter = context.encounters[a.encounter_id]
        changed = encounter.status == CombatEncounter.STATUS_PAUSED
        if changed:
            encounter.status = CombatEncounter.STATUS_ACTIVE
            encounter.schedule_generation += 1
            encounter.state_revision += 1
            from spawns.instance_clock import gameplay_now
            encounter.npc_active_until = gameplay_now(attacker.world) + timedelta(seconds=NPC_ACTIVITY_SECONDS)
            encounter.next_resolution_ts = gameplay_now(attacker.world) if encounter.resolution_interval >= 0 else None
            encounter.save(update_fields=['status', 'schedule_generation', 'state_revision',
                                         'npc_active_until', 'next_resolution_ts'])
        from spawns.instance_clock_transitions import synchronize_combat_pause
        for run in context.runs.values():
            if run.spawned_world_id == encounter.world_id:
                synchronize_combat_pause(run)
        return encounter, a, b, changed

    encounter_ids = {p.encounter_id for p in (a, b) if p}
    members = [p for p in context.participants if p.is_active and p.encounter_id in encounter_ids]
    if len(members) + int(a is None) + int(b is None) > _limit():
        raise ActionError('That fight has reached its participant limit.', code='combat_capacity')
    sources = [context.encounters[pk] for pk in sorted(encounter_ids)]
    if any(e.duel_match_id != (match.pk if match else None) or
           (e.world_id, e.room_id) != (attacker.world_id, attacker.room_id) for e in sources):
        raise ActionError('Those fights cannot be connected.', code='unsupported_combat_topology')
    if len({e.resolution_interval for e in sources}) > 1:
        raise ActionError('Those fights have different pacing.', code='unsupported_combat_topology')
    if ActiveEffect.objects.filter(encounter_id__in=encounter_ids).count() > MAX_EFFECTS:
        raise ActionError('That fight has reached its effect limit.', code='combat_capacity')

    policy = context.policy or CombatPolicy(list(context.actors.values()))
    context.policy = policy
    assignments = {actor_key(attacker): 1, actor_key(target): 2}
    for member in members:
        source_actor = a if a and member.encounter_id == a.encounter_id else b
        assignments[member.actor_key] = (
            assignments[source_actor.actor_key] if member.side_id == source_actor.side_id
            else 3 - assignments[source_actor.actor_key]
        )
    rows = list(assignments)
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            p, q = context.participant(left), context.participant(right)
            if p and q and p.encounter_id == q.encounter_id:
                continue
            actor, other = context.actors[left], context.actors[right]
            if assignments[left] == assignments[right] and not policy.allied(actor, other):
                raise ActionError('Joining would require another combat side.', code='unsupported_combat_topology')
            if assignments[left] != assignments[right]:
                _authorize_hostility(actor, other, match)
                if {left, right} != {actor_key(attacker), actor_key(target)} and policy.allied(actor, other):
                    raise ActionError('Joining would attack an ally.', code='unsupported_combat_topology')

    if sources:
        encounter = sources[0]
    else:
        interval = float(getattr(rules, 'combat_resolution_interval', 0) or 0)
        if match and interval == 0:
            interval = -1
        encounter = CombatEncounter.objects.create(
            world=attacker.world, room=attacker.room, duel_match=match,
            resolution_interval=interval,
        )
        context.encounters[encounter.pk] = encounter
        CombatSide.objects.bulk_create([
            CombatSide(encounter=encounter, position=1), CombatSide(encounter=encounter, position=2),
        ])
        side_ids = list(encounter.sides.order_by('pk').values_list('pk', flat=True))
        CombatSideRelation.objects.create(lower_side_id=side_ids[0], higher_side_id=side_ids[1])
    sides = {s.position: s for s in encounter.sides.all()}
    # Retain the canonical source's side orientation, including after a reversed attack.
    canonical_member = next((p for p in members if p.encounter_id == encounter.pk), None)
    if canonical_member and assignments[canonical_member.actor_key] != canonical_member.team:
        assignments = {key: 3 - side for key, side in assignments.items()}
    next_round = max([e.round_number for e in sources] or [0])
    for member in members:
        source = context.encounters[member.encounter_id]
        offset = next_round - source.round_number
        member.first_eligible_round += offset
        for field_name in ('pending_ability', 'pending_flee'):
            pending = dict(getattr(member, field_name))
            for round_field in ('queued_round', 'started_round', 'ready_round'):
                if round_field in pending:
                    pending[round_field] += offset
            setattr(member, field_name, pending)
        member.encounter = encounter
        member.side = sides[assignments[member.actor_key]]
        member.team = member.side.position
    if members:
        CombatParticipant.objects.bulk_update(members, ['encounter', 'side', 'team', 'first_eligible_round',
                                                      'pending_ability', 'pending_flee'])
    encounter.round_number = next_round
    for donor in sources[1:]:
        ActiveEffect.objects.filter(encounter=donor).update(encounter=encounter)
        for effect in getattr(context, 'effect_rows', {}).values():
            if effect.encounter_id == donor.pk:
                effect.encounter = encounter
        # Historical participants retain their source; finalized reward receipts are immutable.
        donor.status = CombatEncounter.STATUS_FINISHED
        donor.merged_into = encounter
        donor.schedule_generation += 1
        donor.state_revision += 1
        donor.next_resolution_ts = None
        donor.save(update_fields=['status', 'merged_into', 'schedule_generation', 'state_revision', 'next_resolution_ts'])
        from spawns.combat_publication import snapshot_event
        from spawns.events import persist_follow_dependent_game_events
        persist_follow_dependent_game_events([snapshot_event(context, donor)], force=True)
    if a is None:
        a = _join(context, encounter, attacker, sides[assignments[actor_key(attacker)]])
    if b is None:
        b = _join(context, encounter, target, sides[assignments[actor_key(target)]])
    if not a.current_target_id:
        a.current_target = b
        a.save(update_fields=['current_target'])
    if not b.current_target_id:
        b.current_target = a
        b.save(update_fields=['current_target'])
    if reason in {'automatic', 'assist'} and b.player_id and encounter.round_number == 0 and not b.intent_ready:
        b.current_target = None
        from spawns.combat_rounds import target_for
        target_for(context, b, intent=False)
    encounter.state_revision += 1
    if len(sources) != 1 or encounter.status != CombatEncounter.STATUS_ACTIVE or (
        encounter.resolution_interval >= 0 and encounter.next_resolution_ts is None
    ):
        encounter.schedule_generation += 1
    encounter.status = CombatEncounter.STATUS_ACTIVE
    from spawns.instance_clock import gameplay_now
    encounter.npc_active_until = gameplay_now(attacker.world) + timedelta(seconds=NPC_ACTIVITY_SECONDS)
    deadlines = [e.next_resolution_ts for e in sources if e.next_resolution_ts]
    if encounter.resolution_interval >= 0:
        encounter.next_resolution_ts = min(deadlines) if deadlines else gameplay_now(attacker.world) + timedelta(
            seconds=encounter.resolution_interval,
        )
    encounter.save(update_fields=['round_number', 'state_revision', 'schedule_generation',
                                 'status', 'npc_active_until', 'next_resolution_ts'])
    from spawns.combat_reconciliation import request_reconciliation
    request_reconciliation(encounter.world_id, encounter.room_id, [attacker.key, target.key],
                           observed=any(isinstance(actor, Player) and actor.in_game for actor in (attacker, target)))
    from spawns.instance_clock_transitions import synchronize_combat_pause
    for run in context.runs.values():
        if run.spawned_world_id == encounter.world_id:
            synchronize_combat_pause(run)
    return encounter, a, b, True


def engage(attacker, target, *, reason='attack', match=None):
    keys = [actor_key(attacker), actor_key(target)]
    return transact(lambda ctx: engage_locked(
        ctx, ctx.actors[keys[0]], ctx.actors[keys[1]], reason=reason, match=match,
    ), keys=keys)
