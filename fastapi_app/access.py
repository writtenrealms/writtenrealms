"""Use Django's bounded database admission cache at socket authentication."""
import logging

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def _user_admitted(user_id):
    import django
    from django.apps import apps
    from django.db import DatabaseError, close_old_connections

    # FastAPI is also imported by Django workers. Initialize only when this
    # process has not already loaded Django, on the synchronous ORM thread.
    if not apps.ready:
        django.setup()
    from system.access import user_admitted

    close_old_connections()
    try:
        return user_admitted(user_id)
    except DatabaseError:
        logger.warning('Socket admission unavailable; rejecting authentication.')
        return False
    finally:
        close_old_connections()


async def user_admitted(user_id):
    # Never block the socket event loop with a database lookup. Policy/account
    # caches expire within five seconds and retain at most 4096 entries.
    return await sync_to_async(_user_admitted, thread_sensitive=True)(user_id)
