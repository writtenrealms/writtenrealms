import json
import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import F
from django.db.utils import IntegrityError
from django.utils import timezone

from config import constants as adv_consts
from core.utils.mobs import suggest_stats

from rest_framework import serializers

from config import constants as api_consts
from config import game_settings as adv_config
from builders.models import (
    BuilderAssignment,
    Currency,
    LastViewedRoom,
    ItemTemplate,
    ItemTemplateInventory,
    ItemAction,
    Loader,
    MobTemplate,
    MobTemplateInventory,
    MerchantInventory,
    TransformationTemplate,
    Faction,
    FactionAssignment,
    FactionRank,
    FactSchedule,
    Quest,
    Reward,
    Objective,
    Rule,
    RandomItemProfile,
    RoomCheck,
    RoomAction,
    Trigger,
    Skill,
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
from spawns.models import Player, DoorState, PlayerConfig
from system.models import Nexus
from system.policies import get_platform_policy
from users.models import User
from worlds import serializers as world_serializers
from worlds.models import (
    InstanceAssignment,
    World,
    WorldConfig,
    Zone,
    Room,
    RoomFlag,
    RoomDetail,
    Door,
    WorldLocks)


# Common to both RoomActionSerializer and RoomCheckSerializer
def validate_conditions(self, conditions):
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
    is_classless = serializers.BooleanField(source='config.is_classless',
                                            read_only=True)
    instance_of = serializers.SerializerMethodField()
    builder_info = serializers.SerializerMethodField()
    currencies = serializers.SerializerMethodField()
    state = serializers.CharField(source='lifecycle', read_only=True)

    class Meta:
        model = World
        fields = (
            'key', 'id', 'name', 'description', 'motd', 'author', 'created_ts',
            'last_viewed_room', 'short_description', 'state', 'is_multiplayer',
            'is_public', 'factions', 'facts', 'is_classless',
            'review', 'maintenance_mode', 'maintenance_msg', 'instance_of',
            'builder_info', 'currencies',
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
        facts = world.facts or "{}"
        return json.loads(facts)

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
        return [
            {
                'id': currency.id,
                'name': currency.name,
                'code': currency.code,
                'is_default': currency.is_default,
            } for currency in world.currencies.all()
        ]

    def create(self, validated_data):
        if 'author' not in validated_data:
            validated_data['author'] = self.context['request'].user

        world = World.objects.new_world(**validated_data)

        if self.context['request'].data.get('instance_of'):
            instance_of = World.objects.get(
                pk=self.context['request'].data['instance_of'])

            if not instance_of.is_multiplayer:
                raise serializers.ValidationError(
                    'Cannot create an instance of a singleplayer world.')

            world.instance_of = instance_of
            world.is_multiplayer = True
            world.save()
        else:
            Currency.objects.create(
                code='gold',
                name='Gold',
                is_default=True,
                world=world)

            Currency.objects.create(
                code='medals',
                name='Medals',
                is_default=False,
                world=world)

            spawn_world = world.create_spawn_world()
            player = Player.objects.create(
                world=spawn_world,
                user=self.context['request'].user,
                name='Builder',
                is_immortal=True,
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
            'starting_gold',
            'starting_room',
            'death_room',
            'death_mode',
            'death_route',
            'small_background',
            'large_background',
            'can_select_faction',
            'auto_equip',
            'allow_combat',
            'is_narrative',
            'players_can_set_title',
            'allow_pvp',
            'pvp_mode',
            'built_by',
            'is_classless',
            'non_ascii_names',
            'decay_glory',
            'name_exclusions',
            'globals_enabled',
        ]

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

    class Meta:
        model = World
        fields = [
            'id', 'name', 'is_multiplayer', 'maintenance_mode',
            'stats', 'spawned_worlds',
        ]

    def get_stats(self, world):
        return {
            'num_item_templates': world.item_templates.count(),
            'num_mob_templates': world.mob_templates.count(),
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


class WorldAdminSpawnWorldSerializer(serializers.ModelSerializer):

    live_data = serializers.SerializerMethodField()
    forge_data = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name', 'lifecycle', 'lifecycle_change_ts', 'live_data', 'forge_data',
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

    class Meta:
        model = Zone
        fields = (
            'id',
            'key',
            'name',
            'num_rooms',
            'center',
            'zone_data',
            'respawn_wait',
            'pvp_zone',
            'has_assignment'
        )

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
        return json.loads(zone.zone_data)

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

    num_room_checks = serializers.SerializerMethodField()
    num_loads = serializers.SerializerMethodField()
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
            'num_room_checks', 'num_actions', 'num_triggers', 'num_loads', 'details', 'doors',
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

    def get_num_room_checks(self, room):
        return room.room_checks.count()

    def get_num_actions(self, room):
        return room.room_actions.count()

    def get_num_triggers(self, room):
        return Trigger.objects.filter(
            world_id=room.world_id,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=room.id,
        ).count()

    def get_num_loads(self, room):
        room_rules_qs = Rule.objects.filter(
            target_type=ContentType.objects.get_for_model(room),
            target_id=room.id)
        path_ids = PathRoom.objects.filter(
            room=room
        ).values_list('path_id', flat=True)
        path_rules_qs = Rule.objects.filter(
            target_type=ContentType.objects.get_for_model(Path),
            target_id__in=path_ids)
        return room_rules_qs.count() + path_rules_qs.count()

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
            'id', 'key', 'name', 'model_type',
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


class RoomCheckSerializer(serializers.ModelSerializer):
    check = serializers.CharField(source='check_type', required=False, allow_blank=True)

    class Meta:
        model = RoomCheck
        fields = [
            'id',
            'name',
            'direction',
            'prevent',
            'check',
            'argument',
            'argument2',
            'failure_msg',
            'conditions',
        ]

    validate_conditions = validate_conditions


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


class RoomAddLoadSerializer(serializers.Serializer):

    template = ReferenceField()

    def validate_template(self, template):
        if isinstance(template, ItemTemplate) and template.is_persistent:
            raise serializers.ValidationError(
                "Cannot load a persistent item via loader. Use the /load command"
                "instead.")
        return template

    def create(self, validated_data):

        template = self.validated_data['template']
        room = self.context['room']

        loader = Loader.objects.create(
            world=room.world,
            zone=room.zone,
            name="{template} in {room}".format(
                template=template.name,
                room=room.name))
        rule = Rule.objects.create(
            loader=loader,
            template=template,
            target=room)
        return loader


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


class ItemActionSerializer(ActionSerializer):
    class Meta:
        model = ItemAction
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


# Item Templates

class ItemTemplateSerializer(serializers.ModelSerializer):

    budget = serializers.SerializerMethodField()
    cost_budget = serializers.SerializerMethodField()

    # If keywords are not defined, returned what will be used automatically
    # instead, for builder UI display purposes.
    empty_keywords = serializers.SerializerMethodField()

    power = serializers.SerializerMethodField()

    has_assignment = serializers.SerializerMethodField()

    #currency = serializers.SerializerMethodField()

    class Meta:
        model = ItemTemplate
        fields = [
            'id', 'key', 'name', 'model_type', 'is_persistent',
            'level', 'description', 'ground_description', 'notes',
            'keywords', 'empty_keywords',
            'type', 'quality', 'power', 'is_boat', 'is_pickable',
            'cost', 'currency',
            'equipment_type', 'armor_class',
            'weapon_type', 'hit_msg_first', 'hit_msg_third',
            'health_max', 'health_regen', 'mana_max', 'mana_regen',
            'stamina_max', 'stamina_regen',
            'strength', 'constitution', 'dexterity', 'intelligence',
            'attack_power', 'spell_power', 'resilience', 'dodge', 'crit',
            'budget', 'cost_budget', 'food_value', 'food_type',
            'has_assignment',
            'on_use_cmd', 'on_use_description', 'on_use_equipped',
        ]

    def create(self, validated_data):
        # Null out equipment type if the item is not of type equipment
        if (validated_data.get('type') != adv_consts.ITEM_TYPE_EQUIPPABLE
            and validated_data.get('equipment_type')):
            validated_data['equipment_type'] = None
        if (validated_data.get('type') == adv_consts.ITEM_TYPE_FOOD
            and not validated_data.get('food_type')):
            validated_data['food_type'] = adv_consts.ITEM_FOOD_TYPE_STAMINA

        if 'currency' not in validated_data:
            default_currency = Currency.objects.filter(
                world=self.context['world'],
                is_default=True).first()
            validated_data['currency'] = default_currency

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Null out eq type if the item is not currently of type equipment
        # or augment and is not being made of type equipment as part of the
        # save.
        if validated_data.get('equipment_type'):
            if (instance.type != adv_consts.ITEM_TYPE_EQUIPPABLE and
                validated_data.get('type') != adv_consts.ITEM_TYPE_EQUIPPABLE
                and instance.type != adv_consts.ITEM_TYPE_AUGMENT and
                validated_data.get('type') != adv_consts.ITEM_TYPE_AUGMENT):
                validated_data['equipment_type'] = None

            # if adv_consts.ITEM_TYPE_EQUIPPABLE not in (
            #     instance.type, validated_data.get('type')):
            #     validated_data['equipment_type'] = None

        if validated_data.get('type'):
            if (instance.type == adv_consts.ITEM_TYPE_CONTAINER and
                validated_data['type'] != instance.type and
                ItemTemplateInventory.objects.filter(
                    container=instance).exists()):
                raise serializers.ValidationError(
                    'Container has inventory.')

        # Do the actual update
        updated_instance = super().update(instance, validated_data)

        # If any of the attributes being passed are boost attributes,
        # see if the item quality needs to be updated
        if (set(adv_consts.ATTRIBUTES) &
            set(validated_data.keys())):
            budget_spent = updated_instance.budget_spent
            budget = updated_instance.budget
            try:
                budget_ratio = budget_spent / budget
            except ZeroDivisionError:
                budget_ratio = 0
            if budget_ratio == 0:
                updated_instance.quality = adv_consts.ITEM_QUALITY_NORMAL
            elif budget_ratio >= 1.2:
                updated_instance.quality = adv_consts.ITEM_QUALITY_ENCHANTED
            else:
                updated_instance.quality = adv_consts.ITEM_QUALITY_IMBUED
            updated_instance.save()

        return updated_instance

    def get_budget(self, item_template):
        "Return budget utilization"
        spent = item_template.budget_spent
        max = item_template.budget_max
        if max:
            perc = '%s%%' % int(spent / max * 100)
        else:
            perc = 'n/a'
        return "{spent} / {max} ({perc})".format(
            perc=perc, spent=spent, max=max)

    def get_cost_budget(self, item_template):
        from builders.random_items import price_item
        cost = item_template.cost
        budget = price_item(
            level=item_template.level,
            quality=item_template.quality)
        perc = '%s%%' % int(cost / budget * 100)
        return "{spent} / {max} ({perc})".format(
            perc=perc, spent=cost, max=budget)

    def get_empty_keywords(self, item_template):
        # Exclude name tokens, normalize to lowercase
        return ' '.join(list(reversed([
            token.lower() for token in item_template.name.split(' ')
            if token not in adv_consts.EXCLUDE_NAME_TOKENS
        ])))

    def validate_is_persistent(self, value):
        if self.instance and value:
            if Rule.objects.filter(
                template_type=ContentType.objects.get_for_model(self.instance),
                template_id=self.instance.id).exists():
                raise serializers.ValidationError(
                    "Cannot set templates that are loaded by rules to be "
                    "persistent.")
        return value

    def validate_food_type(self, value):
        if (self.instance
            and self.instance.type == adv_consts.ITEM_TYPE_FOOD
            and not value):
            raise serializers.ValidationError("Food items require a food type")
        return value

    def validate(self, data):
        def throw_error():
            raise serializers.ValidationError(
                "Cannot set persistent item to be pickable.")

        if data.get('is_persistent') and data.get('is_pickable'):
            throw_error()

        return data

    def get_power(self, item_template):
        try:
            return item_template.budget_spent / item_template.budget
        except ZeroDivisionError:
            return 0

    def get_has_assignment(self, item_template):
        try:
            if self.context['request'].user == item_template.world.author:
                return True
        except KeyError:
            return False

        builder = WorldBuilder.objects.filter(
            world=item_template.world,
            user=self.context['request'].user).first()

        if not builder:
            return False

        if builder.builder_rank >= 3:
            return True

        if BuilderAssignment.objects.filter(
                builder=builder,
                assignment_id=item_template.id,
                assignment_type=ContentType.objects.get_for_model(ItemTemplate)
            ).exists():
            return True

        return False

    def get_currency(self, item_template):
        return item_template.currency.code


class ItemTemplateInventorySerializer(serializers.ModelSerializer):
    container = ReferenceField(required=False, allow_null=False)
    item_template = ReferenceField(required=True, allow_null=False)
    class Meta:
        model = ItemTemplateInventory
        fields = [
            'key', 'id', 'container', 'item_template',
            'probability', 'num_copies'
        ]

    def validate_num_copies(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} copies max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies

class AddItemTemplateInventorySerializer(serializers.Serializer):

    item_template = ReferenceField()
    probability = serializers.IntegerField(required=False)
    num_copies = serializers.IntegerField(required=False)

    def __init__(self, container, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.container = container

    def create(self, validated_data):
        return ItemTemplateInventory.objects.create(
            container=self.container,
            **validated_data)

    def validate_num_copies(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} copies max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies

    def validate(self, *args, **kwargs):
        if self.container.type != adv_consts.ITEM_TYPE_CONTAINER:
            raise serializers.ValidationError("Item is not a container.")
        return super().validate(*args, **kwargs)


# Mob Templates

class MobTemplateSerializer(serializers.ModelSerializer):

    empty_keywords = serializers.SerializerMethodField()
    core_faction = serializers.SerializerMethodField()
    has_assignment = serializers.SerializerMethodField()

    class Meta:
        model = MobTemplate
        fields = [
            'id', 'key', 'name', 'model_type',
            'level', 'description', 'room_description',
            'keywords', 'empty_keywords',
            'notes', 'gold',
            'type', 'archetype', 'gender', 'exp_worth',
            'roaming_type', 'alignment', 'aggression', 'use_abilities',
            'roam_chance',
            'hit_msg_first', 'hit_msg_third',
            'health_max', 'health_regen', 'mana_max', 'mana_regen',
            'stamina_max', 'stamina_regen', 'regen_rate',
            'attack_power', 'spell_power',  'crit',
            'resilience', 'dodge', 'armor',
            'drops_random_items', 'num_items', 'is_crafter',
            'load_specification',
            'chance_imbued', 'chance_enchanted',
            'factions', 'default_stats',
            'is_elite', 'is_invisible', 'fights_back',
            'core_faction',
            'craft_multiplier', 'craft_enchanted',
            'teaches', 'teaching_conditions', 'combat_script', 'use_abilities',
            'unlearns', 'unlearn_cost',
            'has_assignment', 'traits',
            'is_upgrader', 'upgrade_cost_multiplier',
            'upgrade_success_chance',
            'upgrade_success_cmd', 'upgrade_failure_cmd',
            'merchant_profit',
        ]

    def get_core_faction(self, mob_template):
        return mob_template.factions.get('core')

    def create(self, validated_data):
        from core.utils.mobs import suggest_stats

        # Incorporate suggested stats based on the mob's level
        level = validated_data.get('level', 1)
        suggested_stats = suggest_stats(
            level=validated_data.get('level', level),
            archetype=validated_data.get('archetype', 'warrior'),
            is_elite=validated_data.get('is_elite', False))

        for stat, value in suggested_stats.items():
            if stat not in validated_data:
                validated_data[stat] = value

        # Give humanoid mobs gold by default
        mob_type = validated_data.get('type', adv_consts.MOB_TYPE_BEAST)
        if (mob_type == adv_consts.MOB_TYPE_HUMANOID
            and not suggested_stats.get('gold', 0)):
            validated_data['gold'] = round(adv_config.ILF(level))

        validated_data['default_stats'] = True

        return super().create(validated_data)

    def update(self, instance, validated_data):
        if validated_data.get('default_stats'):

            is_elite = instance.is_elite
            if 'is_elite' in validated_data:
                is_elite = validated_data['is_elite']

            level = validated_data.get('level') or instance.level
            archetype = validated_data.get('archetype') or instance.archetype
            suggested_stats = suggest_stats(
                level=level,
                archetype=archetype,
                is_elite=is_elite)
            validated_data.update(suggested_stats)

        if (validated_data.get('is_elite') is not None
            and instance.default_stats):
            level = validated_data.get('level') or instance.level
            archetype = validated_data.get('archetype') or instance.archetype
            suggested_stats = suggest_stats(
                level=level,
                archetype=archetype,
                is_elite=validated_data['is_elite'])
            validated_data.update(suggested_stats)

        return super().update(instance, validated_data)

    def get_empty_keywords(self, mob_template):
        # Exclude name tokens, normalize to lowercase
        return ' '.join(list(reversed([
            token.lower() for token in mob_template.name.split(' ')
            if token not in adv_consts.EXCLUDE_NAME_TOKENS
        ])))

    def validate_level(self, level):
        if level > api_consts.LEVEL_CAP:
            raise serializers.ValidationError(
                "Max level is %s" % api_consts.LEVEL_CAP)
        return level

    def validate_num_items(self, num_items):
        if num_items > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} items max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_items

    def get_has_assignment(self, mob_template):
        try:
            if self.context['request'].user == mob_template.world.author:
                return True
        except KeyError:
            return False

        builder = WorldBuilder.objects.filter(
            world=mob_template.world,
            user=self.context['request'].user).first()

        if not builder:
            return False

        if builder.builder_rank >= 3:
            return True

        if BuilderAssignment.objects.filter(
                builder=builder,
                assignment_id=mob_template.id,
                assignment_type=ContentType.objects.get_for_model(MobTemplate)
            ).exists():
            return True

        return False


class MobTemplateInventorySerializer(serializers.ModelSerializer):

    container = ReferenceField(required=False, allow_null=False)
    item_template = ReferenceField(required=True, allow_null=False)

    class Meta:
        model = MobTemplateInventory
        fields = [
            'key',
            'id',
            'container',
            'item_template',
            'probability',
            'num_copies'
        ]

    def validate_num_copies(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} copies max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies


class AddMobTemplateInventorySerializer(serializers.Serializer):

    item_template = ReferenceField()
    probability = serializers.IntegerField(required=False)
    num_copies = serializers.IntegerField(required=False)

    def __init__(self, container, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.container = container

    def create(self, validated_data):
        return MobTemplateInventory.objects.create(
            container=self.container,
            **validated_data)

    def validate_num_copies(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} copies max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies


class MobTemplateMerchantInventorySerializer(serializers.ModelSerializer):

    item_template = ReferenceField(required=False, allow_null=True)
    random_item_profile = ReferenceField(required=False, allow_null=True)
    name = serializers.SerializerMethodField()

    def get_name(self, foo): return 'name'

    class Meta:
        model = MerchantInventory
        fields = [
            'id', 'num', 'name',
            'item_template', 'random_item_profile',
        ]

    def validate(self, data):
        validated_data = super().validate(data)

        # have item or profile but neither nor both

        if (not validated_data.get('item_template') and
            not validated_data.get('random_item_profile')):
            raise serializers.ValidationError(
                "Either an item template or a random profile is required.")

        if (validated_data.get('item_template') and
            validated_data.get('random_item_profile')):
            raise serializers.ValidationError(
                "Specify either an item template or a random profile "
                "(but not both).")

        return validated_data

    def validate_num(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "{} copies max".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies


class MobFactionAssignmentSerializer(serializers.ModelSerializer):

    faction = ReferenceField(required=True, allow_null=False)

    class Meta:
        model = FactionAssignment
        fields = [
            'id',
            'faction',
            'value',
        ]

    def validate(self, validated_data):
        faction = validated_data['faction']

        if faction.is_core and FactionAssignment.objects.filter(
            member_type=ContentType.objects.get_for_model(MobTemplate),
            member_id=self.context['mob_template'].id,
            faction__is_core=True).exists():
            raise serializers.ValidationError(
                'Template already has a core faction association')

        return super().validate(validated_data)


    def create(self, validated_data):
        return FactionAssignment.objects.create(
            member=self.context['mob_template'],
            **validated_data)


def validate_reaction(self, validated_data):
    event = validated_data.get('event')
    if event is None and getattr(self, 'instance', None) is not None:
        event = self.instance.event
    if event is None:
        raise serializers.ValidationError("Event is required.")

    option = validated_data.get('option')
    if option is None and getattr(self, 'instance', None) is not None:
        option = self.instance.option
    option = option or ''

    if (event not in (adv_consts.MOB_REACTION_EVENT_ENTERING,
                      adv_consts.MOB_REACTION_EVENT_CONNECT,
                      adv_consts.MOB_REACTION_EVENT_LOAD,
                      adv_consts.MOB_REACTION_EVENT_DEATH,
                      adv_consts.MOB_REACTION_EVENT_COMBAT_ENTER,
                      adv_consts.MOB_REACTION_EVENT_COMBAT_EXIT,
                      adv_consts.MOB_REACTION_EVENT_NEW_ROOM)
        and not option):

        msg = "Option is required: "

        if event == adv_consts.MOB_REACTION_EVENT_SAYING:
            msg += "enter the keywords to react to"

        elif event == adv_consts.MOB_REACTION_EVENT_RECEIVE:
            msg += "enter the template ID to look for"

        elif event == adv_consts.MOB_REACTION_EVENT_PERIODIC:
            msg += "enter how often to react"

        raise serializers.ValidationError(msg)

    if option:
        try:
            trigger_matcher.validate_match_expression(option)
        except trigger_matcher.MatchExpressionError as err:
            raise serializers.ValidationError(
                f"Invalid option matcher expression: {err}"
            )
    return validated_data

class MobReactionSerializer(serializers.ModelSerializer):

    template = serializers.SerializerMethodField()
    option = serializers.CharField(required=False, allow_blank=True)
    reaction = serializers.CharField(source='script')

    class Meta:
        model = Trigger
        fields = [
            'key', 'id',
            'template', 'event', 'option', 'reaction', 'conditions'
        ]

    def get_template(self, trigger):
        if not trigger.target_type_id:
            return None
        if trigger.target_type.model_class() != MobTemplate:
            return None
        if not trigger.target:
            return None
        return KeyNameSerializer(trigger.target).data

    validate = validate_reaction


class AddMobReactionSerializer(serializers.Serializer):

    option = serializers.CharField(required=False, allow_blank=True)
    event = serializers.ChoiceField(choices=adv_consts.MOB_REACTION_EVENTS)
    conditions = serializers.CharField(required=False, allow_blank=True)
    reaction = serializers.CharField()

    def __init__(self, template, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.template = template

    def create(self, validated_data):
        return Trigger.objects.create(
            world=self.template.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobTemplate),
            target_id=self.template.id,
            event=validated_data['event'],
            option=validated_data.get('option', ''),
            script=validated_data['reaction'],
            conditions=validated_data.get('conditions', ''),
            display_action_in_room=False,
        )

    validate = validate_reaction


# Loaders

class LoaderSerializer(serializers.ModelSerializer):
    key = serializers.ReadOnlyField()
    num_rules = serializers.SerializerMethodField()
    zone = ReferenceField(required=False, allow_null=True)
    world = ReferenceField(read_only=True)
    zone_wait = serializers.IntegerField(source='zone.respawn_wait',
                                         read_only=True)

    class Meta:
        model = Loader
        fields = [
            'id', 'key', 'world', 'zone',
            'name', 'description', 'respawn_wait', 'num_rules',
            'loader_condition', 'conditions', 'inherit_zone_wait',
            'zone_wait',
        ]

    def get_num_rules(self, loader):
        return loader.rules.count()

    def create(self, validated_data):
        # Give Loaders created by the API a 5 min wait by default
        if 'respawn_wait' not in validated_data:
            validated_data['respawn_wait'] = 300
        return super().create(validated_data)


class RuleSerializer(serializers.ModelSerializer):

    template = ReferenceField()
    target = ReferenceField(required=False, allow_null=True)
    name = serializers.SerializerMethodField()

    class Meta:
        model = Rule
        fields = [
            'id', 'key', 'model_type', 'name',
            #'loader',
            'template', 'target', 'order', 'num_copies', 'options',
        ]

    def get_name(self, rule):
        return "Rule #%s" % rule.order

    def get_target(self, rule):
        target = rule.target
        if target:
            return KeyNameSerializer(target).data
        return None

    def validate_template(self, template):
        loader = self.context['view'].loader
        template_world_id = getattr(template, 'world_id', None)
        instance_root_id = getattr(loader.world, 'instance_of_id', None)

        if (template_world_id is not None
            and template_world_id not in (loader.world_id, instance_root_id)):
            raise serializers.ValidationError(
                "Template does not belong to loader's world.")

        if isinstance(template, ItemTemplate) and template.is_persistent:
            raise serializers.ValidationError(
                "Rule cannot load persistent items.")
        return template

    def validate_target(self, target):
        loader = self.context['view'].loader
        if isinstance(target, Room) and loader.zone != target.zone:
            raise serializers.ValidationError(
                "Room does not belong to loader's zone.")

        if isinstance(target, Rule) and target.loader != loader:
            raise serializers.ValidationError(
                "Rule target does not belong to loader.")

        if (self.instance
            and isinstance(target, Rule)
            and self.instance == target):
            raise serializers.ValidationError(
                "A rule may not target itself.")

        return target

    def validate_num_copies(self, num_copies):
        if int(num_copies) > api_consts.MAX_RULE_SPAWNS:
            raise serializers.ValidationError(
                "A rule cannot spawn more than {} copies.".format(
                    api_consts.MAX_RULE_SPAWNS))
        return num_copies

    def validate(self, data):
        "Make sure that no quest mob can be loaded twice."

        template = data.get('template') or self.instance.template

        if (data.get('template')
            and isinstance(template, MobTemplate)
            and template.template_quests.count()):
            # See whether any rule already targets this mob template
            existing_qs = Rule.objects.filter(
                template_type=ContentType.objects.get_for_model(template),
                template_id=template.id
            )
            if (self.instance):
                existing_qs = existing_qs.exclude(template_id=template.id)
            if existing_qs.exists():
                raise serializers.ValidationError(
                    "Quest template already loaded by rule %s" % existing_qs.get().id)

        if (data.get('template')
            and isinstance(template, TransformationTemplate)
            and not isinstance(data['target'], Rule)):
            raise serializers.ValidationError(
                "Transformation Templates can only target rules.")

        if (data.get('num_copies')
            and int(data['num_copies']) > 1
            and isinstance(template, MobTemplate)
            and template.template_quests.count()):
            raise serializers.ValidationError(
                "Can only load 1 copy of a quest template.")

        if (isinstance(template, MobTemplate)
            and isinstance(data['target'], Rule)):
            raise serializers.ValidationError(
                "Mob template rule cannot target the output of another rule.")

        return data


class SuggestMobSerializer(serializers.Serializer):
    level = serializers.IntegerField(default=1)
    archetype = serializers.ChoiceField(choices=adv_consts.ARCHETYPES,
                                        default=adv_consts.ARCHETYPE_WARRIOR)


class RandomItemProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = RandomItemProfile
        fields = [
            'name', 'level',
            'chance_imbued', 'chance_enchanted',
            'restriction'
        ]


# Quests


# class AddObjectiveSerializer(serializers.Serializer):

#     type = serializers.ChoiceField(choices=adv_consts.OBJECTIVE_TYPES)
#     template = ReferenceField()
#     qty = serializers.IntegerField(default=1)


class ObjectiveSerializer(serializers.ModelSerializer):

    template = ReferenceField(required=False, allow_null=True)

    class Meta:
        model = Objective
        fields = [
            'id',
            'type',
            'qty',
            'template',
            'currency',
        ]

    def get_template(self, obj):
        if isinstance(obj.template, MobTemplate):
            return MobTemplateSerializer(obj.template).data
        elif isinstance(obj.template, ItemTemplate):
            return ItemTemplateSerializer(obj.template).data
        return None

    def create(self, validated_data, quest):
        return Objective.objects.create(quest=quest, **validated_data)

    def validate(self, data):
        vd = super().validate(data)

        if (vd.get('type')):
            if (vd['type'] == adv_consts.OBJECTIVE_TYPE_ITEM and
                not vd.get('template')):
                raise serializers.ValidationError('Template is required.')
        return vd


class RewardSerializer(serializers.ModelSerializer):

    profile = ReferenceField(required=False, allow_null=True)

    class Meta:
        model = Reward
        fields = [
            'id',
            'type',
            'qty',
            'profile',
            'currency',
        ]

    def create(self, validated_data, quest):
        return Reward.objects.create(quest=quest, **validated_data)

    def get_profile(self, obj):
        if isinstance(obj.profile, ItemTemplate):
            return ItemTemplateSerializer(obj.profile).data
        elif isinstance(obj.profile, RandomItemProfile):
            return RandomItemProfileSerializer(obj.profile).data
        return None

    def validate(self, data):
        vd = super().validate(data)

        if (vd.get('type')):
            if vd['type'] == adv_consts.REWARD_TYPE_FACTION:
                if not vd.get('profile'):
                    raise serializers.ValidationError('Faction is required.')
                elif vd['profile'].is_core:
                    raise serializers.ValidationError(
                        'Can only reward non-core factions.')

            if (vd['type'] == adv_consts.REWARD_TYPE_FACTION and
                not vd.get('profile')):
                raise serializers.ValidationError(
                    'Faction is required.')

            if (vd['type'] == adv_consts.REWARD_TYPE_ITEM and
                not vd.get('profile')):
                raise serializers.ValidationError(
                    'Item template or random profile is required.')

        return vd


class QuestSerializer(serializers.ModelSerializer):

    rewards = RewardSerializer(many=True, read_only=True)
    objectives = ObjectiveSerializer(many=True, read_only=True)
    #mob_template = MobTemplateSerializer(required=False)
    mob_template = ReferenceField(required=False)
    #requires_quest_id = serializers.SerializerMethodField()
    zone = ReferenceField(required=False)
    requires_quest = ReferenceField(required=False, allow_null=True)
    required_by = serializers.SerializerMethodField()

    class Meta:
        model = Quest
        fields = [
            'id', 'key', 'name', 'notes', 'level', 'summary',
            'zone',
            'rewards', 'objectives',
            'is_hidden', 'is_setup', 'is_logged',
            'mob_template',
            'repeatable_after',
            'wait_until_cmds',
            'enquire_cmd_available',
            'enquire_cmds',
            'enquire_keywords',
            'completion_cmd_available',
            'completion_cmds',
            'completion_keywords',
            'completion_despawn',
            'completion_action',
            'complete_silently',
            'repeat_completion_entrance_cmds_after',
            'entrance_cmds',
            'completion_entrance_cmds',
            'repeat_entrance_cmd_after',
            'requires_quest_id',
            'requires_quest',
            'requires_level',
            'required_by',
            'max_level',
            'conditions',
            'completion_conditions',
        ]

    def validate_mob_template(self, mob):
        # See if the quest mob is loaded more than once
        rules_qs = Rule.objects.filter(
            template_type=ContentType.objects.get_for_model(mob),
            template_id=mob.id)
        if (rules_qs.count() > 1 or
            rules_qs.count() == 1 and rules_qs.first().num_copies > 1):
            raise serializers.ValidationError(
                "Cannot assign quest to mob loaded multiple times.")
        return mob

    def validate_requires_quest(self, quest):
        if self.instance and self.instance == quest:
            raise serializers.ValidationError(
                "Cannot have a quest be its own requirement.")
        return quest

    def create(self, validated_data):
        if 'zone' in validated_data:
            world = validated_data['zone'].world
        elif 'zone' in self.context:
            world = self.context['zone'].world
        else:
            raise ValueError("Zone is required for quest creation.")

        return Quest.objects.create(
            world=world,
            **validated_data)

    def get_requires_quest_id(self, quest):
        if not quest.requires_quest:
            return None
        else:
            return quest.requires_quest.relative_id

    def get_required_by(self, quest):
        return [
            ReferenceField().to_representation(pre_quest)
            for pre_quest in quest.prereq_quests.all()
        ]


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

    def create(self, validated_data):
        instance = super().create(validated_data)
        self.check_default(instance, validated_data)
        return instance

    def update(self, instance, validated_data):
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

        # Can't switch from minor to core if a char has that faction assigned
        # as well as a core faction already.
        if (faction
            and not faction.is_core
            and 'is_core' in data
            and data['is_core']):
            # If we're switching the faction from minor to core

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
    class Meta:
        model = Path
        fields = [
            'id',
            'name',
            'key',
        ]


class PathDetailsSerializer(serializers.ModelSerializer):
    rooms = serializers.SerializerMethodField()
    class Meta:
        model = Path
        fields = [
            'id',
            'name',
            'key',
            'rooms',
        ]

    def get_rooms(self, path):
        qs = MapRoomSerializer.prefetch_map(path.rooms.all())
        return MapRoomSerializer(qs, many=True).data

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


class RandomItemProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = RandomItemProfile
        fields = [
            'id', 'key', 'model_type',
            'name',
            'level',
            'chance_imbued',
            'chance_enchanted',
            'restriction',
        ]


class TransformationTemplateSerializer(serializers.ModelSerializer):

    class Meta:
        model = TransformationTemplate
        fields = [
            'id', 'name', 'key', 'model_type', 'transformation_type', 'arg1', 'arg2',
        ]


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
    quests = serializers.SerializerMethodField()
    room = serializers.SerializerMethodField()
    trophy = serializers.SerializerMethodField()
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
            'quests',
            'room',
            'equipment',
            'trophy',
            'factions',
            'animation_data',
            'is_immortal',
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

    def get_quests(self, player):
        return [
            ReferenceField().to_representation(player_quest.quest)
            for player_quest in player.player_quests.all()
        ]

    def get_room(self, player):
        return spawn_serializers.AnimateRoomSerializer(player.room).data

    def get_trophy(self, player):
        from collections import Counter

        entries = player.trophy_entries.all()
        # Get the ID of all the mobs killed
        mob_ids = player.trophy_entries.values_list(
            'mob_template_id', flat=True)

        mob_counts = dict(Counter(mob_ids))

        mobs = MobTemplate.objects.filter(pk__in=set(mob_ids))
        for mob in mobs:
            mob_counts[mob.id] = {
                'mob_template': ReferenceField().to_representation(mob),
                'count': mob_counts[mob.id],
            }

        return mob_counts

    def get_marks(self, player):
        marks = []
        for mark in player.marks.all():
            marks.append({
                'name': mark.name,
                'value': mark.value,
            })
        return marks

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


# Custom Skills

class SkillDetailSerializer(serializers.ModelSerializer):

    skill = serializers.CharField(source='code', read_only=True)

    class Meta:
        model = Skill
        fields = [
            'id',
            'code',
            'skill',
            'name',
            'level',
            'intent',
            'cost',
            'cost_type',
            'cost_calc',
            'damage',
            'damage_type',
            'damage_calc',
            'cast_time',
            'cooldown',
            'effect',
            'effect_damage',
            'effect_duration',
            'effect_damage_type',
            'effect_damage_calc',
            'arguments',
            'help',
            'consumes',
            'requires',
            'learn_conditions',
        ]

    def validate_code(self, code):
        if ' ' in code:
            raise serializers.ValidationError(
                "Code cannot contain spaces.")

        # Check for code uniqueness and case insensitivity
        world = self.context['view'].world
        if self.instance:
            if Skill.objects.filter(
                world=world,
                code__iexact=code).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError(
                    "A skill with this code already exists.")
        else:
            if Skill.objects.filter(world=world, code__iexact=code).exists():
                raise serializers.ValidationError(
                    "A skill with this code already exists.")

        return code


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

    class Meta:
        model = Currency
        fields = ['id', 'code', 'name', 'is_default']
