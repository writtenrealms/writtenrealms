from __future__ import annotations

import logging
import time
from typing import Any

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models.functions import Substr

from core.abilities import definition_world
from core.socials import (
    SOCIAL_CATALOG_MAX_DEFINITIONS,
    SOCIAL_MESSAGE_FIELDS,
    SOCIAL_TEMPLATE_MAX_LENGTH,
    SocialDefinitionError,
    normalize_social_priority,
    validate_social_command,
    validate_social_definition,
)


logger = logging.getLogger(__name__)

SOCIAL_CATALOG_CACHE_TIMEOUT_SECONDS = 60 * 60
SOCIAL_CATALOG_VERSION_FLOOR = 1_000_000_000


def _cache_get(key: str) -> Any:
    try:
        return cache.get(key)
    except Exception:
        logger.warning("Social catalog cache read failed for %s.", key, exc_info=True)
        return None


def _cache_set(key: str, value: Any, *, timeout: int | None) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        logger.warning("Social catalog cache write failed for %s.", key, exc_info=True)


def _cache_delete_many(keys: list[str]) -> None:
    if not keys:
        return
    try:
        cache.delete_many(keys)
    except Exception:
        logger.warning("Social catalog cache invalidation failed.", exc_info=True)


def _catalog_version_key(world_id: int | str | None) -> str:
    return f"spawns.social_catalog.version.{world_id or 'unknown'}"


def _new_catalog_version() -> int:
    return max(int(time.time()), SOCIAL_CATALOG_VERSION_FLOOR)


def get_social_catalog_version(world_id: int | str | None) -> int:
    key = _catalog_version_key(world_id)
    version = _cache_get(key)
    try:
        normalized = int(version)
    except (TypeError, ValueError):
        normalized = 0
    if normalized >= SOCIAL_CATALOG_VERSION_FLOOR:
        return normalized

    normalized = _new_catalog_version()
    _cache_set(key, normalized, timeout=None)
    return normalized


def _catalog_cache_key(world_id: int, version: int) -> str:
    return f"spawns.social_catalog.{world_id}.{version}"


def _definition_cache_key(world_id: int, version: int, social_id: int) -> str:
    return f"spawns.social_definition.{world_id}.{version}.{social_id}"


def bump_social_catalog_version(world_id: int | str | None) -> None:
    if not world_id:
        return
    key = _catalog_version_key(world_id)
    try:
        version = int(cache.incr(key))
    except Exception:
        _cache_set(key, _new_catalog_version(), timeout=None)
        return
    if version < SOCIAL_CATALOG_VERSION_FLOOR:
        _cache_set(key, _new_catalog_version(), timeout=None)


def _invalidate_committed_social_catalog(world_id: int) -> None:
    """Publish a new version, then discard the bounded previous-version items."""
    old_version = get_social_catalog_version(world_id)
    old_catalog_key = _catalog_cache_key(world_id, old_version)
    old_catalog = _cache_get(old_catalog_key)

    # Bumping first prevents a concurrent reader from repopulating a key that
    # remains current after this commit.
    bump_social_catalog_version(world_id)

    stale_keys = [old_catalog_key]
    if isinstance(old_catalog, dict):
        for entry in old_catalog.get("entries") or []:
            try:
                social_id = int(entry["id"])
            except (KeyError, TypeError, ValueError):
                continue
            stale_keys.append(
                _definition_cache_key(world_id, old_version, social_id)
            )
    _cache_delete_many(stale_keys)


def invalidate_social_catalog(world_id: int | str | None) -> None:
    """Invalidate only after commit so rolled-back writes never poison cache state."""
    if not world_id:
        return
    normalized_world_id = int(world_id)
    transaction.on_commit(
        lambda: _invalidate_committed_social_catalog(normalized_world_id)
    )


def _empty_catalog(world_id: int | None = None) -> dict[str, Any]:
    return {
        "world_id": world_id,
        "entries": [],
        "alphabetical": [],
        "exact": {},
    }


def _bounded_social_rows(world_id: int, *, social_id: int | None = None):
    from builders.models import Social

    bounded_fields = {
        f"bounded_{field_name}": Substr(
            field_name,
            1,
            SOCIAL_TEMPLATE_MAX_LENGTH + 1,
        )
        for field_name in SOCIAL_MESSAGE_FIELDS
    }
    queryset = Social.objects.filter(world_id=world_id)
    if social_id is not None:
        queryset = queryset.filter(pk=social_id)
    return (
        queryset.annotate(**bounded_fields)
        .order_by("-priority", "cmd", "id")
        .values(
            "id",
            "cmd",
            "priority",
            *bounded_fields.keys(),
        )
    )


def _definition_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        command = validate_social_command(row.get("cmd"))
        priority = normalize_social_priority(row.get("priority"))
        values = {
            field_name: row.get(f"bounded_{field_name}") or ""
            for field_name in SOCIAL_MESSAGE_FIELDS
        }
        values = validate_social_definition(values)
        social_id = int(row["id"])
    except (KeyError, TypeError, ValueError, SocialDefinitionError) as error:
        logger.warning(
            "Ignoring invalid social definition %s: %s",
            row.get("id"),
            error,
        )
        return None
    return {
        "id": social_id,
        "command": command,
        "priority": priority,
        **values,
    }


def _build_social_catalog(
    world_id: int,
    *,
    version: int | None = None,
    populate_shared_cache: bool = False,
) -> dict[str, Any]:
    rows = list(
        _bounded_social_rows(world_id)[:SOCIAL_CATALOG_MAX_DEFINITIONS + 1]
    )
    if len(rows) > SOCIAL_CATALOG_MAX_DEFINITIONS:
        logger.warning(
            "World %s has more than the supported %s social definitions; "
            "the excess definitions are ignored.",
            world_id,
            SOCIAL_CATALOG_MAX_DEFINITIONS,
        )
        rows = rows[:SOCIAL_CATALOG_MAX_DEFINITIONS]

    definitions = [
        definition
        for row in rows
        if (definition := _definition_from_row(row)) is not None
    ]
    definitions.sort(
        key=lambda social: (
            -social["priority"],
            social["command"],
            social["id"],
        )
    )

    entries = [
        {
            "id": social["id"],
            "command": social["command"],
            "priority": social["priority"],
        }
        for social in definitions
    ]
    catalog = {
        "world_id": world_id,
        "entries": entries,
        "alphabetical": sorted(
            (entry["command"] for entry in entries),
        ),
        "exact": {
            entry["command"]: entry["id"]
            for entry in entries
        },
        # Kept only in the request-local result. The shared catalog contains
        # compact metadata; each bounded definition has its own cache shard.
        "_definitions": {
            social["id"]: social
            for social in definitions
        },
    }
    if populate_shared_cache and version is not None:
        for social in definitions:
            _cache_set(
                _definition_cache_key(world_id, version, social["id"]),
                social,
                timeout=SOCIAL_CATALOG_CACHE_TIMEOUT_SECONDS,
            )
    return catalog


def _shared_catalog_value(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in catalog.items()
        if key != "_definitions"
    }


def get_social_catalog(world: Any) -> dict[str, Any]:
    authored_world = definition_world(world)
    world_id = getattr(authored_world, "id", None)
    if not world_id:
        return _empty_catalog()
    world_id = int(world_id)

    # A transaction can see uncommitted definitions. Never publish or consume
    # shared catalog state from that snapshot; a rollback must leave no trace.
    if connection.in_atomic_block:
        return _build_social_catalog(world_id)

    version = get_social_catalog_version(world_id)
    cache_key = _catalog_cache_key(world_id, version)
    catalog = _cache_get(cache_key)
    if not isinstance(catalog, dict):
        catalog = _build_social_catalog(
            world_id,
            version=version,
            populate_shared_cache=True,
        )
        _cache_set(
            cache_key,
            _shared_catalog_value(catalog),
            timeout=SOCIAL_CATALOG_CACHE_TIMEOUT_SECONDS,
        )
    return catalog


def _load_social_definition(
    *,
    world_id: int,
    version: int | None,
    social_id: int,
) -> dict[str, Any] | None:
    cache_key = None
    if version is not None:
        cache_key = _definition_cache_key(world_id, version, social_id)
        cached = _cache_get(cache_key)
        if isinstance(cached, dict):
            return dict(cached)

    row = _bounded_social_rows(world_id, social_id=social_id).first()
    social = _definition_from_row(row) if row is not None else None
    if social is not None and cache_key is not None:
        _cache_set(
            cache_key,
            social,
            timeout=SOCIAL_CATALOG_CACHE_TIMEOUT_SECONDS,
        )
    return social


def resolve_social_for_command(world: Any, command: Any) -> dict[str, Any] | None:
    try:
        normalized = validate_social_command(command)
    except SocialDefinitionError:
        return None

    catalog = get_social_catalog(world)
    social_id = (catalog.get("exact") or {}).get(normalized)
    if social_id is None:
        # Entries are already ordered by priority, command, and id. Scanning a
        # hard-capped 512 compact entries avoids an O(command-length) prefix map.
        for entry in catalog.get("entries") or []:
            if str(entry.get("command") or "").startswith(normalized):
                social_id = entry.get("id")
                break
    try:
        social_id = int(social_id)
    except (TypeError, ValueError):
        return None

    ephemeral = catalog.get("_definitions") or {}
    if social_id in ephemeral:
        return dict(ephemeral[social_id])

    world_id = int(catalog.get("world_id") or 0)
    if not world_id:
        return None
    version = None if connection.in_atomic_block else get_social_catalog_version(world_id)
    return _load_social_definition(
        world_id=world_id,
        version=version,
        social_id=social_id,
    )


def list_social_commands(world: Any) -> list[str]:
    catalog = get_social_catalog(world)
    return [
        str(command)
        for command in (catalog.get("alphabetical") or [])[
            :SOCIAL_CATALOG_MAX_DEFINITIONS
        ]
    ]
