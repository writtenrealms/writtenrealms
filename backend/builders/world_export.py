from __future__ import annotations

import copy
import json
from typing import Any

import yaml
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from builders import manifests as builder_manifests
from builders import serializers as builder_serializers
from builders.models import (
    AbilityDefinition,
    Currency,
    ItemBundle,
    ItemDefinition,
    ItemTemplate,
    ItemTemplateInventory,
    MerchantProfile,
    MobDefinition,
    MobTemplate,
    MobTemplateInventory,
    Path,
    PathRoom,
    SpawnEntry,
    SpawnPlan,
    Trigger,
)
from config import constants as adv_consts
from core.condition_dsl import validate_condition_payload
from core.scoped_state import STATE_SCOPE_ZONE, get_state_snapshot, replace_state_snapshot
from quests import entity_refs as quest_entity_refs
from quests import manifests as quest_manifests
from quests.models import QuestArcTemplate, QuestTemplate
from worlds.models import Door, Room, RoomDetail, RoomFlag, World, Zone


WORLD_MANIFEST_KIND = "world"
CURRENCY_MANIFEST_KIND = "currency"
ZONE_MANIFEST_KIND = "zone"
ROOM_MANIFEST_KIND = "room"
PATH_MANIFEST_KIND = "path"
MOB_TEMPLATE_MANIFEST_KIND = "mobtemplate"
SPAWN_PLAN_MANIFEST_KIND = "spawnplan"

_WORLD_KIND_ALIASES = {"world"}
_ZONE_KIND_ALIASES = {"zone"}
_ROOM_KIND_ALIASES = {"room"}
_PATH_KIND_ALIASES = {"path"}
_CURRENCY_KIND_ALIASES = {"currency"}
_ITEM_TEMPLATE_KIND_ALIASES = {"itemtemplate", "item-template", "item_template"}
_ITEM_DEFINITION_KIND_ALIASES = {"itemdefinition", "item-definition", "item_definition"}
_ITEM_BUNDLE_KIND_ALIASES = {"itembundle", "item-bundle", "item_bundle"}
_MERCHANT_PROFILE_KIND_ALIASES = {"merchantprofile", "merchant-profile", "merchant_profile"}
_MOB_TEMPLATE_KIND_ALIASES = {"mobtemplate", "mob-template", "mob_template"}
_MOB_DEFINITION_KIND_ALIASES = {"mobdefinition", "mob-definition", "mob_definition"}
_SPAWN_PLAN_KIND_ALIASES = {"spawnplan", "spawn-plan", "spawn_plan"}
_QUEST_KIND_ALIASES = {quest_manifests.QUEST_MANIFEST_KIND}
_QUEST_ARC_KIND_ALIASES = {
    quest_manifests.QUEST_ARC_MANIFEST_KIND,
    "quest-arc",
    "quest_arc",
}
_TRIGGER_KIND_ALIASES = {builder_manifests.TRIGGER_MANIFEST_KIND}
_ABILITY_KIND_ALIASES = {builder_manifests.ABILITY_MANIFEST_KIND}
_ABILITIES_KIND_ALIASES = {builder_manifests.ABILITIES_MANIFEST_KIND}

_ROOM_REF_PREFIX = "room@"
_ZONE_REF_PREFIX = "zone@"
_PATH_REF_PREFIX = "path@"
_ITEM_REF_PREFIX = "itemtemplate."
_ITEM_DEFINITION_REF_PREFIX = "itemdefinition."
_ITEM_BUNDLE_REF_PREFIX = "itembundle."
_MERCHANT_PROFILE_REF_PREFIX = "merchantprofile."
_MOB_REF_PREFIX = "mobtemplate."
_MOB_DEFINITION_REF_PREFIX = "mobdefinition."

_ZONE_SORT_KEY = lambda zone: ((zone.name or "").lower(), zone.id)
_ROOM_SORT_KEY = lambda room: (room.z, room.y, room.x, room.id)

_MOB_TEMPLATE_SPEC_FIELDS = (
    "level",
    "description",
    "room_description",
    "keywords",
    "notes",
    "gold",
    "type",
    "archetype",
    "gender",
    "exp_worth",
    "roaming_type",
    "alignment",
    "aggression",
    "use_abilities",
    "roam_chance",
    "hit_msg_first",
    "hit_msg_third",
    "health_max",
    "health_regen",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
    "regen_rate",
    "attack_power",
    "ability_power",
    "crit",
    "resilience",
    "dodge",
    "armor",
    "drops_random_items",
    "num_items",
    "is_crafter",
    "load_specification",
    "chance_imbued",
    "chance_enchanted",
    "default_stats",
    "is_elite",
    "is_invisible",
    "fights_back",
    "craft_multiplier",
    "craft_enchanted",
    "combat_script",
    "traits",
    "is_upgrader",
    "upgrade_cost_multiplier",
    "upgrade_success_chance",
    "upgrade_success_cmd",
    "upgrade_failure_cmd",
    "merchant_profit",
)


class _WorldExportDumper(yaml.SafeDumper):
    pass


def _string_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_WorldExportDumper.add_representer(str, _string_representer)


def _manifest_to_yaml(manifest: dict[str, Any]) -> str:
    return yaml.dump(
        manifest,
        Dumper=_WorldExportDumper,
        sort_keys=False,
        default_flow_style=False,
    )


def manifest_stream_to_yaml(manifests: list[dict[str, Any]]) -> str:
    return yaml.dump_all(
        manifests,
        Dumper=_WorldExportDumper,
        sort_keys=False,
        default_flow_style=False,
        explicit_start=True,
    ).lstrip()


def parse_document_kind(manifest: dict[str, Any]) -> str:
    builder_manifests._validate_api_version(manifest)
    raw_kind = str(manifest.get("kind") or "").strip().lower()
    if raw_kind in _WORLD_KIND_ALIASES:
        return WORLD_MANIFEST_KIND
    if raw_kind in _CURRENCY_KIND_ALIASES:
        return CURRENCY_MANIFEST_KIND
    if raw_kind in _ZONE_KIND_ALIASES:
        return ZONE_MANIFEST_KIND
    if raw_kind in _ROOM_KIND_ALIASES:
        return ROOM_MANIFEST_KIND
    if raw_kind in _PATH_KIND_ALIASES:
        return PATH_MANIFEST_KIND
    if raw_kind in _ITEM_TEMPLATE_KIND_ALIASES:
        return builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND
    if raw_kind in _ITEM_DEFINITION_KIND_ALIASES:
        return builder_manifests.ITEM_DEFINITION_MANIFEST_KIND
    if raw_kind in _ITEM_BUNDLE_KIND_ALIASES:
        return builder_manifests.ITEM_BUNDLE_MANIFEST_KIND
    if raw_kind in _MERCHANT_PROFILE_KIND_ALIASES:
        return builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND
    if raw_kind in _MOB_TEMPLATE_KIND_ALIASES:
        return MOB_TEMPLATE_MANIFEST_KIND
    if raw_kind in _MOB_DEFINITION_KIND_ALIASES:
        return builder_manifests.MOB_DEFINITION_MANIFEST_KIND
    if raw_kind in _SPAWN_PLAN_KIND_ALIASES:
        return SPAWN_PLAN_MANIFEST_KIND
    if raw_kind in _QUEST_ARC_KIND_ALIASES:
        return quest_manifests.QUEST_ARC_MANIFEST_KIND
    if raw_kind in _QUEST_KIND_ALIASES:
        return quest_manifests.QUEST_MANIFEST_KIND
    if raw_kind in _TRIGGER_KIND_ALIASES:
        return builder_manifests.TRIGGER_MANIFEST_KIND
    if raw_kind in _ABILITY_KIND_ALIASES:
        return builder_manifests.ABILITY_MANIFEST_KIND
    if raw_kind in _ABILITIES_KIND_ALIASES:
        return builder_manifests.ABILITIES_MANIFEST_KIND
    raise serializers.ValidationError(
        "Unsupported manifest kind. Supported kinds: "
        "world, currency, zone, room, path, itemtemplate, itemdefinition, itembundle, merchantprofile, mobtemplate, mobdefinition, spawnplan, questarc, quest, trigger, ability, abilities."
    )


def _room_ref_from_coords(*, x: int, y: int, z: int) -> str:
    return f"{_ROOM_REF_PREFIX}{x},{y},{z}"


def _room_ref(room: Room | None) -> str:
    if room is None:
        return ""
    return _room_ref_from_coords(x=room.x, y=room.y, z=room.z)


def _zone_ref(zone: Zone | None) -> str:
    if zone is None:
        return ""
    relative_id = zone.relative_id or zone.id
    return f"{_ZONE_REF_PREFIX}{relative_id}"


def _path_ref(path: Path | None) -> str:
    if path is None:
        return ""
    relative_id = path.relative_id or path.id
    return f"{_PATH_REF_PREFIX}{relative_id}"


def _parse_room_ref(value: Any) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if not text.startswith(_ROOM_REF_PREFIX):
        raise serializers.ValidationError(
            "Room references must use the form 'room@x,y,z'."
        )
    raw_coords = text[len(_ROOM_REF_PREFIX):]
    parts = [part.strip() for part in raw_coords.split(",")]
    if len(parts) != 3:
        raise serializers.ValidationError(
            "Room references must use the form 'room@x,y,z'."
        )
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        raise serializers.ValidationError(
            "Room references must use integer coordinates in the form 'room@x,y,z'."
        )


def _parse_zone_ref(value: Any, *, field_name: str = "zone") -> int:
    text = str(value or "").strip()
    if not text.startswith(_ZONE_REF_PREFIX):
        raise serializers.ValidationError(
            f"{field_name} must use the portable form 'zone@<relative_id>'."
        )
    raw_relative_id = text[len(_ZONE_REF_PREFIX):].strip()
    if not raw_relative_id:
        raise serializers.ValidationError(
            f"{field_name} must use the portable form 'zone@<relative_id>'."
        )
    try:
        relative_id = int(raw_relative_id)
    except ValueError:
        raise serializers.ValidationError(
            f"{field_name} must use an integer relative id in the form 'zone@<relative_id>'."
        )
    if relative_id <= 0:
        raise serializers.ValidationError(f"{field_name} relative id must be positive.")
    return relative_id


def _parse_path_ref(value: Any, *, field_name: str = "path") -> int:
    text = str(value or "").strip()
    if not text.startswith(_PATH_REF_PREFIX):
        raise serializers.ValidationError(
            f"{field_name} must use the portable form 'path@<relative_id>'."
        )
    raw_relative_id = text[len(_PATH_REF_PREFIX):].strip()
    if not raw_relative_id:
        raise serializers.ValidationError(
            f"{field_name} must use the portable form 'path@<relative_id>'."
        )
    try:
        relative_id = int(raw_relative_id)
    except ValueError:
        raise serializers.ValidationError(
            f"{field_name} must use an integer relative id in the form 'path@<relative_id>'."
        )
    if relative_id <= 0:
        raise serializers.ValidationError(f"{field_name} relative id must be positive.")
    return relative_id


def _item_ref(item_template: ItemTemplate | None) -> str:
    if item_template is None or not item_template.slug:
        return ""
    return f"{_ITEM_REF_PREFIX}{item_template.slug}"


def _item_definition_ref(item_definition: ItemDefinition | None) -> str:
    if item_definition is None or not item_definition.slug:
        return ""
    return f"{_ITEM_DEFINITION_REF_PREFIX}{item_definition.slug}"


def _item_bundle_ref(item_bundle: ItemBundle | None) -> str:
    if item_bundle is None or not item_bundle.slug:
        return ""
    return f"{_ITEM_BUNDLE_REF_PREFIX}{item_bundle.slug}"


def _mob_ref(mob_template: MobTemplate | None) -> str:
    if mob_template is None or not mob_template.slug:
        return ""
    return f"{_MOB_REF_PREFIX}{mob_template.slug}"


def _mob_definition_ref(mob_definition: MobDefinition | None) -> str:
    if mob_definition is None or not mob_definition.slug:
        return ""
    return f"{_MOB_DEFINITION_REF_PREFIX}{mob_definition.slug}"


def _parse_template_slug_ref(value: Any, *, expected_prefix: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    prefix, sep, raw = text.partition(".")
    if sep != "." or f"{prefix}." != expected_prefix:
        raise serializers.ValidationError(
            f"{field_name} must use the form '{expected_prefix}<slug>'."
        )
    slug = str(raw or "").strip()
    if not slug:
        raise serializers.ValidationError(
            f"{field_name} must use the form '{expected_prefix}<slug>'."
        )
    return slug


def _slug_or_error(value: Any, field_name: str) -> str:
    slug = slugify(str(value or ""))
    if not slug:
        raise serializers.ValidationError(
            f"{field_name} must contain at least one slug-safe character."
        )
    return slug


def _parse_json_text(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _coerce_zone_state(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise serializers.ValidationError("spec.state must be a JSON object.")
        if isinstance(parsed, dict):
            return parsed
        raise serializers.ValidationError("spec.state must be a JSON object.")
    raise serializers.ValidationError("spec.state must be a JSON object.")


def _manifest_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")
    return metadata


def _manifest_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    return spec


def _serialize_currency_manifest(currency: Currency) -> dict[str, Any]:
    return {
        "kind": CURRENCY_MANIFEST_KIND,
        "metadata": {
            "code": currency.code,
        },
        "spec": {
            "name": currency.name,
            "is_default": bool(currency.is_default),
        },
    }


def _serialize_zone_manifest(zone: Zone) -> dict[str, Any]:
    return {
        "kind": ZONE_MANIFEST_KIND,
        "metadata": {
            "ref": _zone_ref(zone),
            "name": zone.name or "",
        },
        "spec": {
            "description": zone.description or "",
            "notes": zone.notes or "",
            "is_warzone": bool(zone.is_warzone),
            "state": get_state_snapshot(STATE_SCOPE_ZONE, zone),
            "respawn_wait": int(zone.respawn_wait),
            "pvp_zone": bool(zone.pvp_zone),
            "center": _room_ref(zone.center),
        },
    }


def _serialize_room_manifest(room: Room) -> dict[str, Any]:
    return {
        "kind": ROOM_MANIFEST_KIND,
        "metadata": {
            "ref": _room_ref(room),
            "name": room.name or "",
        },
        "spec": {
            "zone": _zone_ref(room.zone),
            "description": room.description or "",
            "note": room.note or "",
            "type": room.type,
            "color": room.color or "",
            "is_landmark": bool(room.is_landmark),
            "exits": {
                direction: _room_ref(getattr(room, direction, None))
                for direction in adv_consts.DIRECTIONS
            },
            "flags": sorted(room.flags.values_list("code", flat=True)),
            "details": [
                {
                    "keywords": detail.keywords,
                    "description": detail.description,
                    "is_hidden": bool(detail.is_hidden),
                }
                for detail in room.details.all().order_by("created_ts", "id")
            ],
            "doors": [
                {
                    "direction": door.direction,
                    "name": door.name or "",
                    "to_room": _room_ref(door.to_room),
                    "key": _item_ref(door.key),
                    "destroy_key": bool(door.destroy_key),
                    "default_state": door.default_state,
                }
                for door in room.doors_from.all().select_related("key", "to_room").order_by("direction", "id")
            ],
        },
    }


def _serialize_path_manifest(path: Path) -> dict[str, Any]:
    return {
        "kind": PATH_MANIFEST_KIND,
        "metadata": {
            "ref": _path_ref(path),
            "name": path.name or "",
        },
        "spec": {
            "zone": _zone_ref(path.zone),
            "notes": path.notes or "",
            "entry_room": _room_ref(path.entry_room),
            "max_per_room": path.max_per_room,
            "max_per_path": path.max_per_path,
            "rooms": [
                _room_ref(path_room.room)
                for path_room in PathRoom.objects.filter(path=path).select_related("room").order_by("id")
            ],
        },
    }


def _serialize_item_template_manifest(item_template: ItemTemplate) -> dict[str, Any]:
    manifest = builder_manifests.item_template_to_manifest(item_template)
    spec = copy.deepcopy(manifest["spec"])
    spec["inventory"] = [
        _serialize_template_inventory_entry(inventory)
        for inventory in item_template.template_inventories.select_related("item_template").all().order_by(
            "item_template__slug", "id"
        )
    ]
    return {
        "kind": builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND,
        "metadata": {
            "slug": item_template.slug,
            "name": item_template.name or "",
        },
        "spec": spec,
    }


def _serialize_item_definition_manifest(item_definition: ItemDefinition) -> dict[str, Any]:
    manifest = builder_manifests.item_definition_to_manifest(item_definition)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _serialize_template_inventory_entry(inventory) -> dict[str, Any]:
    entry = {
        "probability": int(inventory.probability),
        "num_copies": int(inventory.num_copies),
    }
    if getattr(inventory, "item_template_id", None):
        entry["item_template"] = _item_ref(inventory.item_template)
    elif getattr(inventory, "item_definition_id", None):
        entry["item_definition"] = _item_definition_ref(inventory.item_definition)
    elif getattr(inventory, "item_bundle_id", None):
        entry["item_bundle"] = _item_bundle_ref(inventory.item_bundle)
    return entry


def _serialize_item_bundle_manifest(item_bundle: ItemBundle) -> dict[str, Any]:
    manifest = builder_manifests.item_bundle_to_manifest(item_bundle)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    for entry in manifest["spec"]["entries"]:
        entry["item_definition"] = f"{_ITEM_DEFINITION_REF_PREFIX}{entry['item_definition']}"
    return manifest


def _serialize_merchant_profile_manifest(merchant_profile: MerchantProfile) -> dict[str, Any]:
    manifest = builder_manifests.merchant_profile_to_manifest(merchant_profile)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    for slot in manifest["spec"]["stock"]:
        if "item_definition" in slot:
            slot["item_definition"] = f"{_ITEM_DEFINITION_REF_PREFIX}{slot['item_definition']}"
        if "item_bundle" in slot:
            slot["item_bundle"] = f"{_ITEM_BUNDLE_REF_PREFIX}{slot['item_bundle']}"
    return manifest


def _serialize_mob_template_manifest(mob_template: MobTemplate) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    for field_name in _MOB_TEMPLATE_SPEC_FIELDS:
        value = getattr(mob_template, field_name)
        spec[field_name] = "" if value is None else value
    spec["inventory"] = [
        _serialize_template_inventory_entry(inventory)
        for inventory in mob_template.template_inventories.select_related(
            "item_template",
            "item_definition",
            "item_bundle",
        ).all().order_by(
            "item_template__slug", "id"
        )
    ]
    return {
        "kind": MOB_TEMPLATE_MANIFEST_KIND,
        "metadata": {
            "slug": mob_template.slug,
            "name": mob_template.name or "",
        },
        "spec": spec,
    }


def _serialize_mob_definition_manifest(mob_definition: MobDefinition) -> dict[str, Any]:
    manifest = builder_manifests.mob_definition_to_manifest(mob_definition)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _serialize_spawn_entry(entry: SpawnEntry) -> dict[str, Any]:
    data: dict[str, Any] = {
        "slug": entry.slug,
        "order": int(entry.order),
        "target": copy.deepcopy(entry.target),
        "count": copy.deepcopy(entry.count),
    }
    if entry.name:
        data["name"] = entry.name
    if not entry.is_active:
        data["is_active"] = False
    source = copy.deepcopy(entry.source)
    if isinstance(source, dict) and "pool" in source:
        data["source_pool"] = source["pool"]
    else:
        data["source"] = source
    if entry.placement:
        data["placement"] = copy.deepcopy(entry.placement)
    if entry.affixes:
        data["affixes"] = copy.deepcopy(entry.affixes)
    if entry.conditions:
        data["conditions"] = copy.deepcopy(entry.conditions)
    return data


def _serialize_spawn_plan_manifest(spawn_plan: SpawnPlan) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "zone": _zone_ref(spawn_plan.zone),
        "order": int(spawn_plan.order),
        "is_active": bool(spawn_plan.is_active),
        "reset": copy.deepcopy(spawn_plan.reset_policy),
        "respawn": copy.deepcopy(spawn_plan.respawn_policy),
        "entries": [
            _serialize_spawn_entry(entry)
            for entry in spawn_plan.entries.all().order_by("order", "created_ts", "id")
        ],
    }
    if spawn_plan.randomization:
        spec["randomization"] = copy.deepcopy(spawn_plan.randomization)
    if spawn_plan.conditions:
        spec["conditions"] = copy.deepcopy(spawn_plan.conditions)
    return {
        "kind": SPAWN_PLAN_MANIFEST_KIND,
        "metadata": {
            "slug": spawn_plan.slug,
            "name": spawn_plan.name or "",
        },
        "spec": spec,
    }


def serialize_spawn_plan_payload(
    spawn_plan: SpawnPlan,
    *,
    include_yaml: bool = True,
) -> dict[str, Any]:
    zone = spawn_plan.zone
    zone_ref = _zone_ref(zone)
    respawn_policy = copy.deepcopy(spawn_plan.respawn_policy or {})
    entry_count = spawn_plan.entries.count()
    payload: dict[str, Any] = {
        "id": spawn_plan.id,
        "key": f"spawnplan.{spawn_plan.id}",
        "slug": spawn_plan.slug,
        "name": spawn_plan.name or "",
        "modified_ts": spawn_plan.modified_ts,
        "model_type": "spawnplan",
        "zone": {
            "id": zone.id,
            "key": zone.key,
            "relative_id": zone.relative_id,
            "manifest_ref": zone_ref,
            "name": zone.name,
        },
        "zone_ref": zone_ref,
        "order": int(spawn_plan.order),
        "is_active": bool(spawn_plan.is_active),
        "num_entries": entry_count,
        "entry_count": entry_count,
        "entries": entry_count,
        "respawn_mode": respawn_policy.get("mode", ""),
        "respawn_seconds": respawn_policy.get("seconds"),
    }
    if include_yaml:
        manifest = _serialize_spawn_plan_manifest(spawn_plan)
        delete_manifest = {
            "kind": SPAWN_PLAN_MANIFEST_KIND,
            "operation": builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE,
            "metadata": {
                "slug": spawn_plan.slug,
            },
        }
        payload["manifest"] = manifest
        payload["yaml"] = _manifest_to_yaml(manifest)
        payload["delete_yaml"] = _manifest_to_yaml(delete_manifest)
    return payload


def _serialize_ability_manifest(ability: AbilityDefinition) -> dict[str, Any]:
    manifest = builder_manifests.ability_to_manifest(ability)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _canonicalize_template_ref(
    value: Any,
    *,
    world: World,
    expected_type: str,
) -> Any:
    if value in (None, "", [], {}):
        return value
    if quest_entity_refs.is_dynamic_reference(value):
        return value

    template_id = quest_entity_refs.resolve_template_ref_id(
        world=world,
        value=value,
        expected_type=expected_type,
    )
    if template_id is None:
        return value

    model_cls = ItemTemplate if expected_type == "itemtemplate" else MobTemplate
    template = model_cls.objects.filter(world=world, pk=template_id).first()
    if not template or not template.slug:
        return value
    return f"{expected_type}.{template.slug}"


def _canonicalize_room_ref(value: Any, *, world: World) -> Any:
    if value in (None, "", [], {}):
        return value
    if quest_entity_refs.is_dynamic_reference(value):
        return value

    room_id = quest_entity_refs.resolve_room_ref_id(world=world, value=value)
    if room_id is None:
        return value

    room = Room.objects.filter(world=world, pk=room_id).first()
    if room is None:
        return value
    return _room_ref(room)


def _canonicalize_condition_refs(
    condition: Any,
    *,
    world: World,
    event_target_is_room: bool = False,
) -> Any:
    if condition in (None, {}, []):
        return condition
    if isinstance(condition, list):
        return [
            _canonicalize_condition_refs(
                item,
                world=world,
                event_target_is_room=event_target_is_room,
            )
            for item in condition
        ]
    if not isinstance(condition, dict):
        return condition

    canonical = copy.deepcopy(condition)

    if "all" in canonical:
        child_conditions = canonical.get("all")
        canonical["all"] = _canonicalize_condition_refs(
            child_conditions,
            world=world,
            event_target_is_room=event_target_is_room or quest_manifests._condition_list_targets_room(child_conditions),
        )
    if "any" in canonical:
        canonical["any"] = _canonicalize_condition_refs(
            canonical.get("any"),
            world=world,
            event_target_is_room=event_target_is_room,
        )
    if "not" in canonical:
        canonical["not"] = _canonicalize_condition_refs(
            canonical.get("not"),
            world=world,
            event_target_is_room=event_target_is_room,
        )

    for operator in ("eq", "ne", "gte", "lte", "in"):
        raw_args = canonical.get(operator)
        if not isinstance(raw_args, list) or len(raw_args) != 2:
            continue

        left_path = raw_args[0]
        right_value = raw_args[1]
        uses_room_ref = quest_manifests._condition_uses_room_ref(
            left_path,
            right_value,
            event_target_is_room=event_target_is_room,
        ) or quest_manifests._condition_uses_room_ref(
            left_path,
            None,
            event_target_is_room=event_target_is_room,
        )
        if not uses_room_ref and operator == "in" and isinstance(right_value, list):
            uses_room_ref = any(
                quest_manifests._condition_uses_room_ref(
                    left_path,
                    candidate,
                    event_target_is_room=event_target_is_room,
                )
                for candidate in right_value
            )
        if uses_room_ref:
            if operator == "in" and isinstance(right_value, list):
                canonical[operator] = [
                    left_path,
                    [
                        _canonicalize_room_ref(candidate, world=world)
                        for candidate in right_value
                    ],
                ]
            else:
                canonical[operator] = [
                    left_path,
                    _canonicalize_room_ref(right_value, world=world),
                ]
            continue

        expected_type = quest_manifests._condition_expected_template_type(left_path, right_value)
        if expected_type is None:
            expected_type = quest_manifests._condition_expected_template_type(left_path)
        if not expected_type:
            continue

        if operator == "in" and isinstance(right_value, list):
            canonical[operator] = [
                left_path,
                [
                    _canonicalize_template_ref(candidate, world=world, expected_type=expected_type)
                    for candidate in right_value
                ],
            ]
        else:
            canonical[operator] = [
                left_path,
                _canonicalize_template_ref(right_value, world=world, expected_type=expected_type),
            ]

    return canonical


def _canonicalize_quest_node(node: Any, *, world: World) -> Any:
    if isinstance(node, list):
        return [_canonicalize_quest_node(item, world=world) for item in node]

    if not isinstance(node, dict):
        return node

    if any(key in node for key in ("all", "any", "not", "eq", "ne", "gte", "lte", "in")):
        return _canonicalize_condition_refs(node, world=world)

    canonical = {}
    for key, value in node.items():
        if key in {"room", "room_id"} and (
            str(node.get("type") or "").strip().lower() == "room_prompt"
            or "item_template" in node
            or "item_template_id" in node
        ):
            canonical["room"] = _canonicalize_room_ref(value, world=world)
            continue
        if key in {"mob_template", "mob_template_id"}:
            canonical["mob_template"] = _canonicalize_template_ref(
                value,
                world=world,
                expected_type="mobtemplate",
            )
            continue
        if key in {"item_template", "item_template_id"}:
            canonical["item_template"] = _canonicalize_template_ref(
                value,
                world=world,
                expected_type="itemtemplate",
            )
            continue
        if key in {"entity", "value"} and isinstance(value, str):
            prefix, sep, _ = value.strip().partition(".")
            expected_type = quest_entity_refs.canonical_template_type(prefix) if sep == "." else None
            if expected_type:
                canonical[key] = _canonicalize_template_ref(
                    value,
                    world=world,
                    expected_type=expected_type,
                )
                continue
        canonical[key] = _canonicalize_quest_node(value, world=world)
    return canonical


def _serialize_quest_arc_manifest(quest_arc: QuestArcTemplate) -> dict[str, Any]:
    manifest = quest_manifests.quest_arc_template_to_manifest(quest_arc)
    return {
        "kind": quest_manifests.QUEST_ARC_MANIFEST_KIND,
        "metadata": {
            "slug": quest_arc.slug,
            "name": quest_arc.name,
        },
        "spec": copy.deepcopy(manifest["spec"]),
    }


def _serialize_quest_manifest(quest: QuestTemplate, *, world: World) -> dict[str, Any]:
    manifest = quest_manifests.quest_template_to_manifest(quest)
    spec = _canonicalize_quest_node(copy.deepcopy(manifest["spec"]), world=world)
    spec = quest_manifests.QuestSpec.model_validate(spec).model_dump()
    return {
        "kind": quest_manifests.QUEST_MANIFEST_KIND,
        "metadata": {
            "slug": quest.slug,
            "name": quest.name,
        },
        "spec": spec,
    }


def _serialize_trigger_target(trigger: Trigger) -> dict[str, Any]:
    if not trigger.target_type_id or not trigger.target_id:
        return {"type": "world", "ref": "world"}

    target_model = trigger.target_type.model_class()
    if target_model == Room:
        return {"type": "room", "ref": _room_ref(trigger.target)}
    if target_model == Zone:
        return {"type": "zone", "ref": trigger.target.name if trigger.target else ""}
    if target_model == World:
        return {"type": "world", "ref": "world"}
    if target_model == MobTemplate:
        return {"type": "mobtemplate", "ref": _mob_ref(trigger.target)}
    return {"type": trigger.target_type.model, "ref": ""}


def _serialize_trigger_manifest(trigger: Trigger) -> dict[str, Any]:
    return {
        "kind": builder_manifests.TRIGGER_MANIFEST_KIND,
        "metadata": {
            "name": trigger.name or "",
        },
        "spec": {
            "scope": trigger.scope,
            "kind": builder_manifests._canonical_trigger_kind(trigger.kind),
            "target": _serialize_trigger_target(trigger),
            "match": trigger.match or "",
            "script": trigger.script or "",
            "conditions": trigger.conditions or "",
            "event": trigger.event or "",
            "show_details_on_failure": bool(trigger.show_details_on_failure),
            "failure_message": trigger.failure_message or "",
            "display_action_in_room": bool(trigger.display_action_in_room),
            "gate_delay": int(trigger.gate_delay),
            "order": int(trigger.order),
            "is_active": bool(trigger.is_active),
        },
    }


def _serialize_world_manifest(world: World) -> dict[str, Any]:
    return builder_manifests.world_config_to_manifest(
        world=world,
        manifest_kind=WORLD_MANIFEST_KIND,
        include_metadata=False,
        room_reference_mode="coords",
    )


def serialize_world_documents(world: World) -> list[dict[str, Any]]:
    if not world.config:
        raise serializers.ValidationError("World has no config to export.")

    return [
        *[
            _serialize_currency_manifest(currency)
            for currency in world.currencies.all().order_by("code", "id")
        ],
        *[
            _serialize_item_template_manifest(item_template)
            for item_template in world.item_templates.prefetch_related(
                "template_inventories__item_template",
            ).order_by("slug", "id")
        ],
        *[
            _serialize_item_definition_manifest(item_definition)
            for item_definition in world.item_definitions.all().order_by("slug", "id")
        ],
        *[
            _serialize_item_bundle_manifest(item_bundle)
            for item_bundle in world.item_bundles.prefetch_related(
                "entries__item_definition",
            ).order_by("slug", "id")
        ],
        *[
            _serialize_merchant_profile_manifest(merchant_profile)
            for merchant_profile in world.merchant_profiles.prefetch_related(
                "stock_slots__item_definition",
                "stock_slots__item_bundle",
            ).select_related("funds_currency").order_by("slug", "id")
        ],
        *[
            _serialize_zone_manifest(zone)
            for zone in world.zones.all().order_by("name", "id")
        ],
        *[
            _serialize_room_manifest(room)
            for room in world.rooms.prefetch_related(
                "flags",
                "details",
                "doors_from__key",
                "doors_from__to_room",
            ).select_related(
                "zone",
                "north",
                "east",
                "south",
                "west",
                "up",
                "down",
            ).order_by("z", "y", "x", "id")
        ],
        *[
            _serialize_path_manifest(path)
            for path in world.paths.select_related("zone", "entry_room").order_by(
                "zone__name", "relative_id", "id"
            )
        ],
        *[
            _serialize_mob_template_manifest(mob_template)
            for mob_template in world.mob_templates.prefetch_related(
                "template_inventories__item_template",
                "template_inventories__item_definition",
                "template_inventories__item_bundle",
            ).order_by("slug", "id")
        ],
        *[
            _serialize_mob_definition_manifest(mob_definition)
            for mob_definition in world.mob_definitions.all().order_by("slug", "id")
        ],
        *[
            _serialize_spawn_plan_manifest(spawn_plan)
            for spawn_plan in world.spawn_plans.prefetch_related("entries").select_related("zone").order_by(
                "zone__name", "order", "slug", "id"
            )
        ],
        *[
            _serialize_ability_manifest(ability)
            for ability in world.ability_definitions.all().order_by("slug", "id")
        ],
        *[
            _serialize_quest_arc_manifest(quest_arc)
            for quest_arc in world.quest_arc_templates.all().order_by("slug", "id")
        ],
        *[
            _serialize_quest_manifest(quest, world=world)
            for quest in world.quest_templates.select_related("arc").all().order_by("slug", "id")
        ],
        *[
            _serialize_trigger_manifest(trigger)
            for trigger in world.triggers.select_related("target_type").all().order_by(
                "scope", "order", "created_ts", "id"
            )
        ],
        _serialize_world_manifest(world),
    ]


def _summarize_documents(documents: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "documents": len(documents),
        "currencies": 0,
        "zones": 0,
        "rooms": 0,
        "paths": 0,
        "item_templates": 0,
        "item_definitions": 0,
        "item_bundles": 0,
        "merchant_profiles": 0,
        "mob_templates": 0,
        "mob_definitions": 0,
        "spawn_plans": 0,
        "abilities": 0,
        "quest_arcs": 0,
        "quests": 0,
        "triggers": 0,
    }
    for document in documents:
        kind = parse_document_kind(document)
        if kind == CURRENCY_MANIFEST_KIND:
            counts["currencies"] += 1
        elif kind == ZONE_MANIFEST_KIND:
            counts["zones"] += 1
        elif kind == ROOM_MANIFEST_KIND:
            counts["rooms"] += 1
        elif kind == PATH_MANIFEST_KIND:
            counts["paths"] += 1
        elif kind == builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND:
            counts["item_templates"] += 1
        elif kind == builder_manifests.ITEM_DEFINITION_MANIFEST_KIND:
            counts["item_definitions"] += 1
        elif kind == builder_manifests.ITEM_BUNDLE_MANIFEST_KIND:
            counts["item_bundles"] += 1
        elif kind == builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND:
            counts["merchant_profiles"] += 1
        elif kind == MOB_TEMPLATE_MANIFEST_KIND:
            counts["mob_templates"] += 1
        elif kind == builder_manifests.MOB_DEFINITION_MANIFEST_KIND:
            counts["mob_definitions"] += 1
        elif kind == SPAWN_PLAN_MANIFEST_KIND:
            counts["spawn_plans"] += 1
        elif kind == builder_manifests.ABILITY_MANIFEST_KIND:
            counts["abilities"] += 1
        elif kind == quest_manifests.QUEST_ARC_MANIFEST_KIND:
            counts["quest_arcs"] += 1
        elif kind == quest_manifests.QUEST_MANIFEST_KIND:
            counts["quests"] += 1
        elif kind == builder_manifests.TRIGGER_MANIFEST_KIND:
            counts["triggers"] += 1
    return counts


def serialize_world_export_payload(world: World) -> dict[str, Any]:
    documents = serialize_world_documents(world)
    return {
        "documents": documents,
        "yaml": manifest_stream_to_yaml(documents),
        "summary": _summarize_documents(documents),
    }


def _find_placeholder_zone(world: World) -> Zone | None:
    if world.zones.count() != 1:
        return None
    zone = world.zones.order_by("id").first()
    if not zone:
        return None
    if zone.name != "Starting Zone":
        return None
    if zone.description or zone.notes:
        return None
    if zone.is_warzone or zone.pvp_zone:
        return None
    if int(zone.respawn_wait) != 300:
        return None
    if get_state_snapshot(STATE_SCOPE_ZONE, zone):
        return None
    return zone


def _find_placeholder_room(world: World) -> Room | None:
    if world.rooms.count() != 1:
        return None
    room = world.rooms.order_by("id").first()
    if not room:
        return None
    if room.name != "Starting Room":
        return None
    if (room.x, room.y, room.z) != (0, 0, 0):
        return None
    if room.description or room.note or room.color or room.is_landmark:
        return None
    if any(getattr(room, direction + "_id") for direction in adv_consts.DIRECTIONS):
        return None
    if room.flags.exists() or room.details.exists() or room.doors_from.exists():
        return None
    config = world.config
    if config and (
        config.starting_room_id != room.id or config.death_room_id != room.id
    ):
        return None
    return room


def _get_or_create_zone(*, world: World, zone_name: str, zone_ref: Any = None) -> Zone | None:
    name = str(zone_name or "").strip()
    ref_text = str(zone_ref or "").strip()
    if not name and not ref_text:
        return None
    if ref_text:
        relative_id = _parse_zone_ref(ref_text, field_name="zone")
        zone = Zone.objects.filter(world=world, relative_id=relative_id).first()
        if zone:
            return zone
        placeholder = _find_placeholder_zone(world)
        if placeholder:
            placeholder.name = name or placeholder.name
            placeholder.relative_id = relative_id
            placeholder.save(update_fields=["name", "relative_id"])
            return placeholder
        zone = Zone.objects.create(world=world, name=name or f"Zone {relative_id}")
        if zone.relative_id != relative_id:
            zone.relative_id = relative_id
            zone.save(update_fields=["relative_id"])
        return zone
    zone = Zone.objects.filter(world=world, name=name).order_by("id").first()
    if zone:
        return zone
    placeholder = _find_placeholder_zone(world)
    if placeholder:
        placeholder.name = name
        placeholder.save(update_fields=["name"])
        return placeholder
    return Zone.objects.create(world=world, name=name)


def _get_or_create_room(*, world: World, room_ref: Any, zone: Zone | None = None) -> Room:
    x, y, z = _parse_room_ref(room_ref)
    room = Room.objects.filter(world=world, x=x, y=y, z=z).first()
    if room:
        if zone is not None and room.zone_id != zone.id:
            room.zone = zone
            room.save(update_fields=["zone"])
        return room

    placeholder = _find_placeholder_room(world)
    if placeholder:
        placeholder.x = x
        placeholder.y = y
        placeholder.z = z
        if zone is not None:
            placeholder.zone = zone
            placeholder.save(update_fields=["x", "y", "z", "zone"])
        else:
            placeholder.save(update_fields=["x", "y", "z"])
        return placeholder

    return Room.objects.create(
        world=world,
        zone=zone,
        name="Untitled Room",
        x=x,
        y=y,
        z=z,
    )


def _get_or_create_item_template(*, world: World, value: Any, field_name: str) -> ItemTemplate:
    template_id = quest_entity_refs.resolve_template_ref_id(
        world=world,
        value=value,
        expected_type="itemtemplate",
    )
    if template_id is not None:
        item_template = ItemTemplate.objects.filter(world=world, pk=template_id).first()
        if item_template:
            return item_template

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        raise serializers.ValidationError(f"{field_name} references an unknown item template.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itemtemplate", "item_template"}:
            raise serializers.ValidationError(
                f"{field_name} must reference an item template slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    item_template = ItemTemplate.objects.filter(world=world, slug=slug).first()
    if item_template:
        return item_template

    manifest = {
        "kind": builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND,
        "metadata": {
            "slug": slug,
            "name": slug.replace("-", " ").title(),
        },
        "spec": {},
    }
    parsed = builder_manifests.parse_item_template_manifest(world=world, manifest=manifest)
    return builder_manifests.apply_item_template_manifest(parsed)


def _get_item_definition(*, world: World, value: Any, field_name: str) -> ItemDefinition:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        item_definition = ItemDefinition.objects.filter(world=world, pk=int(text)).first()
        if item_definition:
            return item_definition
        raise serializers.ValidationError(f"{field_name} references an unknown item definition.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itemdefinition", "item_definition"}:
            raise serializers.ValidationError(
                f"{field_name} must reference an item definition slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    item_definition = ItemDefinition.objects.filter(world=world, slug=slug).first()
    if item_definition:
        return item_definition
    raise serializers.ValidationError(f"{field_name} references an unknown item definition.")


def _get_item_bundle(*, world: World, value: Any, field_name: str) -> ItemBundle:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        item_bundle = ItemBundle.objects.filter(world=world, pk=int(text)).first()
        if item_bundle:
            return item_bundle
        raise serializers.ValidationError(f"{field_name} references an unknown item bundle.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itembundle", "item_bundle"}:
            raise serializers.ValidationError(
                f"{field_name} must reference an item bundle slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    item_bundle = ItemBundle.objects.filter(world=world, slug=slug).first()
    if item_bundle:
        return item_bundle
    raise serializers.ValidationError(f"{field_name} references an unknown item bundle.")


def _get_or_create_mob_template(*, world: World, value: Any, field_name: str) -> MobTemplate:
    template_id = quest_entity_refs.resolve_template_ref_id(
        world=world,
        value=value,
        expected_type="mobtemplate",
    )
    if template_id is not None:
        mob_template = MobTemplate.objects.filter(world=world, pk=template_id).first()
        if mob_template:
            return mob_template

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        raise serializers.ValidationError(f"{field_name} references an unknown mob template.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"mobtemplate", "mob_template"}:
            raise serializers.ValidationError(
                f"{field_name} must reference a mob template slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    mob_template = MobTemplate.objects.filter(world=world, slug=slug).first()
    if mob_template:
        return mob_template

    serializer = builder_serializers.MobTemplateSerializer(
        data={
            "slug": slug,
            "name": slug.replace("-", " ").title(),
        },
        context={"world": world},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.save(world=world)


def _resolve_spawn_plan_zone(*, world: World, value: Any, field_name: str) -> Zone:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    relative_id = _parse_zone_ref(text, field_name=field_name)
    zone = Zone.objects.filter(world=world, relative_id=relative_id).first()
    if zone is None:
        raise serializers.ValidationError(f"{field_name} references an unknown zone.")
    return zone


def _resolve_spawn_plan_room(*, world: World, value: Any, field_name: str) -> Room:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.startswith(_ROOM_REF_PREFIX):
        x, y, z = _parse_room_ref(text)
        room = Room.objects.filter(world=world, x=x, y=y, z=z).first()
    else:
        prefix, sep, raw = text.partition(".")
        if sep == "." and prefix == "room" and raw.isdigit():
            room = Room.objects.filter(world=world, pk=int(raw)).first()
        else:
            room = Room.objects.filter(world=world, name=text).order_by("id").first()
    if room is None:
        raise serializers.ValidationError(f"{field_name} references an unknown room.")
    return room


def _resolve_spawn_plan_path(*, world: World, value: Any, field_name: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    relative_id = _parse_path_ref(text, field_name=field_name)
    path = Path.objects.filter(world=world, relative_id=relative_id).first()
    if path is None:
        raise serializers.ValidationError(f"{field_name} references an unknown path.")
    return path


def _validate_condition_payload_or_error(value: Any, *, field_name: str) -> None:
    try:
        validate_condition_payload(value, field_name=field_name)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))


def _normalize_spawn_source(entry_spec: dict[str, Any], *, field_name: str) -> Any:
    has_source = "source" in entry_spec
    has_source_pool = "source_pool" in entry_spec
    if has_source and has_source_pool:
        raise serializers.ValidationError(f"{field_name} must specify either source or source_pool, not both.")
    if has_source_pool:
        source_pool = entry_spec.get("source_pool")
        if not isinstance(source_pool, list) or not source_pool:
            raise serializers.ValidationError(f"{field_name}.source_pool must be a non-empty list.")
        normalized_pool = []
        for index, raw_entry in enumerate(source_pool):
            if isinstance(raw_entry, str):
                normalized_pool.append(raw_entry)
                continue
            if not isinstance(raw_entry, dict):
                raise serializers.ValidationError(
                    f"{field_name}.source_pool[{index}] must be a source ref or mapping."
                )
            source_ref = raw_entry.get("ref") or raw_entry.get("source")
            if not source_ref:
                raise serializers.ValidationError(
                    f"{field_name}.source_pool[{index}] must include ref or source."
                )
            normalized_entry = copy.deepcopy(raw_entry)
            if "weight" in normalized_entry:
                try:
                    weight = int(normalized_entry.get("weight"))
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        f"{field_name}.source_pool[{index}].weight must be an integer."
                    )
                if weight < 0:
                    raise serializers.ValidationError(
                        f"{field_name}.source_pool[{index}].weight cannot be negative."
                    )
                normalized_entry["weight"] = weight
            normalized_pool.append(normalized_entry)
        return {"pool": normalized_pool}
    source = entry_spec.get("source")
    if not source:
        raise serializers.ValidationError(f"{field_name}.source is required.")
    return copy.deepcopy(source)


def _spawn_source_refs(source: Any) -> list[Any]:
    if isinstance(source, dict) and "pool" in source:
        refs = []
        for entry in source.get("pool") or []:
            if isinstance(entry, dict):
                refs.append(entry.get("ref") or entry.get("source"))
            else:
                refs.append(entry)
        return refs
    if isinstance(source, dict):
        return [source.get("ref") or source.get("source")]
    return [source]


def _validate_spawn_source(*, world: World, source: Any, field_name: str) -> None:
    from spawns.spawn_plans import resolve_source

    for index, source_ref in enumerate(_spawn_source_refs(source)):
        ref_field = f"{field_name}[{index}]" if isinstance(source, dict) and "pool" in source else field_name
        resolve_source(world=world, source_spec=source_ref, field_name=ref_field)


def _normalize_spawn_count(value: Any, *, field_name: str) -> Any:
    if value in (None, ""):
        return 1
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be an integer or a min/max mapping.")
    if isinstance(value, int):
        if value < 0:
            raise serializers.ValidationError(f"{field_name} cannot be negative.")
        return value
    if isinstance(value, str):
        try:
            count = int(value)
        except ValueError:
            raise serializers.ValidationError(f"{field_name} must be an integer.")
        if count < 0:
            raise serializers.ValidationError(f"{field_name} cannot be negative.")
        return count
    if isinstance(value, dict):
        normalized = copy.deepcopy(value)
        if not any(key in normalized for key in ("value", "min", "max")):
            raise serializers.ValidationError(
                f"{field_name} must include value or min/max."
            )
        if "value" in normalized:
            try:
                normalized["value"] = int(normalized.get("value") or 0)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"{field_name}.value must be an integer.")
            if normalized["value"] < 0:
                raise serializers.ValidationError(f"{field_name}.value cannot be negative.")
        if "min" in normalized or "max" in normalized:
            try:
                minimum = int(normalized.get("min", 0) or 0)
                maximum = int(normalized.get("max", minimum) or minimum)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"{field_name}.min and max must be integers.")
            if minimum < 0 or maximum < 0:
                raise serializers.ValidationError(f"{field_name}.min and max cannot be negative.")
            if minimum > maximum:
                raise serializers.ValidationError(f"{field_name}.min cannot be greater than max.")
            normalized["min"] = minimum
            normalized["max"] = maximum
        return normalized
    raise serializers.ValidationError(f"{field_name} must be an integer or a min/max mapping.")


def _normalize_spawn_affixes(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")
    normalized = copy.deepcopy(value)
    if "guaranteed" in normalized:
        guaranteed = normalized.get("guaranteed") or []
        if not isinstance(guaranteed, list):
            raise serializers.ValidationError(f"{field_name}.guaranteed must be a list.")
        normalized["guaranteed"] = [str(key).strip() for key in guaranteed if str(key).strip()]
    if "chance" in normalized:
        try:
            chance = int(normalized.get("chance") or 0)
        except (TypeError, ValueError):
            raise serializers.ValidationError(f"{field_name}.chance must be an integer.")
        if chance < 0 or chance > 100:
            raise serializers.ValidationError(f"{field_name}.chance must be between 0 and 100.")
        normalized["chance"] = chance
    if "pool" in normalized:
        pool = normalized.get("pool") or []
        if not isinstance(pool, list):
            raise serializers.ValidationError(f"{field_name}.pool must be a list.")
        normalized_pool = []
        for index, option in enumerate(pool):
            if not isinstance(option, dict):
                raise serializers.ValidationError(f"{field_name}.pool[{index}] must be a mapping.")
            key = str(option.get("key") or "").strip()
            if not key:
                raise serializers.ValidationError(f"{field_name}.pool[{index}].key is required.")
            normalized_option = copy.deepcopy(option)
            normalized_option["key"] = key
            if "weight" in normalized_option:
                try:
                    weight = int(normalized_option.get("weight") or 0)
                except (TypeError, ValueError):
                    raise serializers.ValidationError(f"{field_name}.pool[{index}].weight must be an integer.")
                if weight < 0:
                    raise serializers.ValidationError(f"{field_name}.pool[{index}].weight cannot be negative.")
                normalized_option["weight"] = weight
            if "modifiers" in normalized_option and not isinstance(normalized_option.get("modifiers"), dict):
                raise serializers.ValidationError(f"{field_name}.pool[{index}].modifiers must be a mapping.")
            normalized_pool.append(normalized_option)
        normalized["pool"] = normalized_pool
    return normalized


def _validate_spawn_target(*, world: World, target: Any, entry_slugs: set[str], field_name: str) -> dict[str, Any]:
    if isinstance(target, str):
        target = {"room": target}
    if not isinstance(target, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")
    normalized = copy.deepcopy(target)
    if normalized.get("room"):
        _resolve_spawn_plan_room(world=world, value=normalized.get("room"), field_name=f"{field_name}.room")
        return normalized
    if normalized.get("room_ref"):
        _resolve_spawn_plan_room(world=world, value=normalized.get("room_ref"), field_name=f"{field_name}.room_ref")
        return normalized
    if normalized.get("zone"):
        _resolve_spawn_plan_zone(world=world, value=normalized.get("zone"), field_name=f"{field_name}.zone")
        return normalized
    if normalized.get("path"):
        _resolve_spawn_plan_path(world=world, value=normalized.get("path"), field_name=f"{field_name}.path")
        return normalized
    entry_slug = str(normalized.get("entry") or normalized.get("parent_entry") or "").strip()
    if entry_slug:
        if entry_slug not in entry_slugs:
            raise serializers.ValidationError(f"{field_name}.entry references an unknown entry slug.")
        return normalized
    raise serializers.ValidationError(f"{field_name} must target a room, zone, path, or entry.")


def apply_currency_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Currency, bool]:
    if parse_document_kind(manifest) != CURRENCY_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'currency'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Currency manifests only support operation 'apply'.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    code = str(metadata.get("code") or "").strip()
    if not code:
        raise serializers.ValidationError("metadata.code is required.")

    existing = Currency.objects.filter(world=world, code=code).first()
    name = str(spec.get("name", existing.name if existing else "")).strip()
    if not name:
        raise serializers.ValidationError("spec.name is required.")

    with transaction.atomic():
        currency, created = Currency.objects.update_or_create(
            world=world,
            code=code,
            defaults={
                "name": name,
                "is_default": bool(spec.get("is_default", existing.is_default if existing else False)),
            },
        )
        if currency.is_default:
            Currency.objects.filter(
                world=world,
                is_default=True,
            ).exclude(pk=currency.pk).update(is_default=False)
    return currency, created


def apply_zone_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Zone, bool]:
    if parse_document_kind(manifest) != ZONE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'zone'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Zone manifests only support operation 'apply'.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    ref = str(metadata.get("ref") or "").strip()
    name = str(metadata.get("name") or "").strip()
    if not name:
        raise serializers.ValidationError("metadata.name is required.")

    if ref:
        relative_id = _parse_zone_ref(ref, field_name="metadata.ref")
        existing = Zone.objects.filter(world=world, relative_id=relative_id).first()
    else:
        existing = Zone.objects.filter(world=world, name=name).order_by("id").first()
    created = existing is None

    with transaction.atomic():
        zone = existing or _get_or_create_zone(world=world, zone_name=name, zone_ref=ref)
        zone.name = name
        if "description" in spec or created:
            zone.description = str(spec.get("description", zone.description or ""))
        if "notes" in spec or created:
            zone.notes = str(spec.get("notes", zone.notes or ""))
        if "is_warzone" in spec or created:
            zone.is_warzone = bool(spec.get("is_warzone", zone.is_warzone if existing else False))
        if "respawn_wait" in spec or created:
            zone.respawn_wait = int(spec.get("respawn_wait", zone.respawn_wait if existing else 300))
        if "pvp_zone" in spec or created:
            zone.pvp_zone = bool(spec.get("pvp_zone", zone.pvp_zone if existing else False))
        zone.save()

        if "state" in spec or "zone_data" in spec or created:
            replace_state_snapshot(
                STATE_SCOPE_ZONE,
                zone,
                _coerce_zone_state(
                    spec.get(
                        "state",
                        spec.get(
                            "zone_data",
                            get_state_snapshot(STATE_SCOPE_ZONE, zone) if existing else {},
                        ),
                    )
                ),
            )

        if "center" in spec:
            center_ref = str(spec.get("center") or "").strip()
            zone.center = _get_or_create_room(world=world, room_ref=center_ref, zone=zone) if center_ref else None
            zone.save(update_fields=["center"])

    return zone, created


def apply_room_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Room, bool]:
    if parse_document_kind(manifest) != ROOM_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'room'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Room manifests only support operation 'apply'.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    room_ref = str(metadata.get("ref") or "").strip()
    if not room_ref:
        raise serializers.ValidationError("metadata.ref is required.")
    room_name = str(metadata.get("name") or "").strip()

    x, y, z = _parse_room_ref(room_ref)
    existing = Room.objects.filter(world=world, x=x, y=y, z=z).first()
    created = existing is None

    with transaction.atomic():
        zone_ref = str(spec.get("zone") or "").strip() if "zone" in spec else (
            _zone_ref(existing.zone) if existing and existing.zone else ""
        )
        zone = None
        if zone_ref:
            if zone_ref.startswith(_ZONE_REF_PREFIX):
                zone = _get_or_create_zone(world=world, zone_name="", zone_ref=zone_ref)
            else:
                zone = _get_or_create_zone(world=world, zone_name=zone_ref)

        room = existing or _get_or_create_room(world=world, room_ref=room_ref, zone=zone)
        if zone is not None or "zone" in spec:
            room.zone = zone
        room.name = room_name or room.name or "Untitled Room"

        if "description" in spec or created:
            room.description = str(spec.get("description", room.description or ""))
        if "note" in spec or created:
            room.note = str(spec.get("note", room.note or ""))
        if "type" in spec or created:
            room.type = builder_manifests._coerce_choice(
                spec.get("type", room.type or adv_consts.ROOM_TYPE_INDOOR),
                choices=adv_consts.ROOM_TYPES,
                field_name="spec.type",
            )
        if "color" in spec or created:
            room.color = str(spec.get("color", room.color or ""))
        if "is_landmark" in spec or created:
            room.is_landmark = bool(spec.get("is_landmark", room.is_landmark if existing else False))

        room.x = x
        room.y = y
        room.z = z
        room.save()

        exits = spec.get("exits")
        if exits is not None:
            if not isinstance(exits, dict):
                raise serializers.ValidationError("spec.exits must be a mapping.")
            update_fields = []
            for direction in adv_consts.DIRECTIONS:
                if direction not in exits:
                    continue
                exit_ref = str(exits.get(direction) or "").strip()
                exit_room = _get_or_create_room(world=world, room_ref=exit_ref) if exit_ref else None
                setattr(room, direction, exit_room)
                update_fields.append(direction)
            if update_fields:
                room.save(update_fields=update_fields)

        if "flags" in spec:
            flags = spec.get("flags") or []
            if not isinstance(flags, list):
                raise serializers.ValidationError("spec.flags must be a list.")
            RoomFlag.objects.filter(room=room).delete()
            for flag in flags:
                flag_code = builder_manifests._coerce_choice(
                    flag,
                    choices=adv_consts.ROOM_FLAGS,
                    field_name="spec.flags",
                )
                RoomFlag.objects.get_or_create(room=room, code=flag_code)

        if "details" in spec:
            details = spec.get("details") or []
            if not isinstance(details, list):
                raise serializers.ValidationError("spec.details must be a list.")
            RoomDetail.objects.filter(room=room).delete()
            for detail in details:
                if not isinstance(detail, dict):
                    raise serializers.ValidationError("spec.details entries must be mappings.")
                RoomDetail.objects.create(
                    room=room,
                    keywords=str(detail.get("keywords") or "").strip(),
                    description=str(detail.get("description") or ""),
                    is_hidden=bool(detail.get("is_hidden")),
                )

        if "doors" in spec:
            doors = spec.get("doors") or []
            if not isinstance(doors, list):
                raise serializers.ValidationError("spec.doors must be a list.")
            Door.objects.filter(from_room=room).delete()
            for door in doors:
                if not isinstance(door, dict):
                    raise serializers.ValidationError("spec.doors entries must be mappings.")
                direction = builder_manifests._coerce_choice(
                    door.get("direction"),
                    choices=adv_consts.DIRECTIONS,
                    field_name="spec.doors.direction",
                )
                to_room_ref = str(door.get("to_room") or "").strip()
                if not to_room_ref:
                    raise serializers.ValidationError("spec.doors.to_room is required.")
                key_ref = str(door.get("key") or "").strip()
                Door.objects.create(
                    direction=direction,
                    from_room=room,
                    to_room=_get_or_create_room(world=world, room_ref=to_room_ref),
                    name=str(door.get("name") or "door"),
                    key=(
                        _get_or_create_item_template(
                            world=world,
                            value=key_ref,
                            field_name="spec.doors.key",
                        )
                        if key_ref else None
                    ),
                    destroy_key=bool(door.get("destroy_key")),
                    default_state=builder_manifests._coerce_choice(
                        door.get("default_state", adv_consts.DOOR_STATE_CLOSED),
                        choices=adv_consts.DOOR_STATES,
                        field_name="spec.doors.default_state",
                    ),
                )

    return room, created


def apply_path_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Path, bool]:
    if parse_document_kind(manifest) != PATH_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'path'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Path manifests only support operation 'apply'.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    path_ref = str(metadata.get("ref") or "").strip()
    if not path_ref:
        raise serializers.ValidationError("metadata.ref is required.")
    path_name = str(metadata.get("name") or "").strip()
    if not path_name:
        raise serializers.ValidationError("metadata.name is required.")
    zone_ref = str(spec.get("zone") or "").strip()
    if not zone_ref:
        raise serializers.ValidationError("spec.zone is required.")

    rooms = spec.get("rooms", [])
    if rooms is None:
        rooms = []
    if not isinstance(rooms, list):
        raise serializers.ValidationError("spec.rooms must be a list.")

    def optional_positive_int(value: Any, *, field_name: str) -> int | None:
        if value in (None, ""):
            return None
        try:
            integer = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError(f"{field_name} must be a positive integer or empty.")
        if integer <= 0:
            raise serializers.ValidationError(f"{field_name} must be a positive integer or empty.")
        return integer

    relative_id = _parse_path_ref(path_ref, field_name="metadata.ref")
    max_per_room = optional_positive_int(
        spec.get("max_per_room"),
        field_name="spec.max_per_room",
    )
    max_per_path = optional_positive_int(
        spec.get("max_per_path"),
        field_name="spec.max_per_path",
    )

    with transaction.atomic():
        zone = _resolve_spawn_plan_zone(
            world=world,
            value=zone_ref,
            field_name="spec.zone",
        )
        existing = Path.objects.filter(world=world, relative_id=relative_id).first()
        created = existing is None

        resolved_rooms = []
        seen_room_refs = set()
        for index, room_ref in enumerate(rooms):
            room_ref_text = str(room_ref or "").strip()
            _parse_room_ref(room_ref_text)
            if room_ref_text in seen_room_refs:
                raise serializers.ValidationError(f"spec.rooms[{index}] duplicates room ref '{room_ref_text}'.")
            seen_room_refs.add(room_ref_text)
            resolved_rooms.append(_get_or_create_room(world=world, room_ref=room_ref_text))

        entry_room = None
        entry_room_ref = str(spec.get("entry_room") or "").strip()
        if entry_room_ref:
            _parse_room_ref(entry_room_ref)
            entry_room = _get_or_create_room(world=world, room_ref=entry_room_ref)

        path = existing or Path(world=world)
        path.relative_id = relative_id
        path.name = path_name
        path.zone = zone
        path.notes = str(spec.get("notes") or "")
        path.entry_room = entry_room
        path.max_per_room = max_per_room
        path.max_per_path = max_per_path
        path.save()
        if path.relative_id != relative_id:
            path.relative_id = relative_id
            path.save(update_fields=["relative_id"])

        PathRoom.objects.filter(path=path).delete()
        for room in resolved_rooms:
            PathRoom.objects.create(path=path, room=room)

    return path, created


def delete_path_manifest(*, world: World, manifest: dict[str, Any]) -> Path:
    if parse_document_kind(manifest) != PATH_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'path'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Path delete manifests require operation 'delete'.")
    metadata = _manifest_metadata(manifest)
    path_ref = str(metadata.get("ref") or "").strip()
    if not path_ref:
        raise serializers.ValidationError("metadata.ref is required.")
    relative_id = _parse_path_ref(path_ref, field_name="metadata.ref")
    path = Path.objects.filter(world=world, relative_id=relative_id).first()
    if path is None:
        raise serializers.ValidationError("Path delete manifest does not resolve to an existing path.")
    path.delete()
    return path


def _apply_item_template_inventory(*, world: World, container: ItemTemplate, inventory: list[Any]) -> None:
    if not isinstance(inventory, list):
        raise serializers.ValidationError("spec.inventory must be a list.")
    ItemTemplateInventory.objects.filter(container=container).delete()
    for entry in inventory:
        if not isinstance(entry, dict):
            raise serializers.ValidationError("spec.inventory entries must be mappings.")
        item_template = _get_or_create_item_template(
            world=world,
            value=entry.get("item_template"),
            field_name="spec.inventory.item_template",
        )
        ItemTemplateInventory.objects.create(
            container=container,
            item_template=item_template,
            probability=int(entry.get("probability", 100)),
            num_copies=int(entry.get("num_copies", 1)),
        )


def apply_item_template_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[ItemTemplate, bool]:
    if parse_document_kind(manifest) != builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'itemtemplate'.")

    normalized = copy.deepcopy(manifest)
    spec = _manifest_spec(normalized)
    inventory = spec.pop("inventory", None)

    with transaction.atomic():
        parsed = builder_manifests.parse_item_template_manifest(world=world, manifest=normalized)
        created = parsed.item_template is None
        item_template = builder_manifests.apply_item_template_manifest(parsed)
        if inventory is not None:
            _apply_item_template_inventory(
                world=world,
                container=item_template,
                inventory=inventory,
            )
    return item_template, created


def apply_item_definition_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[ItemDefinition, bool]:
    if parse_document_kind(manifest) != builder_manifests.ITEM_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'itemdefinition'.")

    parsed = builder_manifests.parse_item_definition_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.item_definition is None
    return builder_manifests.apply_item_definition_manifest(parsed), created


def apply_mob_definition_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[MobDefinition, bool]:
    if parse_document_kind(manifest) != builder_manifests.MOB_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'mobdefinition'.")

    parsed = builder_manifests.parse_mob_definition_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.mob_definition is None
    return builder_manifests.apply_mob_definition_manifest(parsed), created


def apply_item_bundle_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[ItemBundle, bool]:
    if parse_document_kind(manifest) != builder_manifests.ITEM_BUNDLE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'itembundle'.")

    parsed = builder_manifests.parse_item_bundle_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.item_bundle is None
    return builder_manifests.apply_item_bundle_manifest(parsed), created


def apply_merchant_profile_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[MerchantProfile, bool]:
    if parse_document_kind(manifest) != builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'merchantprofile'.")

    parsed = builder_manifests.parse_merchant_profile_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.merchant_profile is None
    return builder_manifests.apply_merchant_profile_manifest(parsed), created


def _apply_mob_template_inventory(*, world: World, container: MobTemplate, inventory: list[Any]) -> None:
    if not isinstance(inventory, list):
        raise serializers.ValidationError("spec.inventory must be a list.")
    MobTemplateInventory.objects.filter(container=container).delete()
    for entry in inventory:
        if not isinstance(entry, dict):
            raise serializers.ValidationError("spec.inventory entries must be mappings.")
        source_fields = [
            key
            for key in ("item_template", "item_definition", "item_bundle")
            if entry.get(key)
        ]
        if len(source_fields) != 1:
            raise serializers.ValidationError(
                "spec.inventory entries must specify exactly one of item_template, "
                "item_definition, or item_bundle."
            )
        create_kwargs = {
            "container": container,
            "probability": int(entry.get("probability", 100)),
            "num_copies": int(entry.get("num_copies", 1)),
        }
        if source_fields[0] == "item_template":
            create_kwargs["item_template"] = _get_or_create_item_template(
                world=world,
                value=entry.get("item_template"),
                field_name="spec.inventory.item_template",
            )
        elif source_fields[0] == "item_definition":
            create_kwargs["item_definition"] = _get_item_definition(
                world=world,
                value=entry.get("item_definition"),
                field_name="spec.inventory.item_definition",
            )
        else:
            create_kwargs["item_bundle"] = _get_item_bundle(
                world=world,
                value=entry.get("item_bundle"),
                field_name="spec.inventory.item_bundle",
            )
        MobTemplateInventory.objects.create(
            **create_kwargs,
        )


def apply_mob_template_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[MobTemplate, bool]:
    if parse_document_kind(manifest) != MOB_TEMPLATE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'mobtemplate'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Mob template manifests only support operation 'apply'.")

    metadata = _manifest_metadata(manifest)
    spec = copy.deepcopy(_manifest_spec(manifest))
    inventory = spec.pop("inventory", None)

    slug_source = metadata.get("slug") or metadata.get("name")
    slug = _slug_or_error(slug_source, "metadata.slug")
    name = str(metadata.get("name") or slug.replace("-", " ").title()).strip()
    if not name:
        raise serializers.ValidationError("metadata.name cannot be empty.")

    mob_template = MobTemplate.objects.filter(world=world, slug=slug).first()
    created = mob_template is None

    serializer_data: dict[str, Any] = {
        "slug": slug,
        "name": name,
    }
    for field_name in _MOB_TEMPLATE_SPEC_FIELDS:
        if field_name in spec:
            serializer_data[field_name] = spec.get(field_name)

    serializer = builder_serializers.MobTemplateSerializer(
        instance=mob_template,
        data=serializer_data,
        context={"world": world},
    )
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        saved_mob = serializer.save(world=world) if mob_template is None else serializer.save()

        explicit_updates = {
            field_name: spec.get(field_name)
            for field_name in _MOB_TEMPLATE_SPEC_FIELDS
            if field_name in spec
        }
        if explicit_updates:
            for field_name, value in explicit_updates.items():
                setattr(saved_mob, field_name, value)
            saved_mob.save(update_fields=list(explicit_updates.keys()))

        if inventory is not None:
            _apply_mob_template_inventory(
                world=world,
                container=saved_mob,
                inventory=inventory,
            )

    return saved_mob, created


def apply_spawn_plan_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[SpawnPlan, bool]:
    if parse_document_kind(manifest) != SPAWN_PLAN_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'spawnplan'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError("Spawn plan manifests only support operation 'apply' in this parser.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    slug_source = metadata.get("slug") or metadata.get("name")
    slug = _slug_or_error(slug_source, "metadata.slug")
    name = str(metadata.get("name") or slug.replace("-", " ").title()).strip()
    if not name:
        raise serializers.ValidationError("metadata.name cannot be empty.")

    zone = _resolve_spawn_plan_zone(world=world, value=spec.get("zone"), field_name="spec.zone")
    if spec.get("zone_ref") not in (None, ""):
        zone_ref = _resolve_spawn_plan_zone(world=world, value=spec.get("zone_ref"), field_name="spec.zone_ref")
        if zone_ref.id != zone.id:
            raise serializers.ValidationError("spec.zone_ref must match spec.zone when provided.")
    conditions = copy.deepcopy(spec.get("conditions") or {})
    _validate_condition_payload_or_error(conditions, field_name="spec.conditions")

    entries = spec.get("entries") or []
    if not isinstance(entries, list):
        raise serializers.ValidationError("spec.entries must be a list.")
    entry_slugs = set()
    for index, entry_spec in enumerate(entries):
        if not isinstance(entry_spec, dict):
            raise serializers.ValidationError(f"spec.entries[{index}] must be a mapping.")
        entry_slug = _slug_or_error(
            entry_spec.get("slug") or entry_spec.get("name") or f"entry-{index + 1}",
            f"spec.entries[{index}].slug",
        )
        if entry_slug in entry_slugs:
            raise serializers.ValidationError(f"Duplicate spawn entry slug '{entry_slug}'.")
        entry_slugs.add(entry_slug)

    normalized_entries = []
    for index, entry_spec in enumerate(entries):
        entry_field = f"spec.entries[{index}]"
        entry_slug = _slug_or_error(
            entry_spec.get("slug") or entry_spec.get("name") or f"entry-{index + 1}",
            f"{entry_field}.slug",
        )
        source = _normalize_spawn_source(entry_spec, field_name=entry_field)
        _validate_spawn_source(world=world, source=source, field_name=f"{entry_field}.source")
        target = _validate_spawn_target(
            world=world,
            target=entry_spec.get("target"),
            entry_slugs=entry_slugs,
            field_name=f"{entry_field}.target",
        )
        entry_conditions = copy.deepcopy(entry_spec.get("conditions") or {})
        _validate_condition_payload_or_error(entry_conditions, field_name=f"{entry_field}.conditions")
        normalized_entries.append({
            "slug": entry_slug,
            "name": str(entry_spec.get("name") or ""),
            "order": int(entry_spec.get("order", index + 1) or 0),
            "is_active": bool(entry_spec.get("is_active", True)),
            "source": source,
            "target": target,
            "count": _normalize_spawn_count(entry_spec.get("count", 1), field_name=f"{entry_field}.count"),
            "placement": copy.deepcopy(entry_spec.get("placement") or {}),
            "affixes": _normalize_spawn_affixes(entry_spec.get("affixes"), field_name=f"{entry_field}.affixes"),
            "conditions": entry_conditions,
        })

    with transaction.atomic():
        spawn_plan = SpawnPlan.objects.filter(world=world, slug=slug).first()
        created = spawn_plan is None
        if spawn_plan is None:
            spawn_plan = SpawnPlan(world=world, slug=slug)
        spawn_plan.zone = zone
        spawn_plan.name = name
        spawn_plan.notes = str(spec.get("notes") or "")
        spawn_plan.order = int(spec.get("order", spawn_plan.order or 0) or 0)
        spawn_plan.is_active = bool(spec.get("is_active", True))
        spawn_plan.reset_policy = copy.deepcopy(spec.get("reset") or {})
        spawn_plan.respawn_policy = copy.deepcopy(spec.get("respawn") or {})
        spawn_plan.randomization = copy.deepcopy(spec.get("randomization") or {})
        spawn_plan.conditions = conditions
        spawn_plan.save()

        seen_entry_slugs = []
        for normalized in normalized_entries:
            seen_entry_slugs.append(normalized["slug"])
            entry = SpawnEntry.objects.filter(plan=spawn_plan, slug=normalized["slug"]).first()
            if entry is None:
                entry = SpawnEntry(plan=spawn_plan, slug=normalized["slug"])
            for field_name in (
                "name",
                "order",
                "is_active",
                "source",
                "target",
                "count",
                "placement",
                "affixes",
                "conditions",
            ):
                setattr(entry, field_name, normalized[field_name])
            entry.save()
        spawn_plan.entries.exclude(slug__in=seen_entry_slugs).delete()

    return spawn_plan, created


def delete_spawn_plan_manifest(*, world: World, manifest: dict[str, Any]) -> SpawnPlan:
    if parse_document_kind(manifest) != SPAWN_PLAN_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'spawnplan'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Spawn plan delete manifests require operation 'delete'.")
    metadata = _manifest_metadata(manifest)
    slug = str(metadata.get("slug") or "").strip()
    key = str(metadata.get("key") or "").strip()
    spawn_plan = None
    if slug:
        spawn_plan = SpawnPlan.objects.filter(world=world, slug=slug).first()
    elif key:
        plan_id = builder_manifests._parse_entity_ref(key, "spawnplan", "metadata.key")
        spawn_plan = SpawnPlan.objects.filter(world=world, pk=plan_id).first()
    if spawn_plan is None:
        raise serializers.ValidationError("Spawn plan delete manifest does not resolve to an existing spawn plan.")
    spawn_plan._deleted_payload = serialize_spawn_plan_payload(
        spawn_plan,
        include_yaml=False,
    )
    spawn_plan.delete()
    return spawn_plan


def apply_world_manifest(*, world: World, manifest: dict[str, Any]) -> None:
    if parse_document_kind(manifest) != WORLD_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'world'.")

    normalized = copy.deepcopy(manifest)
    spec = _manifest_spec(normalized)
    for field_name in ("starting_room", "death_room"):
        if field_name not in spec:
            continue
        room_ref = str(spec.get(field_name) or "").strip()
        if not room_ref:
            continue
        if room_ref.startswith(_ROOM_REF_PREFIX):
            room = _get_or_create_room(world=world, room_ref=room_ref)
            spec[field_name] = f"room.{room.id}"

    with transaction.atomic():
        parsed = builder_manifests.parse_world_config_manifest(
            world=world,
            manifest=normalized,
        )
        builder_manifests.apply_world_config_manifest(parsed)


def apply_quest_arc_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[QuestArcTemplate, bool]:
    if parse_document_kind(manifest) != quest_manifests.QUEST_ARC_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'questarc'.")
    with transaction.atomic():
        parsed = quest_manifests.parse_quest_arc_manifest(world=world, manifest=manifest)
        created = parsed.quest_arc is None
        quest_arc = quest_manifests.apply_quest_arc_manifest(parsed)
    return quest_arc, created


def apply_quest_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[QuestTemplate, bool]:
    if parse_document_kind(manifest) != quest_manifests.QUEST_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'quest'.")
    with transaction.atomic():
        parsed = quest_manifests.parse_quest_manifest(world=world, manifest=manifest)
        created = parsed.quest is None
        quest = quest_manifests.apply_quest_manifest(parsed)
    return quest, created


def _resolve_trigger_target(
    *,
    world: World,
    target: dict[str, Any] | None,
) -> tuple[ContentType, int]:
    target = target or {}
    if not isinstance(target, dict):
        raise serializers.ValidationError("spec.target must be a mapping.")

    target_key = str(target.get("key") or "").strip()
    target_ref = str(target.get("ref") or "").strip()
    target_type = str(target.get("type") or "world").strip().lower()

    if target_key and not target_ref:
        if target_type == "room":
            return ContentType.objects.get_for_model(Room), builder_manifests._parse_entity_ref(
                target_key, "room", "spec.target.key"
            )
        if target_type == "zone":
            return ContentType.objects.get_for_model(Zone), builder_manifests._parse_entity_ref(
                target_key, "zone", "spec.target.key"
            )
        if target_type == "world":
            return ContentType.objects.get_for_model(World), builder_manifests._parse_entity_ref(
                target_key, "world", "spec.target.key"
            )
        if target_type in {"mobtemplate", "mob_template"}:
            return ContentType.objects.get_for_model(MobTemplate), builder_manifests._parse_entity_ref(
                target_key, "mobtemplate", "spec.target.key"
            )

    if target_type == "world":
        return ContentType.objects.get_for_model(World), world.id
    if target_type == "room":
        return ContentType.objects.get_for_model(Room), _get_or_create_room(
            world=world,
            room_ref=target_ref,
        ).id
    if target_type == "zone":
        zone = _get_or_create_zone(world=world, zone_name=target_ref)
        if zone is None:
            raise serializers.ValidationError("spec.target.ref is required for zone targets.")
        return ContentType.objects.get_for_model(Zone), zone.id
    if target_type in {"mobtemplate", "mob_template"}:
        return ContentType.objects.get_for_model(MobTemplate), _get_or_create_mob_template(
            world=world,
            value=target_ref,
            field_name="spec.target.ref",
        ).id

    raise serializers.ValidationError(f"Unsupported trigger target type '{target_type}'.")


def _match_existing_trigger(
    *,
    world: World,
    name: str,
    scope: str,
    kind: str,
    target_type: ContentType,
    target_id: int,
    event: str,
    match: str,
) -> Trigger | None:
    candidates = Trigger.objects.filter(
        world=world,
        name=name,
        scope=scope,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
    ).order_by("id")
    if candidates.count() == 1:
        return candidates.first()
    narrowed = candidates.filter(event=event, match=match)
    return narrowed.first()


def normalize_trigger_manifest_for_import(*, world: World, manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(manifest)
    if builder_manifests.parse_manifest_operation(normalized) == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        return normalized

    metadata = _manifest_metadata(normalized)
    spec = _manifest_spec(normalized)
    target = spec.get("target")
    if not isinstance(target, dict):
        return normalized
    if not str(target.get("ref") or "").strip():
        return normalized

    target_type, target_id = _resolve_trigger_target(
        world=world,
        target=target,
    )
    manifest_target_type = target_type.model
    spec["target"] = {
        "type": manifest_target_type,
        "key": f"{manifest_target_type}.{target_id}",
    }

    if metadata.get("id") in (None, "") and metadata.get("key") in (None, ""):
        existing = _match_existing_trigger(
            world=world,
            name=str(metadata.get("name") or ""),
            scope=str(spec.get("scope") or adv_consts.TRIGGER_SCOPE_ROOM).strip().lower(),
            kind=builder_manifests._canonical_trigger_kind(
                str(spec.get("kind") or adv_consts.TRIGGER_KIND_COMMAND).strip().lower()
            ),
            target_type=target_type,
            target_id=target_id,
            event=str(spec.get("event") or "").strip().lower(),
            match=str(spec.get("match") or ""),
        )
        if existing:
            metadata["key"] = existing.key

    return normalized
