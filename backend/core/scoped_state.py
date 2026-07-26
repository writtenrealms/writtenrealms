from __future__ import annotations

import json
from typing import Any

from django.db import IntegrityError, transaction


STATE_SCOPE_WORLD = "world"
STATE_SCOPE_ZONE = "zone"
STATE_SCOPE_ROOM = "room"
STATE_SCOPE_CHARACTER = "character"
STATE_SCOPE_QUEST = "quest"

STATE_SCOPES = (
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    STATE_SCOPE_ROOM,
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_QUEST,
)

RUNTIME_SCOPES = (
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    STATE_SCOPE_ROOM,
)


def normalize_state_scope(scope: Any) -> str:
    normalized = str(scope or "").strip().lower()
    if normalized not in STATE_SCOPES:
        raise ValueError(f"Unsupported state scope '{scope}'.")
    return normalized


def normalize_state_key(key: Any) -> str:
    normalized = str(key or "").strip()
    if not normalized:
        raise ValueError("State key cannot be blank.")
    return normalized


def normalize_state_snapshot(
    value: Any,
    *,
    field_name: str = "state",
) -> dict[str, Any]:
    """Validate and detach an authored or runtime state mapping."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"{field_name} keys must be non-blank strings.")
        normalized[raw_key.strip()] = raw_value
    try:
        # A JSON round trip both validates nested values and prevents callers
        # from retaining mutable references to model state.
        return json.loads(json.dumps(normalized))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON-compatible values.") from exc


def _character_snapshot_from_legacy(character) -> dict[str, Any]:
    state: dict[str, Any] = {}
    mark_manager = getattr(character, "marks", None)
    if mark_manager is None:
        return state
    for mark in mark_manager.all():
        state[str(mark.name or "")] = mark.value
    return state


def _quest_snapshot(quest_instance) -> dict[str, Any]:
    return dict(getattr(quest_instance, "local_state", {}) or {})


def _character_row_config(character):
    from spawns.models import CharacterState, Mob, MobState, Player

    if isinstance(character, Player):
        return CharacterState, "player"
    if isinstance(character, Mob):
        return MobState, "mob"
    raise ValueError("Character state owners must be players or mobs.")


def _runtime_world_for(
    scope: str,
    owner,
    *,
    runtime_world=None,
    require_runtime: bool,
):
    scope = normalize_state_scope(scope)
    if scope == STATE_SCOPE_WORLD:
        candidate = runtime_world or owner
    elif scope in {STATE_SCOPE_ZONE, STATE_SCOPE_ROOM}:
        candidate = runtime_world
    else:
        return runtime_world

    if candidate is None or getattr(candidate, "context_id", None) is None:
        if require_runtime:
            raise ValueError(
                f"{scope.capitalize()} runtime state requires a spawned runtime world."
            )
        return None

    if scope in {STATE_SCOPE_ZONE, STATE_SCOPE_ROOM}:
        authored_world_id = getattr(owner, "world_id", None)
        if candidate.context_id != authored_world_id:
            raise ValueError(
                f"The {scope} does not belong to this runtime world's content context."
            )
    return candidate


def _row_lookup(
    scope: str,
    owner,
    *,
    runtime_world=None,
    require_runtime: bool = True,
):
    scope = normalize_state_scope(scope)
    if scope == STATE_SCOPE_WORLD:
        from worlds.models import WorldState

        world = _runtime_world_for(
            scope,
            owner,
            runtime_world=runtime_world,
            require_runtime=require_runtime,
        )
        return (WorldState, {"world": world}) if world is not None else (None, {})
    if scope == STATE_SCOPE_ZONE:
        from worlds.models import ZoneState

        world = _runtime_world_for(
            scope,
            owner,
            runtime_world=runtime_world,
            require_runtime=require_runtime,
        )
        return (
            (ZoneState, {"world": world, "zone": owner})
            if world is not None else (None, {})
        )
    if scope == STATE_SCOPE_ROOM:
        from worlds.models import RoomState

        world = _runtime_world_for(
            scope,
            owner,
            runtime_world=runtime_world,
            require_runtime=require_runtime,
        )
        return (
            (RoomState, {"world": world, "room": owner})
            if world is not None else (None, {})
        )
    if scope == STATE_SCOPE_CHARACTER:
        model_cls, owner_field = _character_row_config(owner)
        return model_cls, {owner_field: owner}
    raise ValueError(f"Scope '{scope}' does not use a dedicated state row.")


def get_initial_state_snapshot(scope: str, owner) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    if owner is None or scope not in RUNTIME_SCOPES:
        return {}
    value = getattr(owner, "initial_state", {}) or {}
    return dict(value) if isinstance(value, dict) else {}


def replace_initial_state_snapshot(
    scope: str,
    owner,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    if scope not in RUNTIME_SCOPES:
        raise ValueError(f"Scope '{scope}' does not have authored initial state.")
    if owner is None:
        raise ValueError("Initial state requires an authored owner.")
    if scope == STATE_SCOPE_WORLD and getattr(owner, "context_id", None) is not None:
        raise ValueError("Spawned runtime worlds do not author initial state.")
    normalized = normalize_state_snapshot(
        data,
        field_name=f"{scope}.initial_state",
    )
    owner.initial_state = normalized
    owner.save(update_fields=["initial_state"])
    return normalized


def _locked_row(scope: str, owner, *, runtime_world=None):
    model_cls, lookup = _row_lookup(
        scope,
        owner,
        runtime_world=runtime_world,
    )
    try:
        return model_cls.objects.select_for_update().get(**lookup)
    except model_cls.DoesNotExist:
        try:
            with transaction.atomic():
                model_cls.objects.create(**lookup, data={})
        except IntegrityError:
            # Another writer won the unique-owner insert race.
            pass
        return model_cls.objects.select_for_update().get(**lookup)


def get_state_snapshot(
    scope: str,
    owner,
    *,
    runtime_world=None,
) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    if owner is None:
        return {}
    if scope == STATE_SCOPE_QUEST:
        return _quest_snapshot(owner)

    model_cls, lookup = _row_lookup(
        scope,
        owner,
        runtime_world=runtime_world,
        require_runtime=False,
    )
    if model_cls is None:
        # An authored world/zone/room has defaults, not live state.
        return get_initial_state_snapshot(scope, owner)
    row = model_cls.objects.filter(**lookup).only("data").first()
    if row is None:
        if scope == STATE_SCOPE_CHARACTER:
            return _character_snapshot_from_legacy(owner)
        return {}
    return dict(row.data or {})


def replace_state_snapshot(
    scope: str,
    owner,
    data: dict[str, Any] | None,
    *,
    runtime_world=None,
) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    normalized_data = normalize_state_snapshot(data, field_name=f"{scope}.state")
    if owner is None:
        return normalized_data
    if scope == STATE_SCOPE_QUEST:
        owner.local_state = normalized_data
        owner.save(update_fields=["local_state", "modified_ts"])
        return normalized_data

    model_cls, lookup = _row_lookup(
        scope,
        owner,
        runtime_world=runtime_world,
    )
    with transaction.atomic():
        if not normalized_data:
            model_cls.objects.filter(**lookup).delete()
            return {}
        row = _locked_row(scope, owner, runtime_world=runtime_world)
        row.data = normalized_data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    return normalized_data


def get_state_value(
    scope: str,
    owner,
    key: Any,
    default: Any = None,
    *,
    runtime_world=None,
) -> Any:
    try:
        normalized_key = normalize_state_key(key)
    except ValueError:
        return default
    return get_state_snapshot(
        scope,
        owner,
        runtime_world=runtime_world,
    ).get(normalized_key, default)


def set_state_value(
    scope: str,
    owner,
    key: Any,
    value: Any,
    *,
    runtime_world=None,
) -> Any:
    normalized_key = normalize_state_key(key)
    scope = normalize_state_scope(scope)

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot(owner)
        state[normalized_key] = value
        replace_state_snapshot(scope, owner, state)
        return value

    with transaction.atomic():
        row = _locked_row(scope, owner, runtime_world=runtime_world)
        data = dict(row.data or {})
        data[normalized_key] = value
        row.data = normalize_state_snapshot(data, field_name=f"{scope}.state")
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    return value


def clear_state_value(
    scope: str,
    owner,
    key: Any,
    *,
    runtime_world=None,
) -> bool:
    normalized_key = normalize_state_key(key)
    scope = normalize_state_scope(scope)

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot(owner)
        if normalized_key not in state:
            return False
        state.pop(normalized_key, None)
        replace_state_snapshot(scope, owner, state)
        return True

    model_cls, lookup = _row_lookup(
        scope,
        owner,
        runtime_world=runtime_world,
    )
    with transaction.atomic():
        try:
            row = model_cls.objects.select_for_update().get(**lookup)
        except model_cls.DoesNotExist:
            return False
        data = dict(row.data or {})
        if normalized_key not in data:
            return False
        data.pop(normalized_key, None)
        if not data:
            row.delete()
            return True
        row.data = data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    return True


def increment_state_value(
    scope: str,
    owner,
    key: Any,
    amount: int | float = 1,
    *,
    runtime_world=None,
) -> int | float:
    normalized_key = normalize_state_key(key)
    scope = normalize_state_scope(scope)

    try:
        amount_value = int(amount)
    except (TypeError, ValueError):
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            amount_value = 1

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot(owner)
        current_value = state.get(normalized_key, 0)
        try:
            updated_value = current_value + amount_value
        except TypeError:
            updated_value = amount_value
        state[normalized_key] = updated_value
        replace_state_snapshot(scope, owner, state)
        return updated_value

    with transaction.atomic():
        row = _locked_row(scope, owner, runtime_world=runtime_world)
        data = dict(row.data or {})
        current_value = data.get(normalized_key, 0)
        try:
            updated_value = current_value + amount_value
        except TypeError:
            updated_value = amount_value
        data[normalized_key] = updated_value
        row.data = data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    return updated_value


def increment_state_values(
    scope: str,
    owner,
    increments: dict[Any, int | float],
    *,
    runtime_world=None,
) -> dict[str, int | float]:
    """Increment several keys with one locked state-row write."""
    scope = normalize_state_scope(scope)
    normalized_increments: dict[str, int | float] = {}
    for key, amount in increments.items():
        normalized_key = normalize_state_key(key)
        try:
            amount_value: int | float = int(amount)
        except (TypeError, ValueError):
            try:
                amount_value = float(amount)
            except (TypeError, ValueError):
                amount_value = 1
        normalized_increments[normalized_key] = amount_value

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot(owner)
        updated: dict[str, int | float] = {}
        for key, amount in normalized_increments.items():
            try:
                value = state.get(key, 0) + amount
            except TypeError:
                value = amount
            state[key] = value
            updated[key] = value
        replace_state_snapshot(scope, owner, state)
        return updated

    with transaction.atomic():
        row = _locked_row(scope, owner, runtime_world=runtime_world)
        data = dict(row.data or {})
        updated = {}
        for key, amount in normalized_increments.items():
            try:
                value = data.get(key, 0) + amount
            except TypeError:
                value = amount
            data[key] = value
            updated[key] = value
        row.data = data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    return updated


def initialize_character_state(character, data: dict[str, Any] | None):
    """Create initial state for a newly-created Player or Mob, if nonempty."""
    normalized = normalize_state_snapshot(data, field_name="initial_state")
    if not normalized:
        return None
    model_cls, lookup = _row_lookup(STATE_SCOPE_CHARACTER, character)
    row, _ = model_cls.objects.update_or_create(
        **lookup,
        defaults={"data": normalized},
    )
    return row


def initialize_runtime_state(runtime_world, *, replace: bool = False) -> None:
    """Snapshot authored defaults into one spawned runtime world."""
    from worlds.models import RoomState, WorldState, ZoneState

    context = getattr(runtime_world, "context", None)
    if context is None:
        raise ValueError("Runtime state can only be initialized for a spawned world.")

    with transaction.atomic():
        if replace:
            WorldState.objects.filter(world=runtime_world).delete()
            ZoneState.objects.filter(world=runtime_world).delete()
            RoomState.objects.filter(world=runtime_world).delete()

        world_defaults = get_initial_state_snapshot(STATE_SCOPE_WORLD, context)
        if world_defaults:
            WorldState.objects.get_or_create(
                world=runtime_world,
                defaults={"data": world_defaults},
            )

        zone_rows = [
            ZoneState(
                world=runtime_world,
                zone_id=zone_id,
                data=dict(data),
            )
            for zone_id, data in context.zones.exclude(
                initial_state={},
            ).values_list("id", "initial_state")
            if isinstance(data, dict) and data
        ]
        if zone_rows:
            ZoneState.objects.bulk_create(zone_rows, ignore_conflicts=True)

        room_rows = [
            RoomState(
                world=runtime_world,
                room_id=room_id,
                data=dict(data),
            )
            for room_id, data in context.rooms.exclude(
                initial_state={},
            ).values_list("id", "initial_state")
            if isinstance(data, dict) and data
        ]
        if room_rows:
            RoomState.objects.bulk_create(room_rows, ignore_conflicts=True)


def reset_runtime_state(runtime_world) -> None:
    initialize_runtime_state(runtime_world, replace=True)


def _quest_player(quest_instance):
    return getattr(quest_instance, "player", None)


def _quest_player_room(quest_instance):
    return getattr(_quest_player(quest_instance), "room", None)


def _inferred_runtime_world(*, actor=None, runtime_world=None, world=None):
    if runtime_world is not None:
        return runtime_world
    actor_world = getattr(actor, "world", None)
    if getattr(actor_world, "context_id", None) is not None:
        return actor_world
    if getattr(world, "context_id", None) is not None:
        return world
    return None


def resolve_scope_owner(
    scope: str,
    *,
    actor=None,
    world=None,
    runtime_world=None,
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
):
    scope = normalize_state_scope(scope)
    actor_room = getattr(actor, "room", None)
    quest_room = _quest_player_room(quest_instance)
    inferred_runtime_world = _inferred_runtime_world(
        actor=actor,
        runtime_world=runtime_world,
        world=world,
    )
    if scope == STATE_SCOPE_WORLD:
        return (
            inferred_runtime_world
            or world
            or getattr(actor, "world", None)
            or getattr(quest_instance, "world", None)
            or getattr(room, "world", None)
            or getattr(zone, "world", None)
        )
    if scope == STATE_SCOPE_ZONE:
        return (
            zone
            or getattr(room, "zone", None)
            or getattr(actor_room, "zone", None)
            or getattr(quest_room, "zone", None)
        )
    if scope == STATE_SCOPE_ROOM:
        return room or actor_room or quest_room
    if scope == STATE_SCOPE_CHARACTER:
        if character is not None:
            return character
        actor_class_name = getattr(getattr(actor, "__class__", None), "__name__", "")
        if actor_class_name in {"Player", "Mob"}:
            return actor
        return _quest_player(quest_instance)
    if scope == STATE_SCOPE_QUEST:
        return quest_instance
    return None


def _walk_value(value: Any, segments: list[str]) -> Any:
    current = value
    for segment in segments:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment)
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                return None
            idx = int(segment)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        current = getattr(current, segment, None)
    return current


def resolve_state_path(
    path: str,
    *,
    actor=None,
    world=None,
    runtime_world=None,
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
    snapshot_cache: dict[str, dict[str, Any]] | None = None,
) -> Any:
    normalized = str(path or "").strip()
    if not normalized.startswith("state."):
        return None
    segments = normalized.split(".")
    if len(segments) < 3:
        return None
    scope = segments[1]
    if snapshot_cache is not None and scope in snapshot_cache:
        return _walk_value(snapshot_cache[scope], segments[2:])
    inferred_runtime_world = _inferred_runtime_world(
        actor=actor,
        runtime_world=runtime_world,
        world=world,
    )
    try:
        owner = resolve_scope_owner(
            scope,
            actor=actor,
            world=world,
            runtime_world=inferred_runtime_world,
            zone=zone,
            room=room,
            character=character,
            quest_instance=quest_instance,
        )
        snapshot = get_state_snapshot(
            scope,
            owner,
            runtime_world=inferred_runtime_world,
        )
    except ValueError:
        return None
    if snapshot_cache is not None:
        snapshot_cache[scope] = snapshot
    return _walk_value(snapshot, segments[2:])


def build_state_context(
    *,
    actor=None,
    world=None,
    runtime_world=None,
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
) -> dict[str, dict[str, Any]]:
    inferred_runtime_world = _inferred_runtime_world(
        actor=actor,
        runtime_world=runtime_world,
        world=world,
    )
    context: dict[str, dict[str, Any]] = {}
    for scope in STATE_SCOPES:
        owner = resolve_scope_owner(
            scope,
            actor=actor,
            world=world,
            runtime_world=inferred_runtime_world,
            zone=zone,
            room=room,
            character=character,
            quest_instance=quest_instance,
        )
        context[scope] = get_state_snapshot(
            scope,
            owner,
            runtime_world=inferred_runtime_world,
        )
    return context


def coerce_state_command_value(raw_value: Any) -> Any:
    if not isinstance(raw_value, str):
        return raw_value
    text = raw_value.strip()
    if not text:
        return ""

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text
