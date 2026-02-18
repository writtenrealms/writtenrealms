from django.contrib import admin

from builders import models as builders_models
from builders.models import (
    BuilderAction,
    BuilderAssignment,
    EquipmentProfile,
    EquipmentSlot,
    Faction,
    FactionRank,
    FactionAssignment,
    FactionRelationship,
    FactSchedule,
    HousingBlock,
    HousingLease,
    ItemAction,
    ItemTemplate,
    ItemTemplateInventory,
    Loader,
    MobEquipmentProfile,
    MerchantInventory,
    MobReaction,
    MobReactionCondition,
    MobTemplate,
    MobTemplateInventory,
    Objective,
    Path,
    Procession,
    Quest,
    RandomItemProfile,
    Reward,
    RoomAction,
    RoomBlock,
    RoomCheck,
    RoomCommandCheck,
    RoomGetTrigger,
    Rule,
    Skill,
    Social,
    TransformationTemplate,
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


class ItemTemplateAdmin(BaseAdmin):
    list_display = ('id', 'key', 'name', 'type', 'level')
    raw_id_fields = ['world']
    list_filter = (DirectRootWorldFilter,)
    display_as_charfield = [
        'name',
        'hit_msg_first', 'hit_msg_third',
    ]
    display_as_choicefield = [
        'type', 'quality',
        'equipment_type', 'armor_class',
        'weapon_grip',
    ]


class ItemTemplateInventoryAdmin(BaseAdmin):
    list_display = ('id', 'container', 'item_template', 'probability')


# Mob Template

class MobTemplateAdmin(BaseAdmin):
    list_display = ('id', 'key', 'name', 'type', 'level')
    list_filter = (DirectRootWorldFilter,)
    display_as_charfield = [
        'name',
    ]
    display_as_choicefield = [
        'roaming_type',
        'aggression',
        'archetype',
        'gender',
        'type',
    ]
    raw_id_fields = ['world']


class MobTemplateInventoryAdmin(BaseAdmin):
    list_display = ('id', 'container', 'item_template', 'probability')
    raw_id_fields = ['container', 'item_template']


class TransformationTemplateAdmin(BaseAdmin):
    list_display = ('id', 'transformation_type', 'arg1', 'arg2')


class MerchantInventoryAdmin(BaseAdmin):
    list_display = ('id', 'mob', 'item_template', 'random_item_profile', 'num')
    raw_id_fields = ['mob', 'item_template', 'random_item_profile']


def mr_world(mob_reaction):
    return mob_reaction.template.world
mr_world.short_description = 'World'
class MobReactionAdmin(BaseAdmin):
    list_display = ('id', 'template', 'event', mr_world)
    raw_id_fields = ['template']
    display_as_choicefield = ['event']


class MobReactionConditionAdmin(BaseAdmin):
    list_display = ('id', 'reaction', 'condition', 'argument')
    raw_id_fields = ['reaction']
    display_as_choicefield = ['condition']


class EquipmentProfileAdmin(BaseAdmin):
    list_display = ('id', 'name')


class EquipmentSlotAdmin(BaseAdmin):
    list_display = (
        'id',
        'profile', 'slot_name',
        'item_template',
        'level', 'chance_imbued', 'chance_enchanted',
    )
    raw_id_fields = ['profile', 'item_template']
    display_as_choicefield = ['slot_name']


class MobEquipmentProfileAdmin(BaseAdmin):
    list_display = ('mob', 'profile', 'priority')
    raw_id_fields = ['mob', 'profile']


class LoaderAdmin(BaseAdmin):
    list_display = ['id', 'name', 'zone', 'order',]
    raw_id_fields = ['zone', 'world']
    list_filter = (DirectRootWorldFilter,)
    display_as_charfield = ['name']


class RuleAdmin(BaseAdmin):
    list_display = ['id', 'loader', 'order']
    raw_id_fields = ['loader']


def room_world(obj):
    return obj.room.world
room_world.short_description = 'World'


class RoomGetTriggerAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room', 'argument', 'action']
    raw_id_fields = ['room']
    display_as_choicefield = ['action']


class RoomCommandCheckAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room', 'check_type']
    raw_id_fields = ['room']


class RoomCheckAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room', 'prevent', 'check_type', 'argument']
    raw_id_fields = ['room']
    display_as_choicefield = ['prevent', 'check_type']


class RoomActionAdmin(BaseAdmin):
    list_display = ['id', room_world, 'name', 'room']
    raw_id_fields = ['room']


class ItemActionAdmin(BaseAdmin):
    list_display = ['id', 'name', 'item_template']
    raw_id_fields = ['item_template']


# Quests

class QuestAdmin(BaseAdmin):
    list_display = ['id', 'world', 'name', 'mob_template']
    raw_id_fields = ['world', 'zone', 'mob_template', 'requires_quest']
    list_filter = (DirectRootWorldFilter,)
    fields = [
        'relative_id',
        'world',
        'zone',
        'mob_template',
        'type',
        'repeatable_after',

        #'requires_level',
        'requires_quest',

        'name',
        'level',
        'notes',
        'summary',

        'entrance_cmds',
        'repeat_entrance_cmd_after',

        'enquire_cmd_available',
        'enquire_cmds',
        'enquire_keywords',

        'completion_entrance_cmds',
        'repeat_completion_entrance_cmds_after',

        'completion_cmd_available',
        'completion_cmds',
        'completion_keywords',
        'completion_action',
        'completion_despawn',
        'complete_silently',
    ]


class ObjectiveAdmin(BaseAdmin):
    list_display = ['id', 'quest', 'type', 'template', 'qty']
    raw_id_fields = ['quest']
    display_as_choicefield = ['type']


class RewardAdmin(BaseAdmin):
    list_display = ['id', 'quest', 'type']
    raw_id_fields = ['quest']
    display_as_choicefield = ['type']


class RandomItemProfileAdmin(BaseAdmin):
    list_display = ['id', 'name', 'level', 'restriction']
    display_as_choicefield = ['restriction']
    raw_id_fields = ['world']

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


class ProcessionAdmin(BaseAdmin):
    list_display = ('id', 'faction', 'room')
    raw_id_fields = ['faction', 'room']


class FactScheduleAdmin(BaseAdmin):
    list_display = ('id', 'world', 'name')
    raw_id_fields = ['world']


class SkillAdmin(BaseAdmin):
    list_display = ('id', 'world', 'code')
    raw_id_fields = ['world', 'consumes']


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
admin.site.register(EquipmentProfile, EquipmentProfileAdmin)
admin.site.register(EquipmentSlot, EquipmentSlotAdmin)
admin.site.register(Faction, FactionAdmin)
admin.site.register(FactionRank, FactionRankAdmin)
admin.site.register(FactionAssignment, FactionAssignmentAdmin)
admin.site.register(FactionRelationship, FactionRelationshipAdmin)
admin.site.register(FactSchedule, FactScheduleAdmin)
admin.site.register(HousingBlock, HousingBlockAdmin)
admin.site.register(HousingLease, HousingLeaseAdmin)
admin.site.register(ItemAction, ItemActionAdmin)
admin.site.register(ItemTemplate, ItemTemplateAdmin)
admin.site.register(ItemTemplateInventory, ItemTemplateInventoryAdmin)
admin.site.register(Loader, LoaderAdmin)
admin.site.register(MerchantInventory, MerchantInventoryAdmin)
admin.site.register(MobEquipmentProfile, MobEquipmentProfileAdmin)
admin.site.register(MobReaction, MobReactionAdmin)
admin.site.register(MobReactionCondition, MobReactionConditionAdmin)
admin.site.register(MobTemplate, MobTemplateAdmin)
admin.site.register(MobTemplateInventory, MobTemplateInventoryAdmin)
admin.site.register(Objective, ObjectiveAdmin)
admin.site.register(Path, PathAdmin)
admin.site.register(Procession, ProcessionAdmin)
admin.site.register(Quest, QuestAdmin)
admin.site.register(RandomItemProfile, RandomItemProfileAdmin)
admin.site.register(Reward, RewardAdmin)
admin.site.register(RoomAction, RoomActionAdmin)
admin.site.register(RoomBlock, RoomBlockAdmin)
admin.site.register(RoomCheck, RoomCheckAdmin)
admin.site.register(RoomCommandCheck, RoomCommandCheckAdmin)
admin.site.register(RoomGetTrigger, RoomGetTriggerAdmin)
admin.site.register(Rule, RuleAdmin)
admin.site.register(Skill, SkillAdmin)
admin.site.register(Social, SocialAdmin)
admin.site.register(TransformationTemplate, TransformationTemplateAdmin)
admin.site.register(WorldBuilder, WorldBuilderAdmin)
admin.site.register(WorldReview, WorldReviewAdmin)
