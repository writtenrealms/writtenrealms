import json
import re

from django.db import migrations
from django.db.models import F


ROW_BATCH_SIZE = 500
WORLD_BATCH_SIZE = 100

_ROOM_ALIAS_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_@-])"
    r"(?:"
    r"room@\s*(?P<x>[+-]?\d+)\s*,\s*(?P<y>[+-]?\d+)\s*,\s*(?P<z>[+-]?\d+)"
    r"|room\.(?P<database_id>\d+)"
    r")"
    r"(?![A-Za-z0-9_@-]|\s*,\s*[+-]?\d)",
    re.IGNORECASE,
)

# These are authored semantic fields that can contain condition DSL room
# operands, room destinations, or literal room references in command text.
# Direct room relations already survive movement and need no data rewrite.
# Runtime state, authored state seeds, and prose are deliberately excluded.
_AUTHORED_FIELD_SPECS = (
    (
        "builders",
        "CraftingRecipe",
        "world_id",
        ("conditions",),
        {},
    ),
    (
        "builders",
        "ItemDefinition",
        "world_id",
        ("base_properties",),
        {},
    ),
    (
        "builders",
        "MobDefinition",
        "world_id",
        ("base_properties", "traits", "loot", "combat_abilities"),
        {},
    ),
    (
        "builders",
        "SpawnPlan",
        "world_id",
        ("conditions",),
        {},
    ),
    (
        "builders",
        "SpawnEntry",
        "plan__world_id",
        ("target", "traits", "loot", "conditions"),
        {},
    ),
    (
        "builders",
        "Trigger",
        "world_id",
        ("script", "steps", "conditions"),
        {},
    ),
    (
        "builders",
        "AbilityDefinition",
        "world_id",
        ("requirements", "components"),
        {},
    ),
    (
        "builders",
        "RoomAction",
        "room__world_id",
        ("commands", "conditions"),
        {},
    ),
    (
        "builders",
        "RoomGetTrigger",
        "room__world_id",
        ("action_argument",),
        {"action": "transport"},
    ),
    (
        "quests",
        "QuestTemplate",
        "world_id",
        (
            "discovery_policy",
            "slot_schema",
            "graph",
            "reward_policy",
        ),
        {},
    ),
    (
        "worlds",
        "WorldConfig",
        "configured_worlds__id",
        ("ability_progression",),
        {
            "configured_worlds__context__isnull": True,
            "configured_worlds__instance_of__isnull": True,
        },
    ),
    (
        "worlds",
        "DeathRoutingRoute",
        "policy__config__configured_worlds__id",
        ("condition",),
        {
            "policy__config__configured_worlds__context__isnull": True,
        },
    ),
)

_FIELD_STRATEGIES = {
    "builders.craftingrecipe": {
        "conditions": "condition",
    },
    "builders.itemdefinition": {
        "base_properties": "semantic",
    },
    "builders.mobdefinition": {
        "base_properties": "semantic",
        "traits": "semantic",
        "loot": "semantic",
        "combat_abilities": "semantic",
    },
    "builders.spawnplan": {
        "conditions": "condition",
    },
    "builders.spawnentry": {
        "target": "semantic",
        "traits": "semantic",
        "loot": "semantic",
        "conditions": "condition",
    },
    "builders.trigger": {
        "script": "command",
        "steps": "semantic",
        "conditions": "condition",
    },
    "builders.abilitydefinition": {
        "requirements": "semantic",
        "components": "semantic",
    },
    "builders.roomaction": {
        "commands": "command",
        "conditions": "condition",
    },
    "builders.roomgettrigger": {
        "action_argument": "direct_room",
    },
    "quests.questtemplate": {
        "discovery_policy": "semantic",
        "slot_schema": "semantic",
        "graph": "semantic",
        "reward_policy": "semantic",
    },
    "worlds.worldconfig": {
        "ability_progression": "semantic",
    },
    "worlds.deathroutingroute": {
        "condition": "condition",
    },
}

_COMMAND_KEYS = {
    "command",
    "commands",
    "script",
    "on_use_cmd",
    "combat_script",
}
_CONDITION_KEYS = {"conditions", "when", "where"}
_DIRECT_ROOM_KEYS = {
    "room",
    "room_ref",
    "room_id",
    "destination",
    "to_room",
}
_CONDITION_OPERATORS = {"all", "any", "not", "eq", "ne", "gte", "lte", "in"}
_EXPLICIT_ROOM_PATHS = {
    "actor.room_id",
    "actor.room.id",
    "player.room_id",
    "player.room.id",
}


def _canonicalize_room_aliases_in_text(
    value,
    *,
    database_id_refs,
    coordinate_refs,
):
    if not isinstance(value, str) or not value:
        return value

    def replace(match):
        raw_database_id = match.group("database_id")
        if raw_database_id is not None:
            canonical = database_id_refs.get(int(raw_database_id))
        else:
            canonical = coordinate_refs.get(
                (
                    int(match.group("x")),
                    int(match.group("y")),
                    int(match.group("z")),
                )
            )
        return canonical if canonical is not None else match.group(0)

    return _ROOM_ALIAS_IN_TEXT_RE.sub(replace, value)


def _canonicalize_direct_room_reference(
    value,
    *,
    database_id_refs,
    coordinate_refs,
):
    if isinstance(value, bool):
        return value, False
    if isinstance(value, int):
        replacement = database_id_refs.get(value)
        return (
            (replacement, True)
            if replacement is not None
            else (value, False)
        )
    if not isinstance(value, str):
        return value, False

    text = value.strip()
    if text.isdigit():
        replacement = database_id_refs.get(int(text))
        if replacement is not None:
            return replacement, True
    replacement = _canonicalize_room_aliases_in_text(
        value,
        database_id_refs=database_id_refs,
        coordinate_refs=coordinate_refs,
    )
    return replacement, replacement != value


def _canonicalize_command_value(
    value,
    *,
    database_id_refs,
    coordinate_refs,
):
    if isinstance(value, str):
        replacement = _canonicalize_room_aliases_in_text(
            value,
            database_id_refs=database_id_refs,
            coordinate_refs=coordinate_refs,
        )
        return replacement, replacement != value
    if isinstance(value, list):
        replacement = []
        changed = False
        for item in value:
            new_item, item_changed = _canonicalize_command_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
            replacement.append(new_item)
            changed = changed or item_changed
        return (replacement if changed else value), changed
    if isinstance(value, dict):
        replacement = dict(value)
        changed = False
        for key, item in value.items():
            new_item, item_changed = _canonicalize_command_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
            if item_changed:
                replacement[key] = new_item
                changed = True
        return (replacement if changed else value), changed
    return value, False


def _condition_sets_event_target_room(condition):
    if not isinstance(condition, dict):
        return False
    for operator in ("eq", "in"):
        raw_args = condition.get(operator)
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            continue
        if str(raw_args[0] or "").strip() != "event.target_type":
            continue
        candidates = raw_args[1] if operator == "in" else [raw_args[1]]
        if not isinstance(candidates, (list, tuple, set)):
            continue
        if any(
            str(candidate or "").strip().lower() == "room"
            for candidate in candidates
        ):
            return True
    return False


def _condition_rhs_uses_room_reference(
    left_path,
    right_value,
    *,
    event_target_is_room,
):
    path = str(left_path or "").strip()
    if path in _EXPLICIT_ROOM_PATHS:
        return True
    if path == "event.target.id" and event_target_is_room:
        return True
    if not isinstance(right_value, str):
        return False
    text = right_value.strip().lower()
    is_typed_room_ref = (
        text.startswith("room@")
        or (
            text.startswith("room.")
            and text.partition(".")[2].isdigit()
        )
    )
    return is_typed_room_ref and (
        path.endswith(".id")
        or path.endswith("_id")
        or path == "event.target.id"
    )


def _canonicalize_condition_value(
    value,
    *,
    database_id_refs,
    coordinate_refs,
    event_target_is_room=False,
):
    if isinstance(value, str):
        text = value.strip()
        if (
            (text.startswith("{") and text.endswith("}"))
            or (text.startswith("[") and text.endswith("]"))
        ):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, (dict, list)):
                replacement, changed = _canonicalize_condition_value(
                    decoded,
                    database_id_refs=database_id_refs,
                    coordinate_refs=coordinate_refs,
                    event_target_is_room=event_target_is_room,
                )
                if changed:
                    return json.dumps(
                        replacement,
                        separators=(",", ":"),
                    ), True
                return value, False
        return _canonicalize_command_value(
            value,
            database_id_refs=database_id_refs,
            coordinate_refs=coordinate_refs,
        )

    if isinstance(value, list):
        replacement = []
        changed = False
        for item in value:
            new_item, item_changed = _canonicalize_condition_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
                event_target_is_room=event_target_is_room,
            )
            replacement.append(new_item)
            changed = changed or item_changed
        return (replacement if changed else value), changed

    if not isinstance(value, dict):
        return value, False

    replacement = dict(value)
    changed = False
    for key, child in value.items():
        if key in {"all", "any", "not"}:
            child_targets_room = event_target_is_room
            if key == "all" and isinstance(child, list):
                child_targets_room = child_targets_room or any(
                    _condition_sets_event_target_room(item)
                    for item in child
                )
            new_child, child_changed = _canonicalize_condition_value(
                child,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
                event_target_is_room=child_targets_room,
            )
        elif key in {"eq", "ne", "gte", "lte", "in"} and (
            isinstance(child, (list, tuple))
            and len(child) == 2
        ):
            left_path, right_value = child
            candidates = (
                right_value
                if key == "in" and isinstance(right_value, list)
                else [right_value]
            )
            rewritten_candidates = []
            candidates_changed = False
            for candidate in candidates:
                if _condition_rhs_uses_room_reference(
                    left_path,
                    candidate,
                    event_target_is_room=event_target_is_room,
                ):
                    new_candidate, candidate_changed = (
                        _canonicalize_direct_room_reference(
                            candidate,
                            database_id_refs=database_id_refs,
                            coordinate_refs=coordinate_refs,
                        )
                    )
                else:
                    new_candidate, candidate_changed = candidate, False
                rewritten_candidates.append(new_candidate)
                candidates_changed = (
                    candidates_changed or candidate_changed
                )
            new_right_value = (
                rewritten_candidates
                if key == "in" and isinstance(right_value, list)
                else rewritten_candidates[0]
            )
            new_child = [left_path, new_right_value]
            child_changed = candidates_changed
        else:
            new_child, child_changed = _canonicalize_semantic_value(
                child,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
        if child_changed:
            replacement[key] = new_child
            changed = True
    return (replacement if changed else value), changed


def _canonicalize_semantic_value(
    value,
    *,
    database_id_refs,
    coordinate_refs,
):
    if isinstance(value, list):
        replacement = []
        changed = False
        for item in value:
            new_item, item_changed = _canonicalize_semantic_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
            replacement.append(new_item)
            changed = changed or item_changed
        return (replacement if changed else value), changed
    if not isinstance(value, dict):
        return value, False
    if _CONDITION_OPERATORS.intersection(value):
        return _canonicalize_condition_value(
            value,
            database_id_refs=database_id_refs,
            coordinate_refs=coordinate_refs,
        )

    replacement = dict(value)
    changed = False
    for key, item in value.items():
        if key in _COMMAND_KEYS:
            new_item, item_changed = _canonicalize_command_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
        elif key in _CONDITION_KEYS:
            new_item, item_changed = _canonicalize_condition_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
        elif key in _DIRECT_ROOM_KEYS:
            new_item, item_changed = _canonicalize_direct_room_reference(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
        else:
            new_item, item_changed = _canonicalize_semantic_value(
                item,
                database_id_refs=database_id_refs,
                coordinate_refs=coordinate_refs,
            )
        if item_changed:
            replacement[key] = new_item
            changed = True
    return (replacement if changed else value), changed


def _canonicalize_authored_value(
    value,
    *,
    database_id_refs,
    coordinate_refs,
    strategy="semantic",
):
    canonicalizer = {
        "command": _canonicalize_command_value,
        "condition": _canonicalize_condition_value,
        "direct_room": _canonicalize_direct_room_reference,
        "semantic": _canonicalize_semantic_value,
    }.get(strategy)
    if canonicalizer is None:
        raise ValueError(f"Unknown room-reference migration strategy: {strategy}")
    return canonicalizer(
        value,
        database_id_refs=database_id_refs,
        coordinate_refs=coordinate_refs,
    )


def _world_batches(world_queryset):
    batch = []
    for world_id in world_queryset.iterator(chunk_size=WORLD_BATCH_SIZE):
        batch.append(world_id)
        if len(batch) >= WORLD_BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def _room_reference_maps(*, Room, database, world_ids):
    maps = {}
    room_rows = (
        Room.objects.using(database)
        .filter(world_id__in=world_ids)
        .order_by("world_id", "id")
        .values_list("world_id", "id", "relative_id", "x", "y", "z")
        .iterator(chunk_size=ROW_BATCH_SIZE)
    )
    for world_id, room_id, relative_id, x, y, z in room_rows:
        if relative_id is None or relative_id <= 0:
            continue
        world_maps = maps.setdefault(
            world_id,
            {
                "database_id_refs": {},
                "coordinate_refs": {},
            },
        )
        canonical = f"room@{relative_id}"
        world_maps["database_id_refs"][room_id] = canonical
        world_maps["coordinate_refs"][(x, y, z)] = canonical
    return maps


def _canonicalize_model_rows(
    *,
    Model,
    database,
    world_ids,
    world_lookup,
    field_names,
    extra_filters,
    room_maps,
):
    field_strategies = _FIELD_STRATEGIES[Model._meta.label_lower]
    queryset = (
        Model.objects.using(database)
        .filter(
            **{
                f"{world_lookup}__in": world_ids,
                **extra_filters,
            }
        )
        .annotate(_authored_world_id=F(world_lookup))
        .only("id", *field_names)
        .order_by("_authored_world_id", "id")
    )
    pending = []
    for row in queryset.iterator(chunk_size=ROW_BATCH_SIZE):
        refs = room_maps.get(row._authored_world_id)
        if refs is None:
            continue

        changed = False
        for field_name in field_names:
            replacement, field_changed = _canonicalize_authored_value(
                getattr(row, field_name),
                **refs,
                strategy=field_strategies[field_name],
            )
            if field_changed:
                setattr(row, field_name, replacement)
                changed = True
        if not changed:
            continue

        pending.append(row)
        if len(pending) >= ROW_BATCH_SIZE:
            Model.objects.using(database).bulk_update(
                pending,
                field_names,
                batch_size=ROW_BATCH_SIZE,
            )
            pending = []

    if pending:
        Model.objects.using(database).bulk_update(
            pending,
            field_names,
            batch_size=ROW_BATCH_SIZE,
        )


def canonicalize_authored_room_references(apps, schema_editor):
    Room = apps.get_model("worlds", "Room")
    World = apps.get_model("worlds", "World")
    database = schema_editor.connection.alias
    model_specs = []
    for (
        app_label,
        model_name,
        world_lookup,
        field_names,
        extra_filters,
    ) in _AUTHORED_FIELD_SPECS:
        Model = apps.get_model(app_label, model_name)
        concrete_fields = {
            field.name
            for field in Model._meta.concrete_fields
        }
        available_fields = tuple(
            field_name
            for field_name in field_names
            if field_name in concrete_fields
        )
        if available_fields:
            model_specs.append((
                Model,
                world_lookup,
                available_fields,
                extra_filters,
            ))

    world_ids = (
        World.objects.using(database)
        .filter(context__isnull=True)
        .order_by("id")
        .values_list("id", flat=True)
    )
    for batch in _world_batches(world_ids):
        room_maps = _room_reference_maps(
            Room=Room,
            database=database,
            world_ids=batch,
        )
        if not room_maps:
            continue
        for Model, world_lookup, field_names, extra_filters in model_specs:
            _canonicalize_model_rows(
                Model=Model,
                database=database,
                world_ids=batch,
                world_lookup=world_lookup,
                field_names=field_names,
                extra_filters=extra_filters,
                room_maps=room_maps,
            )


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("builders", "0253_mob_initial_state"),
        ("worlds", "0123_stable_room_relative_ids"),
        ("quests", "0007_rename_quest_room_item_description"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_authored_room_references,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
