from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection
from django.db.models import Q, Count, Subquery, OuterRef, IntegerField
from django.utils import timezone


from rest_framework import generics, status, viewsets, serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response

from config import constants as adv_consts
from core.utils import distinct_list

from config import constants as api_consts

from builders.models import (
    Faction,
    FactionAssignment,
    LastViewedRoom,
    WorldBuilder,
    WorldReview)

from core.db import qs_by_pks
from core.permissions import IsLobbyView, IsRootWorld
from core.view_mixins import (
    RequestDataMixin,
    KeyedRetrieveMixin,
    WorldValidatorMixin)

from lobby import serializers as lobby_serializers
from lobby.serializers import LobbyWorldSerializer, LobbyWorldCardSerializer
from lobby.models import FeaturedWorld, DiscoverWorld, InDevelopmentWorld
from spawns.models import Player
from spawns.serializers import PlayerSerializer
from system import (
    serializers as system_serializers,
    models as system_models)
from users import serializers as user_serializers
from users.models import User
from worlds.models import World


class WorldCardListView(generics.ListAPIView):

    serializer_class = LobbyWorldCardSerializer

    @classmethod
    def get_annotated_queryset(self, world_ids):
        # Subquery to count players associated with each world by context_id
        players_count_subquery = Player.objects.filter(
            world__context_id=OuterRef('pk'),
            user__is_temporary=False
        ).values('world__context_id').annotate(
            cnt=Count('id')
        ).values('cnt')

        return qs_by_pks(World, world_ids).annotate(
            num_characters=Subquery(players_count_subquery[:1],
                                    output_field=IntegerField())
        ).select_related('config')


class RecentChars(generics.ListAPIView):

    serializer_class = PlayerSerializer
    queryset = Player.objects.all()

    def get_queryset(self):
        return Player.objects.filter(
            user=self.request.user,
            pending_deletion_ts__isnull=True,
        ).exclude(
            world__context__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
        ).order_by('-last_connection_ts')[0:4]


class FeaturedWorlds(WorldCardListView):

    permission_classes = ()

    def get_queryset(self):
        world_ids = FeaturedWorld.objects.values_list(
            'world_id', flat=True
        ).order_by('order')
        return self.get_annotated_queryset(world_ids)


class DiscoverWorlds(WorldCardListView):

    permission_classes = ()

    def get_queryset(self):
        world_ids = DiscoverWorld.objects.values_list(
            'world_id', flat=True
        ).order_by('order')
        return self.get_annotated_queryset(world_ids)


class AllWorlds(generics.ListAPIView):

    permission_classes = ()
    serializer_class = LobbyWorldSerializer

    def get_queryset(self):
        world_ids = World.objects.filter(
            context_id__isnull=True,
            is_public=True
        ).exclude(
            lifecycle=api_consts.WORLD_STATE_ARCHIVED,
        ).exclude(
            id__in=(4, 83),
        ).values_list('id', flat=True)
        world_ids = [4] + list(world_ids)
        return qs_by_pks(World, world_ids)


class OnlineWorlds(generics.ListAPIView):
    serializer_class = LobbyWorldSerializer
    def get_queryset(self):
        # Return spawn multiplayer worlds that are running
        return World.objects.filter(
            is_multiplayer=True,
            context_id__isnull=False,
            lifecycle=api_consts.WORLD_STATE_RUNNING)


class UserWorlds(generics.ListAPIView):

    serializer_class = LobbyWorldSerializer

    def get_serializer_context(self):
        return {
            'request': self.request,
            'char_counts': 'user'
        }

    def get_queryset(self):
        world_ids = []

        # Worlds where the user is the author
        world_ids.extend(
            World.objects.filter(
                author=self.request.user,
                context_id__isnull=True
            ).order_by(
                '-created_ts'
            ).values_list('id', flat=True))

        # Worlds where the user is a builder
        world_ids.extend(
            WorldBuilder.objects.filter(
                world__context_id__isnull=True,
                user=self.request.user
            ).values_list('world_id', flat=True))

        # Worlds where the user has a player
        world_ids.extend(
            Player.objects.filter(
                user=self.request.user,
                world__context_id__isnull=False,
            ).order_by(
                '-last_connection_ts'
            ).values_list('world__context_id', flat=True))

        world_ids = distinct_list(world_ids)

        # Get archived ids to exclude
        archived_ids = World.objects.filter(
            id__in=world_ids,
            lifecycle=api_consts.WORLD_STATE_ARCHIVED
        ).values_list('id', flat=True)

        # Removed archived IDs
        world_ids = [
            i for i in world_ids if i not in archived_ids
        ]

        # Hack, don't show the wot world unless the user is staff
        if not self.request.user.is_staff:
            world_ids = [
                i for i in world_ids if i != 83
            ]

        # Sort the world ids by last played worlds
        with connection.cursor() as cursor:
            cursor.execute("""
                select distinct W.context_id, max(P.last_connection_ts)
                from spawns_player P, worlds_world W
                where P.user_id = %s and P.world_id = W.id
                group by P.world_id, W.context_id
                order by max(P.last_connection_ts) desc
                limit 20;
                """, [self.request.user.id])
            world_order = [
                int(row[0]) for row in cursor.fetchall()
            ]

        # Exclude archived worlds from world order
        world_order = [ i for i in world_order if i not in archived_ids ]

        # Make sure to add the worlds that did not emerge just from
        # the last 20 played analysis
        world_order = world_order + [
            i for i in world_ids if i not in world_order
        ]

        return qs_by_pks(World, world_order)


class PlayingWorlds(WorldCardListView):

    def get_serializer_context(self):
        return {
            'request': self.request,
            'char_counts': 'user'
        }

    def get_queryset(self):
        world_ids = []
        user = self.request.user

        # Worlds where the user has a player
        world_ids.extend(
            Player.objects.filter(
                user=user,
                world__context_id__isnull=False,
            ).exclude(
                world__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).order_by(
                '-last_connection_ts'
            ).values_list('world__context_id', flat=True))

        world_ids = distinct_list(world_ids)

        # Hack, don't show the wot world unless the user is staff
        if not user.is_staff:
            world_ids = [
                i for i in world_ids if i != 83
            ]

        # Sort the world ids by last played worlds
        with connection.cursor() as cursor:
            cursor.execute("""
                select distinct W.context_id, max(P.last_connection_ts)
                from spawns_player P, worlds_world W
                where P.user_id = %s and P.world_id = W.id
                group by P.world_id, W.context_id
                order by max(P.last_connection_ts) desc
                limit 20;
                """, [user.id])
            world_order = [
                int(row[0]) for row in cursor.fetchall()
            ]

        # Make sure to add the worlds that did not emerge just from
        # the last 20 played analysis
        world_order = world_order + [
            i for i in world_ids if i not in world_order
        ]

        return self.get_annotated_queryset(world_order)


class BuildingWorlds(WorldCardListView):

    serializer_class = LobbyWorldCardSerializer

    def get_serializer_context(self):
        return {
            'request': self.request,
            'char_counts': 'user'
        }

    def get_queryset(self):
        world_ids = []

        # Get Worlds where the user is the author
        world_ids.extend(
            World.objects.filter(
                author=self.request.user,
                context_id__isnull=True
            ).exclude(
                lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).values_list('id', flat=True))

        # Worlds where the user is a builder
        world_ids.extend(
            WorldBuilder.objects.filter(
                world__context_id__isnull=True,
                user=self.request.user
            ).exclude(
                world__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).values_list('world_id', flat=True))

        sorted_world_ids = LastViewedRoom.objects.filter(
            world_id__in=world_ids
        ).order_by('-modified_ts').values_list('world_id', flat=True)

        return self.get_annotated_queryset(sorted_world_ids)


class ReviewedWorlds(generics.ListAPIView):

    serializer_class = LobbyWorldSerializer

    def get_serializer_context(self):
        return {
            'request': self.request,
            'char_counts': 'user'
        }

    def get_queryset(self):
        world_ids = []

        world_ids = WorldReview.objects.filter(
            status=api_consts.WORLD_REVIEW_STATUS_APPROVED
        ).order_by(
            '-modified_ts'
        ).values_list('world_id', flat=True)

        return qs_by_pks(World, world_ids)


class IntroWorlds(WorldCardListView):

    serializer_class = LobbyWorldCardSerializer

    def get_queryset(self):
        world_ids = [4, 217, 1]
        return self.get_annotated_queryset(world_ids)


class PublishedWorlds(WorldCardListView):

    serializer_class = LobbyWorldCardSerializer

    def get_queryset(self):
        approved_worlds_ids = WorldReview.objects.filter(
            status=api_consts.WORLD_REVIEW_STATUS_APPROVED
        ).order_by(
            '-modified_ts'
        ).values_list('world_id', flat=True)
        world_ids = World.objects.filter(
            id__in=approved_worlds_ids,
            context_id__isnull=True,
        ).order_by(
            '-last_entered_ts', 'name'
        ).values_list('id', flat=True)
        return qs_by_pks(World, world_ids)


class PublicWorlds(WorldCardListView):

    serializer_class = LobbyWorldCardSerializer

    def get_queryset(self):
        world_ids = World.objects.filter(
            is_public=True,
            context_id__isnull=True,
            last_entered_ts__isnull=False,
        ).order_by(
            '-last_entered_ts', 'name'
        ).values_list('id', flat=True)
        return self.get_annotated_queryset(world_ids)


class InDevelopmentWorlds(WorldCardListView):

        serializer_class = LobbyWorldCardSerializer

        def get_queryset(self):
            world_ids = InDevelopmentWorld.objects.values_list(
                'world_id', flat=True
            ).order_by(
                'order',
            ).values_list('world_id', flat=True)
            return self.get_annotated_queryset(world_ids)


class SearchWorlds(generics.ListAPIView):

    serializer_class = LobbyWorldSerializer

    def get_queryset(self):
        query = self.request.query_params.get('q', '')
        reviewed = self.request.query_params.get('reviewed', 'true')
        if not query:
            return World.objects.none()

        # Build a list of world IDs where the user is player, author
        # or builder.
        world_ids = []
        # Author
        world_ids.extend(
            World.objects.filter(
                author=self.request.user,
                context_id__isnull=True
            ).exclude(
                lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).values_list('id', flat=True))
        # Builder
        world_ids.extend(
            WorldBuilder.objects.filter(
                world__context_id__isnull=True,
                user=self.request.user
            ).exclude(
                world__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).values_list('world_id', flat=True))
        # Player
        world_ids.extend(
            Player.objects.filter(
                user=self.request.user,
                world__context_id__isnull=False,
            ).exclude(
                world__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
            ).order_by(
                '-last_connection_ts'
            ).values_list('world__context_id', flat=True))
        # Public Worlds
        world_ids.extend(
            World.objects.filter(
                context__isnull=True,
                is_public=True
            ).order_by('-modified_ts').values_list('id', flat=True)
        )

        matched_ids = World.objects.filter(id__in=world_ids).filter(
            Q(name__icontains=query) |
            Q(description__icontains=query)
        ).order_by('-modified_ts').values_list('id', flat=True)

        if reviewed == 'true' and matched_ids:
            reviewed_ids = distinct_list(
                WorldReview.objects.filter(
                    world_id__in=matched_ids,
                    status=api_consts.WORLD_REVIEW_STATUS_APPROVED,
                ).values_list('world', flat=True))
            matched_ids = [ wid for wid in world_ids if wid in reviewed_ids ]

        world_ids = distinct_list(matched_ids)

        return qs_by_pks(World, world_ids)


class WorldLobbyBase(RequestDataMixin, WorldValidatorMixin):
    "Base for endpoints viewing information about a world from its"
    "lobby."

    permission_classes = (IsRootWorld, IsLobbyView,)


class WorldDetail(WorldLobbyBase,
                  generics.RetrieveAPIView):

    serializer_class = LobbyWorldSerializer #WorldSerializer
    queryset = World.objects.all()

    def get_object(self):
        return self.world


class WorldCharacters(WorldLobbyBase,
                      KeyedRetrieveMixin,
                      #generics.ListCreateAPIView):
                      viewsets.ModelViewSet):

    serializer_class = PlayerSerializer
    queryset = Player.objects.all()

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Player.objects.none()

        return Player.objects.filter(
            user=self.request.user,
            pending_deletion_ts__isnull=True,
            world__context_id=self.world.pk,
        ).order_by('-last_connection_ts')

    def perform_create(self, serializer):
        # Create a new spawn world, or get one if it's a multiplayer world
        # that's already been spawned.
        if self.world.is_multiplayer:

            # If the player is currently in an instance,
            # disallow creating a character in the base world.
            if Player.objects.filter(
                world__context__instance_of=self.world,
                in_game=True,
                user=self.request.user
            ).exists():
                raise ValidationError(
                    "Cannot create a character while in an instance.")

            try:
                spawn_world = self.world.spawned_worlds.get(
                    is_multiplayer=True)
            except World.DoesNotExist:
                spawn_world = self.world.create_spawn_world()
        else:
            spawn_world = self.world.create_spawn_world()

        # Create the player object
        player = serializer.save(
            user=self.request.user,
            world=spawn_world,
            last_connection_ts=timezone.now())

        if self.request.data.get('faction'):
            try:
                faction = spawn_world.context.world_factions.get(
                    code=self.request.data.get('faction'))
                FactionAssignment.objects.create(
                    faction=faction,
                    value=1,
                    member_type=ContentType.objects.get_for_model(player),
                    member_id=player.id)

            except Faction.DoesNotExist:
                pass

        player.room = player.get_starting_room()
        player.save()

        return player

    def partial_update(self, request, *args, **kwargs):
        if not self.world.can_edit(request.user):
            raise PermissionDenied("User does not have permission for this operation.")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        player = self.get_object()

        if player.in_game:
            raise ValidationError(
                "Cannot delete a player currently in a game.")

        if player.player_instances.count():
            raise ValidationError(
                "Cannot delete a player with live instances.")

        game_player = player.game_player
        if game_player:
            raise serializers.ValidationError(
                "Cannot delete a player currently in a game.")

        player.name = "%s%s" % (player.name, player.id)
        player.pending_deletion_ts = timezone.now()
        player.save()

        #self.perform_destroy(player)
        return Response(status=status.HTTP_204_NO_CONTENT)

world_chars = WorldCharacters.as_view({
    'get': 'list',
    'post': 'create'
})
world_char = WorldCharacters.as_view({
    'get': 'retrieve',
    'patch': 'partial_update',
    'delete': 'destroy',
})


class WorldLeaders(WorldLobbyBase, generics.ListAPIView):
    serializer_class = PlayerSerializer
    queryset = Player.objects.all()

    def get_queryset(self):
        qs = Player.objects.filter(
            is_immortal=False,
            world__context=self.world,
            pending_deletion_ts__isnull=True,
        ).order_by(
            '-glory',
            '-experience',
            '-created_ts')

        return qs[0:10]

world_leaders = WorldLeaders.as_view()


class Transfer(APIView):

    def post(self, request, world_pk, format=None):
        serializer = lobby_serializers.WorldTransferSerializer(
            data=request.data,
            context={'request': request})
        serializer.is_valid(raise_exception=True)
        player = serializer.save()
        return Response({
            'player_key': player.key,
            'world_id': player.world.id
        }, status=status.HTTP_201_CREATED)

transfer = Transfer.as_view()


class EdeusUniques(APIView):
    def get(self, request, format=None):
        eu = system_models.EdeusUniques.objects.order_by(
            '-created_ts'
        ).first()
        return Response(system_serializers.EdeusUniquesSerializer(eu).data)


class HomeData(APIView):

    permission_classes = ()

    def get(self, request, format=None):

        # Patrons
        patrons = user_serializers.UserSerializer(
            User.objects.filter(name_recognition=True),
            many=True
        ).data

        # Featured Worlds
        world_ids = FeaturedWorld.objects.values_list(
            'world_id', flat=True
        ).order_by('order')[0:3]
        qs = WorldCardListView.get_annotated_queryset(world_ids)
        worlds = LobbyWorldCardSerializer(qs, many=True).data

        return Response({
            'patrons': patrons,
            'worlds': worlds,
        })


class Lobby(APIView):

    def get(self, request, format=None):
        """
        Consolidated endpoint for all of the data that gets displayed to a
        user when they view the lobby. There are 2 groups of sections,
        fixed and variable. Fixed sections are the same for all users and
        can be cached. Variable sections are different for each user and
        cannot be cached.
        """

        return_data = {}

        # Fixed sections, meaning that they will be the same for all users
        # and we can cache them.

        # First, look into the cache for the fixed sections, and if the cache
        # is not older than 15 minutes, return the cached data.
        cache_key = 'lobby_fixed_sections'
        cached_data = cache.get(cache_key)

        ctx = {'request': request}

        if not cached_data:
            # Featured Worlds (Limit 2)
            featured_world_ids = list(FeaturedWorld.objects.values_list(
                'world_id', flat=True
            ).order_by('order')[0:2])

            # Staff Picks (All)
            staff_pick_ids = list(DiscoverWorld.objects.values_list(
                'world_id', flat=True
            ).order_by('order'))

            # In Development (Limit 3)
            in_development_ids = list(InDevelopmentWorld.objects.values_list(
                'world_id', flat=True
            ).order_by('order')[0:3])

            # Start Here (Fixed)
            intro_world_ids = [4, 217, 1]

            # Combine all unique IDs
            world_ids = distinct_list(
                featured_world_ids +
                staff_pick_ids +
                in_development_ids +
                intro_world_ids
            )

            # Fetch all worlds efficiently in one query
            annotated_worlds_qs = WorldCardListView.get_annotated_queryset(world_ids)

            # Create a map for quick lookup
            world_map = {world.id: world for world in annotated_worlds_qs}

            # Build section lists using the map, preserving order
            featured_worlds = [
                world_map[wid] for wid in featured_world_ids if wid in world_map
            ]
            staff_picks = [
                world_map[wid] for wid in staff_pick_ids if wid in world_map
            ]
            in_development = [
                world_map[wid] for wid in in_development_ids if wid in world_map
            ]
            intro_worlds = [
                world_map[wid] for wid in intro_world_ids if wid in world_map
            ]

            # Serialize the data
            featured_data = LobbyWorldCardSerializer(
                featured_worlds, many=True, context=ctx).data
            staff_picks_data = LobbyWorldCardSerializer(
                staff_picks, many=True, context=ctx).data
            in_development_data = LobbyWorldCardSerializer(
                in_development, many=True, context=ctx).data
            intro_data = LobbyWorldCardSerializer(
                intro_worlds, many=True, context=ctx).data

            fixed_sections = {
                'featured': featured_data,
                'staff_picks': staff_picks_data,
                'in_development': in_development_data,
                'intro': intro_data,
            }

            cache.set(cache_key, fixed_sections, 900)
        else:
            fixed_sections = cached_data

        # Variable sections

        # Recent Characters
        recent_characters = Player.objects.filter(
            user=request.user,
            pending_deletion_ts__isnull=True,
        ).order_by('-last_connection_ts')[0:4]
        recent_characters_data = PlayerSerializer(
            recent_characters, many=True, context=ctx).data

        # Playing
        playing_world_ids = list(Player.objects.filter(
            user=request.user,
            world__context_id__isnull=False,
        ).exclude(
            world__lifecycle=api_consts.WORLD_STATE_ARCHIVED,
        ).order_by(
            '-last_connection_ts'
        ).values_list('world__context_id', flat=True)[0:20])

        # Apply distinct_list and then slice to get first 3 distinct IDs
        playing_world_ids = distinct_list(playing_world_ids)[0:3]

        # Building
        building_world_ids = list(LastViewedRoom.objects.filter(
            user=request.user,
        ).order_by(
            '-modified_ts'
        ).values_list('world_id', flat=True)[0:3])

        world_ids = distinct_list(
            playing_world_ids +
            building_world_ids
        )

        qs = WorldCardListView.get_annotated_queryset(world_ids)
        world_map = {world.id: world for world in qs}

        playing_worlds = [world_map[wid] for wid in playing_world_ids if wid in world_map]
        building_worlds = [world_map[wid] for wid in building_world_ids if wid in world_map]

        playing_data = LobbyWorldCardSerializer(
            playing_worlds, many=True, context=ctx).data
        building_data = LobbyWorldCardSerializer(
            building_worlds, many=True, context=ctx).data

        variable_sections = {
            'recent_characters': recent_characters_data,
            'playing': playing_data,
            'building': building_data,
        }

        return_data.update(fixed_sections)
        return_data.update(variable_sections)

        return Response(return_data)

lobby = Lobby.as_view()
