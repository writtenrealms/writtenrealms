from datetime import timedelta
import logging
from time import monotonic
from spawns.instance_clock import serialized_world

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from spawns.instance_clock import gameplay_now, in_simulation, is_time_controlled, time_control_run

from worlds.models import (
    Door,
    Zone,
    ZoneDoorResetSchedule,
    ZONE_POLICY_MODE_FIXED,
)


DOOR_RESET_SCHEDULE_BATCH_SIZE = 100
logger = logging.getLogger(__name__)


def _batched(values, *, size):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _lock_door_policy_zones(*, zone_ids, authored_world_id):
    # Runtime initialization/upkeep only reads authored policy. Shared locks
    # exclude builder edits without serializing different runs of a template.
    # Runtime schedule rows below still take exclusive locks.
    if connection.vendor == 'postgresql':
        return list(Zone.objects.raw(
            'SELECT * FROM "worlds_zone" WHERE id = ANY(%s) AND world_id = %s '
            'ORDER BY id FOR SHARE',
            [zone_ids, authored_world_id],
        ))
    return list(Zone.objects.select_for_update().filter(
        id__in=zone_ids, world_id=authored_world_id,
    ).order_by('id'))


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
            locked_zones = _lock_door_policy_zones(
                zone_ids=zone_id_batch, authored_world_id=world.context_id,
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
            initialized_at = gameplay_now(world)
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
        locked_zones = _lock_door_policy_zones(
            zone_ids=candidate_zone_ids, authored_world_id=world.context_id,
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
        batch_now = gameplay_now(world)
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


@serialized_world(lambda *, world, **kwargs: world)
def process_due_door_resets(world, zones=None, initial=False, budget=None):
    """Run only doorway policy; population replacement is independent."""
    from config import game_settings
    started = monotonic()
    if not game_settings.DOOR_UPKEEP_ENABLED and not initial:
        return []
    if not initial and (world.config.never_reload or (is_time_controlled(world) and not in_simulation(world))):
        return []
    zones = list(zones) if zones is not None else list(world.context.zones.all())
    output = []
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
    schedule_check_ts = gameplay_now(world)
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
    candidate_count = len(candidate_zone_ids)
    if budget is not None and not initial:
        def priority(zone_id):
            schedule = door_schedules_by_zone_id.get(zone_id)
            zone = zone_by_id[zone_id]
            if schedule is None or schedule.next_reset_ts is None or schedule.policy_version != zone.door_reset_policy_version:
                return (0, schedule_check_ts, zone_id)
            return (1, schedule.next_reset_ts, zone_id)
        zone_by_id = {zone.pk: zone for zone in zones}
        candidate_zone_ids = sorted(candidate_zone_ids, key=priority)[:max(1, min(int(budget), 1000))]
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
        output.append({
            'room_id': door.from_room.id,
            'room_key': door.from_room.get_game_key(
                spawn_world=world),
            'state': door.default_state,
            'direction': door.direction,
            'name': door.name,
        })

    logger.info('Door upkeep world=%s candidates=%s processed_zones=%s doors=%s duration_ms=%s budget_exhausted=%s',
                world.pk, candidate_count, len(candidate_zone_ids), len(output),
                round((monotonic()-started)*1000), candidate_count > len(candidate_zone_ids))
    return output
