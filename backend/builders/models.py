from datetime import datetime, timedelta
import random

from croniter import croniter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation)
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from jinja2 import Template
from jinja2.exceptions import TemplateSyntaxError

from core import computations
from backend.core.drops import generation as drops_generation
from config import constants as adv_consts
from core import utils as adv_utils
from core.utils import CamelCase__to__camel_case
from core.utils.items import get_item_budget

from config import constants as api_consts

from core.db import (
    AdventBaseModel,
    AdventWorldBaseModel,
    BaseModel,
    list_to_choice,
    optional)
from core.model_mixins import CharMixin, ItemMixin, MobMixin


class LastViewedRoom(BaseModel):

    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='last_viewed_for')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='last_viewed_for')
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='last_viewed_for')

    class Meta:
        unique_together = ['world', 'user']


class WorldBuilder(AdventBaseModel):

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='world_builders')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='world_builders')
    read_only = models.BooleanField(default=True) # Obsolete
    builder_rank = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ['world', 'user']

    @property
    def name(self):
        return self.user.username or self.user.id


class BuilderAssignment(AdventBaseModel):

    builder = models.ForeignKey(
        'builders.WorldBuilder',
        on_delete=models.CASCADE,
        related_name='builder_assignments')

    assignment_type = models.ForeignKey(ContentType,
                                        on_delete=models.CASCADE,
                                        related_name='assignment_types')
    assignment_id = models.PositiveIntegerField()
    assignment = GenericForeignKey('assignment_type', 'assignment_id')


class ItemTemplate(ItemMixin, AdventBaseModel):

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='item_templates')
    notes = models.TextField(**optional)

    def spawn(self, target, spawn_world, rule=None):
        """
        spawn_world is not target.world because target could be a room,
        in case of which the world will be a context world, which is not
        what we want. It's unfortunately when spawning into an item or a mob
        because it feels like we're passing data that could be passed
        elsewhere, but that's why.
        """
        from spawns.models import Item
        template_fields = {}
        for field in ItemMixin._meta.fields:
            if field.name == 'id':
                continue
            template_fields[field.name] = getattr(self, field.name)

        item = Item.objects.create(
            world=spawn_world,
            container=target,
            template=self,
            rule=rule,
            **template_fields)

        # process template inventory
        for inventory_record in self.template_inventories.all():
            for i in range(0, inventory_record.num_copies):

                if (inventory_record.probability != 100 and
                    not adv_utils.roll_percentage(
                        inventory_record.probability)):
                        continue

                inventory_record.item_template.spawn(
                    target=item,
                    spawn_world=spawn_world)

        return item

    @property
    def budget_spent(self):
        spent_budget = 0
        for attr in adv_consts.ATTRIBUTES:
            if getattr(self, attr):
                spent_budget += (
                    adv_consts.ATTR_BUDGET[attr]
                    * getattr(self, attr))
        return spent_budget

    @property
    def budget_max(self):
        if self.quality == adv_consts.ITEM_QUALITY_NORMAL:
            return 0
        enchanted = False
        if self.quality == adv_consts.ITEM_QUALITY_ENCHANTED:
            enchanted = True
        return get_item_budget(level=self.level,
                               eq_type=self.equipment_type,
                               enchanted=enchanted)

    @property
    def budget(self):
        return get_item_budget(
            level=self.level,
            eq_type=self.equipment_type,
            enchanted=False)

    @staticmethod
    def post_rule_save(sender, **kwargs):
        instance = kwargs.get('instance')
        if (instance
            and instance.type == adv_consts.ITEM_TYPE_EQUIPPABLE
            and not instance.equipment_type):
            instance.equipment_type = adv_consts.EQUIPMENT_TYPE_WEAPON_1H
            instance.save()


models.signals.post_save.connect(ItemTemplate.post_rule_save, ItemTemplate)


class MobTemplate(CharMixin, MobMixin, AdventBaseModel):

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='mob_templates')

    level = models.PositiveIntegerField(default=1)
    name = models.TextField(default='Unnamed Mob')
    description = models.TextField(**optional)
    notes = models.TextField(**optional)
    is_elite = models.BooleanField(default=False)

    assists = models.BooleanField(default=False)

    # Temporary elements, holdovers from previous 'random drops' system,
    # being converted to 'random loads'.
    drops_random_items = models.BooleanField(default=False)
    num_items = models.PositiveIntegerField(default=1)
    chance_normal = models.PositiveIntegerField(default=100)
    chance_imbued = models.PositiveIntegerField(default=20)
    chance_enchanted = models.PositiveIntegerField(default=5)
    load_specification = models.TextField(
        choices=list_to_choice(api_consts.RANDOM_ITEM_SPECIFICATIONS),
        **optional)

    # Crafting fields
    craft_enchanted = models.IntegerField(default=50) # percent chance

    # Upgrade fields
    upgrade_success_chance = models.PositiveIntegerField(default=50)
    upgrade_success_cmd = models.TextField(**optional)
    upgrade_failure_cmd = models.TextField(**optional)

    faction_assignments = GenericRelation(
        'FactionAssignment',
        content_type_field='member_type',
        object_id_field='member_id')

    # If True, this mob will be set to be automatically updated when there
    # are changes to suggested stats.
    default_stats = models.BooleanField(default=False)

    def spawn(self, target, spawn_world, roams=None, rule=None):
        """
        """
        from core.utils.items import type_to_slot
        from builders.random_items import generate_item
        from spawns.models import Mob
        template_fields = {}
        for field in CharMixin._meta.fields:
            if field.name == 'id':
                continue
            template_fields[field.name] = getattr(self, field.name)
        for field in MobMixin._meta.fields:
            if field.name == 'id':
                continue
            template_fields[field.name] = getattr(self, field.name)

        # Runtime spawn state should not be copied from template directly.
        for field_name in ('health', 'stamina', 'mana', 'group_id'):
            template_fields.pop(field_name, None)

        # Template fields can be nullable while spawn fields are not.
        for field_name, value in list(template_fields.items()):
            mob_field = Mob._meta.get_field(field_name)
            if value is None and not mob_field.null:
                if mob_field.empty_strings_allowed:
                    template_fields[field_name] = ''
                elif mob_field.has_default():
                    template_fields[field_name] = mob_field.get_default()

        # See if the mob needs to be assigned a group_id based on the loader
        group_id = None
        if rule and rule.loader.is_group:
            group_id = rule.loader.key
        elif self.assists:
            group_id = self.key

        #roaming_type = roaming or adv_consts.ROAM_STATIC

        mob = Mob.objects.create(
            world=spawn_world,
            room=target,
            template=self,
            **template_fields,
            health=self.health_max,
            stamina=self.stamina_max,
            mana=self.mana_max,
            group_id=group_id,
            #roaming_type=roaming,
            roams=roams,
            rule=rule)

        # Start with equipment profile
        for mob_eq_profile in self.eq_profiles.order_by('priority', 'id'):
            mob_eq_profile.profile.load(mob)

        def equip_if_possible(candidate_item):
            """
            If an item can be equipped, and if the slot is free, equip it.

            Note: closed over function
            """
            if item.type == adv_consts.ITEM_TYPE_EQUIPPABLE:
                slot = type_to_slot(
                    eq_type=item.equipment_type,
                    has_weapon=bool(mob.equipment.weapon),
                    has_offhand=bool(mob.equipment.offhand),
                    archetype=self.archetype)
                if slot and not getattr(mob.equipment, slot, None):
                    mob.equipment.equip(item=item, slot=slot)

        # process random item shortcut
        if self.drops_random_items:
            for i in range(0, self.num_items):
                item = generate_item(
                    char=mob,
                    level=self.level or 1,
                    specification=self.load_specification,
                    chance_imbued=self.chance_imbued,
                    chance_enchanted=self.chance_enchanted,
                    generate_normal=False)
                if item:
                    equip_if_possible(item)

        # process template inventory
        for inventory_record in self.template_inventories.all():
            for i in range(0, inventory_record.num_copies):

                if (inventory_record.probability != 100 and
                    not adv_utils.roll_percentage(
                        inventory_record.probability)):
                        continue

                item = inventory_record.item_template.spawn(
                    target=mob,
                    spawn_world=spawn_world)
                equip_if_possible(item)

        # For every mob, create a corpse item and load it in their
        # inventory.
        mob.create_corpse()

        return mob

    def set_attributes(self):
        """
        For mobs
        """
        stats = computations.compute_stats(
            level=self.level,
            archetype=self.archetype,
            boost_mob=True)
        for stat, value in stats.items():
            setattr(self, stat, value)
        self.save()
        return stats


class TransformationTemplate(AdventBaseModel):
    """
    Apply a transformation to an item or a mob template. Currently only
    works on mobs. Gets applied at animation time.
    """

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='transformation_templates',
                              **optional)

    name = models.TextField()
    transformation_type = models.TextField(
        choices=list_to_choice(api_consts.TRANSFORMATION_TYPES))
    arg1 = models.TextField(**optional)
    arg2 = models.TextField(**optional)

    def apply(self, mob):
        ret_data = {}
        if self.transformation_type == api_consts.TRANSFORMATION_TYPE_ATTR:
            #setattr(mob, self.arg1, self.arg2)
            #mob.save()
            ret_data[self.arg1] = self.arg2
        return ret_data


class DuplicateQuestMob(Exception): pass
MobTemplate.DuplicateQuestMob = DuplicateQuestMob


class MobReaction(AdventBaseModel):
    template = models.ForeignKey('builders.MobTemplate',
                                 on_delete=models.CASCADE,
                                 related_name='reactions')
    event = models.TextField(
        choices=list_to_choice(adv_consts.MOB_REACTION_EVENTS))
    option = models.TextField(**optional)
    reaction = models.TextField()
    conditions = models.TextField(**optional)


class MobReactionCondition(AdventBaseModel):

    reaction = models.ForeignKey('builders.MobReaction',
                                 on_delete=models.CASCADE,
                                 related_name='old_conditions')

    condition = models.TextField(
        choices=list_to_choice(adv_consts.MOB_REACTION_CONDITIONS))

    argument = models.TextField()


class TemplateInventory(AdventBaseModel):
    probability = models.PositiveIntegerField(default=100)
    num_copies = models.PositiveIntegerField(default=1)
    class Meta:
        abstract = True


class MobTemplateInventory(TemplateInventory):
    item_template = models.ForeignKey('builders.ItemTemplate',
                                      on_delete=models.CASCADE,
                                      related_name='inventory_for_mobs')
    container = models.ForeignKey('builders.MobTemplate',
                                  on_delete=models.CASCADE,
                                  related_name='template_inventories')


class ItemTemplateInventory(TemplateInventory):
    item_template = models.ForeignKey('builders.ItemTemplate',
                                      on_delete=models.CASCADE,
                                      related_name='inventory_for_items')
    container = models.ForeignKey('builders.ItemTemplate',
                                  on_delete=models.CASCADE,
                                  related_name='template_inventories')


class Loader(AdventBaseModel):

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='loaders')
    name = models.TextField(default='Unnamed Loader')
    order = models.IntegerField(default=0)

    description = models.TextField(**optional)
    zone = models.ForeignKey('worlds.Zone',
                             on_delete=models.CASCADE,
                             related_name='loaders')

    # If true, all mobs spawning within this loader will be considered
    # belonging to one same group, where
    is_group = models.BooleanField(default=False)

    # When the first removal was since last full load
    last_removal_ts = models.DateTimeField(**optional)
    # Last time we processed rules for this loader
    last_processing_ts = models.DateTimeField(**optional)

    # Seconds. How long to wait before respawning. 0 means never respawn.
    respawn_wait = models.IntegerField(default=300)
    inherit_zone_wait = models.BooleanField(default=True)

    conditions = models.TextField(**optional)

    # Condition based on zone data to determine whether the loader should run
    # Example: north_control == 'orc'
    loader_condition = models.TextField(**optional)

    def run(self, world, force=False, check=True, should_zone_reset=False):
        from spawns.loading import LoaderRun
        return LoaderRun(
            self, world,
            check=check,
            should_zone_reset=should_zone_reset,
        ).execute(force=force)

    # load is deprecated
    load = run

    @staticmethod
    def post_rule_save(sender, **kwargs):
        if kwargs.get('created'):
            instance = kwargs['instance']
            qs = instance.order = instance.__class__.objects.filter(
                zone=instance.zone,
            ).exclude(
                pk=instance.pk
            ).order_by('-order')
            if qs:
                instance.order = qs[0].order + 1
                instance.save()
            else:
                instance.order = 1
                instance.save()

models.signals.post_save.connect(Loader.post_rule_save, Loader)


class Rule(AdventBaseModel):

    loader = models.ForeignKey('builders.Loader',
                               on_delete=models.CASCADE,
                               related_name='rules')

    # Item Template or Mob Template
    template_type = models.ForeignKey(ContentType, on_delete=models.CASCADE,
                                      related_name='template_types')
    template_id = models.PositiveIntegerField()
    template = GenericForeignKey('template_type', 'template_id')

    # Room, Rule, Zone or None
    target_type = models.ForeignKey(ContentType, on_delete=models.CASCADE,
                                    related_name='target_types', **optional)
    target_id = models.PositiveIntegerField(**optional)
    target = GenericForeignKey('target_type', 'target_id')

    order = models.IntegerField(default=0)
    num_copies = models.IntegerField(default=1)

    options = models.TextField(**optional)

    @property
    def name(self):
        _name = self.key
        if self.template:
            _name += " (%s)" % self.template.name
        return _name

    @staticmethod
    def post_rule_save(sender, **kwargs):
        if kwargs.get('created'):
            instance = kwargs['instance']
            qs = instance.order = instance.__class__.objects.filter(
                loader=instance.loader,
            ).exclude(
                pk=instance.pk
            ).order_by('-order')
            if qs:
                instance.order = qs[0].order + 1
                instance.save()
            else:
                instance.order = 1
                instance.save()

models.signals.post_save.connect(Rule.post_rule_save, Rule)


class RoomCommandCheck(AdventBaseModel):
    """
    For single-player worlds only, at least if the intent is to track states.
    This is because they'll be tied to the world and therefore the player,
    otherwise there would need to be one record per world-player pair,
    which for a temporary architectural component would be a lot more work
    than needed.

    Command checks happen before a command is ran. Checks are based off
    commands as specified with the `allow_commands` and `disallow_commands`
    arguments. They can both be empty, which will mean, 'disallow all', but
    they cannot both be defined or an error should be raised.

    Both allow_commands and disallow_commands takes either a simple string or
    a JSON dumped string, either way just a text field.

    Currently supported checks:
    - in_inv : argument is template ID
    """

    name = models.TextField(**optional)

    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='cmd_checks')

    # All commands except the ones listed will be allowed
    disallow_commands = models.TextField(**optional)
    # All commands except the ones listed will be disallowed
    allow_commands = models.TextField(**optional)

    check_type = models.TextField(choices=list_to_choice(adv_consts.CMD_CHECKS), db_column='check')
    argument = models.TextField(blank=True)

    failure_msg = models.TextField()
    hint_msg = models.TextField(**optional)
    #success_msg = models.TextField(**optional)


    track_state = models.BooleanField(default=False)


class RoomCheck(AdventBaseModel):
    name = models.TextField(**optional)
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='room_checks')
    # Useful for exit prevents, but optional
    direction = models.TextField(
        choices=list_to_choice(adv_consts.DIRECTIONS), **optional)

    prevent = models.TextField(
        choices=list_to_choice(adv_consts.ROOM_PREVENTS))

    check_type = models.TextField(
        choices=list_to_choice(adv_consts.ROOM_CHECKS), db_column='check', **optional)
    argument = models.TextField(blank=True)
    argument2 = models.TextField(blank=True)

    failure_msg = models.TextField(**optional)

    conditions = models.TextField(**optional)


class RoomGetTrigger(AdventBaseModel):
    name = models.TextField(**optional)
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='get_triggers')
    # The item template to pick up
    argument = models.TextField(**optional)
    action = models.TextField(choices=list_to_choice(
                                        adv_consts.ROOM_TRIGGER_ACTIONS))
    action_argument = models.TextField(**optional)
    message = models.TextField(**optional)


class Trigger(AdventBaseModel):
    """
    WR2 authored trigger definition.

    This model is intentionally generic so a trigger can target room/zone/world
    scopes now and later expand to other authored entities.
    """

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='triggers')

    target_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name='trigger_target_types',
        **optional)
    target_id = models.PositiveIntegerField(**optional)
    target = GenericForeignKey('target_type', 'target_id')

    scope = models.TextField(
        choices=list_to_choice(api_consts.TRIGGER_SCOPES),
        default=api_consts.TRIGGER_SCOPE_ROOM,
    )
    kind = models.TextField(
        choices=list_to_choice(api_consts.TRIGGER_KINDS),
        default=api_consts.TRIGGER_KIND_COMMAND,
    )

    name = models.TextField(**optional)
    actions = models.TextField(**optional)
    script = models.TextField(**optional)
    conditions = models.TextField(**optional)
    event = models.TextField(
        choices=list_to_choice(api_consts.MOB_REACTION_EVENTS),
        **optional,
    )
    option = models.TextField(**optional)

    show_details_on_failure = models.BooleanField(default=False)
    failure_message = models.TextField(**optional)
    display_action_in_room = models.BooleanField(default=True)

    # 0: no gate; >0: seconds; -1: one-shot.
    gate_delay = models.IntegerField(default=10)

    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)


class ActionBase(AdventBaseModel):

    name = models.TextField(**optional)
    actions = models.TextField()
    commands = models.TextField()
    conditions = models.TextField(**optional)
    show_details_on_failure = models.BooleanField(default=False)
    failure_message = models.TextField(**optional)
    display_action_in_room = models.BooleanField(default=True)

    gate_delay = models.IntegerField(default=10)

    class Meta:
        abstract = True


class RoomAction(ActionBase):

    room = models.ForeignKey('worlds.Room',
                         related_name='room_actions',
                         on_delete=models.CASCADE)


class ItemAction(ActionBase):

    item_template = models.ForeignKey('builders.ItemTemplate',
                                      related_name='item_actions',
                                      on_delete=models.CASCADE)


class Quest(AdventWorldBaseModel):

    name = models.TextField(**optional)
    # Optional builder instructions to players who look at this quest in their
    # quest log
    summary = models.TextField(**optional)
    notes = models.TextField(**optional)
    # suggested level
    level = models.PositiveIntegerField(default=0)

    type = models.TextField(choices=list_to_choice(adv_consts.QUEST_TYPES),
                            **optional)

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='world_quests')
    zone = models.ForeignKey('worlds.Zone',
                             on_delete=models.SET_NULL,
                             related_name='zone_quests',
                             **optional)

    mob_template = models.ForeignKey(MobTemplate,
                                     on_delete=models.PROTECT,
                                     related_name='template_quests')

    # Pre-requisites
    requires_quest = models.ForeignKey('builders.Quest',
                                        on_delete=models.SET_NULL,
                                        related_name='prereq_quests',
                                        **optional)
    requires_level = models.PositiveIntegerField(default=0)

    max_level = models.PositiveIntegerField(default=0)

    # How long to wait before the quest can be repeated again.
    # 0: always repeatable, -1: never repeatable
    repeatable_after = models.IntegerField(default=0)

    conditions = models.TextField(**optional)
    completion_conditions = models.TextField(**optional)

    is_setup = models.BooleanField(default=False)

    # Whether the quest appears in the quest log
    is_logged = models.BooleanField(default=True)

    # If true, the mob will only respond to keywords.
    is_hidden = models.BooleanField(default=False)

    # Commands to execute when a player that qualifies for the quest enters
    # the room.
    entrance_cmds = models.TextField(**optional)
    # Configure how long to wait before allowing a mob to re-issue an entrance
    # command. Expressed in seconds.
    # 0: always repeat, -1: never repeat right away
    repeat_entrance_cmd_after = models.IntegerField(default=10)

    enquire_cmds = models.TextField(**optional)

    # Wether the enquire command is available.
    enquire_cmd_available = models.BooleanField(default=True)

    # Which keywords, if any, will trigger the mob its enquire sequence.
    enquire_keywords = models.TextField(**optional)

    # Other key words that can be used to complete the action. Can enter
    # multiple by seperating them with 'or' for example:
    # cut web or cut webbing or cut silk
    completion_action = models.TextField(**optional)
    completion_cmd_available = models.BooleanField(default=True)
    completion_entrance_cmds = models.TextField(**optional)
    repeat_completion_entrance_cmds_after = models.IntegerField(default=10)
    completion_cmds = models.TextField(**optional)
    completion_keywords = models.TextField(**optional)
    completion_despawn = models.IntegerField(default=0)
    # If this is true, the frontend will not display completion text when
    # completing a quest. It will however still list received rewards,
    # and therefore really only makes sense to use on quests that have no
    # rewards.
    complete_silently = models.BooleanField(default=False)

    # Below Not currently used

    incomplete_msg = models.TextField(**optional)

    # Commands to execute when a player tries to enquire and the repeatable
    # window hasn't been hit yet.
    wait_until_cmds = models.TextField(**optional)

    @property
    def key(self):
        # Since quests can be referenced in arguments, they
        # need to be addressable by ID and not relative IDs.
        # But because we don't want to make the quest
        # non-relative quite yet, we're changing how the key
        # gets computed
        return '%s.%s' % (
            CamelCase__to__camel_case(self.__class__.__name__),
            self.id)

    def get_game_key(self, spawn_world=None):
        if spawn_world is None:
            if self.world.is_multiplayer:
                spawn_world = self.world.spawned_worlds.get(
                    is_multiplayer=True)
            else:
                raise ValueError('spawn_world is required.')
        return '@{world_id}:{model}.{id}'.format(
            world_id=spawn_world.pk,
            model=self.get_class_name(),
            id=self.id)

    def update_live_instances(self):
        return


Quest.connect_relative_id_post_save_signal()


class Objective(AdventBaseModel):

    quest = models.ForeignKey(Quest,
                              on_delete=models.CASCADE,
                              related_name='objectives')
    type = models.TextField(choices=list_to_choice(adv_consts.OBJECTIVE_TYPES))
    qty = models.IntegerField(default=1)

    template_type = models.ForeignKey(ContentType,
                                      on_delete=models.SET_NULL,
                                      related_name='template_objectives',
                                      **optional)
    template_id = models.PositiveIntegerField(**optional)
    template = GenericForeignKey('template_type', 'template_id')

    currency = models.ForeignKey('builders.Currency',
                                 on_delete=models.SET_NULL,
                                 related_name='currency_objectives',
                                 **optional)


class Reward(AdventBaseModel):

    quest = models.ForeignKey(Quest,
                              on_delete=models.CASCADE,
                              related_name='rewards')
    type = models.TextField(choices=list_to_choice(adv_consts.REWARD_TYPES))
    qty = models.IntegerField(default=1)
    #option = models.TextField(**optional)

    # Reward profile can currently be two things:
    # - an item template, which will spawn an item of that template as a reward
    # - a random item profile, which will span a random item
    # - faction, which combined with the qty field will define a change in
    #   faction standing.
    profile_type = models.ForeignKey(ContentType,
                                      on_delete=models.CASCADE,
                                      related_name='for_rewards',
                                      **optional)
    profile_id = models.PositiveIntegerField(**optional)
    profile = GenericForeignKey('profile_type', 'profile_id')

    currency = models.ForeignKey('builders.Currency',
                                 on_delete=models.SET_NULL,
                                 related_name='currency_rewards',
                                 **optional)


class EquipmentProfile(models.Model):
    """
    Profile of equipment to load with a mob. Can be applied to
    multiple mobs.
    """
    name = models.TextField(**optional)
    level = models.PositiveIntegerField(default=0)
    notes = models.TextField(**optional)

    def __str__(self):
        return self.name

    def load(self, mob):
        from spawns.models import Item

        items = []

        for slot in self.slots.all():

            # If an item template is defined, just spawn it
            if slot.item_template:
                item = slot.item_template.spawn(mob, mob.world)
                items.append(item)
                continue

            # Otherwise, we're procedurally generating an item

            level = (
                slot.level or self.level
                or (mob.template.level if mob.template else mob.level))

            if adv_utils.roll_percentage(slot.chance_enchanted):
                quality = adv_consts.ITEM_QUALITY_ENCHANTED
            elif adv_utils.roll_percentage(slot.chance_imbued):
                quality = adv_consts.ITEM_QUALITY_IMBUED
            else:
                quality = adv_consts.ITEM_QUALITY_NORMAL

            if slot.slot_name == adv_consts.EQUIPMENT_SLOT_WEAPON:
                attrs = drops_generation.generate_weapon(
                    level=level,
                    quality=quality,
                    eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)

            elif slot.slot_name == adv_consts.EQUIPMENT_SLOT_OFFHAND:
                attrs = drops_generation.generate_shield(
                    level=level,
                    quality=quality)

            else: # armor

                if slot.is_heavy:
                    armor_class = adv_consts.ARMOR_CLASS_HEAVY
                else:
                    armor_class = adv_consts.ARMOR_CLASS_LIGHT

                attrs = drops_generation.generate_armor(
                    level=level,
                    quality=quality,
                    eq_type=slot.slot_name,
                    armor_class=armor_class)

            item = Item.objects.create(
                world=mob.world,
                quality=quality,
                level=level,
                type=adv_consts.ITEM_TYPE_EQUIPPABLE,
                container=mob.equipment,
                **attrs)
            items.append(item)

            # See if the mob already has something equipped in that slot, and
            # if so delete the item (so that stacking multiple profiles
            # doesn't yield orphan items)
            existing_item = getattr(mob.equipment, slot.slot_name, None)
            if existing_item:
                existing_item.delete()

            mob.equipment.equip(item=item, slot=slot.slot_name)
            #setattr(mob.equipment, slot.slot_name, item)
            #mob.equipment.save()

        mob.save()
        return items


class MerchantInventory(models.Model):
    """
    Note: current soft limit of 1 random item profile per merchant.
    This can be removed once we start tracking which profiles generated
    procedural items so they can be mapped against that.
    """
    mob = models.ForeignKey('builders.MobTemplate',
                            on_delete=models.CASCADE,
                            related_name='merchant_inv')
    item_template = models.ForeignKey('builders.ItemTemplate',
                                      on_delete=models.CASCADE,
                                      **optional)
    random_item_profile = models.ForeignKey('builders.RandomItemProfile',
                                       on_delete=models.CASCADE,
                                       **optional)
    num = models.PositiveIntegerField(default=1)


class EquipmentSlot(models.Model):
    """
    Definition of what should load in a mob's eqipment slot. Can either be
    an item template or a procedurally generated item.

    If an item template is defined, the slot will be loaded with an instance
    of that template.

    If it is none, a procedurally generated item will be inserted instead,
    based off the mob's level if one is not specified.
    """
    profile = models.ForeignKey(EquipmentProfile,
                                on_delete=models.CASCADE,
                                related_name='slots')

    slot_name = models.TextField(
        choices=list_to_choice(adv_consts.EQUIPMENT_SLOTS))

    item_template = models.ForeignKey('builders.ItemTemplate',
                                      on_delete=models.CASCADE,
                                      **optional)

    # 0 means take the level of the mob
    level = models.PositiveIntegerField(default=0)
    is_heavy = models.BooleanField(default=False)
    chance_imbued = models.PositiveIntegerField(default=0)
    chance_enchanted = models.PositiveIntegerField(default=0)


class MobEquipmentProfile(models.Model):
    "M2M table for Mob to EquipmentProfiles"

    # In which order to apply the profiles. If for example a bandit boss
    # mob is wearing all the basic bandit gear + some enchanted slot,
    # the profile with the enchanted slot should have a higher priority.
    priority = models.PositiveIntegerField(default=0)

    mob = models.ForeignKey('builders.MobTemplate',
                            on_delete=models.CASCADE,
                            related_name='eq_profiles')

    profile = models.ForeignKey('builders.EquipmentProfile',
                                on_delete=models.CASCADE,
                                related_name='mobprofiles')


class RandomItemProfile(AdventBaseModel):
    """
    Definition for a random item, as used with random loads, quest rewards,
    merchant items.
    """

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='random_item_profiles',
                              **optional)

    name = models.TextField(**optional)

    # 0 means it will look at the quest giver's level
    level = models.PositiveIntegerField(default=0)
    chance_imbued = models.PositiveIntegerField(default=20)
    chance_enchanted = models.PositiveIntegerField(default=5)
    restriction = models.TextField(
        choices=list_to_choice(api_consts.RANDOM_ITEM_SPECIFICATIONS),
        **optional)

    def __str__(self): return self.name

    def generate(self, char, default_level=None, for_archetype=False):
        from builders.random_items import generate_item
        item = generate_item(
            char=char,
            level=self.level or default_level or 1,
            specification=self.restriction,
            chance_imbued=self.chance_imbued,
            chance_enchanted=self.chance_enchanted,
            for_archetype=for_archetype)
        item.profile = self
        item.save()
        return item


class RoomBlock(BaseModel):
    "Block of rooms to use for moving"

    name = models.TextField()
    rooms = models.ManyToManyField('worlds.Room', related_name='blocks')


class HousingBlock(AdventBaseModel):
    "Group of houses purchasable by a player"

    name = models.TextField()
    price = models.IntegerField()
    owner = models.ForeignKey('spawns.Player',
                              related_name='housing_blocks',
                              on_delete=models.SET_NULL,
                              **optional)
    purchase_ts = models.DateTimeField(**optional)


class HousingLease(AdventBaseModel):
    "Historical records"

    block = models.ForeignKey(HousingBlock,
                              related_name='block_leases',
                              on_delete=models.CASCADE)
    owner = models.ForeignKey('spawns.Player',
                              related_name='housing_leases',
                              on_delete=models.SET_NULL,
                              **optional)
    price = models.IntegerField()


class Faction(AdventBaseModel):

    code = models.TextField()
    name = models.TextField()
    notes = models.TextField(**optional)
    description = models.TextField(**optional)
    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='world_factions')
    is_core = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    # applicable to core faction only, allows creation of a core faction
    # that players cannot start as.
    is_selectable = models.BooleanField(default=True)

    starting_room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.SET_NULL,
        related_name='starting_room_for_factions',
        **optional)

    death_room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.SET_NULL,
        related_name='death_room_for_factions',
        **optional)

    death_rooms = models.ManyToManyField('worlds.Room',
                                         through='builders.Procession')

    def __str__(self):
        return "%s in %s" % (self.name, self.world.name)


class FactionRank(BaseModel):
    faction = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='ranks')
    standing = models.IntegerField()
    name = models.TextField()


class FactionAssignment(BaseModel):
    "Assignment of faction to either player character or mob template."

    faction = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='assignments_for')
    value = models.IntegerField(default=0)

    member_type = models.ForeignKey(ContentType,
                                     on_delete=models.CASCADE,
                                     related_name='faction_assignments',
                                     **optional)
    member_id = models.PositiveIntegerField(**optional)
    member = GenericForeignKey('member_type', 'member_id', )

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['member_type', 'member_id', 'faction'],
                name='builders_member_faction_assignment_unique',
            ),
        ]

    def clean(self):
        super().clean()

        if not self.member_type_id or not self.member_id or not self.faction_id:
            return

        # Guard against multiple core-faction assignments for a single member.
        if Faction.objects.filter(pk=self.faction_id, is_core=True).exists():
            qs = FactionAssignment.objects.filter(
                member_type_id=self.member_type_id,
                member_id=self.member_id,
                faction__is_core=True,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    'Member already has a core faction assignment.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FactionRelationship(BaseModel):
    """
    Model to define which factions are friendly or hostile towards each other.
    """
    faction = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='relationships_from')
    towards = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='relationships_to')
    standing = models.IntegerField(default=0)


class PathRoom(models.Model):

    path = models.ForeignKey('builders.Path',
                             related_name='path_rooms',
                             on_delete=models.CASCADE)
    room = models.ForeignKey('worlds.Room',
                             related_name='room_paths',
                             on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'builders_path_rooms'
        unique_together = (('path', 'room'),)


class Path(AdventWorldBaseModel):
    """
    A Path is a way to define a grouping of rooms, primarily for the purposes
    of mobs roaming them.
    """

    name = models.TextField()
    notes = models.TextField(**optional)

    world = models.ForeignKey('worlds.World',
                              related_name='paths',
                              on_delete=models.CASCADE)

    zone = models.ForeignKey('worlds.Zone',
                             related_name='paths',
                             on_delete=models.CASCADE,
                             **optional)

    rooms = models.ManyToManyField('worlds.Room', through='builders.PathRoom')
    #rooms = models.ManyToManyField('worlds.Room')

    # For both maxes, None means unlimited. 0 would not really make sense
    # for either, as it would mandate an empty path, and then what's the
    # point?
    max_per_room = models.PositiveIntegerField(**optional)
    max_per_path = models.PositiveIntegerField(**optional)

    entry_room = models.ForeignKey('worlds.Room',
                                   related_name='entry_for_paths',
                                   on_delete=models.SET_NULL,
                                   **optional)

    @property
    def key(self):
        return '%s.%s' % (
            CamelCase__to__camel_case(self.__class__.__name__),
            self.id)

    def update_live_instances(self):
        return
        path = self

        running_worlds = path.world.get_running_worlds()
        if not running_worlds.count():
            return path

        for spawn_world in running_worlds:
            pass


Path.connect_relative_id_post_save_signal()


class Procession(AdventBaseModel):

    faction = models.ForeignKey('builders.Faction',
                                related_name='faction_processions',
                                on_delete=models.CASCADE)
    room = models.ForeignKey('worlds.Room',
                             related_name='room_processions',
                             on_delete=models.CASCADE)

    class Meta:
        unique_together = (('faction', 'room'),)

    def update_live_instances(self):
        return
        running_worlds = self.room.world.get_running_worlds()
        if not running_worlds.count(): return
        for spawn_world in running_worlds:
            pass

class FactSchedule(BaseModel):

    world = models.ForeignKey('worlds.World',
                              related_name='fact_schedules',
                              on_delete=models.CASCADE)

    name = models.TextField()

    selection = models.TextField(
        choices=list_to_choice(api_consts.FACT_SCHEDULE_SELECTIONS),
        default=api_consts.FACT_SCHEDULE_SELECTION_DEFAULT)

    fact = models.TextField()
    value = models.TextField()

    schedule = models.TextField()
    schedule_type = models.TextField(
        choices=list_to_choice(api_consts.FACT_SCHEDULE_SCHEDULES),
        default=api_consts.FACT_SCHEDULE_SCHEDULE_INTERVAL)

    change_msg = models.TextField(default='')

    next_run_ts = models.DateTimeField(**optional)

    def set_next_run(self):
        now = timezone.now()

        if self.schedule_type == api_consts.FACT_SCHEDULE_SCHEDULE_INTERVAL:
            last_run = self.next_run_ts or now
            try:
                delay = timedelta(seconds=int(self.schedule))
            except OverflowError:
                delay = timedelta(seconds=2000000000)
            if last_run + delay > now:
                self.next_run_ts = last_run + delay
            else:
                self.next_run_ts = now + delay
            self.save()
        elif self.schedule_type == api_consts.FACT_SCHEDULE_SCHEDULE_CRON:
            self.next_run_ts = croniter(self.schedule, now).get_next(datetime)
            self.save()
        else:
            raise ValueError(f"Unknown schedule type {self.schedule_type}.")

    def run(self, facts):
        "Runs the schedule, returning one k/v pair if an update took place."

        if self.fact not in facts:
            old_value = ''
        else:
            old_value = facts[self.fact]

        values = self.value.lower().split()

        if self.selection == api_consts.FACT_SCHEDULE_SELECTION_CYCLE:
            try:
                current_index = values.index(old_value)
                next_index = current_index + 1
                new_value = values[next_index]
            except (ValueError, IndexError):
                new_value = values[0]
        elif self.selection == api_consts.FACT_SCHEDULE_SELECTION_RANDOM:
            new_value = random.choice(values)
        else:
            new_value = values[0]

        msg = ''
        if self.change_msg:
            try:
                raw_template = Template(self.change_msg)
                msg = adv_utils.capfirst(
                    raw_template.render({
                        'fact': self.fact,
                        'old_value': old_value,
                        'new_value': new_value,
                    }))
            except TemplateSyntaxError:
                pass

        result = {
            'fact': self.fact,
            'old_value': old_value,
            'new_value': new_value,
            'msg': msg,
        }
        return result


class Skill(BaseModel):

    """
    code
    name level

    cast_time cooldown
    cost cost_type cost_calc


    damage damage_type

    effect effect_duration
    effect_damage effect_damage_type

    consumes requires
    """

    world = models.ForeignKey('worlds.World',
                              related_name='skills',
                              on_delete=models.CASCADE)

    code = models.TextField()
    name = models.TextField(**optional)
    level = models.IntegerField(default=0)

    arguments = models.TextField(**optional)

    cost = models.FloatField(default=0)
    cost_type = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_COST_TYPES),
        default=adv_consts.SKILL_COST_TYPE_MANA)
    cost_calc = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_COST_CALCS),
        default=adv_consts.SKILL_COST_CALC_PERC_BASE)

    damage = models.FloatField(default=0)
    damage_type = models.TextField(
        choices=list_to_choice(adv_consts.DAMAGE_TYPES),
        default=adv_consts.DAMAGE_TYPE_PHYSICAL)
    damage_calc = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_DAMAGE_CALCS),
        default=adv_consts.SKILL_DAMAGE_CALC_NORMAL)

    cast_time = models.FloatField(default=0)
    cooldown = models.FloatField(default=0)

    effect = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_EFFECTS),
        **optional)
    effect_damage = models.FloatField(default=0)
    effect_duration = models.FloatField(default=0)
    effect_damage_type = models.TextField(
        choices=list_to_choice(adv_consts.DAMAGE_TYPES),
        default=adv_consts.DAMAGE_TYPE_MAGICAL)
    effect_damage_calc = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_DAMAGE_CALCS),
        default=adv_consts.SKILL_DAMAGE_CALC_NORMAL)

    intent = models.TextField(
        choices=list_to_choice(adv_consts.SKILL_INTENTS),
        default=adv_consts.SKILL_INTENT_DAMAGE)

    consumes = models.ForeignKey(
        'builders.ItemTemplate',
        related_name='consumers',
        on_delete=models.SET_NULL,
        **optional)
    requires = models.TextField(**optional)
    learn_conditions = models.TextField(**optional)

    help = models.TextField(**optional)

    class Meta:
        unique_together = ['world_id', 'code']


class WorldReview(BaseModel):
    """
    A World Review can be initiated by any a world's builders. They start out
    in 'submitted' status with no reviewer. Once a staff user picks it up, they
    get assigned as the reviewer. After that, the reviewer can move the status
    to either:
    - approved
    - reviewed (nice way of saying rejected. Must include a review comment).

    The review can then be placed back in 'submitted' status from 'reviewed'.
    """
    world = models.ForeignKey(
        'worlds.World',
        related_name='world_reviews',
        on_delete=models.CASCADE)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='author_reviews',
        on_delete=models.SET_NULL,
        **optional)

    # The description of the world
    description = models.TextField(**optional)

    # Review text left by the reviewer
    text = models.TextField(**optional)
    status = models.TextField(
        choices=list_to_choice(api_consts.WORLD_REVIEW_STATUSES),
        default=api_consts.WORLD_REVIEW_STATUS_SUBMITTED)


class BuilderAction(BaseModel):

    action = models.TextField(
        choices=list_to_choice(api_consts.BUILDER_ACTIONS))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='builder_user_actions',
        on_delete=models.CASCADE)

    world = models.ForeignKey(
        'worlds.World',
        related_name='builder_world_actions',
        on_delete=models.CASCADE)

    outcome = models.TextField(**optional)
    notes = models.TextField(**optional)

    def note(self, note):
        if not self.notes:
            self.notes = ''
        self.notes += note + '\n'
        self.save()

    def fail(self, outcome='failure', note=''):
        self.outcome = outcome
        if note:
            self.note(note)
        self.save()

    def succeed(self, outcome='success', note=''):
        self.outcome = outcome
        if note:
            self.note(note)
        self.save()


class Social(BaseModel):

    world = models.ForeignKey('worlds.World',
                              related_name='socials',
                              on_delete=models.CASCADE)
    cmd = models.TextField()
    priority = models.PositiveIntegerField(default=0)
    msg_targetless_self = models.TextField(**optional)
    msg_targetless_other = models.TextField(**optional)
    msg_targeted_self = models.TextField(**optional)
    msg_targeted_target = models.TextField(**optional)
    msg_targeted_other = models.TextField(**optional)

    class Meta:
        unique_together = ['world', 'cmd']

def post_social_save(sender, **kwargs):
    from spawns.serializers import AnimateWorldSerializer
    world = kwargs['instance'].world
    for spawn_world in world.spawned_worlds.filter(
        lifecycle=api_consts.WORLD_STATE_RUNNING):
        socials = AnimateWorldSerializer(
            spawn_world,
        ).data['socials']
        spawn_world.game_world.socials = socials

models.signals.post_save.connect(post_social_save, Social)


class Currency(BaseModel):

    world = models.ForeignKey('worlds.World',
                              related_name='currencies',
                              on_delete=models.CASCADE)

    code = models.TextField()
    name = models.TextField()
    is_default = models.BooleanField(default=False, db_index=True)

    class Meta:
        unique_together = ['world', 'code']
