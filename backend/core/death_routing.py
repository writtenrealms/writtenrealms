from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import logging
import math
import re
import threading
from typing import Any, Callable, Mapping

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.condition_dsl import validate_condition_payload


DEATH_ROUTING_CACHE_VERSION = 2
DEATH_ROUTING_SOURCE_LOCAL = "local"
DEATH_ROUTING_SOURCE_BASE_WORLD = "base_world"
DEATH_ROUTING_SOURCES = (
    DEATH_ROUTING_SOURCE_LOCAL,
    DEATH_ROUTING_SOURCE_BASE_WORLD,
)

MAX_AUTHORED_ROUTES = 32
MAX_SELECTOR_VALUES = 32
MAX_CONDITION_NODES = 256
MAX_CONDITION_DEPTH = 16
MAX_TOTAL_LITERAL_VALUES = 256
MAX_STATE_PATH_SEGMENTS = 8
MAX_STATE_PATH_LENGTH = 255
MAX_STRING_LITERAL_LENGTH = 256
MAX_SAFE_INTEGER = 9007199254740991
MAX_RETAINED_SNAPSHOTS_PER_CONFIG = 8

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_STATE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ROOM_COORD_REF_RE = re.compile(
    r"^room@(?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)$",
    re.IGNORECASE,
)
_ROOM_ID_REF_RE = re.compile(r"^room\.(?P<id>\d+)$", re.IGNORECASE)
_ZONE_PORTABLE_REF_RE = re.compile(r"^zone@(?P<relative_id>\d+)$", re.IGNORECASE)
_ZONE_ID_REF_RE = re.compile(r"^zone\.(?P<id>\d+)$", re.IGNORECASE)


def _death_routing_advisory_lock_key(config_id: int) -> int:
    lock_scope = f"death-routing-config:{int(config_id)}".encode("utf-8")
    return int.from_bytes(
        hashlib.blake2b(lock_scope, digest_size=8).digest(),
        byteorder="big",
        signed=True,
    )


def death_routing_config_ids_for_world(*, world, config=None) -> tuple[int, ...]:
    """
    Return the routing configs that must move as one concurrency family.

    Instance deaths may use either the local policy or the base policy. Locking
    both candidates before reading ``death_routing_source`` makes source
    switches and policy publication atomic from the death resolver's point of
    view.
    """
    local_config_id = getattr(config, "pk", None) or getattr(
        world, "config_id", None
    )
    config_ids = {
        int(local_config_id)
        for local_config_id in [local_config_id]
        if local_config_id is not None
    }
    if getattr(world, "instance_of_id", None):
        base_world = getattr(world, "instance_of", None)
        base_config_id = getattr(base_world, "config_id", None)
        if base_config_id is None:
            from worlds.models import World

            base_config_id = (
                World.objects.filter(pk=world.instance_of_id)
                .values_list("config_id", flat=True)
                .first()
            )
        if base_config_id is not None:
            config_ids.add(int(base_config_id))
    return tuple(sorted(config_ids))


def acquire_death_routing_config_locks(
    config_ids,
    *,
    shared: bool,
) -> tuple[int, ...]:
    """
    Acquire transaction-scoped routing locks in a stable global order.

    PostgreSQL shared advisory locks let deaths for the same world run
    concurrently. Builder mutations take the exclusive form and therefore
    wait for in-flight deaths before retiring identifier references. The
    select-for-update fallback preserves correctness on development databases
    without PostgreSQL advisory locks.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Death-routing config locks require an active database transaction."
        )
    normalized_ids = tuple(sorted({
        int(config_id)
        for config_id in config_ids
        if config_id is not None
    }))
    if not normalized_ids:
        raise DeathRoutingPlanError("No death-routing config is available to lock.")

    if connection.vendor == "postgresql":
        function_name = (
            "pg_advisory_xact_lock_shared"
            if shared
            else "pg_advisory_xact_lock"
        )
        with connection.cursor() as cursor:
            for config_id in normalized_ids:
                cursor.execute(
                    f"SELECT {function_name}(%s)",
                    [_death_routing_advisory_lock_key(config_id)],
                )
    else:
        from worlds.models import WorldConfig

        # Non-PostgreSQL environments do not expose a shared row-lock mode.
        # Serializing these rare test/development deaths is preferable to
        # weakening the publication guarantee.
        list(
            WorldConfig.objects.select_for_update()
            .filter(pk__in=normalized_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    return normalized_ids


class DeathRoutingValidationError(ValueError):
    pass


class DeathRoutingPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledCondition:
    operator: str
    source: str | None = None
    state_segments: tuple[str, ...] = ()
    value: Any = None
    values: tuple[Any, ...] = ()
    children: tuple["CompiledCondition", ...] = ()


@dataclass(frozen=True)
class DeathRoutingRouteSpec:
    position: int
    condition: dict[str, Any]
    compiled_condition: dict[str, Any]
    destination_room_id: int
    core_faction_ids: frozenset[int]
    origin_zone_ids: frozenset[int]
    state_paths: frozenset[str]


@dataclass(frozen=True)
class DeathRoutingCompilation:
    routes: tuple[DeathRoutingRouteSpec, ...]

    @property
    def enabled(self) -> bool:
        return bool(self.routes)

    @property
    def required_state_paths(self) -> tuple[str, ...]:
        return tuple(sorted({
            path
            for route in self.routes
            for path in route.state_paths
        }))


@dataclass(frozen=True)
class CompiledDeathRoutingRoute:
    position: int
    condition: CompiledCondition
    destination_room_id: int


@dataclass(frozen=True)
class CompiledDeathRoutingPlan:
    config_id: int
    generation: int
    cache_version: int
    fallback_room_id: int | None
    enabled: bool
    routes: tuple[CompiledDeathRoutingRoute, ...]
    required_state_paths: tuple[str, ...]
    load_error: str | None = None


@dataclass(frozen=True)
class DeathRoutingResolution:
    room_id: int | None
    reason: str
    fallback_reason: str | None
    matched_route_position: int | None


DestinationResolver = Callable[[Any, str], int]
CoreFactionResolver = Callable[[Any, str], tuple[int, str]]
ArchetypeResolver = Callable[[Any, str], str]
ZoneResolver = Callable[[Any, str], tuple[int, str]]


@dataclass
class _CompileBudget:
    nodes: int = 0
    literals: int = 0


@dataclass
class _ConditionBuild:
    canonical: dict[str, Any]
    compiled: dict[str, Any]
    core_faction_ids: set[int]
    origin_zone_ids: set[int]
    state_paths: set[str]


def _normalize_code(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DeathRoutingValidationError(f"{field_name} must be a string.")
    normalized = value.strip().lower()
    if not _CODE_RE.fullmatch(normalized):
        raise DeathRoutingValidationError(
            f"{field_name} must match [a-z][a-z0-9_-]{{0,63}}."
        )
    return normalized


def _normalize_state_path(value: Any, *, field_name: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str):
        raise DeathRoutingValidationError(f"{field_name} must be a condition path.")
    normalized = value.strip()
    prefix = "state.character."
    if not normalized.startswith(prefix) or len(normalized) > MAX_STATE_PATH_LENGTH:
        raise DeathRoutingValidationError(
            f"{field_name} must use state.character.* and be at most "
            f"{MAX_STATE_PATH_LENGTH} characters."
        )
    segments = tuple(normalized[len(prefix):].split("."))
    if (
        not segments
        or len(segments) > MAX_STATE_PATH_SEGMENTS
        or any(not _STATE_SEGMENT_RE.fullmatch(segment) for segment in segments)
    ):
        raise DeathRoutingValidationError(
            f"{field_name} has an invalid character-state path."
        )
    return prefix + ".".join(segments), segments


def _is_dynamic_rhs(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value.strip()) >= 3
        and value.strip().startswith("{")
        and value.strip().endswith("}")
    )


def _normalize_state_literal(value: Any, *, field_name: str) -> Any:
    if _is_dynamic_rhs(value):
        raise DeathRoutingValidationError(
            f"{field_name} cannot use a dynamic right-hand path."
        )
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise DeathRoutingValidationError(
                f"{field_name} exceeds the safe integer range."
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER:
            raise DeathRoutingValidationError(
                f"{field_name} must be a finite safe number."
            )
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LITERAL_LENGTH:
            raise DeathRoutingValidationError(
                f"{field_name} may contain at most "
                f"{MAX_STRING_LITERAL_LENGTH} characters."
            )
        return value
    raise DeathRoutingValidationError(
        f"{field_name} must be a JSON scalar literal."
    )


def _normalize_level_literal(value: Any, *, field_name: str) -> int:
    if (
        _is_dynamic_rhs(value)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_SAFE_INTEGER
    ):
        raise DeathRoutingValidationError(
            f"{field_name} must be a positive integer within the safe "
            "integer range."
        )
    return value


def _literal_identity(value: Any) -> tuple[str, Any]:
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    return ("string", value)


def _normalize_operand_list(
    *,
    operator: str,
    raw_value: Any,
    field_name: str,
    normalize: Callable[[Any, str], tuple[Any, Any]],
) -> tuple[list[Any], list[Any]]:
    if operator == "in":
        if not isinstance(raw_value, list) or not raw_value:
            raise DeathRoutingValidationError(
                f"{field_name} must be a non-empty list."
            )
        if len(raw_value) > MAX_SELECTOR_VALUES:
            raise DeathRoutingValidationError(
                f"{field_name} may contain at most {MAX_SELECTOR_VALUES} values."
            )
        raw_values = raw_value
    else:
        raw_values = [raw_value]

    canonical_values: list[Any] = []
    compiled_values: list[Any] = []
    seen: set[tuple[str, Any]] = set()
    for index, raw_item in enumerate(raw_values):
        item_field = field_name if operator != "in" else f"{field_name}[{index}]"
        canonical, compiled = normalize(raw_item, item_field)
        identity = _literal_identity(compiled)
        if identity in seen:
            raise DeathRoutingValidationError(
                f"{field_name} contains a duplicate normalized value."
            )
        seen.add(identity)
        canonical_values.append(canonical)
        compiled_values.append(compiled)
    return canonical_values, compiled_values


def _merge_builds(
    canonical: dict[str, Any],
    compiled: dict[str, Any],
    children: list[_ConditionBuild],
) -> _ConditionBuild:
    return _ConditionBuild(
        canonical=canonical,
        compiled=compiled,
        core_faction_ids=set().union(
            *(child.core_faction_ids for child in children)
        ),
        origin_zone_ids=set().union(
            *(child.origin_zone_ids for child in children)
        ),
        state_paths=set().union(*(child.state_paths for child in children)),
    )


def _compile_condition(
    value: Any,
    *,
    field_name: str,
    route_is_final: bool,
    resolve_core_faction: CoreFactionResolver,
    resolve_archetype: ArchetypeResolver,
    resolve_zone: ZoneResolver,
    budget: _CompileBudget,
    depth: int = 0,
) -> _ConditionBuild:
    budget.nodes += 1
    if budget.nodes > MAX_CONDITION_NODES or depth > MAX_CONDITION_DEPTH:
        raise DeathRoutingValidationError(
            f"{field_name} exceeds the death-routing condition bounds."
        )
    if not isinstance(value, dict) or len(value) != 1:
        raise DeathRoutingValidationError(
            f"{field_name} must contain exactly one supported operator."
        )
    operator = next(iter(value))

    if operator == "always":
        if depth != 0 or not route_is_final or value.get("always") is not True:
            raise DeathRoutingValidationError(
                f"{field_name}.always is only allowed as true on the final route."
            )
        return _ConditionBuild(
            canonical={"always": True},
            compiled={"op": "always"},
            core_faction_ids=set(),
            origin_zone_ids=set(),
            state_paths=set(),
        )

    if operator in {"all", "any"}:
        raw_children = value.get(operator)
        if not isinstance(raw_children, list) or not raw_children:
            raise DeathRoutingValidationError(
                f"{field_name}.{operator} must be a non-empty list."
            )
        children = [
            _compile_condition(
                child,
                field_name=f"{field_name}.{operator}[{index}]",
                route_is_final=route_is_final,
                resolve_core_faction=resolve_core_faction,
                resolve_archetype=resolve_archetype,
                resolve_zone=resolve_zone,
                budget=budget,
                depth=depth + 1,
            )
            for index, child in enumerate(raw_children)
        ]
        return _merge_builds(
            {operator: [child.canonical for child in children]},
            {"op": operator, "children": [child.compiled for child in children]},
            children,
        )

    if operator == "not":
        child = _compile_condition(
            value.get("not"),
            field_name=f"{field_name}.not",
            route_is_final=route_is_final,
            resolve_core_faction=resolve_core_faction,
            resolve_archetype=resolve_archetype,
            resolve_zone=resolve_zone,
            budget=budget,
            depth=depth + 1,
        )
        return _merge_builds(
            {"not": child.canonical},
            {"op": "not", "children": [child.compiled]},
            [child],
        )

    if operator not in {"eq", "in", "gte", "lte"}:
        raise DeathRoutingValidationError(
            f"{field_name} only supports always, all, any, not, eq, in, "
            "gte, and lte."
        )
    operands = value.get(operator)
    if not isinstance(operands, (list, tuple)) or len(operands) != 2:
        raise DeathRoutingValidationError(
            f"{field_name}.{operator} must be a two-item list."
        )
    raw_path, raw_rhs = operands
    path = str(raw_path or "").strip()
    if operator in {"gte", "lte"} and path != "player.level":
        raise DeathRoutingValidationError(
            f"{field_name}.{operator} is only supported for player.level."
        )

    core_faction_ids: set[int] = set()
    origin_zone_ids: set[int] = set()
    state_paths: set[str] = set()
    compiled_extra: dict[str, Any] = {}

    if path == "player.core_faction":
        def normalize_faction(item, item_field):
            if item is None or _is_dynamic_rhs(item):
                raise DeathRoutingValidationError(
                    f"{item_field} must be a literal core-faction code."
                )
            faction_id, code = resolve_core_faction(item, item_field)
            core_faction_ids.add(int(faction_id))
            return code, int(faction_id)

        canonical_values, compiled_values = _normalize_operand_list(
            operator=operator,
            raw_value=raw_rhs,
            field_name=f"{field_name}.{operator}[1]",
            normalize=normalize_faction,
        )
        source = "core_faction"
    elif path == "player.archetype":
        def normalize_archetype(item, item_field):
            if item is None or _is_dynamic_rhs(item):
                raise DeathRoutingValidationError(
                    f"{item_field} must be a literal archetype code."
                )
            normalized = resolve_archetype(item, item_field)
            return normalized, normalized

        canonical_values, compiled_values = _normalize_operand_list(
            operator=operator,
            raw_value=raw_rhs,
            field_name=f"{field_name}.{operator}[1]",
            normalize=normalize_archetype,
        )
        source = "archetype"
    elif path == "player.level":
        def normalize_level(item, item_field):
            normalized = _normalize_level_literal(
                item,
                field_name=item_field,
            )
            return normalized, normalized

        canonical_values, compiled_values = _normalize_operand_list(
            operator=operator,
            raw_value=raw_rhs,
            field_name=f"{field_name}.{operator}[1]",
            normalize=normalize_level,
        )
        source = "level"
    elif path == "zone.id":
        def normalize_zone(item, item_field):
            if item is None or _is_dynamic_rhs(item):
                raise DeathRoutingValidationError(
                    f"{item_field} must be a literal zone reference."
                )
            zone_id, zone_ref = resolve_zone(item, item_field)
            origin_zone_ids.add(int(zone_id))
            return zone_ref, int(zone_id)

        canonical_values, compiled_values = _normalize_operand_list(
            operator=operator,
            raw_value=raw_rhs,
            field_name=f"{field_name}.{operator}[1]",
            normalize=normalize_zone,
        )
        source = "origin_zone"
    else:
        canonical_path, state_segments = _normalize_state_path(
            raw_path,
            field_name=f"{field_name}.{operator}[0]",
        )

        def normalize_state(item, item_field):
            normalized = _normalize_state_literal(item, field_name=item_field)
            return normalized, normalized

        canonical_values, compiled_values = _normalize_operand_list(
            operator=operator,
            raw_value=raw_rhs,
            field_name=f"{field_name}.{operator}[1]",
            normalize=normalize_state,
        )
        path = canonical_path
        state_paths.add(canonical_path)
        source = "state"
        compiled_extra["segments"] = list(state_segments)

    budget.literals += len(compiled_values)
    if budget.literals > MAX_TOTAL_LITERAL_VALUES:
        raise DeathRoutingValidationError(
            f"{field_name} exceeds the policy limit of "
            f"{MAX_TOTAL_LITERAL_VALUES} literal values."
        )
    canonical_rhs = (
        canonical_values if operator == "in" else canonical_values[0]
    )
    compiled = {"op": operator, "source": source, **compiled_extra}
    if operator == "in":
        compiled["values"] = compiled_values
    else:
        compiled["value"] = compiled_values[0]
    return _ConditionBuild(
        canonical={operator: [path, canonical_rhs]},
        compiled=compiled,
        core_faction_ids=core_faction_ids,
        origin_zone_ids=origin_zone_ids,
        state_paths=state_paths,
    )


def compile_death_routing_policy_value(
    policy: Any,
    *,
    resolve_destination: DestinationResolver,
    resolve_core_faction: CoreFactionResolver,
    resolve_archetype: ArchetypeResolver,
    resolve_zone: ZoneResolver,
    field_name: str = "spec.death_routing",
) -> DeathRoutingCompilation:
    if policy is None:
        return DeathRoutingCompilation(routes=())
    if not isinstance(policy, dict):
        raise DeathRoutingValidationError(f"{field_name} must be a mapping or null.")
    unknown_fields = sorted(set(policy) - {"routes"})
    if unknown_fields:
        raise DeathRoutingValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    raw_routes = policy.get("routes", [])
    if not isinstance(raw_routes, list):
        raise DeathRoutingValidationError(f"{field_name}.routes must be a list.")
    if len(raw_routes) > MAX_AUTHORED_ROUTES:
        raise DeathRoutingValidationError(
            f"{field_name}.routes may contain at most {MAX_AUTHORED_ROUTES} routes."
        )

    budget = _CompileBudget()
    routes: list[DeathRoutingRouteSpec] = []
    for position, route in enumerate(raw_routes):
        route_field = f"{field_name}.routes[{position}]"
        if not isinstance(route, dict):
            raise DeathRoutingValidationError(f"{route_field} must be a mapping.")
        unknown_route_fields = sorted(set(route) - {"when", "destination"})
        if unknown_route_fields:
            raise DeathRoutingValidationError(
                f"{route_field} has unsupported field(s): "
                f"{', '.join(unknown_route_fields)}."
            )
        if "when" not in route or "destination" not in route:
            raise DeathRoutingValidationError(
                f"{route_field} requires when and destination."
            )
        try:
            validate_condition_payload(
                route.get("when"),
                field_name=f"{route_field}.when",
            )
        except ValueError as exc:
            raise DeathRoutingValidationError(str(exc)) from exc
        condition = _compile_condition(
            route.get("when"),
            field_name=f"{route_field}.when",
            route_is_final=position == len(raw_routes) - 1,
            resolve_core_faction=resolve_core_faction,
            resolve_archetype=resolve_archetype,
            resolve_zone=resolve_zone,
            budget=budget,
        )
        destination_room_id = resolve_destination(
            route.get("destination"),
            f"{route_field}.destination",
        )
        routes.append(
            DeathRoutingRouteSpec(
                position=position,
                condition=condition.canonical,
                compiled_condition=condition.compiled,
                destination_room_id=int(destination_room_id),
                core_faction_ids=frozenset(condition.core_faction_ids),
                origin_zone_ids=frozenset(condition.origin_zone_ids),
                state_paths=frozenset(condition.state_paths),
            )
        )
    return DeathRoutingCompilation(routes=tuple(routes))


def _resolve_destination_for_world(world, value: Any, field_name: str) -> int:
    from worlds.models import Room

    room = None
    if isinstance(value, Room):
        room = value
    elif isinstance(value, int) and not isinstance(value, bool):
        room = Room.objects.filter(world=world, pk=value).only("id", "world_id").first()
    else:
        text = str(value or "").strip()
        id_match = _ROOM_ID_REF_RE.fullmatch(text)
        coord_match = _ROOM_COORD_REF_RE.fullmatch(text)
        if id_match:
            room = Room.objects.filter(
                world=world,
                pk=int(id_match.group("id")),
            ).only("id", "world_id").first()
        elif coord_match:
            room = Room.objects.filter(
                world=world,
                x=int(coord_match.group("x")),
                y=int(coord_match.group("y")),
                z=int(coord_match.group("z")),
            ).only("id", "world_id").first()
    if room is None or room.world_id != world.id:
        raise DeathRoutingValidationError(
            f"{field_name} must reference a room in this world."
        )
    return int(room.id)


def _resolve_core_faction_for_world(base_world, value: Any, field_name: str):
    from builders.models import FACTION_TYPE_CORE, Faction

    normalized = _normalize_code(value, field_name=field_name)
    factions = list(
        Faction.objects.filter(world=base_world, code__iexact=normalized)
        .filter(Q(type=FACTION_TYPE_CORE) | Q(is_core=True))
        .only("id", "code")[:2]
    )
    if not factions:
        raise DeathRoutingValidationError(
            f"{field_name} does not resolve to a core faction in the base world."
        )
    if len(factions) > 1:
        raise DeathRoutingValidationError(
            f"{field_name} resolves ambiguously; core faction codes must be unique."
        )
    faction = factions[0]
    return int(faction.id), _normalize_code(faction.code, field_name=field_name)


def _resolve_archetype_for_world(
    base_world,
    value: Any,
    field_name: str,
    *,
    stat_system: Mapping[str, Any] | None = None,
) -> str:
    normalized = _normalize_code(value, field_name=field_name)
    if stat_system is None:
        config = getattr(base_world, "config", None)
        stat_system = (
            getattr(config, "stat_system", {}) or {}
            if config is not None
            else {}
        )
    profiles = stat_system.get("class_profiles") or {}
    if normalized not in profiles:
        raise DeathRoutingValidationError(
            f"{field_name} does not resolve to a base-world class profile."
        )
    return normalized


def _resolve_zone_for_world(world, value: Any, field_name: str) -> tuple[int, str]:
    from worlds.models import Zone

    zone = None
    if isinstance(value, Zone):
        zone = value
    elif isinstance(value, int) and not isinstance(value, bool):
        zone = Zone.objects.filter(world=world, pk=value).only(
            "id", "world_id", "relative_id"
        ).first()
    else:
        text = str(value or "").strip()
        portable_match = _ZONE_PORTABLE_REF_RE.fullmatch(text)
        id_match = _ZONE_ID_REF_RE.fullmatch(text)
        if portable_match:
            zone = Zone.objects.filter(
                world=world,
                relative_id=int(portable_match.group("relative_id")),
            ).only("id", "world_id", "relative_id").first()
        elif id_match:
            zone = Zone.objects.filter(
                world=world,
                pk=int(id_match.group("id")),
            ).only("id", "world_id", "relative_id").first()
    if zone is None or zone.world_id != world.id:
        raise DeathRoutingValidationError(
            f"{field_name} must reference a zone in this world."
        )
    relative_id = int(zone.relative_id or zone.id)
    return int(zone.id), f"zone@{relative_id}"


def compile_death_routing_policy(
    *,
    world,
    policy: Any,
    field_name: str = "spec.death_routing",
    stat_system: Mapping[str, Any] | None = None,
) -> DeathRoutingCompilation:
    base_world = world.instance_of if world.instance_of_id else world
    effective_stat_system = None if world.instance_of_id else stat_system

    def memoize_resolver(resolver):
        values: dict[tuple[Any, ...], Any] = {}

        def resolve(value, name):
            if hasattr(value, "_meta") and getattr(value, "pk", None) is not None:
                key = (
                    "model",
                    value._meta.label_lower,
                    int(value.pk),
                )
            elif value is None or isinstance(value, (bool, int, float, str)):
                key = (type(value).__name__, value)
            else:
                return resolver(value, name)
            if key not in values:
                values[key] = resolver(value, name)
            return values[key]

        return resolve

    return compile_death_routing_policy_value(
        policy,
        resolve_destination=memoize_resolver(
            lambda value, name: _resolve_destination_for_world(
                world, value, name
            )
        ),
        resolve_core_faction=memoize_resolver(
            lambda value, name: _resolve_core_faction_for_world(
                base_world, value, name
            )
        ),
        resolve_archetype=memoize_resolver(
            lambda value, name: _resolve_archetype_for_world(
                base_world,
                value,
                name,
                stat_system=effective_stat_system,
            )
        ),
        resolve_zone=memoize_resolver(
            lambda value, name: _resolve_zone_for_world(
                world, value, name
            )
        ),
        field_name=field_name,
    )


def _compiled_archetype_codes(condition: CompiledCondition) -> set[str]:
    if condition.source == "archetype":
        if condition.operator == "eq":
            return {str(condition.value)}
        return {str(value) for value in condition.values}
    return set().union(*(
        _compiled_archetype_codes(child)
        for child in condition.children
    )) if condition.children else set()


def validate_death_routing_archetype_dependencies(
    *,
    base_world,
    stat_system: Mapping[str, Any],
    excluded_config_ids=(),
) -> None:
    """
    Reject base class-profile removal while a local or instance route uses it.

    Class codes are embedded directly in the compiled IR, so changing the
    contents of an existing profile needs no runtime rebuild. Removing a code
    is different: it would leave canonical manifests that cannot be imported
    again. This bounded authoring-time scan keeps that dependency out of the
    death hot path.
    """
    from worlds.models import DeathRoutingRoute, World

    if getattr(base_world, "instance_of_id", None):
        base_world = base_world.instance_of
    base_config_id = getattr(base_world, "config_id", None)
    if base_config_id is None:
        return

    valid_codes = {
        _normalize_code(code, field_name="stat_system.class_profiles")
        for code in (stat_system.get("class_profiles") or {})
    }
    dependent_config_ids = {
        int(base_config_id),
        *World.objects.filter(
            instance_of_id=base_world.id,
            config_id__isnull=False,
        ).values_list("config_id", flat=True),
    }
    dependent_config_ids.difference_update(
        int(config_id) for config_id in excluded_config_ids
    )
    if not dependent_config_ids:
        return

    missing_codes: set[str] = set()
    compiled_conditions = (
        DeathRoutingRoute.objects.filter(
            policy__enabled=True,
            policy__config_id__in=dependent_config_ids,
        )
        .values_list("compiled_condition", flat=True)
        .iterator(chunk_size=256)
    )
    try:
        for compiled_condition in compiled_conditions:
            condition, _factions, _zones, _paths = (
                _compiled_condition_to_object(compiled_condition)
            )
            missing_codes.update(
                _compiled_archetype_codes(condition) - valid_codes
            )
    except DeathRoutingPlanError as exc:
        raise DeathRoutingValidationError(
            "An existing death route has invalid compiled class conditions."
        ) from exc

    if missing_codes:
        raise DeathRoutingValidationError(
            "Cannot remove class profile(s) used by death routing: "
            f"{', '.join(sorted(missing_codes))}."
        )


def _compiled_condition_to_object(
    data: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[CompiledCondition, set[int], set[int], set[str]]:
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_CONDITION_NODES or depth > MAX_CONDITION_DEPTH:
        raise DeathRoutingPlanError("Compiled condition exceeds safety bounds.")
    if not isinstance(data, dict):
        raise DeathRoutingPlanError("Compiled condition must be a mapping.")
    operator = data.get("op")
    if operator == "always":
        if set(data) != {"op"}:
            raise DeathRoutingPlanError("Compiled always condition is invalid.")
        return CompiledCondition(operator="always"), set(), set(), set()
    if operator in {"all", "any", "not"}:
        raw_children = data.get("children")
        expected_count = 1 if operator == "not" else None
        if (
            not isinstance(raw_children, list)
            or not raw_children
            or (expected_count is not None and len(raw_children) != expected_count)
        ):
            raise DeathRoutingPlanError("Compiled Boolean condition is invalid.")
        children: list[CompiledCondition] = []
        faction_ids: set[int] = set()
        zone_ids: set[int] = set()
        state_paths: set[str] = set()
        for child_data in raw_children:
            child, child_factions, child_zones, child_paths = (
                _compiled_condition_to_object(
                    child_data,
                    depth=depth + 1,
                    budget=budget,
                )
            )
            children.append(child)
            faction_ids.update(child_factions)
            zone_ids.update(child_zones)
            state_paths.update(child_paths)
        return (
            CompiledCondition(operator=operator, children=tuple(children)),
            faction_ids,
            zone_ids,
            state_paths,
        )
    if operator not in {"eq", "in", "gte", "lte"}:
        raise DeathRoutingPlanError("Compiled condition operator is unsupported.")
    source = data.get("source")
    if source not in {
        "core_faction",
        "archetype",
        "level",
        "state",
        "origin_zone",
    }:
        raise DeathRoutingPlanError("Compiled condition source is unsupported.")
    if operator in {"gte", "lte"} and source != "level":
        raise DeathRoutingPlanError(
            "Compiled range condition source is unsupported."
        )
    state_segments: tuple[str, ...] = ()
    state_paths: set[str] = set()
    if source == "state":
        raw_segments = data.get("segments")
        if (
            not isinstance(raw_segments, list)
            or not raw_segments
            or len(raw_segments) > MAX_STATE_PATH_SEGMENTS
            or any(
                not isinstance(segment, str)
                or not _STATE_SEGMENT_RE.fullmatch(segment)
                for segment in raw_segments
            )
        ):
            raise DeathRoutingPlanError("Compiled character-state path is invalid.")
        state_segments = tuple(raw_segments)
        state_paths.add("state.character." + ".".join(state_segments))
    elif "segments" in data:
        raise DeathRoutingPlanError("Compiled non-state condition has state segments.")
    raw_values = (
        data.get("values")
        if operator == "in"
        else [data.get("value")]
    )
    if (
        not isinstance(raw_values, list)
        or not raw_values
        or len(raw_values) > MAX_SELECTOR_VALUES
    ):
        raise DeathRoutingPlanError("Compiled condition values are invalid.")
    values: list[Any] = []
    seen: set[tuple[str, Any]] = set()
    for raw_value in raw_values:
        if source in {"core_faction", "origin_zone"}:
            if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value <= 0:
                raise DeathRoutingPlanError("Compiled reference identifier is invalid.")
            normalized_value = raw_value
        elif source == "archetype":
            try:
                normalized_value = _normalize_code(
                    raw_value,
                    field_name="compiled archetype",
                )
            except DeathRoutingValidationError as exc:
                raise DeathRoutingPlanError(str(exc)) from exc
        elif source == "level":
            try:
                normalized_value = _normalize_level_literal(
                    raw_value,
                    field_name="compiled player level",
                )
            except DeathRoutingValidationError as exc:
                raise DeathRoutingPlanError(str(exc)) from exc
        else:
            try:
                normalized_value = _normalize_state_literal(
                    raw_value,
                    field_name="compiled state literal",
                )
            except DeathRoutingValidationError as exc:
                raise DeathRoutingPlanError(str(exc)) from exc
        identity = _literal_identity(normalized_value)
        if identity in seen:
            raise DeathRoutingPlanError("Compiled condition contains duplicate values.")
        seen.add(identity)
        values.append(normalized_value)
    faction_ids = set(values) if source == "core_faction" else set()
    zone_ids = set(values) if source == "origin_zone" else set()
    return (
        CompiledCondition(
            operator=operator,
            source=source,
            state_segments=state_segments,
            value=values[0] if operator != "in" else None,
            values=tuple(values) if operator == "in" else (),
        ),
        faction_ids,
        zone_ids,
        state_paths,
    )


def _snapshot_data(
    *,
    config_id: int,
    generation: int,
    fallback_room_id: int | None,
    routes: tuple[DeathRoutingRouteSpec, ...],
) -> dict[str, Any]:
    return {
        "_cache_version": DEATH_ROUTING_CACHE_VERSION,
        "_built_at": timezone.now().isoformat(),
        "config_id": int(config_id),
        "plan_generation": int(generation),
        "fallback_room_id": fallback_room_id,
        "enabled": bool(routes),
        "required_state_paths": sorted({
            path for route in routes for path in route.state_paths
        }),
        "routes": [
            {
                "position": route.position,
                "condition": deepcopy(route.compiled_condition),
                "destination_room_id": route.destination_room_id,
            }
            for route in routes
        ],
    }


def _create_snapshot_references(
    *,
    snapshot,
    fallback_room_id: int | None,
    routes: tuple[DeathRoutingRouteSpec, ...],
) -> None:
    from worlds.models import DeathRoutingSnapshotReference

    destination_ids = {
        route.destination_room_id for route in routes
    }
    if fallback_room_id is not None:
        destination_ids.add(int(fallback_room_id))
    faction_ids = {
        faction_id for route in routes for faction_id in route.core_faction_ids
    }
    zone_ids = {
        zone_id for route in routes for zone_id in route.origin_zone_ids
    }
    references = [
        DeathRoutingSnapshotReference(
            snapshot=snapshot,
            destination_room_id=room_id,
        )
        for room_id in sorted(destination_ids)
    ]
    references.extend(
        DeathRoutingSnapshotReference(
            snapshot=snapshot,
            core_faction_id=faction_id,
        )
        for faction_id in sorted(faction_ids)
    )
    references.extend(
        DeathRoutingSnapshotReference(
            snapshot=snapshot,
            origin_zone_id=zone_id,
        )
        for zone_id in sorted(zone_ids)
    )
    if references:
        DeathRoutingSnapshotReference.objects.bulk_create(references)


def _route_spec_from_model(route) -> DeathRoutingRouteSpec:
    if int(route.compiled_version) != DEATH_ROUTING_CACHE_VERSION:
        raise DeathRoutingPlanError(
            "Canonical death-routing condition IR requires recompilation."
        )
    _condition, faction_ids, zone_ids, state_paths = (
        _compiled_condition_to_object(route.compiled_condition)
    )
    return DeathRoutingRouteSpec(
        position=int(route.position),
        condition=deepcopy(route.condition),
        compiled_condition=deepcopy(route.compiled_condition),
        destination_room_id=int(route.destination_room_id),
        core_faction_ids=frozenset(faction_ids),
        origin_zone_ids=frozenset(zone_ids),
        state_paths=frozenset(state_paths),
    )


def _canonical_routes_for_config(config) -> tuple[DeathRoutingRouteSpec, ...]:
    from worlds.models import DeathRoutingPolicy

    policy = (
        DeathRoutingPolicy.objects.filter(config=config)
        .prefetch_related("routes")
        .first()
    )
    if policy is None or not policy.enabled:
        return ()
    routes = tuple(
        _route_spec_from_model(route)
        for route in sorted(policy.routes.all(), key=lambda value: value.position)
    )
    if tuple(route.position for route in routes) != tuple(range(len(routes))):
        raise DeathRoutingPlanError("Canonical death-routing positions are invalid.")
    return routes


def _family_configs_for_update(*, world, config):
    from worlds.models import WorldConfig

    if world.instance_of_id:
        base_config_id = world.instance_of.config_id
        if not base_config_id:
            raise DeathRoutingValidationError(
                "The instance base world has no world config."
            )
        base_config = WorldConfig.objects.select_for_update().get(pk=base_config_id)
        if config.pk == base_config.pk:
            raise DeathRoutingValidationError(
                "An instance must own a distinct local world config."
            )
        local_config = WorldConfig.objects.select_for_update().get(pk=config.pk)
        return base_config, local_config
    locked_config = WorldConfig.objects.select_for_update().get(pk=config.pk)
    return locked_config, locked_config


def _retire_prior_snapshots(*, config_id: int, generation: int) -> None:
    """
    Retire old plans while the config's exclusive routing lock is held.

    Exclusive publication cannot begin until all shared death-resolution locks
    for this config have drained. It is therefore safe to release old
    identifier references immediately and keep only a bounded diagnostic
    history, without a guessed grace period or a per-death lease write.
    """
    from worlds.models import (
        DeathRoutingCompiledSnapshot,
        DeathRoutingSnapshotReference,
    )

    retired_ids = list(
        DeathRoutingCompiledSnapshot.objects.filter(
            config_id=config_id,
            plan_generation__lt=generation,
            retirement_pending=True,
            retired_at__isnull=True,
        ).values_list("id", flat=True)
    )
    if retired_ids:
        DeathRoutingCompiledSnapshot.objects.filter(
            id__in=retired_ids,
        ).update(
            retirement_pending=False,
            retired_at=timezone.now(),
        )
        DeathRoutingSnapshotReference.objects.filter(
            snapshot_id__in=retired_ids,
        ).delete()

    retained_ids = list(
        DeathRoutingCompiledSnapshot.objects.filter(config_id=config_id)
        .order_by("-plan_generation", "-id")
        .values_list("id", flat=True)[:MAX_RETAINED_SNAPSHOTS_PER_CONFIG]
    )
    DeathRoutingCompiledSnapshot.objects.filter(
        config_id=config_id,
    ).exclude(id__in=retained_ids).delete()


def replace_compiled_policy(*, world, config, compilation: DeathRoutingCompilation):
    from worlds.models import (
        DeathRoutingCompiledSnapshot,
        DeathRoutingPolicy,
        DeathRoutingRoute,
    )

    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(world=world, config=config),
            shared=False,
        )
        _base_config, locked_config = _family_configs_for_update(
            world=world,
            config=config,
        )
        policy, _ = DeathRoutingPolicy.objects.select_for_update().get_or_create(
            config=locked_config,
            defaults={"enabled": compilation.enabled},
        )
        policy.routes.all().delete()
        if compilation.routes:
            DeathRoutingRoute.objects.bulk_create([
                DeathRoutingRoute(
                    policy=policy,
                    position=route.position,
                    condition=deepcopy(route.condition),
                    compiled_version=DEATH_ROUTING_CACHE_VERSION,
                    compiled_condition=deepcopy(route.compiled_condition),
                    destination_room_id=route.destination_room_id,
                )
                for route in compilation.routes
            ])
        if policy.enabled != compilation.enabled:
            policy.enabled = compilation.enabled
            policy.save(update_fields=["enabled", "modified_ts"])

        generation = int(locked_config.death_routing_generation or 0) + 1
        DeathRoutingCompiledSnapshot.objects.filter(
            config=locked_config,
            retired_at__isnull=True,
        ).update(retirement_pending=True)
        locked_config.death_routing_generation = generation
        locked_config.save(
            update_fields=["death_routing_generation", "modified_ts"]
        )
        snapshot = DeathRoutingCompiledSnapshot.objects.create(
            config=locked_config,
            plan_generation=generation,
            cache_version=DEATH_ROUTING_CACHE_VERSION,
            data=_snapshot_data(
                config_id=locked_config.id,
                generation=generation,
                fallback_room_id=locked_config.death_room_id,
                routes=compilation.routes,
            ),
        )
        _create_snapshot_references(
            snapshot=snapshot,
            fallback_room_id=locked_config.death_room_id,
            routes=compilation.routes,
        )
        _retire_prior_snapshots(
            config_id=locked_config.id,
            generation=generation,
        )
        config.death_routing_generation = generation

    return locked_config


def rebuild_compiled_policy_snapshot(*, world, config):
    from worlds.models import DeathRoutingCompiledSnapshot

    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(world=world, config=config),
            shared=False,
        )
        _base_config, locked_config = _family_configs_for_update(
            world=world,
            config=config,
        )
        routes = _canonical_routes_for_config(locked_config)
        generation = int(locked_config.death_routing_generation or 0) + 1
        DeathRoutingCompiledSnapshot.objects.filter(
            config=locked_config,
            retired_at__isnull=True,
        ).update(retirement_pending=True)
        locked_config.death_routing_generation = generation
        locked_config.save(
            update_fields=["death_routing_generation", "modified_ts"]
        )
        snapshot = DeathRoutingCompiledSnapshot.objects.create(
            config=locked_config,
            plan_generation=generation,
            cache_version=DEATH_ROUTING_CACHE_VERSION,
            data=_snapshot_data(
                config_id=locked_config.id,
                generation=generation,
                fallback_room_id=locked_config.death_room_id,
                routes=routes,
            ),
        )
        _create_snapshot_references(
            snapshot=snapshot,
            fallback_room_id=locked_config.death_room_id,
            routes=routes,
        )
        _retire_prior_snapshots(
            config_id=locked_config.id,
            generation=generation,
        )
        config.death_routing_generation = generation

    return locked_config


def canonical_death_routing_manifest_value(
    *,
    config,
    serialize_room: Callable[[Any], Any],
) -> dict[str, Any] | None:
    from worlds.models import DeathRoutingPolicy

    policy = (
        DeathRoutingPolicy.objects.filter(config=config)
        .prefetch_related("routes__destination_room")
        .first()
    )
    if policy is None:
        return {"routes": []}
    if not policy.enabled:
        return {"routes": []}
    routes = sorted(policy.routes.all(), key=lambda value: value.position)
    return {
        "routes": [
            {
                "when": deepcopy(route.condition),
                "destination": serialize_room(route.destination_room),
            }
            for route in routes
        ],
    }


def _plan_from_snapshot_data(
    data: Any,
    *,
    config_id: int,
    generation: int,
    fallback_room_id: int | None,
) -> CompiledDeathRoutingPlan:
    if not isinstance(data, dict):
        raise DeathRoutingPlanError("Compiled death-routing snapshot must be a mapping.")
    if int(data.get("_cache_version") or 0) != DEATH_ROUTING_CACHE_VERSION:
        raise DeathRoutingPlanError("Compiled death-routing snapshot version is unsupported.")
    if int(data.get("config_id") or 0) != config_id:
        raise DeathRoutingPlanError("Compiled death-routing snapshot config does not match.")
    if int(data.get("plan_generation") or -1) != generation:
        raise DeathRoutingPlanError("Compiled death-routing snapshot generation does not match.")
    raw_routes = data.get("routes") or []
    if not isinstance(raw_routes, list) or len(raw_routes) > MAX_AUTHORED_ROUTES:
        raise DeathRoutingPlanError("Compiled death-routing routes are invalid.")
    routes: list[CompiledDeathRoutingRoute] = []
    required_state_paths: set[str] = set()
    for expected_position, raw_route in enumerate(raw_routes):
        if not isinstance(raw_route, dict):
            raise DeathRoutingPlanError("Compiled death-routing route is invalid.")
        position = raw_route.get("position")
        room_id = raw_route.get("destination_room_id")
        if (
            isinstance(position, bool)
            or position != expected_position
            or isinstance(room_id, bool)
            or not isinstance(room_id, int)
            or room_id <= 0
        ):
            raise DeathRoutingPlanError("Compiled death-routing route identity is invalid.")
        condition, _factions, _zones, state_paths = (
            _compiled_condition_to_object(raw_route.get("condition"))
        )
        required_state_paths.update(state_paths)
        routes.append(
            CompiledDeathRoutingRoute(
                position=position,
                condition=condition,
                destination_room_id=room_id,
            )
        )
    declared_paths = data.get("required_state_paths") or []
    if (
        not isinstance(declared_paths, list)
        or tuple(sorted(required_state_paths)) != tuple(declared_paths)
    ):
        raise DeathRoutingPlanError("Compiled state-path index is invalid.")
    return CompiledDeathRoutingPlan(
        config_id=config_id,
        generation=generation,
        cache_version=DEATH_ROUTING_CACHE_VERSION,
        fallback_room_id=fallback_room_id,
        enabled=bool(data.get("enabled")) and bool(routes),
        routes=tuple(routes),
        required_state_paths=tuple(declared_paths),
    )


@lru_cache(maxsize=2048)
def _load_compiled_plan_cached(
    config_id: int,
    generation: int,
    cache_version: int,
    fallback_room_id: int | None,
) -> CompiledDeathRoutingPlan:
    from worlds.models import DeathRoutingCompiledSnapshot

    try:
        snapshot = DeathRoutingCompiledSnapshot.objects.only("data").get(
            config_id=config_id,
            plan_generation=generation,
            cache_version=cache_version,
        )
    except DeathRoutingCompiledSnapshot.DoesNotExist:
        if generation == 0:
            return CompiledDeathRoutingPlan(
                config_id=config_id,
                generation=0,
                cache_version=cache_version,
                fallback_room_id=fallback_room_id,
                enabled=False,
                routes=(),
                required_state_paths=(),
            )
        logger.error(
            "Compiled death-routing snapshot is unavailable for config %s "
            "generation %s.",
            config_id,
            generation,
        )
        return CompiledDeathRoutingPlan(
            config_id=config_id,
            generation=generation,
            cache_version=cache_version,
            fallback_room_id=fallback_room_id,
            enabled=False,
            routes=(),
            required_state_paths=(),
            load_error="compiled_plan_unavailable",
        )
    try:
        return _plan_from_snapshot_data(
            snapshot.data,
            config_id=config_id,
            generation=generation,
            fallback_room_id=fallback_room_id,
        )
    except (DeathRoutingPlanError, OverflowError, TypeError, ValueError):
        logger.exception(
            "Compiled death-routing snapshot is invalid for config %s "
            "generation %s.",
            config_id,
            generation,
        )
        return CompiledDeathRoutingPlan(
            config_id=config_id,
            generation=generation,
            cache_version=cache_version,
            fallback_room_id=fallback_room_id,
            enabled=False,
            routes=(),
            required_state_paths=(),
            load_error="compiled_plan_unavailable",
        )


_plan_load_locks_guard = threading.Lock()
_plan_load_locks: dict[tuple[int, int, int, int | None], threading.Lock] = {}


def load_compiled_plan(config) -> CompiledDeathRoutingPlan:
    key = (
        int(config.id),
        int(config.death_routing_generation or 0),
        DEATH_ROUTING_CACHE_VERSION,
        int(config.death_room_id) if config.death_room_id is not None else None,
    )
    with _plan_load_locks_guard:
        load_lock = _plan_load_locks.setdefault(key, threading.Lock())
    try:
        with load_lock:
            return _load_compiled_plan_cached(*key)
    finally:
        with _plan_load_locks_guard:
            if _plan_load_locks.get(key) is load_lock:
                _plan_load_locks.pop(key, None)


def clear_compiled_plan_cache() -> None:
    _load_compiled_plan_cached.cache_clear()


def _walk_character_state(
    character_state: Mapping[str, Any],
    segments: tuple[str, ...],
) -> Any:
    current: Any = character_state
    for segment in segments:
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _scalar_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
            and left == right
        )
    return isinstance(left, str) and isinstance(right, str) and left == right


def _condition_source_value(
    condition: CompiledCondition,
    *,
    core_faction_id: int | None,
    archetype: str | None,
    player_level: int | None,
    character_state: Mapping[str, Any],
    origin_zone_id: int | None,
) -> Any:
    if condition.source == "core_faction":
        return core_faction_id
    if condition.source == "archetype":
        return str(archetype or "").strip().lower()
    if condition.source == "level":
        return player_level
    if condition.source == "origin_zone":
        return origin_zone_id
    return _walk_character_state(character_state, condition.state_segments)


def _condition_matches(
    condition: CompiledCondition,
    *,
    core_faction_id: int | None,
    archetype: str | None,
    player_level: int | None,
    character_state: Mapping[str, Any],
    origin_zone_id: int | None,
) -> bool:
    kwargs = {
        "core_faction_id": core_faction_id,
        "archetype": archetype,
        "player_level": player_level,
        "character_state": character_state,
        "origin_zone_id": origin_zone_id,
    }
    if condition.operator == "always":
        return True
    if condition.operator == "all":
        return all(_condition_matches(child, **kwargs) for child in condition.children)
    if condition.operator == "any":
        return any(_condition_matches(child, **kwargs) for child in condition.children)
    if condition.operator == "not":
        return not _condition_matches(condition.children[0], **kwargs)
    left = _condition_source_value(condition, **kwargs)
    if condition.operator == "eq":
        return _scalar_equal(left, condition.value)
    if condition.operator == "in":
        return any(
            _scalar_equal(left, candidate)
            for candidate in condition.values
        )
    if (
        isinstance(left, bool)
        or not isinstance(left, int)
        or isinstance(condition.value, bool)
        or not isinstance(condition.value, int)
    ):
        return False
    if condition.operator == "gte":
        return left >= condition.value
    return left <= condition.value


def resolve_death_destination(
    plan: CompiledDeathRoutingPlan,
    *,
    core_faction_id: int | None,
    archetype: str | None,
    player_level: int | None,
    character_state: Mapping[str, Any] | None,
    origin_zone_id: int | None,
) -> DeathRoutingResolution:
    if not plan.enabled:
        return DeathRoutingResolution(
            room_id=plan.fallback_room_id,
            reason="fallback",
            fallback_reason=plan.load_error or "disabled_policy",
            matched_route_position=None,
        )
    state = character_state if isinstance(character_state, Mapping) else {}
    for route in plan.routes:
        if _condition_matches(
            route.condition,
            core_faction_id=core_faction_id,
            archetype=archetype,
            player_level=player_level,
            character_state=state,
            origin_zone_id=origin_zone_id,
        ):
            return DeathRoutingResolution(
                room_id=route.destination_room_id,
                reason="ordered_route",
                fallback_reason=None,
                matched_route_position=route.position,
            )
    return DeathRoutingResolution(
        room_id=plan.fallback_room_id,
        reason="fallback",
        fallback_reason="no_match",
        matched_route_position=None,
    )
