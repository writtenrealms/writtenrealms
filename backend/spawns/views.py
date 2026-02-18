from datetime import timedelta
import logging
import uuid

from config import constants as adv_consts

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework import (
    status,
    serializers,
    viewsets)
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from users.tokens import build_token_response

from backend.config.exceptions import ServiceError
from builders.models import Quest
from core import throttles as api_throttles
from core.permissions import IsPlayerInGame
from core.db import qs_by_pks
from core.ip import get_ip
from core.view_mixins import RequestDataMixin
from spawns import serializers as spawn_serializers, tasks as spawn_tasks
from spawns.loading import run_loaders
from spawns.models import (
    Player, PlayerEvent, PlayerEnquire, PlayerQuest, PlayerConfig)
from system.models import IntroConfig, SiteControl, IPBan
from users.models import User
from users.serializers import UserSerializer
from worlds.serializers import WorldSerializer


security_logger = logging.getLogger('security')


class EnterGame(APIView):

    throttle_classes = (api_throttles.PlayGameThrottle,)

    def post(self, request, format=None):

        serializer = spawn_serializers.EnterGameSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)

        player = serializer.validated_data['player']
        spawn_world = player.world

        spawn_tasks.enter_world.delay(player_id=player.id,
                                      world_id=spawn_world.id)

        return Response(
            {
                'world': WorldSerializer(player.world).data,
                'player_config': spawn_serializers.PlayerConfigSerializer(
                    player.config
                ).data,
            },
            status=201)


class PlayGame(APIView):
    "One-click play from the front page feature"

    authentication_classes = ()
    permission_classes = ()

    throttle_classes = (api_throttles.PlayGameThrottle,)

    def post(self, request, format=None):

        try:
            site_control = SiteControl.objects.get(name='prod')
            if site_control.maintenance_mode:
                raise serializers.ValidationError(
                    "Unable to enter world: Written Realms is undergoing "
                    "maintenance. Please try again later.")
        except SiteControl.DoesNotExist:
            pass

        # Create the intro world for this player
        intro_world = IntroConfig.objects.get().world

        # Don't allow players to enter the intro world if it's in maintenance
        if intro_world.maintenance_mode:
            if intro_world.maintenance_msg:
                raise serializers.ValidationError(intro_world.maintenance_msg)
            raise serializers.ValidationError("World is temporarily closed.")

        spawn_world = intro_world.create_spawn_world()

        # Fetch the IP
        ip = get_ip(request)
        if IPBan.objects.filter(ip=ip).exists():
            security_logger.info("Play attempt from banned IP %s" % ip)
            raise ServiceError("Your IP address has been banned.")
        security_logger.info("New Play action from IP %s" % ip)

        # Create the temporary user
        uid = uuid.uuid4()
        User = get_user_model()
        user = User.objects.create(email='%s@writtenrealms.com' % uid,
                                   is_temporary=True,
                                   ip=ip)
        self.world = intro_world
        serializer = spawn_serializers.PlayerSerializer(data={
            'name': 'An adventurer',
            'gender': adv_consts.GENDER_MALE,
        }, context={'view': self})
        serializer.is_valid(raise_exception=True)
        serializer.save(user=user, world=spawn_world)

        run_loaders(world=spawn_world, initial=True)

        # Issue auth tokens for this temporary user
        token_data = build_token_response(user)

        return Response({
            'player': serializer.data,
            **token_data,
            'user': UserSerializer(user).data,
            'world_id': spawn_world.id,
        }, status=status.HTTP_201_CREATED)


class GameView(APIView):
    "Game views for endpoints that return in-game information"

    permission_classes = (IsAuthenticated, IsPlayerInGame)


class Lookup(GameView):
    "Handler that looks up an item or a mob in a spawned world"

    def get(self, request, key, format=None):
        # request.player is set by the permission
        lookup_data = request.player.game_lookup(key)
        return Response(lookup_data)


class QuestLogView(GameView):

    def get_player(self, request):
        # Fetch player based on header
        player_id = request.META.get('HTTP_X_PLAYER_ID', None)
        player = Player.objects.get(pk=player_id)
        return player

    def get_enquired_quests(self, request):
        pass

    def get_quest_data(self, player, quest_ids):
        quests = qs_by_pks(Quest, quest_ids)

        data = spawn_serializers.EnquiredQuestSerializer(
            quests, many=True,
            context={
                'actor': player,
            }).data

        return data


class EnquiredQuests(GameView):
    "Deprecated. Keeping around for reference."

    def get(self, request, format=None):

        # Fetch player based on header
        player_id = request.META.get('HTTP_X_PLAYER_ID', None)
        player = Player.objects.get(pk=player_id)

        completed_quests_ids = set(player.player_quests.filter(
            completion_ts__isnull=False,
            quest__repeatable_after=-1,
        ).values_list('quest_id', flat=True))

        # Map to keep track of when each player has interacted with a quest,
        # either based on the created ts of the PlayerQuest record of the
        # enquired quest, or on the created ts of the completed quest in the
        # case of delivery quests.
        quest_interaction_map = {}

        # All the quests for which the 'enquire' command was run
        enquired_quests = player.player_enquires.exclude(
            quest_id__in=completed_quests_ids
        ).order_by('-created_ts')
        enquired_quest_ids = enquired_quests.values_list(
            'quest_id', flat=True)
        quest_interaction_map.update(
            dict(enquired_quests.values_list('quest_id', 'created_ts')))

        # Get all of the deliver quests that the player has completed

        # Get all of the quests that were completed with a deliver type,
        # meaning they could be the effective enquire directive for a yet
        # uncompleted second quest.
        potential_deliveries = PlayerQuest.objects.filter(
            player=player,
            #quest__type=adv_consts.QUEST_TYPE_DELIVER,
            quest__is_setup=True,
            completion_ts__isnull=False)
        potential_delivery_quest_ids = potential_deliveries.values_list(
            'quest_id', flat=True)
        quest_interaction_map.update(
            dict(potential_deliveries.values_list('quest_id', 'created_ts')))

        # Fetch all the quests that were the next step of a completed
        # initial delivery quest and were themselves completed.
        completed_deliveries = PlayerQuest.objects.filter(
            player=player,
            quest__requires_quest__id__in=potential_delivery_quest_ids,
            completion_ts__isnull=False
        )
        completed_delivery_quest_ids = completed_deliveries.values_list(
           'quest__requires_quest__id', flat=True)

        quest_ids = set(potential_delivery_quest_ids)
        quest_ids = quest_ids.difference(set(completed_delivery_quest_ids))
        quest_ids = quest_ids.union(enquired_quest_ids)

        qs = Quest.objects.filter(id__in=quest_ids)

        quests = reversed(
            sorted(
                qs,
                key=lambda q: quest_interaction_map[q.id]))

        data = spawn_serializers.EnquiredQuestSerializer(
            quests, many=True,
            context={
                'actor': player,
            }).data

        return Response(data)


class RepeatableQuests(QuestLogView):
    "Completed and repeatable but not setup."

    def get(self, request, format=None):
        player = self.get_player(request)

        completed_repeatable_quests_ids = player.player_quests.filter(
                completion_ts__isnull=False,
                quest__is_logged=True,
            ).exclude(
                quest__repeatable_after=-1,
            ).exclude(
                quest__is_setup=True,
            ).order_by('-created_ts').values_list('quest_id', flat=True)

        return Response(self.get_quest_data(
            player=player, quest_ids=completed_repeatable_quests_ids))


class OpenQuests(QuestLogView):

    def get(self, request, format=None):

        player = self.get_player(request)

        # Non-repeatable completed quests
        completed_quests_ids = set(player.player_quests.filter(
            completion_ts__isnull=False,
            quest__repeatable_after=-1,
        ).values_list('quest_id', flat=True))

        # Map to keep track of when each player has interacted with a quest,
        # either based on the created ts of the PlayerQuest record of the
        # enquired quest, or on the created ts of the completed quest in the
        # case of delivery quests.
        quest_interaction_map = {}

        # All the quests for which the 'enquire' command was run
        enquired_quests = player.player_enquires.exclude(
            quest_id__in=completed_quests_ids
        ).order_by('-created_ts')
        enquired_quest_ids = enquired_quests.values_list(
            'quest_id', flat=True)
        quest_interaction_map.update(
            dict(enquired_quests.values_list('quest_id', 'created_ts')))

        # Get all of the deliver quests that the player has completed

        # Get all of the quests that were completed with a deliver type,
        # meaning they could be the effective enquire directive for a yet
        # uncompleted second quest.
        potential_deliveries = PlayerQuest.objects.filter(
            player=player,
            quest__is_setup=True,
            completion_ts__isnull=False)
        potential_delivery_quest_ids = potential_deliveries.values_list(
            'quest_id', flat=True)
        quest_interaction_map.update(
            dict(potential_deliveries.values_list('quest_id', 'created_ts')))

        # Fetch all the quests that were the next step of a completed
        # initial delivery quest and were themselves completed.
        completed_deliveries = PlayerQuest.objects.filter(
            player=player,
            quest__requires_quest__id__in=potential_delivery_quest_ids,
            completion_ts__isnull=False
        )
        completed_delivery_quest_ids = completed_deliveries.values_list(
           'quest__requires_quest__id', flat=True)

        # Fetch all completed repeatable non setup quests to exclude them
        completed_repeatable_quests_ids = player.player_quests.filter(
                completion_ts__isnull=False,
                quest__is_setup=False,
            ).exclude(
                quest__repeatable_after=-1
            ).order_by('-created_ts').values_list('quest_id', flat=True)

        quest_ids = set(potential_delivery_quest_ids)
        quest_ids = quest_ids.difference(set(completed_delivery_quest_ids))
        quest_ids = quest_ids.union(enquired_quest_ids)
        quest_ids = quest_ids.difference(set(completed_repeatable_quests_ids))

        qs = Quest.objects.filter(id__in=quest_ids, is_logged=True)

        quests = reversed(
            sorted(
                qs,
                key=lambda q: quest_interaction_map[q.id]))

        return Response(self.get_quest_data(
            player=player, quest_ids=[q.id for q in quests]))


class CompletedQuests(QuestLogView):
    "Completed and not repeatable"

    def get(self, request, format=None):
        player = self.get_player(request)

        completed_non_repeatable_quests_ids = player.player_quests.filter(
                completion_ts__isnull=False,
                quest__repeatable_after=-1,
                quest__is_logged=True,
            ).order_by('-created_ts').values_list('quest_id', flat=True)

        return Response(self.get_quest_data(
            player=player, quest_ids=completed_non_repeatable_quests_ids))


class PlayerConfigView(GameView):

    def post(self, request, format=None):
        player = request.player
        config = player.config

        # If the player is referencing the first config, create a new one.
        if config.id == 1:
            config = PlayerConfig.objects.create()
            player.config = config
            player.save(update_fields=['config'])

        serializer = spawn_serializers.PlayerConfigSerializer(
            data=request.data,
            instance=config)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
