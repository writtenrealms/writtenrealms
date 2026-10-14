from spawns.instance_clock import serialized_world

from django.db.models import Q
from spawns.instance_clock import gameplay_now, in_simulation, is_time_controlled, time_control_run

from worlds.models import (
    Door,
    Zone,
)


@serialized_world(lambda *, world, **kwargs: world)
def run_spawn_plans_for_world(world, zone_id=None, initial=False, repopulate=False, process_doors=True):
    """
    Process WR2 spawn plans for a spawn world and reset doors when a zone is due.
    """

    if not world.context:
        raise TypeError("Can only process spawn plans on spawn worlds.")

    output = {
        'doors': [],
        'spawn_plans': [],
    }
    if not initial and is_time_controlled(world) and not in_simulation(world):
        return output
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

    from spawns.door_upkeep import process_due_door_resets
    if initial or process_doors:
        output['doors'] = process_due_door_resets(world=world, zones=zones, initial=initial)

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

    world.last_spawn_plan_run_ts = gameplay_now(world)
    world.save(update_fields=['last_spawn_plan_run_ts'])

    if initial and time_control_run(world):
        from spawns.merchants import initialize_controlled_room_merchants

        initialize_controlled_room_merchants(world)

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

    if is_time_controlled(world) and not in_simulation(world):
        return {
            'doors': {'requested': bool(reset_doors), 'doorways_checked': 0, 'door_states_reset': 0},
            'spawn_plans': [],
        }

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
    world.last_spawn_plan_run_ts = gameplay_now(world)
    world.save(update_fields=['last_spawn_plan_run_ts'])
    return output
