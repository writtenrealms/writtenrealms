from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from worlds.models import (
    Door,
    Zone,
    ZoneDoorResetSchedule,
    ZONE_POLICY_MODE_FIXED,
)


DOOR_RESET_SCHEDULE_BATCH_SIZE = 100


def _batched(values, *, size):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _initialize_door_reset_schedules(*, world, zones):
    """Batch-initialize one runtime world's fixed zone schedules."""

    zone_ids = list(dict.fromkeys(zone.id for zone in zones))
    if not zone_ids:
        return []

    locked_by_id = {}
    for zone_id_batch in _batched(
        sorted(zone_ids),
        size=DOOR_RESET_SCHEDULE_BATCH_SIZE,
    ):
        with transaction.atomic():
            locked_zones = list(
                Zone.objects.select_for_update()
                .filter(
                    id__in=zone_id_batch,
                    world_id=world.context_id,
                )
                .order_by("id")
            )
            locked_by_id.update({zone.id: zone for zone in locked_zones})
            fixed_zones = [
                zone
                for zone in locked_zones
                if zone.door_reset_mode == ZONE_POLICY_MODE_FIXED
            ]
            if not fixed_zones:
                continue

            fixed_zone_ids = [zone.id for zone in fixed_zones]
            existing_schedules = list(
                ZoneDoorResetSchedule.objects.select_for_update()
                .filter(
                    world_id=world.id,
                    zone_id__in=fixed_zone_ids,
                )
                .order_by("zone_id")
            )
            schedules_by_zone_id = {
                schedule.zone_id: schedule
                for schedule in existing_schedules
            }
            # Start this chunk's interval only after its rows are locked. A
            # queued world start must not commit an already-due short policy.
            initialized_at = timezone.now()
            missing_schedules = []
            changed_schedules = []
            for zone in fixed_zones:
                next_reset_ts = initialized_at + timedelta(
                    seconds=zone.door_reset_seconds,
                )
                schedule = schedules_by_zone_id.get(zone.id)
                if schedule is None:
                    missing_schedules.append(
                        ZoneDoorResetSchedule(
                            world=world,
                            zone=zone,
                            next_reset_ts=next_reset_ts,
                            policy_version=zone.door_reset_policy_version,
                        )
                    )
                    continue
                schedule.next_reset_ts = next_reset_ts
                schedule.policy_version = zone.door_reset_policy_version
                schedule.modified_ts = initialized_at
                changed_schedules.append(schedule)

            if missing_schedules:
                ZoneDoorResetSchedule.objects.bulk_create(missing_schedules)
            if changed_schedules:
                ZoneDoorResetSchedule.objects.bulk_update(
                    changed_schedules,
                    ["next_reset_ts", "policy_version", "modified_ts"],
                )
    return [
        locked_by_id[zone_id]
        for zone_id in zone_ids
        if zone_id in locked_by_id
    ]


def _process_door_reset_schedule_batch(
    *,
    world,
    candidate_zone_ids,
    reset_doorway_ids,
):
    """Lock and reconcile one bounded batch of runtime door schedules."""

    with transaction.atomic():
        # Keep the global lock order consistent with canonical policy edits
        # and initial schedule creation: authored Zone, runtime schedule, then
        # runtime DoorState inside reset_runtime_doorways().
        locked_zones = list(
            Zone.objects.select_for_update()
            .filter(
                id__in=candidate_zone_ids,
                world_id=world.context_id,
            )
            .order_by("id")
        )
        fixed_zones = [
            zone
            for zone in locked_zones
            if zone.door_reset_mode == ZONE_POLICY_MODE_FIXED
        ]
        if not fixed_zones:
            return []

        fixed_zone_ids = [zone.id for zone in fixed_zones]
        schedules = list(
            ZoneDoorResetSchedule.objects.select_for_update()
            .filter(
                world_id=world.id,
                zone_id__in=fixed_zone_ids,
            )
            .order_by("zone_id")
        )
        schedules_by_zone_id = {
            schedule.zone_id: schedule
            for schedule in schedules
        }
        batch_now = timezone.now()
        missing_schedules = []
        changed_schedules = []
        due_zone_ids = []
        for zone in fixed_zones:
            schedule = schedules_by_zone_id.get(zone.id)
            if schedule is None:
                missing_schedules.append(
                    ZoneDoorResetSchedule(
                        world=world,
                        zone=zone,
                        next_reset_ts=(
                            batch_now
                            + timedelta(seconds=zone.door_reset_seconds)
                        ),
                        policy_version=zone.door_reset_policy_version,
                    )
                )
                continue

            policy_version_mismatch = (
                schedule.policy_version
                != zone.door_reset_policy_version
            )
            should_reset_doors = (
                not policy_version_mismatch
                and schedule.next_reset_ts is not None
                and schedule.next_reset_ts <= batch_now
            )
            if (
                policy_version_mismatch
                or schedule.next_reset_ts is None
                or should_reset_doors
            ):
                schedule.next_reset_ts = (
                    batch_now
                    + timedelta(seconds=zone.door_reset_seconds)
                )
                schedule.policy_version = zone.door_reset_policy_version
                schedule.modified_ts = batch_now
                changed_schedules.append(schedule)
            if should_reset_doors:
                due_zone_ids.append(zone.id)

        if missing_schedules:
            # Normal periodic invocations are serialized per runtime world.
            # Ignore conflicts defensively so an exceptional concurrent
            # caller cannot abort the entire batch after winning the unique
            # (world, zone) insert race.
            ZoneDoorResetSchedule.objects.bulk_create(
                missing_schedules,
                ignore_conflicts=True,
            )
        if changed_schedules:
            ZoneDoorResetSchedule.objects.bulk_update(
                changed_schedules,
                ["next_reset_ts", "policy_version", "modified_ts"],
            )

        if not due_zone_ids:
            return []

        # A doorway belongs to both endpoint zones even when only one authored
        # face exists. Fetch all faces for this due batch once, then choose one
        # deterministic representative per doorway for reset output.
        door_rows_qs = (
            Door.objects.filter(
                Q(from_room__zone_id__in=due_zone_ids)
                | Q(to_room__zone_id__in=due_zone_ids),
                doorway__world_id=world.context_id,
            )
            .select_related("doorway", "from_room")
            .order_by("id")
        )
        door_by_doorway_id = {}
        for door in door_rows_qs:
            if door.doorway_id in reset_doorway_ids:
                continue
            door_by_doorway_id.setdefault(door.doorway_id, door)
        door_rows = list(door_by_doorway_id.values())
        doorway_ids = sorted(door_by_doorway_id)
        if doorway_ids:
            from spawns.actions.doors import reset_runtime_doorways

            reset_runtime_doorways(
                runtime_world=world,
                doorway_ids=doorway_ids,
            )
            reset_doorway_ids.update(doorway_ids)
        return door_rows


def run_spawn_plans_for_world(world, zone_id=None, initial=False, repopulate=False):
    """
    Process WR2 spawn plans for a spawn world and reset doors when a zone is due.
    """

    if not world.context:
        raise TypeError("Can only process spawn plans on spawn worlds.")

    output = {
        'doors': [],
        'spawn_plans': [],
    }
    from spawns.spawn_plans import SpawnReconcileContext, run_spawn_plans

    reconcile_context = SpawnReconcileContext(
        authored_world_id=world.context_id,
        spawn_world_id=world.id,
        zone_id=zone_id,
    )

    if zone_id:
        zone_qs = Zone.objects.filter(pk=zone_id)
        if world.context_id:
            zone_qs = zone_qs.filter(world_id=world.context_id)
        zones = [zone_qs.get()]
    else:
        zones = list(world.context.zones.all())

    if initial:
        zones = _initialize_door_reset_schedules(
            world=world,
            zones=zones,
        )

    # One batched read handles the common non-initial case where every
    # deadline remains in the future. Only missing, uninitialized, or due
    # rows enter the locked path below.
    fixed_zone_ids = [] if initial else [
        zone.id
        for zone in zones
        if zone.door_reset_mode == ZONE_POLICY_MODE_FIXED
    ]
    door_schedules_by_zone_id = {}
    if fixed_zone_ids:
        door_schedules_by_zone_id = {
            schedule.zone_id: schedule
            for schedule in ZoneDoorResetSchedule.objects.filter(
                world_id=world.id,
                zone_id__in=fixed_zone_ids,
            ).only(
                "id",
                "zone_id",
                "next_reset_ts",
                "policy_version",
            )
        }
    schedule_check_ts = timezone.now()
    reset_doorway_ids = set()
    candidate_zone_ids = sorted(
        zone.id
        for zone in zones
        if (
            not initial
            and zone.door_reset_mode == ZONE_POLICY_MODE_FIXED
            and (
                door_schedules_by_zone_id.get(zone.id) is None
                or door_schedules_by_zone_id[zone.id].next_reset_ts is None
                or (
                    door_schedules_by_zone_id[zone.id].next_reset_ts
                    <= schedule_check_ts
                )
                or (
                    door_schedules_by_zone_id[zone.id].policy_version
                    != zone.door_reset_policy_version
                )
            )
        )
    )
    door_rows = []
    for candidate_batch in _batched(
        candidate_zone_ids,
        size=DOOR_RESET_SCHEDULE_BATCH_SIZE,
    ):
        door_rows.extend(
            _process_door_reset_schedule_batch(
                world=world,
                candidate_zone_ids=candidate_batch,
                reset_doorway_ids=reset_doorway_ids,
            )
        )

    for door in door_rows:
        output['doors'].append({
            'room_id': door.from_room.id,
            'room_key': door.from_room.get_game_key(
                spawn_world=world),
            'state': door.default_state,
            'direction': door.direction,
            'name': door.name,
        })

    # Go through each zone and run spawn plans if appropriate.
    for zone in zones:
        output['spawn_plans'].extend(
            run_spawn_plans(
                world=world,
                zone_id=zone.id,
                initial=initial,
                repopulate=repopulate,
                reconcile_context=reconcile_context,
            )
        )

    world.last_spawn_plan_run_ts = timezone.now()
    world.save(update_fields=['last_spawn_plan_run_ts'])

    return output


def repopulate_spawn_plans_for_zone(
    *,
    world,
    zone_id,
    reset_doors=False,
):
    """
    Force spawn plans in one runtime zone and optionally reset its doorways.

    Unlike the periodic world lifecycle runner, this deliberate builder/script
    operation never consumes the authored zone's door-reset timer. Runtime
    doorway states are reset only when explicitly requested.
    """

    if not world.context:
        raise TypeError("Can only repopulate spawn plans on spawn worlds.")

    zone = Zone.objects.filter(
        pk=zone_id,
        world_id=world.context_id,
    ).get()

    from spawns.spawn_plans import SpawnReconcileContext, run_spawn_plans

    door_result = {
        'requested': bool(reset_doors),
        'doorways_checked': 0,
        'door_states_reset': 0,
    }
    if reset_doors:
        doorway_ids = list(
            Door.objects.filter(
                Q(from_room__zone_id=zone.id)
                | Q(to_room__zone_id=zone.id),
                doorway__world_id=world.context_id,
            )
            .order_by('doorway_id')
            .values_list('doorway_id', flat=True)
            .distinct()
        )
        from spawns.actions.doors import reset_runtime_doorways

        door_result['doorways_checked'] = len(doorway_ids)
        door_result['door_states_reset'] = reset_runtime_doorways(
            runtime_world=world,
            doorway_ids=doorway_ids,
        )

    output = {
        'doors': door_result,
        'spawn_plans': run_spawn_plans(
            world=world,
            zone_id=zone.id,
            repopulate=True,
            reconcile_context=SpawnReconcileContext(
                authored_world_id=world.context_id,
                spawn_world_id=world.id,
                zone_id=zone.id,
            ),
        ),
    }
    world.last_spawn_plan_run_ts = timezone.now()
    world.save(update_fields=['last_spawn_plan_run_ts'])
    return output
