from django.db.models import Q, Value, CharField
from django.db.models.functions import Concat, Lower
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from rest_framework import serializers

from config import constants as adv_consts
from config import constants as api_consts
from builders.models import HousingLease
from core.serializers import ref_field, ReferenceField
from core.economy import format_currency
from spawns.models import Item, Player, PlayerEvent, Clan, ClanMembership
from spawns.serializers import PlayerSerializer
from spawns.wallet import WalletError, balance_map, mutate_balances
from system.models import EdeusUniques, Nexus
from users import serializers as user_serializers
from worlds.models import World, Room
from worlds.serializers import WorldSerializer


class ShutdownSerializer(serializers.Serializer):

    world = serializers.IntegerField()

    def validate_world(self, value):
        try:
            world = World.objects.get(pk=value)
        except World.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid world ID.")

        if not world.is_multiplayer:
            raise serializers.ValidationError("World is singleplayer.")

        if not world.context:
            raise serializers.ValidationError("World is not spawn world.")

        if world.lifecycle != api_consts.WORLD_STATE_STOPPING:
            raise serializers.ValidationError(
                "Can only shut down stopping worlds.")

        return world

    def create(self, validated_data):
        world = validated_data['world']
        world.set_state(api_consts.WORLD_STATE_STOPPED)
        world.cleanup()
        return world


class HousingRoomMixin:

    def validate_room(self, room_id):
        try:
            room = Room.objects.get(pk=room_id)
            if not room.housing_block:
                raise serializers.ValidationError(
                    "Room does not belong to a housing block.")
            return room
        except Room.DoesNotExist:
            raise serializers.ValidationError("Invalid room id")


class SignLeaseSerializer(serializers.Serializer, HousingRoomMixin):

    room = serializers.IntegerField()
    player = serializers.IntegerField()

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")

    def create(self, validated_data):
        room = validated_data['room']
        player = validated_data['player']

        block = room.housing_block
        block.owner = player
        block.purchase_ts = timezone.now()
        block.save()

        # Create a lease entry
        HousingLease.objects.create(
            block=block,
            owner=player,
            price=block.price)

        return {
            'owner': ref_field(player),
        }


class ToggleSerializer(serializers.Serializer, HousingRoomMixin):

    room = serializers.IntegerField()

    def create(self, validated_data):
        room = validated_data['room']
        if room.ownership_type == adv_consts.ROOM_OWNERSHIP_TYPE_PRIVATE:
            room.ownership_type = adv_consts.ROOM_OWNERSHIP_TYPE_PUBLIC
        else:
            room.ownership_type = adv_consts.ROOM_OWNERSHIP_TYPE_PRIVATE
        room.save()
        return room


class RootWorldSerializer(serializers.ModelSerializer):

    author = ReferenceField()
    author_email = serializers.SerializerMethodField()
    num_rooms = serializers.SerializerMethodField()
    num_mobs = serializers.SerializerMethodField()
    num_items = serializers.SerializerMethodField()
    num_players = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name', 'modified_ts', 'author', 'is_multiplayer',
            'author_email', 'num_rooms', 'num_players',
            'num_mobs', 'num_items'
        ]

    def get_num_rooms(self, world):
        return world.rooms.count()

    def get_author_email(self, world):
        return world.author.email if world.author else ''

    def get_num_players(self, world):
        return Player.objects.filter(
            world__context=world).count()

    def get_num_mobs(self, world):
        return world.mobs.count()

    def get_num_items(self, world):
        return world.items.count()


class PlayerEventSerializer(serializers.ModelSerializer):

    player = PlayerSerializer()
    world = WorldSerializer(source='player.world')
    root_world_id = serializers.IntegerField(
        source='player.world.context.id')

    class Meta:
        model = PlayerEvent
        fields = [
            'id',
            'player',
            'event',
            'world',
            'created_ts',
            'root_world_id',
        ]


class UserInfoSerializer(user_serializers.UserSerializer):
    """
    User Info as seem by a staff member
    """

    players_count = serializers.SerializerMethodField()
    last_login = serializers.SerializerMethodField()

    class Meta(user_serializers.UserSerializer.Meta):
        fields = list(user_serializers.UserSerializer.Meta.fields) + [
            'players_count',
            'last_login',
        ]

    def get_players_count(self, user):
        return user.characters.count()

    def last_login(self, user):
        return user.characters.all(
        ).order_by('-last_connection_ts')[0].last_connection_ts


class PlayerStaffViewSerializer(PlayerSerializer):
    "Player info with additional details viewable by staff only."

    user = user_serializers.UserSerializer()

    class Meta:
        model = PlayerSerializer.Meta.model
        fields = PlayerSerializer.Meta.fields + [
            'user',
        ]


class EdeusUniquesSerializer(serializers.ModelSerializer):

    run_ts = serializers.DateTimeField()
    warrior = PlayerSerializer()
    mage = PlayerSerializer()
    cleric = PlayerSerializer()
    assassin = PlayerSerializer()

    class Meta:
        model = EdeusUniques
        fields = [
            'run_ts',
            'warrior',
            'mage',
            'cleric',
            'assassin',
        ]


class ModerationDeserializerBase(serializers.Serializer):

    player = serializers.CharField()
    world = serializers.IntegerField() # spawn world

    def validate_world(self, value):
        try:
            return World.objects.get(pk=value)
        except World.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid world ID.")

    def validate(self, data):
        try:
            player = Player.objects.get(
                name__iexact=data['player'],
                world=data['world'])
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

        data['player'] = player
        return data


class BanDeserializer(ModerationDeserializerBase):

    def validate(self, data):
        validated_data = super().validate(data)
        player = validated_data['player']
        if player.user.noplay:
            raise serializers.ValidationError(
                "User is account banned.")
        return validated_data

    def create(self, validated_data):
        player = validated_data['player']
        if player.noplay:
            player.noplay = False
        else:
            player.noplay = True
        player.save(update_fields=['noplay'])
        return player


class MuteDeserializer(ModerationDeserializerBase):
    """
    Mute a player's user account based on the player's name in a world.
    """

    def validate(self, data):
        validated_data = super().validate(data)
        player = validated_data['player']
        if player.user.is_muted:
            raise serializers.ValidationError(
                "User is account muted.")
        return validated_data

    def create(self, validated_data):
        player = validated_data['player']
        if player.is_muted:
            player.is_muted = False
        else:
            player.is_muted = True
        player.save(update_fields=['is_muted'])
        return player


class NochatDeserializer(ModerationDeserializerBase):
    """
    Remove a user's ability to use the chat channel.
    """

    def validate(self, data):
        validated_data = super().validate(data)
        player = validated_data['player']
        if player.user.nochat:
            raise serializers.ValidationError(
                "User is account chat banned.")
        return validated_data

    def create(self, validated_data):
        player = validated_data['player']
        if player.nochat:
            player.nochat = False
        else:
            player.nochat = True
        player.save(update_fields=['nochat'])
        return player


class GlobalBanDeserializer(ModerationDeserializerBase):

    def create(self, validated_data):
        player = validated_data['player']
        user = player.user
        if user.noplay:
            user.noplay = False
        else:
            user.noplay = True
        user.save(update_fields=['noplay'])
        return player


class GlobalMuteDeserializer(ModerationDeserializerBase):
    """
    Mute a player's user account based on the player's name in a world.
    """

    def create(self, validated_data):
        player = validated_data['player']
        user = player.user
        if user.is_muted:
            user.is_muted = False
        else:
            user.is_muted = True
        user.save(update_fields=['is_muted'])
        return player


class GlobalNochatDeserializer(ModerationDeserializerBase):
    """
    Remove a user's ability to use the chat channel.
    """

    def create(self, validated_data):
        player = validated_data['player']
        user = player.user
        if user.nochat:
            user.nochat = False
        else:
            user.nochat = True
        user.save(update_fields=['nochat'])
        return player


# Clan management deserializers
# Most of the validation is done in validate so that the game code
# knows where to look for an error message if it didn't get a 201.

class ClanRegisterDeserializer(serializers.Serializer):
    """
    Register a new clan, or update a clan's name.
    """

    player = serializers.IntegerField(required=True)
    clan = serializers.CharField(required=True)

    def validate_player(self, value):
        try:
            player = Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")
        return player

    def validate(self, data):
        name = data['name'] = data['clan']
        player = data['player']
        data['clan'] = None

        config = player.world.context.config
        cost = int(config.clan_registration_cost or 0)
        currency = config.clan_registration_currency
        if cost and currency is None:
            raise serializers.ValidationError(
                "Clan registration currency is not configured.")
        if cost and cost > balance_map(player).get(currency.code, 0):
            raise serializers.ValidationError(
                f"Registering a clan costs {format_currency(cost, currency)}.")
        data['cost'] = cost
        data['currency'] = currency

        # Two register use cases:
        # * register new clan
        # * change the clan name (re-register)

        # Determine if name is taken (will be needed either way)
        is_taken = Clan.objects.filter(
            world=player.world.context,
            name__iexact=name).exists()

        # First, we look up the player's clan membership
        clan_membership = player.clan_memberships.first()

        # If the player is not in a clan, then it's a new clan
        # and we verify that the name is not taken.
        if not clan_membership:
            if is_taken:
                raise serializers.ValidationError("That name is taken.")
            return data

        data['clan'] = clan = clan_membership.clan

        # If the player is in a clan, then it's a re-register
        # which can only be done by the master.
        if clan_membership.rank != adv_consts.CLAN_RANK_MASTER:
            raise serializers.ValidationError(
                "Only the clan master can change the clan name.")

        # Re-registering master, make sure that there is a change and
        # that the name is not taken.
        if clan.name == name:
            raise serializers.ValidationError(
                "No changes detected.")
        elif is_taken:
            if clan.name.lower() != name.lower():
                raise serializers.ValidationError("That name is taken.")
        return data

    @transaction.atomic
    def create(self, validated_data):
        player = Player.objects.select_for_update().get(
            pk=validated_data['player'].pk)
        clan = validated_data['clan']
        name = validated_data['name']
        cost = validated_data['cost']
        currency = validated_data['currency']

        if cost:
            try:
                mutate_balances(
                    player,
                    {currency: -cost},
                    reason="clan.registration",
                )
            except WalletError as error:
                raise serializers.ValidationError(str(error))

        if clan:
            clan.name = name
            clan.save()
        else:
            clan = Clan.objects.create(
                world=player.world.context,
                name=name,)
            ClanMembership.objects.create(
                player=player,
                clan=clan,
                rank=adv_consts.CLAN_RANK_MASTER)

        return clan


class ClanSetPasswordDeserializer(serializers.Serializer):

    player = serializers.IntegerField(required=True)
    password = serializers.CharField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        clan_membership = player.clan_memberships.first()
        if (not clan_membership
            or clan_membership.rank != adv_consts.CLAN_RANK_MASTER):
            raise serializers.ValidationError(
                "Only the clan master can set the password.")
        data['clan'] = clan_membership.clan
        return data

    def create(self, validated_data):
        clan = validated_data['clan']
        if validated_data['password'].lower() == 'clear':
            clan.password = None
        else:
            clan.password = validated_data['password']
        clan.save()
        return clan


class ClanJoinDeserializer(serializers.Serializer):
    """
    Join a clan.
    """

    player = serializers.IntegerField(required=True)
    clan = serializers.CharField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        clan_name = data['clan'].lower()

        #password = data.get('password')

        clan_membership = player.clan_memberships.first()
        if clan_membership:
            raise serializers.ValidationError(
                "Already a member of a clan.")

        # First, try to match based on name assuming no password
        clan = Clan.objects.filter(
            world=player.world.context,
            name__iexact=clan_name,
            password__isnull=True,
        ).first()

        # See if it's a case of someone not providing a password
        # to a passworded clan
        if Clan.objects.filter(
            world=player.world.context,
            name__iexact=clan_name,
            password__isnull=False,
        ).exists():
            raise serializers.ValidationError(
                "This clan requires a password.")

        # If not successful, try with a password
        if not clan:
            clans = Clan.objects.annotate(
                join_key=Lower(Concat('name', Value(' '), 'password', output_field=CharField()))
            ).filter(join_key=clan_name, world=player.world.context)
            clan = clans.first()

        if not clan:
            raise serializers.ValidationError("Wrong clan name or password.")

        data['clan'] = clan

        # Can only join a clan within one's own core faction.
        clan_master = ClanMembership.objects.filter(
            clan=clan,
            rank=adv_consts.CLAN_RANK_MASTER).first().player
        clan_core_faction_id = clan_master.core_faction_id
        if clan_core_faction_id:
            if player.core_faction_id != clan_core_faction_id:
                raise serializers.ValidationError(
                    "You cannot join this clan.")

        return data

    def create(self, validated_data):
        player = validated_data['player']
        clan = validated_data['clan']
        ClanMembership.objects.create(
            player=player,
            clan=clan,
            rank=adv_consts.CLAN_RANK_MEMBER)
        return clan


class ClanQuitDeserializer(serializers.Serializer):
    "Leave a clan. Masters cannot leave a clan with members."

    player = serializers.IntegerField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        membership = player.clan_memberships.filter(
            clan__world=player.world.context).first()

        if not membership:
            raise serializers.ValidationError(
                "Not a member of any clan.")

        if membership.rank == adv_consts.CLAN_RANK_MASTER:
            if membership.clan.memberships.count() > 1:
                raise serializers.ValidationError(
                    "A clan master cannot leave a clan with members.")

        return data

    def create(self, validated_data):
        player = validated_data['player']
        membership = player.clan_memberships.filter(
            clan__world=player.world.context).first()
        clan = membership.clan
        membership.delete()
        if clan.memberships.count() == 0:
            clan.delete()

        return membership.clan


class ClanPromoteMemberDeserializer(serializers.Serializer):
    "Promote a clan member to master."

    player = serializers.IntegerField(required=True)
    member = serializers.CharField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        member = data['member']

        membership = player.clan_memberships.first()
        if (not membership
            or not membership.rank == adv_consts.CLAN_RANK_MASTER):
            raise serializers.ValidationError(
                "Permission denied.")

        member = Player.objects.filter(
            world=player.world,
            name__iexact=member).first()
        if member:
            member_membership = ClanMembership.objects.filter(
                clan=membership.clan,
                player=member).first()
            if not member_membership:
                member = None
        if not member:
            raise serializers.ValidationError("No such clan member.")

        data['member'] = member

        return data

    def create(self, validated_data):
        player = validated_data['player']
        member = validated_data['member']
        player_membership = player.clan_memberships.first()
        member_membership = member.clan_memberships.first()

        player_membership.rank = adv_consts.CLAN_RANK_MEMBER
        player_membership.save()

        member_membership.rank = adv_consts.CLAN_RANK_MASTER
        member_membership.save()

        return player_membership.clan


class ClanKickMemberDeserializer(serializers.Serializer):

    player = serializers.IntegerField(required=True)
    member = serializers.CharField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        member = data['member']

        membership = player.clan_memberships.first()
        if (not membership
            or not membership.rank == adv_consts.CLAN_RANK_MASTER):
            raise serializers.ValidationError(
                "Permission denied.")

        member = Player.objects.filter(
            world=player.world,
            name__iexact=member).first()
        if member:
            member_membership = ClanMembership.objects.filter(
                clan=membership.clan,
                player=member).first()
            if not member_membership:
                member = None
        if not member:
            raise serializers.ValidationError("No such clan member.")

        data['member'] = member

        return data

    def create(self, validated_data):
        member = validated_data['member']
        member_membership = member.clan_memberships.first()
        member_membership.delete()
        return member_membership.clan


class ClanMembersDeserializer(serializers.Serializer):

    player = serializers.IntegerField(required=True)

    def validate_player(self, value):
        try:
            return Player.objects.get(pk=value)
        except Player.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid player ID.")

    def validate(self, data):
        player = data['player']
        membership = player.clan_memberships.first()
        if not membership:
            raise serializers.ValidationError(
                "Not a member of any clan.")
        return data

    def create(self, validated_data):
        player = validated_data['player']
        membership = player.clan_memberships.first()
        return membership.clan.memberships.all()


class NexusSerializer(serializers.ModelSerializer):

    last_activity_ts = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Nexus
        fields = [
            'id',
            'name',
            'state',
            'last_activity_ts',
            'maintenance_mode',
        ]


class WorldStaffInfoSerializer(serializers.ModelSerializer):

    change_state_ts = serializers.SerializerMethodField()
    state = serializers.CharField(source='lifecycle')

    class Meta:
        model = World
        fields = [
            'id',
            'key',
            'name',
            'state',
            'context_id',
            'change_state_ts',
        ]

    def get_change_state_ts(self, world):
        return (
            world.change_state_ts.strftime('%Y-%m-%d %H:%M:%S')
            if world.change_state_ts
            else None)
