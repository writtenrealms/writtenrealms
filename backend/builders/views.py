import collections
import json

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count
from django.utils import timezone
from django.shortcuts import get_object_or_404


from redis.connection import ConnectionError

from rest_framework import (
    exceptions as drf_exceptions,
    generics,
    permissions,
    viewsets,
    serializers,
    status)
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from config import constants as adv_consts
from core.utils.mobs import suggest_stats

from config import constants as api_consts
from config import game_settings as adv_config
from core.serializers import KeyNameSerializer, ReferenceField
from core.view_mixins import (
    KeyedRetrieveMixin,
    RequestDataMixin,
    WorldValidatorMixin)

from builders import manifests as builder_manifests
from builders import permissions as builder_permissions
from builders import serializers as builder_serializers
from builders.models import (
    BuilderAction,
    BuilderAssignment,
    Currency,
    LastViewedRoom,
    ItemTemplate,
    ItemTemplateInventory,
    ItemAction,
    MobTemplate,
    MobTemplateInventory,
    MerchantInventory,
    MobReaction,
    TransformationTemplate,
    Loader,
    Rule,
    Quest,
    Objective,
    RandomItemProfile,
    Reward,
    RoomCheck,
    RoomAction,
    Trigger,
    Skill,
    Social,
    Path,
    PathRoom,
    Procession,
    FactionAssignment,
    Faction,
    FactionRank,
    FactSchedule,
    WorldBuilder,
    WorldReview)
from spawns.models import Player
from spawns import serializers as spawn_serializers
from users.models import User
from worlds import serializers as world_serializers
from worlds.models import (
    World, Room, Zone, RoomFlag, RoomDetail, Door, StartingEq)
from worlds.services import WorldSmith
from worlds import tasks as world_tasks


class BaseWorldBuilderViewSet(RequestDataMixin,
                              KeyedRetrieveMixin,
                              WorldValidatorMixin,
                              viewsets.ModelViewSet):
    """
    Only use with classes that also use WorldValidatorMixin as we need
    self.world in the permissions.
    """
    permission_classes = (
        permissions.IsAuthenticated,
        builder_permissions.IsWorldBuilder,
    )

    def search_queryset(self, qs, field_name='name'):
        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                lookup = '%s__icontains'    % field_name
                kwargs = {lookup: query}
                qs = qs.filter(**kwargs)

        # Sorting. Possibly doesn't belong here but rather in some other
        # method like 'apply_sort_by' or something.
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            qs = qs.order_by(sorting)

        return qs

    def char_filters(self, qs):
        # Filter by faction
        faction = self.request.query_params.get('faction')
        if faction:
            qs = qs.filter(faction_assignments__faction__code=faction)

        # Filter by level range
        level_range = self.request.query_params.get('level_range')
        if level_range:
            if level_range == '15':
                qs = qs.filter(level__gte=1, level__lte=5)
            elif level_range == '610':
                qs = qs.filter(level__gte=6, level__lte=10)
            elif level_range == '1115':
                qs = qs.filter(level__gte=11, level__lte=15)
            elif level_range == '1620':
                qs = qs.filter(level__gte=16, level__lte=20)

        return qs


class BaseWorldBuilderView(WorldValidatorMixin, APIView):
    # Non ViewSet flavor of BaseWorldBuilderViewSet
    permission_classes = (
        permissions.IsAuthenticated,
        builder_permissions.IsWorldBuilder,
    )

    def initialize_request(self, request, *args, **kwargs):
        _request = super().initialize_request(request, *args, **kwargs)
        if settings.DEBUG and _request.data:
            print("Request data: %s" % _request.data)
        return _request


def _has_zone_assignment(*, user, zone):
    if zone is None:
        return False
    return BuilderAssignment.objects.filter(
        builder__user=user,
        assignment_id=zone.id,
        assignment_type=ContentType.objects.get_for_model(Zone),
    ).exists()


def _has_room_assignment(*, user, room):
    return BuilderAssignment.objects.filter(
        builder__user=user,
        assignment_id=room.id,
        assignment_type=ContentType.objects.get_for_model(Room),
    ).exists()


def _has_room_or_zone_assignment(*, user, room):
    if _has_zone_assignment(user=user, zone=room.zone):
        return True
    return _has_room_assignment(user=user, room=room)


def _assert_can_view_room(*, view, room):
    if view._builder_rank >= 2:
        return
    if not _has_room_or_zone_assignment(user=view.request.user, room=room):
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to this room."
        )


def _assert_can_edit_room(*, view, room):
    if view._builder_rank >= 3:
        return
    if not _has_room_or_zone_assignment(user=view.request.user, room=room):
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter this room."
        )


class WorldCreationMixin:

    def perform_create(self, serializer):
        return serializer.save(world=self.world)


# World

class WorldViewSet(BaseWorldBuilderViewSet):
    queryset = World.objects.filter(
        context__isnull=True
    ).exclude(
        lifecycle=api_consts.WORLD_STATE_ARCHIVED
    )
    serializer_class = builder_serializers.WorldSerializer

    def explore(self, request, pk):

        # Using a very naive caching scheme here, where if the column
        # exist we return the cache. In other words, to reload the cache
        # nuke the column.
        if self.world.full_map:
            data = json.loads(self.world.full_map)
        else:
            qs = self.world.rooms.prefetch_related(
                'north',
                'east',
                'west',
                'south',
                'up',
                'down',
                'zone',
                'world')
            serializer = builder_serializers.MapRoomSerializer(qs, many=True)
            data = serializer.data
            self.world.full_map = json.dumps(data)
            self.world.save(update_fields=['full_map'])

        return Response({'data': data})

    def admin(self, request, pk):
        if self.world.context:
            spawn_world = self.world
            template_world = self.world.context
        else:
            template_world = self.world
            spawn_world = None

        if not template_world.is_multiplayer:
            return self.spw_stats(template_world)

        if not spawn_world:
            spawn_world = template_world.spawned_worlds.get(
                is_multiplayer=True)

        rdb = spawn_world.rdb
        try:
            game_world = rdb.fetch(spawn_world.key)
            game_world.lazy = True
        except (ConnectionError, NotFound):
            game_world = None

        serializer = builder_serializers.WorldStatsSerializer(
            spawn_world,
            context={'game_world': game_world})
        world_data = serializer.data

        world_data['cluster_data'] = {
            'is_cluster': False,
            'is_ready': True,
            'cluster_id': 0,
            'error': '',
        }
        if adv_config.IS_CLUSTER:
            world_data['cluster_data'] = {
                'is_cluster': True,
                'is_ready': True,
                'cluster_id': spawn_world.cluster_id,
                'error': '',
            }

        return Response(world_data)

    def spw_stats(self, template_world):
        spawned_worlds = template_world.spawned_worlds.all()
        online_worlds = spawned_worlds.filter(
            lifecycle=api_consts.WORLD_STATE_RUNNING)

        online_worlds_data = []

        # For each online world, get data details
        for online_world in online_worlds:
            # Get the game world
            rdb = online_world.rdb
            try:
                game_world = rdb.fetch(online_world.key)
                game_world.lazy = True
            except (ConnectionError, NotFound):
                game_world = None

            # Generate stats for each
            online_worlds_data.append(
                builder_serializers.WorldStatsSerializer(
                    online_world,
                    context={'game_world': game_world}
                ).data)

        return Response({
            'spawn_world_count': spawned_worlds.count(),
            'online_world_count': online_worlds.count(),
            'worlds': online_worlds_data
        })

    def destroy(self, request, pk):
        world = self.get_object()
        # If the world has any spawn worlds that are running, prevent
        # deletion.
        if world.spawned_worlds.filter(
            lifecycle=api_consts.WORLD_STATE_RUNNING).exists():
            raise drf_exceptions.ValidationError(
                "Cannot delete a world with running spawn worlds.")
        world.lifecycle = api_consts.WORLD_STATE_ARCHIVED
        world.save(update_fields=['lifecycle'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False)
    def factions(self, request, pk):
        qs = Faction.objects.filter(world_id=pk)

        # Filter by mob type (humanoid, beast, plant)
        query = self.request.query_params.get('query', None)
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)

        serializer = builder_serializers.FactionSerializer(qs, many=True)

        return Response({'data': serializer.data})

    @action(detail=False)
    def map(self, request, pk):
        world = self.get_object()
        return Response({
            'rooms': world.get_map()
        })

    def perform_update(self, serializer):
        world = serializer.save()
        data = {}
        if 'name' in serializer.validated_data:
            data['name'] = serializer.validated_data['name']
        if 'is_public' in serializer.validated_data:
            data['is_public'] = serializer.validated_data['is_public']
        world.spawned_worlds.update(**data)
        return world


class WorldListViewSet(WorldViewSet):

    permission_classes = (
        permissions.IsAuthenticated,
    )


world_list = WorldListViewSet.as_view({'get': 'list', 'post': 'create'})
world_detail = WorldViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
    'delete': 'destroy'
})
world_explore = WorldViewSet.as_view({'get': 'explore'})
#world_admin = WorldViewSet.as_view({'get': 'admin'})
#world_factions = WorldViewSet.as_view({'get': 'factions'})

class WorldAdminView(BaseWorldBuilderView):
    """
    View for the world admin page. It looks at a root world and
    returns stats for the spawned worlds.
    """

    def get(self, request, pk):
        if self.world.context:
            raise self.ValidationError("World is a spawned world.")
        return Response(
            builder_serializers.WorldAdminSerializer(
                self.world,
                context={'rdb': self.world.rdb}).data)

world_admin = WorldAdminView.as_view()


class WorldAdminInstance(BaseWorldBuilderView):
    def get(self, request, world_pk, pk):
        spawn_world = World.objects.get(pk=pk)
        if not spawn_world.context:
            raise self.ValidationError("World is not a spawned world.")
        return Response(
            builder_serializers.WorldAdminInstanceSerializer(spawn_world).data)

world_admin_instance = WorldAdminInstance.as_view()

class WorldMapView(WorldValidatorMixin, APIView):
    permission_classes = (
        builder_permissions.IsWorldBuilder,
    )
    def get(self, request, pk):

        """
        print('builder rank: %s' % self._builder_rank)

        if self._builder_rank >= 2:
            rooms_qs = self.world.rooms.all()
        elif self._builder_rank < 2:
            # See if the builder has permissions for a zone
            # or specific rooms in this world.
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            room_ids = BuilderAssignment.objects.filter(
                builder__user=request.user,
                assignment_type=ContentType.objects.get_for_model(Room),
            ).values_list('assignment_id', flat=True)
            # Get the rooms that either in the zones or that are
            # individually assigned to the builder.
            rooms_qs = Room.objects.filter(
                Q(zone_id__in=zone_ids) | Q(id__in=room_ids),
                world_id=pk)
            print(rooms_qs)
        else:
            raise drf_exceptions.PermissionDenied
        """

        rooms = self.world.get_map()

        if self.request.query_params.get('nodesc'):
            for room in rooms.values():
                del room['description']
        return Response({
            'rooms': rooms,
        })
world_map = WorldMapView.as_view()


class FactionViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.FactionSerializer

    def get_queryset(self):
        world = self.world.instance_of or self.world

        factions_qs = Faction.objects.filter(world=world)

        is_core = self.request.query_params.get('is_core', None)
        if is_core is not None:
            if is_core.lower() == 'true':
                factions_qs = factions_qs.filter(is_core=True)
            elif is_core.lower() == 'false':
                factions_qs = factions_qs.filter(is_core=False)

        factions_qs = self.search_queryset(factions_qs)

        return factions_qs

    def update_live_instances(self, world):
        return

    def perform_create(self, serializer):
        serializer.save(world=self.world)
        self.update_live_instances(self.world)

    def perform_update(self, serializer):
        skill = serializer.save()
        self.update_live_instances(self.world)
        return self.world

    def perform_destroy(self, instance):
        if Reward.objects.filter(
            profile_type=ContentType.objects.get_for_model(Faction),
            profile_id=instance.id).exists():
            raise drf_exceptions.ValidationError(
                'Cannot delete a faction used for a quest reward.')
        if FactionAssignment.objects.filter(
            faction=instance,
            faction__is_core=True).exists():
            raise drf_exceptions.ValidationError(
                'Cannot delete a core faction with assignments.')
        instance.delete()

world_factions = FactionViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
world_faction_detail = FactionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
    'delete': 'destroy',
})

class FactionRankViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.FactionRankSerializer

    def get_queryset(self):
        faction_rank_qs = FactionRank.objects.filter(
            faction__world=self.world,
            faction_id=self.kwargs['faction_pk'])
        return faction_rank_qs

    def perform_create(self, serializer):
        faction = Faction.objects.get(pk=self.kwargs['faction_pk'])
        serializer.save(faction=faction)

world_faction_rank_list = FactionRankViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
world_faction_rank_detail = FactionRankViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
    'delete': 'destroy',
})



class WorldConfigViewSet(WorldViewSet):

    serializer_class = builder_serializers.WorldConfigSerializer

    def get_queryset(self):
        return World.objects.all()

    def get_object(self):
        obj = super().get_object()
        return obj.config

    def perform_update(self, serializer):
        config = serializer.save()
        if config.is_narrative:
            config.allow_combat = False
        else:
            config.allow_combat = True
        config.save()
        return config


world_config = WorldConfigViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
})

# Zone

class ZoneBuilderViewSet(WorldCreationMixin,
                         BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ZoneBuilderSerializer

    def get_queryset(self):
        order_by = self.request.query_params.get('order_by', 'name')
        qs = Zone.objects.filter(
            world=self.world
        ).prefetch_related(
            'world', 'center', 'rooms',
        )

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 1:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(id__in=zone_ids)

        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)
        return qs.order_by(order_by)

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action in [
            'retrieve',
            'rooms',
            'paths',
            'map',
            'loaders',
            'quest_list']:
            return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this room.")

        return obj

    def perform_create(self, serializer):
        zone = super().perform_create(serializer)
        zone.update_live_instances()

        if self._builder_rank < 3:
            BuilderAssignment.objects.create(
                builder=WorldBuilder.objects.get(
                    user=self.request.user,
                    world=zone.world),
                assignment=zone)

        return zone

    @action(detail=False)
    def rooms(self, request, world_pk, pk):
        zone = Zone.objects.get(pk=pk)
        qs = zone.rooms.all().order_by('-created_ts')

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 1:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(zone_id__in=zone_ids)

        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)
        page = self.paginate_queryset(qs)
        serializer = builder_serializers.MapRoomSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False)
    def paths(self, request, world_pk, pk):
        zone = Zone.objects.get(pk=pk)
        qs = zone.paths.all().order_by('-created_ts')

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 1:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(zone_id__in=zone_ids)


        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)
        page = self.paginate_queryset(qs)
        serializer = builder_serializers.PathListSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False)
    def map(self, request, world_pk, pk):
        zone = self.get_object()
        MapRoomSerializer = builder_serializers.MapRoomSerializer
        qs = MapRoomSerializer.prefetch_map(zone.rooms.all())
        return Response({
            'rooms': MapRoomSerializer(qs, many=True).data
        })

    @action(detail=False)
    def loaders(self, request, world_pk, pk):
        zone = Zone.objects.get(pk=pk)
        qs = zone.loaders.all().order_by('-created_ts')

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 1:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(zone_id__in=zone_ids)

        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)
        page = self.paginate_queryset(qs)
        page = self.paginate_queryset(qs)
        serializer = builder_serializers.LoaderSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False)
    def create_path(self, request, world_pk, pk):
        zone = self.get_object()
        serializer = builder_serializers.PathDetailsSerializer(
            data=request.data,
            context={'zone': zone})
        serializer.is_valid(raise_exception=True)
        path = serializer.save()
        path.update_live_instances()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False)
    def quest_list(self, request, world_pk, pk):
        zone = self.get_object()
        qs = zone.zone_quests.all().order_by('-level', '-created_ts')

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 1:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(zone_id__in=zone_ids)

        page = self.paginate_queryset(qs)
        serializer = builder_serializers.QuestSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False)
    def create_quest(self, request, world_pk, pk):
        zone = self.get_object()
        serializer = builder_serializers.QuestSerializer(
            data=request.data,
            context={'zone': zone})
        serializer.is_valid(raise_exception=True)
        quest = serializer.save()
        quest_data = builder_serializers.QuestSerializer(quest).data
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True)
    def loads(self, request, world_pk, pk):

        # Get loaded mobs
        mob_template_ids = Rule.objects.filter(
            template_type=ContentType.objects.get_for_model(MobTemplate),
            loader__zone_id=pk,
        ).values_list('template_id', flat=True)
        mobs = builder_serializers.MobTemplateSerializer(
            MobTemplate.objects.filter(id__in=mob_template_ids),
            context={'request': request},
            many=True
        ).data

        # Get loaded items
        item_template_ids = Rule.objects.filter(
            template_type=ContentType.objects.get_for_model(ItemTemplate),
            loader__zone_id=pk,
        ).values_list('template_id', flat=True)
        items = builder_serializers.ItemTemplateSerializer(
            ItemTemplate.objects.filter(id__in=item_template_ids),
            context={'request': request},
            many=True
        ).data

        return Response({
            'mobs': mobs,
            'items': items,
        })

    @action(detail=False)
    def move(self, request, world_pk, pk):
        zone = self.get_object()
        serializer = builder_serializers.MoveZoneSerializer(
            data=request.data,
            context={'zone': zone})
        serializer.is_valid(raise_exception=True)
        move_data = serializer.save()

        updated_rooms = builder_serializers.RoomBuilderSerializer(
            move_data['rooms'],
            context={'request': request},
            many=True).data

        return Response(
            updated_rooms,
            status=status.HTTP_201_CREATED)

    def destroy(self, request, world_pk, pk, *args, **kwargs):
        zone = Zone.objects.get(pk=pk)
        rooms = zone.rooms.all()
        if rooms.count() > 0:
            raise serializers.ValidationError('Cannot delete a zone with rooms assigned to it.')

        builder = WorldBuilder.objects.filter(
            user=self.request.user,
            world=zone.world).first()

        destroy_output = super().destroy(request, world_pk, pk, *args, **kwargs)

        if builder:
            assignments = BuilderAssignment.objects.filter(
                builder=builder,
                assignment_type=ContentType.objects.get_for_model(Zone),
                assignment_id=zone.id)
            if assignments:
                assignments.delete()

        return destroy_output

zone_list =  ZoneBuilderViewSet.as_view({
    'get': 'list',
    'post': 'create'})
zone_detail =  ZoneBuilderViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})
zone_room_list = ZoneBuilderViewSet.as_view({
    'get': 'rooms',
})
zone_path_list = ZoneBuilderViewSet.as_view({
    'get': 'paths',
    'post': 'create_path',
})
zone_map = ZoneBuilderViewSet.as_view({
    'get': 'map'
})
zone_loaders = ZoneBuilderViewSet.as_view({
    'get': 'loaders',
})
zone_quest_list = ZoneBuilderViewSet.as_view({
    'get': 'quest_list',
    'post': 'create_quest',
})
zone_loads = ZoneBuilderViewSet.as_view({
    'get': 'loads',
})
zone_move = ZoneBuilderViewSet.as_view({
    'post': 'move',
})

# Room

def apply_zone_filter(qs, request):
    zone = request.query_params.get('zone', None)
    if zone is not None:
        if '.' in zone:
            relative_id = zone.split('.')[1]
            qs = qs.filter(zone__relative_id=relative_id)
        else:
            qs = qs.filter(zone_id=zone)
    return qs

class RoomBuilderListViewSet(WorldCreationMixin,
                             BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MapRoomSerializer

    def get_queryset(self):
        qs = Room.objects.filter(world=self.world)
        qs = apply_zone_filter(qs, self.request)

        query = self.request.query_params.get('query')
        if query == '':
            qs = qs.all()
        elif query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)

        return qs.order_by('-created_ts')

room_list =  RoomBuilderListViewSet.as_view({
    'get': 'list',
    'post': 'create',
})


class RoomBuilderDetailViewSet(RoomBuilderListViewSet):
    serializer_class = builder_serializers.RoomBuilderSerializer

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=obj.id,
                assignment_type=ContentType.objects.get_for_model(Room),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this room.")

        return obj

    def mark_last_viewed(self, room):
        lvr, created = LastViewedRoom.objects.get_or_create(
            world=room.world,
            user=self.request.user,
            defaults={'room': room})
        if not created:
            lvr.room = room
            lvr.save()

    def retrieve(self, *args, **kwargs):
        resp = super().retrieve(*args, **kwargs)
        # If the retrieve is successful, update the last viewed room
        # record
        if (resp.status_code == 200):
            room = Room.objects.get(pk=resp.data['id'])
            self.mark_last_viewed(room)
        return resp

    def last_viewed(self, request, world_pk, pk):
        try:
            room = Room.objects.get(pk=pk)
        except Room.DoesNotExist:
            raise NotFound
        return Response({}, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """
        Overwrite rest_framework.mixins.UpdateModelMixin.update so that
        doors can be updated if we're changing a room's exit
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        original_exits = {
            d: getattr(instance, d) for d in adv_consts.DIRECTIONS
        }

        self.perform_update(serializer)

        # See if we need to remove any doors from this action
        for d in adv_consts.DIRECTIONS:
            if d in request.data:
                if getattr(instance, d) != original_exits.get(d):
                    Door.objects.filter(
                        from_room=instance,
                        direction=d,
                    ).update(to_room=getattr(instance, d))

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def perform_update(self, serializer):
        room = serializer.save()
        room.update_live_instances()
        return room

    def destroy(self, request, pk, *args, **kwargs):
        try:
            room = Room.objects.get(pk=pk)
        except Room.DoesNotExist:
            raise NotFound

        world_rooms = room.world.rooms.all()

        if world_rooms.count() == 1:
            raise serializers.ValidationError(
                'Cannot delete the last room in a world.')

        if room.players.filter(in_game=True).count():
            raise serializers.ValidationError(
                'Cannot delete room with a connected player in it.')

        config = room.world.config

        if room == config.starting_room:
            first_room = world_rooms.exclude(id=pk).first()
            config.starting_room = first_room
            config.save()

        if room == config.death_room:
            config.death_room = config.starting_room
            config.save()

        # If any players are in this room, move them to the
        # new starting room
        room.players.update(room=config.starting_room)

        # Delete room related builder assignments
        BuilderAssignment.objects.filter(
            assignment_id=room.id,
            assignment_type=ContentType.objects.get_for_model(Room),
        ).delete()

        return super().destroy(request, pk, *args, **kwargs)


class InstanceRoomListViewSet(WorldCreationMixin,
                                BaseWorldBuilderViewSet):
        serializer_class = builder_serializers.RoomBuilderSerializer

        def get_queryset(self):
            qs = Room.objects.filter(
                world__instance_of=self.world
            ).exclude(
                world__lifecycle=api_consts.WORLD_STATE_ARCHIVED
            )

            query = self.request.query_params.get('query')
            if query == '':
                qs = qs.all()
            elif query:
                try:
                    query = int(query)
                    qs = qs.filter(pk=query)
                except ValueError:
                    qs = qs.filter(name__icontains=query)

            return qs.order_by('-created_ts')

instance_room_list = InstanceRoomListViewSet.as_view({'get': 'list'})


class LegacyRoomBuilderDetailViewSet(RoomBuilderListViewSet):
    serializer_class = builder_serializers.LegacyRoomBuilderSerializer

room_detail = RoomBuilderDetailViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})
room_mark_last_viewed = RoomBuilderDetailViewSet.as_view({
    'post': 'last_viewed',
})

room_detail_legacy = LegacyRoomBuilderDetailViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class RoomDirActionView(WorldValidatorMixin, APIView):

    permission_classes = (
        permissions.IsAuthenticated,
        builder_permissions.IsWorldBuilder,
    )

    def post(self, request, world_pk, pk, format=None):
        if '.' in pk:
            pk = pk.split('.')[1]

        self.room = generics.get_object_or_404(
            Room.objects.filter(world_id=world_pk),
            id=pk)

        if self._builder_rank < 3:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=self.room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=self.room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to change this room.")

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data=request.data)
        serializer.is_valid(raise_exception=True)
        exit_room = serializer.save(room=self.room)

        # Update both rooms
        exit_room.update_live_instances()
        self.room.update_live_instances()

        room_serializer = builder_serializers.MapRoomSerializer
        return Response({
            'direction': serializer.validated_data['direction'],
            'room': room_serializer(self.room).data,
            'exit': room_serializer(exit_room).data
        }, status=status.HTTP_201_CREATED)

room_dir_action = RoomDirActionView.as_view()


class RoomLoadsView(BaseWorldBuilderView):

    def get(self, request, world_pk, room_pk, format=None):
        """
        Return format:
        {
            [loader_id]: {
                'rooms': {
                    'mobs': [],
                    'items': [],
                },
                'paths': {
                    'mobs': [],
                    'items': [],
                },
            },
        }
        """


        if '.' in room_pk:
            room = Room.objects.get(
                world_id=world_pk,
                relative_id=room_pk.split('.')[1])
        else:
            room = Room.objects.get(pk=room_pk)


        # Get the qs for all rules targetting this room
        room_rules_qs = Rule.objects.filter(
            target_type=ContentType.objects.get_for_model(room),
            target_id=room.id)

        # Get the qs for all rules targetting a path that this room belongs to
        path_ids = PathRoom.objects.filter(
            room=room
        ).values_list('path_id', flat=True)
        path_rules_qs = Rule.objects.filter(
            target_type=ContentType.objects.get_for_model(Path),
            target_id__in=path_ids)

        loaders = {}
        def init_loader(id):
            if id not in loaders:
                loaders[id] = {
                    'loader': builder_serializers.LoaderSerializer(
                        Loader.objects.get(pk=id)).data,
                    'room': {
                        'items': [],
                        'mobs': [],
                    },
                    'path': {
                        'items': [],
                        'mobs': [],
                    }
                }

        mob_template_ct = ContentType.objects.get_for_model(MobTemplate)
        item_template_ct = ContentType.objects.get_for_model(ItemTemplate)

        # Process room loads
        for rule in room_rules_qs:
            if rule.template_type == mob_template_ct:
                init_loader(rule.loader_id)
                loaders[rule.loader_id]['room']['mobs'].append(
                    builder_serializers.MobTemplateSerializer(
                        rule.template).data)
            elif rule.template_type == item_template_ct:
                init_loader(rule.loader_id)
                loaders[rule.loader_id]['room']['items'].append(
                    builder_serializers.ItemTemplateSerializer(
                        rule.template).data)

        # Proess path loads
        for rule in path_rules_qs:
            if rule.template_type == mob_template_ct:
                init_loader(rule.loader_id)
                loaders[rule.loader_id]['path']['mobs'].append(
                    builder_serializers.MobTemplateSerializer(
                        rule.template).data)
            elif rule.template_type == item_template_ct:
                init_loader(rule.loader_id)
                loaders[rule.loader_id]['path']['items'].append(
                    builder_serializers.ItemTemplateSerializer(
                        rule.template).data)

        loaders = sorted(loaders.values(), key=lambda x: x['loader']['id'])

        # Legacy
        mob_template_ids = room_rules_qs.filter(
            template_type=ContentType.objects.get_for_model(MobTemplate),
        ).values_list('template_id', flat=True)

        mobs_data = builder_serializers.MobTemplateSerializer(
            MobTemplate.objects.filter(pk__in=mob_template_ids),
            many=True,
        ).data

        return Response({
            'mobs': mobs_data,
            'loaders': loaders,
        })

    def post(self, request, world_pk, room_pk, format=None):
        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            pk=room_pk)

        if self._builder_rank <=2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to alter this room.")

        serializer = builder_serializers.RoomAddLoadSerializer(
            data=request.data,
            context={'room': room})
        serializer.is_valid(raise_exception=True)
        loader = serializer.save()
        return Response(
            builder_serializers.LoaderSerializer(loader).data,
            status=status.HTTP_201_CREATED)

room_loads = RoomLoadsView.as_view()


class RoomCheckViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.RoomCheckSerializer
    pagination_class = None
    queryset = RoomCheck.objects.all()

    def get_queryset(self):
        qs = RoomCheck.objects.filter(
            room__world=self.world,
            room_id=self.kwargs['room_pk'])
        return qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.room.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=obj.room.id,
                assignment_type=ContentType.objects.get_for_model(Room),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this room.")

        return obj

    def perform_create(self, serializer):
        try:
            room = Room.objects.get(
                pk=self.kwargs['room_pk'],
                world=self.world)
        except Room.DoesNotExist:
            raise drf_exceptions.NotFound(
                "Room not found")

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to alter this room.")

        check = serializer.save(room=room)
        check.room.update_live_instances()
        return check.room

    def perform_update(self, serializer):
        check = serializer.save()
        check.room.update_live_instances()
        return check.room


room_checks = RoomCheckViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
room_check_detail = RoomCheckViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class RoomActionViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.RoomActionSerializer
    pagination_class = None
    queryset = RoomAction.objects.all()

    def get_queryset(self):
        qs = RoomAction.objects.filter(
            room__world=self.world,
            room_id=self.kwargs['room_pk'])
        return qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.room.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=obj.room.id,
                assignment_type=ContentType.objects.get_for_model(Room),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this room.")

        return obj

    def perform_create(self, serializer):
        try:
            room = Room.objects.get(
                pk=self.kwargs['room_pk'],
                world=self.world)
        except Room.DoesNotExist:
            raise drf_exceptions.NotFound(
                "Room not found")

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to alter this room.")

        action = serializer.save(room=room)
        action.room.update_live_instances()
        return action.room

    def perform_update(self, serializer):
        action = serializer.save()
        action.room.update_live_instances()
        return action.room


room_action_list = RoomActionViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
room_action_detail = RoomActionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class CloneRoomAction(BaseWorldBuilderView):

    def post(self, request, world_pk, room_pk, pk, format=None):
        action = generics.get_object_or_404(
            RoomAction.objects.all(),
            id=pk)

        new_action = action
        new_action.pk = None
        new_action.save()

        new_action.room.update_live_instances()

        return Response(
            builder_serializers.RoomActionSerializer(new_action).data)


room_action_clone = CloneRoomAction.as_view()


class RoomTriggerListView(BaseWorldBuilderView):

    def get(self, request, world_pk, room_pk, format=None):
        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            pk=room_pk,
        )
        _assert_can_view_room(view=self, room=room)

        room_ct = ContentType.objects.get_for_model(Room)
        triggers = Trigger.objects.filter(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=room_ct,
            target_id=room.id,
        ).order_by("order", "created_ts", "id")

        return Response(
            {
                "new_trigger_template": builder_manifests.serialize_room_trigger_template(
                    world=self.world,
                    room=room,
                ),
                "triggers": [
                    builder_manifests.serialize_trigger_manifest(trigger)
                    for trigger in triggers
                ]
            }
        )


room_triggers = RoomTriggerListView.as_view()


class WorldManifestApplyView(BaseWorldBuilderView):

    def _assert_can_edit_trigger_scope_target(
        self,
        *,
        scope,
        target_id,
        target_model=None,
    ):
        if self._builder_rank >= 3:
            return

        if scope == adv_consts.TRIGGER_SCOPE_ROOM:
            if target_model and target_model is not Room:
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to alter this room-scoped trigger."
                )
            room = generics.get_object_or_404(
                Room.objects.filter(world=self.world),
                pk=target_id,
            )
            _assert_can_edit_room(view=self, room=room)
            return

        if scope == adv_consts.TRIGGER_SCOPE_ZONE:
            zone = generics.get_object_or_404(
                Zone.objects.filter(world=self.world),
                pk=target_id,
            )
            if not _has_zone_assignment(user=self.request.user, zone=zone):
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to alter this zone."
                )
            return

        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter world-scoped triggers."
        )

    def _assert_can_edit_trigger_target(self, parsed_trigger):
        self._assert_can_edit_trigger_scope_target(
            scope=parsed_trigger.scope,
            target_id=parsed_trigger.target_id,
            target_model=parsed_trigger.target_type.model_class() if parsed_trigger.target_type else None,
        )

    def post(self, request, world_pk, format=None):
        manifest_text = request.data.get("manifest")
        if manifest_text is None:
            raise serializers.ValidationError({"manifest": ["This field is required."]})

        manifest = builder_manifests.load_yaml_manifest(manifest_text)
        operation = builder_manifests.parse_manifest_operation(manifest)

        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_trigger_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            trigger = parsed_delete.trigger
            self._assert_can_edit_trigger_scope_target(
                scope=trigger.scope,
                target_id=trigger.target_id,
                target_model=trigger.target_type.model_class() if trigger.target_type else None,
            )
            trigger_payload = {
                "id": trigger.id,
                "key": trigger.key,
                "name": trigger.name or "",
                "scope": trigger.scope,
                "kind": trigger.kind,
            }
            trigger.delete()
            return Response(
                {
                    "kind": builder_manifests.TRIGGER_MANIFEST_KIND,
                    "operation": "deleted",
                    "trigger": trigger_payload,
                },
                status=status.HTTP_200_OK,
            )

        parsed_trigger = builder_manifests.parse_trigger_manifest(
            world=self.world,
            manifest=manifest,
        )
        self._assert_can_edit_trigger_target(parsed_trigger)

        is_create = parsed_trigger.trigger is None
        trigger = builder_manifests.apply_trigger_manifest(parsed_trigger)

        if trigger.scope == adv_consts.TRIGGER_SCOPE_ROOM:
            target_model = trigger.target_type.model_class() if trigger.target_type else None
            if target_model == Room and trigger.target_id:
                room = Room.objects.filter(world=self.world, pk=trigger.target_id).first()
                if room:
                    room.update_live_instances()

        return Response(
            {
                "kind": builder_manifests.TRIGGER_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "trigger": builder_manifests.serialize_trigger_manifest(trigger),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )


world_manifest_apply = WorldManifestApplyView.as_view()


class RoomDetailViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.RoomDetailSerializer
    pagination_class = None
    queryset = RoomDetail.objects.all()

    def get_queryset(self):
        qs = RoomDetail.objects.filter(
            room__world=self.world,
            room_id=self.kwargs['room_pk'])
        return qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.room.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=obj.room.id,
                assignment_type=ContentType.objects.get_for_model(Room),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this room.")

        return obj

    def perform_create(self, serializer):
        try:
            room = Room.objects.get(
                pk=self.kwargs['room_pk'],
                world=self.world)
        except Room.DoesNotExist:
            raise drf_exceptions.NotFound(
                "Room not found")

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to alter this room.")

        detail = serializer.save(room=room)
        detail.room.update_live_instances()
        return detail.room

    def perform_update(self, serializer):
        detail = serializer.save()
        detail.room.update_live_instances()
        return detail.room

room_detail_list = RoomDetailViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
room_detail_detail = RoomDetailViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class RoomConfig(BaseWorldBuilderView):

    def get(self, request, world_pk, pk, format=None):
        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            id=pk)

        return Response({
            'has_instances': room.world.instances.count() > 0,
            'transfer_to': ReferenceField().to_representation(
                room.transfer_to) if room.transfer_to else None,
            'transfer_to_world': ReferenceField().to_representation(
                room.transfer_to.world) if room.transfer_to else None,
        })

    def patch(self, request, world_pk, pk, format=None):
        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            id=pk)
        if 'transfer_to' in request.data:
            room.transfer_to_id = request.data['transfer_to']
            room.save(update_fields=['transfer_to'])
            room.update_live_instances()
        return Response({
            'transfer_to': ReferenceField().to_representation(
                room.transfer_to) if room.transfer_to else None,
            'transfer_to_world': ReferenceField().to_representation(
                room.transfer_to.world) if room.transfer_to else None,
        })

room_config = RoomConfig.as_view()


class RoomFlagsViewBase(BaseWorldBuilderView):

    @staticmethod
    def get_flags():
        return [
            # {
            #     'code': adv_consts.ROOM_FLAG_WORKSHOP,
            #     'label': 'Workshop',
            # },
            {
                'code': adv_consts.ROOM_FLAG_NO_ROAM,
                'label': 'No Roam',
            },
            {
                'code': adv_consts.ROOM_FLAG_PEACEFUL,
                'label': 'Peaceful',
            },
            {
                'code': adv_consts.ROOM_FLAG_NO_QUIT,
                'label': 'No Quit',
            },
        ]

    def get_queryset(self):
        qs = RoomFlag.objects.filter(
            room__world=self.world,
            room_id=self.kwargs['pk'])
        return qs

class RoomFlagList(RoomFlagsViewBase):

    def get(self, request, world_pk, pk, format=None):

        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            id=pk)

        qs = self.get_queryset()
        codes = qs.values_list('code', flat=True)
        flags = self.get_flags()
        for flag in flags:
            if flag['code'] in codes:
                flag['value'] = True
            else:
                flag['value'] = False

        if room.is_landmark:
            flags.append({
                'code': 'landmark',
                'label': 'Landmark',
                'value': True,
            })
        else:
            flags.append({
                'code': 'landmark',
                'label': 'Landmark',
                'value': False,
            })

        return Response(flags)

class RoomFlagToggle(RoomFlagsViewBase):

    def post(self, request, world_pk, pk, code, format=None):
        try:
            room = Room.objects.get(
                pk=pk,
                world=self.world)
        except Room.DoesNotExist:
            raise drf_exceptions.NotFound(
                "Room not found")

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=room.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                if not BuilderAssignment.objects.filter(
                    builder__user=self.request.user,
                    assignment_id=room.id,
                    assignment_type=ContentType.objects.get_for_model(Room),
                ).exists():
                    raise drf_exceptions.PermissionDenied(
                        "You do not have permission to alter this room.")

        # special case for landmark flag
        if code == 'landmark':
            if room.is_landmark:
                room.is_landmark = False
            else:
                room.is_landmark = True
            room.save()
        else:
            try:
                RoomFlag.objects.get(room_id=pk, code=code).delete()
                value = False
            except RoomFlag.DoesNotExist:
                RoomFlag.objects.create(room_id=pk, code=code)
                value = True

        flag_data = None
        for flag in self.get_flags():
            if flag['code'] == 'landmark':
                if room.is_landmark:
                    flag['value'] = True
                else:
                    flag['value'] = False
            elif flag['code'] == code:
                flag_data = flag
                flag_data['value'] = value

        room.update_live_instances()

        return Response(flag_data, status=status.HTTP_201_CREATED)

room_flag_list = RoomFlagList.as_view()
room_flag_toggle = RoomFlagToggle.as_view()


class RoomSetDoor(WorldValidatorMixin, APIView):

    def post(self, request, world_pk, room_pk):
        self.room = generics.get_object_or_404(
            Room.objects.filter(world_id=world_pk),
            id=room_pk)

        serializer = builder_serializers.RoomSetDoorSerializer(
            data=request.data,
            room=self.room)

        serializer.is_valid(raise_exception=True)
        data = serializer.save()

        data['door'].from_room.update_live_instances()
        if data['reverse_door']:
            data['reverse_door'].from_room.update_live_instances()

        return Response({}, status=status.HTTP_201_CREATED)

room_set_door = RoomSetDoor.as_view()


class RoomClearDoor(WorldValidatorMixin, APIView):

    def post(self, request, world_pk, room_pk):
        self.room = generics.get_object_or_404(
            Room.objects.filter(world_id=world_pk),
            id=room_pk)

        serializer = builder_serializers.RoomClearDoorSerializer(
            data=request.data,
            room=self.room)
        serializer.is_valid(raise_exception=True)
        direction = serializer.initial_data.get('direction')
        door = serializer.validated_data['direction']
        room = door.from_room
        door.delete()
        room.update_live_instances()

        exit_room = getattr(room, direction)
        if exit_room:
            try:
                Door.objects.get(
                    from_room=exit_room,
                    to_room=room).delete()
                exit_room.update_live_instances()
            except Door.DoesNotExist:
                pass

        return Response({}, status=status.HTTP_204_NO_CONTENT)

room_clear_door = RoomClearDoor.as_view()


# Item Template

class ItemTemplateViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ItemTemplateSerializer

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        qs = ItemTemplate.objects.filter(
            world=context
        ).prefetch_related('currency')

        # Filter down further if this is a rank 1 builder
        if self.action == 'list' == self._builder_rank <= 1:
            item_template_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(ItemTemplate),
            ).values_list('assignment_id', flat=True)
            qs = qs.filter(pk__in=item_template_ids)

        # The 'item_type' parameter doesn't correspond to any single field on
        # the backend, but rather a combination of type and equipment_type,
        # to make things easier on the frontend.
        item_type = self.request.query_params.get('item_type', None)
        if item_type in adv_consts.EQUIPMENT_TYPES:
            qs = qs.filter(equipment_type=item_type)
        elif item_type in adv_consts.ITEM_TYPES:
            qs = qs.filter(type=item_type)

        query = self.request.query_params.get('query')
        if query:
            try:
                query = int(query)
                qs = qs.filter(pk=query)
            except ValueError:
                qs = qs.filter(name__icontains=query)

        context = self.request.query_params.get('context')
        if (context == 'key'):
            qs = qs.filter(type='key')

        qs = qs.order_by('-modified_ts')

        # Sorting
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            mobs_qs = mobs_qs.order_by(sorting)

        return qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3:
            return obj

        if (self._builder_rank >= 2
            and self.action in ('retrieve', 'quests', 'inventory')):
            return obj

        has_assignment = BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.id,
            assignment_type=ContentType.objects.get_for_model(ItemTemplate),
        ).exists()

        if not has_assignment:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this item.")


        return obj

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['world'] = self.world  # Add world to the context
        return context

    def perform_create(self, serializer):
        item_template = serializer.save(world=self.world)

        # Create a builder assignment if the user is rank 2 or lower
        if self._builder_rank <= 2:
            builder = WorldBuilder.objects.get(user=self.request.user,
                                               world=self.world)
            BuilderAssignment.objects.create(
                builder=builder,
                assignment=item_template)

    def perform_destroy(self, instance):
        if instance.template_items.count():
            raise serializers.ValidationError(
                "Cannot delete a template that has loaded items.")
        if Reward.objects.filter(
            profile_type=ContentType.objects.get_for_model(instance),
            profile_id=instance.id).count():
            raise serializers.ValidationError(
                "Cannot delete a template used for a quest reward.")
        if Objective.objects.filter(
            template_type=ContentType.objects.get_for_model(instance),
            template_id=instance.id,
            qty__gte=1).count():
            raise serializers.ValidationError(
                "Cannot delete a template used for a quest objective.")
        # Delete related builder assignments
        BuilderAssignment.objects.filter(
            assignment_id=instance.id,
            assignment_type=ContentType.objects.get_for_model(ItemTemplate)
        ).delete()
        instance.delete()

    @action(detail=False)
    def inventory(self, request, pk, world_pk):
        serializer = builder_serializers.ItemTemplateInventorySerializer(
            ItemTemplateInventory.objects.filter(
                container=self.get_object()),
            many=True)
        return Response({'data': serializer.data})

    def add_to_inventory(self, request, pk, world_pk):
        item_template = self.get_object()

        # Filter down further if this is a rank 1 builder
        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(ItemTemplate),
                assignment_id=item_template.id).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to edit this item.")

        serializer = builder_serializers.AddItemTemplateInventorySerializer(
            container=item_template,
            data=request.data)
        serializer.is_valid(raise_exception=True)
        iti = serializer.create(serializer.validated_data)
        serializer = builder_serializers.MobTemplateInventorySerializer(iti)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False)
    def quests(self, request, world_pk, pk):
        item_template = self.get_object()
        item_template_ct = ContentType.objects.get_for_model(item_template)

        # Get all the objectives that reference this item template
        quest_ids_via_objectives = set(Objective.objects.filter(
            template_type=item_template_ct,
            template_id=item_template.id
        ).values_list('quest_id', flat=True))

        quest_ids_via_rewards = set(Reward.objects.filter(
            profile_type=item_template_ct,
            profile_id=item_template.id
        ).values_list('quest_id', flat=True))

        quest_qs = Quest.objects.filter(
            pk__in=quest_ids_via_objectives | quest_ids_via_rewards)

        serializer = builder_serializers.QuestSerializer(quest_qs, many=True)
        return Response({'quests': serializer.data})


item_template_list = ItemTemplateViewSet.as_view({
    'get': 'list',
    'post': 'create'})
item_template_detail = ItemTemplateViewSet.as_view({
    'get': 'retrieve',
    'patch': 'partial_update',
    'put': 'update',
    'delete': 'destroy',
})
item_template_inventory = ItemTemplateViewSet.as_view({
    'get': 'inventory',
    'post': 'add_to_inventory',
})
item_template_quests = ItemTemplateViewSet.as_view({'get': 'quests'})

class ItemTemplateInventoryViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ItemTemplateInventorySerializer

    def get_queryset(self):
        return ItemTemplateInventory.objects.all()

item_template_inventory_detail = ItemTemplateInventoryViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})


class ItemTemplateLoadsinView(BaseWorldBuilderView):

    def get(self, request, world_pk, item_template_pk, format=None):
        if '.' in item_template_pk:
            item_template_pk = item_template_pk.split('.')[1]

        data = {}

        # Find loaders that load the item
        item_template_ct = ContentType.objects.get_for_model(ItemTemplate)
        rules_qs = Rule.objects.filter(
            loader__world_id=world_pk,
            template_type=item_template_ct,
            template_id=item_template_pk)
        loader_ids = rules_qs.order_by('loader_id')\
                             .distinct('loader_id')\
                             .values_list('loader_id', flat=True)
        loaders = [
            builder_serializers.LoaderSerializer(loader).data
            for loader in Loader.objects.filter(id__in=loader_ids)
        ]

        # Find mobs that load the item via mob inventory or merchant inventory
        mob_template_ids = list(MobTemplateInventory.objects.filter(
            item_template_id=item_template_pk
        ).values_list('container_id', flat=True))
        merchant_template_ids = list(MerchantInventory.objects.filter(
            item_template_id=item_template_pk
        ).values_list('mob_id', flat=True))
        mob_templates = MobTemplate.objects.filter(
            pk__in=mob_template_ids + merchant_template_ids)
        mob_templates_data = [
            builder_serializers.MobTemplateSerializer(mob_template).data
            for mob_template in mob_templates
        ]

        # Find items that load the item
        item_template_ids = ItemTemplateInventory.objects.filter(
            item_template_id=item_template_pk
        ).values_list('container_id', flat=True)
        item_templates = ItemTemplate.objects.filter(pk__in=item_template_ids)
        item_templates_data = [
            builder_serializers.ItemTemplateSerializer(item_template).data
            for item_template in item_templates
        ]

        return Response({
            'loaders': loaders,
            'mob_templates': mob_templates_data,
            'item_templates': item_templates_data,
        })

item_template_loadsin = ItemTemplateLoadsinView.as_view()


class ItemActionViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.ItemActionSerializer
    pagination_class = None
    queryset = ItemAction.objects.all()

    def get_queryset(self):
        qs = ItemAction.objects.filter(item_template_id=self.kwargs['item_template_pk'])
        return qs

    def perform_create(self, serializer):
        item_template = ItemTemplate.objects.get(
            pk=self.kwargs['item_template_pk'])

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=item_template.id,
                assignment_type=ContentType.objects.get_for_model(ItemTemplate),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to alter this item.")

        action = serializer.save(item_template=item_template)
        return action.item_template

    def perform_update(self, serializer):
        action = serializer.save()
        return action.item_template


item_action_list = ItemActionViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
item_action_detail = ItemActionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class CloneItemAction(BaseWorldBuilderView):

    def post(self, request, world_pk, item_template_pk, pk, format=None):
        action = generics.get_object_or_404(
            ItemAction.objects.all(),
            id=pk)

        new_action = action
        new_action.pk = None
        new_action.save()

        return Response(
            builder_serializers.ItemActionSerializer(new_action).data)

item_action_clone = CloneItemAction.as_view()

# Mob Template

class MobTemplateViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobTemplateSerializer

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        mobs_qs = MobTemplate.objects.filter(world=context)

        # Filter down further if this is a rank 1 builder
        if self.action == 'list' and self._builder_rank <=1:
            mob_template_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(MobTemplate),
            ).values_list('assignment_id', flat=True)
            mobs_qs = mobs_qs.filter(pk__in=mob_template_ids)

        mobs_qs = mobs_qs.order_by('-modified_ts')

        # Filter by mob type (humanoid, beast, plant)
        mob_type = self.request.query_params.get('type', None)
        if mob_type:
            mobs_qs = mobs_qs.filter(type=mob_type)

        mobs_qs = self.char_filters(mobs_qs)
        mobs_qs = self.search_queryset(mobs_qs)

        # Filter by special
        special = self.request.query_params.get('special')
        if special:
            if special == 'is_merchant':
                mobs_qs = mobs_qs.annotate(
                    merchant_inv_count=Count('merchant_inv')
                ).filter(merchant_inv_count__gt=0)
            elif special == 'has_quest':
                mobs_qs = mobs_qs.annotate(
                    quest_count=Count('template_quests')
                ).filter(quest_count__gt=0)
            elif special == 'is_elite':
                mobs_qs = mobs_qs.filter(is_elite=True)

        # Sorting
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            mobs_qs = mobs_qs.order_by(sorting)

        return mobs_qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3:
            return obj

        if (self._builder_rank >= 2 and
            self.action in (
                'retrieve', 'inventory', 'reactions', 'factions', 'quests')):
            return obj

        has_assignment = BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.id,
            assignment_type=ContentType.objects.get_for_model(MobTemplate),
        ).exists()

        if not has_assignment:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this mob.")

        return obj

    def perform_create(self, serializer):

        core_faction = serializer.initial_data.get('core_faction')

        if core_faction:
            try:
                core_faction = Faction.objects.get(
                    world=self.world,
                    code=core_faction,
                    is_core=True)
            except Faction.DoesNotExist:
                raise serializers.ValidationError("Invalid core faction.")

        mob_template = serializer.save(world=self.world)

        if core_faction:
            FactionAssignment.objects.create(
                faction=core_faction,
                member=mob_template,
                value=1)

        # Create a builder assignment if the user is rank 2 or lower
        if self._builder_rank <= 2:
            builder = WorldBuilder.objects.get(user=self.request.user,
                                               world=self.world)
            BuilderAssignment.objects.create(
                builder=builder,
                assignment=mob_template)

    def perform_update(self, serializer):
        core_faction_field_passed_in = 'core_faction' in self.request.data

        mob_template = serializer.save(world=self.world)

        existing_core_faction_assignments = FactionAssignment.objects.filter(
            member_id=mob_template.id,
            member_type=ContentType.objects.get_for_model(mob_template),
            faction__is_core=True,
        ).order_by('-modified_ts', '-created_ts', '-id')
        existing_core_faction_assignment = existing_core_faction_assignments.first()
        if existing_core_faction_assignment:
            existing_core_faction_assignments.exclude(
                id=existing_core_faction_assignment.id).delete()

        if core_faction_field_passed_in:
            core_faction = self.request.data.get('core_faction')
            if core_faction:
                try:
                    faction = Faction.objects.get(
                        world=self.world,
                        code=core_faction,
                        is_core=True)
                except Faction.DoesNotExist:
                    raise serializers.ValidationError("Invalid core faction.")
                # - there was no existing core faction => create
                # - there was a different existing core
                #   faction => delete & create
                # - there was already the same core faction => do nothing
                if (not existing_core_faction_assignment or
                    existing_core_faction_assignment.faction != faction):
                    if existing_core_faction_assignment:
                        existing_core_faction_assignment.delete()
                    FactionAssignment.objects.create(
                        faction=faction,
                        member=mob_template,
                        value = 1)
            elif existing_core_faction_assignment:
                existing_core_faction_assignment.delete()

    def perform_destroy(self, instance):
        if instance.template_mobs.count():
            raise serializers.ValidationError(
                "Cannot delete a template that has loaded mobs.")
        if Quest.objects.filter(
            mob_template=instance).count():
            raise serializers.ValidationError(
                "Cannot delete a template used for a quest.")
        if Objective.objects.filter(
            template_type=ContentType.objects.get_for_model(instance),
            template_id=instance.id,
            qty__gte=1).count():
            raise serializers.ValidationError(
                "Cannot delete a template used for a quest objective.")
        BuilderAssignment.objects.filter(
            assignment_id=instance.id,
            assignment_type=ContentType.objects.get_for_model(MobTemplate)
        ).delete()
        instance.delete()

    @action(detail=False)
    def inventory(self, request, pk, world_pk):
        serializer = builder_serializers.MobTemplateInventorySerializer(
            MobTemplateInventory.objects.filter(
                container=self.get_object()),
            many=True)
        return Response({'data': serializer.data})

    def add_to_inventory(self, request, pk, world_pk):
        mob_template = self.get_object()
        serializer = builder_serializers.AddMobTemplateInventorySerializer(
            container=mob_template,
            data=request.data)
        serializer.is_valid(raise_exception=True)
        mti = serializer.create(serializer.validated_data)
        serializer = builder_serializers.MobTemplateInventorySerializer(mti)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False)
    def reactions(self, request, pk, world_pk):
        serializer = builder_serializers.MobReactionSerializer(
            MobReaction.objects.filter(
                template=self.get_object()),
            many=True)
        return Response({'data': serializer.data})

    def add_reaction(self, request, world_pk, pk):
        mob_template = self.get_object()
        serializer = builder_serializers.AddMobReactionSerializer(
            template=mob_template,
            data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction = serializer.create(serializer.validated_data)
        serializer = builder_serializers.MobReactionSerializer(reaction)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False)
    def factions(self, request, world_pk, pk):
        mob_template = self.get_object()
        factions_qs = FactionAssignment.objects.filter(
            member_type=ContentType.objects.get_for_model(mob_template),
            member_id=mob_template.id)

        is_core = self.request.query_params.get('is_core', None)
        if is_core is not None:
            if is_core.lower() == 'true':
                factions_qs = factions_qs.filter(faction__is_core=True)
            elif is_core.lower() == 'false':
                factions_qs = factions_qs.filter(faction__is_core=False)

        serializer = builder_serializers.MobFactionAssignmentSerializer(
            factions_qs, many=True)
        return Response({'data': serializer.data})

    def add_faction(self, request, world_pk, pk):
        mob_template = self.get_object()

        # if FactionAssignment.objects.filter(
        #         member_type=ContentType.objects.get_for_model(mob_template),
        #         member_id=mob_template.id,
        #         faction__is_core=True).exists():
        #     raise serializers.ValidationError(
        #         'Template already has a core faction association')

        serializer = builder_serializers.MobFactionAssignmentSerializer(
            data=request.data,
            context={'mob_template': mob_template})
        serializer.is_valid(raise_exception=True)
        faction_assignment = serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False)
    def quests(self, request, world_pk, pk):
        mob_template = self.get_object()
        serializer = builder_serializers.QuestSerializer(
            mob_template.template_quests.all(), many=True)
        return Response({'quests': serializer.data})

mob_template_list = MobTemplateViewSet.as_view({
    'get': 'list',
    'post': 'create'})
mob_template_detail = MobTemplateViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})
mob_template_inventory = MobTemplateViewSet.as_view({
    'get': 'inventory',
    'post': 'add_to_inventory'})
mob_template_reactions = MobTemplateViewSet.as_view({
    'get': 'reactions',
    'post': 'add_reaction',
})
mob_template_factions = MobTemplateViewSet.as_view({
    'get': 'factions',
    'post': 'add_faction',
})
mob_template_quests = MobTemplateViewSet.as_view({'get': 'quests'})


class MobTemplateFactionViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobFactionAssignmentSerializer

    def get_queryset(self):
        return FactionAssignment.objects.filter(faction__world=self.world)

mob_template_faction_detail = MobTemplateFactionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})

class MobTemplateReactionViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobReactionSerializer

    def get_queryset(self):
        return MobReaction.objects.filter(template__world=self.world)

mob_template_reaction_detail = MobTemplateReactionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})

class MobTemplateInventoryViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobTemplateInventorySerializer

    def get_queryset(self):
        return MobTemplateInventory.objects.all()

mob_template_inventory_detail = MobTemplateInventoryViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})


class MobTemplateMerchantInventoryViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.MobTemplateMerchantInventorySerializer
    pagination_class = None
    queryset = MerchantInventory.objects.all()

    def get_queryset(self):
        qs = MerchantInventory.objects.filter(
            mob_id=self.kwargs['mob_template_pk'])
        return qs

    def perform_create(self, serializer):
        mob = MobTemplate.objects.get(pk=self.kwargs['mob_template_pk'])

        if self._builder_rank <= 2:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=mob.id,
                assignment_type=ContentType.objects.get_for_model(MobTemplate),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to alter this mob.")

        serializer.save(mob=mob)

mob_template_merchant_inventory_list = MobTemplateMerchantInventoryViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
mob_template_merchant_inventory_detail = MobTemplateMerchantInventoryViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class MobTemplateLoadsinView(BaseWorldBuilderView):
    """
    Given a mob template, return all of the loader information relative
    to it: which rooms, zones and paths it loads in.
    """

    def get(self, request, world_pk, mob_template_pk, format=None):
        """
        Want to return:
        {
            'rooms': [],
            'zones': [],
            'paths': [],
            'loaders': [],
        }

        {
            'rooms': {
                'room_key': {
                    'num_copies': 1,
                    'room_data': <room_data>,
                }, ...
            }
        }
        """

        # Since mob_template_pk could also be key
        if '.' in mob_template_pk:
            mob_template_pk = mob_template_pk.split('.')[1]

        data = {}

        mob_template_ct = ContentType.objects.get_for_model(MobTemplate)
        room_ct = ContentType.objects.get_for_model(Room)
        zone_ct = ContentType.objects.get_for_model(Zone)
        path_ct = ContentType.objects.get_for_model(Path)

        # Find all rules that load the mob template
        rules_qs = Rule.objects.filter(
            loader__world_id=world_pk,
            template_type=mob_template_ct,
            template_id=mob_template_pk)

        # Find all the rules that load this mob into a room, and get the
        # room IDs (since they are the target), as well as how many copies
        # are loaded by that rule.
        rules_data = rules_qs.filter(
            target_type=room_ct,
        ).values_list('target_id', 'num_copies')

        # We do a pass through the rules data to extract two things:
        # 1) the list of room PKs
        # 2) the cumulative count for each room
        room_pks = []
        room_counts = collections.defaultdict(int)
        for room_pk, num_copies in rules_data:
            room_pks.append(room_pk)
            room_counts[room_pk] += num_copies

        # Now all the serialized room data before augmenting it with the
        # room counts
        rooms_data = builder_serializers.MapRoomSerializer(
            Room.objects.filter(pk__in=room_pks),
            context={'request': request},
            many=True
        ).data

        rooms_data_with_counts = {}
        for room_data in rooms_data:
            rooms_data_with_counts[room_data['key']] = {
                'num_copies': room_counts[room_data['id']],
                'room_data': room_data,
            }

        # Find zone rules
        zones_data = rules_qs.filter(
            target_type=zone_ct,
        ).values_list('target_id', 'num_copies')

        zone_pks = []
        zone_counts = collections.defaultdict(int)
        for zone_pk, num_copies in zones_data:
            zone_pks.append(zone_pk)
            zone_counts[zone_pk] += num_copies

        zones_data = builder_serializers.ZoneBuilderSerializer(
            Zone.objects.filter(pk__in=zone_pks),
            context={'request': request},
            many=True
        ).data

        zones_data_with_couts = {}
        for zone_data in zones_data:
            zones_data_with_couts[zone_data['key']] = {
                'num_copies': zone_counts[zone_data['id']],
                'zone_data': zone_data,
            }

        # Find path rules
        paths_data = rules_qs.filter(
            target_type=path_ct,
        ).values_list('target_id', 'num_copies')

        path_pks = []
        path_counts = collections.defaultdict(int)
        for path_pk, num_copies in paths_data:
            path_pks.append(path_pk)
            path_counts[path_pk] += num_copies

        paths_data = builder_serializers.PathListSerializer(
            Path.objects.filter(pk__in=path_pks),
            many=True
        ).data

        paths_data_with_couts = {}
        for path_data in paths_data:
            paths_data_with_couts[path_data['key']] = {
                'num_copies': path_counts[path_data['id']],
                'path_data': path_data,
            }

        # Get all loaders that load the template
        loader_ids = rules_qs.order_by('loader_id')\
                             .distinct('loader_id')\
                             .values_list('loader_id', flat=True)
        loaders = [
            builder_serializers.LoaderSerializer(loader).data
            for loader in Loader.objects.filter(id__in=loader_ids)
        ]

        return Response({
            'rooms': rooms_data_with_counts,
            'zones': zones_data_with_couts,
            'paths': paths_data_with_couts,
            'loaders': loaders,
        })

mob_template_loadsin = MobTemplateLoadsinView.as_view()


# Loader

class LoaderViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.LoaderSerializer

    def get_queryset(self):
        qs = Loader.objects.filter(world=self.world)
        qs = apply_zone_filter(qs, self.request)
        return qs.order_by('-created_ts')

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this loader.")

        return obj

    def perform_create(self, serializer):
        serializer.save(world=self.world)

loader_list = LoaderViewSet.as_view({
    'get': 'list',
    'post': 'create'})
loader_detail = LoaderViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})


# Rule

class RuleViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.RuleSerializer

    def get_queryset(self):
        rules_qs = Rule.objects.filter(loader=self.loader)

        if self._builder_rank < 2:
            zone_ids = BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).values_list('assignment_id', flat=True)
            rules_qs = rules_qs.filter(loader__zone_id__in=zone_ids)

        return rules_qs.order_by('order')

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.loader.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this loader.")

        return obj

    def perform_create(self, serializer):
        if self._builder_rank < 3:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=self.loader.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this loader.")

        serializer.save(loader=self.loader)

    def initialize_request(self, *args, **kwargs):
        request = super().initialize_request(*args, **kwargs)

        loader_pk = self.kwargs['loader_pk']
        try:
            loader_pk = loader_pk.split('.')[1]
        except (ValueError, IndexError): pass

        self.loader = generics.get_object_or_404(
            Loader.objects.filter(world=self.world),
            pk=loader_pk)

        return request


rule_list = RuleViewSet.as_view({
    'get': 'list',
    'post': 'create'})
rule_detail = RuleViewSet.as_view({
    'get': 'retrieve',
    'put': 'partial_update',
    'delete': 'destroy'})


# Quest

class QuestViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.QuestSerializer

    def get_queryset(self):
        qs = Quest.objects.filter(world=self.world).order_by(
            '-level',
            '-created_ts')
        qs = self.search_queryset(qs)
        return qs

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to edit this quest.")

        return obj

    def add_objective(self, request, pk, world_pk):
        quest = self.get_object()
        serializer = builder_serializers.ObjectiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        objective = serializer.create(
            validated_data=serializer.validated_data,
            quest=quest)
        serializer = builder_serializers.ObjectiveSerializer(objective)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def add_reward(self, request, pk, world_pk):
        quest = self.get_object()

        if self._builder_rank < 3:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=quest.zone.id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to this quest.")

        serializer = builder_serializers.RewardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        objective = serializer.create(
            validated_data=serializer.validated_data,
            quest=quest)
        serializer = builder_serializers.RewardSerializer(objective)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):

        if self._builder_rank < 3:
            if not BuilderAssignment.objects.filter(
                builder__user=self.request.user,
                assignment_id=serializer.validated_data['zone'].id,
                assignment_type=ContentType.objects.get_for_model(Zone),
            ).exists():
                raise drf_exceptions.PermissionDenied(
                    "You do not have permission to edit this zone.")

        serializer.save()

    def perform_update(self, serializer):
        quest = serializer.save()
        quest.update_live_instances()
        return quest


quest_list = QuestViewSet.as_view({'get': 'list', 'post': 'create'})
quest_detail = QuestViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})
objective_list = QuestViewSet.as_view({
    'post': 'add_objective',
})
reward_list = QuestViewSet.as_view({
    'post': 'add_reward',
})

class QuestObjectiveViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ObjectiveSerializer
    queryset = Objective.objects.all()

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.quest.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this objective.")

        return obj

    def perform_update(self, serializer):
        objective = serializer.save()
        objective.quest.update_live_instances()
        return objective

objective_detail = QuestObjectiveViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class RewardObjectiveViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.RewardSerializer
    queryset = Reward.objects.all()

    def get_object(self):
        obj = super().get_object()

        if self._builder_rank >= 3: return obj
        if self._builder_rank >= 2 and self.action == 'retrieve': return obj

        if not BuilderAssignment.objects.filter(
            builder__user=self.request.user,
            assignment_id=obj.quest.zone.id,
            assignment_type=ContentType.objects.get_for_model(Zone),
        ).exists():
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to this reward.")

        return obj

    def perform_update(self, serializer):
        reward = serializer.save()
        reward.quest.update_live_instances()
        return reward

reward_detail = RewardObjectiveViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

# Path

class PathViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.PathDetailsSerializer

    def get_queryset(self):
        return Path.objects.filter(world=self.world).order_by(
            '-created_ts')

    def add_room(self, request, world_pk, pk):
        path = self.get_object()
        serializer = builder_serializers.AddPathRoomSerializer(
            data=request.data,
            context={
            'path': path,
            'view': self,
            })
        serializer.is_valid(raise_exception=True)
        path_room = serializer.create(serializer.validated_data)
        path.update_live_instances()
        return Response(
            builder_serializers.PathRoomSerializer(path_room).data,
            status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        if Rule.objects.filter(
            target_type=ContentType.objects.get_for_model(Path),
            target_id=instance.id).count():
            raise serializers.ValidationError(
                "Cannot delete a path that has rules loading it.")
        PathRoom.objects.filter(path=instance).delete()
        instance.delete()

    @action(detail=False)
    def rooms(self, request, world_pk, pk):
        path = self.get_object()
        data = builder_serializers.PathRoomSerializer(
            PathRoom.objects.filter(path=path), many=True).data
        return Response({'data': data})


path_detail = PathViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})
path_rooms = PathViewSet.as_view({
    'post': 'add_room',
    'get': 'rooms',
})

class PathRoomViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.PathRoomSerializer

    def get_queryset(self):
        return PathRoom.objects.all()

    def perform_destroy(self, instance):
        path = instance.path
        instance.delete()
        path.update_live_instances()


path_room_detail = PathRoomViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

# World Config

class RandomItemProfileViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.RandomItemProfileSerializer
    pagination_class = None

    def get_queryset(self):
        qs = self.search_queryset(
            RandomItemProfile.objects.filter(world=self.world)
        ).order_by('level')
        return qs

    def perform_create(self, serializer):
        serializer.save(world=self.world)

    def perform_destroy(self, instance):
        if Reward.objects.filter(
            profile_type=ContentType.objects.get_for_model(instance),
            profile_id=instance.id).count():
            raise serializers.ValidationError(
                "Cannot delete a profile used for a quest reward.")
        instance.delete()


random_item_profile_list = RandomItemProfileViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
random_item_profile_detail = RandomItemProfileViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})

class TransformationTemplateViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.TransformationTemplateSerializer
    pagination_class = None
    queryset = TransformationTemplate.objects.all()

    def get_queryset(self):
        qs = TransformationTemplate.objects.filter(world=self.world)
        qs = self.search_queryset(qs)
        return qs

    def perform_create(self, serializer):
        serializer.save(world=self.world)

transformation_template_list = TransformationTemplateViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
transformation_template_detail = TransformationTemplateViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class StartingEqViewSet(BaseWorldBuilderViewSet):

    serializer_class = world_serializers.StartingEqSerializer
    pagination_class = None
    queryset = StartingEq.objects.all()

    def perform_create(self, serializer):
        serializer.save(worldconfig=self.world.config)

    def get_queryset(self):
        return StartingEq.objects.filter(worldconfig=self.world.config)



starting_eq_list = StartingEqViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
starting_eq_detail = StartingEqViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class WorldBuilderViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.WorldBuilderSerializer
    pagination_class = None

    def get_queryset(self):
        qs = WorldBuilder.objects.filter(world=self.world)
        return qs

    def perform_create(self, serializer):
        if self._builder_rank < 3:
            raise serializers.ValidationError(
                "Only rank 3 builders can add other builders.")

        if serializer.validated_data.get('builder_rank', 1) >= 3 and self._builder_rank < 4:
            raise serializers.ValidationError(
                "Only rank 4 builders can add rank 3+ builders.")

        try:
            user = serializer.validated_data['user']
        except KeyError:
            raise serializers.ValidationError('User is required.')
        if user and WorldBuilder.objects.filter(world=self.world, user=user):
            raise serializers.ValidationError(
                "User is already a builder for this world.")
        serializer.save(world=self.world)

    def perform_update(self, serializer):
        if serializer.validated_data.get('builder_rank', 1) >= 3 and self._builder_rank < 4:
            raise serializers.ValidationError(
                "Only rank 4 builders can set builder ranks above 2.")
        super().perform_update(serializer)

builder_list = WorldBuilderViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
builder_detail = WorldBuilderViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


# Ref Lookup

class RefLookup(APIView):
    """
    Highly heuristic view for looking up various resources via reference
    lookups in the UI.
    """

    def get(self, request, world_key, format=None):
        world_id = world_key.split('.')[1]

        resource = request.GET.get('resource')
        query = request.GET.get('query', '').lower()
        context = request.GET.get('context', '').lower()
        limit = request.GET.get('limit', 7)
        data = []

        resource_to_model = {
            'zone': Zone,
            'item_template': ItemTemplate,
            'mob_template': MobTemplate,
            'transformation_template': TransformationTemplate,
            'room': Room,
            'rule': Rule,
            'path': Path,
        }

        qs = None
        serializer = KeyNameSerializer

        if '.' in query:
            cls, rid = query.split('.')
            model = resource_to_model[cls]

            try:
                if cls in ('zone', 'room', 'path'):
                    qs = model.objects.filter(
                        world_id=world_id,
                        relative_id=rid)
                else:
                    qs = model.objects.filter(pk=rid)
            except (ObjectDoesNotExist, ValueError):
                    pass
        else:

            kwargs = {}
            if context:
                keyword, value = context.split('.')
                if keyword == 'zone':
                    kwargs['zone__relative_id'] = value
                elif keyword == 'loader':
                    if resource == 'room':
                        loader = Loader.objects.get(pk=value)

                        # Only show rooms that are in the loader's zone
                        if loader.zone:
                            kwargs['zone_id'] = loader.zone.id

                        # If there is a query, query by room name
                        if query:
                            kwargs['name__icontains'] = query
                    elif resource == 'path':
                        pass
                    else:
                        kwargs['loader__id'] = value

            elif query:
                kwargs['name__icontains'] = query

            if resource not in ('rule', 'transformation_template'):
                kwargs['world_id'] = world_id

            qs = resource_to_model[resource].objects.filter(**kwargs)

        if qs:
            return Response({'data': serializer(qs[0:10], many=True).data})
        else:
            return Response({'data': []})


class SuggestMob(APIView):

    def post(self, request, format=None):
        serializer = builder_serializers.SuggestMobSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            suggest_stats(
                level=serializer.validated_data['level'],
                archetype=serializer.validated_data['archetype']))

suggest_mob = SuggestMob.as_view()


class UserViewSet(BaseWorldBuilderViewSet):
    "Users who have created a character in a game."

    serializer_class = builder_serializers.UserSerializer

    def get_queryset(self):
        # get list of user IDs in a world
        user_ids = Player.objects.filter(
            world__context=self.world
        ).values_list('user_id', flat=True).distinct()
        qs = User.objects.filter(
            id__in=user_ids,
        ).exclude(
            username__isnull=True
        ).exclude(
            username='')
        qs = self.search_queryset(qs, 'username')

        context = self.request.query_params.get('context')
        if context and context == 'add_builder':
            # get list of users who are not a builder on this world
            user_ids = WorldBuilder.objects.filter(
                world=self.world
            ).values_list('user_id', flat=True)
            qs = qs.exclude(id__in=user_ids)

            if not qs.count():
                query = self.request.query_params.get('query')
                if query:
                    qs = User.objects.filter(email__iexact=query)

        return qs

user_list = UserViewSet.as_view({
    'get': 'list',
})

# Player

class PlayerListViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.PlayerListSerializer
    queryset = Player.objects.all()

    def get_queryset(self):
        qs = Player.objects.filter(
            world__context=self.world).order_by('-created_ts')
        qs = self.search_queryset(qs)

        qs = self.char_filters(qs)

        return qs

class PlayerDetailViewSet(PlayerListViewSet):
    serializer_class = builder_serializers.PlayerDetailSerializer

    @action(detail=False)
    def reset(self, request, world_pk, pk):
        player = get_object_or_404(
            Player,
            pk=pk,
            world__context=self.world)
        player = player.reset()
        return Response(self.serializer_class(
            player, context={'request': request}).data)

player_list = PlayerListViewSet.as_view({'get': 'list'})
player_detail = PlayerDetailViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})
player_reset = PlayerDetailViewSet.as_view({
    'post': 'reset',
})


class ProcessionViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.ProcessionSerializer
    pagination_class = None

    def get_queryset(self):
        processions_qs = Procession.objects.filter(
            room__zone_id=self.kwargs['zone_pk'])
        return processions_qs

    def perform_create(self, serializer):
        procession = serializer.save()
        procession.update_live_instances()
        procession.room.flags.create(
            code=adv_consts.ROOM_FLAG_PEACEFUL,
            room=procession.room)
        procession.room.update_live_instances()
        return procession

    def perform_update(self, serializer):
        original_room = self.get_object().room
        procession = serializer.save()
        procession.update_live_instances()
        if procession.room != original_room:
            original_room.flags.filter(
                code=adv_consts.ROOM_FLAG_PEACEFUL).delete()
            original_room.update_live_instances()
            procession.room.flags.create(
                code=adv_consts.ROOM_FLAG_PEACEFUL,
                room=procession.room)
            procession.room.update_live_instances()
        return procession

    def perform_destroy(self, instance):
        instance.room.flags.filter(
            code=adv_consts.ROOM_FLAG_PEACEFUL).delete()
        super().perform_destroy(instance)

procession_list = ProcessionViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
procession_detail = ProcessionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class FactList(APIView):

    def get(self, request, world_id, format=None):
        world = generics.get_object_or_404(
            World.objects.all(),
            id=world_id)

        # Make sure we're dealing with a spawn world
        if not world.context:
            world = world.spawned_worlds.first()

        try:
            world_facts = json.loads(world.facts) or {}
        except TypeError:
            world_facts = {}

        facts = [
            {
                'fact': fact,
                'value': world_facts[fact]
            } for fact in sorted(world_facts.keys())
        ]

        return Response(facts)


class FactScheduleViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.FactScheduleSerializer

    def get_queryset(self):
        return FactSchedule.objects.filter(world_id=self.kwargs['world_pk'])

    def perform_create(self, serializer):
        serializer.save(world=self.world)

fact_schedule_list = FactScheduleViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
fact_schedule_details = FactScheduleViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class SkillViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.SkillDetailSerializer

    def get_queryset(self):
        return Skill.objects.filter(world=self.world).order_by('-created_ts')

    def update_live_instances(self, world):
        return

    def perform_create(self, serializer):
        serializer.save(world=self.world)
        self.update_live_instances(self.world)

    def perform_update(self, serializer):
        skill = serializer.save()
        self.update_live_instances(self.world)
        return self.world


skill_list = SkillViewSet.as_view({'get': 'list', 'post': 'create'})
skill_detail = SkillViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'
})


class WorldIntanceViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.WorldSerializer

    def get_queryset(self):
        return World.objects.filter(
            instance_of=self.world
        ).exclude(
            lifecycle=api_consts.WORLD_STATE_ARCHIVED
        ).order_by('-created_ts')

instance_list = WorldIntanceViewSet.as_view({'get': 'list', 'post': 'create'})


class WorldReviewViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.WorldReviewSerializer

    def get_queryset(self):
        return WorldReview.objects.filter(world=self.world)

    def perform_create(self, serializer):
        # Check that there are no other submitted reviews
        if WorldReview.objects.filter(
            world=self.world,
            status=api_consts.WORLD_REVIEW_STATUS_SUBMITTED):
            raise serializers.ValidationError(
                'Only one review can be submitted at a time.')

        # Check that it's been long enough since the last rejection
        # if applicable.
        last_rejection = WorldReview.objects.filter(
            world=self.world,
            status=api_consts.WORLD_REVIEW_STATUS_REVIEWED
        ).order_by('-created_ts').first()
        if last_rejection:
            delta = (timezone.now() - last_rejection.created_ts).days
            if delta < 30:
                raise serializers.ValidationError(
                    'Cannot resubmit for another {} days.'.format(30 - delta))

        serializer.save(world=self.world)

    @action(detail=True, methods=['post'], url_path='claim')
    def claim_review(self, request, world_pk, pk):
        if not request.user.is_staff:
            raise drf_exceptions.PermissionDenied('Only staff can claim reviews.')

        review = self.get_object()

        if review.status != api_consts.WORLD_REVIEW_STATUS_SUBMITTED:
            raise serializers.ValidationError(
                'Only submitted reviews can be claimed.')

        review.reviewer = request.user
        review.save()

        return Response(
            self.serializer_class(review).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve_review(self, request, world_pk, pk):
        if not request.user.is_staff:
            raise drf_exceptions.PermissionDenied('Only staff can resolve reviews.')

        review = self.get_object()

        if review.status != api_consts.WORLD_REVIEW_STATUS_SUBMITTED:
            raise serializers.ValidationError(
                'Only submitted reviews can be resolved.')

        _status = request.data.get('status')
        text = request.data.get('text')

        if _status not in [
            api_consts.WORLD_REVIEW_STATUS_APPROVED,
            api_consts.WORLD_REVIEW_STATUS_REVIEWED]:
            raise drf_exceptions.ValidationError(
                "Reviews can only be resolved into either 'approved' or 'reviewed'.")

        if _status == api_consts.WORLD_REVIEW_STATUS_REVIEWED and not text:
            raise drf_exceptions.ValidationError(
                "A review must have a text field if it's not approved.")

        review.status = _status
        if text:
            review.text = text
        review.save()

        return Response(
            self.serializer_class(review).data,
            status=status.HTTP_201_CREATED)

review_list = WorldReviewViewSet.as_view({'get': 'list', 'post': 'create'})
review_detail = WorldReviewViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})


class BuilderAssignmentViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.BuilderAssignmentSerializer

    def get_queryset(self):
        return BuilderAssignment.objects.filter(builder_id=self.kwargs['builder_pk'])

    def perform_create(self, serializer):
        builder_id = self.kwargs.get('builder_pk')  # Adjust if necessary
        builder = get_object_or_404(WorldBuilder, pk=builder_id)
        serializer.save(builder=builder)

builder_assignment_list = BuilderAssignmentViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
builder_assignment_details = BuilderAssignmentViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


class SocialViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.SocialSerializer

    def get_queryset(self):
        return Social.objects.filter(world=self.world).order_by('cmd')

    def perform_create(self, serializer):
        serializer.is_valid(raise_exception=True)
        serializer.save(world=self.world)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['world'] = self.world  # Add world to the context
        return context

social_list = SocialViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
social_details = SocialViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})


# Player Restoration

class PlayerRestore(BaseWorldBuilderView):

    def get(self, request, world_pk, pk):
        player = generics.get_object_or_404(
            Player.objects.filter(world__context=self.world),
            id=pk)

        # Equipment items
        deleted_eq_qs = player.equipment.inventory.filter(
            is_pending_deletion=True
        ).prefetch_related(
            'template',
        ).order_by(
            '-pending_deletion_ts')
        equipment = []
        for eq in deleted_eq_qs:
            equipment_data = {
                'id': eq.id,
                'name': eq.template.name if eq.template else eq.name,
                'type': eq.template.type if eq.template else eq.type,
                'template_id': eq.template_id,
                'pending_deletion_ts': eq.pending_deletion_ts,
                'contains': [],
            }
            equipment_data['contains'] = [
                {
                    'id': contained_item.id,
                    'name': (
                        contained_item.template.name
                        if contained_item.template
                        else contained_item.name),
                    'type': (
                        contained_item.type if contained_item.template
                        else contained_item.type),
                    'template_id': contained_item.template_id,
                }
                for contained_item
                in eq.inventory.prefetch_related('template')
            ]
            equipment.append(equipment_data)

        # Inventory items
        deleted_items_qs = player.inventory.filter(
            is_pending_deletion=True
        ).prefetch_related(
            'template',
        ).order_by(
            '-pending_deletion_ts')

        items = []
        for item in deleted_items_qs:
            type = item.template.type if item.template else item.type
            item_data = {
                'id': item.id,
                'name': item.template.name if item.template else item.name,
                'type': type,
                'template_id': item.template_id,
                'pending_deletion_ts': item.pending_deletion_ts,
                'contains': [],
            }
            item_data['contains'] = [
                {
                    'id': contained_item.id,
                    'name': (
                        contained_item.template.name
                        if contained_item.template
                        else contained_item.name),
                    'type': (
                        contained_item.type if contained_item.template
                        else contained_item.type),
                    'template_id': contained_item.template_id,
                }
                for contained_item
                in item.inventory.prefetch_related('template')
            ]
            items.append(item_data)

        return Response({
            'player': {
                'id': player.id,
                'name': player.name,
            },
            'eq': equipment,
            'items': items,
        })

    def post(self, request, world_pk, pk):
        player = generics.get_object_or_404(
            Player.objects.filter(world=self.world),
            id=pk)
        if player.in_game:
            raise drf_exceptions.ValidationError(
                'Cannot restore gear for a player in-game.')
        player.restore_gear()
        return Response({}, status=status.HTTP_201_CREATED)

player_restore = PlayerRestore.as_view()


class PlayerRestoreItem(BaseWorldBuilderView):

    def post(self, request, world_pk, player_pk, pk):
        player = generics.get_object_or_404(
            Player.objects.all(),
            id=player_pk)
        if player.in_game:
            raise drf_exceptions.ValidationError(
                'Cannot restore gear for a player in-game.')
        player.restore_gear(item_id=pk)
        return Response([pk], status=status.HTTP_201_CREATED)

player_restore_item = PlayerRestoreItem.as_view()


class CurrencyViewSet(BaseWorldBuilderViewSet):

    serializer_class = builder_serializers.CurrencySerializer

    def get_queryset(self):
        return Currency.objects.filter(world=self.world)

    def perform_create(self, serializer):

        # Don't alter any currencies to a running world
        if World.objects.filter(
            context=self.world,
            lifecycle=api_consts.WORLD_STATE_RUNNING).count():
            raise drf_exceptions.ValidationError(
                'Cannot add currencies to a running world.')

        # Currencies should only be set at the base world level
        if self.world.instance_of:
            raise drf_exceptions.ValidationError(
                'Cannot add currencies to an instance world.')

        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get('is_default', False):
            # Unset any other default currency for the same world
            Currency.objects.filter(
                world=self.world, is_default=True
            ).update(is_default=False)
        serializer.save(world=self.world)

    def perform_update(self, serializer):

        # Don't alter any currencies to a running world
        if World.objects.filter(
            context=self.world,
            lifecycle=api_consts.WORLD_STATE_RUNNING).count():
            raise drf_exceptions.ValidationError(
                'Cannot alter currencies in a running world.')

        serializer.is_valid(raise_exception=True)

        # Check if the new value of 'is_default' is True
        if serializer.validated_data.get('is_default', False):
            # Only update if the current instance is not already the default
            if not serializer.instance.is_default:
                # Unset any other default currency for the same world
                Currency.objects.filter(
                    world=self.world, is_default=True
                ).exclude(
                    id=serializer.instance.id
                ).update(is_default=False)

        # Make sure that the 'gold' and 'medals' currencies can't have their
        # codes altered.
        if ('code' in serializer.validated_data and
            serializer.instance.code in ('gold', 'medals') and
            serializer.validated_data['code'] != serializer.instance.code):
            raise drf_exceptions.ValidationError(
                'Cannot alter the code of the gold or medals currency.')

        # Save the instance with the new data
        serializer.save()

    def perform_destroy(self, instance):
        # Don't alter any currencies to a running world
        if World.objects.filter(
            context=self.world,
            lifecycle=api_consts.WORLD_STATE_RUNNING).count():
            raise drf_exceptions.ValidationError(
                'Cannot delete currencies in a running world.')

        # Currencies should only be set at the base world level
        if self.world.instance_of:
            raise drf_exceptions.ValidationError(
                'Cannot delete currencies in an instance world.')

        if instance.code in ('gold', 'medals'):
            raise drf_exceptions.ValidationError(
                'Cannot delete the gold or medals currency.')

        super().perform_destroy(instance)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['world'] = self.world  # Add world to the context
        return context


currency_list = CurrencyViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
currency_details = CurrencyViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy',
})
