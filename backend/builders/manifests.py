from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

import yaml
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from builders import serializers as builder_serializers
from builders.models import Currency, ItemTemplate, MobTemplate, Trigger
from config import constants as adv_consts
from spawns import trigger_matcher
from worlds.models import Room, World, Zone


MANIFEST_API_VERSION = "v1alpha1"
LEGACY_MANIFEST_API_VERSION = "writtenrealms.com/v1alpha1"
TRIGGER_MANIFEST_KIND = "trigger"
WORLD_MANIFEST_KIND = "world"
QUEST_MANIFEST_KIND = "quest"
QUEST_ARC_MANIFEST_KIND = "questarc"
ITEM_TEMPLATE_MANIFEST_KIND = "itemtemplate"
TRIGGER_MANIFEST_OPERATION_APPLY = "apply"
TRIGGER_MANIFEST_OPERATION_DELETE = "delete"

_TRIGGER_KEY_PREFIX = "trigger"
_WORLD_KEY_PREFIX = "world"

_QUEST_ARC_MANIFEST_KIND_ALIASES = {
    QUEST_ARC_MANIFEST_KIND,
    "quest-arc",
    "quest_arc",
}
_ITEM_TEMPLATE_MANIFEST_KIND_ALIASES = {
    ITEM_TEMPLATE_MANIFEST_KIND,
    "item-template",
    "item_template",
}

_WORLD_CONFIG_WORLD_TEXT_FIELDS = (
    "name",
    "short_description",
    "description",
    "motd",
)
_WORLD_CONFIG_WORLD_BOOL_FIELDS = (
    "is_public",
)
_WORLD_CONFIG_CONFIG_TEXT_FIELDS = (
    "built_by",
    "small_background",
    "large_background",
    "name_exclusions",
)
_WORLD_CONFIG_CONFIG_BOOL_FIELDS = (
    "can_select_faction",
    "auto_equip",
    "is_narrative",
    "players_can_set_title",
    "allow_pvp",
    "is_classless",
    "non_ascii_names",
    "decay_glory",
    "globals_enabled",
)
_WORLD_CONFIG_CONFIG_INT_FIELDS = (
    "starting_gold",
)
_WORLD_CONFIG_CONFIG_FLOAT_FIELDS = (
    "combat_resolution_interval",
)
_WORLD_CONFIG_CONFIG_CHOICE_FIELDS = {
    "death_mode": adv_consts.DEATH_MODES,
    "death_route": adv_consts.DEATH_ROUTES,
    "pvp_mode": adv_consts.PVP_MODES,
}
_WORLD_CONFIG_CONFIG_ROOM_FIELDS = (
    "starting_room",
    "death_room",
)
_WORLD_FIELDS_PROPAGATED_TO_SPAWNS = {
    "name",
    "short_description",
    "description",
    "motd",
    "is_public",
}

_SCOPE_TO_TARGET_MODEL = {
    adv_consts.TRIGGER_SCOPE_ROOM: Room,
    adv_consts.TRIGGER_SCOPE_ZONE: Zone,
    adv_consts.TRIGGER_SCOPE_WORLD: World,
}

_SCOPE_TO_TARGET_TYPE = {
    adv_consts.TRIGGER_SCOPE_ROOM: "room",
    adv_consts.TRIGGER_SCOPE_ZONE: "zone",
    adv_consts.TRIGGER_SCOPE_WORLD: "world",
}

_EVENT_TARGET_TYPES = {
    "mobtemplate": ("builders", "mobtemplate"),
    "mob_template": ("builders", "mobtemplate"),
}

_ITEM_TEMPLATE_SPEC_FIELDS = (
    "level",
    "description",
    "ground_description",
    "notes",
    "keywords",
    "type",
    "is_persistent",
    "capacity",
    "quality",
    "is_boat",
    "is_pickable",
    "cost",
    "currency",
    "food_value",
    "food_type",
    "equipment_type",
    "armor_class",
    "weapon_grip",
    "weapon_type",
    "skill_modifier",
    "hit_msg_first",
    "hit_msg_third",
    "on_use_cmd",
    "on_use_description",
    "on_use_equipped",
    "health_max",
    "health_regen",
    "mana_max",
    "mana_regen",
    "stamina_max",
    "stamina_regen",
    "strength",
    "constitution",
    "dexterity",
    "intelligence",
    "attack_power",
    "spell_power",
    "resilience",
    "dodge",
    "crit",
)


class _ManifestDumper(yaml.SafeDumper):
    pass


def _string_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_ManifestDumper.add_representer(str, _string_representer)


@dataclass
class ParsedTriggerManifest:
    world: World
    trigger: Trigger | None
    trigger_id: int | None
    name: str
    scope: str
    kind: str
    target_type: ContentType
    target_id: int
    match: str
    script: str
    conditions: str
    event: str
    show_details_on_failure: bool
    failure_message: str
    display_action_in_room: bool
    gate_delay: int
    order: int
    is_active: bool


@dataclass
class ParsedTriggerDeleteManifest:
    world: World
    trigger: Trigger
    trigger_id: int


@dataclass
class ParsedWorldConfigManifest:
    world: World
    world_updates: dict[str, Any]
    config_updates: dict[str, Any]


@dataclass
class ParsedItemTemplateManifest:
    world: World
    item_template: ItemTemplate | None
    item_template_id: int | None
    slug: str
    name: str
    serializer_data: dict[str, Any]


@dataclass
class ParsedItemTemplateDeleteManifest:
    world: World
    item_template: ItemTemplate
    item_template_id: int


def _entity_key(entity_type: str, entity_id: int) -> str:
    return f"{entity_type}.{entity_id}"


def _parse_entity_ref(value: Any, expected_type: str, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a '{expected_type}.<id>' key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a '{expected_type}.<id>' key."
        )
    if text.isdigit():
        return int(text)

    parts = text.split(".", 1)
    if len(parts) != 2:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a '{expected_type}.<id>' key."
        )
    entity_type, raw_id = parts
    if entity_type != expected_type or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a '{expected_type}.<id>' key."
        )
    return int(raw_id)


def _parse_trigger_id(value: Any, field_name: str) -> int:
    return _parse_entity_ref(
        value,
        expected_type=_TRIGGER_KEY_PREFIX,
        field_name=field_name,
    )


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _deep_merge(base: Any, patch: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    merged = dict(base)
    for key, value in patch.items():
        if key in merged:
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _slug_or_error(value: str, field_name: str) -> str:
    slug = slugify(value or "")
    if not slug:
        raise serializers.ValidationError(
            f"{field_name} must contain at least one slug-safe character."
        )
    return slug


def _normalize_kind(value: Any, field_name: str = "kind") -> str:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    return text.lower()


def _validate_api_version(manifest: dict[str, Any]) -> None:
    raw_version = manifest.get("apiVersion")
    if raw_version in (None, ""):
        return

    api_version = str(raw_version).strip()
    allowed_versions = {MANIFEST_API_VERSION, LEGACY_MANIFEST_API_VERSION}
    if api_version not in allowed_versions:
        raise serializers.ValidationError(
            f"Unsupported apiVersion '{api_version}'. Allowed: {', '.join(sorted(allowed_versions))}."
        )


def parse_manifest_kind(manifest: dict[str, Any]) -> str:
    _validate_api_version(manifest)
    manifest_kind = _normalize_kind(manifest.get("kind"), "kind")
    if manifest_kind == TRIGGER_MANIFEST_KIND:
        return TRIGGER_MANIFEST_KIND
    if manifest_kind == WORLD_MANIFEST_KIND:
        return WORLD_MANIFEST_KIND
    if manifest_kind in _ITEM_TEMPLATE_MANIFEST_KIND_ALIASES:
        return ITEM_TEMPLATE_MANIFEST_KIND
    if manifest_kind == QUEST_MANIFEST_KIND:
        return QUEST_MANIFEST_KIND
    if manifest_kind in _QUEST_ARC_MANIFEST_KIND_ALIASES:
        return QUEST_ARC_MANIFEST_KIND
    raise serializers.ValidationError(
        f"Unsupported manifest kind '{manifest_kind}'. "
        f"Supported kinds: {TRIGGER_MANIFEST_KIND}, {WORLD_MANIFEST_KIND}, {ITEM_TEMPLATE_MANIFEST_KIND}, {QUEST_MANIFEST_KIND}, {QUEST_ARC_MANIFEST_KIND}."
    )


def parse_manifest_operation(manifest: dict[str, Any]) -> str:
    operation = str(manifest.get("operation") or TRIGGER_MANIFEST_OPERATION_APPLY).strip().lower()
    allowed = {
        TRIGGER_MANIFEST_OPERATION_APPLY,
        TRIGGER_MANIFEST_OPERATION_DELETE,
    }
    if operation not in allowed:
        raise serializers.ValidationError(
            f"Unsupported operation '{operation}'. Allowed: {', '.join(sorted(allowed))}."
        )
    return operation


def _coerce_choice(value: Any, choices: list[str], field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise serializers.ValidationError(
            f"{field_name} must be one of: {', '.join(choices)}."
        )
    return normalized


def _canonical_trigger_kind(kind: str | None) -> str:
    return str(kind or "").strip().lower()


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise serializers.ValidationError(f"{field_name} must be a boolean.")

    text = str(value or "").strip().lower()
    if text in ("true", "1", "yes", "y", "on"):
        return True
    if text in ("false", "0", "no", "n", "off"):
        return False
    raise serializers.ValidationError(f"{field_name} must be a boolean.")


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"{field_name} must be an integer.")


def _coerce_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be a number.")
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"{field_name} must be a number.")
    if not math.isfinite(coerced):
        raise serializers.ValidationError(f"{field_name} must be a finite number.")
    return coerced


def _serialize_number(value: Any) -> int | float:
    numeric = float(value or 0)
    if numeric.is_integer():
        return int(numeric)
    return numeric


def load_yaml_documents(manifest_text: str) -> list[dict[str, Any]]:
    if not isinstance(manifest_text, str):
        raise serializers.ValidationError("Manifest must be a YAML string.")
    if not manifest_text.strip():
        raise serializers.ValidationError("Manifest is empty.")

    try:
        docs = [doc for doc in yaml.safe_load_all(manifest_text) if doc is not None]
    except yaml.YAMLError as exc:
        raise serializers.ValidationError(f"Invalid YAML: {exc}")

    if not docs:
        raise serializers.ValidationError("Manifest is empty.")

    manifests = []
    for document in docs:
        if not isinstance(document, dict):
            raise serializers.ValidationError("Manifest root must be a mapping.")
        manifests.append(document)
    return manifests


def load_yaml_manifest(manifest_text: str) -> dict[str, Any]:
    manifests = load_yaml_documents(manifest_text)
    if len(manifests) > 1:
        raise serializers.ValidationError("Only a single YAML document is supported.")
    return manifests[0]


def _serialize_room_reference(room: Room | None) -> dict[str, Any] | None:
    if room is None:
        return None
    return {
        "id": room.id,
        "key": room.key,
        "name": room.name or "",
        "model_type": "room",
    }


def _serialize_world_room_reference(*, room: Room | None, mode: str) -> str:
    if room is None:
        return ""
    if mode == "key":
        return _entity_key("room", room.id)
    if mode == "coords":
        return f"room@{room.x},{room.y},{room.z}"
    raise ValueError(f"Unsupported world room reference mode '{mode}'.")


def world_config_to_manifest(
    *,
    world: World,
    manifest_kind: str = WORLD_MANIFEST_KIND,
    include_metadata: bool = True,
    room_reference_mode: str = "key",
) -> dict[str, Any]:
    config = world.config
    if not config:
        raise serializers.ValidationError("World has no config to serialize.")

    manifest = {
        "kind": manifest_kind,
        "spec": {
            "name": world.name or "",
            "short_description": world.short_description or "",
            "description": world.description or "",
            "motd": world.motd or "",
            "is_public": bool(world.is_public),
            "starting_gold": int(config.starting_gold),
            "combat_resolution_interval": _serialize_number(
                config.combat_resolution_interval
            ),
            "starting_room": _serialize_world_room_reference(
                room=config.starting_room,
                mode=room_reference_mode,
            ),
            "death_room": _serialize_world_room_reference(
                room=config.death_room,
                mode=room_reference_mode,
            ),
            "death_mode": config.death_mode,
            "death_route": config.death_route,
            "pvp_mode": config.pvp_mode,
            "can_select_faction": bool(config.can_select_faction),
            "auto_equip": bool(config.auto_equip),
            "is_narrative": bool(config.is_narrative),
            "players_can_set_title": bool(config.players_can_set_title),
            "allow_pvp": bool(config.allow_pvp),
            "is_classless": bool(config.is_classless),
            "non_ascii_names": bool(config.non_ascii_names),
            "globals_enabled": bool(config.globals_enabled),
            "decay_glory": bool(config.decay_glory),
            "built_by": config.built_by or "",
            "small_background": config.small_background or "",
            "large_background": config.large_background or "",
            "name_exclusions": config.name_exclusions or "",
        },
    }
    if include_metadata:
        manifest["metadata"] = {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
        }
    return manifest


def serialize_world_config_manifest(
    *,
    world: World,
    manifest_kind: str = WORLD_MANIFEST_KIND,
    include_metadata: bool = True,
    room_reference_mode: str = "key",
) -> dict[str, Any]:
    manifest = world_config_to_manifest(
        world=world,
        manifest_kind=manifest_kind,
        include_metadata=include_metadata,
        room_reference_mode=room_reference_mode,
    )
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def serialize_world_config_payload(*, world: World) -> dict[str, Any]:
    config = world.config
    if not config:
        raise serializers.ValidationError("World has no config to serialize.")

    manifest_data = serialize_world_config_manifest(
        world=world,
        manifest_kind=WORLD_MANIFEST_KIND,
        include_metadata=False,
        room_reference_mode="coords",
    )
    return {
        "world": {
            "id": world.id,
            "key": world.key,
            "name": world.name or "",
            "short_description": world.short_description or "",
            "description": world.description or "",
            "motd": world.motd or "",
            "is_public": bool(world.is_public),
        },
        "config": {
            "starting_gold": int(config.starting_gold),
            "combat_resolution_interval": _serialize_number(
                config.combat_resolution_interval
            ),
            "starting_room": _serialize_room_reference(config.starting_room),
            "death_room": _serialize_room_reference(config.death_room),
            "death_mode": config.death_mode,
            "death_route": config.death_route,
            "small_background": config.small_background or "",
            "large_background": config.large_background or "",
            "can_select_faction": bool(config.can_select_faction),
            "auto_equip": bool(config.auto_equip),
            "allow_combat": bool(config.allow_combat),
            "is_narrative": bool(config.is_narrative),
            "players_can_set_title": bool(config.players_can_set_title),
            "allow_pvp": bool(config.allow_pvp),
            "pvp_mode": config.pvp_mode,
            "built_by": config.built_by or "",
            "is_classless": bool(config.is_classless),
            "non_ascii_names": bool(config.non_ascii_names),
            "decay_glory": bool(config.decay_glory),
            "name_exclusions": config.name_exclusions or "",
            "globals_enabled": bool(config.globals_enabled),
        },
        "manifest": manifest_data["manifest"],
        "yaml": manifest_data["yaml"],
    }


def _serialize_currency_reference(currency: Currency | None) -> str:
    if currency is None:
        return ""
    return currency.code or ""


def _item_template_spec_from_instance(item_template: ItemTemplate) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    for field_name in _ITEM_TEMPLATE_SPEC_FIELDS:
        if field_name == "currency":
            spec[field_name] = _serialize_currency_reference(item_template.currency)
            continue

        value = getattr(item_template, field_name, "")
        if value is None:
            spec[field_name] = ""
        else:
            spec[field_name] = value
    return spec


def _new_item_template_defaults(*, world: World) -> ItemTemplate:
    item_template = ItemTemplate(world=world)
    item_template.currency = Currency.objects.filter(
        world=world,
        is_default=True,
    ).first()
    return item_template


def item_template_manifest_template(*, world: World) -> dict[str, Any]:
    item_template = _new_item_template_defaults(world=world)
    return {
        "kind": ITEM_TEMPLATE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
            "slug": "new-item-template",
            "name": "New Item Template",
        },
        "spec": _item_template_spec_from_instance(item_template),
    }


def item_template_to_manifest(item_template: ItemTemplate) -> dict[str, Any]:
    return {
        "kind": ITEM_TEMPLATE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_template.world_id),
            "id": item_template.id,
            "key": item_template.key,
            "slug": item_template.slug,
            "name": item_template.name or "",
        },
        "spec": _item_template_spec_from_instance(item_template),
    }


def item_template_delete_manifest(item_template: ItemTemplate) -> dict[str, Any]:
    return {
        "kind": ITEM_TEMPLATE_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_template.world_id),
            "id": item_template.id,
            "key": item_template.key,
            "slug": item_template.slug,
            "name": item_template.name or "",
        },
    }


def serialize_item_template_payload(item_template: ItemTemplate) -> dict[str, Any]:
    payload = builder_serializers.ItemTemplateSerializer(item_template).data
    manifest = item_template_to_manifest(item_template)
    delete_manifest = item_template_delete_manifest(item_template)
    payload["manifest"] = manifest
    payload["yaml"] = manifest_to_yaml(manifest)
    payload["delete_manifest"] = delete_manifest
    payload["delete_yaml"] = manifest_to_yaml(delete_manifest)
    return payload


def trigger_to_manifest(trigger: Trigger) -> dict[str, Any]:
    target_type = _SCOPE_TO_TARGET_TYPE.get(trigger.scope, "")
    target_key = ""
    target_name = ""
    if trigger.target_type_id and trigger.target_id:
        target_type = trigger.target_type.model
        target_key = _entity_key(target_type, trigger.target_id)
        if trigger.target:
            target_name = getattr(trigger.target, "name", "") or ""

    manifest = {
        "kind": TRIGGER_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, trigger.world_id),
            "id": trigger.id,
            "key": trigger.key,
            "name": trigger.name or "",
        },
        "spec": {
            "scope": trigger.scope,
            "kind": _canonical_trigger_kind(trigger.kind),
            "target": {
                "type": target_type,
                "key": target_key,
            },
            "match": trigger.match or "",
            "script": trigger.script or "",
            "conditions": _deserialize_conditions_payload(trigger.conditions),
            "event": trigger.event or "",
            "show_details_on_failure": bool(trigger.show_details_on_failure),
            "failure_message": trigger.failure_message or "",
            "display_action_in_room": bool(trigger.display_action_in_room),
            "gate_delay": int(trigger.gate_delay),
            "order": int(trigger.order),
            "is_active": bool(trigger.is_active),
        },
    }
    if target_name:
        manifest["spec"]["target"]["name"] = target_name

    return manifest


def manifest_to_yaml(manifest: dict[str, Any]) -> str:
    return yaml.dump(
        manifest,
        Dumper=_ManifestDumper,
        sort_keys=False,
        default_flow_style=False,
    )


def _deserialize_conditions_payload(raw_conditions: str | None) -> Any:
    text = str(raw_conditions or "").strip()
    if not text:
        return ""
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return raw_conditions or ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return raw_conditions or ""
    if isinstance(parsed, (dict, list)):
        return parsed
    return raw_conditions or ""


def _coerce_conditions_payload(raw_conditions: Any) -> str:
    if isinstance(raw_conditions, (dict, list)):
        builder_serializers.validate_conditions(None, raw_conditions)
        return json.dumps(raw_conditions)
    conditions = _coerce_text(raw_conditions)
    if conditions:
        builder_serializers.validate_conditions(None, conditions)
    return conditions


def serialize_trigger_manifest(trigger: Trigger) -> dict[str, Any]:
    manifest = trigger_to_manifest(trigger)
    delete_manifest = trigger_delete_manifest(trigger)
    target_data = manifest["spec"]["target"]
    return {
        "id": trigger.id,
        "key": trigger.key,
        "name": trigger.name or "",
        "scope": trigger.scope,
        "kind": _canonical_trigger_kind(trigger.kind),
        "event": trigger.event or "",
        "match": trigger.match or "",
        "target": {
            "type": target_data.get("type", ""),
            "key": target_data.get("key", ""),
            "name": target_data.get("name", ""),
        },
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def room_trigger_template_manifest(*, world: World, room: Room) -> dict[str, Any]:
    return {
        "kind": TRIGGER_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
            "name": f"{room.name} Trigger",
        },
        "spec": {
            "scope": adv_consts.TRIGGER_SCOPE_ROOM,
            "kind": adv_consts.TRIGGER_KIND_COMMAND,
            "target": {
                "type": _SCOPE_TO_TARGET_TYPE[adv_consts.TRIGGER_SCOPE_ROOM],
                "key": _entity_key("room", room.id),
                "name": room.name or "",
            },
            "match": "pull lever",
            "script": (
                "/cmd room -- /echo *CLICK*.\n"
                "/cmd room -- /echo Something happens.\n"
            ),
            "conditions": "",
            "show_details_on_failure": False,
            "failure_message": "",
            "display_action_in_room": True,
            "gate_delay": 10,
            "order": 0,
            "is_active": True,
        },
    }


def serialize_room_trigger_template(*, world: World, room: Room) -> dict[str, Any]:
    manifest = room_trigger_template_manifest(world=world, room=room)
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def mob_trigger_template_manifest(*, world: World, mob_template: MobTemplate) -> dict[str, Any]:
    template_name = mob_template.name or f"Mob {mob_template.id}"
    return {
        "kind": TRIGGER_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
            "name": f"{template_name} Reaction",
        },
        "spec": {
            "scope": adv_consts.TRIGGER_SCOPE_WORLD,
            "kind": adv_consts.TRIGGER_KIND_EVENT,
            "target": {
                "type": "mobtemplate",
                "key": _entity_key("mobtemplate", mob_template.id),
                "name": template_name,
            },
            "event": adv_consts.MOB_REACTION_EVENT_SAYING,
            "match": "hello and (traveler or friend)",
            "script": "say Welcome, traveler.",
            "conditions": "",
            "show_details_on_failure": False,
            "failure_message": "",
            "display_action_in_room": False,
            "gate_delay": 10,
            "order": 0,
            "is_active": True,
        },
    }


def serialize_mob_trigger_template(*, world: World, mob_template: MobTemplate) -> dict[str, Any]:
    manifest = mob_trigger_template_manifest(world=world, mob_template=mob_template)
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def trigger_delete_manifest(trigger: Trigger) -> dict[str, Any]:
    return {
        "kind": TRIGGER_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, trigger.world_id),
            "id": trigger.id,
            "key": trigger.key,
            "name": trigger.name or "",
        },
    }


def _resolve_target(
    *,
    world: World,
    scope: str,
    target_data: Any,
    trigger: Trigger | None,
) -> tuple[ContentType, int]:
    model_cls = _SCOPE_TO_TARGET_MODEL[scope]
    expected_type = _SCOPE_TO_TARGET_TYPE[scope]

    if target_data is None:
        if scope == adv_consts.TRIGGER_SCOPE_WORLD:
            if trigger.target_type_id and trigger.target_id:
                trigger_model = trigger.target_type.model_class()
                if trigger_model is not World:
                    raise serializers.ValidationError(
                        "Existing trigger target does not match scope 'world'."
                    )
                if trigger.target_id != world.id:
                    raise serializers.ValidationError(
                        "World scoped trigger target must belong to this world."
                    )
                return trigger.target_type, trigger.target_id
            return ContentType.objects.get_for_model(World), world.id

        if not trigger.target_type_id or not trigger.target_id:
            raise serializers.ValidationError("spec.target is required.")
        trigger_model = trigger.target_type.model_class()
        if trigger_model is not model_cls:
            raise serializers.ValidationError(
                f"spec.target.type must be '{expected_type}' for scope '{scope}'."
            )
        exists = model_cls.objects.filter(world=world, pk=trigger.target_id).exists()
        if not exists:
            raise serializers.ValidationError("Existing trigger target does not exist.")
        return trigger.target_type, trigger.target_id

    if not isinstance(target_data, dict):
        raise serializers.ValidationError("spec.target must be a mapping.")

    target_type = str(target_data.get("type") or expected_type).strip().lower()
    if target_type != expected_type:
        raise serializers.ValidationError(
            f"spec.target.type must be '{expected_type}' for scope '{scope}'."
        )

    target_ref = target_data.get("key", target_data.get("id"))
    if target_ref is None:
        if scope == adv_consts.TRIGGER_SCOPE_WORLD:
            target_id = world.id
        else:
            raise serializers.ValidationError("spec.target.key is required.")
    else:
        target_id = _parse_entity_ref(
            target_ref,
            expected_type=expected_type,
            field_name="spec.target.key",
        )

    if scope == adv_consts.TRIGGER_SCOPE_WORLD:
        if target_id != world.id:
            raise serializers.ValidationError(
                "World scoped triggers must target the current world."
            )
        return ContentType.objects.get_for_model(World), world.id

    target_obj = model_cls.objects.filter(world=world, pk=target_id).first()
    if not target_obj:
        raise serializers.ValidationError("Trigger target does not exist in this world.")
    return ContentType.objects.get_for_model(model_cls), target_obj.id


def _resolve_event_target(
    *,
    world: World,
    target_data: Any,
    trigger: Trigger | None,
) -> tuple[ContentType, int]:
    if target_data is None:
        if not trigger or not trigger.target_type_id or not trigger.target_id:
            raise serializers.ValidationError("spec.target is required.")

        model_name = trigger.target_type.model
        if model_name not in _EVENT_TARGET_TYPES:
            raise serializers.ValidationError(
                "Event triggers must target one of: "
                + ", ".join(sorted(_EVENT_TARGET_TYPES.keys()))
                + "."
            )

        model_cls = trigger.target_type.model_class()
        if not model_cls:
            raise serializers.ValidationError("Existing trigger target type is invalid.")

        exists = model_cls.objects.filter(world=world, pk=trigger.target_id).exists()
        if not exists:
            raise serializers.ValidationError("Existing trigger target does not exist in this world.")
        return trigger.target_type, trigger.target_id

    if not isinstance(target_data, dict):
        raise serializers.ValidationError("spec.target must be a mapping.")

    target_type = str(target_data.get("type") or "").strip().lower()
    if target_type not in _EVENT_TARGET_TYPES:
        raise serializers.ValidationError(
            "spec.target.type must be one of: "
            + ", ".join(sorted(_EVENT_TARGET_TYPES.keys()))
            + "."
        )

    target_ref = target_data.get("key", target_data.get("id"))
    if target_ref is None:
        raise serializers.ValidationError("spec.target.key is required.")

    target_id = _parse_entity_ref(
        target_ref,
        expected_type=target_type,
        field_name="spec.target.key",
    )

    app_label, model_name = _EVENT_TARGET_TYPES[target_type]
    try:
        target_ct = ContentType.objects.get(app_label=app_label, model=model_name)
    except ContentType.DoesNotExist:
        raise serializers.ValidationError("spec.target.type is not available.")

    model_cls = target_ct.model_class()
    if not model_cls:
        raise serializers.ValidationError("spec.target.type could not be resolved.")

    target_obj = model_cls.objects.filter(world=world, pk=target_id).first()
    if not target_obj:
        raise serializers.ValidationError("Trigger target does not exist in this world.")
    return target_ct, target_obj.id


def _resolve_trigger_reference(*, world: World, metadata: dict[str, Any]) -> tuple[Trigger | None, int | None]:
    trigger_key = metadata.get("key")
    trigger_id_raw = metadata.get("id")

    parsed_key_id = None
    parsed_id = None

    if trigger_key is not None:
        parsed_key_id = _parse_trigger_id(trigger_key, "metadata.key")
    if trigger_id_raw is not None:
        parsed_id = _parse_trigger_id(trigger_id_raw, "metadata.id")

    if parsed_key_id and parsed_id and parsed_key_id != parsed_id:
        raise serializers.ValidationError(
            "metadata.id and metadata.key refer to different triggers."
        )

    trigger_id = parsed_key_id or parsed_id
    if trigger_id is None:
        return None, None

    trigger = Trigger.objects.filter(world=world, pk=trigger_id).first()
    if not trigger:
        raise serializers.ValidationError(
            "Trigger referenced by manifest was not found. Omit metadata.id/key to create a new trigger."
        )
    return trigger, trigger_id


def parse_trigger_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedTriggerManifest:
    _validate_api_version(manifest)
    manifest_kind = _normalize_kind(manifest.get("kind"), "kind")
    if manifest_kind != TRIGGER_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{TRIGGER_MANIFEST_KIND}'."
        )

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")

    world_ref = metadata.get("world")
    if world_ref is not None:
        manifest_world_id = _parse_entity_ref(
            world_ref,
            expected_type=_WORLD_KEY_PREFIX,
            field_name="metadata.world",
        )
        if manifest_world_id != world.id:
            raise serializers.ValidationError(
                "Manifest world does not match the selected world."
            )

    trigger, trigger_id = _resolve_trigger_reference(world=world, metadata=metadata)

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")

    is_create = trigger is None
    if is_create and "scope" not in spec:
        raise serializers.ValidationError("spec.scope is required when creating a trigger.")

    scope = _coerce_choice(
        spec.get("scope", trigger.scope if trigger else adv_consts.TRIGGER_SCOPE_ROOM),
        choices=adv_consts.TRIGGER_SCOPES,
        field_name="spec.scope",
    )
    kind = _coerce_choice(
        spec.get("kind", trigger.kind if trigger else adv_consts.TRIGGER_KIND_COMMAND),
        choices=adv_consts.TRIGGER_KINDS,
        field_name="spec.kind",
    )
    kind = _canonical_trigger_kind(kind)
    if kind == adv_consts.TRIGGER_KIND_EVENT and scope != adv_consts.TRIGGER_SCOPE_WORLD:
        raise serializers.ValidationError("Event triggers must use scope 'world'.")

    if kind == adv_consts.TRIGGER_KIND_EVENT:
        target_type, target_id = _resolve_event_target(
            world=world,
            target_data=spec.get("target"),
            trigger=trigger,
        )
    else:
        target_type, target_id = _resolve_target(
            world=world,
            scope=scope,
            target_data=spec.get("target"),
            trigger=trigger,
        )

    name = _coerce_text(metadata.get("name", trigger.name if trigger else ""))

    if (
        is_create
        and spec.get("target") is None
        and (kind == adv_consts.TRIGGER_KIND_EVENT or scope != adv_consts.TRIGGER_SCOPE_WORLD)
    ):
        raise serializers.ValidationError("spec.target is required when creating a trigger.")

    conditions = _coerce_conditions_payload(
        spec.get("conditions", trigger.conditions if trigger else ""),
    )

    match = _coerce_text(spec.get("match", trigger.match if trigger else ""))
    if match:
        try:
            trigger_matcher.validate_match_expression(match)
        except trigger_matcher.MatchExpressionError as err:
            raise serializers.ValidationError(f"Invalid spec.match matcher expression: {err}")

    event = _coerce_text(spec.get("event", trigger.event if trigger else "")).strip().lower()
    if kind == adv_consts.TRIGGER_KIND_EVENT:
        if not event:
            raise serializers.ValidationError("spec.event is required for kind 'event'.")
        event = _coerce_choice(
            event,
            choices=adv_consts.MOB_REACTION_EVENTS,
            field_name="spec.event",
        )
    elif event:
        event = _coerce_choice(
            event,
            choices=adv_consts.MOB_REACTION_EVENTS,
            field_name="spec.event",
        )

    if kind == adv_consts.TRIGGER_KIND_COMMAND and not match.strip():
        raise serializers.ValidationError("spec.match is required for kind 'command'.")

    if (
        kind == adv_consts.TRIGGER_KIND_EVENT
        and event in (
            adv_consts.MOB_REACTION_EVENT_SAYING,
            adv_consts.MOB_REACTION_EVENT_RECEIVE,
            adv_consts.MOB_REACTION_EVENT_PERIODIC,
        )
        and not match.strip()
    ):
        raise serializers.ValidationError(f"spec.match is required for event '{event}'.")

    return ParsedTriggerManifest(
        world=world,
        trigger=trigger,
        trigger_id=trigger_id,
        name=name,
        scope=scope,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        match=match,
        script=_coerce_text(spec.get("script", trigger.script if trigger else "")),
        conditions=conditions,
        event=event,
        show_details_on_failure=_coerce_bool(
            spec.get(
                "show_details_on_failure",
                trigger.show_details_on_failure if trigger else False,
            ),
            "spec.show_details_on_failure",
        ),
        failure_message=_coerce_text(
            spec.get("failure_message", trigger.failure_message if trigger else "")
        ),
        display_action_in_room=_coerce_bool(
            spec.get(
                "display_action_in_room",
                trigger.display_action_in_room if trigger else True,
            ),
            "spec.display_action_in_room",
        ),
        gate_delay=_coerce_int(
            spec.get("gate_delay", trigger.gate_delay if trigger else 10),
            "spec.gate_delay",
        ),
        order=_coerce_int(spec.get("order", trigger.order if trigger else 0), "spec.order"),
        is_active=_coerce_bool(
            spec.get("is_active", trigger.is_active if trigger else True),
            "spec.is_active",
        ),
    )


def parse_trigger_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedTriggerDeleteManifest:
    _validate_api_version(manifest)
    manifest_kind = _normalize_kind(manifest.get("kind"), "kind")
    if manifest_kind != TRIGGER_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{TRIGGER_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")

    world_ref = metadata.get("world")
    if world_ref is not None:
        manifest_world_id = _parse_entity_ref(
            world_ref,
            expected_type=_WORLD_KEY_PREFIX,
            field_name="metadata.world",
        )
        if manifest_world_id != world.id:
            raise serializers.ValidationError(
                "Manifest world does not match the selected world."
            )

    trigger, trigger_id = _resolve_trigger_reference(world=world, metadata=metadata)
    if trigger is None or trigger_id is None:
        raise serializers.ValidationError(
            "metadata.id or metadata.key is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedTriggerDeleteManifest(
        world=world,
        trigger=trigger,
        trigger_id=trigger_id,
    )


def _parse_item_template_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item template key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item template key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item template key."
        )
    if entity_type not in {"itemtemplate", "item_template"}:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item template key."
        )
    return int(raw_id)


def _resolve_item_template_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[ItemTemplate | None, int | None]:
    template_id = metadata.get("id")
    template_key = metadata.get("key")
    template_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if template_id is not None:
        parsed_id = _parse_item_template_reference(template_id, "metadata.id")
        resolved_by_id = ItemTemplate.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Item template referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if template_key not in (None, ""):
        parsed_key_id = _parse_item_template_reference(template_key, "metadata.key")
        resolved_by_key = ItemTemplate.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Item template referenced by metadata.key was not found."
            )

    resolved_by_slug = None
    if template_slug:
        resolved_by_slug = ItemTemplate.objects.filter(world=world, slug=template_slug).first()

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_slug) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different item templates."
        )

    item_template = resolved_by_id or resolved_by_key or resolved_by_slug
    if item_template is None:
        return None, None
    return item_template, item_template.id


def _resolve_currency_reference(*, world: World, value: Any, field_name: str) -> Currency | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be a currency id, 'currency.<id>', or currency code."
        )
    if isinstance(value, int):
        currency = Currency.objects.filter(world=world, pk=value).first()
        if currency:
            return currency
        raise serializers.ValidationError(f"{field_name} references an unknown currency.")

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        currency = Currency.objects.filter(world=world, pk=int(text)).first()
        if currency:
            return currency
        raise serializers.ValidationError(f"{field_name} references an unknown currency.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix != "currency":
            raise serializers.ValidationError(
                f"{field_name} must be a currency id, 'currency.<id>', or currency code."
            )
        text = raw
        if text.isdigit():
            currency = Currency.objects.filter(world=world, pk=int(text)).first()
            if currency:
                return currency
            raise serializers.ValidationError(f"{field_name} references an unknown currency.")

    currency = Currency.objects.filter(world=world, code=text).first()
    if currency:
        return currency
    raise serializers.ValidationError(f"{field_name} references an unknown currency.")


def parse_item_template_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemTemplateManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_TEMPLATE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_TEMPLATE_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Item template manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
        )

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")

    world_ref = metadata.get("world")
    if world_ref is not None:
        manifest_world_id = _parse_entity_ref(
            world_ref,
            expected_type=_WORLD_KEY_PREFIX,
            field_name="metadata.world",
        )
        if manifest_world_id != world.id:
            raise serializers.ValidationError("Manifest world does not match the selected world.")

    item_template, item_template_id = _resolve_item_template_reference(
        world=world,
        metadata=metadata,
    )

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if item_template is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating an item template.")

    base_spec = item_template_manifest_template(world=world)["spec"]
    if item_template is not None:
        base_spec = item_template_to_manifest(item_template)["spec"]
    merged_spec = _deep_merge(base_spec, spec_patch)

    unknown_fields = sorted(set(merged_spec.keys()) - set(_ITEM_TEMPLATE_SPEC_FIELDS))
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = item_template.slug if item_template else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if ItemTemplate.objects.filter(world=world, slug=slug).exclude(pk=item_template_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another item template."
        )

    default_name = item_template.name if item_template else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    serializer_data: dict[str, Any] = {
        "name": name,
        "slug": slug,
    }

    for field_name in _ITEM_TEMPLATE_SPEC_FIELDS:
        if field_name not in merged_spec:
            continue
        value = merged_spec.get(field_name)
        if field_name == "currency":
            if value == "":
                if item_template is not None:
                    serializer_data[field_name] = None
                continue
            serializer_data[field_name] = (
                _resolve_currency_reference(
                    world=world,
                    value=value,
                    field_name="spec.currency",
                ).id
                if value is not None else None
            )
            continue
        serializer_data[field_name] = value

    serializer = builder_serializers.ItemTemplateSerializer(
        instance=item_template,
        data=serializer_data,
        context={"world": world},
    )
    serializer.is_valid(raise_exception=True)

    return ParsedItemTemplateManifest(
        world=world,
        item_template=item_template,
        item_template_id=item_template_id,
        slug=slug,
        name=name,
        serializer_data=serializer_data,
    )


def parse_item_template_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemTemplateDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_TEMPLATE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_TEMPLATE_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")

    world_ref = metadata.get("world")
    if world_ref is not None:
        manifest_world_id = _parse_entity_ref(
            world_ref,
            expected_type=_WORLD_KEY_PREFIX,
            field_name="metadata.world",
        )
        if manifest_world_id != world.id:
            raise serializers.ValidationError("Manifest world does not match the selected world.")

    item_template, item_template_id = _resolve_item_template_reference(
        world=world,
        metadata=metadata,
    )
    if item_template is None or item_template_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedItemTemplateDeleteManifest(
        world=world,
        item_template=item_template,
        item_template_id=item_template_id,
    )


def parse_world_config_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedWorldConfigManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != WORLD_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{WORLD_MANIFEST_KIND}'."
        )

    operation = str(manifest.get("operation") or TRIGGER_MANIFEST_OPERATION_APPLY).strip().lower()
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"World config manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}'."
        )

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")

    world_ref = metadata.get("world")
    if world_ref is not None:
        manifest_world_id = _parse_entity_ref(
            world_ref,
            expected_type=_WORLD_KEY_PREFIX,
            field_name="metadata.world",
        )
        if manifest_world_id != world.id:
            raise serializers.ValidationError(
                "Manifest world does not match the selected world."
            )

    config = world.config
    if not config:
        raise serializers.ValidationError("Selected world has no world config.")

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")

    allowed_fields = set(_WORLD_CONFIG_WORLD_TEXT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_WORLD_BOOL_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_TEXT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_BOOL_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_INT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_FLOAT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_CHOICE_FIELDS.keys())
    allowed_fields.update(_WORLD_CONFIG_CONFIG_ROOM_FIELDS)

    unknown_fields = sorted(set(spec.keys()) - allowed_fields)
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    world_updates: dict[str, Any] = {}
    for field_name in _WORLD_CONFIG_WORLD_TEXT_FIELDS:
        if field_name in spec:
            world_updates[field_name] = _coerce_text(spec.get(field_name))
    if "name" in world_updates and not world_updates["name"].strip():
        raise serializers.ValidationError("spec.name cannot be empty.")

    for field_name in _WORLD_CONFIG_WORLD_BOOL_FIELDS:
        if field_name in spec:
            world_updates[field_name] = _coerce_bool(
                spec.get(field_name),
                f"spec.{field_name}",
            )

    config_updates: dict[str, Any] = {}

    for field_name in _WORLD_CONFIG_CONFIG_TEXT_FIELDS:
        if field_name in spec:
            config_updates[field_name] = _coerce_text(spec.get(field_name))

    for field_name in _WORLD_CONFIG_CONFIG_BOOL_FIELDS:
        if field_name in spec:
            config_updates[field_name] = _coerce_bool(
                spec.get(field_name),
                f"spec.{field_name}",
            )

    for field_name in _WORLD_CONFIG_CONFIG_INT_FIELDS:
        if field_name in spec:
            value = _coerce_int(spec.get(field_name), f"spec.{field_name}")
            if value < 0:
                raise serializers.ValidationError(f"spec.{field_name} must be >= 0.")
            config_updates[field_name] = value

    for field_name in _WORLD_CONFIG_CONFIG_FLOAT_FIELDS:
        if field_name in spec:
            value = _coerce_float(spec.get(field_name), f"spec.{field_name}")
            if value < 0 and value != -1:
                raise serializers.ValidationError(
                    f"spec.{field_name} must be -1 or >= 0."
                )
            config_updates[field_name] = value

    for field_name, choices in _WORLD_CONFIG_CONFIG_CHOICE_FIELDS.items():
        if field_name in spec:
            config_updates[field_name] = _coerce_choice(
                spec.get(field_name),
                choices=choices,
                field_name=f"spec.{field_name}",
            )

    for field_name in _WORLD_CONFIG_CONFIG_ROOM_FIELDS:
        if field_name not in spec:
            continue
        room_id = _parse_entity_ref(
            spec.get(field_name),
            expected_type="room",
            field_name=f"spec.{field_name}",
        )
        room = Room.objects.filter(world=world, pk=room_id).first()
        if not room:
            raise serializers.ValidationError(
                f"Room referenced by spec.{field_name} was not found in this world."
            )
        config_updates[field_name] = room

    return ParsedWorldConfigManifest(
        world=world,
        world_updates=world_updates,
        config_updates=config_updates,
    )


def apply_world_config_manifest(parsed: ParsedWorldConfigManifest):
    world = parsed.world
    config = world.config
    if not config:
        raise serializers.ValidationError("Selected world has no world config.")

    with transaction.atomic():
        world_updates = parsed.world_updates
        if world_updates:
            for field_name, value in world_updates.items():
                setattr(world, field_name, value)
            world.save(update_fields=list(world_updates.keys()))

            spawn_updates = {
                field_name: value
                for field_name, value in world_updates.items()
                if field_name in _WORLD_FIELDS_PROPAGATED_TO_SPAWNS
            }
            if spawn_updates:
                world.spawned_worlds.update(**spawn_updates)

        config_updates = dict(parsed.config_updates)
        if "is_narrative" in config_updates:
            config_updates["allow_combat"] = not bool(config_updates["is_narrative"])

        if config_updates:
            for field_name, value in config_updates.items():
                setattr(config, field_name, value)
            config.save(update_fields=list(config_updates.keys()))

    return config


def apply_item_template_manifest(parsed: ParsedItemTemplateManifest) -> ItemTemplate:
    serializer = builder_serializers.ItemTemplateSerializer(
        instance=parsed.item_template,
        data=parsed.serializer_data,
        context={"world": parsed.world},
    )
    serializer.is_valid(raise_exception=True)
    if parsed.item_template is None:
        return serializer.save(world=parsed.world)
    return serializer.save()


def apply_trigger_manifest(parsed: ParsedTriggerManifest) -> Trigger:
    trigger = parsed.trigger
    if trigger is None:
        return Trigger.objects.create(
            world=parsed.world,
            name=parsed.name,
            scope=parsed.scope,
            kind=parsed.kind,
            target_type=parsed.target_type,
            target_id=parsed.target_id,
            match=parsed.match,
            script=parsed.script,
            conditions=parsed.conditions,
            event=parsed.event,
            show_details_on_failure=parsed.show_details_on_failure,
            failure_message=parsed.failure_message,
            display_action_in_room=parsed.display_action_in_room,
            gate_delay=parsed.gate_delay,
            order=parsed.order,
            is_active=parsed.is_active,
        )

    trigger.name = parsed.name
    trigger.scope = parsed.scope
    trigger.kind = parsed.kind
    trigger.target_type = parsed.target_type
    trigger.target_id = parsed.target_id
    trigger.match = parsed.match
    trigger.script = parsed.script
    trigger.conditions = parsed.conditions
    trigger.event = parsed.event
    trigger.show_details_on_failure = parsed.show_details_on_failure
    trigger.failure_message = parsed.failure_message
    trigger.display_action_in_room = parsed.display_action_in_room
    trigger.gate_delay = parsed.gate_delay
    trigger.order = parsed.order
    trigger.is_active = parsed.is_active
    trigger.save()
    return trigger
