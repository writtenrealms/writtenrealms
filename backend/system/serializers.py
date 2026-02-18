import collections
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Value, CharField
from django.db.models.functions import Concat, Lower
from django.conf import settings
from django.utils import timezone

from rest_framework import serializers

from config import constants as adv_consts
from core import utils as adv_utils
from backend.core.drops import generation as drops_generation
from core.utils.items import price_item

from config import constants as api_consts
from builders.models import HousingLease, FactionAssignment
from core.serializers import ref_field, ReferenceField
from spawns.loading import run_loaders
from spawns.models import Item, Mob, Player, PlayerEvent, Clan, ClanMembership
from spawns.serializers import PlayerSerializer
from system.models import EdeusUniques, Nexus
from users import serializers as user_serializers
from worlds.models import World, Zone, Room
from worlds.serializers import WorldSerializer


class RunningSpawnWorldOperation(serializers.Serializer):

    world_id = serializers.IntegerField()

    def validate_world_id(self, data):
        try:
            self.world = World.objects.get(pk=data)
        except World.DoesNotExist:
            raise serializers.ValidationError("World does not exist.")

        if not self.world.context:
            raise serializers.ValidationError(
                "Must run loaders on spawn worlds.")

        if self.world.lifecycle != 'running':
            raise serializers.ValidationError(
                "World must be running")

        return data


class RunLoadersSerializer(RunningSpawnWorldOperation):

    world_id = serializers.IntegerField()
    zone_id = serializers.IntegerField(required=False)
    repopulate = serializers.BooleanField(required=False, default=False)

    def validate_zone_id(self, data):
        try:
            zone = Zone.objects.get(pk=data)
        except Zone.DoesNotExist:
            raise serializers.ValidationError("Zone does not exist.")
        self.zone = zone
        return data

    def update_world(self, world, zone_id=None, repopulate=False, rdb=None):
        # Run the loaders
        loaders_output = run_loaders(
            world=self.world,
            zone_id=zone_id,
            repopulate=repopulate,
            rdb=rdb)

        # return the mob IDs of each loaded mob
        mob_pks = []
        for run_output in loaders_output['rules']:
            if not run_output: continue
            for rule_id, spawns in run_output.items():
                for spawn in spawns:
                    if isinstance(spawn, Mob):
                        mob_pks.append(spawn.pk)
        return {
            'mob_pks': mob_pks,
            'doors': loaders_output['doors'],
        }

    def create(self, validated_data, rdb=None):
        rdb = self.world.rdb
        return self.update_world(
            validated_data['world_id'],
            zone_id=validated_data.get('zone_id'),
            repopulate=validated_data.get('repopulate', False),
            rdb=rdb)


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


class UpdateMerchantsSerializer(RunningSpawnWorldOperation):

    data = serializers.ListField()

    def create(self, validated_data):

        rdb = self.world.rdb

        merchant_data = validated_data['data']
        world = self.world
        added_items = []

        for merchant_chunk in merchant_data:
            try:
                merchant = Mob.objects.get(
                    pk=merchant_chunk['id'],
                    is_pending_deletion=False)
            except Mob.DoesNotExist:
                continue

            merch_inv_qs = merchant.template.merchant_inv.all()
            if not merch_inv_qs.exists():
                continue

            template_counts = collections.defaultdict(int)
            procedural_counts = collections.defaultdict(int)
            procedural_items = []

            # Split up items into template and procedural groups
            for item_chunk in merchant_chunk['inventory']:
                try:
                    item = Item.objects.get(pk=item_chunk['id'])
                except Item.DoesNotExist:
                    continue

                if item.template:
                    template_counts[item.template.id] += 1
                elif item.profile:
                    procedural_counts[item.profile.id] += 1
                    procedural_items.append(item)
                else:
                    continue

            # Fill or refill template inventory slots
            template_slots = merch_inv_qs.filter(
                item_template__isnull=False,
                num__gt=0)
            for template_slot in template_slots:
                count = template_counts[template_slot.item_template.id]
                for i in range(count, template_slot.num):
                    # Spawn the item
                    item = template_slot.item_template.spawn(
                        target=merchant,
                        spawn_world=self.world)
                    added_items.append(item)

            # Fill or refill procedural inventory slots
            procedural_slots = merch_inv_qs.filter(
                random_item_profile__isnull=False,
                num__gt=0)
            for procedural_slot in procedural_slots:
                # Get currently loaded item count by profile
                count = procedural_counts.get(
                    procedural_slot.random_item_profile.id, 0)
                for i in range(count, procedural_slot.num):
                    # Spawn the item
                    item = procedural_slot.random_item_profile.generate(
                        char=merchant,
                        default_level=merchant.level)
                    added_items.append(item)

            # Cycle portion of procedural inventory
            # Only do this if no players are in the room
            if merchant_chunk['player_in_room']:
                continue

            for item in procedural_items:
                THREE_HOURS = 60 * 60 * 3
                ts = timezone.now() - timedelta(seconds=THREE_HOURS)
                if item.created_ts <= ts:
                    # Create the new item
                    new_item = item.profile.generate(
                        char=merchant,
                        default_level=merchant.level)

                    added_items.append(new_item)

                    # Mark the old item for deletion and de-animate it
                    item.is_pending_deletion = True
                    item.save()

        return []



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


class UpgradeSerializer(serializers.Serializer):

    item = serializers.IntegerField()
    player = serializers.IntegerField()
    mob = serializers.IntegerField()

    def validate_item(self, item_id):
        try:
            item = Item.objects.get(pk=item_id)

            if item.template:
                raise serializers.ValidationError(
                    "Can only upgrade randomly generated items.")

            if item.quality not in (adv_consts.ITEM_QUALITY_IMBUED,
                                    adv_consts.ITEM_QUALITY_ENCHANTED):
                raise serializers.ValidationError(
                    "Can only upgrade imbued or enchanted items.")

            if (item.quality == adv_consts.ITEM_QUALITY_IMBUED
                and item.upgrade_count >= 1):
                raise serializers.ValidationError(
                    "Can only upgrade imbued items once.")

            if (item.quality == adv_consts.ITEM_QUALITY_ENCHANTED
                and item.upgrade_count >= 3):
                raise serializers.ValidationError(
                    "Can only upgrade enchanted items three times.")

        except Item.DoesNotExist:
            raise serializers.ValidationError("Invalid item id")
        return item

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")

    def validate_mob(self, mob_id):
        try:
            return Mob.objects.get(pk=mob_id)
        except Mob.DoesNotExist:
            raise serializers.ValidationError("Invalid mob id")

    def create(self, validated_data):
        item = validated_data['item']
        player = validated_data['player']
        mob = validated_data['mob']

        # First, determine whether the upgrade will succeed or fail
        is_success = adv_utils.roll_percentage(
            mob.template.upgrade_success_chance)
        if not is_success:
            item.delete()
            return {
                'outcome': 'failure',
                'item': None,
                'command': mob.template.upgrade_failure_cmd,
            }

        # Upgrade the item
        item = item.boost()

        # Update the item's cost since the upgrade count went up
        item.cost = price_item(
            level=item.level,
            quality=item.quality,
            eq_type=item.equipment_type,
            upgrade_count=item.upgrade_count)

        # Assign ownership of the item to be the player. This is necessary
        # for the situation where a player buys from a merchant and then
        # immediately upgrades the item, before extraction got a chance
        # to run.
        item.container = player
        item.save(update_fields=['container_type', 'container_id', 'cost'])

        return {
            'outcome': 'success',
            'item': None,
            'command': mob.template.upgrade_success_cmd,
        }


class CraftItemSerializer(serializers.Serializer):

    eq_type = serializers.ChoiceField(choices=adv_consts.EQUIPMENT_TYPES)
    player = serializers.IntegerField()
    mob = serializers.IntegerField()

    def validate_mob(self, mob_id):
        try:
            return Mob.objects.filter(is_pending_deletion=False).get(pk=mob_id)
        except Mob.DoesNotExist:
            raise serializers.ValidationError('Invalid mob id')

    def validate_player(self, player_id):
        try:
            return Player.objects.get(pk=player_id)
        except Player.DoesNotExist:
            raise serializers.ValidationError("Invalid player id")

    def create(self, validated_data):
        from builders.random_items import (
            generate_archetype_characteristics,
            price_item)
        player = validated_data['player']
        mob = validated_data['mob']
        eq_type = validated_data['eq_type']

        # Determine quality
        if adv_utils.roll_percentage(mob.template.craft_enchanted):
            quality = adv_consts.ITEM_QUALITY_ENCHANTED
        else:
            quality = adv_consts.ITEM_QUALITY_IMBUED

        # Determine main stat & armor class
        archetype_characteristics = generate_archetype_characteristics(
            player.archetype)
        main_stat = archetype_characteristics['main_stat']
        armor_class = archetype_characteristics['armor_class']

        if eq_type in (
            adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            adv_consts.EQUIPMENT_TYPE_WEAPON_2H):
            stats = drops_generation.generate_weapon(
                level=player.level,
                quality=quality,
                eq_type=eq_type,
                main_stat=main_stat)
        elif eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
            stats = drops_generation.generate_shield(
                level=player.level,
                quality=quality,
                main_stat=main_stat,
                armor_class=armor_class)
        elif eq_type == adv_consts.EQUIPMENT_TYPE_ACCESSORY:
            raise serializers.ValidationError("Cannot craft accessories.")
        else:
            stats = drops_generation.generate_armor(
                level=player.level,
                quality=quality,
                eq_type=eq_type,
                main_stat=main_stat,
                armor_class=armor_class)

        stats['cost'] = price_item(
            level=player.level,
            quality=quality,
            eq_type=eq_type)

        item = Item.objects.create(
            world=player.world,
            quality=quality,
            level=player.level,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            container=player,
            **stats)

        return item


class RootWorldSerializer(serializers.ModelSerializer):

    author = ReferenceField()
    author_email = serializers.CharField(source='author.email')
    num_rooms = serializers.SerializerMethodField()
    num_mobs = serializers.SerializerMethodField()
    num_items = serializers.SerializerMethodField()
    num_players = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = [
            'id', 'name', 'author', 'is_multiplayer',
            'author_email', 'num_rooms', 'num_players',
            'num_mobs', 'num_items'
        ]

    def get_num_rooms(self, world):
        return world.rooms.count()

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

        # Make sure that the player has enough gold
        cost = player.world.context.config.clan_registration_cost
        if cost > player.gold:
            raise serializers.ValidationError(
                "Registering a clan costs %s gold." % cost)
        data['cost'] = cost

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

    def create(self, validated_data):
        player = validated_data['player']
        clan = validated_data['clan']
        name = validated_data['name']

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

        player.gold -= player.world.context.config.clan_registration_cost
        player.save(update_fields=['gold'])
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
        clan_core_faction_a = FactionAssignment.objects.filter(
            faction__world=player.world.context,
            faction__is_core=True,
            member_id=clan_master.id,
            member_type=ContentType.objects.get_for_model(clan_master)
        ).first()
        if clan_core_faction_a:
            player_core_faction_a = FactionAssignment.objects.filter(
                faction__world=player.world.context,
                faction__is_core=True,
                member_id=player.id,
                member_type=ContentType.objects.get_for_model(clan_master)
            ).first()
            if (not player_core_faction_a or
                player_core_faction_a.faction != clan_core_faction_a.faction):
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
