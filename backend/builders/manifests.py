from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from builders import serializers as builder_serializers
from builders.models import Trigger
from config import constants as adv_consts
from worlds.models import Room, World, Zone


MANIFEST_API_VERSION = "v1alpha1"
LEGACY_MANIFEST_API_VERSION = "writtenrealms.com/v1alpha1"
TRIGGER_MANIFEST_KIND = "trigger"
TRIGGER_MANIFEST_OPERATION_APPLY = "apply"
TRIGGER_MANIFEST_OPERATION_DELETE = "delete"

_TRIGGER_KEY_PREFIX = "trigger"
_WORLD_KEY_PREFIX = "world"

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
    actions: str
    script: str
    conditions: str
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


def load_yaml_manifest(manifest_text: str) -> dict[str, Any]:
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
    if len(docs) > 1:
        raise serializers.ValidationError("Only a single YAML document is supported.")

    manifest = docs[0]
    if not isinstance(manifest, dict):
        raise serializers.ValidationError("Manifest root must be a mapping.")

    return manifest


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
            "actions": trigger.actions or "",
            "script": trigger.script or "",
            "conditions": trigger.conditions or "",
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
            "actions": "pull lever",
            "script": "/cmd room -- /echo -- Something happens.",
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
    target_type, target_id = _resolve_target(
        world=world,
        scope=scope,
        target_data=spec.get("target"),
        trigger=trigger,
    )

    name = _coerce_text(metadata.get("name", trigger.name if trigger else ""))

    if is_create and scope != adv_consts.TRIGGER_SCOPE_WORLD and spec.get("target") is None:
        raise serializers.ValidationError("spec.target is required when creating a trigger.")

    conditions = _coerce_text(spec.get("conditions", trigger.conditions if trigger else ""))
    if "conditions" in spec:
        builder_serializers.validate_conditions(None, conditions)

    return ParsedTriggerManifest(
        world=world,
        trigger=trigger,
        trigger_id=trigger_id,
        name=name,
        scope=scope,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        actions=_coerce_text(spec.get("actions", trigger.actions if trigger else "")),
        script=_coerce_text(spec.get("script", trigger.script if trigger else "")),
        conditions=conditions,
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
            actions=parsed.actions,
            script=parsed.script,
            conditions=parsed.conditions,
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
    trigger.actions = parsed.actions
    trigger.script = parsed.script
    trigger.conditions = parsed.conditions
    trigger.show_details_on_failure = parsed.show_details_on_failure
    trigger.failure_message = parsed.failure_message
    trigger.display_action_in_room = parsed.display_action_in_room
    trigger.gate_delay = parsed.gate_delay
    trigger.order = parsed.order
    trigger.is_active = parsed.is_active
    trigger.save()
    return trigger
