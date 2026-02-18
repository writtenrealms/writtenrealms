import json
import logging
import math
import re

from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation)
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils import timezone
from redis import exceptions as redis_exceptions

from config import constants as adv_consts
from core.computations import compute_stats
from core.utils import has_number
from core.utils.items import (
    get_item_budget,
    type_to_slot,
    calculate_power)

from config import constants as api_consts
from config import game_settings as adv_config

from core.db import (
    BaseModel,
    AdventBaseModel,
    list_to_choice,
    optional)
from core.model_mixins import CharMixin, ItemMixin, MobMixin
from worlds.models import StartingEq


lifecycle_logger = logging.getLogger('lifecycle')


class Equipment(AdventBaseModel):

    weapon = models.ForeignKey(
        'spawns.Item', related_name='weapon_equipped',
        on_delete=models.SET_NULL, **optional)
    offhand = models.ForeignKey(
        'spawns.Item', related_name='offhand_equipped',
        on_delete=models.SET_NULL, **optional)
    head = models.ForeignKey(
        'spawns.Item', related_name='head_equipped',
        on_delete=models.SET_NULL, **optional)
    shoulders = models.ForeignKey(
        'spawns.Item', related_name='shoulders_equipped',
        on_delete=models.SET_NULL, **optional)
    body = models.ForeignKey(
        'spawns.Item', related_name='body_equipped',
        on_delete=models.SET_NULL, **optional)
    arms = models.ForeignKey(
        'spawns.Item', related_name='arms_equipped',
        on_delete=models.SET_NULL, **optional)
    hands = models.ForeignKey(
        'spawns.Item', related_name='hands_equipped',
        on_delete=models.SET_NULL, **optional)
    waist = models.ForeignKey(
        'spawns.Item', related_name='waist_equipped',
        on_delete=models.SET_NULL, **optional)
    legs = models.ForeignKey(
        'spawns.Item', related_name='legs_equipped',
        on_delete=models.SET_NULL, **optional)
    feet = models.ForeignKey(
        'spawns.Item', related_name='feet_equipped',
        on_delete=models.SET_NULL, **optional)
    accessory = models.ForeignKey(
        'spawns.Item', related_name='accessory_equipped',
        on_delete=models.SET_NULL, **optional)

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    def __str__(self):
        try:
            return "Eq %s for %s" % (self.id, self.player)
        except (AttributeError, self.DoesNotExist):
            try:
                return "Eq %s for %s" % (self.id, self.mob)
            except (AttributeError, self.DoesNotExist):
                return "Eq for nothing"

    @property
    def char(self):
        try:
            return self.player
        except AttributeError:
            return self.mob

    #@transaction.atomic
    def equip(self, item, slot):
        setattr(self, slot, item)
        self.save()
        item.container = self
        item.save()
        return item


class PlayerManager(models.Manager):

    def create(self, name, room, user, world, *args, **kwargs):
        return super().create(
            name=name,
            room=room,
            user=user,
            world=world,
            *args, **kwargs)


class Player(CharMixin, AdventBaseModel):

    objects = PlayerManager()

    pending_deletion_ts = models.DateTimeField(db_index=True, **optional)

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='players')
    user = models.ForeignKey('users.User',
                             on_delete=models.CASCADE,
                             related_name='characters')
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.SET_NULL,
                             related_name='players',
                             **optional)
    equipment = models.OneToOneField('spawns.Equipment',
                                     related_name='player',
                                     on_delete=models.CASCADE,
                                     **optional)
    config = models.ForeignKey('spawns.PlayerConfig',
                               related_name='players',
                               on_delete=models.CASCADE,
                               **optional)

    is_immortal = models.BooleanField(default=False)
    is_invisible = models.BooleanField(default=False)

    language_proficiency = models.DecimalField(max_digits=2, decimal_places=1,
                                               default='0.0')

    # Field that gets set when we start to save a player's data so that
    # we never run into a risk of duplicate saving.
    save_start_ts = models.DateField(**optional)

    # Points gained and lost when killing / dying to other players
    glory = models.PositiveIntegerField(default=0)
    medals = models.PositiveIntegerField(default=0)
    currencies = models.TextField(**optional)

    # Moderation flags
    nochat = models.BooleanField(default=False)
    noplay = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ['name', 'world']

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    viewed_rooms = models.ManyToManyField(
        'worlds.Room',
        related_name='visited_by')

    last_connection_ts = models.DateTimeField(db_index=True, **optional)
    last_action_ts = models.DateTimeField(**optional)

    in_game = models.BooleanField(default=False)

    mute_list = models.TextField(**optional)
    # Space delimited, lowercase list of channels the player is
    # listening to.
    channels = models.TextField(default='chat', blank=True)

    cooldowns = models.TextField(**optional)
    effects = models.TextField(**optional)

    def __str__(self):
        return "{name} ({level})".format(
            name=self.name,
            level=self.level)

    def save_data(self, exiting=False, player_data_id=None):
        from spawns.extraction import APIExtractor

        if not self.world.is_multiplayer:
            raise TypeError("SPW data gets saved at the world level.")

        try:
            with transaction.atomic():
                player = Player.objects.select_for_update().get(pk=self.pk)
                if player.save_start_ts:
                    return player
                if not player.in_game:
                    lifecycle_logger.info(
                        "Player %s [ %s ] is not in game, skipping save." % (
                            player.name, player.id
                        ))
                    return player
                player.save_start_ts = timezone.now()
                player.save()

            if player_data_id:
                player_data = json.loads(
                    PlayerData.objects.get(pk=player_data_id).data)

                api_extractor = APIExtractor(world=self.world,
                                            extraction_data=player_data)
                api_extractor.extract_player(player)

        finally:
            with transaction.atomic():
                player = Player.objects.select_for_update().get(pk=self.pk)
                player.save_start_ts = None
                if exiting:
                    player.in_game = False
                player.save()

        return player

    @classmethod
    def validate_name(cls, world, name):
        "Method meant to handle validation in serializers."
        from rest_framework import serializers

        if world.is_multiplayer:

            if ' ' in name:
                name = name.split(' ')[0]

            name = name.capitalize()[0:20]

            # Edeus-specific policies, would be nice to make this configurable
            # as well.
            if world.id == 1:
                if has_number(name):
                    raise serializers.ValidationError(
                        "No numbers allowed in player names.")

            if Player.objects.filter(
                world__context=world,
                name__iexact=name).exists():
                raise serializers.ValidationError(
                    "This name is already taken.")

            if re.search('\d+', name):
                raise serializers.ValidationError(
                    "No numbers allowed in player names.")
            elif re.search('\W+', name):
                raise serializers.ValidationError(
                    "No special characters allowed in player names.")

            if world.config.name_exclusions:
                lname = name.lower()
                if lname in world.config.name_exclusions.lower().split():
                    raise serializers.ValidationError(
                        "That name is unavailable.")

        return name

    def game_lookup(self, key, rdb=None):
        "Lookup something in the game by its key."
        raise NotImplementedError("Old game lookup is no longer supported.")

    def initialize(self, reset=False, level=1, starting_eq=True):
        from builders.models import FactionAssignment
        from worlds.models import Door

        if reset:
            self.level = level
            self.experience = adv_config.LEVEL_EXPERIENCE[level-1]
            self.gold = self.world.config.starting_gold
            self.glory = 0
            self.room = self.get_starting_room()
            # Delete factions
            FactionAssignment.objects.filter(
                faction__world=self.world.context,
                faction__is_core=False,
                member_type__model='player',
                member_id=self.id).delete()
            # Delete trophy
            self.trophy_entries.all().delete()
            # Delete aliases
            self.aliases.all().delete()
            # Delete player quests
            self.player_quests.all().delete()
            # Delete enquired quests
            self.player_enquires.all().delete()
            # Delete visited rooms
            self.viewed_rooms.clear()
            # Delete equipment
            eq = self.equipment
            for eq_slot in adv_consts.EQUIPMENT_SLOTS:
                if getattr(eq, eq_slot, None):
                    setattr(eq, eq_slot, None)
            eq.save()
            self.equipment.inventory.all().delete()
            # Delete inventory
            self.inventory.all().delete()
            # Delete marks
            self.marks.all().delete()

        stats = compute_stats(self.level, self.archetype)
        self.health = stats['health_max']
        self.mana = stats['mana_max']
        self.stamina = stats['stamina_max']
        self.save()

        if starting_eq:
            char_eqs = StartingEq.objects.filter(
                worldconfig=self.world.config,
            ).filter(
                models.Q(archetype=self.archetype)
                | models.Q(archetype__isnull=True))
            for starting_eq in char_eqs:
                for i in range(0, starting_eq.num):
                    item_template = starting_eq.itemtemplate
                    item = item_template.spawn(self, self.world)

                    if item_template.equipment_type:
                        if (self.archetype == adv_consts.ARCHETYPE_ASSASSIN
                            and (item_template.equipment_type
                                    == adv_consts.EQUIPMENT_TYPE_WEAPON_2H)):
                            continue

                        slot = type_to_slot(
                            eq_type=item_template.equipment_type,
                            archetype=self.archetype,
                            has_weapon=bool(self.equipment.weapon))
                        self.equipment.equip(item, slot)

            if self.world.config.starting_gold:
                self.gold += self.world.config.starting_gold
                self.save()

        # Add door states
        if not self.world.is_multiplayer:
            for door in Door.objects.filter(
                from_room__world=self.world.context):
                DoorState.objects.create(
                    door=door,
                    world=self.world,
                    state=door.default_state)

        return self

    def get_starting_room(self):
        """
        By default, a player goes to the starting room for that world.
        But if they have a core faction defined, they should go to
        their starting room instead, if it's defined.
        """

        # Right now there should only be 1 core faction, but just in
        # case in the future we allow multiple, and since it doesn't
        # make the code slower otherwise, we treat the future case.
        core_factions = self.faction_assignments.filter(
            faction__is_core=True
        ).order_by('created_ts')
        for core_faction in core_factions:
            if core_faction.faction.starting_room:
                return core_faction.faction.starting_room

        # Default to the world's starting room
        return self.world.context.config.starting_room

    def reset(self, level=1):
        player = self
        if player.world.is_multiplayer:
            player.initialize(reset=True, level=level)
            return player
        else:
            original_world = player.world
            root_world = player.world.context
            new_spawn_world = root_world.create_spawn_world()

            player.world = new_spawn_world
            player.save()

            player = player.initialize(reset=True, level=level)

            original_world.delete()

            return player

    def restore_gear(self, item_id=None):
        print("Restoring gear for %s..." % self.name)
        from django.contrib.contenttypes.models import ContentType
        from spawns.models import Equipment, Item, Player
        player = self
        player_eq = player.equipment
        player_ct = ContentType.objects.get_for_model(Player)
        eq_ct = ContentType.objects.get_for_model(Equipment)

        # Restore equipment
        eq_qs = Item.objects.filter(
            container_type=eq_ct,
            container_id=player_eq.id,
            is_pending_deletion=True)
        if item_id:
            eq_qs = eq_qs.filter(id=item_id)
        if eq_qs:
            print("Restoring %s equipment items..." % eq_qs.count())
            for item in eq_qs:
                item.is_pending_deletion = False
                item.save(update_fields=['is_pending_deletion'])
                contents = item.inventory.all()
                if contents:
                    contents.update(is_pending_deletion=False)

        # Restore inventory
        inv_qs = Item.objects.filter(
            container_type=player_ct,
            container_id=player.id,
            is_pending_deletion=True,)
        if item_id:
            inv_qs = inv_qs.filter(id=item_id)
        if inv_qs:
            print("Restoring %s inventory items..." % inv_qs.count())
            for item in inv_qs:
                item.is_pending_deletion = False
                item.save(update_fields=['is_pending_deletion'])
                contents = item.inventory.all()
                if contents:
                    contents.update(is_pending_deletion=False)

        print("Done.")

    @property
    def power(self):
        "Return semi-objective measure of how powerful a player is"
        total_power = 0

        for slot in adv_consts.EQUIPMENT_SLOTS:
            item = getattr(self.equipment, slot)
            if not item: continue
            total_power += calculate_power(item, self.archetype)

        return total_power * adv_config.ILF(self.level)

    @property
    def clan(self):
        clan_membership = self.clan_memberships.first()
        if not clan_membership:
            return None
        return {
            'name': clan_membership.clan.name,
            'rank': clan_membership.rank,
        }

    @property
    def game_player(self):
        raise NotImplementedError("Old game lookup is no longer supported.")

def post_player_save(sender, **kwargs):
    player = kwargs['instance']
    if not player.config:
        default_config = PlayerConfig.objects.order_by('created_ts').first()
        if not default_config:
            # This should only happen in tests, or would in a fresh db install.
            # On prod, there will always be a first player config record
            config = PlayerConfig.objects.create(
                room_brief=False,
                combat_brief=False)
            default_config = config
        player.config = default_config
        player.save(update_fields=['config'])
models.signals.post_save.connect(post_player_save, Player)


class PlayerData(BaseModel):
    "Player extraction data persisted when they are exiting a world."

    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='exit_data')
    data = models.TextField(**optional)

    def get_equipment(self):
        for chunk in json.loads(self.data):
            if chunk['model'] == 'equipment':
                return chunk

    def get_inventory(self):
        for chunk in json.loads(self.data):
            if chunk['model'] == 'inventory':
                return chunk


class PlayerTrophy(BaseModel):
    player = models.ForeignKey('spawns.Player',
                               related_name='trophy_entries',
                               on_delete=models.CASCADE)
    mob_template = models.ForeignKey('builders.MobTemplate',
                                     related_name='trophy_entries',
                                     on_delete=models.CASCADE)


models.signals.post_save.connect(Player.post_char_save, Player)
models.signals.post_delete.connect(Player.post_char_delete, Player)


class PlayerFlexSkill(BaseModel):

    number = models.IntegerField()

    player = models.ForeignKey(Player,
                               related_name='flex_skills',
                               on_delete=models.CASCADE)
    code = models.TextField()


class PlayerFeat(BaseModel):
    number = models.IntegerField()
    player = models.ForeignKey(Player,
                               related_name='feats',
                               on_delete=models.CASCADE)
    code = models.TextField()


class PlayerEvent(BaseModel):
    player = models.ForeignKey(Player, related_name='events',
                               on_delete=models.CASCADE)
    event = models.TextField(choices=list_to_choice(api_consts.PLAYER_EVENTS))
    ip = models.TextField(**optional)


class PlayerConfig(BaseModel):
    "Config values set and used by the frontend."

    room_brief = models.BooleanField(default=False)
    combat_brief = models.BooleanField(default=False)
    buffer_length = models.PositiveIntegerField(default=200)
    # MPW only, whether to log out after 5 minutes if idle
    idle_logout = models.BooleanField(default=True)

    # Whether to show join / part messages
    display_connect = models.BooleanField(default=False)

    # Whether to display chats
    display_chat = models.BooleanField(default=True)

    mobile_map_width = models.PositiveIntegerField(default=1)


class Mob(CharMixin, MobMixin, AdventBaseModel):

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='mobs')
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='mobs')
    template = models.ForeignKey('builders.MobTemplate',
                                 on_delete=models.SET_NULL,
                                 related_name='template_mobs',
                                 **optional)
    equipment = models.OneToOneField('spawns.Equipment',
                                     related_name='mob',
                                     on_delete=models.CASCADE,
                                     **optional)

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    rule = models.ForeignKey('builders.Rule',
                             related_name='rules',
                             on_delete=models.SET_NULL,
                             **optional)

    # Generic FK to keep track of where a mob is supposed to roam
    roams_type = models.ForeignKey(ContentType,
                                   on_delete=models.SET_NULL,
                                   **optional)
    roams_id = models.PositiveIntegerField(**optional)
    roams = GenericForeignKey('roams_type', 'roams_id')

    is_pending_deletion = models.BooleanField(default=False)
    pending_deletion_ts = models.DateTimeField(db_index=True, **optional)

    class Meta:
        indexes = [
            models.Index(fields=['created_ts']),
            models.Index(fields=['is_pending_deletion']),
        ]

    def create_corpse(self):
        name = self.template.name if self.template else self.name
        return Item.objects.create(
            name='the corpse of %s' % name,
            keywords='corpse',
            ground_description='The corpse of {} is lying here.'.format(name),
            type=adv_consts.ITEM_TYPE_CORPSE,
            world=self.world,
            level=self.level,
            is_pickable=False,
            container=self)

    def delete(self):
        try:
            corpse = self.inventory.get(
                type=adv_consts.ITEM_TYPE_CORPSE)
            corpse.container = self.room
            corpse.save()

            inventory_items = self.inventory.exclude(
                pk=corpse.pk,
            ).values_list('pk', flat=True)
            equipment_items = self.equipment.inventory.values_list(
                'pk', flat=True)

            Item.objects.filter(
                pk__in=set(inventory_items) | set(equipment_items)
            ).update(
                container_type=ContentType.objects.get_for_model(corpse),
                container_id=corpse.id,
            )
        except Item.DoesNotExist:
            pass
        return super().delete()

    @property
    def game_mob(self):
        raise NotImplementedError("Old game lookup is no longer supported.")


models.signals.post_save.connect(Mob.post_char_save, Mob)
models.signals.post_delete.connect(Mob.post_char_delete, Mob)


class Item(ItemMixin, AdventBaseModel):
    """
    There are two kinds of items. Templated items and procedural items.
    """

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='items')

    # For templated items
    template = models.ForeignKey('builders.ItemTemplate',
                                 on_delete=models.SET_NULL,
                                 related_name='template_items',
                                 **optional)

    # For procedural items
    profile = models.ForeignKey('builders.RandomItemProfile',
                                on_delete=models.SET_NULL,
                                **optional)

    container_type = models.ForeignKey(ContentType,
                                       on_delete=models.SET_NULL,
                                       **optional)
    container_id = models.PositiveIntegerField(**optional)
    container = GenericForeignKey('container_type', 'container_id')

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    rule = models.ForeignKey('builders.Rule',
                             related_name='item_rules',
                             on_delete=models.SET_NULL,
                             **optional)

    # Rather than outright removing items when following extraction we see
    # that they no longer belong to a player, we mark them as pending.
    # This is because, they could be in the wild and be re-picked up by
    # another player.
    is_pending_deletion = models.BooleanField(default=False)
    pending_deletion_ts = models.DateTimeField(db_index=True, **optional)

    label = models.TextField(**optional)

    upgrade_count = models.PositiveIntegerField(default=0)
    augment = models.ForeignKey('spawns.Item',
                                related_name='augment_items',
                                on_delete=models.SET_NULL,
                                **optional)

    class Meta:
        indexes = [
            models.Index(fields=['container_id']),
            models.Index(fields=['type']),
            models.Index(fields=['is_pending_deletion']),
            models.Index(fields=['container_type']),
            models.Index(fields=['is_persistent']),
            models.Index(fields=['created_ts']),
        ]


    def get_game_data(self):
        "Gets the data representation as the game engine expects it"
        from builders.serializers import ItemTemplateSerializer
        template_data = ItemTemplateSerializer(self.template).data
        return template_data

    def get_contained_ids(self):
        """
        Returns the ID of items contained in a container, including all
        nested items.
        """
        ids = []
        for nested_item in self.inventory.all():
            ids.append(nested_item.id)
            if nested_item.type == adv_consts.ITEM_TYPE_CONTAINER:
                ids.extend(nested_item.get_contained_ids())
        return ids

    def boost(self, amount=20):
        "Boost the stats on an item by a percentage amount."
        for attr in adv_consts.ATTRIBUTES:
            value = getattr(self, attr, None)
            if value:
                value = math.ceil(value * 120 / 100)
                setattr(self, attr, value)
        self.upgrade_count += 1
        self.save()
        return self

    @property
    def budget_spent(self):
        spent_budget = 0
        for attr in adv_consts.ATTRIBUTES:
            if getattr(self, attr):
                spent_budget += (
                    adv_consts.ATTR_BUDGET[attr]
                    * getattr(self, attr))
        return spent_budget

class RoomCommandCheckState(BaseModel):

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='world_check_states')
    cmd_check = models.ForeignKey('builders.RoomCommandCheck',
                                  on_delete=models.CASCADE,
                                  related_name='room_cmd_check_states')
    passed_ts = models.DateTimeField(**optional)


class PlayerEnquire(AdventBaseModel):
    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='player_enquires')
    quest = models.ForeignKey('builders.Quest',
                              on_delete=models.CASCADE,
                              related_name='played_enquires')
    enquire_ts = models.DateTimeField(**optional)


class PlayerQuest(AdventBaseModel):

    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='player_quests')
    quest = models.ForeignKey('builders.Quest',
                              on_delete=models.CASCADE,
                              related_name='player_quests')
    completion_ts = models.DateTimeField(**optional)


class Alias(BaseModel):

    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='aliases')
    match = models.TextField()
    replacement = models.TextField()


class DoorState(BaseModel):
    "SPWs only, track door state"

    door = models.ForeignKey('worlds.Door',
                             on_delete=models.CASCADE,
                             related_name='door_states')
    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='door_states')
    state = models.TextField(choices=list_to_choice(adv_consts.DOOR_STATES),
                             default=adv_consts.DOOR_STATE_CLOSED)



class Mark(BaseModel):

    name = models.TextField()
    value = models.TextField()
    player = models.ForeignKey('Player',
                               on_delete=models.CASCADE,
                               related_name='marks')


class Clan(BaseModel):

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='clans')
    name = models.TextField()
    password = models.TextField(**optional)


class ClanMembership(BaseModel):

    clan = models.ForeignKey('Clan',
                            on_delete=models.CASCADE,
                            related_name='memberships')
    player = models.ForeignKey('Player',
                              on_delete=models.CASCADE,
                              related_name='clan_memberships')
    rank = models.TextField(choices=list_to_choice(adv_consts.CLAN_RANKS),
                            default=adv_consts.CLAN_RANK_MEMBER)
