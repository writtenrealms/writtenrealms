"""Participant fixtures for the existing ability, flee, and reward scenarios.

The helpers keep test setup concise; production admission is exercised by the
formation and command tests rather than being used to arrange every fixture.
"""
from spawns.models import CombatEncounter, CombatParticipant, CombatSide, CombatSideRelation
from django.db.models import Q


def combat_member(encounter, kind):
    cache = getattr(encounter, '_fixture_members', {})
    if kind not in cache:
        cache[kind] = encounter.participants.filter(
            Q(**{kind + '_id__isnull': False}) | Q(actor_snapshot__kind=kind),
        ).order_by('pk').first()
        encounter._fixture_members = cache
    return cache[kind]


def create_combat_encounter(**values):
    from config import constants
    world = values['world']
    if world.lifecycle != constants.WORLD_LIFECYCLE_RUNNING:
        world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        world.save(update_fields=['lifecycle'])
    player = values.pop('player', None)
    mob = values.pop('mob', None)
    pending_player = values.pop('pending_player_ability', {})
    pending_mob = values.pop('pending_mob_ability', {})
    pending_flee = values.pop('pending_flee', {})
    initiative_order = values.pop('initiative_order', [])
    values.pop('faceoff_override', None)
    existing = CombatParticipant.objects.filter(player=player, is_active=True).select_related('encounter').first() if player else None
    encounter = existing.encounter if existing else CombatEncounter.objects.create(**values)
    if not existing:
        sides = [CombatSide.objects.create(encounter=encounter, position=i) for i in (1, 2)]
        CombatSideRelation.objects.create(lower_side=sides[0], higher_side=sides[1])
    else:
        sides = list(encounter.sides.order_by('position'))
    members = []
    for side, kind, actor, pending in [(sides[0], 'player', player, pending_player),
                                       (sides[1], 'mob', mob, pending_mob)]:
        if actor is None:
            continue
        member, created = CombatParticipant.objects.get_or_create(
            encounter=encounter, **{kind: actor}, defaults={
                'side': side, 'team': side.position,
                'is_active': encounter.status != CombatEncounter.STATUS_FINISHED,
                'actor_snapshot': {'key': actor.key, 'kind': kind, 'id': actor.pk, 'name': actor.name},
                'initiative': 1 if kind == 'player' else 0,
                'pending_ability': pending, 'pending_flee': pending_flee if kind == 'player' else {},
                'intent_ready': True,
            },
        )
        members.append(member)
    if len(members) == 2:
        for member, target in [members, members[::-1]]:
            if member.current_target_id is None:
                member.current_target = target
                member.save(update_fields=['current_target'])
    for ref in initiative_order:
        encounter.participants.filter(**{ref['type'] + '_id': ref['id']}).update(initiative=ref.get('initiative', 0))
    return encounter


def save_combat_fixture(encounter, *, update_fields=None, **kwargs):
    mapping = {'pending_player_ability': ('player', 'pending_ability'),
               'pending_mob_ability': ('mob', 'pending_ability'),
               'pending_flee': ('player', 'pending_flee')}
    fields = list(update_fields or [])
    for field in fields:
        if field in mapping:
            kind, name = mapping[field]
            combat_member(encounter, kind).save(update_fields=[name])
    ordinary = [f for f in fields if f not in mapping]
    if ordinary or update_fields is None:
        encounter.save(update_fields=ordinary or None, **kwargs)
        if encounter.status == 'finished':
            encounter.participants.update(is_active=False, current_target=None, pending_ability={}, pending_flee={})


def refresh_combat_fixture(encounter, **kwargs):
    encounter.refresh_from_db(**kwargs)
    encounter._fixture_members = {}


def dispatch_and_drain_combat(player_id, text):
    """Drive a command and its queued immediate rounds as a client would see them."""
    from tests.utils import dispatch_text_command
    from spawns.combat_reconciliation import reconcile
    from spawns.combat_rounds import resolve
    from spawns.events import flush_game_event_outbox
    from spawns.models import CombatRoomState, Player
    from worlds.models import World
    from config import constants

    world_id = Player.objects.values_list('world_id', flat=True).get(pk=player_id)
    World.objects.filter(pk=world_id).update(lifecycle=constants.WORLD_LIFECYCLE_RUNNING)

    existing_ids = set(CombatEncounter.objects.values_list('pk', flat=True))
    from unittest.mock import patch
    from spawns.tasks import resolve_combat_tracker_chase
    with patch('spawns.tasks.resolve_combat_tracker_chase.delay', side_effect=resolve_combat_tracker_chase):
        dispatch_text_command(player_id, text)
        flush_game_event_outbox()
    drain_queued_combat(existing_ids=existing_ids)


def drain_queued_combat(*, existing_ids=()):
    from spawns.combat_reconciliation import reconcile
    from spawns.combat_rounds import resolve
    from spawns.events import flush_game_event_outbox
    from spawns.models import CombatRoomState
    for _ in range(100):
        state = CombatRoomState.objects.filter(next_run_ts__isnull=False).order_by('pk').first()
        if state is None:
            break
        reconcile(state.pk)
    for _ in range(100):
        due = list(CombatEncounter.objects.filter(status='active', resolution_interval=0)
                   .exclude(pk__in=existing_ids).values_list('pk', flat=True))
        if not due:
            break
        for encounter_id in due:
            resolve(encounter_id, auto_advance=True, durable_events=True)
    flush_game_event_outbox()


def participant_initiative_order(encounter):
    return [dict(type=p.actor_key.split('.')[0], id=int(p.actor_key.split('.')[1]), initiative=p.initiative)
            for p in encounter.participants.order_by('-initiative', 'pk')]


def combat_outbox_events():
    from spawns.models import GameEventOutbox
    from spawns.events import GameEvent
    return [GameEvent(row.event_type, row.data, row.recipients, row.text)
            for row in GameEventOutbox.objects.order_by('created_ts', 'batch_id', 'sequence')]
