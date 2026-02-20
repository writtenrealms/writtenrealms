import json
import re
from core import utils as adv_utils

from config import constants as adv_consts
from core.utils import is_ascii

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from rest_framework import serializers
from rest_framework.fields import Field

from core.utils import capfirst, format_actor_msg, has_number

from config import constants as api_consts
from config import game_settings as adv_config
from builders.models import (
    Currency,
    RoomCommandCheck,
    RoomGetTrigger,
    RoomCheck,
    Quest,
    Objective,
    Reward,
    ItemTemplate,
    MobTemplate,
    TransformationTemplate,
    MerchantInventory,
    Faction,
    FactionAssignment,
    FactionRelationship,
    RoomAction,
    Trigger,
    Path,
    Procession,
    Rule)
from core.serializers import (
    KeyField,
    InstanceOrTemplateValueField,
    ContainerTypeField,
    ReferenceField)
from spawns import instances
from spawns.models import (
    Player,
    Item,
    Mob,
    Equipment,
    PlayerEnquire,
    PlayerQuest,
    Alias,
    PlayerFlexSkill,
    PlayerEvent,
    PlayerConfig,
    Mark)
from system.models import SiteControl
from worlds.models import World, Zone, Room, RoomDetail


class PlayerSerializer(serializers.ModelSerializer):

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
            'is_immortal', 'is_staff', 'is_confirmed', 'link_id',
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

        core_assignment = qs.filter(faction__is_core=True).first()
        if core_assignment:
            return core_assignment.faction.name

        default_faction = Faction.objects.filter(
            world=player.world.context,
            is_core=True,
            is_default=True).first()
        if default_faction:
            FactionAssignment.objects.create(
                member=player,
                faction=default_faction)
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
    """
    Serizlier that takes an item and makes it itself + its template so that
    the data will look like how the Game engine expects it.
    """

    name = serializers.ReadOnlyField(source='template.name')
    description = serializers.ReadOnlyField(source='template.description')

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
    death_mode = serializers.CharField(source='config.death_mode')
    flee_to_unknown_rooms = serializers.BooleanField(
        source='config.flee_to_unknown_rooms')
    players_can_set_title = serializers.BooleanField(
        source='config.players_can_set_title')
    allow_pvp = serializers.BooleanField(
        source='config.allow_pvp')
    allow_combat = serializers.BooleanField(
        source='config.allow_combat')
    death_route = serializers.CharField(source='config.death_route')
    death_gold_penalty = serializers.FloatField(
        source='config.death_gold_penalty')
    classless = serializers.SerializerMethodField()
    globals_enabled = serializers.BooleanField(
        source='config.globals_enabled')
    #classless = serializers.BooleanField(source='config.is_classless')

    factions = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    feats = serializers.SerializerMethodField()
    facts = serializers.SerializerMethodField()
    socials = serializers.SerializerMethodField()
    currencies = serializers.SerializerMethodField()

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
            'death_gold_penalty',
            'has_corpse_decay',
            'auto_equip',
            'globals_enabled',
            'factions',
            'death_mode',
            'skills',
            'feats',
            'flee_to_unknown_rooms',
            'death_route',
            'allow_pvp',
            'allow_combat',
            'players_can_set_title',
            'facts',
            'classless',
            'tier',
            'socials',
            'currencies',
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

    def get_skills(self, spawn_world):
        import inspect
        from builders.serializers import SkillDetailSerializer

        ret_data = {}

        """
        for name, obj in inspect.getmembers(skills):
            try:
                mro = inspect.getmro(obj)
            except AttributeError:
                continue

            # Find classes that inherit ClassSkill as well as Skill,
            # which should exclude the mixins only.
            if (skills.ClassSkillMixin in mro
                and skills.Skill in mro):
                skill = obj

                if skill.archetype not in ret_data:
                    ret_data[skill.archetype] = {}

                if skill.level:
                    ret_data[skill.archetype][skill.code] = {
                        'code': skill.code,
                        'archetype': skill.archetype,
                        'level': skill.level,
                        'name': skill.name,
                        'is_flex': skill.is_flex,
                        'is_feat': bool(skill.feat_code),
                        'default_hotkey': skill.default_hotkey,
                        'stances': getattr(skill, 'stances', []),
                        'disabled': getattr(skill, 'disabled', []),
                    }

        # Go through each class's skills and determine core vs flex skills
        for archetype, arch_data in ret_data.items():

            core_skills = []
            flex_skills = []
            feat_skills = []
            for skill_code, skill_data in arch_data.items():
                if not skill_data['is_flex']:
                    if skill_data['is_feat']:
                        feat_skills.append(skill_data)
                    else:
                        core_skills.append(skill_data)
                else:
                    flex_skills.append(skill_data)

            # Sort flex skills first by skill and then by level
            flex_skills = sorted(flex_skills, key=lambda s: s['name'])
            flex_skills = sorted(flex_skills, key=lambda s: s['level'])
            flex_skill_codes = [ s['code'] for s in flex_skills ]
            arch_data['flex'] = flex_skill_codes

            # Sort core skills by level
            core_skills = sorted([
                skill for skill in core_skills
            ], key=lambda s: s['level'])

            # Sort core skills by default hotkey
            core_skills = sorted([
                skill for skill in core_skills
            ], key=lambda s: s['default_hotkey'])

            arch_data['core'] = [ s['code'] for s in core_skills ]

            # Sort feat skills by code and then by level
            feat_skills = sorted(feat_skills, key=lambda s: s['code'])
            feat_skills = sorted(feat_skills, key=lambda s: s['level'])
            feat_skill_codes = [ s['code'] for s in feat_skills ]
            arch_data['feat'] = feat_skill_codes
        """

        # Get classless skills
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world
        defs = {}
        for skill in root_world.skills.all():
            defs[skill.code] = SkillDetailSerializer(skill).data
        ret_data['custom'] = {'definitions': defs}

        return ret_data

    def get_feats(self, spawn_world):
        return {}

    def get_facts(self, spawn_world):
        return json.loads(spawn_world.facts or '{}')

    def get_instance_of(self, spawn_world):
        base_context = spawn_world.context
        instance_context = base_context.instance_of
        if not instance_context:
            return None
        return instance_context.spawned_worlds.get(
            is_multiplayer=True).key

    def get_socials(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world

        socials = {'cmds': {}, 'order': []}
        socials_qs = root_world.socials.all().order_by('cmd')
        for social in socials_qs:
            socials['cmds'][social.cmd] = [
                social.msg_targetless_self,
                social.msg_targetless_other,
                social.msg_targeted_self,
                social.msg_targeted_target,
                social.msg_targeted_other,
            ]
            socials['order'].append(social.cmd)
        return socials

    def get_classless(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world
        return root_world.config.is_classless

    def get_currencies(self, spawn_world):
        root_world = spawn_world.context
        root_world = root_world.instance_of or root_world
        currencies = {}
        for currency in root_world.currencies.all():
            currencies[currency.id] = {
                'code': currency.code,
                'name': currency.name,
                'is_default': currency.is_default,
            }
        return currencies

    def get_leader(self, spawn_world):
        leader = spawn_world.leader
        return leader.key if leader else None


class AnimateZoneSerializer(serializers.ModelSerializer):
    zone_data = serializers.SerializerMethodField()
    class Meta:
        model = Zone
        fields = ['id', 'key', 'name', 'is_warzone', 'zone_data']
    def get_zone_data(self, zone):
        return json.loads(zone.zone_data)


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
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id', 'key', 'chunk_type',
            'template_id', 'profile_id', 'in_container', 'rule_id',
            'ground_description', 'keywords', 'cost', 'label', 'upgrade_count',
            'augment', 'currency',
        ]

    def get_fields(self):
        fields = super().get_fields()

        # Add in the template fields
        template_fields = [
            'name', 'level', 'description',
            'type', 'is_persistent', 'quality', 'cost', #'currency',
            'food_value', 'food_type',
            'is_boat', 'is_pickable', 'capacity',
            'equipment_type', 'armor_class', 'weapon_type',
            'weapon_grip', 'hit_msg_first', 'hit_msg_third',
            'health_max', 'health_regen',
            'mana_max', 'mana_regen',
            'stamina_max', 'stamina_regen',
            'strength', 'constitution', 'dexterity', 'intelligence',
            'attack_power', 'spell_power', 'armor', 'crit',
            'resilience', 'dodge',
            'skill_modifier',
            'on_use_cmd', 'on_use_description', 'on_use_equipped',
        ]

        for template_field_name in template_fields:
            _field = InstanceOrTemplateValueField()
            fields[template_field_name]  = _field

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
        if item.template and item.template.ground_description:
            return item.template.ground_description
        elif item.ground_description:
            return item.ground_description

        name = item.template.name if item.template else item.name
        verb = 'lies'
        if ((item.template and not item.template.is_pickable)
            or not item.is_pickable):
            verb = 'is'

        return "{name} {verb} here.".format(
            name=adv_utils.capfirst(name),
            verb=verb)

    def get_keywords(self, item):

        # If actual keywords were defined, we simply take those
        keywords = item.keywords
        if not keywords and item.template:
            keywords = item.template.keywords

        if keywords:
            # Exclude name tokens, normalize to lowercase
            tokens = [
                token.lower() for token in re.split('\W+', keywords)
                if token not in adv_consts.EXCLUDE_NAME_TOKENS
            ]

        # If neither item or template has keywords defined, we take the
        # name of the item or template, and then break it down
        if not keywords:
            name = item.template.name if item.template else item.name
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
        quality = item.template.quality if item.template else item.quality
        if quality:
            tokens.append(quality)

        # Add the keyword 'item' for all items
        tokens.append('item')

        # Add shield / weapon / armor tokens
        eq_type = item.template.equipment_type if item.template else item.equipment_type
        if not eq_type : pass
        elif eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
            tokens.append('shield')
        elif eq_type.startswith('weapon'):
            tokens.append('weapon')
        else:
            tokens.append('armor')

        # Add container token
        item_type = item.template.type if item.template else item.type
        if item_type == adv_consts.ITEM_TYPE_CONTAINER:
            tokens.append('container')

        tokens = [ token.lower() for token in tokens ]

        return ' '.join(tokens)

    def get_currency(self, item):
        if item.template:
            currency = item.template.currency
            return currency.code if currency else 'gold'
        currency = Currency.objects.filter(
            world=item.world.context, is_default=True
        ).first()
        return currency.code if currency else 'gold'


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
    has_quest = serializers.SerializerMethodField()
    room_description = serializers.SerializerMethodField()
    keywords = serializers.SerializerMethodField()
    factions = serializers.SerializerMethodField()
    is_merchant = serializers.SerializerMethodField()
    gold = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    roams = serializers.SerializerMethodField()

    class Meta:
        model = Mob
        fields = [
            'id', 'key', 'room', 'template_id', 'rule_id',
            'health', 'mana', 'stamina',
            'has_quest', 'group_id',
            'room_description', 'keywords',
            'factions',
            'is_merchant', 'gold',
            'reactions',
            'roams',
        ]

    def get_fields(self):
        fields = super().get_fields()

        template_fields = [
            'level', 'name', 'description',
            'type', 'archetype', 'gender', 'exp_worth', 'roaming_type',
            'alignment', 'aggression',
            'hit_msg_first', 'hit_msg_third',
            'health_max', 'health_regen',
            'mana_max', 'mana_regen',
            'stamina_max', 'stamina_regen',
            'regen_rate',
            'strength', 'constitution', 'dexterity', 'intelligence',
            'attack_power', 'spell_power', 'armor', 'crit',
            'resilience', 'dodge',
            'fights_back', 'use_abilities', 'combat_script',
            'roam_chance',
            'control_flag', 'flags',
            'is_elite', 'is_invisible', 'is_crafter', 'craft_multiplier',
            'merchant_profit',
            'teaches', 'teaching_conditions',
            'unlearns', 'unlearn_cost', 'traits',
            'is_upgrader', 'upgrade_cost_multiplier',

            #'drops_random_items', 'num_items',
            #'chance_normal', 'chance_imbued', 'chance_enchanted',
        ]
        for template_field_name in template_fields:
            _field = serializers.ReadOnlyField(
                source='template.%s' % template_field_name)
            fields[template_field_name]  = _field

        return fields

    def to_representation(self, instance):
        data = super().to_representation(instance)

        # Apply transformations
        mob = instance
        # process any future transformations, in order
        if mob.rule_id:
            trans_rules_qs = Rule.objects.filter(
                target_type=ContentType.objects.get_for_model(Rule),
                target_id=mob.rule_id,
                template_type=ContentType.objects.get_for_model(
                    TransformationTemplate))

            for trans_rule in trans_rules_qs:
                transformations = trans_rule.template.apply(mob)
                for attr, value in transformations.items():
                    data[attr] = value

        return data

    # Getters

    def get_has_quest(self, mob):
        return mob.template.template_quests.exists()

    def get_is_merchant(self, mob):
        return mob.template.merchant_inv.exists()

    def get_room_description(self, mob):
        if mob.template and mob.template.room_description:
            return mob.template.room_description
        elif mob.room_description:
            return mob.room_description

        name = mob.template.name if mob.template else mob.name
        title = mob.template.title if mob.template else mob.title
        if title:
            title = ' ' + title
        return "{name}{title} is here.".format(
            name=adv_utils.capfirst(name),
            title=title)

    def get_keywords(self, mob):

        keywords = mob.keywords
        if not keywords and mob.template:
            keywords = mob.template.keywords

        # If neither mob or template has keywords defined, we take the
        # name of the mob or template, and then break it down
        if not keywords:
            name = mob.template.name if mob.template else mob.name
            keywords = ' '.join(list(reversed([
                token.lower() for token in re.split('\W+', name)
                if token not in adv_consts.EXCLUDE_NAME_TOKENS
            ])))

        tokens = keywords.split(' ')

        # Add the keyword 'mob' for all mobs
        tokens.append('mob')

        # Add the mob's gender
        gender = mob.template.gender if mob.template else mob.gender
        tokens.append(gender)

        # Add the mob's key
        tokens.append(mob.key)

        # Add the mob's faction codes
        factions = mob.template.factions if mob.template else mob.factions
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

        mob_type = mob.template.type if mob.template else mob.type

        fa_qs = mob.template.faction_assignments.all()

        core_assignment = fa_qs.filter(faction__is_core=True).first()
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
        for f_assignment in fa_qs.filter(faction__is_core=False):
            factions[f_assignment.faction.code] = f_assignment.value

        return factions

    def get_gold(self, mob):
        if mob.template:
            return mob.template.gold or 0

        return mob.gold or 0

        if mob.template:
            if mob.template.type != adv_consts.MOB_TYPE_HUMANOID:
                return 0
            gold = mob.template.gold
            #level = mob.template.level
        else:
            if mob.type != adv_consts.MOB_TYPE_HUMANOID:
                return 0
            gold = mob.gold
            #level = mob.level
        return gold
        #return gold if gold else adv_config.ILF(level)

    def get_reactions(self, mob):
        if not mob.template_id:
            return []
        mob_template_ct = ContentType.objects.get_for_model(MobTemplate)
        return AnimateMobReactionSerializer(
            Trigger.objects.filter(
                world_id=mob.template.world_id,
                kind=adv_consts.TRIGGER_KIND_EVENT,
                target_type=mob_template_ct,
                target_id=mob.template_id,
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
    is_builder = serializers.BooleanField(source='user.is_builder')
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
    trophy = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    config = serializers.SerializerMethodField()
    effects = serializers.SerializerMethodField()
    marks = serializers.SerializerMethodField()
    clan = serializers.SerializerMethodField()
    currencies = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = [
            'id', 'key', 'name', 'title', 'level', 'gender', 'keywords',
            'description',
            'factions', 'aliases', 'language_proficiency',
            'gold', 'glory', 'medals', 'currencies',
            'is_immortal', 'is_invisible', 'autoflee',
            #'notell',
            #'noplay',
            'nochat', 'is_muted',
            'archetype', 'room', 'user_id',
            'experience', 'is_temporary', 'is_builder',
            'health', 'stamina', 'mana',
            'room_description',
            'trophy', 'skills', 'config', 'effects', 'marks',
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

    def get_trophy(self, player):
        trophy = {}
        for entry in player.trophy_entries.all():
            if entry.mob_template_id not in trophy:
                trophy[entry.mob_template_id] = 0
            trophy[entry.mob_template_id] += 1
        return trophy

    def get_skills(self, player):
        "Return flex & feat selections"
        flex_skills = {1: None, 2: None, 3: None}

        # Fetch custom skills.
        player_skills = json.loads(player.skills or "{}") or {}
        custom_skills = player_skills.get('custom', {})
        # Validate that all codes are still valid.
        if custom_skills != {}:
            root_world = player.world.context
            root_world = root_world.instance_of or root_world
            codes = root_world.skills.values_list('code', flat=True)
            custom_skills = {
                position: code for position, code in custom_skills.items()
                if code in codes
            }

        return {
            'flex': flex_skills,
            'feat': {},
            'custom': custom_skills
        }

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
        core_assignment = fa_qs.filter(faction__is_core=True).first()
        if core_assignment:
            keywords.append(core_assignment.faction.code.lower())
        return ' '.join(keywords)

    def get_marks(self, player):
        marks = {}
        for mark in player.marks.all():
            marks[mark.name] = mark.value
        return marks

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

    def get_currencies(self, player):
        currencies = {}
        if player.currencies:
            return json.loads(player.currencies)
        return currencies


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


class AnimateRoomCommandCheckSerializer(serializers.ModelSerializer):
    room = serializers.CharField(source='room.key')
    state = serializers.SerializerMethodField()
    check = serializers.CharField(source='check_type')
    #key = serializers.SerializerMethodField()

    class Meta:
        model = RoomCommandCheck
        fields = [
            'id', 'key', 'room',
            'disallow_commands', 'allow_commands',
            'check', 'argument',
            'failure_msg',
            'hint_msg',
            'state',
        ]

    def get_state(self, room_cmd_check):
        if room_cmd_check.track_state:
            world = self.context.get('world')
            state_q = room_cmd_check.room_cmd_check_states.filter(world=world)
            if state_q.exists():
                if state_q.get().passed_ts:
                    return adv_consts.ROOM_CHECK_STATE_PASSED
            return adv_consts.ROOM_CHECK_STATE_FAILED
        return None

    # def get_key(self, cmd_check):
    #     return cmd_check.get_game_key(self.context['world'])


class AnimateRoomCheckSerializer(serializers.ModelSerializer):
    prevent_entry = serializers.SerializerMethodField()
    prevent_exit = serializers.SerializerMethodField()
    check = serializers.CharField(source='check_type', allow_null=True)

    def get_prevent_entry(self, cmd_check):
        if cmd_check.prevent == adv_consts.ROOM_PREVENT_ENTER:
            return cmd_check.room.key
        return None

    def get_prevent_exit(self, cmd_check):
        if cmd_check.prevent == adv_consts.ROOM_PREVENT_EXIT:
            return cmd_check.room.key
        return None

    class Meta:
        model = RoomCheck
        fields = [
            'id',
            'key',
            'direction',
            'prevent_entry',
            'prevent_exit',
            'check',
            'argument',
            'argument2',
            'failure_msg',
            'conditions',
        ]

    def get_key(self, room_check):
        return room_check.get_game_key(self.context['world'])


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


class AnimateQuestSerializer(serializers.ModelSerializer):

    chunk_type = serializers.SerializerMethodField()
    mob = serializers.SerializerMethodField()
    objectives = serializers.SerializerMethodField()
    rewards = serializers.SerializerMethodField()
    requires_quest_id = serializers.SerializerMethodField()
    suggested_level = serializers.CharField(source='level', read_only=True)

    class Meta:
        model = Quest
        fields = [
            'id', 'key', 'chunk_type', 'mob', 'suggested_level',
            'name',
            'entrance_cmds',
            'repeat_entrance_cmd_after',
            'enquire_cmds',
            'enquire_keywords',
            'enquire_cmd_available',
            'is_hidden',
            'completion_action',
            'completion_cmds',
            'completion_keywords',
            'completion_cmd_available',
            'completion_entrance_cmds',
            'repeat_completion_entrance_cmds_after',
            'completion_despawn',
            'complete_silently',
            'incomplete_msg',
            'repeatable_after',
            'objectives', 'rewards',
            'requires_quest_id',
            'requires_level',
            'max_level',
            'conditions',
            'completion_conditions',
        ]

    def get_chunk_type(self, quest):
        return "quest"

    def get_mob(self, quest):
        return self.context['mob'].key

    def get_objectives(self, quest):
        return AnimateObjectiveSerializer(
            quest.objectives.all(), many=True).data

    def get_rewards(self, quest):
        return AnimateRewardSerializer(
            quest.rewards.all(), many=True).data

    def get_requires_quest_id(self, quest):
        if not quest.requires_quest:
            return None
        else:
            return quest.requires_quest.id


class AnimateObjectiveSerializer(serializers.ModelSerializer):

    currency = serializers.SerializerMethodField()

    class Meta:
        model = Objective
        fields = [
            'id', 'key', 'type', 'qty', 'template_id', 'currency'
        ]

    def get_currency(self, objective):
        if objective.currency:
            return objective.currency.code
        return None


class AnimateRewardSerializer(serializers.ModelSerializer):

    option = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Reward
        fields = ['id', 'key', 'type', 'qty', 'option', 'currency']

    def get_option(self, reward):
        if reward.type == adv_consts.REWARD_TYPE_FACTION:
            return reward.profile.code
        if reward.type == adv_consts.REWARD_TYPE_ITEM:
            return reward.profile.id
        return ""

    def get_currency(self, reward):
        if reward.currency:
            return reward.currency.code
        return None


class AnimatePlayerQuestSerializer(serializers.ModelSerializer):
    player = serializers.CharField(source='player.key')
    quest = serializers.SerializerMethodField()
    chunk_type = serializers.SerializerMethodField()

    class Meta:
        model = PlayerQuest
        fields = ['id', 'key', 'player', 'quest', 'completion_ts', 'chunk_type']

    def get_quest(self, player_quest):
        return player_quest.quest.get_game_key(self.context.get('world'))

    def get_chunk_type(self, player_quest):
        return "player_quest"


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
            'event', 'option', 'reaction', 'reaction_id',
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
            'mana',
            'stamina',
            'gold',
            'glory',
            'title',
            'medals',
            'last_action_ts',
            'mute_list',
            'channels',
            'is_invisible',
            'cooldowns',
            'effects',
            'currencies',
        ]


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


class LoadTemplateSerializer(serializers.Serializer):

    world_id = serializers.IntegerField()
    template_type = serializers.ChoiceField(choices=['item', 'mob'])
    template_id = serializers.IntegerField()
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

        # Determine the template
        if world.context.instance_of:
            context = world.context.instance_of
        else:
            context = world.context
        try:
            if data['template_type'] == 'item':
                template = ItemTemplate.objects.get(
                    pk=data['template_id'],
                    #world=world.context)
                    world=context)
            else:
                template = MobTemplate.objects.get(
                    pk=data['template_id'],
                    #world=world.context)
                    world=context)
        except ObjectDoesNotExist:
            raise serializers.ValidationError(
                "Template does not belong to this world")
        data['template'] = template

        return data

    def validate_room(self, room_id):
        # Determine the room
        try:
            return Room.objects.get(pk=room_id)
        except Room.DoesNotExist:
            raise serializers.ValidationError("Invalid Room ID")


class GenerateDropSerializer(serializers.Serializer):

    level = serializers.IntegerField()
    quality = serializers.ChoiceField(adv_consts.ITEM_QUALITIES)
    # Extraction data for the item that will be the container. This is needed
    # because if a mob dies, the API will know nothing of the corpse object
    # to load the random item into.
    #container_data = serializers.JSONField()
    world = serializers.IntegerField()

    def validate_world(self, value):
        try:
            return World.objects.get(pk=value)
        except World.DoesNotExist:
            raise serializers.ValidationError('Invalid world ID')


    def create(self, validated_data):
        from backend.core.drops import generate_equipment
        level = validated_data['level']
        quality = validated_data['quality']

        attrs = generate_equipment(
            level=level,
            quality=quality)

        # We are creating an item without a container, because the game
        # engine will then put it in a corpse and if we want the item to
        # actually be held on to, it will give a proper container then.
        return Item.objects.create(
            world=validated_data['world'],
            quality=quality,
            level=level,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            **attrs)

        return item


class WorldCompletionSerializer(serializers.Serializer):

    player = serializers.IntegerField()

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")


class QuestSerializerBase(serializers.Serializer):

    player = serializers.IntegerField()
    quest = serializers.IntegerField()

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")

    def validate_quest(self, quest_id):
        try:
            return Quest.objects.get(pk=quest_id)
        except Quest.DoesNotExist:
            raise serializers.ValidationError("Invalid quest id")


class QuestCompletionSerializer(QuestSerializerBase):

    def create(self, validated_data):
        player = validated_data['player']
        quest = validated_data['quest']
        player_quest, created = PlayerQuest.objects.get_or_create(
            player=player,
            quest=quest)
        player_quest.completion_ts = timezone.now()
        player_quest.save()
        return player_quest


class QuestEnquireSerializer(QuestSerializerBase):
    "Serializer to create the enquire record"

    def create(self, validated_data):
        player = validated_data['player']
        quest = validated_data['quest']
        player_enquire, created = PlayerEnquire.objects.get_or_create(
            player=player,
            quest=quest)
        player_enquire.enquire_ts = timezone.now()
        player_enquire.save()
        return player_enquire


class EnquiredQuestSerializer(serializers.ModelSerializer):

    enquire_cmds = serializers.SerializerMethodField()
    quest_giver = serializers.CharField(source='mob_template.name')
    quest_name = serializers.CharField(source='name')

    class Meta:
        model = Quest
        fields = [
            'id',
            'quest_name',
            'enquire_cmds',
            'quest_giver',
            'level',
            'summary',
        ]

    def get_enquire_cmds(self, quest):
        enquire_cmds = []

        quest_mob_name = capfirst(quest.mob_template.name)

        if quest.type == adv_consts.QUEST_TYPE_DELIVER:
            questlines = (quest.completion_cmds or "").splitlines()
        else:
            questlines = (quest.enquire_cmds or "").splitlines()

        for line in questlines:
            formatted_line = line
            line_tokens = line.split(' ')
            message = format_actor_msg(
                ' '.join(line_tokens[1:]),
                self.context['actor'])

            if (line_tokens[0] == 'say'):
                formatted_line = "%s says '%s'" % (
                    quest_mob_name, message)
            elif (line_tokens[0] == 'emote'):
                formatted_line = '%s %s' % (
                    quest_mob_name, message)
            elif (line_tokens[0] == 'echo'):
                formatted_line  = message
            else:
                continue
            #elif (line_tokens[0] == 'pass'):
            #    continue
            enquire_cmds.append(formatted_line)
        return enquire_cmds

    def get_quest_giver(self, enquire_quest):
        return enquire_quest.quest.mob_template.name


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
