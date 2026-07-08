import logging
import uuid

from config import constants as adv_consts

from django.contrib.auth import get_user_model

from rest_framework import (
    status,
    serializers)
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from users.tokens import build_token_response

from backend.config.exceptions import ServiceError
from core import throttles as api_throttles
from core.permissions import IsPlayerInGame
from core.ip import get_ip
from spawns import serializers as spawn_serializers, tasks as spawn_tasks
from spawns.loading import run_spawn_plans_for_world
from spawns.models import (
    Player, PlayerConfig)
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

        run_spawn_plans_for_world(world=spawn_world, initial=True)

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
