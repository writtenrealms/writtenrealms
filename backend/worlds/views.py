#from django.shortcuts import render

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from rest_framework import (
    generics,
    mixins,
    permissions,
    serializers,
    status,
    viewsets)
from rest_framework.decorators import api_view
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from core.view_mixins import KeyedRetrieveMixin

from worlds import permissions as world_permissions
from worlds.models import World, Zone, Room
from worlds.serializers import WorldSerializer, ZoneSerializer, RoomSerializer

User = get_user_model()


class WorldViewSet(KeyedRetrieveMixin, viewsets.ModelViewSet):
    # Only show root worlds
    queryset = World.objects.filter(context__isnull=True)

    serializer_class = WorldSerializer
    permission_classes = (
        permissions.IsAuthenticatedOrReadOnly,
        world_permissions.IsAuthorOrReadOnly)

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    # def perform_destroy(self, instance):
    #     if not instance.context:
    #         from rest_framework import serializers
    #         raise serializers.ValidationError('Cannot delete root world')
    #     instance.delete()

world_detail = WorldViewSet.as_view({'get': 'retrieve'})


class ZoneViewSet(KeyedRetrieveMixin, viewsets.ModelViewSet):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    permission_classes = (
        permissions.IsAuthenticatedOrReadOnly,
        world_permissions.IsAuthorOrReadOnly)


class RoomViewSet(KeyedRetrieveMixin, viewsets.ModelViewSet):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    permission_classes = (
        permissions.IsAuthenticatedOrReadOnly,
        world_permissions.IsAuthorOrReadOnly)


@api_view(['GET'])
def api_root(request, format=None):
    return Response({
        'users': reverse('user-list', request=request, format=format),
        'worlds': reverse('world-list', request=request, format=format),
    })