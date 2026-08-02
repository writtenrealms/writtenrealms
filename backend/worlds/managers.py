from config import constants as adv_consts

from django.core.exceptions import ValidationError
from django.db import models


class WorldQuerySet(models.QuerySet):

    def update(self, **kwargs):
        if 'next_room_relative_id' in kwargs:
            raise ValidationError(
                'Use the world room-identity allocator instead of updating '
                'its high-water mark directly.')
        identity_fields = {
            'instance_of',
            'instance_of_id',
            'context',
            'context_id',
            'instance_slug',
        }.intersection(kwargs)
        if identity_fields:
            raise ValidationError(
                'World manifest scope fields cannot be updated directly: '
                f'{", ".join(sorted(identity_fields))}.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if 'next_room_relative_id' in fields:
            raise ValidationError(
                'Use the world room-identity allocator instead of updating '
                'its high-water mark directly.')
        identity_fields = {
            'instance_of',
            'instance_of_id',
            'context',
            'context_id',
            'instance_slug',
        }.intersection(fields)
        if identity_fields:
            raise ValidationError(
                'World manifest scope fields cannot be updated directly: '
                f'{", ".join(sorted(identity_fields))}.')
        return super().bulk_update(objs, fields, batch_size=batch_size)


class WorldManager(models.Manager.from_queryset(WorldQuerySet)):

    def advance_room_identity_allocator(
        self,
        *,
        world_id,
        next_relative_id,
    ):
        """Advance, but never rewind, one world's room identity counter."""

        queryset = models.QuerySet(
            model=self.model,
            using=self.db,
        )
        return queryset.filter(
            pk=world_id,
            next_room_relative_id__lt=next_relative_id,
        ).update(
            next_room_relative_id=next_relative_id,
        )

    def new_world(self, *args, **kwargs):
        """
        Essentially creates a new template world with a zone, room, config
        object, and anything that a 'proper' new world should contain.
        Outside of specific tests or private units of functionality, should
        be used over .create
        """
        from worlds.models import Room, Zone, WorldConfig

        provided_config = kwargs.pop("config", None)
        world = super().create(**kwargs)
        zone = Zone.objects.create(name='Starting Zone', world=world)
        room = Room.objects.create(
            name='Starting Room',
            world=world,
            zone=zone,
            x=0, y=0, z=0)

        if provided_config is not None:
            config = provided_config
            config_update_fields = []
            if not config.starting_room_id:
                config.starting_room = room
                config_update_fields.append("starting_room")
            if not config.death_room_id:
                config.death_room = room
                config_update_fields.append("death_room")
            if config_update_fields:
                config.save(update_fields=config_update_fields)
        else:
            config = WorldConfig.objects.create(
                starting_room=room,
                death_room=room,
            )

        world.config = config
        world.save(update_fields=["config"])
        return world


class RoomQuerySet(models.QuerySet):

    _IDENTITY_FIELDS = frozenset({
        'relative_id',
        'world',
        'world_id',
    })

    def update(self, **kwargs):
        immutable_fields = self._IDENTITY_FIELDS.intersection(kwargs)
        if immutable_fields:
            fields = ', '.join(sorted(immutable_fields))
            raise ValidationError(
                f'Room identity fields cannot be updated: {fields}.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        immutable_fields = self._IDENTITY_FIELDS.intersection(fields)
        if immutable_fields:
            field_names = ', '.join(sorted(immutable_fields))
            raise ValidationError(
                f'Room identity fields cannot be updated: {field_names}.')
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        # Per-room creation is what serializes the world's permanent
        # high-water allocator. Silently accepting bulk inserts here could
        # reuse retired identities or leave the allocator behind.
        raise ValidationError(
            'Rooms cannot be bulk-created; use the room identity allocator.')


class RoomManager(models.Manager.from_queryset(RoomQuerySet)):

    def create_with_imported_relative_id(self, *, relative_id, **kwargs):
        """
        Create a room with an identity supplied by a portable manifest.

        Room.save() serializes this against the owning world's persistent
        allocator and advances the high-water mark. An identity below that
        mark cannot be recreated after deletion.
        """
        if isinstance(relative_id, bool):
            raise ValidationError('Room relative IDs must be positive integers.')
        try:
            relative_id = int(relative_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                'Room relative IDs must be positive integers.') from exc
        if relative_id <= 0:
            raise ValidationError('Room relative IDs must be positive integers.')
        return self.create(relative_id=relative_id, **kwargs)

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
