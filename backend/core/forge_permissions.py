"""Database-backed authorization for Forge requests handled by Celery workers."""

from django.core.exceptions import PermissionDenied

from fastapi_app.forge_ws import complete_job
from users.models import User
from worlds.models import World


def require_user(user_id):
    # The ID comes from the gateway's verified JWT, never the request payload.
    # Re-read permissions here so queued jobs cannot rely on stale JWT roles.
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise PermissionDenied('An active user account is required.')
    return user


def require_staff(user_id):
    user = require_user(user_id)
    if not user.is_staff:
        raise PermissionDenied('Staff access is required.')
    return user


def require_world_admin(world_id, user_id):
    user = require_user(user_id)
    try:
        if isinstance(world_id, bool) or not isinstance(world_id, (str, int)):
            raise ValueError
        world_id = int(world_id)
        if not 0 < world_id < 2**63:
            raise ValueError
        # Spawn -> template -> root, or instance template -> root. Load the
        # authors too because World.can_edit compares the author object.
        # Authorization takes at most three indexed queries, independent of
        # the world's players, instances, or number of assigned builders.
        world = World.objects.select_related(
            'author', 'context__author', 'context__instance_of__author',
            'instance_of__author',
        ).get(pk=world_id)
    except (TypeError, ValueError, World.DoesNotExist):
        raise PermissionDenied('You cannot administer this world.') from None
    root = world.context if world.context_id else world
    root = root.instance_of if root.instance_of_id else root
    # Keep the REST builder administration policy as the source of truth.
    if not root.can_edit(user):
        raise PermissionDenied('You cannot administer this world.')
    return world, user


def reject_job(client_id, job, error):
    if client_id:
        complete_job(client_id=client_id, job=job, status='error',
                     data={'error': str(error)})
