from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404

from rest_framework.generics import get_object_or_404

from builders.models import Quest
from worlds.models import World, Room, Zone, WorldURL

# Registration for the models that will have relative ID access
RELATIVE_MODELS = [Zone]

class KeyedRetrieveMixin:
    "Mixin for GenericAPIView that also has the WorldValidatorMixin applied"

    def get_object(self):

        # Try key access
        if self.kwargs.get('pk') and '.' in self.kwargs['pk']:

            cls_name, rid = self.kwargs['pk'].split('.')

            # Default
            self.kwargs['pk'] = rid

            # Try the relative models
            for rel_cls in RELATIVE_MODELS:
                if rel_cls.__name__.lower() == cls_name:
                    self.kwargs['pk'] = rel_cls.objects.get(
                    world=self.world,
                    relative_id=rid).pk
                    break

            cls = None
            if cls_name == 'room':
                cls = Room
            elif cls_name == 'zone':
                cls = Zone

        return super().get_object()


class WorldValidatorMixin:
    """
    Mixin to validate that the world id/key given in a url is a valid world.
    If it is, it is saved in self.world.

    Will actually change self.kwargs['world_pk'] to be the ID rather than key
    if applicable.
    """

    def initialize_request(self, *args, **kwargs):

        world_pk = self.kwargs.get('world_pk')
        if world_pk:

            # See if a key got passed in instead of the pk
            try:
                type, id = world_pk.split('.')
                if type == 'world':
                    self.kwargs['world_pk'] = world_pk = id
            except ValueError: # Not a key
                pass

            self.world = get_object_or_404(World.objects.all(), pk=world_pk)

        elif self.kwargs.get('pk'):
            # If we didn't find a world_pk but we do have the validator
            # applied, then we have to assume that the world was passed as
            # pk (which is required for KeyedRetrieveMixin to work, and the
            # two are often used in combination).
            try:
                pk = self.kwargs['pk']
                rtype, rid = pk.split('.')
                if rtype == 'world':
                    self.kwargs['pk'] = pk = rid
            except ValueError: # Not a key
                pass

            world_pk = pk

            #self.world = get_object_or_404(World.objects.all(), pk=pk)

            self.private_url = ''
            try:
                self.world = get_object_or_404(World.objects.all(), pk=world_pk)
            except Http404 as e:
                # See if there exists a private URL for this world
                try:
                    world_url = WorldURL.objects.get(
                        url=world_pk)
                    self.world = world_url.world
                    if world_url.is_private:
                        self.private_url = world_pk
                    kwargs['pk'] = self.world.pk
                except WorldURL.DoesNotExist:
                    raise e

        request = super().initialize_request(*args, **kwargs)
        return request


class RequestDataMixin:
    """
    Mixin that prints out the incoming request's data provided
    settings.DEBUG is true
    """

    def initialize_request(self, request, *args, **kwargs):
        _request = super().initialize_request(request, *args, **kwargs)
        if settings.DEBUG and _request.data:
            print("Request data: %s" % _request.data)
        return _request

