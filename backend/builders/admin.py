from django.contrib import admin

from builders import models as builders_models
from builders.models import (
    BuilderAction,
    BuilderAssignment,
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingProfileRecipe,
    CraftingRecipe,
    Faction,
    FactionRank,
    FactionAssignment,
    FactionRelationship,
    FactSchedule,
    HousingBlock,
    HousingLease,
    ItemBundle,
    ItemBundleEntry,
    ItemDefinition,
    ItemSalvageYield,
    MobDefinition,
    Path,
    RoomAction,
    RoomBlock,
    RoomGetTrigger,
    Social,
    SpawnEntry,
    SpawnPlacement,
    SpawnPlan,
    SpawnPlanRun,
    WorldBuilder,
    WorldReview)
from core.admin import BaseAdmin, DirectRootWorldFilter
from worlds.models import World


class FactionAdmin(BaseAdmin):
    list_display = ('id', 'code', 'is_core', 'name', 'world')
    raw_id_fields = ['world', 'death_room', 'starting_room']
    list_filter = ('is_core',)


class FactionRankAdmin(BaseAdmin):
    list_display = ('id', 'standing', 'name', 'faction')
    raw_id_fields = ['faction']


class FactionAssignmentAdmin(BaseAdmin):
    list_display = ('id', 'faction', 'member', 'value')
    raw_id_fields = ['faction']


class FactionRelationshipAdmin(BaseAdmin):
    list_display = ('id', 'faction', 'towards', 'standing')
    raw_id_fields = ['faction', 'towards']


class WorldBuilderAdmin(BaseAdmin):
    list_display = ('id', 'world', 'user', 'read_only')
    raw_id_fields = ['world', 'user']
    list_filter = (DirectRootWorldFilter, 'read_only')


class ItemDefinitionAdmin(BaseAdmin):
    list_display = ('id', 'key', 'slug', 'name', 'item_type', 'world')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)


class ItemSalvageYieldInline(admin.TabularInline):
    model = ItemSalvageYield
    raw_id_fields = ['material']
    extra = 0


ItemDefinitionAdmin.inlines = [ItemSalvageYieldInline]


class CraftMaterialAdmin(BaseAdmin):
    list_display = ('id', 'slug', 'name', 'order', 'world')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)


class CraftingIngredientInline(admin.TabularInline):
    model = CraftingIngredient
    raw_id_fields = ['material']
    extra = 0


class CraftingRecipeAdmin(BaseAdmin):
    list_display = ('id', 'slug', 'name', 'group', 'cost', 'currency', 'order', 'world')
    raw_id_fields = ['world', 'output_item_definition', 'currency']
    list_filter = (DirectRootWorldFilter, 'group')
    inlines = [CraftingIngredientInline]


class CraftingProfileRecipeInline(admin.TabularInline):
    model = CraftingProfileRecipe
    raw_id_fields = ['recipe']
    extra = 0


class CraftingProfileAdmin(BaseAdmin):
    list_display = ('id', 'slug', 'name', 'world')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)
    inlines = [CraftingProfileRecipeInline]


class ItemBundleEntryInline(admin.TabularInline):
    model = ItemBundleEntry
    raw_id_fields = ['item_definition']
    extra = 0


class ItemBundleAdmin(BaseAdmin):
    list_display = ('id', 'key', 'slug', 'name', 'world')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)
    inlines = [ItemBundleEntryInline]


class MobDefinitionAdmin(BaseAdmin):
    list_display = ('id', 'key', 'slug', 'name', 'mob_type', 'world')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)


class SpawnEntryInline(admin.TabularInline):
    model = SpawnEntry
    raw_id_fields = [
        'target_room',
        'target_zone',
        'target_path',
        'target_entry',
    ]
    extra = 0


class SpawnPlanAdmin(BaseAdmin):
    list_display = ['id', 'slug', 'name', 'world', 'zone', 'order', 'is_active']
    raw_id_fields = ['world', 'zone']
    list_filter = (DirectRootWorldFilter, 'is_active')
    inlines = [SpawnEntryInline]


class SpawnPlanRunAdmin(BaseAdmin):
    list_display = ['id', 'spawn_world', 'plan', 'status', 'generated_at', 'last_reconciled_at']
    raw_id_fields = ['spawn_world', 'plan']
    list_filter = ('status',)


class SpawnPlacementAdmin(BaseAdmin):
    list_display = ['id', 'run', 'entry_slug', 'slot_index', 'room', 'source_type', 'source_slug']
    raw_id_fields = ['run', 'room']


def room_world(obj):
    return obj.room.world
room_world.short_description = 'World'


class RoomGetTriggerAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room', 'argument', 'action']
    raw_id_fields = ['room']
    display_as_choicefield = ['action']


class RoomActionAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room']
    raw_id_fields = ['room']


def num_rooms(obj):
    return obj.rooms.count()
#num_rooms.short_description='Num Rooms'
class PathAdmin(BaseAdmin):
    list_display = ['id', 'name', 'zone', num_rooms]
    list_filter = (DirectRootWorldFilter,)
    raw_id_fields = ['world', 'zone', 'entry_room']
    fields = [
        'world',
        'zone',
        'relative_id',
        'name',
        'notes',
        'max_per_room',
        'max_per_path',
        'entry_room',
    ]


class RoomBlockAdmin(BaseAdmin):
    list_display = ['id', 'name']


class HousingBlockAdmin(BaseAdmin):
    list_display = ['id', 'name', 'owner', 'price']
    raw_id_fields = ['owner']


class HousingLeaseAdmin(BaseAdmin):
    list_display = ['id', 'block', 'owner', 'price', 'created_ts']
    raw_id_fields = ['block', 'owner']


class FactScheduleAdmin(BaseAdmin):
    list_display = ('id', 'world', 'name')
    raw_id_fields = ['world']


class WorldReviewAdmin(BaseAdmin):
    list_display = ('id', 'world', 'status', 'reviewer')
    raw_id_fields = ['world', 'reviewer']


class BuilderActionAdmin(BaseAdmin):
    list_display = ('id', 'action', 'outcome', 'world', 'user')
    raw_id_fields = ['world', 'user']


class BuilderAssignmentAdmin(BaseAdmin):
    list_display = ('id', 'builder', 'assignment',)
    raw_id_fields = ['builder']


class SocialAdmin(BaseAdmin):
    list_display = ('id', 'cmd', 'world')
    raw_id_fields = ['world']


# class

admin.site.register(BuilderAction, BuilderActionAdmin)
admin.site.register(BuilderAssignment, BuilderAssignmentAdmin)
admin.site.register(CraftMaterial, CraftMaterialAdmin)
admin.site.register(CraftingProfile, CraftingProfileAdmin)
admin.site.register(CraftingRecipe, CraftingRecipeAdmin)
admin.site.register(Faction, FactionAdmin)
admin.site.register(FactionRank, FactionRankAdmin)
admin.site.register(FactionAssignment, FactionAssignmentAdmin)
admin.site.register(FactionRelationship, FactionRelationshipAdmin)
admin.site.register(FactSchedule, FactScheduleAdmin)
admin.site.register(HousingBlock, HousingBlockAdmin)
admin.site.register(HousingLease, HousingLeaseAdmin)
admin.site.register(ItemDefinition, ItemDefinitionAdmin)
admin.site.register(ItemBundle, ItemBundleAdmin)
admin.site.register(MobDefinition, MobDefinitionAdmin)
admin.site.register(Path, PathAdmin)
admin.site.register(RoomAction, RoomActionAdmin)
admin.site.register(RoomBlock, RoomBlockAdmin)
admin.site.register(RoomGetTrigger, RoomGetTriggerAdmin)
admin.site.register(Social, SocialAdmin)
admin.site.register(SpawnPlan, SpawnPlanAdmin)
admin.site.register(SpawnPlanRun, SpawnPlanRunAdmin)
admin.site.register(SpawnPlacement, SpawnPlacementAdmin)
admin.site.register(WorldBuilder, WorldBuilderAdmin)
admin.site.register(WorldReview, WorldReviewAdmin)
