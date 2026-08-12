from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Any, Literal

from django.db.models import Q

from worlds.models import BIGINT_MAX, Room, World


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


@dataclass(frozen=True)
class ParsedBaseWorldRoomReference:
    """A portable room address scoped to an instance's direct base world."""

    relative_id: int


_RELATIVE_ROOM_REF_RE = re.compile(r"^room@([0-9]+)$", re.IGNORECASE)
_COORDINATE_ROOM_REF_RE = re.compile(
    r"^room@\s*([+-]?[0-9]+)\s*,\s*([+-]?[0-9]+)\s*,\s*([+-]?[0-9]+)$",
    re.IGNORECASE,
)
_DATABASE_ROOM_REF_RE = re.compile(r"^room\.([0-9]+)$", re.IGNORECASE)
_BASE_WORLD_ROOM_REF_RE = re.compile(
    r"^world@base/room@([0-9]+)$",
    re.IGNORECASE,
)
_ROOM_REF_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_@-])"
    r"(?:"
    r"room@\s*[+-]?\d+\s*,\s*[+-]?\d+\s*,\s*[+-]?\d+"
    r"|room@[+-]?\d+"
    r"|room\.\d+"
    r")"
    r"(?![A-Za-z0-9_@-]|\s*,\s*[+-]?\d)",
    re.IGNORECASE,
)
_BASE_WORLD_ROOM_ALIAS_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_@-])"
    r"world@base/"
    r"(?:"
    r"room@\s*[+-]?\d+\s*,\s*[+-]?\d+\s*,\s*[+-]?\d+"
    r"|room@\d+"
    r"|room\.\d+"
    r")"
    r"(?![A-Za-z0-9_@-]|\s*,\s*[+-]?\d)",
    re.IGNORECASE,
)
_COMMAND_SEGMENT_BOUNDARY_RE = re.compile(r"&&|\r\n?|\n")
_AMBIENT_COMMAND_WRAPPERS = {
    "cmd",
    "force",
    "rcmd",
    "wcmd",
    "zcmd",
}
_ACTIVE_ROOM_OBJECT_CACHES: ContextVar[
    dict[int, dict[tuple[Any, ...], Room]] | None
] = ContextVar(
    "active_room_reference_object_caches",
    default=None,
)
_INTEGER_MIN = -(1 << 31)
_INTEGER_MAX = (1 << 31) - 1


def _bounded_int(value: str, *, minimum: int, maximum: int) -> int | None:
    """Parse an ASCII integer without exceeding Python or database bounds."""

    text = str(value or "").strip()
    signless = text.lstrip("+-")
    if not signless or not signless.isascii() or not signless.isdecimal():
        return None
    significant_digits = signless.lstrip("0") or "0"
    max_digits = max(len(str(abs(minimum))), len(str(abs(maximum))))
    if len(significant_digits) > max_digits:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if minimum <= parsed <= maximum else None


def parse_room_reference(value: Any) -> ParsedRoomReference | None:
    """Parse a canonical or legacy typed room reference."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    coordinate_match = _COORDINATE_ROOM_REF_RE.fullmatch(text)
    if coordinate_match:
        coordinates = tuple(
            _bounded_int(
                part,
                minimum=_INTEGER_MIN,
                maximum=_INTEGER_MAX,
            )
            for part in coordinate_match.groups()
        )
        if any(part is None for part in coordinates):
            return None
        x, y, z = coordinates
        return ParsedRoomReference(kind="coordinates", x=x, y=y, z=z)

    relative_match = _RELATIVE_ROOM_REF_RE.fullmatch(text)
    if relative_match:
        relative_id = _bounded_int(
            relative_match.group(1),
            minimum=1,
            maximum=BIGINT_MAX,
        )
        if relative_id is None:
            return None
        return ParsedRoomReference(
            kind="relative_id",
            relative_id=relative_id,
        )

    database_match = _DATABASE_ROOM_REF_RE.fullmatch(text)
    if database_match:
        database_id = _bounded_int(
            database_match.group(1),
            minimum=1,
            maximum=BIGINT_MAX,
        )
        if database_id is None:
            return None
        return ParsedRoomReference(
            kind="database_id",
            database_id=database_id,
        )

    return None


def parse_base_world_room_reference(
    value: Any,
) -> ParsedBaseWorldRoomReference | None:
    """Parse ``world@base/room@N`` without accepting local legacy aliases."""

    if not isinstance(value, str):
        return None
    match = _BASE_WORLD_ROOM_REF_RE.fullmatch(value.strip())
    if match is None:
        return None
    relative_id = _bounded_int(
        match.group(1),
        minimum=1,
        maximum=BIGINT_MAX,
    )
    if relative_id is None:
        return None
    return ParsedBaseWorldRoomReference(relative_id=relative_id)


def direct_base_world_for_room_reference(world: World | None) -> World | None:
    """Return the direct authored base for a template or spawned instance."""

    if world is None:
        return None

    context_id = getattr(world, "context_id", None)
    if context_id:
        template = world._state.fields_cache.get("context")
        if template is None:
            template = (
                World.objects.select_related("instance_of")
                .filter(pk=context_id)
                .first()
            )
        if (
            template is None
            or getattr(template, "context_id", None)
            or not getattr(template, "instance_of_id", None)
        ):
            return None
        base_world = getattr(template, "instance_of", None)
    elif getattr(world, "instance_of_id", None):
        base_world = getattr(world, "instance_of", None)
    else:
        return None

    if (
        base_world is None
        or getattr(base_world, "context_id", None)
        or getattr(base_world, "instance_of_id", None)
    ):
        return None
    return base_world


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


def resolve_base_world_room_reference(
    world: World | None,
    value: Any,
) -> Room | None:
    """Resolve a portable base-world address from an instance world."""

    parsed = parse_base_world_room_reference(value)
    base_world = direct_base_world_for_room_reference(world)
    if parsed is None or base_world is None:
        return None
    return resolve_room_reference(
        base_world,
        f"room@{parsed.relative_id}",
    )


def resolve_base_world_room_reference_id(
    world: World | None,
    value: Any,
) -> int | None:
    """Resolve a portable base-world address without loading its room."""

    parsed = parse_base_world_room_reference(value)
    base_world = direct_base_world_for_room_reference(world)
    if parsed is None or base_world is None:
        return None
    return resolve_room_reference_id(
        base_world,
        f"room@{parsed.relative_id}",
    )


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


def format_base_world_room_manifest_ref(room: Room) -> str:
    """Return the portable base-world form for an already validated room."""

    return f"world@base/{format_room_manifest_ref(room)}"


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


def canonicalize_base_world_room_reference(
    world: World | None,
    value: Any,
) -> str | None:
    """Resolve a base-world address and return its portable canonical form."""

    room = resolve_base_world_room_reference(world, value)
    if room is None:
        return None
    return format_base_world_room_manifest_ref(room)


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

    scoped_spans = [
        match.span()
        for match in _BASE_WORLD_ROOM_ALIAS_IN_TEXT_RE.finditer(text)
    ]
    matches = [
        match
        for match in _ROOM_REF_IN_TEXT_RE.finditer(text)
        if not any(
            start <= match.start() < end
            for start, end in scoped_spans
        )
    ]
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
        lambda match: (
            match.group(0)
            if any(
                start <= match.start() < end
                for start, end in scoped_spans
            )
            else replacements.get(match.group(0), match.group(0))
        ),
        text,
    )


def _command_segment_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for boundary in _COMMAND_SEGMENT_BOUNDARY_RE.finditer(text):
        ranges.append((start, boundary.start()))
        start = boundary.end()
    ranges.append((start, len(text)))
    return ranges


def _dispatched_command_name(segment: str) -> str | None:
    command = segment.strip()
    if not command:
        return None
    first_token = command.split(maxsplit=1)[0].lower()
    if first_token.lstrip("/") in _AMBIENT_COMMAND_WRAPPERS:
        _, separator, nested = command.partition("--")
        if not separator or not nested.strip():
            return None
        command = nested.strip()
        first_token = command.split(maxsplit=1)[0].lower()
    return first_token


def canonicalize_command_room_references_in_text(
    world: World | None,
    text: str,
    *,
    strict: bool = False,
    canonical_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_canonical_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> str:
    """
    Canonicalize room addresses in authored command text.

    A ``world@base/room@N`` token is base-scoped only when it belongs to an
    ``exitinstance`` command (directly or through an ambient ``/cmd``-style
    wrapper). Other commands retain the token verbatim. Ordinary ``room@N``
    references continue to resolve in ``world``.
    """

    if not isinstance(text, str) or not text:
        return text

    scoped_matches = list(_BASE_WORLD_ROOM_ALIAS_IN_TEXT_RE.finditer(text))
    interpreted_matches: list[re.Match[str]] = []
    if scoped_matches:
        segment_ranges = _command_segment_ranges(text)
        segment_index = 0
        for match in scoped_matches:
            while (
                segment_index + 1 < len(segment_ranges)
                and match.start() >= segment_ranges[segment_index][1]
            ):
                segment_index += 1
            start, end = segment_ranges[segment_index]
            if not (start <= match.start() < end):
                continue
            if _dispatched_command_name(text[start:end]) == "/exitinstance":
                interpreted_matches.append(match)

    replacements: dict[tuple[int, int], str] = {}
    if interpreted_matches:
        parsed_by_span = {
            match.span(): parse_base_world_room_reference(match.group(0))
            for match in interpreted_matches
        }
        invalid_match = next(
            (
                match
                for match in interpreted_matches
                if parsed_by_span[match.span()] is None
            ),
            None,
        )
        if invalid_match is not None and strict:
            raise RoomReferenceError(
                "Base-world room destinations must use "
                "'world@base/room@<positive-relative-id>'; database and "
                "coordinate aliases are not supported.",
            )

        valid_matches = [
            match
            for match in interpreted_matches
            if parsed_by_span[match.span()] is not None
        ]
        base_world = direct_base_world_for_room_reference(world)
        if valid_matches and base_world is None and strict:
            raise RoomReferenceError(
                "A world@base room destination requires a direct authored "
                "base world.",
            )

        resolved: dict[ParsedRoomReference, Room] = {}
        if valid_matches and base_world is not None and base_canonical_ref_cache is None:
            references = {
                ParsedRoomReference(
                    kind="relative_id",
                    relative_id=parsed_by_span[match.span()].relative_id,
                )
                for match in valid_matches
            }
            unresolved_references = set(references)
            for reference in references:
                cached_room = _cached_room_reference(
                    base_world,
                    f"room@{reference.relative_id}",
                )
                if cached_room is not None:
                    resolved[reference] = cached_room
                    unresolved_references.discard(reference)
            resolved.update(
                _resolve_parsed_room_references(
                    base_world,
                    unresolved_references,
                )
            )

        for match in valid_matches:
            parsed = parsed_by_span[match.span()]
            cache_key = ("relative_id", parsed.relative_id)
            canonical = (
                base_canonical_ref_cache.get(cache_key)
                if base_canonical_ref_cache is not None
                else None
            )
            room = resolved.get(
                ParsedRoomReference(
                    kind="relative_id",
                    relative_id=parsed.relative_id,
                )
            )
            if canonical is not None:
                replacements[match.span()] = f"world@base/{canonical}"
            elif room is not None:
                replacements[match.span()] = (
                    format_base_world_room_manifest_ref(room)
                )
            elif strict:
                raise RoomReferenceError(
                    f"Base-world room reference '{match.group(0)}' does not "
                    "resolve in the direct base world.",
                )

    if replacements:
        pieces: list[str] = []
        cursor = 0
        for (start, end), replacement in sorted(replacements.items()):
            pieces.append(text[cursor:start])
            pieces.append(replacement)
            cursor = end
        pieces.append(text[cursor:])
        text = "".join(pieces)

    return canonicalize_room_references_in_text(
        world,
        text,
        strict=strict,
        canonical_ref_cache=canonical_ref_cache,
    )


__all__ = [
    "ParsedBaseWorldRoomReference",
    "ParsedRoomReference",
    "RoomReferenceError",
    "RoomReferenceKind",
    "build_room_reference_object_cache",
    "canonicalize_base_world_room_reference",
    "canonicalize_command_room_references_in_text",
    "canonicalize_room_reference",
    "canonicalize_room_references_in_text",
    "direct_base_world_for_room_reference",
    "format_base_world_room_manifest_ref",
    "format_room_manifest_ref",
    "legacy_room_coordinate_ref",
    "parse_base_world_room_reference",
    "parse_room_reference",
    "refresh_room_reference_object_cache",
    "resolve_base_world_room_reference",
    "resolve_base_world_room_reference_id",
    "resolve_room_reference",
    "resolve_room_reference_id",
    "use_room_reference_object_caches",
]
