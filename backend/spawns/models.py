import json
import logging
import re
import uuid

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
    type_to_slot,
    calculate_power)

from config import constants as api_consts
from config import game_settings as adv_config

from core.db import (
    BaseModel,
    AdventBaseModel,
    list_to_choice,
    optional)
from core.leveling import (
    experience_for_level,
    get_world_leveling_config,
)
from core.model_mixins import CharMixin, ItemMixin, MobMixin


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


class Player(CharMixin, AdventBaseModel):

    pending_deletion_ts = models.DateTimeField(db_index=True, **optional)

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='players')
    # Canonical player core identity. Reputation continues to use
    # FactionAssignment; new players do not duplicate their core identity there.
    core_faction = models.ForeignKey(
        'builders.Faction',
        on_delete=models.RESTRICT,
        related_name='core_faction_players',
        **optional,
    )
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

    is_builder = models.BooleanField(default=False)
    is_invisible = models.BooleanField(default=False)

    language_proficiency = models.DecimalField(max_digits=2, decimal_places=1,
                                               default='0.0')

    # Field that gets set when we start to save a player's data so that
    # we never run into a risk of duplicate saving.
    save_start_ts = models.DateField(**optional)

    # Points gained and lost when killing / dying to other players
    glory = models.PositiveIntegerField(default=0)
    wallet_revision = models.BigIntegerField(default=0)
    death_sequence = models.BigIntegerField(default=0)
    location_sequence = models.BigIntegerField(default=0)

    # Moderation flags
    nochat = models.BooleanField(default=False)
    noplay = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ['name', 'world']
        indexes = [
            models.Index(
                models.F('world'),
                models.functions.Lower('name'),
                condition=models.Q(in_game=True),
                name='spawn_player_world_lname_live',
            ),
            models.Index(
                fields=['world', 'room', 'id'],
                condition=models.Q(in_game=True),
                name='spawn_player_world_room_live',
            ),
        ]

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
    state = models.TextField(
        choices=list_to_choice(adv_consts.CHARACTER_STATES),
        default=adv_consts.CHARACTER_STATE_STANDING,
    )

    mute_list = models.TextField(**optional)
    # Space delimited, lowercase list of channels the player is
    # listening to.
    channels = models.TextField(default='chat', blank=True)

    cooldowns = models.TextField(**optional)
    effects = models.TextField(**optional)
    known_abilities = models.JSONField(default=list)
    ability_hotkeys = models.JSONField(default=dict)
    ability_cooldowns = models.JSONField(default=dict)
    command_history = models.JSONField(default=list)

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

    def initialize(
        self,
        reset=False,
        level=None,
        include_starting_equipment=True,
        reset_origin_world_id=None,
        reset_origin_room_id=None,
    ):
        from builders.models import FactionAssignment
        from core.economy import economy_world
        from spawns.wallet import replace_balances

        if reset:
            if reset_origin_world_id is None:
                reset_origin_world_id = self.world_id
            if reset_origin_room_id is None:
                reset_origin_room_id = self.room_id
        leveling_config = get_world_leveling_config(self.world)
        if level is None:
            level = leveling_config.starting_level
        level = max(1, min(int(level or 1), leveling_config.max_level))

        if reset:
            self.level = level
            self.experience = experience_for_level(level, leveling_config)
            self.glory = 0
            starting_room = self.get_starting_room()
            if (
                reset_origin_world_id != self.world_id
                or reset_origin_room_id != starting_room.id
            ):
                self.location_sequence = int(self.location_sequence or 0) + 1
            self.room = starting_room
            # Delete factions
            FactionAssignment.objects.filter(
                faction__world=self.world.context,
                faction__is_core=False,
                member_type__model='player',
                member_id=self.id).delete()
            # Delete aliases
            self.aliases.all().delete()
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
            # Delete accumulated crafting materials.
            self.material_balances.all().delete()
            # Delete legacy marks and canonical character state
            self.marks.all().delete()
            CharacterState.objects.filter(player=self).delete()
            self.known_abilities = []
            self.ability_hotkeys = {}
            self.ability_cooldowns = {}
            self.active_effect_records.all().delete()
        elif self.level < leveling_config.starting_level:
            self.level = leveling_config.starting_level
            self.experience = experience_for_level(self.level, leveling_config)

        stats = compute_stats(
            self.level,
            self.archetype,
            char=self,
            world=self.world,
        )
        self.health = max(1, int(stats.get('health_max') or 1))
        self.energy = int(stats.get('energy_max') or 0)
        self.stamina = int(stats.get('stamina_max') or 0)

        from spawns.actions.abilities import grant_starting_abilities
        grant_starting_abilities(self)

        equipped_starting_items = False
        if include_starting_equipment:
            from builders.models import ItemDefinition
            from core.world_config import inherited_system_world

            definition_world = inherited_system_world(self.world) or self.world

            starting_entries = []
            definition_ids = set()
            definition_slugs = set()
            for starting_item in self.world.config.starting_equipment or []:
                if not isinstance(starting_item, dict):
                    continue
                archetype = starting_item.get("archetype")
                if archetype and archetype != self.archetype:
                    continue
                raw_definition = starting_item.get("item_definition")
                if raw_definition in (None, ""):
                    raw_definition = starting_item.get("item_definition_id")
                if isinstance(raw_definition, int) and not isinstance(raw_definition, bool):
                    definition_key = ("id", raw_definition)
                    definition_ids.add(raw_definition)
                else:
                    definition_slug = str(raw_definition or "").strip()
                    prefix, sep, raw = definition_slug.partition(".")
                    if sep == "." and prefix in {"itemdefinition", "item_definition"}:
                        definition_slug = raw
                    if not definition_slug:
                        continue
                    definition_key = ("slug", definition_slug)
                    definition_slugs.add(definition_slug)
                starting_entries.append((starting_item, definition_key))

            definition_filter = models.Q()
            if definition_ids:
                definition_filter |= models.Q(pk__in=definition_ids)
            if definition_slugs:
                definition_filter |= models.Q(slug__in=definition_slugs)

            definitions_by_key = {}
            if definition_ids or definition_slugs:
                definitions = (
                    ItemDefinition.objects
                    .filter(world=definition_world)
                    .filter(definition_filter)
                    .select_related("world", "world__config")
                )
                for definition in definitions:
                    definitions_by_key[("id", definition.id)] = definition
                    definitions_by_key[("slug", definition.slug)] = definition

            equipment = self.equipment
            planned_slot_ids = {
                slot: getattr(equipment, f"{slot}_id", None)
                for slot in adv_consts.EQUIPMENT_SLOTS
            }
            planned_weapon_type = ""
            if planned_slot_ids.get(adv_consts.EQUIPMENT_SLOT_WEAPON):
                planned_weapon_type = str(
                    getattr(equipment.weapon, "equipment_type", "") or ""
                )

            touched_slots = set()
            items_to_equip = []
            for starting_item, definition_key in starting_entries:
                definition = definitions_by_key.get(definition_key)
                if not definition:
                    continue
                try:
                    count = int(starting_item.get("count", starting_item.get("num", 1)) or 1)
                except (TypeError, ValueError):
                    count = 1
                for _ in range(0, max(0, count)):
                    item = definition.spawn(self, self.world)

                    if not item.equipment_type or starting_item.get("equip", True) is False:
                        continue
                    if (self.archetype == adv_consts.ARCHETYPE_ASSASSIN
                        and (item.equipment_type
                                == adv_consts.EQUIPMENT_TYPE_WEAPON_2H)):
                        continue

                    slot = type_to_slot(
                        eq_type=item.equipment_type,
                        archetype=self.archetype,
                        has_weapon=bool(planned_slot_ids.get(
                            adv_consts.EQUIPMENT_SLOT_WEAPON)),
                        has_offhand=bool(planned_slot_ids.get(
                            adv_consts.EQUIPMENT_SLOT_OFFHAND)),
                    )
                    if (slot not in adv_consts.EQUIPMENT_SLOTS
                        or planned_slot_ids.get(slot)):
                        continue
                    if (item.equipment_type == adv_consts.EQUIPMENT_TYPE_WEAPON_2H
                        and planned_slot_ids.get(adv_consts.EQUIPMENT_SLOT_OFFHAND)):
                        continue
                    if (slot == adv_consts.EQUIPMENT_SLOT_OFFHAND
                        and planned_weapon_type == adv_consts.EQUIPMENT_TYPE_WEAPON_2H):
                        continue

                    setattr(equipment, slot, item)
                    planned_slot_ids[slot] = item.id
                    if slot == adv_consts.EQUIPMENT_SLOT_WEAPON:
                        planned_weapon_type = item.equipment_type
                    touched_slots.add(slot)
                    items_to_equip.append(item)

            if items_to_equip:
                equipment_type = ContentType.objects.get_for_model(Equipment)
                for item in items_to_equip:
                    item.container_type = equipment_type
                    item.container_id = equipment.id
                with transaction.atomic():
                    equipment.save(update_fields=sorted(touched_slots))
                    Item.objects.bulk_update(
                        items_to_equip,
                        ["container_type", "container_id"],
                    )
                equipped_starting_items = True

        if equipped_starting_items:
            # Starter gear can raise resource maxima. New and reset characters
            # should begin topped off against the fully equipped loadout.
            stats = compute_stats(
                self.level,
                self.archetype,
                char=self,
                world=self.world,
            )
            self.health = max(1, int(stats.get('health_max') or 1))
            self.energy = int(stats.get('energy_max') or 0)
            self.stamina = int(stats.get('stamina_max') or 0)
        self.save()

        base_world = economy_world(self.world)
        starting_balances = {
            row.currency_id: int(row.amount)
            for row in base_world.starting_currency_balances.all()
        }
        replace_balances(
            self,
            starting_balances,
            reason="character.reset" if reset else "character.initialize",
            emit_event=False,
        )
        if (
            reset
            and self.in_game
            and (
                reset_origin_world_id != self.world_id
                or reset_origin_room_id != self.room_id
            )
        ):
            from spawns.events import (
                enqueue_game_events,
                flush_game_event_outbox,
                player_room_enter_event,
            )

            enqueue_game_events([
                player_room_enter_event(
                    player=self,
                    origin_room_id=reset_origin_room_id,
                    destination_room_id=self.room_id,
                    source="character_reset",
                )
            ])
            transaction.on_commit(
                flush_game_event_outbox,
                robust=True,
            )

        return self

    def get_starting_room(self):
        """
        By default, a player goes to the starting room for that world.
        But if they have a core faction defined, they should go to
        their starting room instead, if it's defined.
        """

        if self.core_faction_id:
            core_faction = self._state.fields_cache.get('core_faction')
            if core_faction is None:
                core_faction = self.core_faction
            if core_faction.starting_room_id:
                return core_faction.starting_room
        else:
            # Transitional read-only fallback for rows created before direct
            # core-faction identity was introduced.
            legacy_assignment = (
                self.faction_assignments
                .filter(
                    models.Q(faction__type='core')
                    | models.Q(faction__is_core=True)
                )
                .select_related('faction', 'faction__starting_room')
                .order_by('created_ts')
                .first()
            )
            if (
                legacy_assignment
                and legacy_assignment.faction.starting_room_id
            ):
                return legacy_assignment.faction.starting_room

        # Default to the world's starting room
        authored_world = self.world.context or self.world
        return authored_world.config.starting_room

    def reset(self, level=None):
        with transaction.atomic():
            player = (
                type(self).objects.select_for_update(of=('self',))
                .select_related('world', 'world__context')
                .get(pk=self.pk)
            )
            reset_origin_world_id = player.world_id
            reset_origin_room_id = player.room_id
            if player.world.is_multiplayer:
                player.initialize(
                    reset=True,
                    level=level,
                    reset_origin_world_id=reset_origin_world_id,
                    reset_origin_room_id=reset_origin_room_id,
                )
                return player

            original_world = player.world
            root_world = player.world.context
            new_spawn_world = root_world.create_spawn_world()

            player.world = new_spawn_world
            player.save()

            player = player.initialize(
                reset=True,
                level=level,
                reset_origin_world_id=reset_origin_world_id,
                reset_origin_room_id=reset_origin_room_id,
            )

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


class CharacterState(BaseModel):

    player = models.OneToOneField(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='character_state_record',
    )
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['player_id']


class DeathResolutionReceipt(BaseModel):
    """Durable idempotency result for one player death incident."""

    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='death_resolution_receipts',
    )
    death_token = models.UUIDField()
    request_fingerprint = models.CharField(max_length=128)
    origin_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.SET_NULL,
        related_name='death_receipts_originating_here',
        **optional,
    )
    origin_room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.SET_NULL,
        related_name='death_receipts_originating_here',
        **optional,
    )
    destination_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.SET_NULL,
        related_name='death_receipts_ending_here',
        **optional,
    )
    destination_room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.SET_NULL,
        related_name='death_receipts_ending_here',
        **optional,
    )
    origin_instance_run = models.ForeignKey(
        'worlds.InstanceRun',
        on_delete=models.SET_NULL,
        related_name='death_resolution_receipts',
        **optional,
    )
    origin_instance_participant = models.ForeignKey(
        'worlds.InstanceParticipant',
        on_delete=models.SET_NULL,
        related_name='death_resolution_receipts',
        **optional,
    )
    routing_source = models.CharField(max_length=32)
    origin_config = models.ForeignKey(
        'worlds.WorldConfig',
        on_delete=models.SET_NULL,
        related_name='origin_death_resolution_receipts',
        **optional,
    )
    source_generation = models.BigIntegerField(default=0)
    plan_config = models.ForeignKey(
        'worlds.WorldConfig',
        on_delete=models.SET_NULL,
        related_name='plan_death_resolution_receipts',
        **optional,
    )
    plan_generation = models.BigIntegerField(default=0)
    matched_route_position = models.PositiveSmallIntegerField(**optional)
    core_faction = models.ForeignKey(
        'builders.Faction',
        on_delete=models.SET_NULL,
        related_name='death_resolution_receipts',
        **optional,
    )
    decision_reason = models.CharField(max_length=64)
    fallback_reason = models.CharField(max_length=64, blank=True, default='')
    death_sequence = models.BigIntegerField()
    location_sequence = models.BigIntegerField()
    penalty = models.JSONField(default=dict, blank=True)
    corpse_id = models.BigIntegerField(**optional)
    result = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'death_token'],
                name='spawns_death_receipt_player_token',
            ),
        ]


class MobState(BaseModel):

    mob = models.OneToOneField(
        'spawns.Mob',
        on_delete=models.CASCADE,
        related_name='character_state_record',
    )
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['mob_id']


class DuelMatch(BaseModel):
    """Durable invitation, lifecycle, and result for an instanced PvP match."""

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = list_to_choice((
        STATUS_PENDING,
        STATUS_ACTIVE,
        STATUS_COMPLETED,
        STATUS_DECLINED,
        STATUS_CANCELLED,
        STATUS_EXPIRED,
    ))
    OPEN_STATUSES = (STATUS_PENDING, STATUS_ACTIVE)

    base_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='duel_matches',
    )
    template_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='duel_template_matches',
    )
    entrance_room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.SET_NULL,
        related_name='duel_matches_started_here',
        blank=True,
        null=True,
    )
    # Instance cleanup may remove the spawned world and its InstanceRun. Match
    # history remains canonical and durable after that runtime cleanup.
    run = models.OneToOneField(
        'worlds.InstanceRun',
        on_delete=models.SET_NULL,
        related_name='duel_match',
        blank=True,
        null=True,
    )
    challenger = models.ForeignKey(
        'spawns.Player',
        on_delete=models.SET_NULL,
        related_name='duel_challenges_sent',
        blank=True,
        null=True,
    )
    challenged = models.ForeignKey(
        'spawns.Player',
        on_delete=models.SET_NULL,
        related_name='duel_challenges_received',
        blank=True,
        null=True,
    )
    winner = models.ForeignKey(
        'spawns.Player',
        on_delete=models.SET_NULL,
        related_name='duel_match_wins',
        blank=True,
        null=True,
    )
    loser = models.ForeignKey(
        'spawns.Player',
        on_delete=models.SET_NULL,
        related_name='duel_match_losses',
        blank=True,
        null=True,
    )
    status = models.TextField(
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    outcome = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['status', 'expires_at']),
            models.Index(fields=['challenger', 'status']),
            models.Index(fields=['challenged', 'status']),
            models.Index(fields=['base_world', 'status']),
            models.Index(fields=['template_world', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(challenger__isnull=True)
                    | models.Q(challenged__isnull=True)
                    | ~models.Q(challenger=models.F('challenged'))
                ),
                name='spawns_duel_distinct_challengers',
            ),
        ]


class DuelParticipant(BaseModel):
    """Team-aware match membership; spectators can be added without reshaping."""

    ROLE_CONTESTANT = "contestant"
    ROLE_SPECTATOR = "spectator"
    ROLE_CHOICES = list_to_choice((ROLE_CONTESTANT, ROLE_SPECTATOR))

    RESULT_PENDING = "pending"
    RESULT_WON = "won"
    RESULT_LOST = "lost"
    RESULT_CHOICES = list_to_choice((
        RESULT_PENDING,
        RESULT_WON,
        RESULT_LOST,
    ))

    match = models.ForeignKey(
        'spawns.DuelMatch',
        on_delete=models.CASCADE,
        related_name='participants',
    )
    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='duel_participations',
    )
    role = models.TextField(
        choices=ROLE_CHOICES,
        default=ROLE_CONTESTANT,
        db_index=True,
    )
    team = models.PositiveSmallIntegerField(default=1)
    result = models.TextField(
        choices=RESULT_CHOICES,
        default=RESULT_PENDING,
        db_index=True,
    )
    joined_at = models.DateTimeField(blank=True, null=True)
    exited_at = models.DateTimeField(blank=True, null=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['match', 'player'],
                name='spawns_duel_unique_participant',
            ),
            models.CheckConstraint(
                condition=models.Q(team__gte=1),
                name='spawns_duel_team_positive',
            ),
        ]
        indexes = [
            models.Index(fields=['match', 'role', 'team']),
            models.Index(fields=['player', 'role']),
        ]


class CombatEncounter(BaseModel):
    STATUS_ACTIVE = "active"
    STATUS_FINISHED = "finished"
    STATUS_CHOICES = list_to_choice((STATUS_ACTIVE, STATUS_FINISHED))

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='combat_encounters',
    )
    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='combat_encounters',
    )
    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='combat_encounters',
    )
    duel_match = models.ForeignKey(
        'spawns.DuelMatch',
        on_delete=models.CASCADE,
        related_name='combat_encounters',
        blank=True,
        null=True,
    )
    mob = models.ForeignKey(
        'spawns.Mob',
        on_delete=models.SET_NULL,
        related_name='combat_encounters',
        blank=True,
        null=True,
    )
    status = models.TextField(
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    resolution_interval = models.FloatField(default=0)
    round_number = models.PositiveIntegerField(default=0)
    next_resolution_ts = models.DateTimeField(db_index=True, **optional)
    last_resolution_ts = models.DateTimeField(db_index=True, **optional)
    pending_player_ability = models.JSONField(default=dict)
    pending_mob_ability = models.JSONField(default=dict)
    pending_flee = models.JSONField(default=dict)
    initiative_order = models.JSONField(default=list)
    opening_priority = models.JSONField(default=list)
    faceoff_override = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['status', 'next_resolution_ts']),
            models.Index(fields=['player', 'status']),
            models.Index(fields=['mob', 'status']),
            models.Index(fields=['duel_match', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['duel_match'],
                condition=(
                    models.Q(status='active')
                    & models.Q(duel_match__isnull=False)
                ),
                name='spawns_duel_one_active_encounter',
            ),
        ]

    @property
    def active_effects(self):
        from spawns.actions.effects import active_effect_payload, encounter_effects

        return [active_effect_payload(effect) for effect in encounter_effects(self)]

    @property
    def is_combat_locked(self) -> bool:
        """Ordinary movement closes after the first encounter round begins."""
        return int(self.round_number or 0) > 0


class CombatParticipant(BaseModel):
    """Actor-local state for PvP and future multi-participant encounters."""

    encounter = models.ForeignKey(
        'spawns.CombatEncounter',
        on_delete=models.CASCADE,
        related_name='participants',
    )
    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='combat_participations',
        blank=True,
        null=True,
    )
    mob = models.ForeignKey(
        'spawns.Mob',
        on_delete=models.CASCADE,
        related_name='combat_participations',
        blank=True,
        null=True,
    )
    team = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)
    pending_ability = models.JSONField(default=dict, blank=True)
    pending_flee = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(player__isnull=False, mob__isnull=True)
                    | models.Q(player__isnull=True, mob__isnull=False)
                ),
                name='spawns_combat_participant_one_actor',
            ),
            models.CheckConstraint(
                condition=models.Q(team__gte=1),
                name='spawns_combat_participant_team_positive',
            ),
            models.UniqueConstraint(
                fields=['encounter', 'player'],
                condition=models.Q(player__isnull=False),
                name='spawns_combat_unique_player',
            ),
            models.UniqueConstraint(
                fields=['encounter', 'mob'],
                condition=models.Q(mob__isnull=False),
                name='spawns_combat_unique_mob',
            ),
        ]
        indexes = [
            models.Index(fields=['encounter', 'is_active', 'team']),
            models.Index(fields=['mob', 'is_active']),
            models.Index(
                fields=['player', 'encounter'],
                condition=(
                    models.Q(is_active=True)
                    & models.Q(player__isnull=False)
                ),
                name='spawn_combat_player_active',
            ),
        ]

    @property
    def actor(self):
        return self.player or self.mob


class ActiveEffect(BaseModel):
    """Canonical runtime state for effects that follow an actor between fights."""

    SCOPE_ENCOUNTER = "encounter"
    SCOPE_CHARACTER = "character"
    SCOPE_CHOICES = list_to_choice((SCOPE_ENCOUNTER, SCOPE_CHARACTER))

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='active_effects',
    )
    encounter = models.ForeignKey(
        'spawns.CombatEncounter',
        on_delete=models.SET_NULL,
        related_name='character_effects',
        blank=True,
        null=True,
    )
    source_player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.SET_NULL,
        related_name='sourced_active_effects',
        blank=True,
        null=True,
    )
    source_mob = models.ForeignKey(
        'spawns.Mob',
        on_delete=models.SET_NULL,
        related_name='sourced_active_effects',
        blank=True,
        null=True,
    )
    target_player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='active_effect_records',
        blank=True,
        null=True,
    )
    target_mob = models.ForeignKey(
        'spawns.Mob',
        on_delete=models.CASCADE,
        related_name='active_effect_records',
        blank=True,
        null=True,
    )
    scope = models.TextField(
        choices=SCOPE_CHOICES,
        default=SCOPE_CHARACTER,
    )
    effect = models.SlugField(max_length=120)
    category = models.TextField(default='neutral')
    label = models.TextField()
    stack_key = models.SlugField(max_length=120, blank=True)
    stacking = models.TextField(default='independent')
    remaining_rounds = models.PositiveIntegerField(default=1)
    duration_rounds = models.PositiveIntegerField(default=1)
    rounds_elapsed = models.PositiveIntegerField(default=0)
    started_round = models.PositiveIntegerField(default=0)
    started_round_id = models.TextField(blank=True)
    primitives = models.JSONField(default=list)
    tick = models.JSONField(default=dict)
    source_snapshot = models.JSONField(default=dict)
    is_hostile = models.BooleanField(default=False, db_index=True)
    next_tick_ts = models.DateTimeField(db_index=True, blank=True, null=True)
    last_tick_ts = models.DateTimeField(blank=True, null=True)
    last_tick_token = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['encounter', 'remaining_rounds']),
            models.Index(fields=['target_player', 'remaining_rounds']),
            models.Index(fields=['target_mob', 'remaining_rounds']),
            models.Index(fields=['is_hostile', 'next_tick_ts']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(target_player__isnull=False, target_mob__isnull=True)
                    | models.Q(target_player__isnull=True, target_mob__isnull=False)
                ),
                name='spawns_effect_exactly_one_target',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_player__isnull=True)
                    | models.Q(source_mob__isnull=True)
                ),
                name='spawns_effect_at_most_one_source',
            ),
            models.CheckConstraint(
                condition=models.Q(remaining_rounds__gte=1),
                name='spawns_effect_remaining_rounds_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(duration_rounds__gte=1),
                name='spawns_effect_duration_rounds_positive',
            ),
            models.UniqueConstraint(
                fields=['target_player', 'scope', 'stack_key'],
                condition=(
                    models.Q(
                        scope='character',
                        stacking='refresh',
                        target_player__isnull=False,
                    )
                    & ~models.Q(stack_key='')
                ),
                name='spawns_effect_unique_player_refresh_stack',
            ),
            models.UniqueConstraint(
                fields=['target_mob', 'scope', 'stack_key'],
                condition=(
                    models.Q(
                        scope='character',
                        stacking='refresh',
                        target_mob__isnull=False,
                    )
                    & ~models.Q(stack_key='')
                ),
                name='spawns_effect_unique_mob_refresh_stack',
            ),
        ]


def delete_encounter_scoped_effects(sender, instance, using, **kwargs):
    """Keep SET_NULL provenance from orphaning encounter-owned effects."""
    ActiveEffect.objects.using(using).filter(
        encounter_id=instance.id,
        scope=ActiveEffect.SCOPE_ENCOUNTER,
    ).delete()


models.signals.pre_delete.connect(
    delete_encounter_scoped_effects,
    sender=CombatEncounter,
)


class PlayerMaterialBalance(AdventBaseModel):
    """A compact, transactional balance for one authored craft material."""

    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='material_balances',
    )
    material = models.ForeignKey(
        'builders.CraftMaterial',
        on_delete=models.RESTRICT,
        related_name='player_balances',
    )
    quantity = models.PositiveIntegerField(default=0)

    class Meta(AdventBaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'material'],
                name='spawns_material_balance_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name='spawns_material_balance_nonnegative',
            ),
        ]


class PlayerCurrencyBalance(AdventBaseModel):
    """The canonical sparse balance for one player and authored currency."""

    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='currency_balances')
    currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.RESTRICT,
        related_name='player_balances')
    amount = models.BigIntegerField(default=0)

    class Meta(AdventBaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'currency'],
                name='spawns_currency_balance_unique'),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='spawns_currency_balance_nonnegative'),
            models.CheckConstraint(
                condition=models.Q(amount__lte=9007199254740991),
                name='spawns_currency_balance_safe_integer'),
        ]


class CraftingActionReceipt(AdventBaseModel):
    """Committed result for replay-safe material-spending commands."""

    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='crafting_action_receipts',
    )
    request_id = models.UUIDField()
    segment = models.CharField(max_length=128, default='r')
    action = models.SlugField(max_length=32)
    result = models.JSONField(default=dict)

    class Meta(AdventBaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'request_id', 'segment'],
                name='spawns_crafting_receipt_unique',
            ),
        ]
        indexes = [
            models.Index(
                fields=['player', 'request_id'],
                name='spawns_crafting_receipt_idx',
            ),
        ]


class ScheduledTriggerRun(AdventBaseModel):
    """Durable runtime cursor for one typed trigger-step sequence."""

    STATUS_ACTIVE = 'active'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        STATUS_ACTIVE,
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    )

    trigger = models.ForeignKey(
        'builders.Trigger',
        on_delete=models.SET_NULL,
        related_name='scheduled_runs',
        **optional)
    runtime_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='scheduled_trigger_runs')
    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='scheduled_trigger_runs')

    actor_type = models.TextField()
    actor_id = models.PositiveBigIntegerField()
    actor_key = models.TextField()
    request_id = models.UUIDField(**optional)
    request_segment = models.CharField(max_length=128, default='r')
    request_connection_id = models.TextField(**optional)

    steps = models.JSONField(default=list)
    bindings = models.JSONField(default=dict, blank=True)
    next_step_index = models.PositiveIntegerField(default=0)
    next_run_ts = models.DateTimeField()
    started_ts = models.DateTimeField(default=timezone.now)
    status = models.TextField(
        choices=list_to_choice(STATUS_CHOICES),
        default=STATUS_ACTIVE)
    on_step_error = models.TextField(default='cancel')
    failure_code = models.TextField(blank=True, default='')
    last_error = models.TextField(blank=True, default='')
    completed_ts = models.DateTimeField(**optional)

    class Meta(AdventBaseModel.Meta):
        indexes = [
            models.Index(
                fields=['status', 'next_run_ts', 'id'],
                name='spawn_trigger_run_due_idx',
            ),
            models.Index(
                fields=['status', 'modified_ts', 'id'],
                name='spawn_trigger_run_prune_idx',
            ),
            models.Index(
                fields=['runtime_world', 'actor_type', 'actor_id'],
                condition=models.Q(status='active'),
                name='spawn_trigger_actor_active_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'trigger',
                    'runtime_world',
                    'room',
                    'actor_type',
                    'actor_id',
                ],
                condition=models.Q(status='active'),
                name='spawn_trigger_actor_active_uniq',
            ),
        ]


class GameEventOutbox(BaseModel):
    """Durable, at-least-once delivery for events produced by committed work."""

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    batch_id = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False)
    sequence = models.PositiveIntegerField(default=0)
    event_type = models.TextField()
    data = models.JSONField(default=dict)
    recipients = models.JSONField(default=list)
    text = models.TextField(**optional)
    group = models.TextField(**optional)
    connection_id = models.TextField(**optional)
    available_ts = models.DateTimeField(default=timezone.now, db_index=True)
    claim_token = models.UUIDField(**optional)
    claimed_until = models.DateTimeField(db_index=True, **optional)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')


class EventSubscriptionReceipt(BaseModel):
    """Idempotency receipt for at-least-once event subscribers."""

    event_id = models.UUIDField()
    subscriber = models.SlugField(max_length=120)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['event_id', 'subscriber'],
                name='spawns_event_receipt_unique_subscriber',
            ),
        ]


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


models.signals.post_save.connect(Player.post_char_save, Player)
models.signals.post_delete.connect(Player.post_char_delete, Player)


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
    definition = models.ForeignKey('builders.MobDefinition',
                                   on_delete=models.SET_NULL,
                                   related_name='spawned_mobs',
                                   **optional)
    definition_slug_snapshot = models.SlugField(max_length=120, blank=True)
    roll_metadata = models.JSONField(default=dict, blank=True)
    currency_reward_snapshot = models.JSONField(default=dict, blank=True)
    trait_instances = models.JSONField(default=list, blank=True)
    loot = models.JSONField(default=dict, blank=True)
    ability_cooldowns = models.JSONField(default=dict, blank=True)
    equipment = models.OneToOneField('spawns.Equipment',
                                     related_name='mob',
                                     on_delete=models.CASCADE,
                                     **optional)

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    spawn_placement = models.ForeignKey('builders.SpawnPlacement',
                                        related_name='mobs',
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
    attackable = models.BooleanField(default=True)
    target_priority = models.IntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=['created_ts']),
            models.Index(fields=['is_pending_deletion']),
            models.Index(
                fields=['world', 'room', 'id'],
                condition=models.Q(is_pending_deletion=False),
                name='spawn_mob_world_room_live',
            ),
        ]

    def create_corpse(self):
        if self.definition:
            name = self.definition.name
        else:
            name = self.name
        return Item.objects.create(
            name='the corpse of %s' % name,
            keywords='corpse',
            room_description='The corpse of {} is lying here.'.format(name),
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


class MerchantRuntime(AdventBaseModel):
    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='merchant_runtimes')
    mob = models.OneToOneField('spawns.Mob',
                               on_delete=models.CASCADE,
                               related_name='merchant_runtime')
    profile = models.ForeignKey('builders.MerchantProfile',
                                on_delete=models.CASCADE,
                                related_name='merchant_runtimes')
    settlement_currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.RESTRICT,
        related_name='merchant_runtimes')
    is_active = models.BooleanField(default=True)
    last_restocked_ts = models.DateTimeField(**optional)
    next_restock_ts = models.DateTimeField(db_index=True, **optional)
    remaining_purchase_budget = models.BigIntegerField(**optional)

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    class Meta:
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['next_restock_ts']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(remaining_purchase_budget__isnull=True)
                    | models.Q(
                        remaining_purchase_budget__gte=0,
                        remaining_purchase_budget__lte=9007199254740991,
                    )
                ),
                name='spawns_merchant_budget_safe'),
        ]


class MerchantStockEntry(AdventBaseModel):
    STATUS_AVAILABLE = "available"
    STATUS_SOLD = "sold"
    STATUS_EXPIRED = "expired"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = [
        STATUS_AVAILABLE,
        STATUS_SOLD,
        STATUS_EXPIRED,
        STATUS_RETIRED,
    ]

    runtime = models.ForeignKey('spawns.MerchantRuntime',
                                on_delete=models.CASCADE,
                                related_name='stock_entries')
    stock_slot = models.ForeignKey('builders.MerchantStockSlot',
                                   on_delete=models.SET_NULL,
                                   related_name='runtime_entries',
                                   **optional)
    item = models.OneToOneField('spawns.Item',
                                on_delete=models.CASCADE,
                                related_name='merchant_stock_entry')
    bundle_roll_id = models.TextField(**optional)
    price = models.BigIntegerField(default=0)
    currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.RESTRICT,
        related_name='merchant_stock_entries')
    status = models.TextField(
        choices=list_to_choice(STATUS_CHOICES),
        default=STATUS_AVAILABLE,
        db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['bundle_roll_id']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    price__gte=0,
                    price__lte=9007199254740991,
                ),
                name='spawns_merchant_stock_price_safe'),
        ]


class MerchantBuybackEntry(AdventBaseModel):
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_BOUGHT_BACK = "bought_back"
    STATUS_CHOICES = [
        STATUS_ACTIVE,
        STATUS_EXPIRED,
        STATUS_BOUGHT_BACK,
    ]

    runtime = models.ForeignKey('spawns.MerchantRuntime',
                                on_delete=models.CASCADE,
                                related_name='buyback_entries')
    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='merchant_buyback_entries')
    item = models.OneToOneField('spawns.Item',
                                on_delete=models.CASCADE,
                                related_name='merchant_buyback_entry')
    sold_price = models.BigIntegerField(default=0)
    buyback_price = models.BigIntegerField(default=0)
    currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.RESTRICT,
        related_name='merchant_buyback_entries')
    status = models.TextField(
        choices=list_to_choice(STATUS_CHOICES),
        default=STATUS_ACTIVE,
        db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_ts']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    sold_price__gte=0,
                    sold_price__lte=9007199254740991,
                    buyback_price__gte=0,
                    buyback_price__lte=9007199254740991,
                ),
                name='spawns_merchant_buyback_price_safe'),
        ]


class Item(ItemMixin, AdventBaseModel):
    """Runtime item spawned from a WR2 item definition, bundle, or procedure."""

    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='items')

    definition = models.ForeignKey('builders.ItemDefinition',
                                   on_delete=models.SET_NULL,
                                   related_name='spawned_items',
                                   **optional)
    definition_slug_snapshot = models.SlugField(max_length=120, blank=True)
    roll_metadata = models.JSONField(default=dict, blank=True)

    container_type = models.ForeignKey(ContentType,
                                       on_delete=models.SET_NULL,
                                       **optional)
    container_id = models.PositiveIntegerField(**optional)
    container = GenericForeignKey('container_type', 'container_id')

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    spawn_placement = models.ForeignKey('builders.SpawnPlacement',
                                        related_name='items',
                                        on_delete=models.SET_NULL,
                                        **optional)

    # Rather than outright removing items when following extraction we see
    # that they no longer belong to a player, we mark them as pending.
    # This is because, they could be in the wild and be re-picked up by
    # another player.
    is_pending_deletion = models.BooleanField(default=False)
    pending_deletion_ts = models.DateTimeField(db_index=True, **optional)

    label = models.TextField(**optional)

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
            models.Index(
                fields=['world', 'container_type', 'container_id', 'definition'],
                condition=models.Q(is_pending_deletion=False),
                name='spawn_item_live_container_def',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(cost__isnull=True, currency__isnull=True)
                    | models.Q(
                        cost__gte=0,
                        cost__lte=9007199254740991,
                        currency__isnull=False,
                    )
                ),
                name='spawns_item_money_pair'),
        ]

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

    @property
    def budget_spent(self):
        spent_budget = 0
        for value in (self.attributes or {}).values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                spent_budget += 10 * value
        for attr in [
            adv_consts.ATTR_AP,
            adv_consts.ATTR_ABILITY_POWER,
            adv_consts.ATTR_CRIT,
            adv_consts.ATTR_DODGE,
            adv_consts.ATTR_RESILIENCE,
            adv_consts.ATTR_MAX_HEALTH,
            adv_consts.ATTR_MAX_ENERGY,
            adv_consts.ATTR_MAX_STAMINA,
            adv_consts.ATTR_REGEN_HEALTH,
            adv_consts.ATTR_REGEN_ENERGY,
            adv_consts.ATTR_REGEN_STAMINA,
            adv_consts.ATTR_WEAPON_DAMAGE,
        ]:
            value = getattr(self, attr, 0)
            if value:
                spent_budget += adv_consts.ATTR_BUDGET[attr] * value
        return spent_budget

class Alias(BaseModel):

    player = models.ForeignKey('spawns.Player',
                               on_delete=models.CASCADE,
                               related_name='aliases')
    match = models.TextField()
    replacement = models.TextField()


class DoorState(BaseModel):
    """Runtime state for one logical doorway in one live world."""

    doorway = models.ForeignKey(
        'worlds.Doorway',
        on_delete=models.CASCADE,
        related_name='runtime_states',
    )
    world = models.ForeignKey('worlds.World',
                              on_delete=models.CASCADE,
                              related_name='door_states')
    state = models.TextField(choices=list_to_choice(adv_consts.DOOR_STATES),
                             default=adv_consts.DOOR_STATE_CLOSED)
    revision = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['world', 'doorway'],
                name='spawns_door_state_runtime_doorway',
            ),
        ]
class PreparedGameAction(AdventBaseModel):
    """Durable door request receipt, optionally pending a short wind-up."""

    ACTION_OPEN_DOOR = 'open_door'
    ACTION_CLOSE_DOOR = 'close_door'
    ACTION_LOCK_DOOR = 'lock_door'
    ACTION_UNLOCK_DOOR = 'unlock_door'
    ACTION_CHOICES = (
        ACTION_OPEN_DOOR,
        ACTION_CLOSE_DOOR,
        ACTION_LOCK_DOOR,
        ACTION_UNLOCK_DOOR,
    )

    STATUS_PENDING = 'pending'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        STATUS_PENDING,
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    )

    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='prepared_game_actions',
    )
    runtime_world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='prepared_game_actions',
    )
    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='prepared_game_actions',
    )
    doorway = models.ForeignKey(
        'worlds.Doorway',
        on_delete=models.CASCADE,
        related_name='prepared_game_actions',
    )
    action_type = models.TextField(choices=list_to_choice(ACTION_CHOICES))
    status = models.TextField(
        choices=list_to_choice(STATUS_CHOICES),
        default=STATUS_PENDING,
    )
    run_at = models.DateTimeField()
    expected_revision = models.BigIntegerField(default=0)
    request_id = models.UUIDField(**optional)
    request_segment = models.CharField(max_length=128, default='r')
    request_selector = models.TextField(blank=True, default='')
    target_direction = models.TextField(
        choices=list_to_choice(adv_consts.DIRECTIONS),
    )
    target_name = models.TextField(default='door')
    failure_code = models.CharField(max_length=64, blank=True, default='')
    result = models.JSONField(default=dict, blank=True)
    completed_ts = models.DateTimeField(**optional)

    class Meta(AdventBaseModel.Meta):
        indexes = [
            models.Index(
                fields=['status', 'run_at', 'id'],
                name='spawn_prepared_due_idx',
            ),
            models.Index(
                fields=['status', 'modified_ts', 'id'],
                name='spawn_prepared_prune_idx',
            ),
            models.Index(
                fields=['runtime_world', 'doorway', 'status'],
                name='spawn_prepared_door_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['player'],
                condition=models.Q(status='pending'),
                name='spawn_prepared_player_pending_uniq',
            ),
            models.UniqueConstraint(
                fields=['player', 'request_id', 'request_segment'],
                condition=models.Q(request_id__isnull=False),
                name='spawn_prepared_request_uniq',
            ),
        ]



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
