import copy
import json
import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.text import slugify

from config import constants as adv_consts
from core.utils.mobs import suggest_stats
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    get_state_snapshot,
)

from rest_framework import serializers

from config import constants as api_consts
from config import game_settings as adv_config
from core.combat_formulas import (
    CombatFormulaValidationError,
    normalize_combat_system,
)
from core.equipment_system import (
    EquipmentSystemValidationError,
    get_armor_class_keys,
    has_authored_armor_classes,
    normalize_equipment_system,
    validate_armor_class_reference,
)
from core.leveling import (
    LevelingConfigError,
    normalize_leveling_curve,
    validate_leveling_config,
)
from core.economy import MAX_CURRENCY_AMOUNT, economy_world, money_payload
from core.stat_system import (
    StatSystemValidationError,
    normalize_stat_system,
    world_uses_classes,
)
from core.world_config import (
    INSTANCE_INHERITED_CONFIG_FIELDS,
    INSTANCE_LOCAL_CONFIG_FIELDS,
)
from builders.models import (
    BuilderAssignment,
    Currency,
    CraftMaterial,
    CraftingProfile,
    CraftingRecipe,
    LastViewedRoom,
    ItemBundle,
    ItemDefinition,
    MobDefinition,
    MerchantProfile,
    FACTION_TYPE_CORE,
    FACTION_TYPE_REPUTATION,
    Faction,
    FactionAssignment,
    FactionRank,
    FactSchedule,
    RoomAction,
    Trigger,
    Social,
    Path,
    PathRoom,
    WorldBuilder,
    WorldReview,
    Procession)
from core.db import qs_by_pks
from core.serializers import KeyNameSerializer, ReferenceField, AuthorField
from spawns import serializers as spawn_serializers
from spawns import trigger_matcher
from spawns.models import Player, DoorState, PlayerConfig, Mob, Item, Equipment
from system.models import Nexus
from system.policies import get_platform_policy
from users.models import User
from worlds import serializers as world_serializers
from worlds.models import (
    InstanceAssignment,
    InstanceRun,
    World,
    WorldConfig,
    Zone,
    Room,
    RoomFlag,
    RoomDetail,
    Door,
    WorldLocks)
from worlds.services import is_recoverable_lifecycle


def _coerce_attribute_map(value):
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            parsed = {}
            for line in value.splitlines():
                line = line.strip()
                if not line:
                    continue
                key, sep, raw_amount = line.partition(":")
                if not sep:
                    raise serializers.ValidationError(
                        "Attributes must be a JSON object or lines like `strength: 3`."
                    )
                key = key.strip()
                raw_amount = raw_amount.strip()
                try:
                    parsed[key] = float(raw_amount) if "." in raw_amount else int(raw_amount)
                except ValueError:
                    raise serializers.ValidationError(
                        f"Attribute '{key}' must have a numeric value."
                    )
            value = parsed
    if not isinstance(value, dict):
        raise serializers.ValidationError("Attributes must be a mapping.")
    normalized = {}
    for raw_key, raw_amount in value.items():
        key = str(raw_key or "").strip()
        if not key:
            raise serializers.ValidationError("Attribute keys must be non-empty strings.")
        if not isinstance(raw_amount, (int, float)) or isinstance(raw_amount, bool):
            raise serializers.ValidationError(
                f"Attribute '{key}' must have a numeric value."
            )
        normalized[key] = raw_amount
    return normalized


def validate_conditions(self, conditions):
        if isinstance(conditions, (dict, list)):
            from core.condition_dsl import validate_condition_payload
            try:
                json.dumps(conditions)
            except TypeError:
                raise serializers.ValidationError("Conditions must be JSON-serializable.")
            try:
                validate_condition_payload(conditions, field_name="conditions")
            except ValueError as exc:
                raise serializers.ValidationError(str(exc))
            return conditions
        from backend.core.conditions import (
            break_text, BREAK_TOKENS, CONDITIONS)
        for text in break_text(conditions):
            if text in BREAK_TOKENS: continue
            tokens = [ t.lower() for t in re.split('\W+', text) if t]
            condition_name = tokens[0].lower()
            args = tokens[1:]
            try:
                condition_spec = [
                    spec for spec in CONDITIONS
                    if spec['name'] == condition_name
                ][0]
            except IndexError:
                raise serializers.ValidationError(
                    "Invalid condition name '%s'" % condition_name)
            if len(args) < len(condition_spec['args']):
                raise serializers.ValidationError(
                    "Insufficient number of arguments to '%s'" % condition_name)
        return conditions


def _normalize_template_slug(serializer, value, *, model_cls, field_label):
    text = str(value or "").strip()
    if not text:
        return ""

    normalized = slugify(text)
    if not normalized:
        raise serializers.ValidationError(
            f"{field_label} slug must contain letters or numbers."
        )

    world = getattr(serializer.instance, "world", None) or serializer.context.get("world")
    if world:
        existing = model_cls.objects.filter(world=world, slug=normalized)
        if serializer.instance:
            existing = existing.exclude(pk=serializer.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                f"{field_label} slug is already in use in this world."
            )

    return normalized


_INSTANCE_CONFIG_LOCAL_ROOM_FIELDS = {"starting_room", "death_room", "exits_to"}
_WORLD_CONFIG_CLONE_SKIP_FIELDS = {
    "id",
    "created_ts",
    "modified_ts",
    *_INSTANCE_CONFIG_LOCAL_ROOM_FIELDS,
    *INSTANCE_INHERITED_CONFIG_FIELDS,
}


def _clone_world_config_for_instance(base_config):
    values = {}
    for field in WorldConfig._meta.fields:
        if field.primary_key or field.name in _WORLD_CONFIG_CLONE_SKIP_FIELDS:
            continue
        values[field.name] = copy.deepcopy(getattr(base_config, field.name))

    config = WorldConfig.objects.create(**values)
    return config


class WorldSerializer(serializers.ModelSerializer):
    """
    World as seen by a builder, which gets loaded by the
    frontend as the builder world.
    """

    last_viewed_room = serializers.SerializerMethodField()
    review = serializers.SerializerMethodField()
    author = AuthorField()
    factions = serializers.SerializerMethodField()
    facts = serializers.SerializerMethodField()
    is_classless = serializers.SerializerMethodField()
    instance_of = serializers.SerializerMethodField()
    builder_info = serializers.SerializerMethodField()
    currencies = serializers.SerializerMethodField()
    default_currency = serializers.SerializerMethodField()
    initial_currency_code = serializers.RegexField(
        r'^[a-z][a-z0-9_-]{0,63}$',
        write_only=True,
        required=False,
        default='gold')
    initial_currency_name = serializers.CharField(
        write_only=True,
        required=False,
        default='Gold')
    initial_currency_plural_name = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        default='Gold')
    state = serializers.CharField(source='lifecycle', read_only=True)

    class Meta:
        model = World
        fields = (
            'key', 'id', 'name', 'description', 'motd', 'author', 'created_ts',
            'last_viewed_room', 'short_description', 'state', 'is_multiplayer',
            'is_public', 'factions', 'facts', 'is_classless',
            'review', 'maintenance_mode', 'maintenance_msg', 'instance_of',
            'builder_info', 'currencies', 'default_currency',
            'initial_currency_code', 'initial_currency_name',
            'initial_currency_plural_name',
        )

    def validate(self, *args, **kwargs):
        request = self.context['request']
        if request.user.is_temporary:
            raise serializers.ValidationError(
                "Must sign up to create a world.")
        if self.instance is None:
            policy = get_platform_policy()
            if policy.world_creation != 'all' and not request.user.is_staff:
                raise serializers.ValidationError(
                    "World creation is currently disabled.")
        return super().validate(*args, **kwargs)

    def get_is_classless(self, world):
        return not world_uses_classes(world)

    def get_last_viewed_room(self, world):
        #from builders.serializers import RoomBuilderSerializer
        try:
            user = self.context['request'].user
        except KeyError:
            return None

        try:
            room = LastViewedRoom.objects.get(world=world, user=user).room
        except LastViewedRoom.DoesNotExist:
            room = world.config.starting_room
            LastViewedRoom.objects.create(world=world, user=user, room=room)
        return MapRoomSerializer(room).data

    def get_factions(self, world):
        world = world.context or world
        return FactionSerializer(
            world.world_factions.all(),
            many=True).data

    def get_facts(self, world):
        return get_state_snapshot(STATE_SCOPE_WORLD, world)

    def get_review(self, world):
        review = WorldReview.objects.filter(
            world=world
        ).order_by('-created_ts').first()

        if not review:
            return {
                'status': api_consts.WORLD_REVIEW_STATUS_UNSUBMITTED,
                'text': '',
                'reviewer': '',
            }
        else:
            reviewer = review.reviewer.username if review.reviewer else ''
            return {
                'status': review.status,
                'text': review.text,
                'reviewer': reviewer,
            }

    def get_instance_of(self, world):
        base_world = world.instance_of
        if not base_world: return {}
        return {
            'name': base_world.name,
            'id': base_world.id,
        }

    def get_builder_info(self, world):
        if (self.context['request'].user == world.author
            or self.context['request'].user.is_staff):
            return {
                'builder_rank': 4,
                'builder_id': None,
                'builder_assignments': [],
            }

        try:
            builder = WorldBuilder.objects.get(
                world=world,
                user=self.context['request'].user)
        except WorldBuilder.DoesNotExist:
            return {
                'builder_rank': 0,
                'builder_id': None,
                'builder_assignments': [],
            }

        assignments = []
        builder_assignments = builder.builder_assignments.prefetch_related(
            'assignment',
        )
        for builder_assignment in builder_assignments:
            assignments.append(
                ReferenceField().to_representation(
                    builder_assignment.assignment))

        return {
            'builder_rank': builder.builder_rank,
            'builder_id': builder.id,
            'builder_assignments': assignments,
        }

    def get_currencies(self, world):
        base_world = economy_world(world)
        return [
            {
                'id': currency.id,
                'name': currency.name,
                'plural_name': currency.plural_name or currency.name,
                'description': currency.description,
                'code': currency.code,
                'is_default': currency.id == base_world.default_currency_id,
            } for currency in base_world.currencies.all()
        ]

    def get_default_currency(self, world):
        return economy_world(world).default_currency_id and economy_world(world).default_currency.code

    def _instance_base_world(self):
        raw_instance_of = self.context['request'].data.get('instance_of')
        if not raw_instance_of:
            return None

        try:
            instance_of = World.objects.get(pk=raw_instance_of)
        except (TypeError, ValueError, World.DoesNotExist):
            raise serializers.ValidationError({
                'instance_of': 'Selected base world was not found.',
            })

        if not instance_of.is_multiplayer:
            raise serializers.ValidationError(
                'Cannot create an instance of a singleplayer world.')
        return instance_of

    def create(self, validated_data):
        if 'author' not in validated_data:
            validated_data['author'] = self.context['request'].user

        initial_currency_code = validated_data.pop('initial_currency_code', 'gold')
        initial_currency_name = validated_data.pop('initial_currency_name', 'Gold')
        initial_currency_plural_name = validated_data.pop(
            'initial_currency_plural_name', 'Gold')
        instance_of = self._instance_base_world()
        with transaction.atomic():
            if instance_of:
                validated_data['instance_of'] = instance_of
                validated_data['is_multiplayer'] = True
                if instance_of.config_id:
                    validated_data['config'] = _clone_world_config_for_instance(
                        instance_of.config)

            world = World.objects.new_world(**validated_data)

            if instance_of:
                return world

            from builders.currencies import create_currency

            initial_currency = create_currency(
                world=world,
                code=initial_currency_code,
                name=initial_currency_name,
                plural_name=initial_currency_plural_name)
            world.config.death_currency = initial_currency
            world.config.clan_registration_currency = initial_currency
            world.config.save(update_fields=[
                'death_currency',
                'clan_registration_currency',
            ])

            spawn_world = world.create_spawn_world()
            player = Player.objects.create(
                world=spawn_world,
                user=self.context['request'].user,
                name='Builder',
                is_builder=True,
                room=world.config.starting_room,
                last_connection_ts=timezone.now())
            player.initialize()

        return world


# World Config

class WorldConfigSerializer(serializers.ModelSerializer):

    death_room = ReferenceField(required=True, allow_null=False)
    starting_room = ReferenceField(required=True, allow_null=False)

    class Meta:
        model = WorldConfig
        fields = [
            'starting_equipment',
            'starting_level',
            'leveling_curve',
            'max_level',
            'starting_room',
            'death_room',
            'death_mode',
            'death_route',
            'death_currency',
            'death_currency_penalty',
            'clan_registration_currency',
            'clan_registration_cost',
            'small_background',
            'large_background',
            'can_select_faction',
            'auto_equip',
            'allow_combat',
            'combat_resolution_interval',
            'default_roam_chance',
            'combat_system',
            'is_narrative',
            'players_can_set_title',
            'pvp_mode',
            'built_by',
            'non_ascii_names',
            'decay_glory',
            'name_exclusions',
            'globals_enabled',
            'equipment_system',
            'stat_system',
        ]

    def validate_combat_resolution_interval(self, value):
        if value < 0 and value != -1:
            raise serializers.ValidationError(
                "combat_resolution_interval must be -1 or >= 0."
            )
        return value

    def validate_default_roam_chance(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                "default_roam_chance must be between 0 and 100."
            )
        return value

    def validate_leveling_curve(self, value):
        try:
            return normalize_leveling_curve(value)
        except LevelingConfigError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_stat_system(self, value):
        try:
            return normalize_stat_system(value)
        except StatSystemValidationError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_equipment_system(self, value):
        try:
            return normalize_equipment_system(value)
        except EquipmentSystemValidationError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_combat_system(self, value):
        try:
            return normalize_combat_system(value)
        except CombatFormulaValidationError as exc:
            raise serializers.ValidationError(str(exc))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        config = self.instance
        world = self.context.get("world") if isinstance(self.context, dict) else None
        if world is None and config is not None:
            world = config.configured_worlds.filter(instance_of__isnull=False).first()
        if getattr(world, "instance_of_id", None):
            requested_fields = set(attrs.keys())
            inherited_fields = sorted(requested_fields & INSTANCE_INHERITED_CONFIG_FIELDS)
            if inherited_fields:
                raise serializers.ValidationError(
                    "Instance worlds inherit core systems from their base world. "
                    f"Cannot alter: {', '.join(inherited_fields)}."
                )
            disallowed_fields = sorted(requested_fields - INSTANCE_LOCAL_CONFIG_FIELDS)
            if disallowed_fields:
                raise serializers.ValidationError(
                    "Instance worlds can only alter local instance config. "
                    f"Cannot alter: {', '.join(disallowed_fields)}."
                )
        if world is not None:
            base_world = economy_world(world)
            currency_errors = {}
            for field_name in (
                "death_currency",
                "clan_registration_currency",
            ):
                currency = attrs.get(
                    field_name,
                    getattr(config, field_name, None),
                )
                if currency is not None and currency.world_id != base_world.pk:
                    currency_errors[field_name] = (
                        "Currency must belong to this world's base economy."
                    )
            if currency_errors:
                raise serializers.ValidationError(currency_errors)

        effective_death_mode = attrs.get(
            "death_mode",
            getattr(config, "death_mode", adv_consts.DEATH_MODE_LOSE_NONE),
        )
        effective_death_currency = attrs.get(
            "death_currency",
            getattr(config, "death_currency", None),
        )
        if (
            effective_death_mode == adv_consts.DEATH_MODE_LOSE_CURRENCY
            and effective_death_currency is None
        ):
            raise serializers.ValidationError({
                "death_currency": (
                    "A death currency is required when death mode is lose_currency."
                ),
            })

        effective_clan_cost = attrs.get(
            "clan_registration_cost",
            getattr(config, "clan_registration_cost", 0),
        )
        effective_clan_currency = attrs.get(
            "clan_registration_currency",
            getattr(config, "clan_registration_currency", None),
        )
        if effective_clan_cost and effective_clan_currency is None:
            raise serializers.ValidationError({
                "clan_registration_currency": (
                    "A clan registration currency is required when the cost is nonzero."
                ),
            })
        equipment_system = attrs.get(
            "equipment_system",
            getattr(config, "equipment_system", None),
        )
        try:
            equipment_system = normalize_equipment_system(equipment_system)
            if "equipment_system" in attrs:
                attrs["equipment_system"] = equipment_system
            if "stat_system" in attrs:
                armor_class_keys = (
                    get_armor_class_keys(equipment_system)
                    if has_authored_armor_classes(equipment_system)
                    else None
                )
                attrs["stat_system"] = normalize_stat_system(
                    attrs["stat_system"],
                    armor_class_keys=armor_class_keys,
                )
        except (EquipmentSystemValidationError, StatSystemValidationError) as exc:
            raise serializers.ValidationError(str(exc))

        try:
            validate_leveling_config(
                starting_level=attrs.get(
                    "starting_level",
                    getattr(config, "starting_level", 1),
                ),
                max_level=attrs.get(
                    "max_level",
                    getattr(config, "max_level", 20),
                ),
                leveling_curve=attrs.get(
                    "leveling_curve",
                    getattr(config, "leveling_curve", None),
                ),
            )
        except LevelingConfigError as exc:
            raise serializers.ValidationError(str(exc))
        return attrs

# World Admin

class WorldAdminSerializer(serializers.ModelSerializer):
    """
    Detailed view of a ROOT world, giving an admin visibility
    into all of its running components.

    Must be passed a rdb context argument:
    serializer = WorldAdminSerializer(world, context={'rdb': rdb})

    Returns:
        dict: A dictionary with:
            id (int): ID of the ROOT world being looked at.
            name (string): Name of the ROOT world.
            is_multiplayer (bool): Whether the ROOT world is multiplayer.
            maintenance_mode (string): Whether the ROOT world is in
                maintenance mode. If so, the reason is given.
            singleplayer_data (dict): Single Player Worlds data
                spw_count(int): Number of Singleplayer instances.
                live_spw_instances(List[SPWAdminSerializer]): List of SPWs
                    live in the game world.
                stale_spw_instances(int): Count of SPWs that are stale
            multiplayer_data (dict): Multi Player Worlds data
    """

    stats = serializers.SerializerMethodField()
    spawned_worlds = serializers.SerializerMethodField()
    instance_runs = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name', 'is_multiplayer', 'maintenance_mode',
            'stats', 'spawned_worlds', 'instance_runs',
        ]

    def get_stats(self, world):
        return {
            'num_item_definitions': world.item_definitions.count(),
            'num_mob_definitions': world.mob_definitions.count(),
            'num_rooms': world.rooms.count(),
        }

    def get_spawned_worlds(self, world):
        instances = qs_by_pks(
            World,
            world.spawned_worlds.values_list('id', flat=True))
        return [
            WorldAdminSpawnWorldSerializer(instance).data
            for instance in instances
        ]

    def get_instance_runs(self, world):
        runs = InstanceRun.objects.filter(
            base_world=world,
        ).exclude(
            status=InstanceRun.STATUS_CLEANED,
        ).annotate(
            admin_participant_count=Count('participants'),
            admin_active_participant_count=Count(
                'participants',
                filter=Q(participants__exited_at__isnull=True),
            ),
        ).select_related(
            'template_world',
            'spawned_world',
            'leader',
        ).order_by(
            '-last_active_at',
            '-started_at',
            '-id',
        )[:100]
        return WorldAdminInstanceRunSerializer(runs, many=True).data


class WorldAdminSpawnWorldSerializer(serializers.ModelSerializer):

    live_data = serializers.SerializerMethodField()
    forge_data = serializers.SerializerMethodField()
    recovery_actions = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name', 'lifecycle', 'lifecycle_change_ts', 'live_data',
            'forge_data', 'recovery_actions',
        ]

    def get_forge_data(self, world):
        return {
            'num_players': world.players.count(),
            'num_items': world.items.count(),
            'num_pending_items': world.items.filter(is_pending_deletion=True).count(),
            'num_mobs': world.mobs.count(),
        }

    def get_live_data(self, world):
        return {
            'state': 'absent',
            'connected_players': [],
            'num_items': 0,
            'num_mobs': 0,
            'ref': '',
        }

    def get_recovery_actions(self, world):
        return {
            'recover_to_stopped': is_recoverable_lifecycle(world.lifecycle),
        }


class WorldAdminInstanceRunSerializer(serializers.ModelSerializer):
    template_world = serializers.SerializerMethodField()
    spawned_world = serializers.SerializerMethodField()
    leader = serializers.SerializerMethodField()
    participant_count = serializers.SerializerMethodField()
    active_participant_count = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = InstanceRun
        fields = [
            'id',
            'ref',
            'status',
            'is_active',
            'started_at',
            'last_active_at',
            'completed_at',
            'failed_at',
            'expires_at',
            'closed_at',
            'cleanup_after',
            'template_world',
            'spawned_world',
            'leader',
            'participant_count',
            'active_participant_count',
        ]

    def get_template_world(self, run):
        return {
            'id': run.template_world_id,
            'name': run.template_world.name,
        }

    def get_spawned_world(self, run):
        return {
            'id': run.spawned_world_id,
            'name': run.spawned_world.name,
            'lifecycle': run.spawned_world.lifecycle,
            'is_multiplayer': run.spawned_world.is_multiplayer,
            'recovery_actions': {
                'recover_to_stopped': is_recoverable_lifecycle(
                    run.spawned_world.lifecycle),
            },
        }

    def get_leader(self, run):
        if not run.leader_id:
            return None
        return {
            'id': run.leader_id,
            'name': run.leader.name,
        }

    def get_participant_count(self, run):
        if hasattr(run, 'admin_participant_count'):
            return run.admin_participant_count
        return run.participants.count()

    def get_active_participant_count(self, run):
        if hasattr(run, 'admin_active_participant_count'):
            return run.admin_active_participant_count
        return run.participants.filter(exited_at__isnull=True).count()

    def get_is_active(self, run):
        return run.status in InstanceRun.ACTIVE_STATUSES


class WorldAdminInstancePlayerSerializer(serializers.ModelSerializer):
    room = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = [
            'id',
            'name',
            'is_builder',
            'last_connection_ts',
            'last_action_ts',
            'room',
        ]

    def get_room(self, player):
        if not player.room_id:
            return None
        return {
            'id': player.room_id,
            'key': player.room.key,
            'name': player.room.name,
        }


class WorldAdminInstanceSerializer(serializers.ModelSerializer):
    context_world = serializers.SerializerMethodField()
    parent_world = serializers.SerializerMethodField()
    leader = serializers.SerializerMethodField()
    instance_run = serializers.SerializerMethodField()
    world_state = serializers.SerializerMethodField()
    lifecycle_details = serializers.SerializerMethodField()
    spawn_plan_details = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()
    active_players = serializers.SerializerMethodField()
    recovery_actions = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id',
            'key',
            'name',
            'is_multiplayer',
            'instance_ref',
            'context_world',
            'parent_world',
            'leader',
            'instance_run',
            'world_state',
            'lifecycle_details',
            'spawn_plan_details',
            'counts',
            'active_players',
            'recovery_actions',
        ]

    def _content_type_id(self, model_cls):
        cache_attr = f'_{model_cls.__name__.lower()}_ct_id'
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, ContentType.objects.get_for_model(model_cls).id)
        return getattr(self, cache_attr)

    def get_context_world(self, spawn_world):
        if not spawn_world.context_id:
            return None
        return {
            'id': spawn_world.context_id,
            'name': spawn_world.context.name,
        }

    def get_parent_world(self, spawn_world):
        template_world = spawn_world.context
        if not template_world or not template_world.instance_of_id:
            return None
        return {
            'id': template_world.instance_of_id,
            'name': template_world.instance_of.name,
        }

    def get_leader(self, spawn_world):
        if not spawn_world.leader_id:
            return None
        return {
            'id': spawn_world.leader_id,
            'name': spawn_world.leader.name,
        }

    def get_instance_run(self, spawn_world):
        try:
            run = spawn_world.instance_run
        except InstanceRun.DoesNotExist:
            return None

        active_participants = run.participants.filter(exited_at__isnull=True)
        return {
            'id': run.id,
            'ref': run.ref,
            'status': run.status,
            'started_at': run.started_at,
            'last_active_at': run.last_active_at,
            'completed_at': run.completed_at,
            'failed_at': run.failed_at,
            'expires_at': run.expires_at,
            'closed_at': run.closed_at,
            'cleanup_after': run.cleanup_after,
            'participant_count': run.participants.count(),
            'active_participant_count': active_participants.count(),
            'initial_member_ids': run.initial_member_ids,
        }

    def get_world_state(self, spawn_world):
        return get_state_snapshot(STATE_SCOPE_WORLD, spawn_world)

    def get_lifecycle_details(self, spawn_world):
        cleanup_started_ts = None
        try:
            cleanup_started_ts = spawn_world.worldlocks.clean_start_ts
        except WorldLocks.DoesNotExist:
            pass

        return {
            'current': spawn_world.lifecycle,
            'changed_at': spawn_world.lifecycle_change_ts,
            'cleanup_started_ts': cleanup_started_ts,
            'last_spawn_plan_run_ts': spawn_world.last_spawn_plan_run_ts,
            'last_extraction_ts': spawn_world.last_extraction_ts,
            'last_entered_ts': spawn_world.last_entered_ts,
            'last_played_ts': spawn_world.last_played_ts,
        }

    def get_spawn_plan_details(self, spawn_world):
        context_world = spawn_world.context
        return {
            'last_run_ts': spawn_world.last_spawn_plan_run_ts,
            'configured_spawn_plan_count': context_world.spawn_plans.count()
            if context_world else 0,
            'configured_spawn_entry_count': sum(
                plan.entries.count()
                for plan in context_world.spawn_plans.all()
            ) if context_world else 0,
        }

    def get_counts(self, spawn_world):
        active_items_qs = spawn_world.items.filter(is_pending_deletion=False)
        pending_items_qs = spawn_world.items.filter(is_pending_deletion=True)
        active_mobs_qs = spawn_world.mobs.filter(is_pending_deletion=False)
        pending_mobs_qs = spawn_world.mobs.filter(is_pending_deletion=True)
        active_players_qs = spawn_world.players.filter(in_game=True)

        room_ct_id = self._content_type_id(Room)
        player_ct_id = self._content_type_id(Player)
        mob_ct_id = self._content_type_id(Mob)
        item_ct_id = self._content_type_id(Item)
        equipment_ct_id = self._content_type_id(Equipment)

        return {
            'mobs_loaded': active_mobs_qs.count(),
            'mobs_pending_deletion': pending_mobs_qs.count(),
            'items_total': active_items_qs.count(),
            'items_on_ground': active_items_qs.filter(
                container_type_id=room_ct_id
            ).count(),
            'items_pending_deletion': pending_items_qs.count(),
            'players_logged_in': active_players_qs.count(),
            'player_records': spawn_world.players.count(),
            'instance_assignments': InstanceAssignment.objects.filter(
                instance=spawn_world
            ).count(),
            'instance_participants': getattr(
                spawn_world,
                'instance_run',
                None
            ).participants.count() if hasattr(spawn_world, 'instance_run') else 0,
            'items_by_container_type': {
                'rooms': active_items_qs.filter(
                    container_type_id=room_ct_id
                ).count(),
                'players': active_items_qs.filter(
                    container_type_id=player_ct_id
                ).count(),
                'mobs': active_items_qs.filter(
                    container_type_id=mob_ct_id
                ).count(),
                'inside_items': active_items_qs.filter(
                    container_type_id=item_ct_id
                ).count(),
                'equipment': active_items_qs.filter(
                    container_type_id=equipment_ct_id
                ).count(),
                'without_container': active_items_qs.filter(
                    Q(container_type__isnull=True) | Q(container_id__isnull=True)
                ).count(),
            },
        }

    def get_active_players(self, spawn_world):
        players = spawn_world.players.filter(
            in_game=True
        ).select_related(
            'room',
        ).order_by(
            'name',
            'id',
        )
        return WorldAdminInstancePlayerSerializer(players, many=True).data

    def get_recovery_actions(self, spawn_world):
        return {
            'recover_to_stopped': is_recoverable_lifecycle(spawn_world.lifecycle),
        }


class WorldStatsSerializer(serializers.ModelSerializer):
    "Returns stats about a spawned world from the API side."

    # API values
    api_state = serializers.CharField(source='lifecycle')
    api_mob_count = serializers.SerializerMethodField()
    api_item_count = serializers.SerializerMethodField()
    api_num_players = serializers.SerializerMethodField()
    api_online_players = serializers.SerializerMethodField()
    # Only for SPWs
    # player_name = serializers.SerializerMethodField()

    # Game values
    game_state = serializers.SerializerMethodField()
    game_mob_count = serializers.SerializerMethodField()
    game_item_count = serializers.SerializerMethodField()
    game_players = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name',
            # API fields
            'api_state',
            'api_mob_count', 'api_item_count', 'api_num_players',
            'api_online_players',
            # Game fields
            'game_state',
            'game_mob_count', 'game_item_count', 'game_players',
        ]

    def get_api_mob_count(self, world):
        return world.mobs.count()

    def get_api_item_count(self, world):
        return world.items.count()

    def get_api_num_players(self, world):
        return world.players.count()

    def get_api_online_players(self, world):
        return PlayerListSerializer(
            world.players.filter(in_game=True),
            many=True).data

    def get_game_state(self, world):
        game_world = self.context['game_world']
        if game_world:
            return game_world.state
        return 'N/A'

    def get_game_mob_count(self, world):
        game_world = self.context['game_world']
        if game_world:
            return len(game_world.get_backref_keys('mobs'))
        return 0

    def get_game_item_count(self, world):
        game_world = self.context['game_world']
        if game_world:
            return len(game_world.get_backref_keys('items'))
        return 0

    def get_game_players(self, world):
        return []


# Zones

class ZoneBuilderSerializer(serializers.ModelSerializer):

    name = serializers.CharField(required=False)
    num_rooms = serializers.SerializerMethodField()
    center = serializers.SerializerMethodField()
    zone_data = serializers.SerializerMethodField()
    has_assignment = serializers.SerializerMethodField()
    manifest_ref = serializers.SerializerMethodField()
    yaml = serializers.SerializerMethodField()
    delete_yaml = serializers.SerializerMethodField()

    class Meta:
        model = Zone
        fields = (
            'id',
            'key',
            'relative_id',
            'manifest_ref',
            'name',
            'modified_ts',
            'num_rooms',
            'center',
            'zone_data',
            'respawn_wait',
            'pvp_zone',
            'has_assignment',
            'yaml',
            'delete_yaml',
        )
        read_only_fields = ('relative_id', 'manifest_ref', 'yaml', 'delete_yaml')

    def get_num_rooms(self, zone):
        return zone.rooms.count()

    def get_center(self, zone):
        if zone.center:
            return MapRoomSerializer(zone.center).data
        rooms = zone.rooms
        if rooms.count():
            return MapRoomSerializer(zone.rooms.order_by('created_ts')[0]).data
        return None

    def get_zone_data(self, zone):
        return get_state_snapshot(STATE_SCOPE_ZONE, zone)

    def get_manifest_ref(self, zone):
        return f"zone@{zone.relative_id or zone.id}"

    def _include_manifest_yaml(self):
        return self.context.get('include_manifest_yaml', False)

    def get_yaml(self, zone):
        if not self._include_manifest_yaml():
            return ""
        from builders import world_export as builder_world_export
        return builder_world_export.serialize_zone_manifest_payload(zone)["yaml"]

    def get_delete_yaml(self, zone):
        if not self._include_manifest_yaml():
            return ""
        from builders import world_export as builder_world_export
        return builder_world_export.serialize_zone_manifest_payload(zone)["delete_yaml"]

    def get_has_assignment(self, zone):
        try:
            if self.context['request'].user == zone.world.author:
                return True
        except KeyError:
            return False

        builder = WorldBuilder.objects.filter(
            world=zone.world,
            user=self.context['request'].user).first()

        if not builder:
            return False

        if builder.builder_rank >= 3:
            return True

        if BuilderAssignment.objects.filter(
                builder=builder,
                assignment_id=zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone)
            ).exists():
            return True

        return False


class MoveZoneSerializer(serializers.Serializer):

    direction = serializers.ChoiceField(choices=adv_consts.DIRECTIONS)
    distance = serializers.IntegerField()

    @transaction.atomic
    def create(self, validated_data):
        zone = self.context['zone']

        rooms_qs = zone.rooms.all()
        direction = validated_data['direction']
        distance = validated_data['distance']

        if direction == 'north':
            axis = 'y'
            rooms_qs = rooms_qs.order_by('-y')
        elif direction == 'south':
            axis = 'y'
            rooms_qs = rooms_qs.order_by('y')
            distance = 0 - int(distance)
        elif direction == 'east':
            axis = 'x'
            rooms_qs = rooms_qs.order_by('-x')
        elif direction == 'west':
            axis = 'x'
            rooms_qs = rooms_qs.order_by('x')
            distance = 0 - int(distance)
        elif direction == 'up':
            axis = 'z'
            rooms_qs = rooms_qs.order_by('-z')
        elif direction == 'down':
            axis = 'z'
            rooms_qs = rooms_qs.order_by('z')
            distance = 0 - int(distance)

        try:
            updated_rooms = []
            for room in rooms_qs:
                setattr(room, axis, F(axis) + distance)
                room.save()
                room.update_live_instances()
                updated_rooms.append(room)
        except IntegrityError:
            raise serializers.ValidationError("Coordinate conflict")

        return {
            'rooms': Room.objects.filter(
                id__in=rooms_qs.values_list('id', flat=True))
        }


# Rooms

class RoomFlagField(serializers.Field):

    def __init__(self, code, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.code = code

    def get_attribute(self, room):
        return room

    def to_representation(self, room):
        return room.flags.filter(code=self.code).exists()

    def to_internal_value(self, data):
        return data


class RoomBuilderSerializer(serializers.ModelSerializer):

    zone = ReferenceField(required=False)
    x = serializers.IntegerField(required=False)
    y = serializers.IntegerField(required=False)
    z = serializers.IntegerField(required=False)
    name = serializers.CharField(required=False)
    type = serializers.CharField(required=False)
    north = ReferenceField(required=False, allow_null=True)
    east = ReferenceField(required=False, allow_null=True)
    south = ReferenceField(required=False, allow_null=True)
    west = ReferenceField(required=False, allow_null=True)
    up = ReferenceField(required=False, allow_null=True)
    down = ReferenceField(required=False, allow_null=True)

    num_spawn_plan_entries = serializers.SerializerMethodField()
    num_actions = serializers.SerializerMethodField()
    num_triggers = serializers.SerializerMethodField()
    details = serializers.SerializerMethodField()
    doors = serializers.SerializerMethodField()
    has_assignment = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = [
            'id', 'key', 'model_type', 'name',
            'type', 'description', 'note', 'color',
            'x', 'y', 'z',
            'zone',
            'num_actions', 'num_triggers', 'num_spawn_plan_entries', 'details', 'doors',
            'has_assignment',
        ] + list(adv_consts.DIRECTIONS)

    def validate_color(self, color):
        if color and re.search('[^a-zA-Z0-9#\s]', color):
            raise serializers.ValidationError("Invalid color value.")
        return color

    def validate(self, attrs):
        room = self.instance
        if room:
            for direction in adv_consts.DIRECTIONS:
                exit_room = attrs.get(direction)
                if exit_room and exit_room.world_id != room.world_id:
                    raise serializers.ValidationError(
                        "Cannot link to a room in another world.")
        return super().validate(attrs)

    def get_num_actions(self, room):
        return room.room_actions.count()

    def get_num_triggers(self, room):
        return Trigger.objects.filter(
            world_id=room.world_id,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=room.id,
        ).count()

    def get_num_spawn_plan_entries(self, room):
        room_entries = 0
        for plan in room.world.spawn_plans.all():
            room_entries += sum(
                1 for entry in plan.entries.all()
                if isinstance(entry.target, dict)
                and entry.target.get('room') in {room.key, f'room.{room.id}'}
            )
        path_ids = PathRoom.objects.filter(
            room=room
        ).values_list('path_id', flat=True)
        path_refs = {f'path@{path_id}' for path_id in path_ids}
        path_entries = 0
        for plan in room.world.spawn_plans.all():
            path_entries += sum(
                1 for entry in plan.entries.all()
                if isinstance(entry.target, dict)
                and entry.target.get('path') in path_refs
            )
        return room_entries + path_entries

    def get_fields(self):
        fields = super().get_fields()

        for flag in adv_consts.ROOM_FLAGS:
            fields['is_' + flag] = RoomFlagField(code=flag, required=False)

        return fields

    def get_map(self, obj):
        room = obj

        serialized_room = MapRoomSerializer(room).data
        serialized_rooms = MapRoomSerializer(
            MapRoomSerializer.prefetch_map(Room.objects.get_map(room)),
            many=True).data

        return {
            'rooms': serialized_rooms,
            'center': serialized_room,
            'selected': serialized_room,
        }

    def get_details(self, room):
        return [
            detail.keywords for detail in room.details.all()
        ]

    def get_doors(self, room):
        doors = {}
        doors_data = RoomDoorSerializer(room.doors_from.all(), many=True).data
        for door_data in doors_data:
            doors[door_data['direction']] = door_data
        return doors

    def get_has_assignment(self, room):
        try:
            if self.context['request'].user == room.world.author:
                return True
        except KeyError:
            return False

        builder = WorldBuilder.objects.filter(
            world=room.world,
            user=self.context['request'].user).first()

        if not builder:
            return False

        if builder.builder_rank >= 3:
            return True

        if BuilderAssignment.objects.filter(
                builder=builder,
                assignment_id=room.id,
                assignment_type=ContentType.objects.get_for_model(Room)
            ).exists():
            return True
        if BuilderAssignment.objects.filter(
                builder=builder,
                assignment_id=room.zone_id,
                assignment_type=ContentType.objects.get_for_model(Zone)
            ).exists():
            return True
        return False

    def update(self, instance, validated_data):
        # Handle room flags
        for flag in adv_consts.ROOM_FLAGS:
            value = validated_data.get('is_' + flag)

            if value is not None:
                if value in (True, 'true', 'True'):
                    RoomFlag.objects.get_or_create(
                        room=instance,
                        code=flag)
                elif value in (False, 'false', 'False'):
                    RoomFlag.objects.filter(
                        room=instance, code=flag
                    ).delete()

        try:
            return super().update(instance, validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                "A room already exists at those coordinates.")


class LegacyRoomBuilderSerializer(RoomBuilderSerializer):
    map = serializers.SerializerMethodField()

    class Meta(RoomBuilderSerializer.Meta):
        fields = RoomBuilderSerializer.Meta.fields + ['map']


class MapRoomSerializer(serializers.ModelSerializer):

    zone = ReferenceField()
    north = ReferenceField(required=False, allow_null=True)
    east = ReferenceField(required=False, allow_null=True)
    south = ReferenceField(required=False, allow_null=True)
    west = ReferenceField(required=False, allow_null=True)
    up = ReferenceField(required=False, allow_null=True)
    down = ReferenceField(required=False, allow_null=True)
    flags = serializers.SlugRelatedField(many=True, slug_field='code', read_only=True)

    class Meta:
        model = Room
        fields = [
            'id', 'key', 'name', 'model_type', 'modified_ts',
            'type', 'zone', 'note', 'flags',
            'description',
            'x', 'y', 'z',
        ] + list(adv_consts.DIRECTIONS)

    @staticmethod
    def prefetch_map(qs):
        return qs.prefetch_related(
            'north',
            'east',
            'west',
            'south',
            'up',
            'down',
            'zone',
            'world',
            'flags',
        )



EDITABLE_ATTRIBUTES = [
    'type', 'name', 'description', 'note', 'x', 'y', 'z', 'zone'
]
class RoomEditSerializer(serializers.Serializer):
    """
    Serializer used to edit a room in game.

    2020/05/26 - looks unused to me.
    """

    attribute = serializers.ChoiceField(choices=EDITABLE_ATTRIBUTES)
    value = serializers.CharField()

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room

    def validate(self, data):
        vd = super().validate(data)
        if (data['attribute'] == 'type'
            and data['value'] not in adv_consts.ROOM_TYPES):
            raise serializers.ValidationError(
                "Invalid room type '%s'" % data['value'])

        if data['attribute'] == 'zone':
            try:
                if data['value'].startswith('zone.'):
                    zone = Zone.objects.get(
                        world=self.room.world,
                        #relative_id=data['value'].split('.')[1])
                        id=data['value'].split('.')[1])
                else:
                    zone = Zone.objects.get(
                        pk=data['value'],
                        world=self.room.world)
                data['value'] = zone
            except Zone.DoesNotExist:
                raise serializers.ValidationError("Invalid zone id/key.")

        return vd

    def create(self, validated_data):
        setattr(self.room,
                validated_data['attribute'],
                validated_data['value'])
        self.room.save()
        return self.room

class RoomDirActionSerializer(serializers.Serializer):

    direction = serializers.ChoiceField(choices=adv_consts.DIRECTIONS,
                                        allow_blank=True)
    action = serializers.ChoiceField(choices=adv_consts.EXIT_ACTIONS)

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room

    def create(self, validated_data):
        action = validated_data['action']
        direction = validated_data['direction']
        room = self.room

        if action == adv_consts.EXIT_ACTION_MUTUAL:
            exit_room = self.set_mutual_exit(room, direction)
        elif action == adv_consts.EXIT_ACTION_ONE_WAY:
            exit_room = self.set_one_way(room, direction)
        elif action == adv_consts.EXIT_ACTION_NO_EXIT:
            exit_room = self.set_no_exit(room, direction)
        elif action == adv_consts.EXIT_ACTION_CREATE:
            exit_room = self.create_at(room, direction)
        elif action == adv_consts.EXIT_ACTION_MOVE:
            exit_room = self.move_to(room, direction)
        else:
            raise ValueError('invalid action: %s' % action)

        return exit_room

    def set_mutual_exit(self, room, direction):
        """
        Attempt to create a mutual exit with the first found of:
        * A room currently at the specified exit
        * A neightbor to the current room
        * A room connecting one-way inbound to this room
        """
        exit_room = (getattr(room, direction)
                     or room.get_neighbor(direction)
                     or room.get_inbound_exit_room(direction))
        if not exit_room:
            raise ValueError("No room to connect to.")
        setattr(room, direction, exit_room)
        room.save()
        setattr(exit_room, adv_consts.REVERSE_DIRECTIONS[direction], room)
        exit_room.save()

        # If there were doors (which presumably would only happen if
        # previously we were in a one-way scenario), remove them.
        room.doors_from.all().delete()
        room.doors_to.all().delete()

        return exit_room

    def set_one_way(self, room, direction):
        "Same determination logic as mutual exit"
        exit_room = (getattr(room, direction)
                     or room.get_neighbor(direction)
                     or room.get_inbound_exit_room(direction))
        if not exit_room:
            raise ValueError("No room to connect to.")
        setattr(room, direction, exit_room)
        room.save()
        setattr(exit_room, adv_consts.REVERSE_DIRECTIONS[direction], None)
        exit_room.save()

        # If there was a door going from the exit room to the room, remove it
        room.doors_to.all().delete()

        return exit_room

    def set_no_exit(self, room, direction):
        exit_room = (getattr(room, direction)
                     or room.get_inbound_exit_room(direction))
        if not exit_room:
            raise serializers.ValidationError(
                'No room to disconnect from.')

        # If there are doors, remove them
        Door.objects.filter(
            from_room=room,
            to_room=exit_room).delete()
        Door.objects.filter(
            from_room=exit_room,
            to_room=room).delete()

        setattr(room, direction, None)
        room.save()
        setattr(exit_room, adv_consts.REVERSE_DIRECTIONS[direction], None)
        exit_room.save()

        # Clear doors
        room.doors_from.all().delete()
        room.doors_to.all().delete()

        return exit_room

    def create_at(self, room, direction):
        return room.create_at(direction)

    def move_to(self, room, direction):
        diff = adv_consts.DIR_COORD_DIFF[direction]
        x = room.x + diff[0]
        y = room.y + diff[1]
        z = room.z + diff[2]

        # Make sure there isn't already a room there
        try:
            room = Room.objects.get(world=room.world, x=x, y=y, z=z)
            raise ValueError("A room already exists %s." % direction)
        except Room.DoesNotExist:
            pass

        room.x = x
        room.y = y
        room.z = z
        room.save()
        return room


class RoomDetailSerializer(serializers.ModelSerializer):

    class Meta:
        model = RoomDetail
        fields = [
            'id',
            'keywords',
            'description',
            'is_hidden',
        ]

    def validate_keywords(self, keywords):
        return keywords.split(' ')[0].lower()


class ActionSerializer(serializers.ModelSerializer):

    validate_conditions = validate_conditions

    def validate_commands(self, commands):

        for command in commands.split('\n'):

            subcommands = [ c for c in re.split('[&&|;]', command) if c ]
            for subcommand in subcommands:

                tokens = [ c.lower() for c in re.split('[^\w/]', subcommand) if c ]
                main_cmd = tokens[0]
                args = tokens[1:]

                # Support for [20, if-cmd, else-cmd] syntax
                try:
                    float(main_cmd)
                    continue
                except (TypeError, ValueError):
                    pass

                """
                cmd_spec = is_room_cmd(main_cmd)
                if not cmd_spec:
                    raise serializers.ValidationError(
                        "Invalid room command '%s'" % main_cmd)

                # Take into account that some of the arguments may be optional
                cmd_spec_args = cmd_spec['args']
                min_num_args = len([
                    arg for arg in cmd_spec['args']
                    if arg[-1] != '?'
                ])

                if len(args) < min_num_args:
                    raise serializers.ValidationError(
                        "Insufficient number of arguments to '%s'" % main_cmd)
                """

        return commands


class RoomActionSerializer(ActionSerializer):
    class Meta:
        model = RoomAction
        fields = [
            'id',
            'name',
            'actions',
            'commands',
            'conditions',
            'show_details_on_failure',
            'failure_message',
            'display_action_in_room',
            'gate_delay',
        ]


# Doors

class RoomDoorSerializer(serializers.ModelSerializer):

    from_room = ReferenceField()
    to_room = ReferenceField()
    key = ReferenceField(required=False, allow_null=True)

    class Meta:
        model = Door
        fields = [
            'id',
            'direction',
            'name',
            'key',
            'from_room',
            'to_room',
            'default_state',
            'destroy_key',
        ]


class RoomSetDoorSerializer(serializers.Serializer):

    name = serializers.CharField(required=False, allow_null=True)
    key = ReferenceField(required=False, allow_null=True)
    direction = serializers.ChoiceField(choices=adv_consts.DIRECTIONS)
    default_state = serializers.ChoiceField(
        choices=adv_consts.DOOR_STATES, default=adv_consts.DOOR_STATE_CLOSED)
    destroy_key = serializers.BooleanField(default=False)

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room

    def validate_name(self, name):
        if name:
            name = name.split()[0]
            return name.lower()
        return name

    def create(self, validated_data):
        # See if there is already a door defined in that direction
        direction = validated_data['direction']
        to_room = getattr(self.room, direction, None)

        if not to_room:
            raise serializers.ValidationError(
                "Room has no exit in the specified direction.")

        # Set door to exit
        try:
            door = Door.objects.get(
                from_room=self.room,
                direction=direction)
            if door.to_room != to_room:
                door.to_room = to_room
            if validated_data.get('name'):
                door.name = validated_data['name']
        except Door.DoesNotExist:
            door = Door.objects.create(
                from_room=self.room,
                direction=direction,
                to_room=to_room,
                name=validated_data['name'])

        if validated_data.get('key'):
            door.key = validated_data['key']
        else:
            door.key = None
        if validated_data.get('destroy_key'):
            door.destroy_key = validated_data['destroy_key']
        else:
            door.destroy_key = False
        if validated_data.get('default_state'):
                door.default_state = validated_data['default_state']
        door.save()

        spawned_spws = self.room.world.spawned_worlds.filter(
            is_multiplayer=False)
        # For SPWs, set the door state
        for spawn_world in spawned_spws:
            try:
                door_state = DoorState.objects.get(
                    door=door,
                    world=spawn_world)
            except DoorState.DoesNotExist:
                door_state = DoorState.objects.create(
                    door=door,
                    world=spawn_world,
                    state=door.default_state)

        # Is there a reverse connection?
        reverse_door = None
        if getattr(to_room, adv_consts.REVERSE_DIRECTIONS[direction], None):
            try:
                reverse_door = Door.objects.get(
                    from_room=to_room,
                    to_room=self.room)
            except Door.DoesNotExist:
                reverse_door = Door.objects.create(
                    from_room=to_room,
                    to_room=self.room,
                    direction=adv_consts.REVERSE_DIRECTIONS[direction],
                    name=validated_data.get('name'),
                    default_state=validated_data['default_state'])
                if validated_data.get('key'):
                    reverse_door.key = validated_data['key']
                if validated_data.get('destroy_key'):
                    reverse_door.destroy_key = validated_data['destroy_key']
                reverse_door.save()

            for spawn_world in spawned_spws:
                try:
                    door_state = DoorState.objects.get(
                        door=reverse_door,
                        world=spawn_world)
                except DoorState.DoesNotExist:
                    door_state = DoorState.objects.create(
                        door=reverse_door,
                        world=spawn_world,
                        state=reverse_door.default_state)

        return {
            'door': door,
            'reverse_door': reverse_door
        }


class RoomClearDoorSerializer(serializers.Serializer):

    direction = serializers.ChoiceField(choices=adv_consts.DIRECTIONS)

    def __init__(self, room, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room

    def validate_direction(self, direction):
        try:
            door = Door.objects.get(
                from_room=self.room,
                direction=direction)
        except Door.DoesNotExist:
            raise serializers.ValidationError("No door %s." % direction)

        return door


class ItemDefinitionSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='item_type', read_only=True)
    attributes = serializers.JSONField(read_only=True)
    randomized = serializers.SerializerMethodField()

    class Meta:
        model = ItemDefinition
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'description', 'ground_description', 'notes', 'keywords',
            'type', 'base_properties', 'attributes', 'randomization',
            'randomized', 'cost', 'currency',
        ]

    def get_randomized(self, item_definition):
        randomization = item_definition.randomization or {}
        return bool(randomization.get('attributes'))


class ItemBundleSerializer(serializers.ModelSerializer):
    entry_count = serializers.SerializerMethodField()

    class Meta:
        model = ItemBundle
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'notes', 'entry_count',
        ]

    def get_entry_count(self, item_bundle):
        return item_bundle.entries.count()


class MobDefinitionSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='mob_type', read_only=True)
    attributes = serializers.JSONField(read_only=True)
    randomized = serializers.SerializerMethodField()

    class Meta:
        model = MobDefinition
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'description', 'room_description', 'notes', 'keywords',
            'type', 'assists', 'base_properties', 'attributes',
            'randomization', 'randomized', 'traits', 'loot', 'combat_abilities',
            'trainer',
        ]

    def get_randomized(self, mob_definition):
        randomization = mob_definition.randomization or {}
        return bool(randomization.get('attributes'))


class MerchantProfileSerializer(serializers.ModelSerializer):
    stock_count = serializers.SerializerMethodField()
    settlement_currency = serializers.SerializerMethodField()

    class Meta:
        model = MerchantProfile
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'notes', 'sell_markup', 'buy_multiplier',
            'restock_interval_seconds', 'funds_mode', 'settlement_currency',
            'purchase_budget', 'buyback_enabled', 'buyback_max_items',
            'stock_count',
        ]

    def get_stock_count(self, merchant_profile):
        return merchant_profile.stock_slots.count()

    def get_settlement_currency(self, merchant_profile):
        if merchant_profile.settlement_currency_id:
            return merchant_profile.settlement_currency.code
        return ''


class CraftMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = CraftMaterial
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'description', 'order',
        ]


class CraftingRecipeSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    output_item_definition = serializers.SerializerMethodField()
    ingredient_count = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    money = serializers.SerializerMethodField()

    class Meta:
        model = CraftingRecipe
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'group', 'order', 'cost', 'currency', 'money',
            'conditions', 'failure_message',
            'output_item_definition', 'ingredient_count',
        ]

    def get_currency(self, recipe):
        return recipe.currency.code if recipe.currency_id else None

    def get_money(self, recipe):
        if recipe.cost is None or recipe.currency_id is None:
            return None
        return money_payload(int(recipe.cost), recipe.currency)

    def get_output_item_definition(self, recipe):
        definition = recipe.output_item_definition
        return {
            'id': definition.id,
            'key': definition.key,
            'slug': definition.slug,
            'name': definition.name,
        }

    def get_ingredient_count(self, recipe):
        return recipe.ingredients.count()


class CraftingProfileSerializer(serializers.ModelSerializer):
    recipe_count = serializers.SerializerMethodField()

    class Meta:
        model = CraftingProfile
        fields = [
            'id', 'key', 'slug', 'name', 'model_type', 'modified_ts',
            'keywords', 'recipe_count',
        ]

    def get_recipe_count(self, profile):
        return profile.recipe_entries.count()


def validate_reaction(self, validated_data):
    event = validated_data.get('event')
    if event is None and getattr(self, 'instance', None) is not None:
        event = self.instance.event
    if event is None:
        raise serializers.ValidationError("Event is required.")

    match = validated_data.get('match')
    if match is None and getattr(self, 'instance', None) is not None:
        match = self.instance.match
    match = match or ''

    events_requiring_match = (
        adv_consts.MOB_REACTION_EVENT_SAYING,
        adv_consts.MOB_REACTION_EVENT_RECEIVE,
        adv_consts.MOB_REACTION_EVENT_PERIODIC,
    )
    if event in events_requiring_match and not match:

        msg = "Match is required: "

        if event == adv_consts.MOB_REACTION_EVENT_SAYING:
            msg += "enter the words to react to"

        elif event == adv_consts.MOB_REACTION_EVENT_RECEIVE:
            msg += "enter the value to match for receive"

        elif event == adv_consts.MOB_REACTION_EVENT_PERIODIC:
            msg += "enter the value to match for periodic"

        raise serializers.ValidationError(msg)

    if match:
        try:
            trigger_matcher.validate_match_expression(match)
        except trigger_matcher.MatchExpressionError as err:
            raise serializers.ValidationError(
                f"Invalid match matcher expression: {err}"
            )
    return validated_data

class MobReactionSerializer(serializers.ModelSerializer):

    definition = serializers.SerializerMethodField()
    match = serializers.CharField(required=False, allow_blank=True)
    reaction = serializers.CharField(source='script')

    class Meta:
        model = Trigger
        fields = [
            'key', 'id',
            'definition', 'event', 'match', 'reaction', 'conditions'
        ]

    def get_definition(self, trigger):
        if not trigger.target_type_id:
            return None
        if trigger.target_type.model_class() != MobDefinition:
            return None
        if not trigger.target:
            return None
        return KeyNameSerializer(trigger.target).data

    validate = validate_reaction


class AddMobReactionSerializer(serializers.Serializer):

    match = serializers.CharField(required=False, allow_blank=True)
    event = serializers.ChoiceField(choices=adv_consts.MOB_REACTION_EVENTS)
    conditions = serializers.CharField(required=False, allow_blank=True)
    reaction = serializers.CharField()

    def __init__(self, definition, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.definition = definition

    def create(self, validated_data):
        return Trigger.objects.create(
            world=self.definition.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=self.definition.id,
            event=validated_data['event'],
            match=validated_data.get('match', ''),
            script=validated_data['reaction'],
            conditions=validated_data.get('conditions', ''),
            display_action_in_room=False,
        )

    validate = validate_reaction


class SuggestMobSerializer(serializers.Serializer):
    level = serializers.IntegerField(default=1)
    archetype = serializers.ChoiceField(choices=adv_consts.ARCHETYPES,
                                        default=adv_consts.ARCHETYPE_WARRIOR)


class MobDefinitionSuggestionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=120)
    type = serializers.ChoiceField(
        choices=adv_consts.MOB_TYPES,
        default=adv_consts.MOB_TYPE_HUMANOID,
    )
    level = serializers.IntegerField(default=1, min_value=1)
    crit_percent = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=100,
    )
    resilience_percent = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=100,
    )
    armor_percent = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=100,
    )
    dodge_percent = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=100,
    )


# Factions

class FactionRankSerializer(serializers.ModelSerializer):
    class Meta:
        model = FactionRank
        fields = [
            'id',
            'standing',
            'name',
        ]


class FactionSerializer(serializers.ModelSerializer):

    starting_room = ReferenceField(required=False, allow_null=True)
    death_room = ReferenceField(required=False, allow_null=True)
    death_rooms = serializers.SerializerMethodField()
    ranks = FactionRankSerializer(many=True, read_only=True)

    class Meta:
        model = Faction
        fields = [
            'id',
            'key',
            'code',
            'name',
            'description',
            'modified_ts',
            'type',
            'playable',
            'default_languages',
            'is_core',
            'starting_room',
            'death_room',
            'is_default',
            'is_selectable',
            'death_rooms',
            'ranks',
        ]

    def check_default(self, instance, validated_data):
        if validated_data.get('is_default'):
            Faction.objects.filter(
                world=instance.world,
                is_default=True
            ).exclude(
                pk=instance.id
            ).update(is_default=False)

    def _normalize_type_fields(self, validated_data):
        if 'is_core' in validated_data:
            validated_data['type'] = (
                FACTION_TYPE_CORE
                if validated_data.pop('is_core')
                else FACTION_TYPE_REPUTATION
            )
        if 'is_selectable' in validated_data:
            validated_data['playable'] = validated_data['is_selectable']
        if validated_data.get('type') == FACTION_TYPE_REPUTATION:
            validated_data['playable'] = False
            validated_data['is_selectable'] = False
            validated_data['is_default'] = False
        elif validated_data.get('type') == FACTION_TYPE_CORE:
            validated_data['is_core'] = True
            if 'playable' in validated_data:
                validated_data['is_selectable'] = validated_data['playable']
        return validated_data

    def create(self, validated_data):
        validated_data = self._normalize_type_fields(validated_data)
        instance = super().create(validated_data)
        self.check_default(instance, validated_data)
        return instance

    def update(self, instance, validated_data):
        validated_data = self._normalize_type_fields(validated_data)
        instance = super().update(instance, validated_data)
        self.check_default(instance, validated_data)
        return instance

    def validate_code(self, code):
        lowercase_code = code.lower()
        joined_code = '_'.join(lowercase_code.split(' '))
        return joined_code

    def validate(self, data):
        faction = self.instance

        # Can't be default and unselected
        if 'is_selectable' in data and 'is_default' in data:
            if data['is_default'] and not data['is_selectable']:
                raise serializers.ValidationError(
                    'Cannot set default faction to be unselectable')
        if self.instance:
            if (self.instance.is_default
                and data.get('is_selectable') == False):
                raise serializers.ValidationError(
                    'Cannot set default faction to be unselectable')

        # Can't be core and default
        if 'is_core' in data and 'is_default' in data:
            if not data['is_core'] and data['is_default']:
                raise serializers.ValidationError(
                    'Cannot set non-core faction to be default.')
        if self.instance:
            if (not self.instance.is_core and data['is_default']):
                raise serializers.ValidationError(
                    'Cannot set non-core faction to be default.')

        if (self.instance
            and data.get('code') and data['code'] != self.instance.code):
            world = self.instance.world
            running_worlds = world.spawned_worlds.filter(
                lifecycle=api_consts.WORLD_STATE_RUNNING)
            if running_worlds.exists():
                raise serializers.ValidationError(
                    'Cannot change faction code with running worlds.')

        # Enforce code uniqueness
        if data.get('code'):
            if self.instance:
                if data['code'] != self.instance.code:
                    if Faction.objects.filter(
                        world=world,
                        code=data['code']).exists():
                        raise serializers.ValidationError(
                            'A faction with this code already exists.')
            else:
                world = self.context['view'].world
                if Faction.objects.filter(
                    world=world,
                    code=data['code']).exists():
                    raise serializers.ValidationError(
                        'A faction with this code already exists.')

        # Can't switch from reputation to core if a char has that faction assigned
        # as well as a core faction already.
        if (faction
            and not faction.is_core
            and 'is_core' in data
            and data['is_core']):
            # If we're switching the faction from reputation to core

            error = ('Cannot change to core faction when characters with '
                     'this faction already have a core faction.')

            # And there players with this faction & another core faction
            player_ids_with_faction = faction.assignments_for.filter(
                member_type__model='player'
            ).values_list('member_id', flat=True)
            are_player_core_assignments = FactionAssignment.objects.filter(
                member_type__model='player',
                member_id__in=player_ids_with_faction,
                faction__world_id=self.instance.world_id,
                faction__is_core=True).exists()
            if are_player_core_assignments:
                raise serializers.ValidationError(error)

            # Or mobs with this faction & another core faction
            mob_ids_with_faction = faction.assignments_for.filter(
                member_type__model='mob',
            ).values_list('member_id', flat=True)
            are_mob_core_assignments = FactionAssignment.objects.filter(
                member_type__model='mob',
                member_id__in=mob_ids_with_faction,
                faction__world_id=self.instance.world_id,
                faction__is_core=True).exists()
            if are_mob_core_assignments:
                raise serializers.ValidationError(error)

        return super().validate(data)

    def get_death_rooms(self, faction):
        death_rooms = []
        for procession in faction.faction_processions.all():
            death_rooms.append(
                ReferenceField().to_representation(procession.room))
        return death_rooms


# Paths

class PathListSerializer(serializers.ModelSerializer):
    manifest_ref = serializers.SerializerMethodField()

    class Meta:
        model = Path
        fields = [
            'id',
            'relative_id',
            'manifest_ref',
            'name',
            'key',
            'modified_ts',
        ]
        read_only_fields = ('relative_id', 'manifest_ref')

    def get_manifest_ref(self, path):
        return f"path@{path.relative_id or path.id}"


class PathDetailsSerializer(serializers.ModelSerializer):
    rooms = serializers.SerializerMethodField()
    manifest_ref = serializers.SerializerMethodField()
    class Meta:
        model = Path
        fields = [
            'id',
            'relative_id',
            'manifest_ref',
            'name',
            'key',
            'zone',
            'rooms',
        ]
        read_only_fields = ('relative_id', 'manifest_ref', 'zone')

    def get_rooms(self, path):
        qs = MapRoomSerializer.prefetch_map(path.rooms.all())
        return MapRoomSerializer(qs, many=True).data

    def get_manifest_ref(self, path):
        return f"path@{path.relative_id or path.id}"

    def create(self, validated_data):
        zone = self.context['zone']
        return Path.objects.create(
            world=zone.world,
            zone=zone,
            name=validated_data['name'])


class PathRoomSerializer(serializers.ModelSerializer):

    room = ReferenceField(required=True, allow_null=False)

    class Meta:
        model = PathRoom
        fields = ['id', 'room']


class AddPathRoomSerializer(serializers.Serializer):

    room = ReferenceField()

    def create(self, validated_data):
        qs = PathRoom.objects.filter(path=self.context['path'])
        room = validated_data['room']
        path = self.context['path']
        if qs.filter(room=room):
            raise serializers.ValidationError(
                "Room already belongs to the path.")
        return PathRoom.objects.create(room=room, path=path)


# Processions

class ProcessionSerializer(serializers.ModelSerializer):

    room = ReferenceField(required=True, allow_null=False)
    faction = ReferenceField(required=True, allow_null=False)

    class Meta:
        model = Procession
        fields = ['id', 'room', 'faction']


class WorldBuilderSerializer(serializers.ModelSerializer):

    user = ReferenceField(required=False, allow_null=False)

    class Meta:
        model = WorldBuilder
        fields = [
            'id', 'user', 'read_only', 'builder_rank'
        ]


class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'name', 'key']


# Player Admin

class PlayerListSerializer(serializers.ModelSerializer):

    class Meta:
        model = Player
        fields = [
            'id',
            'key',
            'name',
            'modified_ts',
            'title',
            'level',
            'gender',
            'archetype',
        ]


class PlayerDetailSerializer(serializers.ModelSerializer):

    name = serializers.CharField(required=False, allow_null=False)
    viewed_rooms = serializers.SerializerMethodField()
    inventory = serializers.SerializerMethodField()
    equipment = serializers.SerializerMethodField()
    room = serializers.SerializerMethodField()
    animation_data = serializers.SerializerMethodField()
    marks = serializers.SerializerMethodField()
    world = WorldSerializer(required=False, allow_null=False)
    instance_details = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = PlayerListSerializer.Meta.fields + [
            'experience',
            'viewed_rooms',
            'inventory',
            'room',
            'equipment',
            'factions',
            'animation_data',
            'is_builder',
            'world',
            'instance_details',
            'power',
            'marks',
        ]

    def get_viewed_rooms(self, player):
        return [
            room.get_game_key(player.world)
            for room in player.viewed_rooms.filter(
                world=player.world.context)
        ]

    def get_inventory(self, player):
        return [
            spawn_serializers.AnimateItemSerializer(item).data
            for item in player.inventory.filter(is_pending_deletion=False)
        ]

    def get_equipment(self, player):
        slots = {}
        player_eq = player.equipment
        for eq_slot in adv_consts.EQUIPMENT_SLOTS:
            item = getattr(player_eq, eq_slot, None)
            if item:
                item_data = spawn_serializers.AnimateItemSerializer(item).data
                slots[eq_slot] = item_data
        return slots

    def get_room(self, player):
        return spawn_serializers.AnimateRoomSerializer(player.room).data

    def get_marks(self, player):
        return [
            {
                'name': name,
                'value': value,
            }
            for name, value in sorted(
                get_state_snapshot(STATE_SCOPE_CHARACTER, player).items()
            )
        ]

    def get_animation_data(self, player):
        return spawn_serializers.AnimatePlayerSerializer(player).data


    def get_instance_details(self, player):
        if player.world.is_multiplayer:
            return {}

        data = {}

        data['mob_count'] = player.world.mobs.count()
        data['item_count'] = player.world.items.count()

        return data


# Facts

class FactScheduleSerializer(serializers.ModelSerializer):

    change_msg = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = FactSchedule
        fields = [
            'id',
            'name',
            'selection',
            'fact',
            'value',
            'schedule',
            'schedule_type',
            'change_msg',
        ]

    def validate_fact(self, fact):
        return fact.lower().replace(' ', '_')


# World Reviews

class WorldReviewSerializer(serializers.ModelSerializer):

    description = serializers.CharField(required=True, allow_null=False)
    world = world_serializers.WorldSerializer(required=False)
    world_author = serializers.SerializerMethodField()
    world_builders = serializers.SerializerMethodField()
    world_last_updated = serializers.SerializerMethodField()

    class Meta:
        model = WorldReview
        fields = [
            'id',
            'world',
            'reviewer',
            'description',
            'text',
            'status',
            'world_author',
            'world_builders',
            'world_last_updated',
        ]

        read_only_fields = ['world']

    def get_world_author(self, review):
        author = review.world.author
        return {
            'id': author.id,
            'name': author.name,
            'email': author.email,
            'last_login': author.last_login,
        }

    def get_world_builders(self, review):
        return [
            {
                'id': builder.id,
                'name': builder.name,
                'email': builder.email,
                'last_login': builder.last_login,
            }
            for builder in review.world.builders.all()
            if builder != review.world.author
        ]

    def get_world_last_updated(self, review):
        last_viewed_room = LastViewedRoom.objects.filter(
            world=review.world
        ).order_by('-modified_ts').first()
        return last_viewed_room.modified_ts if last_viewed_room else None


class BuilderAssignmentSerializer(serializers.ModelSerializer):

    builder = ReferenceField(read_only=True)
    assignment = ReferenceField()

    class Meta:
        model = BuilderAssignment
        fields = ['id', 'builder', 'assignment']


class SocialSerializer(serializers.ModelSerializer):

    class Meta:
        model = Social
        fields = [
            'id',
            'cmd',
            'priority',
            'msg_targetless_self',
            'msg_targetless_other',
            'msg_targeted_self',
            'msg_targeted_target',
            'msg_targeted_other',
        ]

    def validate(self, data):
        if self.instance:
            if Social.objects.filter(
                world=self.context['world'],
                cmd=data['cmd']
                ).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError("Social already exists.")
        else:
            if Social.objects.filter(world=self.context['world'],
                                     cmd=data['cmd']).exists():
                raise serializers.ValidationError("Social already exists.")

        if (data.get('msg_targetless_self')
            or data.get('msg_targetless_other')):
            if (not data.get('msg_targetless_self')
                or not data.get('msg_targetless_other')):
                raise serializers.ValidationError(
                    "If specifying an emote without a target, "
                    "both Self and Other fields are required.")

        if (data.get('msg_targeted_self')
            or data.get('msg_targeted_target')
            or data.get('msg_targeted_other')):
            if (not data.get('msg_targeted_self')
                or not data.get('msg_targeted_target')
                or not data.get('msg_targeted_other')):
                raise serializers.ValidationError(
                    "If specifying an emote with a target, "
                    "all three fields are required (Self, Target, Other).")
        return data


class CurrencySerializer(serializers.ModelSerializer):

    is_default = serializers.SerializerMethodField()
    starting_amount = serializers.IntegerField(
        required=False,
        write_only=True,
        min_value=0,
        max_value=MAX_CURRENCY_AMOUNT,
    )
    usage = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Currency
        fields = [
            'id', 'code', 'name', 'plural_name', 'description',
            'is_default', 'starting_amount', 'usage', 'can_delete',
        ]

    def validate_code(self, value):
        value = str(value or '').strip().lower()
        if self.instance is not None and value != self.instance.code:
            raise serializers.ValidationError('Currency codes cannot be changed.')
        return value

    def get_is_default(self, currency):
        world = self.context.get('world') or currency.world
        return economy_world(world).default_currency_id == currency.pk

    def _starting_amount(self, currency):
        prefetched = getattr(currency, '_prefetched_objects_cache', {})
        rows = prefetched.get('starting_balance_rules')
        if rows is not None:
            row = next(
                (entry for entry in rows if entry.world_id == currency.world_id),
                None,
            )
        else:
            row = currency.starting_balance_rules.filter(
                world=currency.world,
            ).first()
        return int(row.amount) if row else 0

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['starting_amount'] = self._starting_amount(instance)
        return data

    def get_usage(self, currency):
        usage_map = self.context.get('currency_usage_map')
        if usage_map is not None:
            return usage_map.get(currency.pk, [])

        from builders.currencies import currency_usage

        cache = getattr(self, '_currency_usage_cache', None)
        if cache is None:
            cache = self._currency_usage_cache = {}
        if currency.pk not in cache:
            cache[currency.pk] = currency_usage(currency)
        return cache[currency.pk]

    def get_can_delete(self, currency):
        return not self.get_usage(currency)
