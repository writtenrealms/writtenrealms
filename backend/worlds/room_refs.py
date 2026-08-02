from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Any, Literal

from django.db.models import Q

from worlds.models import Room, World


RoomReferenceKind = Literal["relative_id", "coordinates", "database_id"]


class RoomReferenceError(ValueError):
    """Raised when a room reference cannot be made portable."""


@dataclass(frozen=True)
class ParsedRoomReference:
    kind: RoomReferenceKind
    relative_id: int | None = None
    database_id: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None


_RELATIVE_ROOM_REF_RE = re.compile(r"^room@(\d+)$", re.IGNORECASE)
_COORDINATE_ROOM_REF_RE = re.compile(
    r"^room@\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*,\s*([+-]?\d+)$",
    re.IGNORECASE,
)
_DATABASE_ROOM_REF_RE = re.compile(r"^room\.(\d+)$", re.IGNORECASE)
_ROOM_REF_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_@-])"
    r"(?:"
    r"room@\s*[+-]?\d+\s*,\s*[+-]?\d+\s*,\s*[+-]?\d+"
    r"|room@\d+"
    r"|room\.\d+"
    r")"
    r"(?![A-Za-z0-9_@-]|\s*,\s*[+-]?\d)",
    re.IGNORECASE,
)
_ACTIVE_ROOM_OBJECT_CACHES: ContextVar[
    dict[int, dict[tuple[Any, ...], Room]] | None
] = ContextVar(
    "active_room_reference_object_caches",
    default=None,
)


def parse_room_reference(value: Any) -> ParsedRoomReference | None:
    """Parse a canonical or legacy typed room reference."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    coordinate_match = _COORDINATE_ROOM_REF_RE.fullmatch(text)
    if coordinate_match:
        x, y, z = (int(part) for part in coordinate_match.groups())
        return ParsedRoomReference(kind="coordinates", x=x, y=y, z=z)

    relative_match = _RELATIVE_ROOM_REF_RE.fullmatch(text)
    if relative_match:
        return ParsedRoomReference(
            kind="relative_id",
            relative_id=int(relative_match.group(1)),
        )

    database_match = _DATABASE_ROOM_REF_RE.fullmatch(text)
    if database_match:
        return ParsedRoomReference(
            kind="database_id",
            database_id=int(database_match.group(1)),
        )

    return None


def _room_reference_lookup(
    world: World | None,
    value: Any,
) -> dict[str, int] | None:
    parsed = parse_room_reference(value)
    world_id = getattr(world, "pk", None)
    if parsed is None or not world_id:
        return None

    if parsed.kind == "relative_id":
        if parsed.relative_id is None or parsed.relative_id <= 0:
            return None
        return {
            "world_id": world_id,
            "relative_id": parsed.relative_id,
        }
    if parsed.kind == "database_id":
        if parsed.database_id is None or parsed.database_id <= 0:
            return None
        return {
            "world_id": world_id,
            "pk": parsed.database_id,
        }
    if parsed.x is None or parsed.y is None or parsed.z is None:
        return None
    return {
        "world_id": world_id,
        "x": parsed.x,
        "y": parsed.y,
        "z": parsed.z,
    }


def _room_object_cache_key(
    parsed: ParsedRoomReference,
) -> tuple[Any, ...] | None:
    if parsed.kind == "relative_id":
        return ("relative_id", parsed.relative_id)
    if parsed.kind == "database_id":
        return ("database_id", parsed.database_id)
    if parsed.kind == "coordinates":
        return ("coordinates", parsed.x, parsed.y, parsed.z)
    return None


def build_room_reference_object_cache(
    rooms: list[Room],
) -> dict[tuple[Any, ...], Room]:
    """Index fully loaded rooms for one bounded manifest apply."""

    cache: dict[tuple[Any, ...], Room] = {}
    for room in rooms:
        cache[("database_id", room.id)] = room
        cache[("relative_id", room.relative_id)] = room
        cache[("coordinates", room.x, room.y, room.z)] = room
    return cache


def refresh_room_reference_object_cache(
    cache: dict[tuple[Any, ...], Room],
    room: Room,
) -> None:
    """Refresh one room's aliases after a manifest updates it."""

    stale_keys = [
        key
        for key, cached_room in cache.items()
        if cached_room.id == room.id
    ]
    for key in stale_keys:
        cache.pop(key, None)
    cache[("database_id", room.id)] = room
    cache[("relative_id", room.relative_id)] = room
    cache[("coordinates", room.x, room.y, room.z)] = room


@contextmanager
def use_room_reference_object_caches(
    caches: dict[int, dict[tuple[Any, ...], Room]],
):
    """Use request-local room identity maps during one manifest batch."""

    token = _ACTIVE_ROOM_OBJECT_CACHES.set(caches)
    try:
        yield
    finally:
        _ACTIVE_ROOM_OBJECT_CACHES.reset(token)


def _cached_room_reference(
    world: World | None,
    value: Any,
) -> Room | None:
    caches = _ACTIVE_ROOM_OBJECT_CACHES.get()
    world_id = getattr(world, "pk", None)
    if caches is None or not world_id:
        return None
    world_cache = caches.get(world_id)
    if world_cache is None:
        return None
    parsed = parse_room_reference(value)
    if parsed is None:
        return None
    cache_key = _room_object_cache_key(parsed)
    if cache_key is None:
        return None
    return world_cache.get(cache_key)


def resolve_room_reference(world: World | None, value: Any) -> Room | None:
    """Resolve a typed room reference within one authored world."""

    cached_room = _cached_room_reference(world, value)
    if cached_room is not None:
        return cached_room
    lookup = _room_reference_lookup(world, value)
    if lookup is None:
        return None
    return Room.objects.filter(**lookup).first()


def resolve_room_reference_id(
    world: World | None,
    value: Any,
) -> int | None:
    """Resolve a typed room reference to its database id without loading it."""

    cached_room = _cached_room_reference(world, value)
    if cached_room is not None:
        return cached_room.id
    lookup = _room_reference_lookup(world, value)
    if lookup is None:
        return None
    return Room.objects.filter(**lookup).values_list("id", flat=True).first()


def format_room_manifest_ref(room: Room) -> str:
    """Return the portable, movement-stable reference for a room."""

    relative_id = getattr(room, "relative_id", None)
    if isinstance(relative_id, bool):
        relative_id = None
    try:
        relative_id = int(relative_id)
    except (TypeError, ValueError):
        relative_id = None
    if relative_id is None or relative_id <= 0:
        raise RoomReferenceError(
            "A room must have a positive relative_id before it can be referenced.",
        )
    return f"room@{relative_id}"


def legacy_room_coordinate_ref(room: Room) -> str:
    """Return the legacy coordinate reference for compatibility tooling."""

    try:
        coordinates = (int(room.x), int(room.y), int(room.z))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RoomReferenceError(
            "A room must have integer coordinates before it can be referenced.",
        ) from exc
    return f"room@{coordinates[0]},{coordinates[1]},{coordinates[2]}"


def canonicalize_room_reference(
    world: World | None,
    value: Any,
) -> str | None:
    """Resolve one typed room reference and return its portable form."""

    room = resolve_room_reference(world, value)
    if room is None:
        return None
    return format_room_manifest_ref(room)


def _resolve_parsed_room_references(
    world: World | None,
    references: set[ParsedRoomReference],
) -> dict[ParsedRoomReference, Room]:
    """Batch-resolve parsed references for semantic-text normalization."""

    world_id = getattr(world, "pk", None)
    if not world_id or not references:
        return {}

    relative_ids = {
        ref.relative_id
        for ref in references
        if ref.kind == "relative_id"
        and ref.relative_id is not None
        and ref.relative_id > 0
    }
    database_ids = {
        ref.database_id
        for ref in references
        if ref.kind == "database_id"
        and ref.database_id is not None
        and ref.database_id > 0
    }
    coordinates = {
        (ref.x, ref.y, ref.z)
        for ref in references
        if ref.kind == "coordinates"
        and ref.x is not None
        and ref.y is not None
        and ref.z is not None
    }

    resolved: dict[ParsedRoomReference, Room] = {}
    rooms = Room.objects.filter(world_id=world_id).only(
        "id",
        "world_id",
        "relative_id",
        "x",
        "y",
        "z",
    )

    identity_queries = []
    if relative_ids:
        identity_queries.append(Q(relative_id__in=relative_ids))
    if database_ids:
        identity_queries.append(Q(pk__in=database_ids))
    if identity_queries:
        for room in rooms.filter(reduce(or_, identity_queries)).order_by("id"):
            relative_ref = ParsedRoomReference(
                kind="relative_id",
                relative_id=room.relative_id,
            )
            database_ref = ParsedRoomReference(
                kind="database_id",
                database_id=room.id,
            )
            if relative_ref in references:
                resolved[relative_ref] = room
            if database_ref in references:
                resolved[database_ref] = room

    if coordinates:
        coordinate_query = reduce(
            or_,
            (Q(x=x, y=y, z=z) for x, y, z in coordinates),
        )
        for room in rooms.filter(coordinate_query).order_by("id"):
            coordinate_ref = ParsedRoomReference(
                kind="coordinates",
                x=room.x,
                y=room.y,
                z=room.z,
            )
            if coordinate_ref in references:
                resolved.setdefault(coordinate_ref, room)

    return resolved


def canonicalize_room_references_in_text(
    world: World | None,
    text: str,
    *,
    strict: bool = False,
    canonical_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> str:
    """
    Rewrite literal typed room references in semantic text to portable refs.

    Dynamic or constructed references are intentionally outside this helper's
    scope. In non-strict mode, unresolved literal references are preserved.
    """

    if not isinstance(text, str) or not text:
        return text

    matches = list(_ROOM_REF_IN_TEXT_RE.finditer(text))
    if not matches:
        return text

    parsed_by_token = {
        match.group(0): parse_room_reference(match.group(0))
        for match in matches
    }
    parsed_references = {
        parsed
        for parsed in parsed_by_token.values()
        if parsed is not None
    }
    resolved = (
        _resolve_parsed_room_references(world, parsed_references)
        if canonical_ref_cache is None
        else {}
    )

    replacements: dict[str, str] = {}
    for token, parsed in parsed_by_token.items():
        canonical = None
        if parsed is not None and canonical_ref_cache is not None:
            if parsed.kind == "relative_id":
                cache_key = ("relative_id", parsed.relative_id)
            elif parsed.kind == "database_id":
                cache_key = ("database_id", parsed.database_id)
            else:
                cache_key = ("coordinates", parsed.x, parsed.y, parsed.z)
            canonical = canonical_ref_cache.get(cache_key)
        room = resolved.get(parsed) if parsed is not None else None
        if canonical is not None:
            replacements[token] = canonical
        elif room is not None:
            replacements[token] = format_room_manifest_ref(room)
        elif strict:
            raise RoomReferenceError(
                f"Room reference '{token}' does not resolve in this world.",
            )

    if not replacements:
        return text
    return _ROOM_REF_IN_TEXT_RE.sub(
        lambda match: replacements.get(match.group(0), match.group(0)),
        text,
    )


__all__ = [
    "ParsedRoomReference",
    "RoomReferenceError",
    "RoomReferenceKind",
    "build_room_reference_object_cache",
    "canonicalize_room_reference",
    "canonicalize_room_references_in_text",
    "format_room_manifest_ref",
    "legacy_room_coordinate_ref",
    "parse_room_reference",
    "refresh_room_reference_object_cache",
    "resolve_room_reference",
    "resolve_room_reference_id",
    "use_room_reference_object_caches",
]
