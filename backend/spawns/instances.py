from config import constants as adv_consts

from spawns.models import Item


def prepare_entry(player, spawned_world, room=None):

    entrance = room or spawned_world.config.starting_room

    # Determine entrance room
    # entrance = (
    #     spawned_world.config.exits_to or
    #     spawned_world.config.starting_room)

    # Get all the IDs of the items that are tied to this character
    item_ids = []
    # add inventory
    item_ids.extend(player.inventory.values_list('id', flat=True))
    item_ids.extend(
        player.equipment.inventory.values_list('id', flat=True))

    # See which of the containers have nested items
    containers = Item.objects.filter(
        id__in=item_ids,
        type=adv_consts.ITEM_TYPE_CONTAINER)
    for container in containers:
        item_ids.extend(container.get_contained_ids())

    # Set all the items to now belong to the new world
    Item.objects.filter(id__in=item_ids).update(world=spawned_world)

    # Set the player's world
    player.world = spawned_world
    player.room = entrance

    # Save changes
    player.save(update_fields=['world, roomn'])

    return entrance
