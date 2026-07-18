import json
import re
from core import utils as adv_utils
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    get_state_snapshot,
)
from core.factions import core_faction_policy

from config import constants as adv_consts
from core.utils import is_ascii

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist

from rest_framework import serializers
from rest_framework.fields import Field

from config import constants as api_consts
from config import game_settings as adv_config
from builders.models import (
    RoomGetTrigger,
    ItemDefinition,
    MobDefinition,
    Faction,
    FactionAssignment,
    FactionRelationship,
    RoomAction,
    Trigger,
    Path,
    Procession)
from core.serializers import (
    KeyField,
    ContainerTypeField,
    ReferenceField)
from core.equipment_system import get_world_equipment_payload
from core.economy import economy_world, money_payload
from core.stat_system import (
    StatSystemValidationError,
    get_world_class_selection,
    get_world_stat_system,
    world_uses_classes,
)
from spawns import instances
from spawns.models import (
    Player,
    Item,
    Mob,
    Equipment,
    Alias,
    PlayerConfig,
    Mark,
    PlayerCurrencyBalance)
from system.models import SiteControl
from worlds.models import World, Zone, Room, RoomDetail


def _currency_definition_payload(currency):
    """Serialize a code-keyed catalog entry without repeating its code."""
    return {
        'name': currency.name,
        'plural_name': currency.plural_name or currency.name,
        'description': currency.description or '',
    }


def world_economy_payload(world):
    """Return the inherited, stable-code currency catalog for a world."""
    root_world = economy_world(world)
    prefetched = getattr(root_world, '_prefetched_objects_cache', {})
    currencies = prefetched.get('currencies')
    if currencies is None:
        currencies = root_world.currencies.all().order_by('code', 'id')
    else:
        currencies = sorted(
            currencies,
            key=lambda currency: (currency.code, currency.id),
        )

    currency_list = list(currencies)
    currencies_by_id = {
        currency.id: currency
        for currency in currency_list
    }
    default_currency = currencies_by_id.get(root_world.default_currency_id)
    if root_world.default_currency_id and default_currency is None:
        raise ValueError(
            "The world's default currency does not belong to its economy.")

    return {
        'revision': int(root_world.economy_revision),
        'default_currency': (
            default_currency.code if default_currency is not None else None),
        'currencies': {
            currency.code: _currency_definition_payload(currency)
            for currency in currency_list
        },
    }


def player_economy_payload(player):
    """Return one private sparse wallet snapshot with the default balance."""
    root_world = economy_world(player.world)
    prefetched = getattr(player, '_prefetched_objects_cache', {})
    rows = prefetched.get('currency_balances')
    if rows is None:
        rows = PlayerCurrencyBalance.objects.filter(
            player=player,
            amount__gt=0,
            currency__world_id=root_world.id,
        ).select_related('currency').order_by('currency__code', 'currency_id')

    positive_balances = {
        row.currency.code: int(row.amount)
        for row in rows
        if row.amount > 0 and row.currency.world_id == root_world.id
    }
    default_currency = None
    if root_world.default_currency_id:
        default_currency = root_world.default_currency
        if default_currency.world_id != root_world.id:
            raise ValueError(
                "The world's default currency does not belong to its economy.")

    balances = {}
    if default_currency is not None:
        balances[default_currency.code] = positive_balances.pop(
            default_currency.code,
            0,
        )
    for code in sorted(positive_balances):
        balances[code] = positive_balances[code]

    return {
        'wallet_revision': int(player.wallet_revision),
        'balances': balances,
    }


class PlayerSerializer(serializers.ModelSerializer):

    archetype = serializers.CharField(required=False, allow_blank=True)
    can_transfer = serializers.SerializerMethodField()
    core_faction = serializers.SerializerMethodField()
    world_name = serializers.CharField(source='world.name', required=False)
    world_is_multi = serializers.BooleanField(source='world.is_multiplayer',
                                              required=False)
    world_key = serializers.CharField(source='world.key', read_only=True)
    world_id = serializers.IntegerField(source='world.id', read_only=True)
    root_world_id = serializers.IntegerField(
        source='world.context.id', read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    title = serializers.SerializerMethodField()
    is_staff = serializers.BooleanField(source='user.is_staff', read_only=True)
    is_confirmed = serializers.BooleanField(source='user.is_confirmed',
                                            read_only=True)
    link_id = serializers.IntegerField(source='user.link_id', read_only=True)

    class Meta:
        model = Player
        fields = [
            'key', 'name', 'description',
            'id', 'level', 'gender', 'title', 'glory',
            'archetype', 'core_faction', 'display_faction',
            'is_builder', 'is_staff', 'is_confirmed', 'link_id',
            # For single player worlds only, indicates whether the player
            # is eligible for a transfer.
            'can_transfer',
            'world_id', 'world_name', 'world_key', 'user_id', 'root_world_id',
            'world_is_multi',
            'last_connection_ts',
        ]

    def validate_name(self, value):
        """
        Because users may pass more than 1 word as the name, we only look
        at the first passed token here, and process the rest to 'title'.
        """
        return Player.validate_name(
            world=self.context['view'].world,
            name=value)

    def validate(self, validated_data):
        world = self.context['view'].world

        if not self.context['view'].world.config.can_create_chars:
            raise serializers.ValidationError(
                "Character creation is disabled for this world.")

        # If more than 1 word was passed at the name and a title was not
        # passed, capture the rest of name string as the title
        if (self.context['view'].world.is_multiplayer
            and 'name' in self.initial_data
            and ' ' in self.initial_data['name']
            and not 'title' in self.initial_data):
            title = ' '.join(self.initial_data['name'].split()[1:])
            validated_data['title'] = title

        if not world.config.non_ascii_names:
            if (self.initial_data.get('name')
                and not is_ascii(self.initial_data['name'])):
                raise serializers.ValidationError(
                    "Names must be ASCII characters only.")

        try:
            stat_system = get_world_stat_system(world)
        except StatSystemValidationError:
            stat_system = {}
        class_profiles = stat_system.get("class_profiles") or {}
        if class_profiles:
            class_selection = get_world_class_selection(world)
            requested_archetype = str(
                validated_data.get(
                    "archetype",
                    self.initial_data.get("archetype", ""),
                ) or ""
            ).strip()
            default_archetype = (
                class_selection.get("default")
                or next(iter(class_profiles.keys()))
            )
            if class_selection.get("enabled", True):
                archetype = requested_archetype or default_archetype
                if archetype not in class_profiles:
                    raise serializers.ValidationError({
                        "archetype": "Invalid class for this world."
                    })
            else:
                archetype = default_archetype
            validated_data["archetype"] = archetype
        else:
            if "archetype" in validated_data or "archetype" in self.initial_data:
                validated_data["archetype"] = str(
                    validated_data.get(
                        "archetype",
                        self.initial_data.get("archetype", ""),
                    ) or ""
                ).strip()

        return validated_data

    def create(self, validated_data):

        if 'room' not in validated_data:
            try:
                room = validated_data['world'].context.config.starting_room
                validated_data['room'] = room
            except IndexError:
                raise serializers.ValidationError("World has no starting room")

        player = super().create(validated_data)

        # Initialize player and return it
        return player.initialize()

    def get_can_transfer(self, player):
        return player.world.lifecycle == api_consts.WORLD_STATE_COMPLETE

    def get_core_faction(self, player):
        qs = FactionAssignment.objects.filter(
            member_type__model='player',
            member_id=player.id)

        core_assignment = qs.filter(faction__type='core').first()
        if core_assignment:
            return core_assignment.faction.name

        policy = core_faction_policy(player.world.context or player.world)
        if policy.default:
            default_faction = Faction.objects.filter(
                world=player.world.context or player.world,
                type='core',
                code=policy.default).first()
            if default_faction:
                return default_faction.name
        return adv_consts.FACTION_CORE_HUMAN

    def get_title(self, player):
        if player.title:
            return player.title
        if player.archetype:
            return "the {archetype}".format(
                archetype=adv_utils.capfirst(player.archetype))
        return ""


class ItemSerializer(serializers.ModelSerializer):
    """Serialize a runtime item for game-facing APIs."""

    name = serializers.ReadOnlyField()
    description = serializers.ReadOnlyField()

    class Meta:
        model = Item
        fields = ['key', 'name']


class EnterGameSerializer(serializers.Serializer):
    player_key = serializers.CharField()

    def validate(self, data):
        player = Player.objects.get(pk=data['player_key'].split('.')[1])
        data['player'] = player
        return data


class ExitGameSerializer(serializers.Serializer):
    player_key = serializers.CharField()


class PlayerConfigSerializer(serializers.ModelSerializer):

    class Meta:
        model = PlayerConfig
        fields = [
            'room_brief',
            'combat_brief',
            'idle_logout',
            'display_connect',
            'display_chat',
            'mobile_map_width',
        ]

    def create(self, validated_data):
        return super().create(validated_data)

    def save(self, *args, **kwargs):
        instance = super().save(*args, **kwargs)

        # Process the idle logout attribute, update in game if applicable
        if 'idle_logout' in self.validated_data:
            player = instance.players.first()
            try:
                player.game_player.idle_logout = bool(
                    self.validated_data['idle_logout'])
            except AttributeError:
                pass

        return instance


# ==== Animation Serializers ====

class AnimateWorldSerializer(serializers.ModelSerializer):
    context = serializers.CharField(source='context.key')
    instance_of = serializers.SerializerMethodField(source='context.instance_of')
    leader = serializers.SerializerMethodField()

    # Config
    never_reload = serializers.BooleanField(source='config.never_reload')
    has_corpse_decay = serializers.BooleanField(
        source='config.has_corpse_decay')
    auto_equip = serializers.BooleanField(source='config.auto_equip')

    starting_room = KeyField(source='config.starting_room')
    death_room = KeyField(source='config.death_room')
    starting_level = serializers.IntegerField(source='config.starting_level')
    max_level = serializers.IntegerField(source='config.max_level')
    leveling_curve = serializers.JSONField(source='config.leveling_curve')
    death_mode = serializers.CharField(source='config.death_mode')
    combat_resolution_interval = serializers.FloatField(
        source='config.combat_resolution_interval')
    flee_to_unknown_rooms = serializers.BooleanField(
        source='config.flee_to_unknown_rooms')
    players_can_set_title = serializers.BooleanField(
        source='config.players_can_set_title')
    pvp_mode = serializers.CharField(source='config.pvp_mode')
    allow_pvp = serializers.BooleanField(
        source='config.allow_pvp', read_only=True)
    allow_combat = serializers.BooleanField(
        source='config.allow_combat')
    death_route = serializers.CharField(source='config.death_route')
    death_currency = serializers.CharField(
        source='config.death_currency.code', allow_null=True)
    death_currency_penalty = serializers.FloatField(
        source='config.death_currency_penalty')
    classless = serializers.SerializerMethodField()
    globals_enabled = serializers.BooleanField(
        source='config.globals_enabled')

    factions = serializers.SerializerMethodField()
    facts = serializers.SerializerMethodField()
    economy = serializers.SerializerMethodField()
    equipment = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id',
            'key',
            'name',
            'context',
            'instance_of',
            'instance_ref',
            'is_multiplayer',
            'never_reload',
            'starting_room',
            'death_room',
            'starting_level',
            'max_level',
            'leveling_curve',
            'combat_resolution_interval',
            'death_currency',
            'death_currency_penalty',
            'has_corpse_decay',
            'auto_equip',
            'globals_enabled',
            'factions',
            'death_mode',
            'flee_to_unknown_rooms',
            'death_route',
            'pvp_mode',
            'allow_pvp',
            'allow_combat',
            'players_can_set_title',
            'facts',
            'classless',
            'tier',
            'economy',
            'equipment',
            'leader',
        ]

    def get_factions(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world

        factions = {}
        for faction in root_world.world_factions.all():

            if faction.death_room:
                death_room_key = faction.death_room.get_game_key(spawn_world)
            else:
                death_room_key = None

            faction_ranks = []
            index = 0
            for rank in faction.ranks.order_by('standing'):
                index += 1
                faction_ranks.append({
                    'standing': rank.standing,
                    'name': rank.name,
                    'number': index
                })

            if not faction_ranks:
                faction_ranks = [{
                    'standing': 100,
                    'name': 'Recruit',
                    'number': 1
                }]

            factions[faction.code] = {
                'code': faction.code,
                'name': faction.name,
                'death_room': death_room_key,
                'friendly': [],
                'hostile': [],
                'ranks': faction_ranks,
                'is_default': faction.is_default,
                'is_core': faction.is_core,
            }
        return factions

    def get_facts(self, spawn_world):
        return get_state_snapshot(STATE_SCOPE_WORLD, spawn_world)

    def get_instance_of(self, spawn_world):
        base_context = spawn_world.context
        instance_context = base_context.instance_of
        if not instance_context:
            return None
        return instance_context.spawned_worlds.get(
            is_multiplayer=True).key

    def get_classless(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world
        return not world_uses_classes(root_world)

    def get_economy(self, spawn_world):
        return world_economy_payload(spawn_world)

    def get_equipment(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world
        return get_world_equipment_payload(root_world)

    def get_leader(self, spawn_world):
        leader = spawn_world.leader
        return leader.key if leader else None


class AnimateZoneSerializer(serializers.ModelSerializer):
    zone_data = serializers.SerializerMethodField()
    class Meta:
        model = Zone
        fields = ['id', 'key', 'name', 'zone_data']
    def get_zone_data(self, zone):
        return get_state_snapshot(STATE_SCOPE_ZONE, zone)


class AnimateRoomSerializer(serializers.ModelSerializer):

    zone = KeyField()
    north = KeyField()
    east = KeyField()
    south = KeyField()
    west = KeyField()
    up = KeyField()
    down = KeyField()

    context_room_id = serializers.ReadOnlyField(source='id')

    triggers_completion = serializers.SerializerMethodField()
    flags = serializers.SerializerMethodField()
    is_landmark = serializers.SerializerMethodField()

    owner_data = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = [
            'id', 'key', 'name', 'type', 'description', 'color', 'note',
            'x', 'y', 'z', 'zone', 'context_room_id',
            'is_landmark',
            'triggers_completion', 'transfer_to',
            'flags',
            'ownership_type',
            'owner_data',
            'price',
            'enters_instance',
        ] + list(adv_consts.DIRECTIONS)

    def get_triggers_completion(self, room):
        if room.transfer_to and room.world.id == 217:
            return True
        return False

    def get_flags(self, room):
        return ' '.join(room.flags.values_list('code', flat=True))

    def get_is_landmark(self, room):
        if room.is_landmark:
            return True
        if room.type == adv_consts.ROOM_TYPE_WATER:
            return True
        return False

    def get_owner_data(self, room):
        if room.housing_block and room.housing_block.owner:
            return ReferenceField().to_representation(room.housing_block.owner)
        return None

    def get_price(self, room):
        if room.housing_block:
            return room.housing_block.price
        return None


class AnimateRoomDetailSerializer(serializers.ModelSerializer):

    room = serializers.CharField(source='room.key')

    class Meta:
        model = RoomDetail
        fields = ['id', 'key', 'room', 'keywords', 'description', 'is_hidden']


class AnimateItemSerializer(serializers.ModelSerializer):
    #in_container = KeyField(source='container')
    chunk_type = serializers.SerializerMethodField()
    ground_description = serializers.SerializerMethodField()
    keywords = serializers.SerializerMethodField()
    in_container = serializers.SerializerMethodField()
    augment = KeyField()
    value = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id', 'key', 'chunk_type',
            'definition_id', 'definition_slug_snapshot',
            'in_container',
            'ground_description', 'keywords', 'label',
            'augment', 'value',
        ]

    def get_fields(self):
        fields = super().get_fields()

        authored_fields = [
            'name', 'level', 'description',
            'type', 'is_persistent', 'quality',
            'food_value', 'food_type',
            'is_boat', 'is_pickable', 'capacity',
            'equipment_type', 'armor_class', 'weapon_type',
            'weapon_grip', 'weapon_damage', 'hit_msg_first', 'hit_msg_third',
            'attributes',
            'health_max', 'health_regen',
            'energy_max', 'energy_regen',
            'stamina_max', 'stamina_regen',
            'attack_power', 'ability_power', 'weapon_damage', 'armor', 'crit',
            'resilience', 'dodge',
            'on_use_cmd', 'on_use_description', 'on_use_equipped',
        ]

        for field_name in authored_fields:
            fields[field_name] = serializers.ReadOnlyField()

        return fields

    def get_chunk_type(self, obj):
        return 'item'

    def get_in_container(self, obj):
        container = obj.container
        if not container:
            return None
        if isinstance(container, Equipment):
            return container.char.key
        return container.key

    def get_ground_description(self, item):
        if item.ground_description:
            return item.ground_description

        name = item.name
        verb = 'lies'
        if not item.is_pickable:
            verb = 'is'

        return "{name} {verb} here.".format(
            name=adv_utils.capfirst(name),
            verb=verb)

    def get_keywords(self, item):

        # If actual keywords were defined, we simply take those
        keywords = item.keywords
        if keywords:
            # Exclude name tokens, normalize to lowercase
            tokens = [
                token.lower() for token in re.split('\W+', keywords)
                if token not in adv_consts.EXCLUDE_NAME_TOKENS
            ]

        # If no keywords were defined, derive them from the item name.
        if not keywords:
            name = item.name
            keywords = name or ''

            # Exclude name tokens, normalize to lowercase
            tokens = list(reversed([
                token.lower() for token in re.split('\W+', keywords)
                if token not in adv_consts.EXCLUDE_NAME_TOKENS
            ]))

        tokens = [ token for token in tokens if token ]

        # Because we want 'gauntlet' to be a valid token to pick up 'gauntlets'
        # we generate a second token list with singular version of encountered
        # (presumably) plural words.
        plural_tokens = []
        for token in tokens:
            if token[-1].lower() == 's':
                plural_tokens.append(token[:-1])
        tokens.extend(plural_tokens)

        # Add quality
        quality = item.quality
        if quality:
            tokens.append(quality)

        # Add the keyword 'item' for all items
        tokens.append('item')

        # Add shield / weapon / armor tokens
        eq_type = item.equipment_type
        if not eq_type : pass
        elif eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
            tokens.append('shield')
        elif eq_type.startswith('weapon'):
            tokens.append('weapon')
        else:
            tokens.append('armor')

        # Add container token
        item_type = item.type
        if item_type == adv_consts.ITEM_TYPE_CONTAINER:
            tokens.append('container')

        tokens = [ token.lower() for token in tokens ]

        return ' '.join(tokens)

    def get_value(self, item):
        if item.cost is None or item.currency is None:
            return None
        return money_payload(int(item.cost), item.currency)


class AnimateItemDeletionSerializer(serializers.ModelSerializer):
    chunk_type = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id', 'key', 'chunk_type',
        ]

    def get_chunk_type(self, obj):
        return 'item_deletion'


class AnimateItemActionSerializer(serializers.ModelSerializer):

    # item
    # Will be populated by injection with the item key

    class Meta:
        model = RoomAction
        fields = [
            'id',
            'key',
            #'item',
            'actions',
            'commands',
            'conditions',
            'show_details_on_failure',
            'failure_message',
            'display_action_in_room',
            'gate_delay',
        ]


class AnimateMobSerializer(serializers.ModelSerializer):
    room = KeyField()
    room_description = serializers.SerializerMethodField()
    keywords = serializers.SerializerMethodField()
    factions = serializers.SerializerMethodField()
    is_merchant = serializers.SerializerMethodField()
    currency_rewards = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    roams = serializers.SerializerMethodField()

    class Meta:
        model = Mob
        fields = [
            'id', 'key', 'room', 'definition_id', 'definition_slug_snapshot',
            'health', 'energy', 'stamina',
            'group_id',
            'room_description', 'keywords',
            'factions',
            'is_merchant', 'currency_rewards',
            'reactions',
            'roams',
        ]

    def get_fields(self):
        fields = super().get_fields()

        authored_fields = [
            'level', 'name', 'description',
            'type', 'archetype', 'gender', 'exp_worth', 'roaming_type',
            'alignment', 'aggression',
            'hit_msg_first', 'hit_msg_third',
            'health_max', 'health_regen',
            'energy_max', 'energy_regen',
            'stamina_max', 'stamina_regen',
            'regen_rate',
            'attributes',
            'attack_power', 'ability_power', 'armor', 'crit',
            'resilience', 'dodge',
            'fights_back', 'use_abilities', 'combat_script',
            'roam_chance',
            'control_flag', 'flags',
            'is_elite', 'is_invisible',
            'traits',
        ]
        for field_name in authored_fields:
            fields[field_name] = serializers.ReadOnlyField()

        return fields

    # Getters

    def get_is_merchant(self, mob):
        return bool(mob.definition and mob.definition.merchant_profile_id)

    def get_room_description(self, mob):
        if mob.room_description:
            return mob.room_description

        name = mob.name
        title = mob.title
        if title:
            title = ' ' + title
        return "{name}{title} is here.".format(
            name=adv_utils.capfirst(name),
            title=title)

    def get_keywords(self, mob):

        keywords = mob.keywords
        # If no keywords were defined, derive them from the mob name.
        if not keywords:
            name = mob.name
            keywords = ' '.join(list(reversed([
                token.lower() for token in re.split('\W+', name)
                if token not in adv_consts.EXCLUDE_NAME_TOKENS
            ])))

        tokens = keywords.split(' ')

        # Add the keyword 'mob' for all mobs
        tokens.append('mob')

        # Add the mob's gender
        gender = mob.gender
        tokens.append(gender)

        # Add the mob's key
        tokens.append(mob.key)

        # Add the mob's faction codes
        factions = mob.factions
        core_faction = factions.pop('core', None)
        if core_faction:
            tokens.append(core_faction)
        tokens.extend(factions.keys())

        tokens = [ token.lower() for token in tokens ]

        return ' '.join(tokens)

    def get_factions(self, mob):
        """
        This method is very similar to core.model_mixins.CharMixin.
        Re-defining it here allows to make sure that mobs only use the
        defaulting mechanism if humanoid. Beasts do not default to a core
        faction.

        The reason we overwrite it is that a beast would return a default
        if treated as a Char, so we can't just delegate to `char.factions`
        and then return whatever it gives. If a non-humanoid, and if there is
        no explicit assignment we want to return nothing.
        """

        mob_type = mob.type

        faction_source = mob.definition or mob
        fa_qs = faction_source.faction_assignments.all()

        core_assignment = fa_qs.filter(faction__type='core').first()
        core_faction = core_assignment.faction.code if core_assignment else None
        """
        if mob_type == adv_consts.MOB_TYPE_HUMANOID and not core_faction:
            core_factions = Faction.objects.filter(
                world=mob.world.context,
                is_core=True,
                is_selectable=True)
            default_factions = core_factions.filter(is_default=True)
            if default_factions:
                core_faction = default_factions.first().code
            elif core_factions:
                core_faction = core_factions.first().code
        """

        factions = {'core': core_faction} if core_faction else {}

        # get other factions
        for f_assignment in fa_qs.filter(faction__type='reputation'):
            factions[f_assignment.faction.code] = f_assignment.value

        return factions

    def get_currency_rewards(self, mob):
        snapshot = mob.currency_reward_snapshot or {}
        return [
            {'amount': int(snapshot[code]), 'currency': code}
            for code in sorted(snapshot)
        ]

    def get_reactions(self, mob):
        if not mob.definition_id:
            return []
        mob_definition_ct = ContentType.objects.get_for_model(MobDefinition)
        return AnimateMobReactionSerializer(
            Trigger.objects.filter(
                world_id=mob.definition.world_id,
                kind=adv_consts.TRIGGER_KIND_EVENT,
                target_type=mob_definition_ct,
                target_id=mob.definition_id,
                is_active=True,
            ).order_by('order', 'created_ts', 'id'),
            many=True).data

    def get_roams(self, mob):
        return mob.roams.key if mob.roams else None


class AnimatePlayerSerializer(serializers.ModelSerializer):

    # References
    room = serializers.CharField(source='room.key')
    home = serializers.SerializerMethodField()

    # User properties
    is_temporary = serializers.BooleanField(source='user.is_temporary')
    player_housing = serializers.BooleanField(source='user.player_housing')
    name_recognition = serializers.BooleanField(source='user.name_recognition')
    is_staff = serializers.BooleanField(source='user.is_staff')
    is_confirmed = serializers.BooleanField(source='user.is_confirmed')
    link_id = serializers.IntegerField(source='user.link_id')

    # Config attributes
    idle_logout = serializers.BooleanField(source='config.idle_logout')

    # User Flags
    # notell = serializers.SerializerMethodField()
    # noplay = serializers.SerializerMethodField()
    nochat = serializers.SerializerMethodField()
    is_muted = serializers.SerializerMethodField()
    cooldowns = serializers.SerializerMethodField()
    effects = serializers.SerializerMethodField()

    archetype = serializers.SerializerMethodField()
    room_description = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    autoflee = serializers.SerializerMethodField()
    keywords = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    config = serializers.SerializerMethodField()
    effects = serializers.SerializerMethodField()
    marks = serializers.SerializerMethodField()
    clan = serializers.SerializerMethodField()
    economy = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = [
            'id', 'key', 'name', 'title', 'level', 'gender', 'keywords',
            'description',
            'factions', 'aliases', 'language_proficiency',
            'glory', 'economy',
            'is_builder', 'is_invisible', 'autoflee',
            #'notell',
            #'noplay',
            'nochat', 'is_muted',
            'archetype', 'room', 'user_id',
            'experience', 'is_temporary',
            'health', 'stamina', 'energy',
            'state',
            'room_description',
            'config', 'effects', 'marks',
            'user_name', 'is_staff', 'is_confirmed', 'link_id',
            'player_housing', 'name_recognition',
            'home',
            'idle_logout',
            'mute_list', 'channels', 'clan', 'cooldowns', 'effects',
        ]

    def get_archetype(self, player):
        return player.archetype or ""

    def get_room_description(self, player):
        player_reference = adv_utils.capfirst(player.name)
        if player.title:
            player_reference += " " + player.title
        return "%s is here." % player_reference

    def get_aliases(self, player):
        aliases = AnimateAliasSerializer(player.aliases, many=True).data

        # This will return a list of the serialized aliases, but instead
        # we want to pass the data structure as a dict with the alias
        # as the key.
        return_dict = {}
        for alias_dict in aliases:
            return_dict[alias_dict['match']] = alias_dict

        return return_dict

    def get_autoflee(self, player):
        # If the player's world mandates an autoflee, use that. In practice,
        # this will only happen in the intro world.
        return player.world.config.autoflee or 0

    def get_effects(self, player): return {}

    def get_config(self, player):
        return {'use_grapevine': player.user.use_grapevine}

    def get_user_name(self, player):
        return player.user.username or ''

    def get_home(self, player):
        # TODO: If at some point we enable multiple houses per world,
        # there would need to be some determining factor taken into
        # consideration here.
        if not player.user.player_housing:
            return None

        blocks = player.housing_blocks.all()
        if not blocks.count():
            return None

        return blocks.first().block_rooms.first().key

    def get_is_muted(self, player):
        if player.is_muted:
            return True
        if player.user.is_muted:
            return True
        return False

    def get_nochat(self, player):
        if player.nochat: return True
        if player.user.nochat: return True
        return False

    # def get_notell(self, player):
    #     if player.user.flags.filter(code=api_consts.USER_FLAG_NOTELL).exists():
    #         return True
    #     return False

    # def get_nochat(self, player):
    #     if player.user.flags.filter(code=api_consts.USER_FLAG_NOCHAT).exists():
    #         return True
    #     return False

    # def get_noplay(self, player):
    #     if player.user.flags.filter(code=api_consts.USER_FLAG_NOPLAY).exists():
    #         return True
    #     return False

    def get_keywords(self, player):
        keywords = [player.name.lower(), 'player', player.key]
        fa_qs = player.faction_assignments.all()
        core_assignment = fa_qs.filter(faction__type='core').first()
        if core_assignment:
            keywords.append(core_assignment.faction.code.lower())
        return ' '.join(keywords)

    def get_marks(self, player):
        return get_state_snapshot(STATE_SCOPE_CHARACTER, player)

    def get_clan(self, player):
        return player.clan

    def get_cooldowns(self, player):
        cooldowns = player.cooldowns
        if cooldowns:
            return json.loads(player.cooldowns)
        return {}

    def get_effects(self, player):
        effects = {}
        if player.effects:
            return json.loads(player.effects)
        return effects

    def get_economy(self, player):
        return player_economy_payload(player)


class AnimateEquipmentSerializer(serializers.ModelSerializer):
    char = serializers.SerializerMethodField()
    weapon = KeyField()
    offhand = KeyField()
    head = KeyField()
    shoulders = KeyField()
    body = KeyField()
    arms = KeyField()
    hands = KeyField()
    waist = KeyField()
    legs = KeyField()
    feet = KeyField()
    accessory = KeyField()
    equipment_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Equipment
        fields = [
            'id', 'key', 'char', 'equipment_id',
            'weapon', 'offhand', 'head', 'shoulders', 'body', 'arms', 'hands',
            'waist', 'legs', 'feet', 'accessory',
        ]
    def get_char(self, eq):
        try:
            return eq.player.key
        except AttributeError:
            return eq.mob.key


class AnimateRoomActionSerializer(serializers.ModelSerializer):
    room = serializers.CharField(source='room.key')
    class Meta:
        model = RoomAction
        fields = [
            'id',
            'key',
            'room',
            'actions',
            'commands',
            'conditions',
            'show_details_on_failure',
            'failure_message',
            'display_action_in_room',
            'gate_delay',
        ]


class AnimateRoomGetTriggerSerializer(serializers.ModelSerializer):
    room = serializers.CharField(source='room.key')
    class Meta:
        model = RoomGetTrigger
        fields = [
            'id', 'key', 'room', 'argument',
            'action', 'action_argument','message',
        ]


class AnimateAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alias
        fields = ['id', 'match', 'replacement']


class AnimateMobReactionSerializer(serializers.ModelSerializer):
    reaction_id = serializers.CharField(source='id', read_only=True)
    reaction = serializers.CharField(source='script')
    #conditions = serializers.SerializerMethodField()

    class Meta:
        model = Trigger
        fields = [
            'event', 'match', 'reaction', 'reaction_id',
            'conditions',
        ]


class AnimatePathSerializer(serializers.ModelSerializer):

    rooms = serializers.SerializerMethodField()

    class Meta:
        model = Path
        fields = [
            'id',
            'key',
            'name',
            'rooms',
            'max_per_room',
        ]

    def get_rooms(self, path):
        return ','.join([
            'room.%s' % i
            for i in path.rooms.values_list('id', flat=True)
        ])


class AnimateProcessionSerializer(serializers.ModelSerializer):

    faction_code = serializers.CharField(source='faction.code')
    room = serializers.CharField(source='room.key')

    class Meta:
        model = Procession
        fields = [
            'id',
            'key',
            'faction_code',
            'room',
        ]

    #def get_room(self, procession):
    #    return procession.room.key


# Extraction Serializers

class ExtractPlayerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Player
        fields = [
            'room',
            'experience',
            'level',
            'health',
            'energy',
            'stamina',
            'glory',
            'title',
            'last_action_ts',
            'mute_list',
            'channels',
            'is_invisible',
            'cooldowns',
            'effects',
            'wallet_revision',
        ]
        read_only_fields = ['wallet_revision']


class ExtractEquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = adv_consts.EQUIPMENT_SLOTS

    def save(self, *args, **kwargs):
        # Make sure that all items that are equipped belong to the
        # equipment container.
        instance = super().save(*args, **kwargs)

        #"""
        eq_item_pks = [
            getattr(instance, f'{slot}_id')
            for slot in adv_consts.EQUIPMENT_SLOTS
            if getattr(instance, f'{slot}_id') is not None
        ]
        #"""

        """
        # Gather the equipped item PKs
        eq_item_pks = []
        for slot in adv_consts.EQUIPMENT_SLOTS:
            eq_item = getattr(instance, slot, None)
            if eq_item:
                eq_item_pks.append(eq_item.pk)
        #"""

        # Update their container
        Item.objects.filter(
            pk__in=eq_item_pks
        ).update(
            container_type=ContentType.objects.get(model='equipment'),
            container_id=instance.pk,
        )

        return instance


# System serializers


class SpawnRewardsSerializer(serializers.Serializer):
    #mob_id = serializers.IntegerField()
    player_id = serializers.IntegerField()

    def validate_player_id(self, data):
        try:
            self.player = Player.objects.get(pk=data)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Player does not exist")

        if not self.player.in_game:
            raise serializers.ValidationError("Player not currently in game.")

        return data

    # def validate_mob_id(self, data):
    #     try:
    #         self.mob = Mob.objects.get(pk=data)
    #     except Mob.DoesNotExist:
    #         raise serializers.ValidationError("Mob does not exist.")
    #     return data


class LoadDefinitionSerializer(serializers.Serializer):

    world_id = serializers.IntegerField()
    definition_type = serializers.ChoiceField(choices=['item', 'mob'])
    definition_id = serializers.CharField()
    actor_type = serializers.ChoiceField(choices=['player', 'mob', 'room'])
    actor_id = serializers.IntegerField()
    room = serializers.IntegerField()
    cmd = serializers.CharField(required=False)

    def validate(self, data):
        # Determine the actor
        try:
            if data['actor_type'] == 'mob':
                actor = Mob.objects.get(pk=data['actor_id'])
            elif data['actor_type'] == 'player':
                actor = Player.objects.get(pk=data['actor_id'])
            elif data['actor_type'] == 'room':
                actor = Room.objects.get(pk=data['actor_id'])
            else:
                raise ObjectDoesNotExist
        except ObjectDoesNotExist:
            raise serializers.ValidationError("Invalid actor ID.")
        data['actor'] = actor

        if data['actor_type'] == 'room':
            world = World.objects.get(pk=data['world_id'])
        else:
            world = actor.world

        data['spawn_world'] = world

        # Determine the authored definition.
        if world.context.instance_of:
            context = world.context.instance_of
        else:
            context = world.context
        definition_ref = str(data['definition_id']).strip()
        definition = None
        if data['definition_type'] == 'item':
            if definition_ref.isdigit():
                definition = ItemDefinition.objects.filter(
                    pk=int(definition_ref),
                    world=context,
                ).first()
            if definition is None:
                definition = ItemDefinition.objects.filter(
                    slug=definition_ref,
                    world=context,
                ).first()
        if definition is None and data['definition_type'] == 'mob':
            if definition_ref.isdigit():
                definition = MobDefinition.objects.filter(
                    pk=int(definition_ref),
                    world=context,
                ).first()
            if definition is None:
                definition = MobDefinition.objects.filter(
                    slug=definition_ref,
                    world=context,
                ).first()
        if definition is None:
            raise serializers.ValidationError(
                "Definition does not belong to this world")
        data['definition'] = definition

        return data

    def validate_room(self, room_id):
        # Determine the room
        try:
            return Room.objects.get(pk=room_id)
        except Room.DoesNotExist:
            raise serializers.ValidationError("Invalid Room ID")


class WorldCompletionSerializer(serializers.Serializer):

    player = serializers.IntegerField()

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")


class EnterInstanceSerializer(serializers.Serializer):
    player = serializers.IntegerField()
    instance = serializers.IntegerField()
    data = serializers.ListField()

    def validate_player(self, player):
        return Player.objects.get(pk=player)

    def validate_instance(self, instance):
        return World.objects.get(pk=instance)


class ExitInstanceSerializer(serializers.Serializer):
    player = serializers.IntegerField()
    data = serializers.ListField()

    def validate_player(self, player):
        return Player.objects.get(pk=player)
