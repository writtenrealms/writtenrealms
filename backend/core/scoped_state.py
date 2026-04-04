from __future__ import annotations

import json
from typing import Any

from django.db import transaction


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


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _world_snapshot_from_legacy(world) -> dict[str, Any]:
    return _json_object(getattr(world, "facts", None))


def _zone_snapshot_from_legacy(zone) -> dict[str, Any]:
    return _json_object(getattr(zone, "zone_data", None))


def _character_snapshot_from_legacy(character) -> dict[str, Any]:
    state: dict[str, Any] = {}
    mark_manager = getattr(character, "marks", None)
    if mark_manager is None:
        return state
    for mark in mark_manager.all():
        state[str(mark.name or "")] = mark.value
    return state


def _quest_snapshot_from_legacy(quest_instance) -> dict[str, Any]:
    return dict(getattr(quest_instance, "local_state", {}) or {})


def _row_config(scope: str):
    scope = normalize_state_scope(scope)
    if scope == STATE_SCOPE_WORLD:
        from worlds.models import WorldState

        return WorldState, "world"
    if scope == STATE_SCOPE_ZONE:
        from worlds.models import ZoneState

        return ZoneState, "zone"
    if scope == STATE_SCOPE_ROOM:
        from worlds.models import RoomState

        return RoomState, "room"
    if scope == STATE_SCOPE_CHARACTER:
        from spawns.models import CharacterState

        return CharacterState, "player"
    raise ValueError(f"Scope '{scope}' does not use a dedicated state row.")


def _legacy_snapshot(scope: str, owner) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    if owner is None:
        return {}
    if scope == STATE_SCOPE_WORLD:
        return _world_snapshot_from_legacy(owner)
    if scope == STATE_SCOPE_ZONE:
        return _zone_snapshot_from_legacy(owner)
    if scope == STATE_SCOPE_ROOM:
        return {}
    if scope == STATE_SCOPE_CHARACTER:
        return _character_snapshot_from_legacy(owner)
    if scope == STATE_SCOPE_QUEST:
        return _quest_snapshot_from_legacy(owner)
    return {}


def _sync_legacy_storage(scope: str, owner, data: dict[str, Any]) -> None:
    scope = normalize_state_scope(scope)
    if owner is None:
        return
    if scope == STATE_SCOPE_WORLD:
        owner.facts = json.dumps(data or {})
        owner.save(update_fields=["facts"])
    elif scope == STATE_SCOPE_ZONE:
        owner.zone_data = json.dumps(data or {})
        owner.save(update_fields=["zone_data"])
    elif scope == STATE_SCOPE_QUEST:
        owner.local_state = data or {}
        owner.save(update_fields=["local_state", "modified_ts"])


def _get_or_create_row(scope: str, owner):
    model_cls, owner_field = _row_config(scope)
    row, _ = model_cls.objects.get_or_create(
        **{owner_field: owner},
        defaults={"data": _legacy_snapshot(scope, owner)},
    )
    return model_cls.objects.select_for_update().get(pk=row.pk)


def get_state_snapshot(scope: str, owner) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    if owner is None:
        return {}
    if scope == STATE_SCOPE_QUEST:
        return _quest_snapshot_from_legacy(owner)

    model_cls, owner_field = _row_config(scope)
    row = model_cls.objects.filter(**{owner_field: owner}).first()
    if row is None:
        return _legacy_snapshot(scope, owner)
    return dict(row.data or {})


def replace_state_snapshot(scope: str, owner, data: dict[str, Any] | None) -> dict[str, Any]:
    scope = normalize_state_scope(scope)
    normalized_data = dict(data or {})
    if owner is None:
        return normalized_data

    if scope == STATE_SCOPE_QUEST:
        _sync_legacy_storage(scope, owner, normalized_data)
        return normalized_data

    with transaction.atomic():
        row = _get_or_create_row(scope, owner)
        row.data = normalized_data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    _sync_legacy_storage(scope, owner, normalized_data)
    return normalized_data


def get_state_value(scope: str, owner, key: Any, default: Any = None) -> Any:
    try:
        normalized_key = normalize_state_key(key)
    except ValueError:
        return default
    return get_state_snapshot(scope, owner).get(normalized_key, default)


def set_state_value(scope: str, owner, key: Any, value: Any) -> Any:
    normalized_key = normalize_state_key(key)
    scope = normalize_state_scope(scope)

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot_from_legacy(owner)
        state[normalized_key] = value
        _sync_legacy_storage(scope, owner, state)
        return value

    with transaction.atomic():
        row = _get_or_create_row(scope, owner)
        data = dict(row.data or {})
        data[normalized_key] = value
        row.data = data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    _sync_legacy_storage(scope, owner, data)
    return value


def clear_state_value(scope: str, owner, key: Any) -> bool:
    normalized_key = normalize_state_key(key)
    scope = normalize_state_scope(scope)

    if scope == STATE_SCOPE_QUEST:
        state = _quest_snapshot_from_legacy(owner)
        if normalized_key not in state:
            return False
        state.pop(normalized_key, None)
        _sync_legacy_storage(scope, owner, state)
        return True

    with transaction.atomic():
        row = _get_or_create_row(scope, owner)
        data = dict(row.data or {})
        if normalized_key not in data:
            return False
        data.pop(normalized_key, None)
        row.data = data
        row.version = int(row.version or 0) + 1
        row.save(update_fields=["data", "version", "modified_ts"])
    _sync_legacy_storage(scope, owner, data)
    return True


def increment_state_value(scope: str, owner, key: Any, amount: int | float = 1) -> int | float:
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
        state = _quest_snapshot_from_legacy(owner)
        current_value = state.get(normalized_key, 0)
        try:
            updated_value = current_value + amount_value
        except TypeError:
            updated_value = amount_value
        state[normalized_key] = updated_value
        _sync_legacy_storage(scope, owner, state)
        return updated_value

    with transaction.atomic():
        row = _get_or_create_row(scope, owner)
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
    _sync_legacy_storage(scope, owner, data)
    return updated_value


def _quest_player(quest_instance):
    return getattr(quest_instance, "player", None)


def _quest_player_room(quest_instance):
    return getattr(_quest_player(quest_instance), "room", None)


def resolve_scope_owner(
    scope: str,
    *,
    actor=None,
    world=None,
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
):
    scope = normalize_state_scope(scope)
    actor_room = getattr(actor, "room", None)
    quest_room = _quest_player_room(quest_instance)
    if scope == STATE_SCOPE_WORLD:
        return (
            world
            or getattr(room, "world", None)
            or getattr(zone, "world", None)
            or getattr(actor, "world", None)
            or getattr(quest_instance, "world", None)
        )
    if scope == STATE_SCOPE_ZONE:
        return (
            zone
            or getattr(room, "zone", None)
            or getattr(actor_room, "zone", None)
            or getattr(quest_room, "zone", None)
        )
    if scope == STATE_SCOPE_ROOM:
        return (
            room
            or actor_room
            or quest_room
        )
    if scope == STATE_SCOPE_CHARACTER:
        if character is not None:
            return character
        actor_class_name = getattr(getattr(actor, "__class__", None), "__name__", "")
        if actor_class_name == "Player":
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
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
) -> Any:
    normalized = str(path or "").strip()
    if not normalized.startswith("state."):
        return None
    segments = normalized.split(".")
    if len(segments) < 3:
        return None
    scope = segments[1]
    try:
        owner = resolve_scope_owner(
            scope,
            actor=actor,
            world=world,
            zone=zone,
            room=room,
            character=character,
            quest_instance=quest_instance,
        )
    except ValueError:
        return None
    snapshot = get_state_snapshot(scope, owner)
    return _walk_value(snapshot, segments[2:])


def build_state_context(
    *,
    actor=None,
    world=None,
    zone=None,
    room=None,
    character=None,
    quest_instance=None,
) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for scope in STATE_SCOPES:
        owner = resolve_scope_owner(
            scope,
            actor=actor,
            world=world,
            zone=zone,
            room=room,
            character=character,
            quest_instance=quest_instance,
        )
        context[scope] = get_state_snapshot(scope, owner)
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
