from __future__ import annotations

from django.core.cache import cache


def trigger_policy_cache_version_key(world_id: int | str | None) -> str:
    return f"spawns.trigger_policy_hooks.version.{world_id or 'unknown'}"


def get_trigger_policy_cache_version(world_id: int | str | None) -> int:
    key = trigger_policy_cache_version_key(world_id)
    version = cache.get(key)
    if version is None:
        version = 1
        cache.set(key, version, timeout=None)
    try:
        return int(version)
    except (TypeError, ValueError):
        return 1


def bump_trigger_policy_cache_version(world_id: int | str | None) -> None:
    if not world_id:
        return

    key = trigger_policy_cache_version_key(world_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, timeout=None)
