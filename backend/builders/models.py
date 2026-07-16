from datetime import datetime, timedelta
import random

from croniter import croniter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation)
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from jinja2 import Template
from jinja2.exceptions import TemplateSyntaxError

from config import constants as adv_consts
from core import utils as adv_utils
from core.utils import CamelCase__to__camel_case

from config import constants as api_consts

from core.db import (
    AdventBaseModel,
    AdventWorldBaseModel,
    BaseModel,
    list_to_choice,
    optional)


FACTION_TYPE_CORE = 'core'
FACTION_TYPE_REPUTATION = 'reputation'
FACTION_TYPES = (
    FACTION_TYPE_CORE,
    FACTION_TYPE_REPUTATION,
)

FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION = 'mob_definition'


def _generate_unique_world_slug(instance, *, fallback_prefix: str) -> str:
    base_text = getattr(instance, "name", "") or fallback_prefix
    base_slug = slugify(base_text) or fallback_prefix
    model_cls = instance.__class__
    world_id = getattr(instance, "world_id", None)
    if not world_id:
        return base_slug

    slug = base_slug
    counter = 2
    qs = model_cls.objects.filter(world_id=world_id)
    if instance.pk:
        qs = qs.exclude(pk=instance.pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


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


class ItemDefinition(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='item_definitions')
    slug = models.SlugField(max_length=120, blank=True)
    name = models.TextField(default='Unnamed Item')
    description = models.TextField(**optional)
    ground_description = models.TextField(**optional)
    keywords = models.TextField(**optional)
    notes = models.TextField(**optional)
    item_type = models.TextField(
        choices=list_to_choice(adv_consts.ITEM_TYPES),
        default=adv_consts.ITEM_TYPE_INERT)
    base_properties = models.JSONField(default=dict, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]

    def save(self, *args, **kwargs):
        is_create = self._state.adding
        if not self.slug:
            self.slug = _generate_unique_world_slug(
                self,
                fallback_prefix="item-definition",
            )
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(dict.fromkeys([
                *kwargs["update_fields"],
                "modified_ts",
            ]))
        super().save(*args, **kwargs)
        if not is_create:
            from builders.item_definitions import sync_spawned_items_from_definition

            sync_spawned_items_from_definition(self)

    def spawn(self, target, spawn_world, rule=None, rng=None):
        from builders.item_definitions import spawn_item_from_definition

        return spawn_item_from_definition(
            self,
            target,
            spawn_world,
            rng=rng,
            rule=rule,
        )


class ItemBundle(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='item_bundles')
    slug = models.SlugField(max_length=120, blank=True)
    name = models.TextField(default='Unnamed Item Bundle')
    notes = models.TextField(**optional)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _generate_unique_world_slug(
                self,
                fallback_prefix="item-bundle",
            )
        super().save(*args, **kwargs)

    def spawn(self, target, spawn_world, rule=None, rng=None):
        from builders.item_definitions import spawn_item_from_bundle

        return spawn_item_from_bundle(
            self,
            target,
            spawn_world,
            rng=rng,
            rule=rule,
        )


class ItemBundleEntry(AdventBaseModel):
    bundle = models.ForeignKey(
        'builders.ItemBundle',
        on_delete=models.CASCADE,
        related_name='entries')
    item_definition = models.ForeignKey(
        'builders.ItemDefinition',
        on_delete=models.CASCADE,
        related_name='bundle_entries')
    weight = models.PositiveIntegerField(default=1)
    min_quantity = models.PositiveIntegerField(default=1)
    max_quantity = models.PositiveIntegerField(default=1)
    probability = models.PositiveIntegerField(default=100)

    class Meta(AdventBaseModel.Meta):
        ordering = ['created_ts', 'id']


class MerchantProfile(AdventBaseModel):
    FUNDS_MODE_UNLIMITED = "unlimited"
    FUNDS_MODE_FINITE = "finite"
    FUNDS_MODES = [
        FUNDS_MODE_UNLIMITED,
        FUNDS_MODE_FINITE,
    ]
    BUYBACK_EXPIRES_ON_RESTOCK = "on_restock"
    BUYBACK_EXPIRES_OPTIONS = [
        BUYBACK_EXPIRES_ON_RESTOCK,
    ]

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='merchant_profiles')
    slug = models.SlugField(max_length=120, blank=True)
    name = models.TextField(default='Unnamed Merchant')
    notes = models.TextField(**optional)

    sell_markup = models.FloatField(default=1.0)
    buy_multiplier = models.FloatField(default=0.4)

    restock_interval_seconds = models.PositiveIntegerField(**optional)

    funds_mode = models.TextField(
        choices=list_to_choice(FUNDS_MODES),
        default=FUNDS_MODE_UNLIMITED)
    funds_currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.SET_NULL,
        related_name='merchant_funds_profiles',
        **optional)
    purchase_budget = models.PositiveIntegerField(default=0)

    buyback_enabled = models.BooleanField(default=False)
    buyback_max_items = models.PositiveIntegerField(default=0)
    buyback_expires = models.TextField(
        choices=list_to_choice(BUYBACK_EXPIRES_OPTIONS),
        default=BUYBACK_EXPIRES_ON_RESTOCK)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _generate_unique_world_slug(
                self,
                fallback_prefix="merchant-profile",
            )
        super().save(*args, **kwargs)


class MerchantStockSlot(AdventBaseModel):
    SOURCE_ITEM_DEFINITION = "item_definition"
    SOURCE_ITEM_BUNDLE = "item_bundle"
    REFRESH_FILL_MISSING = "fill_missing"
    REFRESH_REROLL_ON_RESTOCK = "reroll_on_restock"
    REFRESH_MODES = [
        REFRESH_FILL_MISSING,
        REFRESH_REROLL_ON_RESTOCK,
    ]

    profile = models.ForeignKey(
        'builders.MerchantProfile',
        on_delete=models.CASCADE,
        related_name='stock_slots')
    key = models.SlugField(max_length=120)
    item_definition = models.ForeignKey(
        'builders.ItemDefinition',
        on_delete=models.CASCADE,
        related_name='merchant_stock_slots',
        **optional)
    item_bundle = models.ForeignKey(
        'builders.ItemBundle',
        on_delete=models.CASCADE,
        related_name='merchant_stock_slots',
        **optional)
    count = models.PositiveIntegerField(default=1)
    refresh = models.TextField(
        choices=list_to_choice(REFRESH_MODES),
        default=REFRESH_FILL_MISSING)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('profile', 'key')]
        ordering = ['created_ts', 'id']


class MobDefinition(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='mob_definitions')
    slug = models.SlugField(max_length=120, blank=True)
    name = models.TextField(default='Unnamed Mob')
    description = models.TextField(**optional)
    room_description = models.TextField(**optional)
    keywords = models.TextField(**optional)
    notes = models.TextField(**optional)
    mob_type = models.TextField(
        choices=list_to_choice(adv_consts.MOB_TYPES),
        default=adv_consts.MOB_TYPE_BEAST)
    assists = models.BooleanField(default=False)
    base_properties = models.JSONField(default=dict, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)
    traits = models.JSONField(default=list, blank=True)
    loot = models.JSONField(default=dict, blank=True)
    combat_abilities = models.JSONField(default=list, blank=True)
    attackable = models.BooleanField(default=True)
    merchant_profile = models.ForeignKey(
        'builders.MerchantProfile',
        on_delete=models.SET_NULL,
        related_name='mob_definitions',
        **optional)
    merchant_availability = models.TextField(
        default='present',
        blank=True)
    trainer = models.JSONField(default=dict, blank=True)
    faction_assignments = GenericRelation(
        'FactionAssignment',
        content_type_field='member_type',
        object_id_field='member_id')

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]

    def save(self, *args, **kwargs):
        is_create = self._state.adding
        if not self.slug:
            self.slug = _generate_unique_world_slug(
                self,
                fallback_prefix="mob-definition",
            )
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(dict.fromkeys([
                *kwargs["update_fields"],
                "modified_ts",
            ]))
        super().save(*args, **kwargs)
        if not is_create:
            from builders.mob_definitions import sync_spawned_mobs_from_definition

            sync_spawned_mobs_from_definition(self)

    def spawn(self, target, spawn_world, roams=None, rule=None, rng=None):
        from builders.mob_definitions import spawn_mob_from_definition

        return spawn_mob_from_definition(
            self,
            target,
            spawn_world,
            rng=rng,
            roams=roams,
            rule=rule,
        )


class TransformationTemplate(AdventBaseModel):
    """
    Apply a transformation to authored spawn output. Currently only
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


class SpawnPlan(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='spawn_plans')
    zone = models.ForeignKey(
        'worlds.Zone',
        on_delete=models.CASCADE,
        related_name='spawn_plans')
    slug = models.SlugField(max_length=120, blank=True)
    name = models.TextField(default='Unnamed Spawn Plan')
    notes = models.TextField(**optional)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    respawn_policy = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=dict, blank=True)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _generate_unique_world_slug(
                self,
                fallback_prefix='spawn-plan',
            )
        super().save(*args, **kwargs)


class SpawnEntry(AdventBaseModel):
    plan = models.ForeignKey(
        'builders.SpawnPlan',
        on_delete=models.CASCADE,
        related_name='entries')
    slug = models.SlugField(max_length=120)
    name = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    source = models.JSONField(default=dict, blank=True)
    target = models.JSONField(default=dict, blank=True)
    count = models.JSONField(default=dict, blank=True)
    placement = models.JSONField(default=dict, blank=True)
    traits = models.JSONField(default=dict, blank=True)
    loot = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=dict, blank=True)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('plan', 'slug')]


class SpawnPlanRun(AdventBaseModel):
    STATUS_ACTIVE = 'active'
    STATUS_RESET = 'reset'

    spawn_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='spawn_plan_runs')
    plan = models.ForeignKey(
        'builders.SpawnPlan',
        on_delete=models.CASCADE,
        related_name='runs')
    seed = models.TextField()
    spec_hash = models.TextField(blank=True)
    entry_states = models.JSONField(default=dict, blank=True, db_default={})
    status = models.TextField(default=STATUS_ACTIVE)
    generated_at = models.DateTimeField(default=timezone.now)
    last_reconciled_at = models.DateTimeField(**optional)
    reset_at = models.DateTimeField(**optional)

    class Meta(AdventBaseModel.Meta):
        indexes = [
            models.Index(fields=['spawn_world', 'plan', 'status']),
        ]


class SpawnPlacement(AdventBaseModel):
    run = models.ForeignKey(
        'builders.SpawnPlanRun',
        on_delete=models.CASCADE,
        related_name='placements')
    entry_slug = models.SlugField(max_length=120)
    slot_index = models.PositiveIntegerField()
    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='spawn_placements')
    source_type = models.TextField()
    source_slug = models.SlugField(max_length=120)
    source_id = models.PositiveIntegerField(**optional)
    parent_entry_slug = models.SlugField(max_length=120, blank=True)
    parent_slot_index = models.PositiveIntegerField(**optional)
    traits = models.JSONField(default=list, blank=True)
    modifiers = models.JSONField(default=dict, blank=True)
    state = models.JSONField(default=dict, blank=True)
    is_retired = models.BooleanField(default=False, db_default=False)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('run', 'entry_slug', 'slot_index')]
        indexes = [
            models.Index(fields=['run', 'entry_slug']),
        ]


class RoomGetTrigger(AdventBaseModel):
    name = models.TextField(**optional)
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='get_triggers')
    # The item reference to pick up
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
    match = models.TextField(**optional)
    script = models.TextField(**optional)
    conditions = models.TextField(**optional)
    event = models.TextField(
        choices=list_to_choice(api_consts.TRIGGER_EVENTS),
        **optional,
    )

    show_details_on_failure = models.BooleanField(default=False)
    failure_message = models.TextField(**optional)
    display_action_in_room = models.BooleanField(default=True)

    # 0: no gate; >0: seconds; -1: one-shot.
    gate_delay = models.IntegerField(default=10)

    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(AdventBaseModel.Meta):
        indexes = [
            models.Index(
                fields=[
                    'world',
                    'kind',
                    'event',
                    'scope',
                    'target_type',
                    'target_id',
                    'is_active',
                    'order',
                ],
                name='trigger_hook_lookup_idx',
            ),
        ]


def post_trigger_policy_cache_bump(sender, **kwargs):
    from core.trigger_policy_cache import bump_trigger_policy_cache_version_on_commit

    bump_trigger_policy_cache_version_on_commit(kwargs['instance'].world_id)


models.signals.post_save.connect(
    post_trigger_policy_cache_bump,
    Trigger,
    dispatch_uid='builders.trigger.policy_cache.post_save',
)
models.signals.post_delete.connect(
    post_trigger_policy_cache_bump,
    Trigger,
    dispatch_uid='builders.trigger.policy_cache.post_delete',
)


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
    type = models.TextField(
        choices=list_to_choice(FACTION_TYPES),
        default=FACTION_TYPE_REPUTATION)
    playable = models.BooleanField(default=False)
    default_languages = models.JSONField(default=list, blank=True)
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

    def save(self, *args, **kwargs):
        if self.type == FACTION_TYPE_CORE:
            self.is_core = True
            self.playable = bool(self.playable)
            self.is_selectable = bool(self.playable)
        elif self.is_core:
            self.type = FACTION_TYPE_CORE
            self.playable = bool(self.is_selectable)
        else:
            self.type = FACTION_TYPE_REPUTATION
            self.is_core = False
            self.is_default = False
            self.is_selectable = False
            self.playable = False
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(dict.fromkeys([
                *kwargs["update_fields"],
                "type",
                "is_core",
                "is_default",
                "is_selectable",
                "playable",
                "modified_ts",
            ]))
        return super().save(*args, **kwargs)


class FactionRank(BaseModel):
    faction = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='ranks')
    standing = models.IntegerField()
    name = models.TextField()


class FactionAssignment(BaseModel):
    "Assignment of faction to either player character or authored mob definition."

    faction = models.ForeignKey('builders.Faction',
                                on_delete=models.CASCADE,
                                related_name='assignments_for')
    value = models.IntegerField(default=0)
    source = models.TextField(blank=True, default='')

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
        core_faction_filter = models.Q(type=FACTION_TYPE_CORE) | models.Q(is_core=True)
        if Faction.objects.filter(pk=self.faction_id).filter(core_faction_filter).exists():
            qs = FactionAssignment.objects.filter(
                member_type_id=self.member_type_id,
                member_id=self.member_id,
            ).filter(
                models.Q(faction__type=FACTION_TYPE_CORE) | models.Q(faction__is_core=True)
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


class AbilityDefinition(BaseModel):
    world = models.ForeignKey(
        'worlds.World',
        related_name='ability_definitions',
        on_delete=models.CASCADE)

    slug = models.TextField()
    name = models.TextField()
    command_verbs = models.JSONField(default=list)
    action_type = models.TextField(default='primary', db_index=True)
    consumes_primary_action_on_resolve = models.BooleanField(default=True)
    consumes_primary_action_while_casting = models.BooleanField(default=True)
    target = models.JSONField(default=dict)
    availability = models.JSONField(default=dict)
    requirements = models.JSONField(default=dict)
    cost = models.JSONField(default=dict)
    cast_time = models.JSONField(default=dict)
    cooldown = models.JSONField(default=dict)
    help = models.JSONField(default=dict)
    components = models.JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        unique_together = ['world_id', 'slug']
        indexes = [
            models.Index(fields=['world', 'slug']),
            models.Index(fields=['world', 'is_active']),
            GinIndex(fields=['command_verbs']),
        ]

    def __str__(self):
        return f"{self.slug} - {self.name}"


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
