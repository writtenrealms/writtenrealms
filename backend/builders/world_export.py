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
    Currency,
    ItemTemplate,
    ItemTemplateInventory,
    MobTemplate,
    MobTemplateInventory,
    Trigger,
)
from config import constants as adv_consts
from quests import entity_refs as quest_entity_refs
from quests import manifests as quest_manifests
from quests.models import QuestArcTemplate, QuestTemplate
from worlds.models import Door, Room, RoomDetail, RoomFlag, World, Zone


WORLD_MANIFEST_KIND = "world"
CURRENCY_MANIFEST_KIND = "currency"
ZONE_MANIFEST_KIND = "zone"
ROOM_MANIFEST_KIND = "room"
MOB_TEMPLATE_MANIFEST_KIND = "mobtemplate"

_WORLD_KIND_ALIASES = {"world", "worldconfig", "world-config", "world_config"}
_ZONE_KIND_ALIASES = {"zone"}
_ROOM_KIND_ALIASES = {"room"}
_CURRENCY_KIND_ALIASES = {"currency"}
_ITEM_TEMPLATE_KIND_ALIASES = {"itemtemplate", "item-template", "item_template"}
_MOB_TEMPLATE_KIND_ALIASES = {"mobtemplate", "mob-template", "mob_template"}
_QUEST_KIND_ALIASES = {quest_manifests.QUEST_MANIFEST_KIND}
_QUEST_ARC_KIND_ALIASES = {
    quest_manifests.QUEST_ARC_MANIFEST_KIND,
    "quest-arc",
    "quest_arc",
}
_TRIGGER_KIND_ALIASES = {builder_manifests.TRIGGER_MANIFEST_KIND}

_ROOM_REF_PREFIX = "room@"
_ITEM_REF_PREFIX = "itemtemplate."
_MOB_REF_PREFIX = "mobtemplate."

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
    "mana_max",
    "mana_regen",
    "stamina_max",
    "stamina_regen",
    "regen_rate",
    "attack_power",
    "spell_power",
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
    "teaches",
    "teaching_conditions",
    "combat_script",
    "unlearns",
    "unlearn_cost",
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
    if raw_kind in _ITEM_TEMPLATE_KIND_ALIASES:
        return builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND
    if raw_kind in _MOB_TEMPLATE_KIND_ALIASES:
        return MOB_TEMPLATE_MANIFEST_KIND
    if raw_kind in _QUEST_ARC_KIND_ALIASES:
        return quest_manifests.QUEST_ARC_MANIFEST_KIND
    if raw_kind in _QUEST_KIND_ALIASES:
        return quest_manifests.QUEST_MANIFEST_KIND
    if raw_kind in _TRIGGER_KIND_ALIASES:
        return builder_manifests.TRIGGER_MANIFEST_KIND
    raise serializers.ValidationError(
        "Unsupported manifest kind. Supported kinds: "
        "world, currency, zone, room, itemtemplate, mobtemplate, questarc, quest, trigger."
    )


def _room_ref_from_coords(*, x: int, y: int, z: int) -> str:
    return f"{_ROOM_REF_PREFIX}{x},{y},{z}"


def _room_ref(room: Room | None) -> str:
    if room is None:
        return ""
    return _room_ref_from_coords(x=room.x, y=room.y, z=room.z)


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


def _item_ref(item_template: ItemTemplate | None) -> str:
    if item_template is None or not item_template.slug:
        return ""
    return f"{_ITEM_REF_PREFIX}{item_template.slug}"


def _mob_ref(mob_template: MobTemplate | None) -> str:
    if mob_template is None or not mob_template.slug:
        return ""
    return f"{_MOB_REF_PREFIX}{mob_template.slug}"


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


def _coerce_zone_data(value: Any) -> str:
    if value in (None, ""):
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        return text or "{}"
    try:
        return json.dumps(value)
    except TypeError:
        raise serializers.ValidationError("spec.zone_data must be a JSON-compatible value or string.")


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
            "name": zone.name or "",
        },
        "spec": {
            "description": zone.description or "",
            "notes": zone.notes or "",
            "is_warzone": bool(zone.is_warzone),
            "zone_data": _parse_json_text(zone.zone_data),
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
            "zone": room.zone.name if room.zone else "",
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


def _serialize_item_template_manifest(item_template: ItemTemplate) -> dict[str, Any]:
    manifest = builder_manifests.item_template_to_manifest(item_template)
    spec = copy.deepcopy(manifest["spec"])
    spec["inventory"] = [
        {
            "item_template": _item_ref(inventory.item_template),
            "probability": int(inventory.probability),
            "num_copies": int(inventory.num_copies),
        }
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


def _serialize_mob_template_manifest(mob_template: MobTemplate) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    for field_name in _MOB_TEMPLATE_SPEC_FIELDS:
        value = getattr(mob_template, field_name)
        spec[field_name] = "" if value is None else value
    spec["inventory"] = [
        {
            "item_template": _item_ref(inventory.item_template),
            "probability": int(inventory.probability),
            "num_copies": int(inventory.num_copies),
        }
        for inventory in mob_template.template_inventories.select_related("item_template").all().order_by(
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


def _canonicalize_condition_refs(condition: Any, *, world: World) -> Any:
    if condition in (None, {}, []):
        return condition
    if isinstance(condition, list):
        return [_canonicalize_condition_refs(item, world=world) for item in condition]
    if not isinstance(condition, dict):
        return condition

    canonical = copy.deepcopy(condition)

    if "all" in canonical:
        canonical["all"] = _canonicalize_condition_refs(canonical.get("all"), world=world)
    if "any" in canonical:
        canonical["any"] = _canonicalize_condition_refs(canonical.get("any"), world=world)
    if "not" in canonical:
        canonical["not"] = _canonicalize_condition_refs(canonical.get("not"), world=world)

    for operator in ("eq", "ne", "gte", "lte", "in"):
        raw_args = canonical.get(operator)
        if not isinstance(raw_args, list) or len(raw_args) != 2:
            continue

        left_path = raw_args[0]
        right_value = raw_args[1]
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
            _serialize_mob_template_manifest(mob_template)
            for mob_template in world.mob_templates.prefetch_related(
                "template_inventories__item_template",
            ).order_by("slug", "id")
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
        "item_templates": 0,
        "mob_templates": 0,
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
        elif kind == builder_manifests.ITEM_TEMPLATE_MANIFEST_KIND:
            counts["item_templates"] += 1
        elif kind == MOB_TEMPLATE_MANIFEST_KIND:
            counts["mob_templates"] += 1
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
    if str(zone.zone_data or "{}").strip() not in {"", "{}"}:
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


def _get_or_create_zone(*, world: World, zone_name: str) -> Zone | None:
    name = str(zone_name or "").strip()
    if not name:
        return None
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
    name = str(metadata.get("name") or "").strip()
    if not name:
        raise serializers.ValidationError("metadata.name is required.")

    existing = Zone.objects.filter(world=world, name=name).order_by("id").first()
    created = existing is None

    with transaction.atomic():
        zone = existing or _get_or_create_zone(world=world, zone_name=name)
        zone.name = name
        if "description" in spec or created:
            zone.description = str(spec.get("description", zone.description or ""))
        if "notes" in spec or created:
            zone.notes = str(spec.get("notes", zone.notes or ""))
        if "is_warzone" in spec or created:
            zone.is_warzone = bool(spec.get("is_warzone", zone.is_warzone if existing else False))
        if "zone_data" in spec or created:
            zone.zone_data = _coerce_zone_data(spec.get("zone_data", zone.zone_data if existing else "{}"))
        if "respawn_wait" in spec or created:
            zone.respawn_wait = int(spec.get("respawn_wait", zone.respawn_wait if existing else 300))
        if "pvp_zone" in spec or created:
            zone.pvp_zone = bool(spec.get("pvp_zone", zone.pvp_zone if existing else False))
        zone.save()

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
        zone_name = str(spec.get("zone") or "").strip() if "zone" in spec else (
            existing.zone.name if existing and existing.zone else ""
        )
        zone = _get_or_create_zone(world=world, zone_name=zone_name) if zone_name else None

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


def _apply_mob_template_inventory(*, world: World, container: MobTemplate, inventory: list[Any]) -> None:
    if not isinstance(inventory, list):
        raise serializers.ValidationError("spec.inventory must be a list.")
    MobTemplateInventory.objects.filter(container=container).delete()
    for entry in inventory:
        if not isinstance(entry, dict):
            raise serializers.ValidationError("spec.inventory entries must be mappings.")
        item_template = _get_or_create_item_template(
            world=world,
            value=entry.get("item_template"),
            field_name="spec.inventory.item_template",
        )
        MobTemplateInventory.objects.create(
            container=container,
            item_template=item_template,
            probability=int(entry.get("probability", 100)),
            num_copies=int(entry.get("num_copies", 1)),
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


def apply_world_manifest(*, world: World, manifest: dict[str, Any]) -> None:
    if parse_document_kind(manifest) != WORLD_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'world'.")

    normalized = copy.deepcopy(manifest)
    normalized["kind"] = builder_manifests.WORLD_CONFIG_MANIFEST_KIND
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
