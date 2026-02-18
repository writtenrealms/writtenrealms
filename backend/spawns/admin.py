from django.contrib import admin

from spawns.models import (
    Clan,
    ClanMembership,
    Player,
    PlayerData,
    Item,
    Mob,
    Equipment,
    RoomCommandCheckState,
    PlayerEnquire,
    PlayerQuest,
    PlayerTrophy,
    Alias,
    PlayerFlexSkill,
    PlayerFeat,
    PlayerEvent,
    PlayerConfig,
    Mark)

from core.admin import BaseAdmin, ContextRootWorldFilter

class PlayerAdmin(BaseAdmin):
    list_display = ('id', 'name', 'level', 'room', 'user')
    #list_filter = [ContextRootWorldFilter]
    display_as_charfield = ['name', 'title']
    display_as_choicefield = ['archetype', 'gender']
    search_fields = ['id', 'name']
    # Detail
    exclude = ['equipment', 'viewed_rooms']
    raw_id_fields = ['world', 'room', 'equipment', 'user', 'config']


class PlayerTrophyAdmin(BaseAdmin):
    list_display = ('id', 'player', 'mob_template', 'created_ts')
    raw_id_fields = ['player', 'mob_template']


def eq_for(eq):
    try:
        return eq.player.name
    except AttributeError:
        return eq.mob.name or eq.mob.template.name
eq_for.short_description = 'For'


class EquipmentAdmin(BaseAdmin):
    list_display = ('id', eq_for)
    raw_id_fields = [
        'weapon',
        'offhand',
        'head',
        'shoulders',
        'body',
        'arms',
        'hands',
        'waist',
        'legs',
        'feet',
        'accessory'
    ]


def name(item):
    if item.template:
        return item.template.name
    else:
        return item.name
class ItemAdmin(BaseAdmin):
    list_display = ('id', name, 'world', 'container', 'template')
    #list_filter = [ContextRootWorldFilter]
    raw_id_fields = ['world', 'template', 'rule', 'profile', 'augment']
    display_as_choicefield = [
        'type', 'quality', 'armor_class', 'equipment_type'
    ]


class MobAdmin(BaseAdmin):
    list_display = ('id', 'world', 'room', 'template')
    #list_filter = [ContextRootWorldFilter]
    # Detail
    exclude = ['equipment']
    raw_id_fields = ['world', 'room', 'template', 'rule']


class RoomCommandCheckStateAdmin(BaseAdmin):
    list_display = ('id', 'world', 'cmd_check', 'passed_ts')
    raw_id_fields = ['world', 'cmd_check']


class AliasAdmin(BaseAdmin):
    list_display = ('id', 'player', 'match', 'replacement')
    raw_id_fields = ('player',)


class PlayerQuestAdmin(BaseAdmin):
    list_display = ('id', 'player', 'quest', 'completion_ts')
    raw_id_fields = ('player', 'quest')


class PlayerEnquireAdmin(BaseAdmin):
    list_display = ('id', 'player', 'quest', 'enquire_ts')
    raw_id_fields = ('player', 'quest')


class PlayerFlexSkillAdmin(BaseAdmin):
    list_display = ('id', 'player', 'code')
    raw_id_fields = ('player',)


class PlayerFeatAdmin(BaseAdmin):
    list_display = ('id', 'player', 'code')
    raw_id_fields = ('player',)


def user(player_event):
    return player_event.player.user
class PlayerEventAdmin(BaseAdmin):
    list_display = ('id', 'player', user, 'event', 'created_ts', 'ip')
    raw_id_fields = ('player',)


class PlayerConfigAdmin(BaseAdmin):
    list_display = ('id', 'room_brief', 'combat_brief')


class MarkAdmin(BaseAdmin):
    list_display = ['id', 'player', 'name', 'value']
    raw_id_fields = ['player']


class ClanAdmin(BaseAdmin):
    list_display = ['id', 'name', 'world']
    raw_id_fields = ['world']


class ClanMembershipAdmin(BaseAdmin):
    list_display = ['id', 'player', 'clan', 'rank']
    raw_id_fields = ['player', 'clan']


def player_data_world(player_data):
    return player_data.player.world
class PlayerDataAdmin(BaseAdmin):
    list_display = ['id', 'created_ts', 'player', player_data_world]
    raw_id_fields = ['player']

admin.site.register(Clan, ClanAdmin)
admin.site.register(ClanMembership, ClanMembershipAdmin)
admin.site.register(Player, PlayerAdmin)
admin.site.register(Equipment, EquipmentAdmin)
admin.site.register(Item, ItemAdmin)
admin.site.register(Mob, MobAdmin)
admin.site.register(RoomCommandCheckState, RoomCommandCheckStateAdmin)
admin.site.register(Alias, AliasAdmin)
admin.site.register(PlayerEnquire, PlayerEnquireAdmin)
admin.site.register(PlayerQuest, PlayerQuestAdmin)
admin.site.register(PlayerTrophy, PlayerTrophyAdmin)
admin.site.register(PlayerFlexSkill, PlayerFlexSkillAdmin)
admin.site.register(PlayerFeat, PlayerFeatAdmin)
admin.site.register(PlayerEvent, PlayerEventAdmin)
admin.site.register(PlayerConfig, PlayerConfigAdmin)
admin.site.register(Mark, MarkAdmin)
admin.site.register(PlayerData, PlayerDataAdmin)