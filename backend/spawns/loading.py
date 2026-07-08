from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from worlds.models import Zone, Door


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

    if zone_id:
        zone_qs = Zone.objects.filter(pk=zone_id)
        if world.context_id:
            zone_qs = zone_qs.filter(world_id=world.context_id)
        zones = [zone_qs.get()]
    else:
        zones = world.context.zones.all()

    # Go through each zone and run spawn plans if appropriate.
    for zone in zones:

        with transaction.atomic():
            zone = Zone.objects.select_for_update().get(pk=zone.pk)

            # Determine if the zone is due for a reset
            should_zone_reset = False
            if not zone.last_respawn_ts:
                should_zone_reset = True
            else:
                threshold = (
                    zone.last_respawn_ts
                    + timedelta(seconds=zone.respawn_wait))
                if timezone.now() > threshold:
                    should_zone_reset = True

            if should_zone_reset:
                zone.last_respawn_ts = timezone.now()
                zone.save(update_fields=['last_respawn_ts'])

        # Reset doors for MPW
        if world.is_multiplayer and should_zone_reset:
            doors = Door.objects.filter(
                from_room__zone=zone)
            for door in doors:
                output['doors'].append({
                    'room_id': door.from_room.id,
                    'room_key': door.from_room.get_game_key(
                        spawn_world=world),
                    'state': door.default_state,
                    'direction': door.direction,
                    'name': door.name,
                })

        from spawns.spawn_plans import run_spawn_plans
        output['spawn_plans'].extend(
            run_spawn_plans(
                world=world,
                zone_id=zone.id,
                initial=initial,
                repopulate=repopulate,
            )
        )

    world.last_spawn_plan_run_ts = timezone.now()
    world.save(update_fields=['last_spawn_plan_run_ts'])

    return output
