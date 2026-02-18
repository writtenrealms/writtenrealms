from django.contrib import admin

from core.admin import BaseAdmin, DirectRootWorldFilter
from worlds.models import (
    Door,
    InstanceAssignment,
    Room,
    RoomFlag,
    RoomDetail,
    StartingEq,
    World,
    WorldConfig,
    WorldURL,
    Zone)


class IsRootWorldFilter(admin.SimpleListFilter):
    title = 'world type'
    parameter_name = 'is_root_world'
    def lookups(self, request, model_admin):
        return (
            (True, 'Root'),
            (False, 'Spawn'),
        )
    def queryset(self, request, queryset):
        if self.value() == 'True':
            return queryset.filter(context_id__isnull=True)
        elif self.value() == 'False':
            return queryset.filter(context_id__isnull=False)
        else:
            return queryset.all()

def for_player(world):
    if world.is_multiplayer and world.context:
        return 'multiplayer'

    players = world.players.all()
    if len(players) == 1:
        return players[0]
    return None
for_player.short_description = 'For'

class WorldAdmin(BaseAdmin):
    list_display = ['id', 'name', 'context', 'lifecycle', for_player]
    list_filter = (IsRootWorldFilter,)
    raw_id_fields = ['author', 'config', 'context', 'instance_of', 'leader']
    display_as_charfield = ['name']
    display_as_choicefield = ['lifecycle']
    exclude = ['full_map']
    search_fields = ['id', 'name']


def num_worlds(config):
    return config.configured_worlds.count()
num_worlds.short_description = 'Number of Worlds'

def root_world(config):
    world = config.configured_worlds.first()
    return world.context if world.context else world
root_world.short_description = 'Root World'

class WorldConfigAdmin(BaseAdmin):
    list_display = ['id', num_worlds, root_world]
    raw_id_fields = ['starting_room', 'death_room', 'exits_to']
    exclude = ['starting_eq']
    display_as_choicefield = ['death_mode']
    search_fields = ['configured_worlds__name']


class ZoneAdmin(BaseAdmin):

    list_display = ['id', 'key', 'name', 'world']
    raw_id_fields = ['world', 'center']
    list_filter = (DirectRootWorldFilter,)


class RoomAdmin(BaseAdmin):
    list_display = ['id', 'key', 'name', 'world', 'x', 'y', 'z']
    list_filter = (DirectRootWorldFilter,)
    raw_id_fields = [
        'world',
        'enters_instance',
        'zone',
        'north', 'east', 'south', 'west', 'up', 'down',
        'transfer_to',
        'housing_block',
        'exits_to',
    ]
    search_fields = ['id', 'name']
    display_as_choicefield = ['type']


def room_flag_world(roomflag):
    return roomflag.room.world
room_flag_world.short_description = 'World'

class RoomFlagAdmin(BaseAdmin):
    list_display = ['id', 'code', 'room', room_flag_world]
    raw_id_fields = ['room']
    display_as_choicefield = ['code']


def room_detail_world(roomdetail):
    return roomdetail.room.world
room_detail_world.short_description = 'World'
class RoomDetailAdmin(BaseAdmin):
    list_display = ['id', 'keywords', 'room', room_detail_world]
    raw_id_fields = ['room']


class StartingEqAdmin(BaseAdmin):
    list_display = ['id', 'worldconfig', 'itemtemplate']
    raw_id_fields = ['worldconfig', 'itemtemplate']


class DoorAdmin(BaseAdmin):
    list_display = [
        'id', 'from_room', 'to_room', 'default_state'
    ]


class WorldURLAdmin(BaseAdmin):
    list_display = ['world', 'url', 'is_private']
    raw_id_fields = ['world']


class InstanceAssignmentAdmin(BaseAdmin):
    list_display = ['id', 'player', 'instance', 'transfer_from', 'leader']
    raw_id_fields = ['instance', 'player', 'transfer_from', 'leader']


admin.site.register(World, WorldAdmin)
admin.site.register(WorldConfig, WorldConfigAdmin)
admin.site.register(Zone, ZoneAdmin)
admin.site.register(Room, RoomAdmin)
admin.site.register(RoomFlag, RoomFlagAdmin)
admin.site.register(RoomDetail, RoomDetailAdmin)
admin.site.register(StartingEq, StartingEqAdmin)
admin.site.register(Door, DoorAdmin)
admin.site.register(WorldURL, WorldURLAdmin)
admin.site.register(InstanceAssignment, InstanceAssignmentAdmin)
