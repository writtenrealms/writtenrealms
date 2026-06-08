from __future__ import annotations

import time

from django.core.cache import cache
from django.db import transaction

TRIGGER_POLICY_CACHE_VERSION_FLOOR = 1_000_000_000


def trigger_policy_cache_version_key(world_id: int | str | None) -> str:
    return f"spawns.trigger_policy_hooks.version.{world_id or 'unknown'}"


def _new_trigger_policy_cache_version() -> int:
    return max(int(time.time()), TRIGGER_POLICY_CACHE_VERSION_FLOOR)


def get_trigger_policy_cache_version(world_id: int | str | None) -> int:
    key = trigger_policy_cache_version_key(world_id)
    version = cache.get(key)
    try:
        normalized_version = int(version)
    except (TypeError, ValueError):
        normalized_version = 0

    if normalized_version >= TRIGGER_POLICY_CACHE_VERSION_FLOOR:
        return normalized_version

    normalized_version = _new_trigger_policy_cache_version()
    cache.set(key, normalized_version, timeout=None)
    return normalized_version


def bump_trigger_policy_cache_version(world_id: int | str | None) -> None:
    if not world_id:
        return

    key = trigger_policy_cache_version_key(world_id)
    try:
        version = int(cache.incr(key))
    except (TypeError, ValueError):
        cache.set(key, _new_trigger_policy_cache_version(), timeout=None)
        return

    if version < TRIGGER_POLICY_CACHE_VERSION_FLOOR:
        cache.set(key, _new_trigger_policy_cache_version(), timeout=None)


def bump_trigger_policy_cache_version_on_commit(world_id: int | str | None) -> None:
    if not world_id:
        return

    transaction.on_commit(lambda: bump_trigger_policy_cache_version(world_id))
