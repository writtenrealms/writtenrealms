from django.contrib import admin

from core.admin import BaseAdmin, DirectRootWorldFilter
from worlds.models import (
    DeathRoutingCompiledSnapshot,
    DeathRoutingPolicy,
    DeathRoutingRoute,
    DeathRoutingSnapshotReference,
    Door,
    InstanceAssignment,
    InstanceParticipant,
    InstanceRun,
    Room,
    RoomFlag,
    RoomDetail,
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
    list_display = [
        'id',
        num_worlds,
        root_world,
        'death_routing_generation',
        'death_routing_source',
    ]
    raw_id_fields = ['starting_room', 'exits_to']
    display_as_choicefield = [
        'death_mode',
        'death_routing_source',
        'pvp_mode',
    ]
    readonly_fields = [
        'death_room',
        'death_routing_generation',
        'death_routing_source',
        'death_routing_source_generation',
    ]
    search_fields = ['configured_worlds__name']


class ZoneAdmin(BaseAdmin):

    list_display = ['id', 'key', 'name', 'world', 'default_roam_chance']
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


class InstanceRunAdmin(BaseAdmin):
    list_display = [
        'id',
        'template_world',
        'spawned_world',
        'status',
        'leader',
        'started_at',
        'last_active_at',
    ]
    list_filter = ['status']
    raw_id_fields = ['base_world', 'template_world', 'spawned_world', 'leader']
    search_fields = ['id', 'ref', 'template_world__name', 'spawned_world__name']


class InstanceParticipantAdmin(BaseAdmin):
    list_display = [
        'id',
        'run',
        'player',
        'role',
        'transfer_from',
        'return_runtime_world',
        'joined_at',
        'exited_at',
        'exit_reason',
    ]
    list_filter = ['role', 'exit_reason']
    raw_id_fields = [
        'run',
        'player',
        'transfer_from',
        'return_runtime_world',
    ]


class DerivedDeathRoutingAdmin(BaseAdmin):
    """Derived routing records are published only through the compiler."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DeathRoutingPolicyAdmin(DerivedDeathRoutingAdmin):
    list_display = ['id', 'config', 'enabled', 'modified_ts']
    raw_id_fields = ['config']


class DeathRoutingRouteAdmin(DerivedDeathRoutingAdmin):
    list_display = [
        'id',
        'policy',
        'position',
        'compiled_version',
        'destination_room',
    ]
    raw_id_fields = [
        'policy',
        'destination_room',
    ]


class DeathRoutingCompiledSnapshotAdmin(DerivedDeathRoutingAdmin):
    list_display = [
        'id',
        'config',
        'plan_generation',
        'cache_version',
        'retirement_pending',
        'retired_at',
    ]
    raw_id_fields = ['config']


class DeathRoutingSnapshotReferenceAdmin(DerivedDeathRoutingAdmin):
    list_display = [
        'id',
        'snapshot',
        'destination_room',
        'core_faction',
        'origin_zone',
    ]
    raw_id_fields = [
        'snapshot',
        'destination_room',
        'core_faction',
        'origin_zone',
    ]


admin.site.register(World, WorldAdmin)
admin.site.register(WorldConfig, WorldConfigAdmin)
admin.site.register(DeathRoutingPolicy, DeathRoutingPolicyAdmin)
admin.site.register(DeathRoutingRoute, DeathRoutingRouteAdmin)
admin.site.register(
    DeathRoutingCompiledSnapshot,
    DeathRoutingCompiledSnapshotAdmin,
)
admin.site.register(
    DeathRoutingSnapshotReference,
    DeathRoutingSnapshotReferenceAdmin,
)
admin.site.register(Zone, ZoneAdmin)
admin.site.register(Room, RoomAdmin)
admin.site.register(RoomFlag, RoomFlagAdmin)
admin.site.register(RoomDetail, RoomDetailAdmin)
admin.site.register(Door, DoorAdmin)
admin.site.register(WorldURL, WorldURLAdmin)
admin.site.register(InstanceAssignment, InstanceAssignmentAdmin)
admin.site.register(InstanceRun, InstanceRunAdmin)
admin.site.register(InstanceParticipant, InstanceParticipantAdmin)
