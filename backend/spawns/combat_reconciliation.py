"""Event-driven, restartable room admission with bounded candidate pages."""
from spawns.instance_clock import serialized_world

from datetime import timedelta
from dataclasses import replace

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import constants
from spawns.actions.base import ActionError
from spawns.combat_encounters import (
    CombatPolicy, NPC_ACTIVITY_SECONDS, actor_filter, actor_key, engage_locked, eligible, transact,
)
from spawns.combat_commands import schedule
from spawns.combat_publication import snapshot_event
from spawns.events import persist_follow_dependent_game_events
from spawns.models import CombatEncounter, CombatParticipant, CombatRoomState, Mob, Player


PAGE_SIZE = 32
SOURCE_PAGE_SIZE = 8
MAX_CHANGED = 64
MAX_ADMISSIONS = 8
MAX_OPENING_PAGES = 64


def request_reconciliation(world_id, room_id, keys=(), *, observed=False):
    if not world_id or not room_id:
        return
    from spawns.instance_clock import gameplay_now, is_time_controlled
    now = gameplay_now(world_id)
    with transaction.atomic():
        state, _ = CombatRoomState.objects.get_or_create(world_id=world_id, room_id=room_id)
        state = CombatRoomState.objects.select_for_update().get(pk=state.pk)
        already_pending = state.next_run_ts is not None
        changed = sorted(set(state.changed_actors) | set(keys or ['*']))
        state.changed_actors = changed if len(changed) <= MAX_CHANGED and '*' not in changed else ['*']
        state.dirty_generation += 1
        state.next_run_ts = now
        if observed:
            state.active_until = now + timedelta(seconds=NPC_ACTIVITY_SECONDS)
        state.save(update_fields=['changed_actors', 'dirty_generation', 'next_run_ts', 'active_until'])
        state_id = state.pk
        def enqueue():
            from spawns.tasks import reconcile_combat_room
            reconcile_combat_room.delay(state_id)
        if not already_pending and not is_time_controlled(world_id):
            transaction.on_commit(enqueue, robust=True)


def recover_due_reconciliations(*, limit=100):
    from spawns.tasks import reconcile_combat_room

    now = timezone.now()
    from spawns.instance_clock import live_worlds
    ids = list(live_worlds(CombatRoomState.objects.filter(next_run_ts__lte=now))
               .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
               .order_by('next_run_ts', 'pk').values_list('pk', flat=True)[:max(1, min(limit, 100))])
    for state_id in ids:
        reconcile_combat_room.delay(state_id)
    return len(ids)


def _page(world_id, room_id, kind, after=0, size=PAGE_SIZE):
    model = Player if kind == 'player' else Mob
    queryset = model.objects.filter(world_id=world_id, room_id=room_id, pk__gt=after, health__gt=0)
    queryset = queryset.filter(in_game=True) if kind == 'player' else queryset.filter(is_pending_deletion=False)
    return list(queryset.select_related('world', 'room', *(['definition'] if kind == 'mob' else ['core_faction']))
                .prefetch_related('faction_assignments__faction').order_by('pk')[:size])


def _load_keys(keys, world_id, room_id):
    rows = []
    for kind, model in [('player', Player), ('mob', Mob)]:
        ids = [int(k.split('.')[1]) for k in keys if k.startswith(kind + '.')]
        rows.extend(model.objects.filter(pk__in=ids, world_id=world_id, room_id=room_id)
                    .select_related('world', 'room', *(['definition'] if kind == 'mob' else ['core_faction']))
                    .prefetch_related('faction_assignments__faction').order_by('pk'))
    return [actor for actor in rows if not isinstance(actor, Player) or actor.in_game]


def _initiates(policy, actor, target):
    if not isinstance(actor, Mob) or not eligible(actor) or not eligible(target) or target.is_invisible:
        return False
    if not policy.automatic_allowed(actor, target):
        return False
    aggression = constants.canonical_mob_aggression(actor.aggression)
    relation = policy.relationship(actor, target)
    return (aggression == constants.MOB_AGGRESSION_PLAYERS and isinstance(target, Player)
            or aggression == constants.MOB_AGGRESSION_ALL and relation != 'allied'
            or aggression in (constants.MOB_AGGRESSION_NORMAL, constants.MOB_AGGRESSION_FRIENDLY)
            and relation == 'hostile')


@serialized_world(lambda state_id: CombatRoomState.objects.filter(pk=state_id).values_list('world_id', flat=True).first())
def reconcile(state_id):
    from spawns.instance_clock import in_simulation, time_control_run
    ref = CombatRoomState.objects.filter(pk=state_id).values('world_id', 'room_id').first()
    if ref is None:
        return 0
    runtime_id = ref['world_id']
    run = time_control_run(runtime_id)
    if run and run.time_paused and not in_simulation(runtime_id):
        return 0
    if not (run and run.pause_in_combat and not in_simulation(runtime_id)
            and Player.objects.filter(pk=run.owner_id, world_id=runtime_id,
                                      room_id=ref['room_id'], in_game=True).exists()):
        return _reconcile_page(state_id, runtime_id)

    # The exclusive instance lock prevents a command or scheduled round from
    # interleaving with this opening. Keep the normal bounded pages, but finish
    # this room (including assistance) before the first admission pauses time.
    # Other rooms and ordinary multiplayer worlds retain one page per job.
    from spawns.instance_clock_transitions import defer_combat_pause
    admitted = 0
    with defer_combat_pause(run):
        for _ in range(MAX_OPENING_PAGES):
            admitted += _reconcile_page(state_id, runtime_id, enqueue_continuation=False)
            state = CombatRoomState.objects.filter(pk=state_id).values('next_run_ts', 'lease_until').first()
            if not state or state['next_run_ts'] is None or state['lease_until'] is not None:
                break
        else:
            raise ActionError('This room has too much combat admission work before pausing.',
                              code='instance_turn_budget')
    return admitted


def _reconcile_page(state_id, runtime_id, *, enqueue_continuation=True):
    from spawns.instance_clock import gameplay_now, is_time_controlled, in_simulation
    now = gameplay_now(runtime_id)
    with transaction.atomic():
        state = CombatRoomState.objects.select_for_update().filter(pk=state_id).first()
        if state is None or (state.lease_until and state.lease_until > now):
            return 0
        if not state.cursor:
            if state.applied_generation == state.dirty_generation:
                return 0
            state.frozen_generation = state.dirty_generation
            state.frozen_actors = state.changed_actors or ['*']
            state.changed_actors = []
            state.cursor = {'source_kind': 'player', 'source_after': 0,
                            'target_kind': 'player', 'target_after': 0}
        state.lease_until = now + timedelta(seconds=15)
        state.save(update_fields=['frozen_generation', 'frozen_actors', 'changed_actors', 'cursor', 'lease_until'])
        frozen_generation, lease_until = state.frozen_generation, state.lease_until
    cursor = dict(state.cursor)
    full = '*' in state.frozen_actors
    if full:
        sources = _page(state.world_id, state.room_id, cursor['source_kind'],
                        cursor['source_after'], SOURCE_PAGE_SIZE)
    else:
        keys = state.frozen_actors[cursor['source_after']:cursor['source_after'] + SOURCE_PAGE_SIZE]
        sources = _load_keys(keys, state.world_id, state.room_id)
    targets = _page(state.world_id, state.room_id, cursor['target_kind'], cursor['target_after'])
    actors = list({actor_key(a): a for a in [*sources, *targets]}.values())
    policy = CombatPolicy(actors)
    observed = Player.objects.filter(world_id=state.world_id, room_id=state.room_id, in_game=True).exists()
    active_until = now + timedelta(seconds=NPC_ACTIVITY_SECONDS) if observed else state.active_until
    if active_until and active_until > now:
        paused_ids = list(CombatEncounter.objects.filter(world_id=state.world_id, room_id=state.room_id,
                          status=CombatEncounter.STATUS_PAUSED).values_list('pk', flat=True)[:MAX_ADMISSIONS])
        for encounter_id in paused_ids:
            def reactivate(ctx):
                encounter = ctx.encounters[encounter_id]
                if encounter.status != CombatEncounter.STATUS_PAUSED:
                    return
                encounter.status = CombatEncounter.STATUS_ACTIVE
                encounter.npc_active_until = active_until
                encounter.next_resolution_ts = now if encounter.resolution_interval >= 0 else None
                encounter.schedule_generation += 1
                encounter.state_revision += 1
                encounter.save(update_fields=['status', 'npc_active_until', 'next_resolution_ts', 'schedule_generation', 'state_revision'])
                persist_follow_dependent_game_events([snapshot_event(ctx, encounter)], force=True)
                schedule(encounter)
            transact(reactivate, encounter_ids=[encounter_id])
    stale_ids = list(CombatParticipant.objects.filter(encounter__world_id=state.world_id,
        encounter__room_id=state.room_id, is_active=True).filter(
        Q(player__isnull=False) & (~Q(player__room_id=state.room_id) | ~Q(player__world_id=state.world_id) | Q(player__health__lte=0))
        | Q(mob__isnull=False) & (~Q(mob__room_id=state.room_id) | ~Q(mob__world_id=state.world_id)
                                | Q(mob__health__lte=0) | Q(mob__is_pending_deletion=True))
    ).values_list('encounter_id', flat=True).distinct()[:MAX_ADMISSIONS])
    for encounter_id in stale_ids:
        def repair(ctx):
            from spawns.combat_rounds import detach_actor
            encounter = ctx.encounters[encounter_id]
            for member in list(ctx.members(encounter)):
                actor = ctx.actors.get(member.actor_key)
                if not eligible(actor) or (actor.world_id, actor.room_id) != (encounter.world_id, encounter.room_id):
                    detach_actor(ctx, member.actor_key, reason='unavailable')
        transact(repair, encounter_ids=[encounter_id])
    members = {p.actor_key: p for p in CombatParticipant.objects.filter(
        encounter__world_id=state.world_id, encounter__room_id=state.room_id,
        is_active=True,
    ).filter(
        # Only candidates' membership is needed; avoid an uncapped room roster.
        actor_filter([actor_key(a) for a in actors]),
    )}
    pairs = [(a, b) for a in sources for b in targets if actor_key(a) != actor_key(b)]
    offset = int(cursor.get('pair_offset', 0))
    admitted = 0
    while offset < len(pairs) and admitted < MAX_ADMISSIONS and (
        not is_time_controlled(state.world_id) or in_simulation(state.world_id)
    ):
        left, right = pairs[offset]
        offset += 1
        left_member, right_member = members.get(actor_key(left)), members.get(actor_key(right))
        if left_member and right_member and left_member.encounter_id == right_member.encounter_id:
            continue
        for actor, target in ((left, right), (right, left)):
            if admitted >= MAX_ADMISSIONS:
                break
            ally_key = None
            initiates = _initiates(policy, actor, target)
            if not initiates and isinstance(actor, Mob) and actor_key(target) in members:
                assist = actor.definition.combat_assist if actor.definition_id else 'none'
                initiates = (assist != 'none' and policy.automatic_allowed(actor, target)
                             and policy.allied(actor, target)
                             and (assist != 'same_spawn_cohort' or actor.group_id and actor.group_id == target.group_id))
                if initiates:
                    ally_key = actor_key(target)
                    ally = members[ally_key]
                    opponent = CombatParticipant.objects.filter(encounter_id=ally.encounter_id,
                                 is_active=True).exclude(side_id=ally.side_id).select_related('player', 'mob').order_by('pk').first()
                    target = opponent.actor if opponent else None
            if not initiates or target is None:
                continue
            if isinstance(target, Mob) and isinstance(actor, Mob) and not (
                observed or active_until and active_until > now
            ):
                continue
            def admit(ctx):
                a, b = ctx.actors.get(actor_key(actor)), ctx.actors.get(actor_key(target))
                encounter, _, _, changed = engage_locked(ctx, a, b,
                    reason='assist' if ally_key else 'automatic', ally_key=ally_key)
                members.update({p.actor_key: p for p in ctx.members(encounter)})
                if changed:
                    schedule(encounter)
                    events = []
                    if isinstance(b, Player):
                        from spawns.actions.combat import _engage_events, stand_player
                        stand_player(b)
                        events = _engage_events(player=b, room=b.room, mob=a)
                        events[0] = replace(events[0], text=f'{a.name[:1].upper() + a.name[1:]} attacks you!')
                    events.append(snapshot_event(ctx, encounter))
                    persist_follow_dependent_game_events(events, force=True)
                return changed
            try:
                admitted += bool(transact(admit, keys=[actor_key(actor), actor_key(target), *([ally_key] if ally_key else [])]))
            except ActionError as error:
                if error.code not in {'target_invalid', 'target_missing', 'not_attackable', 'engagement_ineligible', 'combat_capacity',
                                      'unsupported_combat_topology', 'pvp_disabled', 'combat_disabled', 'combat_changed'}:
                    raise
    cursor['pair_offset'] = offset
    done = False
    if offset >= len(pairs):
        cursor.pop('pair_offset', None)
        if len(targets) == PAGE_SIZE:
            cursor['target_after'] = targets[-1].pk
        elif cursor['target_kind'] == 'player':
            cursor['target_kind'], cursor['target_after'] = 'mob', 0
        else:
            cursor['target_kind'], cursor['target_after'] = 'player', 0
            if full:
                if len(sources) == SOURCE_PAGE_SIZE:
                    cursor['source_after'] = sources[-1].pk
                elif cursor['source_kind'] == 'player':
                    cursor['source_kind'], cursor['source_after'] = 'mob', 0
                else:
                    done = True
            else:
                cursor['source_after'] += SOURCE_PAGE_SIZE
                done = cursor['source_after'] >= len(state.frozen_actors)
    with transaction.atomic():
        fresh = CombatRoomState.objects.select_for_update().get(pk=state_id)
        if fresh.lease_until != lease_until or fresh.frozen_generation != frozen_generation:
            return admitted
        fresh.cursor = {} if done else cursor
        if done:
            fresh.applied_generation = frozen_generation
        fresh.lease_until = None
        fresh.active_until = active_until
        fresh.next_run_ts = now if not done or fresh.dirty_generation != frozen_generation else None
        fresh.save(update_fields=['cursor', 'applied_generation', 'lease_until', 'active_until', 'next_run_ts'])
        if enqueue_continuation and fresh.next_run_ts and not is_time_controlled(fresh.world_id):
            from spawns.tasks import reconcile_combat_room
            transaction.on_commit(lambda: reconcile_combat_room.delay(state_id), robust=True)
    return admitted
