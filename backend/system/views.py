from datetime import timedelta
import json

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Max, Min
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import (
    generics,
    status,
    serializers,
    viewsets)
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from config import constants as adv_consts
from core.utils import expiration_ts

from config import constants as api_consts
from builders.models import (
    Reward,
    FactionRank,
    FactionAssignment,
    WorldReview)
from builders import serializers as builder_serializers
from builders.serializers import MapRoomSerializer
from core.permissions import IsSystemUser, IsStaffUser
from core.db import qs_by_pks
from spawns import (
  serializers as spawn_serializers,
  tasks as spawn_tasks)
from spawns.models import Player, PlayerData, Item, PlayerEvent
from spawns.serializers import PlayerSerializer
from spawns.services import WorldGate
from system import serializers as system_serializers
from system import tasks as system_tasks
from system.models import Nexus
from system.services import get_staff_panel
from users import (serializers as user_serializers, models as user_models)
from users.models import User
from worlds.models import World, Room, RoomFlag


class SystemView(APIView):
    permission_classes = (
        IsAuthenticated,
        IsSystemUser,
    )


class RunLoaders(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.RunLoadersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.save()
        return Response(data, status=status.HTTP_201_CREATED)


class GenerateDrops(SystemView):

    def post(self, request, format=None):
        serializer = spawn_serializers.GenerateDropSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()

        return Response([], status=status.HTTP_201_CREATED)


class UpdateMerchants(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.UpdateMerchantsSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        animation_data = serializer.save()
        return Response({
            'animation_data': animation_data,
        }, status=status.HTTP_201_CREATED)


class SpawnRewards(SystemView):

    def post(self, request, reward_pk, format=None):
        try:
            reward = Reward.objects.get(pk=reward_pk)
        except Reward.DoesNotExist:
            raise serializers.ValidationError("Reward does not exist.")

        serializer = spawn_serializers.SpawnRewardsSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)

        animation_data = []
        for i in range(0, reward.qty):

            if reward.profile_type.model == 'itemtemplate':
                item = reward.profile.spawn(
                    target=serializer.player,
                    spawn_world=serializer.player.world)
            elif reward.profile_type.model == 'randomitemprofile':
                print('spawning reward for archetype')
                item = reward.profile.generate(
                    char=serializer.player,
                    default_level=reward.quest.mob_template.level,
                    for_archetype=True)
                print('spawned %s' % item.name)
            else:
                continue

        return Response({
            'animation_data': [],
        }, status=status.HTTP_201_CREATED)


class LabelItem(SystemView):
    """
    Note on labels. This is just a quick and dirty view for the game engine
    to edit a label on an item. But it could be done differently, as a
    generic editing of an item endpoint, which would be authenticated by
    generating a player specific JWT token which DRT would then be
    looked up as a permission.
    """

    def post(self, request, item_pk, format=None):
        try:
            item = Item.objects.get(pk=item_pk)
        except Item.DoesNotExist:
            raise serializers.ValidationError("Item does not exist.")

        item.label = request.data['label']
        item.save()

        return Response({})

label_item = LabelItem.as_view()


class LoadTemplate(SystemView):

    def post(self, request, format=None):
        serializer = spawn_serializers.LoadTemplateSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)

        vd = serializer.validated_data
        template = vd['template']
        actor = vd['actor']

        # Update the actor's room if it's out of sync, given that we're
        # loading based on the last saved room.
        if not isinstance(actor, Room) and actor.room != vd['room']:
            actor.room = vd['room']
            actor.save()

        if vd['template_type'] == 'item':
            item = template.spawn(actor, vd['spawn_world'])

            data = spawn_serializers.AnimateItemSerializer(item).data
            if vd.get('cmd'):
                data['cmd'] = vd['cmd']
            return Response(data, status=status.HTTP_201_CREATED)

        elif vd['template_type'] == 'mob':
            if isinstance(vd['actor'], Room):
                try:
                    room = vd['room']
                except KeyError:
                    room = vd['actor']
            else:
                room = actor.room
            mob = template.spawn(room, vd['spawn_world'])

            data = spawn_serializers.AnimateMobSerializer(mob).data
            if vd.get('cmd'):
                data['cmd'] = vd['cmd']
            return Response(data, status=status.HTTP_201_CREATED)

        return Response({})


# class Extract(SystemView):
#     "Endpoint invoked by the Save command"

#     def post(self, request, format=None):
#         serializer = spawn_serializers.GameExtractionSerializer(
#             data=request.data)
#         serializer.is_valid(raise_exception=True)
#         serializer.save()
#         return Response({}, status=status.HTTP_201_CREATED)


class Complete(SystemView):
    """
    Attempt to complete a single player world. If it cannot be completed,
    will return a 400
    """

    def post(self, request, format=None):
        serializer = spawn_serializers.WorldCompletionSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.validated_data['player']

        spawn_world = player.world
        WorldGate(player=player, world=spawn_world).exit()
        spawn_world.lifecycle = api_consts.WORLD_STATE_COMPLETE
        spawn_world.save(update_fields=['lifecycle'])

        return Response({}, status=status.HTTP_201_CREATED)


class EnquireQuest(SystemView):

    def post(self, request, format=None):
        serializer = spawn_serializers.QuestEnquireSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        player_enquire = serializer.save()
        return Response({
            'player_quest_key': player_enquire.quest.key,
        }, status=status.HTTP_201_CREATED)


class CompleteQuest(SystemView):

    def post(self, request, format=None):
        serializer = spawn_serializers.QuestCompletionSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        player_quest = serializer.save()
        return Response({
            'player_quest_key': player_quest.key,
        }, status=status.HTTP_201_CREATED)


class Quit(SystemView):
    """
    Game engine notifying the API that a user has quit. This could be from
    a single or multi player world, and it could be via the actual quit cmd,
    or via a force quit.
    """

    def post(self, request, format=None):

        try:
            player = Player.objects.get(pk=request.data['player'])
        except (KeyError, Player.DoesNotExist):
            raise serializers.ValidationError("Player does not exist.")

        try:
            world = World.objects.get(pk=request.data['world'],
                                      context__isnull=False)
        except (KeyError, World.DoesNotExist):
            raise serializers.ValidationError("World does not exist.")

        extraction_data = request.data.get('data')
        if extraction_data:
            player_data = PlayerData.objects.create(
                player=player,
                data=json.dumps(extraction_data))
        else:
            player_data = None

        spawn_tasks.exit_world.delay(
            player_id=player.id,
            world_id=world.id,
            player_data_id=player_data.id if player_data else None)

        return Response({'world_id': world.context.id},
                        status=status.HTTP_201_CREATED)


class Reset(SystemView):

    def post(self, request, pk, format=None):
        player = generics.get_object_or_404(Player.objects.all(), pk=pk)
        try:
            level = int(request.data.get('level', 1))
        except (TypeError, ValueError):
            level = 1

        player.reset(level=level)
        return Response({
            'player_key': player.key,
        }, status=status.HTTP_201_CREATED)


class ShutdownWorld(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ShutdownSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({}, status=status.HTTP_201_CREATED)


class Whois(SystemView):

    def get(self, request, world_id, name, format=None):
        world = get_object_or_404(World.objects.all(), pk=world_id)

        context = world.context
        if context.instance_of:
            context = context.instance_of

        player = Player.objects.filter(
            world__context=context,
            name__iexact=name).first()
        if not player:
            running_instance_ids = World.objects.filter(
                context__instance_of=context,
                lifecycle=api_consts.WORLD_STATE_RUNNING
            ).values_list('id', flat=True)
            player = Player.objects.filter(
                world__in=running_instance_ids,
                name__iexact=name).first()

        if not player:
            raise serializers.ValidationError("No-one by that name.")

        player_data = PlayerSerializer(player).data

        player_data['email'] = player.user.email
        player_data['is_confirmed'] = player.user.is_confirmed
        player_data['username'] = player.user.username

        # Factions data
        factions_data = []
        ranks_qs = FactionRank.objects.filter(
            faction__world=context)
        for faction_assignment in FactionAssignment.objects.filter(
            member_type__model='player',
            member_id=player.id,
            value__gt=0,
            faction__is_core=False):

            rank = ranks_qs.filter(
                faction=faction_assignment.faction,
                standing__lte=faction_assignment.value
            ).order_by('-standing').first()

            if not rank: continue

            factions_data.append({
                'id': faction_assignment.faction.id,
                'name': faction_assignment.faction.name,
                'standing': faction_assignment.value,
                'rank': rank.name,
            })

        # First sort by standing
        factions_data = list(
            reversed(
                sorted(
                    factions_data, key=lambda d: d['standing'])))
        # Then remove the standing
        for d in factions_data:
            del d['standing']

        player_data['factions'] = factions_data

        if context.instance_of:
            alts = Player.objects.filter(
                user=player.user,
                world__context=context,
            ).exclude(pk=player.id)
        else:
            alts = player.user.characters.filter(
                world=world,
            ).exclude(pk=player.id)
        player_data['alts'] = [
            {
                'id': alt.id,
                'name': alt.name,
            }
            for alt in alts
        ]

        return Response({'player': player_data})


class Ban(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.BanDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'banned': player.noplay,
            'name': player.name,
            'player_id': player.id,
        }, status=status.HTTP_201_CREATED)


class Mute(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.MuteDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'muted': player.is_muted,
            'name': player.name,
            'player_id': player.id,
        }, status=status.HTTP_201_CREATED)


class Nochat(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.NochatDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'nochat': player.nochat,
            'name': player.name,
            'player_id': player.id,
        }, status=status.HTTP_201_CREATED)


class GlobalBan(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.GlobalBanDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'banned': player.user.noplay,
            'name': player.name,
            'user_id': player.user.id,
        }, status=status.HTTP_201_CREATED)


class GlobalMute(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.GlobalMuteDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'muted': player.user.is_muted,
            'name': player.name,
            'user_id': player.user.id,
        }, status=status.HTTP_201_CREATED)


class GlobalNochat(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.GlobalNochatDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'nochat': player.user.nochat,
            'name': player.name,
            'user_id': player.user.id,
        }, status=status.HTTP_201_CREATED)


class SignLease(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.SignLeaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return_data = serializer.save()
        return Response(return_data)

lease_sign = SignLease.as_view()


class UpgradeItem(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.UpgradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return_data = serializer.save()
        return Response(return_data, status=status.HTTP_201_CREATED)

upgrade_item = UpgradeItem.as_view()


class CraftItem(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.CraftItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        return Response({'item_key': item.key}, status=status.HTTP_201_CREATED)

craft_item = CraftItem.as_view()


class ToggleRoom(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        room = serializer.save()
        return Response(room.ownership_type, status=status.HTTP_201_CREATED)


toggle_room = ToggleRoom.as_view()


# Clans


class RegisterClan(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanRegisterDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        player = serializer.validated_data['player']
        return Response({
            'clan': player.clan,
            'cost': serializer.validated_data['cost'],
        }, status=status.HTTP_201_CREATED)

register_clan = RegisterClan.as_view()


class ClanSetPassword(SystemView):

        def post(self, request, format=None):
            serializer = system_serializers.ClanSetPasswordDeserializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            clan = serializer.save()
            return Response(clan.name, status=status.HTTP_201_CREATED)

clan_set_password = ClanSetPassword.as_view()


class JoinClan(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanJoinDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        player = serializer.validated_data['player']
        return Response({'clan': player.clan}, status=status.HTTP_201_CREATED)

join_clan = JoinClan.as_view()


class QuitClan(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanQuitDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clan = serializer.save()
        return Response(clan.name, status=status.HTTP_201_CREATED)

quit_clan = QuitClan.as_view()


class ClanPromoteMember(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanPromoteMemberDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        player = serializer.validated_data['player']
        member = serializer.validated_data['member']
        return Response({
            'player': {
                'key': player.key,
                'clan': player.clan,
            },
            'member': {
                'key': member.key,
                'clan': member.clan,
            },
        }, status=status.HTTP_201_CREATED)

clan_promote = ClanPromoteMember.as_view()


class ClanKickMember(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanKickMemberDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        member = serializer.validated_data['member']
        return Response({
            'member': {
                'key': member.key,
                'name': member.name
            }
        }, status=status.HTTP_201_CREATED)

clan_kick = ClanKickMember.as_view()


class ClanMembers(SystemView):

    def post(self, request, format=None):
        serializer = system_serializers.ClanMembersDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        memberships = serializer.save()
        master = memberships.filter(rank=adv_consts.CLAN_RANK_MASTER).first()
        members = [{'name': master.player.name, 'rank': master.rank}]
        non_master_members = memberships.exclude(
            rank=adv_consts.CLAN_RANK_MASTER
        ).order_by('player__name')
        for member in non_master_members:
            members.append({
                'name': member.player.name,
                'rank': member.rank,
            })
        return Response({
            'members': members,
        }, status=status.HTTP_201_CREATED)

clan_members = ClanMembers.as_view()


class EnterInstance(SystemView):
    """
    Prepares player for entering an instance. Actual entrance will be
    normal /game/enter call.
    """

    def post(self, request, format=None):

        try:
            player = Player.objects.get(pk=request.data['player'])
        except (KeyError, Player.DoesNotExist):
            raise serializers.ValidationError("Player does not exist.")

        if player.player_instances.count() >= 2:
            raise serializers.ValidationError("Too many instance assignments.")

        # Validate the reference if there is one, which could either be the
        # instance reference or the leader ID.
        ref = None
        if request.data.get('ref'):
            ref = request.data['ref']
            try:
                instance  = World.objects.get(
                    instance_ref=ref,
                    context__instance_of=player.world.context,
                )
            except World.DoesNotExist:
                raise serializers.ValidationError(
                    "Invalid reference.")

        try:
            transfer_to = Room.objects.get(pk=request.data['transfer_to'])
        except (KeyError, Room.DoesNotExist):
            raise serializers.ValidationError("Room does not exist.")

        try:
            transfer_from = Room.objects.get(pk=request.data['transfer_from'])
        except (KeyError, Room.DoesNotExist):
            raise serializers.ValidationError("Room does not exist.")

        try:
            extraction_data = request.data['data']
        except KeyError:
            raise serializers.ValidationError("No extraction data.")

        instance_context = transfer_to.world

        # Sanity checks
        if instance_context.instance_of != player.world.context:
            raise serializers.ValidationError("Invalid instance configuration.")

        player_data = PlayerData.objects.create(
            player=player,
            data=json.dumps(extraction_data))

        member_ids = request.data.get('member_ids', [])

        spawn_tasks.exit_world.delay(
            player_id=player.id,
            world_id=player.world.id,
            player_data_id=player_data.id,
            transfer_to=transfer_to.id,
            transfer_from=transfer_from.id,
            ref=ref,
            member_ids=member_ids)

        return Response({
            'banner_url': instance_context.config.large_background,
            'card_url': instance_context.config.small_background,
            'transfer_to_world_id': instance_context.id,
            'world_name': instance_context.name,
            'room_id': transfer_to.id,
            'ref': ref,
        }, status=status.HTTP_201_CREATED)

enter_instance = EnterInstance.as_view()


class LeaveInstance(SystemView):

    def post(self, request, format=None):

        try:
            player = Player.objects.get(pk=request.data['player'])
        except (KeyError, Player.DoesNotExist):
            raise serializers.ValidationError("Player does not exist.")

        # Make sure the player is actually in an instance
        if not player.world.context.instance_of:
            raise serializers.ValidationError("Player is not in an instance.")

        try:
            extraction_data = request.data['data']
        except KeyError:
            raise serializers.ValidationError("No extraction data.")

        player_data = PlayerData.objects.create(
            player=player,
            data=json.dumps(extraction_data))

        spawn_tasks.exit_world.delay(
            player_id=player.id,
            world_id=player.world.id,
            player_data_id=player_data.id,
            leave_instance=True)

        original_world = player.world.context.instance_of

        return Response({
            'banner_url': original_world.config.large_background,
            'world_name': original_world.name,
            'transfer_to_world_id': original_world.id,
        }, status=status.HTTP_201_CREATED)


leave_instance = LeaveInstance.as_view()

# ==== Staff Views ====


class StaffViewMixin:
    permission_classes = (
        IsAuthenticated,
        IsStaffUser,
    )


class RootWorlds(ListAPIView):

    queryset = World.objects.filter(context__isnull=True)
    serializer_class = system_serializers.RootWorldSerializer

    # def get(self, request, format=None):
    #     qs = World.objects.filter(context__isnull=True)
    #     page = self.paginate_queryset(qs)
    #     serializer = system_serializers.RootWorldSerializer(
    #         page,
    #         many=True)
    #     return self.get_paginated_response(serializer.data)
    #     return Response(serializer.data)


class PlayerEvents(generics.ListAPIView):

    serializer_class = system_serializers.PlayerEventSerializer
    queryset = PlayerEvent.objects.order_by('-created_ts')


class Playing(ListAPIView):

    serializer_class = system_serializers.PlayerStaffViewSerializer

    def get_queryset(self):

        qs = Player.objects.filter(
            in_game=True,
            user__is_temporary=False,
            world__lifecycle=api_consts.WORLD_STATE_RUNNING)

        return qs


class SignUps(ListAPIView):

    queryset = user_models.User.objects.filter(is_temporary=False).order_by(
        '-date_joined')
    serializer_class = user_serializers.UserSerializer


class Activity(ListAPIView):

    serializer_class = user_serializers.UserSerializer

    def get_queryset(self):
        cutoff = timezone.now() - timedelta(days=2)
        qs = Player.objects.filter(
            last_connection_ts__gte=cutoff,
            user__is_temporary=False,
        ).order_by('-last_connection_ts')

        user_ids = set()
        player_ids = set()
        for player in qs:
            if player.user_id and player.user_id not in user_ids:
                user_ids.add(player.user_id)
                player_ids.add(player.id)

        user_pks = Player.objects.filter(
            pk__in=player_ids,
        ).order_by('-last_connection_ts').values_list('user_id', flat=True).distinct()

        return qs_by_pks(user_models.User, user_pks)

        return Player.objects.filter(
            pk__in=player_ids,
        ).order_by('-last_connection_ts')


class UserInfo(APIView):

    def get(self, request, user_pk, format=None):
        data = {}
        user = generics.get_object_or_404(
            user_models.User.objects.all(), pk=user_pk)

        # Basic user info
        #data.update(user_serializers.UserSerializer(user).data)
        data.update(system_serializers.UserInfoSerializer(user).data)

        # Events
        events = system_serializers.PlayerEventSerializer(
            PlayerEvent.objects.filter(
                player__user=user
            ).order_by('-created_ts')[0:20],
            many=True).data
        data.update({'events': events})

        # Worlds
        worlds = system_serializers.RootWorldSerializer(
            user.worlds.order_by('-created_ts'), many=True).data
        data.update({'worlds': worlds})

        # Players
        players = spawn_serializers.PlayerSerializer(
            user.characters.order_by('-last_connection_ts'), many=True).data
        data.update({'players': players})

        return Response(data)


class StaffStats(APIView):

    def get(self, request, format=None):
        data = {}
        return Response(data)


class Reviews(ListAPIView):

    serializer_class = builder_serializers.WorldReviewSerializer
    # queryset = WorldReview.objects.filter(
    #     status=api_consts.WORLD_REVIEW_STATUS_SUBMITTED)

    def get_queryset(self):
        assigned = self.request.query_params.get('assigned', None)

        qs = WorldReview.objects.filter(
            status=api_consts.WORLD_REVIEW_STATUS_SUBMITTED)

        if assigned == 'self':
            qs = qs.filter(reviewer=self.request.user)
        elif assigned == 'false':
            qs = qs.filter(reviewer__isnull=True)

        return qs


class ReviewViewSet(viewsets.ModelViewSet):

    serializer_class = builder_serializers.WorldReviewSerializer
    queryset = WorldReview.objects.all()



staff_review_detail = ReviewViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
})


class StaffSearch(APIView):

    def get(self, request):
        query = request.query_params.get('q')

        if not query:
            return Response({'users': [], 'players': []})

        user_ids = set()
        player_ids = set()

        # Try players
        try:
            user_id = int(query)
            if User.objects.filter(pk=user_id).exists():
                user_ids.add(user_id)
        except (ValueError, TypeError):
            name_matches = User.objects.filter(username__icontains=query)
            if name_matches:
                user_ids.update(
                    name_matches.values_list('id', flat=True)[0:20])

            email_matches = User.objects.filter(email__icontains=query)
            if email_matches:
                user_ids.update(
                    email_matches.values_list('id', flat=True)[0:20])

        # Get players
        try:
            player_id = int(query)
            if Player.objects.filter(pk=player_id).exists():
                player_ids.add(player_id)
        except (ValueError, TypeError):
            player_matches = Player.objects.filter(name__icontains=query)
            if player_matches:
                player_ids.update(
                    player_matches.values_list('id', flat=True)[0:20])

        users = User.objects.filter(id__in=user_ids).order_by('-date_joined')

        players = Player.objects.filter(
            id__in=player_ids,
        ).order_by('-last_connection_ts')

        return Response({
            'users': system_serializers.UserInfoSerializer(
                users, many=True).data,
            'players': spawn_serializers.PlayerSerializer(
                players, many=True).data,
        })

staff_search = StaffSearch.as_view()

class StaffPanel(APIView, StaffViewMixin):

    def get(self, request, format=None):
        panel_data = get_staff_panel()
        return Response(panel_data)

staff_panel = StaffPanel.as_view()

class StaffInit(APIView, StaffViewMixin):

    def post(self, request, format=None):
        system_tasks.initialize.delay()
        return Response({}, status=status.HTTP_201_CREATED)

staff_init = StaffInit.as_view()

class StaffTeardown(APIView, StaffViewMixin):

    def post(self, request, format=None):
        system_tasks.teardown.delay()
        return Response({}, status=status.HTTP_201_CREATED)

staff_teardown = StaffTeardown.as_view()


class StaffInvalidateUserEmail(APIView, StaffViewMixin):

    def post(self, request, user_pk=None, format=None):
        user = get_object_or_404(User, pk=user_pk)
        user.is_invalid = True
        user.save()
        return Response(system_serializers.UserInfoSerializer(user).data,
                        status=status.HTTP_201_CREATED)

invalidate_email = StaffInvalidateUserEmail.as_view()


class NexusViewSet(viewsets.ModelViewSet, StaffViewMixin):

    queryset = Nexus.objects.all()
    serializer_class = system_serializers.NexusSerializer

nexus_details = NexusViewSet.as_view({
    'get': 'retrieve',
    'patch': 'partial_update',
})


class NexusData(APIView, StaffViewMixin):

    def get(self, request, pk=None, format=None):
        nexus = get_object_or_404(Nexus, pk=pk)

        if nexus.name == 'nexus-sandbox':
            worlds = []
        else:
            worlds = [
                system_serializers.WorldStaffInfoSerializer(world).data
                for world in nexus.worlds.filter(
                    is_multiplayer=True
                ).order_by('-change_state_ts')
            ]

        if nexus.state == api_consts.NEXUS_STATE_READY:
            rdb = nexus.rdb
            timings = rdb.redis.zrange('timings', 0, -1, withscores=True)
            dbsize = rdb.redis.dbsize()
        else:
            timings = []
            dbsize = 0

        return Response({
            'dbsize': dbsize,
            'timings': timings,
            'now': expiration_ts(0),
            'worlds': worlds,
        })


# ==== Public Views ====

def get_archetype_skills(archetype):
    # WR2 does not currently expose archetype command metadata here.
    return {
        'core': [],
        'flex': [],
    }

class ArchetypeSkills(APIView):

    permission_classes = ()

    def get(self, request, archetype, format=None):
        skills = get_archetype_skills(archetype)
        return Response(skills)


class AllSkills(APIView):
    permission_classes = ()

    def get(self, request, format=None):
        return Response({
            archetype: get_archetype_skills(archetype)
            for archetype in adv_consts.ARCHETYPES
            if archetype
        })
