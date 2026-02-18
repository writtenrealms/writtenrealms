from config import constants as adv_consts

from django.db import models


class WorldManager(models.Manager):

    def new_world(self, *args, **kwargs):
        """
        Essentially creates a new template world with a zone, room, config
        object, and anything that a 'proper' new world should contain.
        Outside of specific tests or private units of functionality, should
        be used over .create
        """
        from worlds.models import Room, Zone, WorldConfig

        world = super().create(**kwargs)
        zone = Zone.objects.create(name='Starting Zone', world=world)
        room = Room.objects.create(
            name='Starting Room',
            world=world,
            zone=zone,
            x=0, y=0, z=0)
        world.config = WorldConfig.objects.create(
            starting_room=room, death_room=room)
        world.save()
        return world


class RoomManager(models.Manager):

    def get_map(self, room, radius=5):
        dimensions = adv_consts.MAP_DIMENSIONS
        return self.get_queryset().filter(
            world=room.world,
            x__gte=room.x - radius,
            x__lte=room.x + radius,
            y__gte=room.y - radius,
            y__lte=room.y + radius,
            z__gte=room.z - radius,
            z__lte=room.z + radius)

    def prefetch_map(self, qs):
        return qs.prefetch_related(
            'north',
            'east',
            'west',
            'south',
            'up',
            'down',
            'zone',
            'world',
            'flags',
        )
