import uuid

from django.db import transaction
from django.utils import timezone

from config import constants as adv_consts
from worlds.models import (
    InstanceAssignment,
    InstanceParticipant,
    InstanceRun,
    World,
)


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

        if ref:
            run = _active_run_qs().select_for_update().filter(
                ref=ref,
                template_world=template_world,
            ).select_related(
                'spawned_world',
            ).first()
            if not run:
                spawned_world = World.objects.select_for_update().filter(
                    instance_ref=ref,
                    context=template_world,
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


def enter_instance(
        *,
        player,
        transfer_to,
        transfer_from,
        ref=None,
        member_ids=None):
    run = get_or_create_instance_run(
        transfer_to.world,
        player=player,
        transfer_from=transfer_from,
        ref=ref,
        member_ids=member_ids)
    _ensure_spawned_instance_started(run)

    with transaction.atomic():
        player.world = run.spawned_world
        player.room = transfer_to
        player.save(update_fields=['world', 'room'])
        move_player_carried_items_to_world(player, run.spawned_world)

    return run


def leave_instance(*, player):
    if not player.world.context or not player.world.context.instance_of_id:
        raise ValueError("Player is not in an instance.")

    spawned_instance = player.world
    template_world = spawned_instance.context
    base_world = template_world.instance_of

    run = _run_for_spawned_world(spawned_instance, leader=spawned_instance.leader)
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

        if participant and not participant.exited_at:
            participant.exited_at = now
            participant.save(update_fields=['exited_at'])
        run.last_active_at = now
        run.save(update_fields=['last_active_at'])

    return player


def active_participation_count(player):
    return player.instance_participations.filter(
        exited_at__isnull=True,
        run__status__in=InstanceRun.ACTIVE_STATUSES,
    ).count()
