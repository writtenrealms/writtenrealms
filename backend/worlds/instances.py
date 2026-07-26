import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from config import constants as adv_consts
from worlds.models import (
    InstanceAssignment,
    InstanceParticipant,
    InstanceRun,
    World,
)


@dataclass(frozen=True)
class InstanceResetResult:
    run_id: int
    instance_ref: str
    spawned_world_id: int
    player_ids: list[int]
    mobs_deleted: int
    items_deleted: int
    combat_encounters_deleted: int
    spawn_plan_runs_reset: int
    runtime_scoped_state_reset: bool


def _normalize_member_ids(member_ids):
    if not member_ids:
        return []
    normalized = []
    for member_id in member_ids:
        if member_id in (None, ''):
            continue
        normalized.append(int(member_id))
    return normalized


def _assignment_member_ids(member_ids):
    return ' '.join(str(member_id) for member_id in _normalize_member_ids(member_ids))


def _active_run_qs():
    return InstanceRun.objects.filter(status__in=InstanceRun.ACTIVE_STATUSES)


def _is_match_instance_template(template_world):
    config = getattr(template_world, 'config', None)
    return (
        config is not None
        and config.pvp_mode == adv_consts.PVP_MODE_MATCH
    )


def _template_has_active_match_run(template_world):
    from spawns.models import DuelMatch

    return DuelMatch.objects.filter(
        template_world_id=template_world.id,
        status=DuelMatch.STATUS_ACTIVE,
        run__status__in=InstanceRun.ACTIVE_STATUSES,
    ).exists()


def _assert_match_run_entry_allowed(*, run, player, template_world=None):
    """
    Match instance refs identify a run; they are not bearer admission tokens.

    Keep this check in the instance service so command, API, and async entry
    paths all enforce the same contestant-only policy.
    """
    from spawns.models import DuelMatch, DuelParticipant

    template_world = template_world or run.template_world
    match = DuelMatch.objects.filter(run=run).only("id", "status").first()
    if match is None:
        if not _is_match_instance_template(template_world):
            return
        raise RuntimeError(
            "This match instance is private to its active contestants."
        )

    admitted = (
        match.status == DuelMatch.STATUS_ACTIVE
        and DuelParticipant.objects.filter(
            match=match,
            player_id=player.id,
            role=DuelParticipant.ROLE_CONTESTANT,
        ).exists()
    )
    if not admitted:
        raise RuntimeError(
            "This match instance is private to its active contestants."
        )


def _assert_match_template_requires_ref(template_world, *, ref):
    if ref:
        return
    if (
        _is_match_instance_template(template_world)
        or _template_has_active_match_run(template_world)
    ):
        raise RuntimeError(
            "Match instances can only be entered through an accepted duel."
        )


def _assert_instance_template(template_world):
    if template_world.context:
        raise TypeError("Cannot create an instance of a spawn world.")
    if not template_world.instance_of_id:
        raise TypeError("Cannot create an instance of a base world.")


def _run_for_spawned_world(spawned_world, *, leader=None, member_ids=None):
    try:
        return spawned_world.instance_run
    except InstanceRun.DoesNotExist:
        pass

    template_world = spawned_world.context
    if not template_world or not template_world.instance_of_id:
        raise ValueError("Spawned world is not an instance run.")

    now = timezone.now()
    ref = spawned_world.instance_ref or uuid.uuid4().hex
    if not spawned_world.instance_ref:
        spawned_world.instance_ref = ref
        spawned_world.save(update_fields=['instance_ref'])

    return InstanceRun.objects.create(
        base_world=template_world.instance_of,
        template_world=template_world,
        spawned_world=spawned_world,
        ref=ref,
        leader=leader or spawned_world.leader,
        status=InstanceRun.STATUS_ACTIVE,
        started_at=now,
        last_active_at=now,
        seed=ref,
        initial_member_ids=_normalize_member_ids(member_ids),
    )


def _create_run(template_world, *, leader, member_ids=None, **spawn_kwargs):
    ref = uuid.uuid4().hex
    spawned_world = template_world.create_spawn_world(
        instance_ref=ref,
        leader=leader,
        **spawn_kwargs)
    now = timezone.now()
    return InstanceRun.objects.create(
        base_world=template_world.instance_of,
        template_world=template_world,
        spawned_world=spawned_world,
        ref=ref,
        leader=leader,
        status=InstanceRun.STATUS_ACTIVE,
        started_at=now,
        last_active_at=now,
        seed=ref,
        initial_member_ids=_normalize_member_ids(member_ids),
    )


def create_fresh_instance_run(
        template_world,
        *,
        leader,
        member_ids=None,
        **spawn_kwargs):
    """
    Create a new run without consulting or reusing any leader-owned run.

    Match acceptance uses this path because every accepted challenge must have
    a distinct runtime world, even when the same leader just completed another
    match against the same opponent.
    """
    _assert_instance_template(template_world)
    with transaction.atomic():
        template_world = World.objects.select_related(
            'config',
            'instance_of',
        ).get(pk=template_world.pk)
        return _create_run(
            template_world,
            leader=leader,
            member_ids=member_ids,
            **spawn_kwargs,
        )


def _upsert_assignment(*, run, player, transfer_from, member_ids=None):
    assignment, created = InstanceAssignment.objects.get_or_create(
        instance=run.spawned_world,
        player=player,
        defaults={
            'transfer_from': transfer_from,
            'member_ids': _assignment_member_ids(member_ids),
        })
    update_fields = []
    if transfer_from and assignment.transfer_from_id != transfer_from.id:
        assignment.transfer_from = transfer_from
        update_fields.append('transfer_from')
    if member_ids and not assignment.member_ids:
        assignment.member_ids = _assignment_member_ids(member_ids)
        update_fields.append('member_ids')
    if update_fields:
        assignment.save(update_fields=update_fields)
    return assignment


def _participant_role(run, player):
    if run.leader_id == player.id:
        return InstanceParticipant.ROLE_LEADER
    return InstanceParticipant.ROLE_MEMBER


def _upsert_participant(*, run, player, transfer_from):
    now = timezone.now()
    participant, created = InstanceParticipant.objects.get_or_create(
        run=run,
        player=player,
        defaults={
            'role': _participant_role(run, player),
            'transfer_from': transfer_from,
            'joined_at': now,
        })
    update_fields = []
    role = _participant_role(run, player)
    if participant.role != role:
        participant.role = role
        update_fields.append('role')
    if transfer_from and participant.transfer_from_id != transfer_from.id:
        participant.transfer_from = transfer_from
        update_fields.append('transfer_from')
    if participant.exited_at:
        participant.exited_at = None
        participant.joined_at = now
        update_fields.extend(['exited_at', 'joined_at'])
    if update_fields:
        participant.save(update_fields=sorted(set(update_fields)))
    return participant


def _mark_other_active_participations_exited(*, run, player):
    now = timezone.now()
    InstanceParticipant.objects.filter(
        player=player,
        exited_at__isnull=True,
        run__status__in=InstanceRun.ACTIVE_STATUSES,
    ).exclude(run=run).update(exited_at=now)


def _ensure_spawned_instance_started(run):
    spawned_world = run.spawned_world
    if spawned_world.lifecycle == adv_consts.WORLD_LIFECYCLE_RUNNING:
        return
    if spawned_world.lifecycle not in (
        adv_consts.WORLD_LIFECYCLE_NEW,
        adv_consts.WORLD_LIFECYCLE_STOPPED,
    ):
        return

    from worlds.services import WorldSmith

    WorldSmith(spawned_world).start()
    run.spawned_world.refresh_from_db()


def enter_players_into_run(
        run,
        *,
        players_and_transfer_rooms: Iterable[tuple],
        entry_room):
    """
    Atomically admit and move a bounded group into an already-created run.

    ``players_and_transfer_rooms`` is an iterable of ``(player,
    transfer_from_room)`` pairs. The caller owns match/challenge validation;
    this helper owns the shared instance bookkeeping and movement transaction.
    """
    player_pairs = list(players_and_transfer_rooms)
    if not player_pairs:
        raise ValueError("At least one player is required.")

    player_ids = [player.id for player, _transfer_from in player_pairs]
    if any(player_id is None for player_id in player_ids):
        raise ValueError("Players must be saved before entering an instance.")
    if len(player_ids) != len(set(player_ids)):
        raise ValueError("A player cannot enter the same run twice.")
    if not entry_room or entry_room.world_id != run.template_world_id:
        raise ValueError("Entry room does not belong to the instance template.")
    if any(
        transfer_from
        and transfer_from.world_id != run.base_world_id
        for _player, transfer_from in player_pairs
    ):
        raise ValueError(
            "Transfer room does not belong to the instance's base world."
        )

    transfer_rooms_by_player_id = {
        player.id: transfer_from
        for player, transfer_from in player_pairs
    }

    _ensure_spawned_instance_started(run)

    from spawns.models import Player

    with transaction.atomic():
        run = InstanceRun.objects.select_for_update().select_related(
            'base_world',
            'spawned_world',
            'template_world',
        ).get(pk=run.pk)
        if run.status not in InstanceRun.ACTIVE_STATUSES:
            raise RuntimeError("This instance run is no longer active.")

        players = list(
            Player.objects.select_for_update(of=('self',))
            .select_related('world', 'world__context')
            .filter(pk__in=player_ids)
            .order_by('pk')
        )
        if len(players) != len(player_ids):
            raise ValueError("One or more players no longer exist.")

        for player in players:
            in_base_world = player.world.context_id == run.base_world_id
            already_in_run = player.world_id == run.spawned_world_id
            if not in_base_world and not already_in_run:
                raise RuntimeError(
                    "A player is no longer in the instance's base world."
                )
            transfer_from = transfer_rooms_by_player_id[player.id]
            if (
                not already_in_run
                and transfer_from
                and player.room_id != transfer_from.id
            ):
                raise RuntimeError(
                    "A player moved away before instance entry completed."
                )

        now = timezone.now()
        for player in players:
            transfer_from = transfer_rooms_by_player_id[player.id]
            _upsert_assignment(
                run=run,
                player=player,
                transfer_from=transfer_from,
            )
            _mark_other_active_participations_exited(run=run, player=player)
            _upsert_participant(
                run=run,
                player=player,
                transfer_from=transfer_from,
            )

            player.world = run.spawned_world
            player.room = entry_room
            player.save(update_fields=['world', 'room'])
            move_player_carried_items_to_world(player, run.spawned_world)
            move_player_character_effects_to_world(
                player,
                run.spawned_world,
            )

        run.last_active_at = now
        run.save(update_fields=['last_active_at'])

    return run


def get_or_create_instance_run(
        template_world,
        *,
        player,
        transfer_from=None,
        ref=None,
        member_ids=None,
        **spawn_kwargs):
    _assert_instance_template(template_world)

    with transaction.atomic():
        template_world = World.objects.select_for_update().get(pk=template_world.pk)
        _assert_match_template_requires_ref(template_world, ref=ref)

        if ref:
            run = _active_run_qs().select_for_update().filter(
                ref=ref,
                template_world=template_world,
            ).select_related(
                'spawned_world',
            ).first()
            if not run:
                if _is_match_instance_template(template_world):
                    raise RuntimeError("Invalid or completed match reference.")
                spawned_world = World.objects.select_for_update().filter(
                    instance_ref=ref,
                    context=template_world,
                ).exclude(
                    pk__in=InstanceRun.objects.values('spawned_world_id'),
                ).first()
                if not spawned_world:
                    raise RuntimeError("Invalid instance reference %s" % ref)
                run = _run_for_spawned_world(
                    spawned_world,
                    leader=spawned_world.leader or player,
                    member_ids=member_ids)
        else:
            run = _active_run_qs().select_for_update().filter(
                template_world=template_world,
                leader=player,
            ).select_related(
                'spawned_world',
            ).first()
            if not run:
                spawned_world = template_world.spawned_worlds.select_for_update().filter(
                    leader=player,
                ).exclude(
                    pk__in=InstanceRun.objects.values('spawned_world_id'),
                ).first()
                if spawned_world:
                    run = _run_for_spawned_world(
                        spawned_world,
                        leader=player,
                        member_ids=member_ids)
                else:
                    run = _create_run(
                        template_world,
                        leader=player,
                        member_ids=member_ids,
                        **spawn_kwargs)

        _assert_match_run_entry_allowed(
            run=run,
            player=player,
            template_world=template_world,
        )

        member_id_list = _normalize_member_ids(member_ids)
        if member_id_list and not run.initial_member_ids:
            run.initial_member_ids = member_id_list
            run.save(update_fields=['initial_member_ids'])

        _upsert_assignment(
            run=run,
            player=player,
            transfer_from=transfer_from,
            member_ids=member_id_list)
        _mark_other_active_participations_exited(run=run, player=player)
        _upsert_participant(
            run=run,
            player=player,
            transfer_from=transfer_from)

        run.last_active_at = timezone.now()
        run.save(update_fields=['last_active_at'])

    return run


def _equipment_item_ids(player):
    if not player.equipment_id:
        return set()

    ids = set(player.equipment.inventory.values_list('id', flat=True))
    for slot in adv_consts.EQUIPMENT_SLOTS:
        item = getattr(player.equipment, slot, None)
        if item:
            ids.add(item.id)
    return ids


def player_carried_item_ids(player):
    from spawns.models import Item

    top_level_ids = set(player.inventory.values_list('id', flat=True))
    top_level_ids.update(_equipment_item_ids(player))

    all_ids = set(top_level_ids)
    for item in Item.objects.filter(id__in=top_level_ids):
        all_ids.update(item.get_contained_ids())
    return all_ids


def move_player_carried_items_to_world(player, world):
    from spawns.models import Item

    item_ids = player_carried_item_ids(player)
    if item_ids:
        Item.objects.filter(id__in=item_ids).update(world=world)
    return item_ids


def move_player_character_effects_to_world(player, world):
    """Keep character-scoped effects aligned with their target's runtime."""
    from spawns.models import ActiveEffect

    ActiveEffect.objects.filter(
        target_player=player,
        scope=ActiveEffect.SCOPE_CHARACTER,
    ).exclude(world=world).update(world=world)


def enter_instance(
        *,
        player,
        transfer_to,
        transfer_from,
        ref=None,
        member_ids=None):
    entry_origin = (
        player.__class__.objects.filter(pk=player.pk)
        .values(
            'world_id',
            'world__context_id',
            'room_id',
            'room__world_id',
        )
        .first()
    )
    if entry_origin is None:
        raise ValueError("Player no longer exists.")

    template_world = transfer_to.world
    base_world_id = template_world.instance_of_id
    if not transfer_from or transfer_from.world_id != base_world_id:
        raise ValueError(
            "Transfer room does not belong to the instance's base world."
        )
    origin_is_base_entrance = (
        entry_origin['world__context_id'] == base_world_id
        and entry_origin['room_id'] == transfer_from.id
    )
    origin_is_same_template_run = (
        entry_origin['world__context_id'] == template_world.id
        and entry_origin['room__world_id'] == template_world.id
    )
    if not origin_is_base_entrance and not origin_is_same_template_run:
        raise RuntimeError(
            "A player is no longer at a valid instance entrance."
        )

    run = get_or_create_instance_run(
        template_world,
        player=player,
        transfer_from=transfer_from,
        ref=ref,
        member_ids=member_ids)
    if transfer_from.world_id != run.base_world_id:
        raise ValueError(
            "Transfer room does not belong to the instance's base world."
        )
    _ensure_spawned_instance_started(run)

    with transaction.atomic():
        run = (
            InstanceRun.objects.select_for_update()
            .select_related(
                'spawned_world',
                'template_world',
            )
            .get(pk=run.pk)
        )
        if run.status not in InstanceRun.ACTIVE_STATUSES:
            raise RuntimeError("This instance run is no longer active.")
        _assert_match_run_entry_allowed(
            run=run,
            player=player,
            template_world=run.template_world,
        )
        locked_player = player.__class__.objects.select_for_update().get(
            pk=player.pk
        )
        if locked_player.world_id != entry_origin['world_id']:
            raise RuntimeError(
                "A player changed worlds before instance entry completed."
            )
        if locked_player.room_id != entry_origin['room_id']:
            raise RuntimeError(
                "A player moved away before instance entry completed."
            )
        locked_player.world = run.spawned_world
        locked_player.room = transfer_to
        locked_player.save(update_fields=['world', 'room'])
        move_player_carried_items_to_world(locked_player, run.spawned_world)
        move_player_character_effects_to_world(
            locked_player,
            run.spawned_world,
        )
        # Preserve the existing service contract for callers that reuse the
        # passed model instance immediately after entry.
        player.world = run.spawned_world
        player.room = transfer_to

    return run


def leave_instance(*, player, force_active_duel=False):
    if not player.world.context or not player.world.context.instance_of_id:
        raise ValueError("Player is not in an instance.")

    spawned_instance = player.world
    template_world = spawned_instance.context
    base_world = template_world.instance_of

    run = _run_for_spawned_world(spawned_instance, leader=spawned_instance.leader)
    from spawns.models import DuelMatch, DuelParticipant

    has_active_duel = DuelMatch.objects.filter(
        run=run,
        status=DuelMatch.STATUS_ACTIVE,
        participants__player=player,
        participants__role=DuelParticipant.ROLE_CONTESTANT,
    ).exists()
    if has_active_duel:
        if not force_active_duel:
            raise ValueError(
                "Use `duel surrender` before leaving an active duel."
            )
        from spawns.duels import abandon_duel_run

        abandon_duel_run(run)
    base_spawn_world = base_world.spawned_worlds.filter(
        is_multiplayer=True
    ).get()

    room = None
    participant = run.participants.filter(player=player).first()
    if participant and participant.transfer_from_id:
        room = participant.transfer_from
    if not room:
        assignment = InstanceAssignment.objects.filter(
            player=player,
            instance=spawned_instance,
        ).select_related('transfer_from').first()
        if assignment and assignment.transfer_from_id:
            room = assignment.transfer_from
    if not room:
        room = base_world.config.starting_room

    now = timezone.now()
    with transaction.atomic():
        player.world = base_spawn_world
        player.room = room
        player.save(update_fields=['world', 'room'])
        move_player_carried_items_to_world(player, base_spawn_world)
        move_player_character_effects_to_world(player, base_spawn_world)

        if participant and not participant.exited_at:
            participant.exited_at = now
            participant.save(update_fields=['exited_at'])
        DuelParticipant.objects.filter(
            match__run=run,
            player=player,
            exited_at__isnull=True,
        ).update(exited_at=now)
        run.last_active_at = now
        run.save(update_fields=['last_active_at'])

    return player


def _assert_spawned_instance(spawned_world):
    if not spawned_world.context or not spawned_world.context.instance_of_id:
        raise ValueError("You are not in an instance.")


def _active_participant_players(run):
    from spawns.models import Player

    participant_player_ids = run.participants.filter(
        exited_at__isnull=True,
    ).values_list('player_id', flat=True)
    return list(
        Player.objects.select_for_update()
        .filter(
            pk__in=participant_player_ids,
            world=run.spawned_world,
        )
        .order_by('id')
    )


def _instance_starting_room(spawned_world):
    config = getattr(spawned_world, 'config', None)
    if config and config.starting_room_id:
        return config.starting_room
    template_world = spawned_world.context
    if template_world and template_world.config and template_world.config.starting_room_id:
        return template_world.config.starting_room
    raise ValueError("This instance does not have a starting room.")


def _protected_player_item_ids(players):
    item_ids = set()
    for player in players:
        item_ids.update(player_carried_item_ids(player))
    return item_ids


def _reset_spawn_plan_runs(spawned_world):
    from builders.models import SpawnPlanRun

    return SpawnPlanRun.objects.filter(
        spawn_world=spawned_world,
        status=SpawnPlanRun.STATUS_ACTIVE,
    ).update(
        status=SpawnPlanRun.STATUS_RESET,
        reset_at=timezone.now(),
    )


def reset_instance(*, player) -> InstanceResetResult:
    """
    Rebuild the player's active spawned instance world in place.

    The run/ref and active participants remain, while transient instance
    population, combat, door overrides, and runtime state are reset before
    initial spawn plans run again.
    """
    from core.scoped_state import reset_runtime_state
    from spawns.loading import run_spawn_plans_for_world
    from spawns.models import (
        CombatEncounter,
        DoorState,
        Item,
        Mob,
    )
    with transaction.atomic():
        player = player.__class__.objects.select_for_update().get(pk=player.pk)
        spawned_world = World.objects.select_for_update().get(pk=player.world_id)
        _assert_spawned_instance(spawned_world)

        try:
            run = InstanceRun.objects.select_for_update().select_related(
                'spawned_world',
                'template_world',
                'base_world',
            ).get(spawned_world=spawned_world)
        except InstanceRun.DoesNotExist:
            run = _run_for_spawned_world(
                spawned_world,
                leader=spawned_world.leader or player,
            )
            run = InstanceRun.objects.select_for_update().get(pk=run.pk)

        starting_room = _instance_starting_room(spawned_world)
        active_players = _active_participant_players(run)
        if all(active_player.id != player.id for active_player in active_players):
            active_players.append(player)
        protected_item_ids = _protected_player_item_ids(active_players)

        combat_encounters_qs = CombatEncounter.objects.filter(world=spawned_world)
        combat_encounters_deleted = combat_encounters_qs.count()
        combat_encounters_qs.delete()

        items_qs = Item.objects.filter(world=spawned_world)
        if protected_item_ids:
            items_qs = items_qs.exclude(pk__in=protected_item_ids)
        items_deleted = items_qs.count()
        items_qs.delete()

        mobs_qs = Mob.objects.filter(world=spawned_world)
        mobs_deleted = mobs_qs.count()
        mobs_qs.delete()

        DoorState.objects.filter(world=spawned_world).delete()
        reset_runtime_state(spawned_world)

        spawn_plan_runs_reset = _reset_spawn_plan_runs(spawned_world)

        player_ids = [active_player.id for active_player in active_players]
        if player_ids:
            player.__class__.objects.filter(
                pk__in=player_ids,
            ).update(room=starting_room)

        run.progress = {}
        run.outcome = {}
        run.last_active_at = timezone.now()
        run.save(update_fields=['progress', 'outcome', 'last_active_at', 'modified_ts'])

        spawned_world.is_clean = True
        spawned_world.last_spawn_plan_run_ts = None
        spawned_world.save(update_fields=['is_clean', 'last_spawn_plan_run_ts'])

        run_spawn_plans_for_world(world=spawned_world, initial=True)

    return InstanceResetResult(
        run_id=run.id,
        instance_ref=run.ref,
        spawned_world_id=spawned_world.id,
        player_ids=player_ids,
        mobs_deleted=mobs_deleted,
        items_deleted=items_deleted,
        combat_encounters_deleted=combat_encounters_deleted,
        spawn_plan_runs_reset=spawn_plan_runs_reset,
        runtime_scoped_state_reset=True,
    )


def active_participation_count(player):
    return player.instance_participations.filter(
        exited_at__isnull=True,
        run__status__in=InstanceRun.ACTIVE_STATUSES,
    ).count()
