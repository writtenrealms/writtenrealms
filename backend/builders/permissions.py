from rest_framework import permissions

from builders.models import WorldBuilder, Loader, Rule, MobTemplate, ItemTemplate
from worlds.models import World

class IsWorldBuilder(permissions.BasePermission):
    "ensures the requester is a builder for the requested world object"

    def has_permission(self, request, view):

        # Special case for WOT API
        if (view.world.is_public
            and view.world.id == 83
            and request.method in permissions.SAFE_METHODS):
            return True

        # Determine the viewer's build rank, if any
        builder_rank = 0
        builder = None
        if request.user.is_authenticated:
            if (view.world.author == request.user
                or request.user.is_staff):
                builder_rank = 4
            else:
                try:
                    builder = WorldBuilder.objects.get(
                        world=view.world, user=request.user)
                    builder_rank = builder.builder_rank
                except WorldBuilder.DoesNotExist:
                    pass
            view._builder_rank = builder_rank

        if request.method in permissions.SAFE_METHODS:
            if (request.user.is_authenticated and
                builder_rank > 0):
                return True

        """
        # check for builder (also include read-only)
        if (request.user.is_authenticated
            and WorldBuilder.objects.filter(
                world=view.world,
                user=request.user).exists()):
            return True
        """

        return view.world.can_edit(request.user, builder=builder)


class CanCreateWorld(permissions.BasePermission):

    def has_permission(self, request, view):
        return request.user.is_builder
