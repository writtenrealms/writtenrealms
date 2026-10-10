"""Platform admission, with bounded process-local caches on hot game paths.

Changes become effective in other workers within five seconds. A process does
not retain a user/email cache entry for longer than that, or more than 4096
entries. Staff can always recover a closed installation.
"""
from collections import OrderedDict
from threading import RLock
from time import monotonic

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.exceptions import PermissionDenied

from core.access_policy import access_denial_detail, email_is_allowed

ACCESS_CACHE_SECONDS = 5
_CACHE_LIMIT = 4096
_cache = OrderedDict()
_cache_lock = RLock()


def clear_access_cache(*args, **kwargs):
    with _cache_lock:
        _cache.clear()


def _cached(key, loader):
    now = monotonic()
    with _cache_lock:
        found = _cache.get(key)
        if found and found[0] > now:
            _cache.move_to_end(key)
            return found[1]
    # Concurrent misses may duplicate one bounded lookup, but unrelated users
    # never wait on a database query while holding the process-wide lock.
    value = loader()
    with _cache_lock:
        _cache[key] = (now + ACCESS_CACHE_SECONDS, value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return value


def access_mode():
    from system.models import SiteControl
    return _cached(('mode',), lambda: SiteControl.objects.filter(
        name='prod').values_list('access_mode', flat=True).first() or 'open')


def email_admitted(email, *, is_staff=False):
    if is_staff:
        return True
    normalized = email.strip().casefold() if isinstance(email, str) else ''
    if not email_is_allowed(normalized, settings.ACCESS_EMAIL_ALLOWLIST):
        return False
    mode = access_mode()
    if mode == 'open':
        return True
    if mode == 'closed' or not normalized:
        return False
    from system.models import AccessAllowlistEntry
    return _cached(('email', normalized), lambda: AccessAllowlistEntry.objects.filter(
        email=normalized).exists())


def require_platform_access(email=None, *, user=None):
    if user is not None:
        admitted = email_admitted(user.email, is_staff=user.is_staff)
    else:
        # Email login has no authenticated identity yet. Only an existing
        # active staff account receives the recovery exception.
        staff = False
        if email:
            staff = get_user_model().objects.filter(
                email__iexact=email, is_staff=True, is_active=True).exists()
        admitted = email_admitted(email, is_staff=staff)
    if not admitted:
        raise PermissionDenied(access_denial_detail(access_mode()))


def player_admitted(player_id, user_id=None):
    # A gateway command identifies its character. Resolve its current owner
    # inside the worker, without trusting any client-provided staff flag.
    def load():
        from spawns.models import Player
        owner = Player.objects.filter(pk=player_id).values(
            'user_id', 'user__email', 'user__is_staff', 'user__is_active').first()
        return owner
    owner = _cached(('player', player_id), load)
    return bool(owner and (user_id is None or owner['user_id'] == user_id)
                and owner['user__is_active'] and email_admitted(
        owner['user__email'], is_staff=owner['user__is_staff']))
