from django.conf import settings
from rest_framework import permissions

from django.core.exceptions import ObjectDoesNotExist


class IsSystemUser(permissions.BasePermission):
    message = 'User does not have permission for this operation.'

    def has_permission(self, request, view):
        system_user_email = getattr(settings, 'SYSTEM_USER_EMAIL', 'system@example.com')
        if (request.user.email or '').lower() == system_user_email.lower():
            return True
        return False


class IsTemporaryUser(permissions.BasePermission):
    "Permission for endpoints that should only be invoked by a temporary user"

    message = 'User is not a temporary user.'

    def has_permission(self, request, view):
        if request.user.is_authenticated and request.user.is_temporary:
            return True
        return False


class IsPlayerInGame(permissions.BasePermission):
    message = 'Player is not in game or does not belong to this user.'

    def has_permission(self, request, view):
        player_id = request.META.get('HTTP_X_PLAYER_ID', None)

        try:
            player = request.user.characters.get(pk=player_id)
        except ObjectDoesNotExist:
            return False

        if player.in_game:
            request.player = player
            return True

        return False


class IsStaffUser(permissions.BasePermission):
    message = 'User is not Staff.'

    def has_permission(self, request, view):
        return request.user.is_staff


# Permissions below assume that the views that use them use the
# WorldValidatorMixin to set view.world to the appropriate world

class IsRootWorld(permissions.BasePermission):
    message = 'World is not root world.'

    def has_permission(self, request, view):
        if (not view.world.context):
            return True
        return False

class IsLobbyView(permissions.BasePermission):
    message = 'This lobby is private.'

    def has_permission(self, request, view):
        if request.user.is_staff:
            return True

        if not request.user.is_authenticated:

            # For unauthenticated users, we only allow to view the info
            # of public worlds
            if (view.world.is_public
                and request.method in permissions.SAFE_METHODS):
                return True

            return False

        else:
            # For authenticated users, any of them are allowed to create
            # characters in public worlds, but in private worlds only if they
            # are allowed to edit it.
            if view.world.is_public:
                return True
            elif getattr(view, 'private_url', False):
                return True
            elif request.method in permissions.SAFE_METHODS:
                # check for builder (also include read-only)
                from builders.models import WorldBuilder
                if WorldBuilder.objects.filter(
                    world=view.world,
                    user=request.user).exists():
                    return True
                # Check to see if the user has a character in the world
                from spawns.models import Player
                if Player.objects.filter(
                    world__context=view.world,
                    user=request.user):
                    return True
            # Special case where you can create a character in a private world
            # if you already have a character in it.
            elif (not view.world.is_public
                  and view.__class__.__name__ == 'WorldCharacters'
                  and request.method == 'POST'):
                if request.user.characters.filter(
                    world__context=view.world).exists():
                    return True
            return view.world.can_edit(request.user)

        return False
