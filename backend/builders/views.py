import copy
import collections
import json

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models.deletion import RestrictedError
from django.db.models import Count, Prefetch, Q
from django.db import transaction
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
from builders.balance.mob_suggestions import suggest_mob_definition_manifest
from builders.balance.power_analysis import (
    analyze_item_definition_power,
    analyze_mob_definition_power,
)
from core.utils.mobs import suggest_stats

from config import constants as api_consts
from config import game_settings as adv_config
from core.abilities import definition_world
from core.economy import economy_world
from core.leveling import LevelingConfigError
from core.scoped_state import STATE_SCOPE_WORLD, get_state_snapshot
from core.serializers import KeyNameSerializer, ReferenceField
from core.view_mixins import (
    KeyedRetrieveMixin,
    RequestDataMixin,
    WorldValidatorMixin)
from lobby.cache import LOBBY_FIXED_SECTIONS_CACHE_KEY

from builders import manifests as builder_manifests
from builders import permissions as builder_permissions
from builders import serializers as builder_serializers
from builders import world_export as builder_world_export
from quests import manifests as quest_manifests
from builders.models import (
    AbilityDefinition,
    BuilderAction,
    BuilderAssignment,
    Currency,
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingProfileRecipe,
    CraftingRecipe,
    LastViewedRoom,
    ItemBundle,
    ItemDefinition,
    MobDefinition,
    MerchantProfile,
    SpawnPlan,
    RoomAction,
    Trigger,
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
from worlds.models import (
    World, Room, Zone, RoomFlag, RoomDetail, Door)
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
                lookup = '%s__icontains' % field_name
                query_filter = Q(**{lookup: query})
                if any(field.name == 'slug' for field in qs.model._meta.concrete_fields):
                    query_filter |= Q(slug__icontains=query)
                qs = qs.filter(query_filter)

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
        cache.delete(LOBBY_FIXED_SECTIONS_CACHE_KEY)
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
        propagated_fields = (
            "name",
            "short_description",
            "description",
            "motd",
            "is_public",
        )
        updates_for_spawns = {}
        for field_name in propagated_fields:
            if field_name in serializer.validated_data:
                updates_for_spawns[field_name] = serializer.validated_data[field_name]
        if updates_for_spawns:
            world.spawned_worlds.update(**updates_for_spawns)
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


def _get_builder_admin_spawn_world(root_world, pk):
    return get_object_or_404(
        World.objects.select_related(
            'context',
            'context__instance_of',
            'leader',
            'worldlocks',
        ),
        Q(context=root_world) | Q(context__instance_of=root_world),
        pk=pk,
    )


class WorldAdminInstance(BaseWorldBuilderView):
    def get(self, request, world_pk, pk):
        spawn_world = _get_builder_admin_spawn_world(self.world, pk)
        return Response(
            builder_serializers.WorldAdminInstanceSerializer(spawn_world).data)

world_admin_instance = WorldAdminInstance.as_view()


class WorldAdminInstanceReset(BaseWorldBuilderView):
    def post(self, request, world_pk, pk):
        spawn_world = _get_builder_admin_spawn_world(self.world, pk)
        WorldSmith(spawn_world).reset()
        return Response(
            builder_serializers.WorldAdminInstanceSerializer(spawn_world).data
        )


world_admin_instance_reset = WorldAdminInstanceReset.as_view()


class WorldAdminInstanceRecover(BaseWorldBuilderView):
    def post(self, request, world_pk, pk):
        spawn_world = _get_builder_admin_spawn_world(self.world, pk)
        WorldSmith(spawn_world).recover_to_stopped()
        spawn_world.refresh_from_db()
        return Response(
            builder_serializers.WorldAdminInstanceSerializer(spawn_world).data
        )


world_admin_instance_recover = WorldAdminInstanceRecover.as_view()


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

    def _serialize_faction_response(self, faction):
        payload = builder_manifests.serialize_faction_payload(faction)
        payload["modified_ts"] = faction.modified_ts
        payload["model_type"] = faction.model_type
        return payload

    def get_queryset(self):
        world = self.world.instance_of or self.world

        factions_qs = Faction.objects.filter(world=world)

        faction_type = self.request.query_params.get('type', None)
        if faction_type:
            factions_qs = factions_qs.filter(type=faction_type)

        playable = self.request.query_params.get('playable', None)
        if playable in ('true', '1'):
            factions_qs = factions_qs.filter(playable=True)
        elif playable in ('false', '0'):
            factions_qs = factions_qs.filter(playable=False)

        is_core = self.request.query_params.get('is_core', None)
        if is_core is not None:
            if is_core.lower() == 'true':
                factions_qs = factions_qs.filter(is_core=True)
            elif is_core.lower() == 'false':
                factions_qs = factions_qs.filter(is_core=False)

        factions_qs = self.search_queryset(factions_qs)

        return factions_qs

    def retrieve(self, request, *args, **kwargs):
        faction = self.get_object()
        return Response(self._serialize_faction_response(faction))

    def update_live_instances(self, world):
        return

    def perform_create(self, serializer):
        serializer.save(world=self.world)
        self.update_live_instances(self.world)

    def perform_update(self, serializer):
        serializer.save()
        self.update_live_instances(self.world)
        return self.world

    def perform_destroy(self, instance):
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

    def retrieve(self, request, *args, **kwargs):
        return Response(
            builder_manifests.serialize_world_config_payload(world=self.world)
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["world"] = self.world
        return context

    def perform_update(self, serializer):
        config = serializer.save()
        if self.world.instance_of_id:
            return config
        if config.is_narrative:
            config.allow_combat = False
        else:
            config.allow_combat = True
        config.save(update_fields=["allow_combat"])
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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['include_manifest_yaml'] = self.action in [
            'create',
            'retrieve',
            'update',
            'partial_update',
        ]
        return context

    def get_queryset(self):
        order_by = self.request.query_params.get(
            'sort_by',
            self.request.query_params.get('order_by', 'name'))
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
            'spawn_plans',
            'spawn_plan_detail',
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
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            qs = qs.order_by(sorting)
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
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            qs = qs.order_by(sorting)
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
    def create_path(self, request, world_pk, pk):
        zone = self.get_object()
        serializer = builder_serializers.PathDetailsSerializer(
            data=request.data,
            context={'zone': zone})
        serializer.is_valid(raise_exception=True)
        path = serializer.save()
        path.update_live_instances()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True)
    def spawn_plans(self, request, world_pk, pk):
        zone = self.get_object()
        qs = zone.spawn_plans.select_related('zone').prefetch_related('entries')

        query = self.request.query_params.get('query')
        if query:
            try:
                qs = qs.filter(pk=int(query))
            except ValueError:
                qs = qs.filter(Q(name__icontains=query) | Q(slug__icontains=query))

        sorting = self.request.query_params.get('sort_by')
        if sorting:
            qs = qs.order_by(sorting)
        else:
            qs = qs.order_by('order', 'created_ts', 'id')

        spawn_plans = [
            builder_world_export.serialize_spawn_plan_payload(
                spawn_plan,
                include_yaml=False,
            )
            for spawn_plan in qs
        ]
        page = self.paginate_queryset(spawn_plans)
        if page is None:
            return Response({
                'count': len(spawn_plans),
                'results': spawn_plans,
                'spawn_plans': spawn_plans,
            })

        response = self.get_paginated_response(page)
        response.data['spawn_plans'] = spawn_plans
        return response

    @action(detail=True)
    def spawn_plan_detail(self, request, world_pk, pk, spawn_plan_pk):
        zone = self.get_object()
        spawn_plan = get_object_or_404(
            zone.spawn_plans.select_related('zone').prefetch_related('entries'),
            pk=spawn_plan_pk,
        )
        return Response(
            builder_world_export.serialize_spawn_plan_payload(spawn_plan)
        )

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
zone_spawn_plans = ZoneBuilderViewSet.as_view({
    'get': 'spawn_plans',
})
zone_spawn_plan_detail = ZoneBuilderViewSet.as_view({
    'get': 'spawn_plan_detail',
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
        if self._builder_rank >= 2 and self.action in ['retrieve', 'manifest']: return obj

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

    def manifest(self, request, *args, **kwargs):
        room = self.get_object()
        return Response(
            builder_world_export.serialize_room_manifest_payload(room)
        )

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
room_manifest = RoomBuilderDetailViewSet.as_view({
    'get': 'manifest',
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


class RoomSpawnPlansView(BaseWorldBuilderView):

    def get(self, request, world_pk, room_pk, format=None):
        if '.' in room_pk:
            room = Room.objects.get(
                world_id=world_pk,
                relative_id=room_pk.split('.')[1])
        else:
            room = Room.objects.get(pk=room_pk)

        path_ids = set(
            PathRoom.objects.filter(room=room).values_list('path_id', flat=True)
        )
        room_refs = {
            room.key,
            f"room.{room.id}",
            f"room@{room.x},{room.y},{room.z}",
            room.name,
        }
        zone_refs = {
            room.zone.key,
            f"zone.{room.zone.id}",
            f"zone@{room.zone.relative_id}",
            room.zone.name,
        } if room.zone_id else set()
        path_refs = {f"path@{path_id}" for path_id in path_ids}
        spawn_plan_payloads = []
        for spawn_plan in room.world.spawn_plans.prefetch_related('entries').order_by(
            'order', 'created_ts', 'id'
        ):
            matching_entries = []
            for entry in spawn_plan.entries.all():
                target = entry.target if isinstance(entry.target, dict) else {}
                if (
                    target.get('room') in room_refs
                    or target.get('room_ref') in room_refs
                    or target.get('zone') in zone_refs
                    or target.get('path') in path_refs
                ):
                    matching_entries.append(entry.slug)
            if matching_entries:
                payload = builder_world_export.serialize_spawn_plan_payload(
                    spawn_plan,
                    include_yaml=False,
                )
                payload['matching_entries'] = matching_entries
                spawn_plan_payloads.append(payload)

        return Response({
            'spawn_plans': spawn_plan_payloads,
        })

room_spawn_plans = RoomSpawnPlansView.as_view()


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


def _serialize_builder_trigger_response(trigger):
    payload = builder_manifests.serialize_trigger_manifest(trigger)
    manifest_spec = payload["manifest"].get("spec") or {}
    payload.update({
        "conditions": manifest_spec.get("conditions", ""),
        "script": manifest_spec.get("script", ""),
        "show_details_on_failure": bool(manifest_spec.get("show_details_on_failure")),
        "failure_message": manifest_spec.get("failure_message", ""),
        "display_action_in_room": bool(manifest_spec.get("display_action_in_room")),
        "gate_delay": int(manifest_spec.get("gate_delay") or 0),
        "order": int(manifest_spec.get("order") or 0),
        "is_active": bool(manifest_spec.get("is_active")),
        "created_ts": trigger.created_ts,
        "modified_ts": trigger.modified_ts,
    })
    return payload


class RoomTriggerViewSet(BaseWorldBuilderViewSet):
    serializer_class = serializers.Serializer
    http_method_names = ['get', 'head', 'options']

    def _get_room(self):
        room = generics.get_object_or_404(
            Room.objects.filter(world=self.world),
            pk=self.kwargs.get("room_pk"),
        )
        _assert_can_view_room(view=self, room=room)
        return room

    def get_queryset(self):
        room = self._get_room()
        room_ct = ContentType.objects.get_for_model(Room)
        qs = Trigger.objects.filter(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=room_ct,
            target_id=room.id,
        ).select_related("target_type")

        kind = self.request.query_params.get('kind')
        if kind in adv_consts.TRIGGER_KINDS:
            qs = qs.filter(kind=kind)

        is_active = self.request.query_params.get('is_active')
        if is_active in ('true', '1'):
            qs = qs.filter(is_active=True)
        elif is_active in ('false', '0'):
            qs = qs.filter(is_active=False)

        query = self.request.query_params.get('query')
        if query:
            try:
                query_id = int(query)
            except ValueError:
                qs = qs.filter(
                    Q(name__icontains=query)
                    | Q(match__icontains=query)
                    | Q(event__icontains=query)
                    | Q(script__icontains=query)
                )
            else:
                qs = qs.filter(pk=query_id)

        sort_by = self.request.query_params.get('sort_by')
        allowed_sort_fields = {
            'id',
            'name',
            'kind',
            'event',
            'match',
            'order',
            'gate_delay',
            'is_active',
            'created_ts',
            'modified_ts',
        }
        if sort_by and sort_by.lstrip('-') in allowed_sort_fields:
            return qs.order_by(sort_by)

        return qs.order_by('order', 'created_ts', 'id')

    def _template_payload(self):
        return builder_manifests.serialize_room_trigger_template(
            world=self.world,
            room=self._get_room(),
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            data = [_serialize_builder_trigger_response(trigger) for trigger in page]
            response = self.get_paginated_response(data)
            response.data["new_trigger_template"] = self._template_payload()
            response.data["triggers"] = data
            return response

        data = [_serialize_builder_trigger_response(trigger) for trigger in queryset]
        return Response(
            {
                "count": len(data),
                "next": None,
                "previous": None,
                "results": data,
                "new_trigger_template": self._template_payload(),
                "triggers": data,
            }
        )

    def retrieve(self, request, *args, **kwargs):
        trigger = self.get_object()
        return Response(_serialize_builder_trigger_response(trigger))


room_triggers = RoomTriggerViewSet.as_view({
    'get': 'list',
})
room_trigger_detail = RoomTriggerViewSet.as_view({
    'get': 'retrieve',
})


class WorldTriggerViewSet(BaseWorldBuilderViewSet):
    serializer_class = serializers.Serializer
    http_method_names = ['get', 'head', 'options']

    def _assert_can_view_world_triggers(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to view world triggers."
        )

    def get_queryset(self):
        self._assert_can_view_world_triggers()

        qs = Trigger.objects.filter(world=self.world).select_related("target_type")

        scope = self.request.query_params.get('scope')
        if scope in adv_consts.TRIGGER_SCOPES:
            qs = qs.filter(scope=scope)

        kind = self.request.query_params.get('kind')
        if kind in adv_consts.TRIGGER_KINDS:
            qs = qs.filter(kind=kind)

        event = self.request.query_params.get('event')
        if event in adv_consts.TRIGGER_EVENTS:
            qs = qs.filter(event=event)

        is_active = self.request.query_params.get('is_active')
        if is_active in ('true', '1'):
            qs = qs.filter(is_active=True)
        elif is_active in ('false', '0'):
            qs = qs.filter(is_active=False)

        query = self.request.query_params.get('query')
        if query:
            try:
                query_id = int(query)
            except ValueError:
                qs = qs.filter(
                    Q(name__icontains=query)
                    | Q(match__icontains=query)
                    | Q(event__icontains=query)
                    | Q(script__icontains=query)
                )
            else:
                qs = qs.filter(pk=query_id)

        sort_by = self.request.query_params.get('sort_by')
        allowed_sort_fields = {
            'id',
            'name',
            'scope',
            'kind',
            'event',
            'match',
            'order',
            'gate_delay',
            'is_active',
            'created_ts',
            'modified_ts',
        }
        if sort_by and sort_by.lstrip('-') in allowed_sort_fields:
            return qs.order_by(sort_by)

        return qs.order_by('scope', 'kind', 'order', 'created_ts', 'id')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            data = [_serialize_builder_trigger_response(trigger) for trigger in page]
            return self.get_paginated_response(data)

        data = [_serialize_builder_trigger_response(trigger) for trigger in queryset]
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        trigger = self.get_object()
        return Response(_serialize_builder_trigger_response(trigger))


world_trigger_list = WorldTriggerViewSet.as_view({
    'get': 'list',
})
world_trigger_detail = WorldTriggerViewSet.as_view({
    'get': 'retrieve',
})


def _serialize_builder_ability_response(ability):
    payload = builder_manifests.serialize_ability_payload(ability)
    payload.update({
        "created_ts": ability.created_ts,
        "modified_ts": ability.modified_ts,
    })
    return payload


class WorldAbilityViewSet(BaseWorldBuilderViewSet):
    serializer_class = serializers.Serializer
    http_method_names = ['get', 'head', 'options']

    def _assert_can_view_abilities(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to view abilities."
        )

    def get_queryset(self):
        self._assert_can_view_abilities()

        qs = AbilityDefinition.objects.filter(world=definition_world(self.world))

        is_active = self.request.query_params.get('is_active')
        if is_active in ('true', '1'):
            qs = qs.filter(is_active=True)
        elif is_active in ('false', '0'):
            qs = qs.filter(is_active=False)

        query = self.request.query_params.get('query')
        if query:
            try:
                query_id = int(query)
            except ValueError:
                qs = qs.filter(
                    Q(name__icontains=query)
                    | Q(slug__icontains=query)
                    | Q(command_verbs__contains=[query])
                )
            else:
                qs = qs.filter(pk=query_id)

        sort_by = self.request.query_params.get('sort_by')
        allowed_sort_fields = {
            'id',
            'name',
            'slug',
            'is_active',
            'created_ts',
            'modified_ts',
        }
        if sort_by and sort_by.lstrip('-') in allowed_sort_fields:
            return qs.order_by(sort_by)

        return qs.order_by('slug', 'id')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            data = [_serialize_builder_ability_response(ability) for ability in page]
            return self.get_paginated_response(data)

        data = [_serialize_builder_ability_response(ability) for ability in queryset]
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        ability = self.get_object()
        return Response(_serialize_builder_ability_response(ability))


world_ability_list = WorldAbilityViewSet.as_view({
    'get': 'list',
})
world_ability_detail = WorldAbilityViewSet.as_view({
    'get': 'retrieve',
})


class WorldExportView(BaseWorldBuilderView):

    def get(self, request, pk, format=None):
        if self._builder_rank < 3:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to export this world."
            )
        return Response(builder_world_export.serialize_world_export_payload(self.world))


world_export = WorldExportView.as_view()


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

    def _assert_can_edit_world_config(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter world configuration."
        )

    def _assert_can_edit_quest_templates(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter quest templates."
        )

    def _assert_can_edit_abilities(self):
        if self.world.instance_of_id:
            raise serializers.ValidationError(
                "Abilities are inherited from the base world and cannot be altered on an instance world."
            )
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter abilities."
        )

    def _assert_can_edit_socials(self):
        if self.world.pk != definition_world(self.world).pk:
            raise serializers.ValidationError(
                "Socials are inherited from the base world and cannot be "
                "altered on an instance or spawned world."
            )
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter socials."
        )

    def _assert_can_edit_item_definitions(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter item definitions."
        )

    def _assert_can_edit_mob_definitions(self):
        if self._builder_rank >= 3:
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter mob definitions."
        )

    def _assert_can_edit_zone_manifest(self, manifest):
        metadata = manifest.get("metadata") or {}
        zone_ref = str(metadata.get("ref") or "").strip()
        zone = None
        if zone_ref:
            try:
                relative_id = builder_world_export._parse_zone_ref(
                    zone_ref,
                    field_name="metadata.ref",
                )
            except serializers.ValidationError:
                return
            zone = Zone.objects.filter(
                world=self.world,
                relative_id=relative_id,
            ).first()
        else:
            zone_name = str(metadata.get("name") or "").strip()
            zone = Zone.objects.filter(world=self.world, name=zone_name).first() if zone_name else None
        if zone is None:
            if self._builder_rank >= 3:
                return
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to create zones via manifests."
            )
        if self._builder_rank >= 3:
            return
        if _has_zone_assignment(user=self.request.user, zone=zone):
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter this zone."
        )

    def _assert_can_edit_room_manifest(self, manifest):
        metadata = builder_world_export._manifest_metadata(manifest)
        room_ref = str(metadata.get("ref") or "").strip()
        if not room_ref:
            return
        try:
            x, y, z = builder_world_export._parse_room_ref(room_ref)
        except serializers.ValidationError:
            return
        room = Room.objects.filter(world=self.world, x=x, y=y, z=z).first()
        if room is None:
            if self._builder_rank >= 3:
                return
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to create rooms via manifests."
            )
        _assert_can_edit_room(view=self, room=room)

    def _assert_can_edit_path_manifest(self, manifest):
        if self._builder_rank >= 3:
            return
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            metadata = manifest.get("metadata") or {}
            path_ref = str(metadata.get("ref") or "").strip()
            if not path_ref:
                return
            try:
                relative_id = builder_world_export._parse_path_ref(
                    path_ref,
                    field_name="metadata.ref",
                )
            except serializers.ValidationError:
                return
            path = (
                Path.objects
                .select_related("zone")
                .filter(world=self.world, relative_id=relative_id)
                .first()
            )
            if path is None:
                return
            if _has_zone_assignment(user=self.request.user, zone=path.zone):
                return
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to alter this path."
            )

        spec = manifest.get("spec") or {}
        zone_ref = str(spec.get("zone") or "").strip()
        if not zone_ref:
            return
        try:
            zone = builder_world_export._resolve_spawn_plan_zone(
                world=self.world,
                value=zone_ref,
                field_name="spec.zone",
            )
        except serializers.ValidationError:
            return
        if _has_zone_assignment(user=self.request.user, zone=zone):
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter this path."
        )

    def _assert_can_edit_spawn_plan_manifest(self, manifest):
        if self._builder_rank >= 3:
            return
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            metadata = manifest.get("metadata") or {}
            slug = str(metadata.get("slug") or "").strip()
            key = str(metadata.get("key") or "").strip()
            spawn_plan = None
            if slug:
                spawn_plan = (
                    SpawnPlan.objects
                    .select_related("zone")
                    .filter(world=self.world, slug=slug)
                    .first()
                )
            elif key:
                try:
                    plan_id = builder_manifests._parse_entity_ref(
                        key,
                        "spawnplan",
                        "metadata.key",
                    )
                except serializers.ValidationError:
                    return
                spawn_plan = (
                    SpawnPlan.objects
                    .select_related("zone")
                    .filter(world=self.world, pk=plan_id)
                    .first()
                )
            if spawn_plan is None:
                return
            if _has_zone_assignment(user=self.request.user, zone=spawn_plan.zone):
                return
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to alter this spawn plan."
            )
        spec = manifest.get("spec") or {}
        zone_ref = spec.get("zone")
        if not zone_ref:
            return
        try:
            zone = builder_world_export._resolve_spawn_plan_zone(
                world=self.world,
                value=zone_ref,
                field_name="spec.zone",
            )
        except serializers.ValidationError:
            return
        if _has_zone_assignment(user=self.request.user, zone=zone):
            return
        raise drf_exceptions.PermissionDenied(
            "You do not have permission to alter this spawn plan."
        )

    def _apply_trigger_manifest(self, manifest):
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

        manifest = builder_world_export.normalize_trigger_manifest_for_import(
            world=self.world,
            manifest=manifest,
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

    def _apply_world_manifest(self, manifest):
        self._assert_can_edit_world_config()
        builder_world_export.apply_world_manifest(world=self.world, manifest=manifest)
        return Response(
            {
                "kind": builder_world_export.WORLD_MANIFEST_KIND,
                "operation": "updated",
            },
            status=status.HTTP_200_OK,
        )

    def _apply_quest_manifest(self, manifest):
        self._assert_can_edit_quest_templates()
        operation = quest_manifests.parse_manifest_operation(manifest)
        if operation == quest_manifests.MANIFEST_OPERATION_DELETE:
            parsed_delete = quest_manifests.parse_quest_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            quest = parsed_delete.quest
            quest_payload = {
                "id": quest.id,
                "key": quest.key,
                "slug": quest.slug,
                "name": quest.name,
            }
            quest.delete()
            return Response(
                {
                    "kind": quest_manifests.QUEST_MANIFEST_KIND,
                    "operation": "deleted",
                    "quest": quest_payload,
                },
                status=status.HTTP_200_OK,
            )

        parsed_quest = quest_manifests.parse_quest_manifest(
            world=self.world,
            manifest=manifest,
        )
        is_create = parsed_quest.quest is None
        quest = quest_manifests.apply_quest_manifest(parsed_quest)
        return Response(
            {
                "kind": quest_manifests.QUEST_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "quest": quest_manifests.serialize_quest_template_payload(quest),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_item_definition_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_item_definition_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            item_definition = parsed_delete.item_definition
            item_definition_payload = {
                "id": item_definition.id,
                "key": item_definition.key,
                "slug": item_definition.slug,
                "name": item_definition.name,
            }
            try:
                item_definition.delete()
            except RestrictedError:
                raise serializers.ValidationError(
                    "Cannot delete an item definition used by a crafting recipe."
                )
            return Response(
                {
                    "kind": builder_manifests.ITEM_DEFINITION_MANIFEST_KIND,
                    "operation": "deleted",
                    "item_definition": item_definition_payload,
                },
                status=status.HTTP_200_OK,
            )

        item_definition, is_create = builder_world_export.apply_item_definition_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.ITEM_DEFINITION_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "item_definition": builder_manifests.serialize_item_definition_payload(
                    item_definition
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_mob_definition_manifest(self, manifest):
        self._assert_can_edit_mob_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_mob_definition_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            mob_definition = parsed_delete.mob_definition
            mob_definition_payload = {
                "id": mob_definition.id,
                "key": mob_definition.key,
                "slug": mob_definition.slug,
                "name": mob_definition.name,
            }
            mob_definition.delete()
            return Response(
                {
                    "kind": builder_manifests.MOB_DEFINITION_MANIFEST_KIND,
                    "operation": "deleted",
                    "mob_definition": mob_definition_payload,
                },
                status=status.HTTP_200_OK,
            )

        mob_definition, is_create = builder_world_export.apply_mob_definition_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.MOB_DEFINITION_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "mob_definition": builder_manifests.serialize_mob_definition_payload(
                    mob_definition
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_item_bundle_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_item_bundle_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            item_bundle = parsed_delete.item_bundle
            item_bundle_payload = {
                "id": item_bundle.id,
                "key": item_bundle.key,
                "slug": item_bundle.slug,
                "name": item_bundle.name,
            }
            item_bundle.delete()
            return Response(
                {
                    "kind": builder_manifests.ITEM_BUNDLE_MANIFEST_KIND,
                    "operation": "deleted",
                    "item_bundle": item_bundle_payload,
                },
                status=status.HTTP_200_OK,
            )

        item_bundle, is_create = builder_world_export.apply_item_bundle_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.ITEM_BUNDLE_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "item_bundle": builder_manifests.serialize_item_bundle_payload(
                    item_bundle
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_merchant_profile_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_merchant_profile_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            merchant_profile = parsed_delete.merchant_profile
            merchant_profile_payload = {
                "id": merchant_profile.id,
                "key": merchant_profile.key,
                "slug": merchant_profile.slug,
                "name": merchant_profile.name,
            }
            merchant_profile.delete()
            return Response(
                {
                    "kind": builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND,
                    "operation": "deleted",
                    "merchant_profile": merchant_profile_payload,
                },
                status=status.HTTP_200_OK,
            )

        merchant_profile, is_create = builder_world_export.apply_merchant_profile_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "merchant_profile": builder_manifests.serialize_merchant_profile_payload(
                    merchant_profile
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_craft_material_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed = builder_manifests.parse_craft_material_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            material = parsed.material
            payload = {
                "id": material.id,
                "key": material.key,
                "slug": material.slug,
                "name": material.name,
            }
            try:
                material.delete()
            except RestrictedError:
                raise serializers.ValidationError(
                    "Cannot delete a craft material that is still referenced."
                )
            return Response(
                {
                    "kind": builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND,
                    "operation": "deleted",
                    "craft_material": payload,
                },
                status=status.HTTP_200_OK,
            )

        material, is_create = builder_world_export.apply_craft_material_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "craft_material": builder_manifests.serialize_craft_material_payload(
                    material
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_crafting_recipe_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed = builder_manifests.parse_crafting_recipe_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            recipe = parsed.recipe
            payload = {
                "id": recipe.id,
                "key": recipe.key,
                "slug": recipe.slug,
                "name": recipe.name,
            }
            recipe.delete()
            return Response(
                {
                    "kind": builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND,
                    "operation": "deleted",
                    "crafting_recipe": payload,
                },
                status=status.HTTP_200_OK,
            )

        recipe, is_create = builder_world_export.apply_crafting_recipe_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "crafting_recipe": builder_manifests.serialize_crafting_recipe_payload(
                    recipe
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_crafting_profile_manifest(self, manifest):
        self._assert_can_edit_item_definitions()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed = builder_manifests.parse_crafting_profile_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            profile = parsed.profile
            payload = {
                "id": profile.id,
                "key": profile.key,
                "slug": profile.slug,
                "name": profile.name,
            }
            profile.delete()
            return Response(
                {
                    "kind": builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND,
                    "operation": "deleted",
                    "crafting_profile": payload,
                },
                status=status.HTTP_200_OK,
            )

        profile, is_create = builder_world_export.apply_crafting_profile_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "crafting_profile": builder_manifests.serialize_crafting_profile_payload(
                    profile
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_faction_manifest(self, manifest):
        self._assert_can_edit_world_config()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_faction_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            faction = parsed_delete.faction
            if FactionAssignment.objects.filter(
                faction=faction,
                faction__type=builder_manifests.FACTION_TYPE_CORE,
            ).exists():
                raise serializers.ValidationError(
                    "Cannot delete a core faction with assignments."
                )
            faction_payload = {
                "id": faction.id,
                "key": f"faction.{faction.id}",
                "code": faction.code,
                "name": faction.name,
            }
            faction.delete()
            return Response(
                {
                    "kind": builder_manifests.FACTION_MANIFEST_KIND,
                    "operation": "deleted",
                    "faction": faction_payload,
                },
                status=status.HTTP_200_OK,
            )

        faction, is_create = builder_world_export.apply_faction_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.FACTION_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "faction": builder_manifests.serialize_faction_payload(faction),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_ability_manifest(self, manifest):
        self._assert_can_edit_abilities()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            parsed_delete = builder_manifests.parse_ability_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            ability = parsed_delete.ability
            ability_payload = {
                "id": ability.id,
                "key": f"ability.{ability.id}",
                "slug": ability.slug,
                "name": ability.name,
            }
            ability.delete()
            return Response(
                {
                    "kind": builder_manifests.ABILITY_MANIFEST_KIND,
                    "operation": "deleted",
                    "ability": ability_payload,
                },
                status=status.HTTP_200_OK,
            )

        parsed_ability = builder_manifests.parse_ability_manifest(
            world=self.world,
            manifest=manifest,
        )
        is_create = parsed_ability.ability is None
        ability = builder_manifests.apply_ability_manifest(parsed_ability)
        return Response(
            {
                "kind": builder_manifests.ABILITY_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "ability": builder_manifests.serialize_ability_payload(ability),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_abilities_manifest(self, manifest):
        self._assert_can_edit_abilities()
        parsed_bundle = builder_manifests.parse_abilities_manifest(
            world=self.world,
            manifest=manifest,
        )
        abilities = builder_manifests.apply_abilities_manifest(parsed_bundle)
        return Response(
            {
                "kind": builder_manifests.ABILITIES_MANIFEST_KIND,
                "operation": "applied",
                "abilities": [
                    builder_manifests.serialize_ability_payload(ability)
                    for ability in abilities
                ],
            },
            status=status.HTTP_200_OK,
        )

    def _apply_social_manifest(self, manifest):
        self._assert_can_edit_socials()
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            social = builder_world_export.delete_social_manifest(
                world=self.world,
                manifest=manifest,
            )
            return Response(
                {
                    "kind": builder_manifests.SOCIAL_MANIFEST_KIND,
                    "operation": "deleted",
                    "social": social._deleted_payload,
                },
                status=status.HTTP_200_OK,
            )

        social, is_create = builder_world_export.apply_social_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_manifests.SOCIAL_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "social": builder_manifests.serialize_social_payload(social),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_quest_arc_manifest(self, manifest):
        self._assert_can_edit_quest_templates()
        operation = quest_manifests.parse_manifest_operation(manifest)
        if operation == quest_manifests.MANIFEST_OPERATION_DELETE:
            parsed_delete = quest_manifests.parse_quest_arc_delete_manifest(
                world=self.world,
                manifest=manifest,
            )
            quest_arc = parsed_delete.quest_arc
            quest_arc_payload = {
                "id": quest_arc.id,
                "key": quest_arc.key,
                "slug": quest_arc.slug,
                "name": quest_arc.name,
            }
            quest_arc.delete()
            return Response(
                {
                    "kind": quest_manifests.QUEST_ARC_MANIFEST_KIND,
                    "operation": "deleted",
                    "quest_arc": quest_arc_payload,
                },
                status=status.HTTP_200_OK,
            )

        parsed_quest_arc = quest_manifests.parse_quest_arc_manifest(
            world=self.world,
            manifest=manifest,
        )
        is_create = parsed_quest_arc.quest_arc is None
        quest_arc = quest_manifests.apply_quest_arc_manifest(parsed_quest_arc)
        return Response(
            {
                "kind": quest_manifests.QUEST_ARC_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "quest_arc": quest_manifests.serialize_quest_arc_payload(quest_arc),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_currency_manifest(self, manifest):
        self._assert_can_edit_world_config()
        try:
            currency, operation = builder_world_export.apply_currency_manifest(
                world=self.world,
                manifest=manifest,
            )
        except ValidationError as error:
            detail = (
                error.message_dict
                if hasattr(error, 'message_dict')
                else error.messages
            )
            raise drf_exceptions.ValidationError(detail)
        return Response(
            {
                "kind": builder_world_export.CURRENCY_MANIFEST_KIND,
                "operation": operation,
                "currency": {
                    "code": currency.code,
                    "name": currency.name,
                    "plural_name": currency.plural_name or currency.name,
                    "description": currency.description or "",
                },
            },
            status=status.HTTP_201_CREATED if operation == "created" else status.HTTP_200_OK,
        )

    def _apply_zone_manifest(self, manifest):
        self._assert_can_edit_zone_manifest(manifest)
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            zone = builder_world_export.delete_zone_manifest(
                world=self.world,
                manifest=manifest,
            )
            zone_payload = getattr(zone, "_deleted_payload", None)
            if zone_payload is None:
                zone_payload = builder_world_export.serialize_zone_payload(
                    zone,
                    include_yaml=False,
                )
            return Response(
                {
                    "kind": builder_world_export.ZONE_MANIFEST_KIND,
                    "operation": "deleted",
                    "zone": zone_payload,
                },
                status=status.HTTP_200_OK,
            )

        zone, is_create = builder_world_export.apply_zone_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_world_export.ZONE_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "zone": builder_world_export.serialize_zone_payload(zone),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_room_manifest(self, manifest):
        self._assert_can_edit_room_manifest(manifest)
        room, is_create = builder_world_export.apply_room_manifest(
            world=self.world,
            manifest=manifest,
        )
        room.update_live_instances()
        return Response(
            {
                "kind": builder_world_export.ROOM_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "room": {
                    "id": room.id,
                    "key": room.key,
                    "name": room.name,
                    "ref": builder_world_export._room_ref(room),
                },
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _serialize_path_manifest_response(self, path):
        zone = path.zone
        return {
            "id": path.id,
            "key": path.key,
            "ref": builder_world_export._path_ref(path),
            "relative_id": path.relative_id,
            "name": path.name,
            "zone": {
                "id": zone.id,
                "key": zone.key,
                "relative_id": zone.relative_id,
                "manifest_ref": builder_world_export._zone_ref(zone),
                "name": zone.name,
            } if zone else None,
        }

    def _apply_path_manifest(self, manifest):
        self._assert_can_edit_path_manifest(manifest)
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            path = builder_world_export.delete_path_manifest(
                world=self.world,
                manifest=manifest,
            )
            return Response(
                {
                    "kind": builder_world_export.PATH_MANIFEST_KIND,
                    "operation": "deleted",
                    "path": self._serialize_path_manifest_response(path),
                },
                status=status.HTTP_200_OK,
            )

        path, is_create = builder_world_export.apply_path_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_world_export.PATH_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "path": self._serialize_path_manifest_response(path),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _apply_spawn_plan_manifest(self, manifest):
        self._assert_can_edit_spawn_plan_manifest(manifest)
        operation = builder_manifests.parse_manifest_operation(manifest)
        if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
            spawn_plan = builder_world_export.delete_spawn_plan_manifest(
                world=self.world,
                manifest=manifest,
            )
            spawn_plan_payload = getattr(spawn_plan, "_deleted_payload", None)
            if spawn_plan_payload is None:
                spawn_plan_payload = builder_world_export.serialize_spawn_plan_payload(
                    spawn_plan,
                    include_yaml=False,
                )
            return Response(
                {
                    "kind": builder_world_export.SPAWN_PLAN_MANIFEST_KIND,
                    "operation": "deleted",
                    "spawn_plan": spawn_plan_payload,
                },
                status=status.HTTP_200_OK,
            )

        spawn_plan, is_create = builder_world_export.apply_spawn_plan_manifest(
            world=self.world,
            manifest=manifest,
        )
        return Response(
            {
                "kind": builder_world_export.SPAWN_PLAN_MANIFEST_KIND,
                "operation": "created" if is_create else "updated",
                "spawn_plan": builder_world_export.serialize_spawn_plan_payload(
                    spawn_plan
                ),
            },
            status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK,
        )

    def _dispatch_manifest(self, manifest):
        manifest_kind = builder_world_export.parse_document_kind(manifest)

        if manifest_kind == builder_world_export.WORLD_MANIFEST_KIND:
            return self._apply_world_manifest(manifest)
        if manifest_kind == builder_world_export.CURRENCY_MANIFEST_KIND:
            return self._apply_currency_manifest(manifest)
        if manifest_kind == builder_world_export.ZONE_MANIFEST_KIND:
            return self._apply_zone_manifest(manifest)
        if manifest_kind == builder_world_export.ROOM_MANIFEST_KIND:
            return self._apply_room_manifest(manifest)
        if manifest_kind == builder_world_export.PATH_MANIFEST_KIND:
            return self._apply_path_manifest(manifest)
        if manifest_kind == builder_manifests.TRIGGER_MANIFEST_KIND:
            return self._apply_trigger_manifest(manifest)
        if manifest_kind == builder_manifests.ITEM_DEFINITION_MANIFEST_KIND:
            return self._apply_item_definition_manifest(manifest)
        if manifest_kind == builder_manifests.ITEM_BUNDLE_MANIFEST_KIND:
            return self._apply_item_bundle_manifest(manifest)
        if manifest_kind == builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND:
            return self._apply_merchant_profile_manifest(manifest)
        if manifest_kind == builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND:
            return self._apply_craft_material_manifest(manifest)
        if manifest_kind == builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND:
            return self._apply_crafting_recipe_manifest(manifest)
        if manifest_kind == builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND:
            return self._apply_crafting_profile_manifest(manifest)
        if manifest_kind == builder_manifests.FACTION_MANIFEST_KIND:
            return self._apply_faction_manifest(manifest)
        if manifest_kind == builder_manifests.MOB_DEFINITION_MANIFEST_KIND:
            return self._apply_mob_definition_manifest(manifest)
        if manifest_kind == builder_manifests.ABILITY_MANIFEST_KIND:
            return self._apply_ability_manifest(manifest)
        if manifest_kind == builder_manifests.ABILITIES_MANIFEST_KIND:
            return self._apply_abilities_manifest(manifest)
        if manifest_kind == builder_manifests.SOCIAL_MANIFEST_KIND:
            return self._apply_social_manifest(manifest)
        if manifest_kind == builder_world_export.SPAWN_PLAN_MANIFEST_KIND:
            return self._apply_spawn_plan_manifest(manifest)
        if manifest_kind == quest_manifests.QUEST_MANIFEST_KIND:
            return self._apply_quest_manifest(manifest)
        if manifest_kind == quest_manifests.QUEST_ARC_MANIFEST_KIND:
            return self._apply_quest_arc_manifest(manifest)

        raise serializers.ValidationError("Unsupported manifest kind.")

    def _batch_summary(self, results):
        summary = {
            "documents": len(results),
            "kinds": {},
        }
        for result in results:
            kind = str(result.get("kind") or "").strip().lower()
            if not kind:
                continue
            summary["kinds"][kind] = summary["kinds"].get(kind, 0) + 1
        return summary

    def post(self, request, world_pk, format=None):
        manifest_text = request.data.get("manifest")
        if manifest_text is None:
            raise serializers.ValidationError({"manifest": ["This field is required."]})

        manifests = builder_manifests.load_yaml_documents(manifest_text)
        if len(manifests) == 1:
            return self._dispatch_manifest(manifests[0])

        results = []
        with transaction.atomic():
            for index, manifest in enumerate(manifests, start=1):
                try:
                    response = self._dispatch_manifest(manifest)
                except drf_exceptions.PermissionDenied as exc:
                    kind = str((manifest.get("kind") or "manifest")).strip().lower() or "manifest"
                    raise drf_exceptions.PermissionDenied(
                        f"Document {index} ({kind}) failed: {exc.detail}"
                    )
                except serializers.ValidationError as exc:
                    kind = str((manifest.get("kind") or "manifest")).strip().lower() or "manifest"
                    raise serializers.ValidationError(
                        f"Document {index} ({kind}) failed: {exc.detail}"
                    )
                results.append(response.data)

        return Response(
            {
                "kind": "batch",
                "operation": "applied",
                "summary": self._batch_summary(results),
                "results": results,
            },
            status=status.HTTP_200_OK,
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


class ItemDefinitionViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ItemDefinitionSerializer
    http_method_names = ['get', 'head', 'options']

    def _serialize_item_definition_response(self, item_definition):
        payload = builder_manifests.serialize_item_definition_payload(item_definition)
        payload["modified_ts"] = item_definition.modified_ts
        payload["model_type"] = item_definition.model_type
        payload["randomized"] = bool((item_definition.randomization or {}).get("attributes"))
        return payload

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        qs = ItemDefinition.objects.filter(world=context).order_by('-modified_ts')

        item_type = (
            self.request.query_params.get('item_type')
            or self.request.query_params.get('type')
        )
        if item_type in adv_consts.ITEM_TYPES:
            qs = qs.filter(item_type=item_type)

        return self.search_queryset(qs)

    def retrieve(self, request, *args, **kwargs):
        item_definition = self.get_object()
        return Response(self._serialize_item_definition_response(item_definition))

    @action(methods=['get'], detail=True)
    def power(self, request, *args, **kwargs):
        item_definition = self.get_object()
        return Response(analyze_item_definition_power(self.world, item_definition))


item_definition_list = ItemDefinitionViewSet.as_view({
    'get': 'list',
})
item_definition_detail = ItemDefinitionViewSet.as_view({
    'get': 'retrieve',
})
item_definition_power = ItemDefinitionViewSet.as_view({
    'get': 'power',
})


class ItemBundleViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.ItemBundleSerializer
    http_method_names = ['get', 'head', 'options']

    def _serialize_item_bundle_response(self, item_bundle):
        payload = builder_manifests.serialize_item_bundle_payload(item_bundle)
        payload["modified_ts"] = item_bundle.modified_ts
        payload["model_type"] = item_bundle.model_type
        payload["entry_count"] = item_bundle.entries.count()
        return payload

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        qs = ItemBundle.objects.filter(world=context).prefetch_related("entries").order_by('-modified_ts')
        return self.search_queryset(qs)

    def retrieve(self, request, *args, **kwargs):
        item_bundle = self.get_object()
        return Response(self._serialize_item_bundle_response(item_bundle))


item_bundle_list = ItemBundleViewSet.as_view({
    'get': 'list',
})
item_bundle_detail = ItemBundleViewSet.as_view({
    'get': 'retrieve',
})


class MobDefinitionViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobDefinitionSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def _serialize_mob_definition_response(self, mob_definition):
        payload = builder_manifests.serialize_mob_definition_payload(mob_definition)
        payload["modified_ts"] = mob_definition.modified_ts
        payload["model_type"] = mob_definition.model_type
        payload["randomized"] = bool((mob_definition.randomization or {}).get("attributes"))
        return payload

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        qs = MobDefinition.objects.filter(world=context).order_by('-modified_ts')

        mob_type = self.request.query_params.get('type')
        if mob_type in adv_consts.MOB_TYPES:
            qs = qs.filter(mob_type=mob_type)

        randomized = self.request.query_params.get('randomized')
        if randomized == 'true':
            qs = qs.exclude(randomization={})
        elif randomized == 'false':
            qs = qs.filter(randomization={})

        return self.search_queryset(qs)

    def retrieve(self, request, *args, **kwargs):
        mob_definition = self.get_object()
        return Response(self._serialize_mob_definition_response(mob_definition))

    @action(methods=['get'], detail=True)
    def power(self, request, *args, **kwargs):
        mob_definition = self.get_object()
        return Response(analyze_mob_definition_power(self.world, mob_definition))

    @action(methods=['get', 'post'], detail=True)
    def reactions(self, request, pk, world_pk):
        mob_definition = self.get_object()
        mob_definition_ct = ContentType.objects.get_for_model(MobDefinition)
        if request.method.lower() == 'post':
            serializer = builder_serializers.AddMobReactionSerializer(
                definition=mob_definition,
                data=request.data,
            )
            serializer.is_valid(raise_exception=True)
            reaction = serializer.create(serializer.validated_data)
            serializer = builder_serializers.MobReactionSerializer(reaction)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        reaction_triggers = Trigger.objects.filter(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=mob_definition_ct,
            target_id=mob_definition.id,
        ).order_by('order', 'created_ts', 'id')
        serializer = builder_serializers.MobReactionSerializer(
            reaction_triggers,
            many=True)
        return Response(
            {
                'data': serializer.data,
                'new_trigger_template': builder_manifests.serialize_mob_trigger_template(
                    world=self.world,
                    mob_definition=mob_definition,
                ),
                'triggers': [
                    builder_manifests.serialize_trigger_manifest(trigger)
                    for trigger in reaction_triggers
                ],
            }
        )


mob_definition_list = MobDefinitionViewSet.as_view({
    'get': 'list',
})
mob_definition_detail = MobDefinitionViewSet.as_view({
    'get': 'retrieve',
})
mob_definition_power = MobDefinitionViewSet.as_view({
    'get': 'power',
})
mob_definition_reactions = MobDefinitionViewSet.as_view({
    'get': 'reactions',
    'post': 'reactions',
})


class MerchantProfileViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MerchantProfileSerializer
    http_method_names = ['get', 'head', 'options']

    def _serialize_merchant_profile_response(self, merchant_profile):
        payload = builder_manifests.serialize_merchant_profile_payload(merchant_profile)
        payload["modified_ts"] = merchant_profile.modified_ts
        payload["model_type"] = merchant_profile.model_type
        payload["stock_count"] = merchant_profile.stock_slots.count()
        return payload

    def get_queryset(self):
        context = self.world
        if context.instance_of:
            context = context.instance_of

        qs = (
            MerchantProfile.objects
            .filter(world=context)
            .select_related("settlement_currency")
            .prefetch_related("stock_slots")
            .order_by('-modified_ts')
        )

        funds_mode = self.request.query_params.get('funds_mode')
        if funds_mode in MerchantProfile.FUNDS_MODES:
            qs = qs.filter(funds_mode=funds_mode)

        buyback_enabled = self.request.query_params.get('buyback_enabled')
        if buyback_enabled == 'true':
            qs = qs.filter(buyback_enabled=True)
        elif buyback_enabled == 'false':
            qs = qs.filter(buyback_enabled=False)

        return self.search_queryset(qs)

    def retrieve(self, request, *args, **kwargs):
        merchant_profile = self.get_object()
        return Response(self._serialize_merchant_profile_response(merchant_profile))


merchant_profile_list = MerchantProfileViewSet.as_view({
    'get': 'list',
})
merchant_profile_detail = MerchantProfileViewSet.as_view({
    'get': 'retrieve',
})


class CraftMaterialViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.CraftMaterialSerializer
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        context = self.world.instance_of or self.world
        return self.search_queryset(
            CraftMaterial.objects.filter(world=context).order_by('order', 'name', 'id')
        )

    def retrieve(self, request, *args, **kwargs):
        material = self.get_object()
        payload = builder_manifests.serialize_craft_material_payload(material)
        payload["modified_ts"] = material.modified_ts
        payload["model_type"] = material.model_type
        return Response(payload)


craft_material_list = CraftMaterialViewSet.as_view({'get': 'list'})
craft_material_detail = CraftMaterialViewSet.as_view({'get': 'retrieve'})


class CraftingRecipeViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.CraftingRecipeSerializer
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        context = self.world.instance_of or self.world
        qs = (
            CraftingRecipe.objects
            .filter(world=context)
            .select_related('output_item_definition', 'currency')
            .prefetch_related(
                Prefetch(
                    'ingredients',
                    queryset=CraftingIngredient.objects.select_related('material'),
                ),
            )
            .order_by('group', 'order', 'slug', 'id')
        )
        group = str(self.request.query_params.get('group') or '').strip().lower()
        if group:
            qs = qs.filter(group=group)
        return self.search_queryset(
            qs,
            field_name='output_item_definition__name',
        )

    def retrieve(self, request, *args, **kwargs):
        recipe = self.get_object()
        payload = builder_manifests.serialize_crafting_recipe_payload(recipe)
        payload["modified_ts"] = recipe.modified_ts
        payload["model_type"] = recipe.model_type
        return Response(payload)


crafting_recipe_list = CraftingRecipeViewSet.as_view({'get': 'list'})
crafting_recipe_detail = CraftingRecipeViewSet.as_view({'get': 'retrieve'})


class CraftingProfileViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.CraftingProfileSerializer
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        context = self.world.instance_of or self.world
        return self.search_queryset(
            CraftingProfile.objects
            .filter(world=context)
            .prefetch_related(
                Prefetch(
                    'recipe_entries',
                    queryset=CraftingProfileRecipe.objects.select_related('recipe'),
                ),
            )
            .order_by('name', 'id')
        )

    def retrieve(self, request, *args, **kwargs):
        profile = self.get_object()
        payload = builder_manifests.serialize_crafting_profile_payload(profile)
        payload["modified_ts"] = profile.modified_ts
        payload["model_type"] = profile.model_type
        payload["recipe_count"] = profile.recipe_entries.count()
        return Response(payload)


crafting_profile_list = CraftingProfileViewSet.as_view({'get': 'list'})
crafting_profile_detail = CraftingProfileViewSet.as_view({'get': 'retrieve'})


class MobDefinitionReactionViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.MobReactionSerializer

    def get_queryset(self):
        return Trigger.objects.filter(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=self.kwargs['mob_definition_pk'],
        )


mob_definition_reaction_detail = MobDefinitionReactionViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'delete': 'destroy'})

# Path

class PathViewSet(BaseWorldBuilderViewSet):
    serializer_class = builder_serializers.PathDetailsSerializer

    def get_queryset(self):
        qs = Path.objects.filter(world=self.world)
        sorting = self.request.query_params.get('sort_by')
        if sorting is not None:
            return qs.order_by(sorting)
        return qs.order_by('-created_ts')

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
            'item_definition': ItemDefinition,
            'item_bundle': ItemBundle,
            'mob_definition': MobDefinition,
            'room': Room,
            'path': Path,
            'spawn_plan': SpawnPlan,
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

            elif query:
                kwargs['name__icontains'] = query

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


class MobDefinitionSuggestion(BaseWorldBuilderView):

    def post(self, request, world_pk, format=None):
        serializer = builder_serializers.MobDefinitionSuggestionSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        try:
            payload = suggest_mob_definition_manifest(
                self.world,
                name=serializer.validated_data["name"],
                slug=serializer.validated_data["slug"],
                mob_type=serializer.validated_data["type"],
                level=serializer.validated_data["level"],
                rating_percents={
                    "crit": serializer.validated_data.get("crit_percent"),
                    "resilience": serializer.validated_data.get("resilience_percent"),
                    "armor": serializer.validated_data.get("armor_percent"),
                    "dodge": serializer.validated_data.get("dodge_percent"),
                },
            )
        except LevelingConfigError as exc:
            raise serializers.ValidationError({"level": [str(exc)]})
        return Response(payload)


mob_definition_suggestion = MobDefinitionSuggestion.as_view()


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

        world_facts = get_state_snapshot(STATE_SCOPE_WORLD, world)

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

    @property
    def social_world(self):
        return definition_world(self.world)

    def get_queryset(self):
        return Social.objects.filter(world=self.social_world).order_by('cmd')

    def _assert_can_edit_social(self):
        if self._builder_rank < 3:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to alter world socials.")
        if self.world.pk != self.social_world.pk:
            raise drf_exceptions.ValidationError(
                "Socials are inherited from the base world and are read-only here.")

    def perform_create(self, serializer):
        self._assert_can_edit_social()
        serializer.save(world=self.social_world)

    def perform_update(self, serializer):
        self._assert_can_edit_social()
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_edit_social()
        instance.delete()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['world'] = self.social_world
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
            'definition',
        ).order_by(
            '-pending_deletion_ts')
        equipment = []
        for eq in deleted_eq_qs:
            equipment_data = {
                'id': eq.id,
                'name': eq.definition.name if eq.definition else eq.name,
                'type': eq.definition.item_type if eq.definition else eq.type,
                'definition_id': eq.definition_id,
                'pending_deletion_ts': eq.pending_deletion_ts,
                'contains': [],
            }
            equipment_data['contains'] = [
                {
                    'id': contained_item.id,
                    'name': (
                        contained_item.definition.name
                        if contained_item.definition
                        else contained_item.name),
                    'type': (
                        contained_item.definition.item_type if contained_item.definition
                        else contained_item.type),
                    'definition_id': contained_item.definition_id,
                }
                for contained_item
                in eq.inventory.prefetch_related('definition')
            ]
            equipment.append(equipment_data)

        # Inventory items
        deleted_items_qs = player.inventory.filter(
            is_pending_deletion=True
        ).prefetch_related(
            'definition',
        ).order_by(
            '-pending_deletion_ts')

        items = []
        for item in deleted_items_qs:
            type = item.definition.item_type if item.definition else item.type
            item_data = {
                'id': item.id,
                'name': item.definition.name if item.definition else item.name,
                'type': type,
                'definition_id': item.definition_id,
                'pending_deletion_ts': item.pending_deletion_ts,
                'contains': [],
            }
            item_data['contains'] = [
                {
                    'id': contained_item.id,
                    'name': (
                        contained_item.definition.name
                        if contained_item.definition
                        else contained_item.name),
                    'type': (
                        contained_item.definition.item_type if contained_item.definition
                        else contained_item.type),
                    'definition_id': contained_item.definition_id,
                }
                for contained_item
                in item.inventory.prefetch_related('definition')
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
        return (
            Currency.objects.filter(world=economy_world(self.world))
            .select_related('world')
            .prefetch_related('starting_balance_rules')
            .order_by('code')
        )

    def _assert_can_edit_currency(self):
        if self._builder_rank < 3:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to alter world currencies.")
        if self.world.pk != economy_world(self.world).pk:
            raise drf_exceptions.ValidationError(
                "Currencies are inherited from the base world and are read-only here.")

    def perform_create(self, serializer):
        from builders.currencies import create_currency, set_starting_balance

        self._assert_can_edit_currency()
        starting_amount = serializer.validated_data.pop('starting_amount', None)
        try:
            with transaction.atomic():
                serializer.instance = create_currency(
                    world=self.world,
                    **serializer.validated_data)
                if starting_amount is not None:
                    set_starting_balance(
                        currency=serializer.instance,
                        amount=starting_amount,
                    )
        except ValidationError as error:
            raise drf_exceptions.ValidationError(error.message_dict if hasattr(error, 'message_dict') else error.messages)

    def perform_update(self, serializer):
        from builders.currencies import set_starting_balance, update_currency

        self._assert_can_edit_currency()
        serializer.validated_data.pop('code', None)
        starting_amount = serializer.validated_data.pop('starting_amount', None)
        try:
            with transaction.atomic():
                serializer.instance = update_currency(
                    serializer.instance,
                    **serializer.validated_data)
                if starting_amount is not None:
                    set_starting_balance(
                        currency=serializer.instance,
                        amount=starting_amount,
                    )
        except ValidationError as error:
            raise drf_exceptions.ValidationError(error.message_dict if hasattr(error, 'message_dict') else error.messages)

    def perform_destroy(self, instance):
        from builders.currencies import delete_currency

        self._assert_can_edit_currency()
        try:
            delete_currency(instance)
        except ValidationError as error:
            raise drf_exceptions.ValidationError(error.messages)

    @action(detail=True, methods=['post'])
    def make_default(self, request, *args, **kwargs):
        from builders.currencies import select_default_currency

        self._assert_can_edit_currency()
        currency = self.get_object()
        try:
            select_default_currency(world=self.world, currency=currency)
        except ValidationError as error:
            raise drf_exceptions.ValidationError(error.messages)
        return Response(self.get_serializer(currency).data)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['world'] = self.world  # Add world to the context
        if self.action in ('list', 'retrieve'):
            from builders.currencies import currency_usage_map

            context['currency_usage_map'] = currency_usage_map(
                world=economy_world(self.world),
            )
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
currency_make_default = CurrencyViewSet.as_view({
    'post': 'make_default',
})
