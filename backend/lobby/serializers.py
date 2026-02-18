from config import constants as adv_consts
from core.utils import is_ascii

from rest_framework import serializers

from config import constants as api_consts
from builders import serializers as builder_serializers
from builders.models import WorldBuilder
from core.serializers import AuthorField
from spawns.models import Player
from spawns.models import Item, Player
from worlds.models import World
from worlds.serializers import WorldSerializer


class LobbyWorldSerializer(WorldSerializer):

    author = AuthorField()
    num_user_characters = serializers.SerializerMethodField()
    num_world_player_characters = serializers.SerializerMethodField()
    num_characters = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_create_chars = serializers.BooleanField(
                                    source='config.can_create_chars',
                                    read_only=True)
    can_select_faction = serializers.BooleanField(
        source='config.can_select_faction', read_only=True)
    small_background = serializers.CharField(source='config.small_background',
                                             read_only=True)
    large_background = serializers.CharField(source='config.large_background',
                                             read_only=True)
    built_by = serializers.SerializerMethodField()
    allow_combat = serializers.BooleanField(source='config.allow_combat',
                                            read_only=True)
    is_narrative = serializers.BooleanField(source='config.is_narrative',
                                            read_only=True)
    is_private = serializers.SerializerMethodField()

    core_factions = serializers.SerializerMethodField()

    default_gender = serializers.CharField(source='config.default_gender',
                                           read_only=True)
    can_select_gender = serializers.BooleanField(
        source='config.can_select_gender', read_only=True)

    is_classless = serializers.BooleanField(
        source='config.is_classless', read_only=True)

    instance_of = serializers.SerializerMethodField()

    def get_built_by(self, world):
        if world.config and world.config.built_by:
            return world.config.built_by
        elif world.author:
            return world.author.name
        else:
            return 'Anonymous User'

    class Meta:
        model = World
        fields = [
            'id', 'key',
            'name', 'short_description', 'description', 'author', 'is_private',
            'built_by',
            'num_characters',
            'num_user_characters',
            'num_world_player_characters',
            'is_multiplayer',
            'can_edit',
            'can_create_chars',
            'can_select_faction',
            'small_background', 'large_background',
            'core_factions',
            'allow_combat', 'is_narrative',
            'default_gender', 'can_select_gender',
            'is_classless',
            'instance_of',
        ]

    def get_num_characters(self, world):
        if self.context.get('char_counts') == 'user':
            if self.context.get('request'):
                user = self.context['request'].user
            elif self.context.get('user'):
                user = self.context['user']
            else:
                return 0

            if user.is_authenticated:
                return Player.objects.filter(
                    world__context_id=world.pk,
                    user=user).count()

        return Player.objects.filter(
            world__context_id=world.pk,
            user__is_temporary=False,
        ).count()

    def get_num_user_characters(self, world):
        if self.context.get('request'):
            user = self.context['request'].user
        elif self.context.get('user'):
            user = self.context['user']
        else:
            return 0

        if user.is_authenticated:
            return Player.objects.filter(
                world__context_id=world.pk,
                user=user).count()
        return 0

    def get_num_world_player_characters(self, world):
        return Player.objects.filter(world__context_id=world.pk).count()

    def get_can_edit(self, world):
        if self.context.get('request'):
            user = self.context['request'].user
        elif self.context.get('user'):
            user = self.context['user']
        else:
            return False

        if not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if world.author == user:
            return True
        if WorldBuilder.objects.filter(
            world=world,
            user=user).exists():
            return True
        return False

    def get_core_factions(self, world):
        factions = world.world_factions.filter(
            is_core=True
        ).order_by('created_ts')
        return builder_serializers.FactionSerializer(
            factions, many=True).data

    def get_is_private(self, world):
        return not world.is_public

    def get_instance_of(self, world):
        base_world = world.instance_of
        if not base_world: return {}
        return {
            'name': base_world.name,
            'id': base_world.id,
        }


class LobbyWorldCardSerializer(WorldSerializer):

    small_background = serializers.CharField(source='config.small_background',
                                             read_only=True)

    num_characters = serializers.IntegerField(read_only=True)

    class Meta:
        model = World
        fields = [
            'id',
            'name',
            'num_characters',
            'small_background',
            'description',
        ]


class WorldTransferSerializer(serializers.Serializer):
    """
    Serializer to execute the transfer of a player from a single player
    world to a multi player world. The transfer is based on the
    ``transfer_to`` attribute of the room that the player is in.

    The serializer does not perform the actual animation step.
    """

    player = serializers.IntegerField()
    name = serializers.CharField()
    title = serializers.CharField(required=False)
    gender = serializers.ChoiceField(['male', 'female'], required=False)

    def validate_player(self, player_id):
        try:
            player = Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player ID")

        if player.user != self.context['request'].user:
            raise serializers.ValidationError(
                "Player does not belong to this user account.")

        # User should not be temporary
        if player.user.is_temporary:
            raise serializers.ValidationError("User is temporary.")

        return player

    def validate(self, validated_data):
        player = validated_data['player']

        # Origin world should be SPW
        if player.world.is_multiplayer:
            raise serializers.ValidationError(
                "Player is not in a single player world.")

        # Player in transfer room
        if not player.room.transfer_to:
            raise serializers.ValidationError(
                "Player is not in a transfer room.")

        # Dest world MPW
        if not player.room.transfer_to.world.is_multiplayer:
            raise serializers.ValidationError(
                "Destination world is not multiplayer.")

        # Origin world complete
        if player.world.lifecycle != api_consts.WORLD_STATE_COMPLETE:
            raise serializers.ValidationError(
                "Player is not in a completed world.")

        name = validated_data['name']

        if not is_ascii(name):
            raise serializers.ValidationError(
                "Name must be ASCII characters only.")

        # If more than one token is supplied for the name, set the
        # subsequent tokens to the title.
        if ' ' in name:
            first, rest = name.split(' ', maxsplit=1)
            name = first
            #validated_data['name'] = name
            if 'title' not in validated_data:
                validated_data['title'] = rest

        validated_data['name'] = Player.validate_name(
            player.room.transfer_to.world, name)

        return validated_data

    def create(self, validated_data):
        "Execute the transfer from SPW to MPW"

        player = validated_data['player']
        dest_room = player.room.transfer_to

        # Make sure that we really are transfering to another world.
        dest_spawn_world = dest_room.world.spawned_worlds.first()
        if dest_spawn_world == player.world:
            raise ValueError("Destination room is in current world.")

        # Get all the IDs of the items that are tied to this character
        item_ids = []
        # add inventory
        item_ids.extend(player.inventory.values_list('id', flat=True))
        item_ids.extend(
            player.equipment.inventory.values_list('id', flat=True))
        # See which of the containers have nested items
        containers = Item.objects.filter(
            id__in=item_ids,
            type=adv_consts.ITEM_TYPE_CONTAINER)
        for container in containers:
            item_ids.extend(container.get_contained_ids())

        # Set all the items to now belong to the new world
        Item.objects.filter(id__in=item_ids).update(world=dest_spawn_world)

        # Set the player's world
        player.world = dest_spawn_world
        player.room = dest_room
        # Change player's name
        player.name = validated_data['name']
        if validated_data.get('title'):
            player.title = validated_data['title']
        # Optionally change player's gender
        if validated_data.get('gender'):
            player.gender = validated_data['gender']
        # Save changes
        player.save()

        # Clear out viewed rooms
        player.viewed_rooms.through.objects.filter(player=player).delete()

        return player
