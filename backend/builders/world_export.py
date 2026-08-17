from __future__ import annotations

import copy
import json
from typing import Any

import yaml
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.deletion import RestrictedError
from django.utils.text import slugify
from rest_framework import serializers

from builders import manifests as builder_manifests
from builders.doors import DoorFaceSpec, replace_room_door_faces
from builders.models import (
    AbilityDefinition,
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingProfileRecipe,
    CraftingRecipe,
    Currency,
    Faction,
    FactionAssignment,
    ItemBundle,
    ItemDefinition,
    ItemSalvageYield,
    LastViewedRoom,
    MerchantProfile,
    MobDefinition,
    Path,
    PathRoom,
    SpawnEntry,
    SpawnPlan,
    Social,
    TrainerProfile,
    TrainerProfileAbility,
    Trigger,
)
from builders.loot_tables import normalize_loot_table
from config import constants as adv_consts
from core.abilities import AbilityValidationError, normalize_ability_definition
from core.condition_dsl import validate_condition_payload
from core.death_routing import (
    acquire_death_routing_config_locks,
    death_routing_config_ids_for_world,
)
from core.mob_traits import normalize_trait_table
from core.scoped_state import (
    STATE_SCOPE_ROOM,
    STATE_SCOPE_ZONE,
    get_initial_state_snapshot,
    normalize_state_snapshot,
    replace_initial_state_snapshot,
)
from quests import entity_refs as quest_entity_refs
from quests import manifests as quest_manifests
from quests.models import QuestArcTemplate, QuestTemplate
from worlds.models import (
    BIGINT_MAX,
    Door,
    Room,
    RoomDetail,
    RoomFlag,
    World,
    WorldConfig,
    Zone,
)
from worlds.room_refs import (
    RoomReferenceError,
    build_room_reference_object_cache,
    canonicalize_command_room_references_in_text,
    canonicalize_room_reference,
    format_room_manifest_ref,
    parse_room_reference,
    refresh_room_reference_object_cache,
    resolve_room_reference,
    use_room_reference_object_caches,
)


WORLD_MANIFEST_KIND = "world"
WORLD_BUNDLE_MANIFEST_KIND = "worldbundle"
CURRENCY_MANIFEST_KIND = "currency"
ZONE_MANIFEST_KIND = "zone"
ROOM_MANIFEST_KIND = "room"
PATH_MANIFEST_KIND = "path"
SPAWN_PLAN_MANIFEST_KIND = "spawnplan"
SOCIAL_MANIFEST_KIND = builder_manifests.SOCIAL_MANIFEST_KIND
CANONICAL_MANIFEST_API_VERSION = builder_manifests.CANONICAL_MANIFEST_API_VERSION

_WORLD_KIND_ALIASES = {"world"}
_WORLD_BUNDLE_KIND_ALIASES = {
    WORLD_BUNDLE_MANIFEST_KIND,
    "world-bundle",
    "world_bundle",
}
_ZONE_KIND_ALIASES = {"zone"}
_ROOM_KIND_ALIASES = {"room"}
_PATH_KIND_ALIASES = {"path"}
_CURRENCY_KIND_ALIASES = {"currency"}
_ITEM_DEFINITION_KIND_ALIASES = {"itemdefinition", "item-definition", "item_definition"}
_ITEM_BUNDLE_KIND_ALIASES = {"itembundle", "item-bundle", "item_bundle"}
_MERCHANT_PROFILE_KIND_ALIASES = {"merchantprofile", "merchant-profile", "merchant_profile"}
_CRAFT_MATERIAL_KIND_ALIASES = {"craftmaterial", "craft-material", "craft_material"}
_CRAFTING_RECIPE_KIND_ALIASES = {"craftingrecipe", "crafting-recipe", "crafting_recipe"}
_CRAFTING_PROFILE_KIND_ALIASES = {"craftingprofile", "crafting-profile", "crafting_profile"}
_TRAINER_PROFILE_KIND_ALIASES = {"trainerprofile", "trainer-profile", "trainer_profile"}
_FACTION_KIND_ALIASES = {"faction"}
_MOB_DEFINITION_KIND_ALIASES = {"mobdefinition", "mob-definition", "mob_definition"}
_SPAWN_PLAN_KIND_ALIASES = {"spawnplan", "spawn-plan", "spawn_plan"}
_SOCIAL_KIND_ALIASES = {SOCIAL_MANIFEST_KIND}
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
_SPAWN_ENTRY_REF_PREFIX = "entry."
_BASE_WORLD_BUNDLE_REF = "world@base"
_INSTANCE_BUNDLE_REF_PREFIX = "instance."
_ITEM_DEFINITION_REF_PREFIX = "itemdefinition."
_ITEM_BUNDLE_REF_PREFIX = "itembundle."
_MERCHANT_PROFILE_REF_PREFIX = "merchantprofile."
_CRAFTING_PROFILE_REF_PREFIX = "craftingprofile."
_TRAINER_PROFILE_REF_PREFIX = "trainerprofile."
_MOB_DEFINITION_REF_PREFIX = "mobdefinition."

_ZONE_SORT_KEY = lambda zone: ((zone.name or "").lower(), zone.id)
_ROOM_SORT_KEY = lambda room: (room.z, room.y, room.x, room.id)

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
    if raw_kind in _WORLD_BUNDLE_KIND_ALIASES:
        return WORLD_BUNDLE_MANIFEST_KIND
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
    if raw_kind in _ITEM_DEFINITION_KIND_ALIASES:
        return builder_manifests.ITEM_DEFINITION_MANIFEST_KIND
    if raw_kind in _ITEM_BUNDLE_KIND_ALIASES:
        return builder_manifests.ITEM_BUNDLE_MANIFEST_KIND
    if raw_kind in _MERCHANT_PROFILE_KIND_ALIASES:
        return builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND
    if raw_kind in _CRAFT_MATERIAL_KIND_ALIASES:
        return builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND
    if raw_kind in _CRAFTING_RECIPE_KIND_ALIASES:
        return builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND
    if raw_kind in _CRAFTING_PROFILE_KIND_ALIASES:
        return builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND
    if raw_kind in _TRAINER_PROFILE_KIND_ALIASES:
        return builder_manifests.TRAINER_PROFILE_MANIFEST_KIND
    if raw_kind in _FACTION_KIND_ALIASES:
        return builder_manifests.FACTION_MANIFEST_KIND
    if raw_kind in _MOB_DEFINITION_KIND_ALIASES:
        return builder_manifests.MOB_DEFINITION_MANIFEST_KIND
    if raw_kind in _SPAWN_PLAN_KIND_ALIASES:
        return SPAWN_PLAN_MANIFEST_KIND
    if raw_kind in _SOCIAL_KIND_ALIASES:
        return SOCIAL_MANIFEST_KIND
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
        "worldbundle, world, currency, zone, room, path, itemdefinition, "
        "itembundle, merchantprofile, trainerprofile, faction, mobdefinition, spawnplan, "
        "social, questarc, quest, trigger, ability, abilities."
    )


def _room_ref_from_coords(*, x: int, y: int, z: int) -> str:
    return f"{_ROOM_REF_PREFIX}{x},{y},{z}"


def _room_ref(
    room: Room | None,
    *,
    world: World | int | None = None,
    field_name: str = "Room reference",
) -> str:
    if room is None:
        return ""
    expected_world_id = world.id if isinstance(world, World) else world
    if expected_world_id is not None and room.world_id != expected_world_id:
        raise serializers.ValidationError(
            f"{field_name} points to a room outside this world."
        )
    try:
        return format_room_manifest_ref(room)
    except RoomReferenceError as exc:
        raise serializers.ValidationError(str(exc)) from exc


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
    """Parse the legacy coordinate form retained for v1alpha1 imports."""

    text = str(value or "").strip()
    parsed = parse_room_reference(text)
    if parsed is None or parsed.kind != "coordinates":
        raise serializers.ValidationError(
            "Room references must use the form 'room@x,y,z'."
        )
    return parsed.x, parsed.y, parsed.z


def _resolve_room_reference_or_error(
    *,
    world: World,
    value: Any,
    field_name: str,
) -> Room:
    parsed = parse_room_reference(value)
    if parsed is None:
        raise serializers.ValidationError(
            f"{field_name} must use 'room@<relative_id>', legacy "
            "'room@x,y,z', or legacy 'room.<database_id>'."
        )
    room = resolve_room_reference(world, value)
    if room is None:
        raise serializers.ValidationError(
            f"{field_name} references an unknown room in this world."
        )
    return room


def _coerce_room_coordinates(
    value: Any,
    *,
    field_name: str = "spec.coordinates",
) -> tuple[int, int, int]:
    if not isinstance(value, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")
    unknown_fields = sorted(set(value) - {"x", "y", "z"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
        )
    missing_fields = [axis for axis in ("x", "y", "z") if axis not in value]
    if missing_fields:
        raise serializers.ValidationError(
            f"{field_name} requires x, y, and z."
        )
    try:
        return tuple(int(value[axis]) for axis in ("x", "y", "z"))
    except (TypeError, ValueError):
        raise serializers.ValidationError(
            f"{field_name}.x, y, and z must be integers."
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


def _item_definition_ref(item_definition: ItemDefinition | None) -> str:
    if item_definition is None or not item_definition.slug:
        return ""
    return f"{_ITEM_DEFINITION_REF_PREFIX}{item_definition.slug}"


def _item_bundle_ref(item_bundle: ItemBundle | None) -> str:
    if item_bundle is None or not item_bundle.slug:
        return ""
    return f"{_ITEM_BUNDLE_REF_PREFIX}{item_bundle.slug}"


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


def _coerce_initial_state(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            value = {}
        else:
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    f"{field_name} must be a JSON object."
                ) from exc
    try:
        return normalize_state_snapshot(value, field_name=field_name)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc


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
            "plural_name": currency.plural_name or "",
            "description": currency.description or "",
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
            "initial_state": get_initial_state_snapshot(STATE_SCOPE_ZONE, zone),
            "respawn_wait": int(zone.respawn_wait),
            "pvp_zone": bool(zone.pvp_zone),
            "center": _room_ref(
                zone.center,
                world=zone.world_id,
                field_name=f"Zone '{zone.name}' center",
            ),
        },
    }


def _serialize_zone_delete_manifest(zone: Zone) -> dict[str, Any]:
    return {
        "kind": ZONE_MANIFEST_KIND,
        "operation": builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "ref": _zone_ref(zone),
            "name": zone.name or "",
        },
    }


def serialize_zone_manifest_payload(zone: Zone) -> dict[str, Any]:
    manifest = _serialize_zone_manifest(zone)
    delete_manifest = _serialize_zone_delete_manifest(zone)
    return {
        "manifest": manifest,
        "yaml": _manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": _manifest_to_yaml(delete_manifest),
    }


def serialize_zone_payload(
    zone: Zone,
    *,
    include_yaml: bool = True,
) -> dict[str, Any]:
    payload = {
        "id": zone.id,
        "key": zone.key,
        "relative_id": zone.relative_id,
        "manifest_ref": _zone_ref(zone),
        "name": zone.name,
        "description": zone.description or "",
        "notes": zone.notes or "",
        "respawn_wait": int(zone.respawn_wait),
        "pvp_zone": bool(zone.pvp_zone),
        "center": _room_ref(
            zone.center,
            world=zone.world_id,
            field_name=f"Zone '{zone.name}' center",
        ),
    }
    if include_yaml:
        payload.update(serialize_zone_manifest_payload(zone))
    return payload


def _room_door_faces_for_export(room: Room) -> list[Door]:
    export_faces = getattr(room, "_export_door_faces", None)
    if export_faces is not None:
        return export_faces
    cached_faces = getattr(
        room,
        "_prefetched_objects_cache",
        {},
    ).get("doors_from")
    if cached_faces is None:
        cached_faces = list(
            room.doors_from.select_related(
                "doorway__key",
                "to_room",
            )
        )
    return sorted(cached_faces, key=lambda door: (door.direction, door.id))


def _serialize_room_manifest(room: Room) -> dict[str, Any]:
    export_flags = getattr(room, "_export_flags", None)
    if export_flags is None:
        flag_codes = sorted(room.flags.values_list("code", flat=True))
    else:
        flag_codes = [flag.code for flag in export_flags]

    export_details = getattr(room, "_export_details", None)
    if export_details is None:
        export_details = room.details.all().order_by("created_ts", "id")

    return {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
        "kind": ROOM_MANIFEST_KIND,
        "metadata": {
            "ref": _room_ref(room, world=room.world_id),
            "name": room.name or "",
        },
        "spec": {
            "coordinates": {
                "x": room.x,
                "y": room.y,
                "z": room.z,
            },
            "zone": _zone_ref(room.zone),
            "description": room.description or "",
            "note": room.note or "",
            "type": room.type,
            "color": room.color or "",
            "initial_state": get_initial_state_snapshot(STATE_SCOPE_ROOM, room),
            "is_landmark": bool(room.is_landmark),
            "exits": {
                direction: _room_ref(
                    getattr(room, direction, None),
                    world=room.world_id,
                    field_name=f"Room '{room.name}' {direction} exit",
                )
                for direction in adv_consts.DIRECTIONS
            },
            "flags": flag_codes,
            "details": [
                {
                    "keywords": detail.keywords,
                    "description": detail.description,
                    "is_hidden": bool(detail.is_hidden),
                }
                for detail in export_details
            ],
            "doors": [
                {
                    "direction": door.direction,
                    "name": door.name or "",
                    "to_room": _room_ref(
                        door.to_room,
                        world=room.world_id,
                        field_name=(
                            f"Room '{room.name}' {door.direction} door target"
                        ),
                    ),
                    "key": _item_definition_ref(door.key),
                    "destroy_key": bool(door.destroy_key),
                    "default_state": door.default_state,
                }
                for door in _room_door_faces_for_export(room)
            ],
            **(
                {
                    "merchant": {
                        "profile": (
                            f"{_MERCHANT_PROFILE_REF_PREFIX}{room.merchant_profile.slug}"
                        ),
                    },
                }
                if room.merchant_profile_id else {}
            ),
            **(
                {
                    "crafting": {
                        "profile": (
                            f"{_CRAFTING_PROFILE_REF_PREFIX}{room.crafting_profile.slug}"
                        ),
                    },
                }
                if room.crafting_profile_id else {}
            ),
            **(
                {
                    "trainer": {
                        "profile": (
                            f"{_TRAINER_PROFILE_REF_PREFIX}"
                            f"{room.trainer_profile.slug}"
                        ),
                    },
                }
                if room.trainer_profile_id else {}
            ),
        },
    }


def serialize_room_manifest_payload(room: Room) -> dict[str, Any]:
    manifest = _serialize_room_manifest(room)
    return {
        "manifest": manifest,
        "yaml": _manifest_to_yaml(manifest),
    }


def _serialize_path_manifest(path: Path) -> dict[str, Any]:
    export_path_rooms = getattr(path, "_export_path_rooms", None)
    if export_path_rooms is None:
        export_path_rooms = (
            PathRoom.objects.filter(path=path)
            .select_related("room")
            .order_by("id")
        )
    return {
        "kind": PATH_MANIFEST_KIND,
        "metadata": {
            "ref": _path_ref(path),
            "name": path.name or "",
        },
        "spec": {
            "zone": _zone_ref(path.zone),
            "notes": path.notes or "",
            "entry_room": _room_ref(
                path.entry_room,
                world=path.world_id,
                field_name=f"Path '{path.name}' entry room",
            ),
            "max_per_room": path.max_per_room,
            "max_per_path": path.max_per_path,
            "rooms": [
                _room_ref(
                    path_room.room,
                    world=path.world_id,
                    field_name=f"Path '{path.name}' room",
                )
                for path_room in export_path_rooms
            ],
        },
    }


def _serialize_item_definition_manifest(item_definition: ItemDefinition) -> dict[str, Any]:
    manifest = builder_manifests.item_definition_to_manifest(item_definition)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


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


def _portable_authored_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _serialize_craft_material_manifest(material: CraftMaterial) -> dict[str, Any]:
    return _portable_authored_manifest(
        builder_manifests.craft_material_to_manifest(material)
    )


def _serialize_crafting_recipe_manifest(recipe: CraftingRecipe) -> dict[str, Any]:
    return _portable_authored_manifest(
        builder_manifests.crafting_recipe_to_manifest(recipe)
    )


def _serialize_crafting_profile_manifest(profile: CraftingProfile) -> dict[str, Any]:
    return _portable_authored_manifest(
        builder_manifests.crafting_profile_to_manifest(profile)
    )


def _serialize_trainer_profile_manifest(profile: TrainerProfile) -> dict[str, Any]:
    return _portable_authored_manifest(
        builder_manifests.trainer_profile_to_manifest(profile)
    )


def _serialize_faction_manifest(faction: Faction) -> dict[str, Any]:
    manifest = builder_manifests.faction_to_manifest(
        faction,
        room_reference_mode="manifest",
    )
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _serialize_mob_definition_manifest(mob_definition: MobDefinition) -> dict[str, Any]:
    manifest = builder_manifests.mob_definition_to_manifest(mob_definition)
    manifest["metadata"].pop("world", None)
    manifest["metadata"].pop("id", None)
    manifest["metadata"].pop("key", None)
    return manifest


def _canonicalize_spawn_source_for_export(
    *,
    world: World,
    source: Any,
    field_name: str,
    source_ref_cache: dict[tuple[str, str, Any], str] | None = None,
) -> Any:
    if source_ref_cache is None:
        resolved_sources = _validate_spawn_source(
            world=world,
            source=source,
            field_name=field_name,
        )
        canonical_refs = [
            f"{resolved.source_type}.{resolved.source_slug}"
            for resolved in resolved_sources
        ]
    else:
        canonical_refs = []
        aliases = {
            "itembundle": "itembundle",
            "item_bundle": "itembundle",
            "itemdefinition": "itemdefinition",
            "item_definition": "itemdefinition",
            "mobdefinition": "mobdefinition",
            "mob_definition": "mobdefinition",
        }
        for index, source_ref in enumerate(_spawn_source_refs(source)):
            text = str(source_ref or "").strip()
            prefix, separator, raw_value = text.partition(".")
            source_type = aliases.get(prefix.strip().lower()) if separator else None
            raw_value = raw_value.strip()
            ref_field = (
                f"{field_name}[{index}]"
                if isinstance(source, dict) and "pool" in source
                else field_name
            )
            if source_type is None or not raw_value:
                raise serializers.ValidationError(
                    f"{ref_field} must use a supported ref such as "
                    "mobdefinition.slug or itemdefinition.slug."
                )
            cache_key = (
                source_type,
                "id" if raw_value.isdigit() else "slug",
                int(raw_value) if raw_value.isdigit() else raw_value,
            )
            canonical_ref = source_ref_cache.get(cache_key)
            if canonical_ref is None:
                raise serializers.ValidationError(
                    f"{ref_field} does not resolve to authored content in "
                    f"{'the inherited base world' if world.instance_of_id else 'this world'}."
                )
            canonical_refs.append(canonical_ref)

    if isinstance(source, dict) and "pool" in source:
        canonical_pool = []
        for pool_entry, canonical_ref in zip(
            source.get("pool") or [],
            canonical_refs,
        ):
            if isinstance(pool_entry, dict):
                canonical_entry = copy.deepcopy(pool_entry)
                if "ref" in canonical_entry:
                    canonical_entry["ref"] = canonical_ref
                elif "source" in canonical_entry:
                    canonical_entry["source"] = canonical_ref
                else:
                    canonical_entry["ref"] = canonical_ref
                canonical_pool.append(canonical_entry)
            else:
                canonical_pool.append(canonical_ref)
        return {"pool": canonical_pool}

    if isinstance(source, dict):
        canonical = copy.deepcopy(source)
        field = "ref" if "ref" in canonical else "source"
        canonical[field] = canonical_refs[0]
        return canonical
    return canonical_refs[0]


def _serialize_spawn_entry(
    entry: SpawnEntry,
    *,
    world: World,
    room_ref_cache: dict[tuple[Any, ...], str],
    source_ref_cache: dict[tuple[str, str, Any], str],
) -> dict[str, Any]:
    targets = [
        ("room", entry.target_room_id),
        ("zone", entry.target_zone_id),
        ("path", entry.target_path_id),
        ("entry", entry.target_entry_id),
    ]
    populated_targets = [target_type for target_type, target_id in targets if target_id]
    if len(populated_targets) != 1:
        raise serializers.ValidationError(
            f"Spawn entry '{entry.slug}' must have exactly one target."
        )

    target_type = populated_targets[0]
    if target_type == "room":
        if entry.target_room.world_id != world.id:
            raise serializers.ValidationError(
                f"Spawn entry '{entry.slug}' targets a room outside this world."
            )
        target = _room_ref(entry.target_room)
    elif target_type == "zone":
        if entry.target_zone.world_id != world.id:
            raise serializers.ValidationError(
                f"Spawn entry '{entry.slug}' targets a zone outside this world."
            )
        target = _zone_ref(entry.target_zone)
    elif target_type == "path":
        if entry.target_path.world_id != world.id:
            raise serializers.ValidationError(
                f"Spawn entry '{entry.slug}' targets a path outside this world."
            )
        target = _path_ref(entry.target_path)
    else:
        target_entry = entry.target_entry
        if target_entry.plan_id != entry.plan_id:
            raise serializers.ValidationError(
                f"Spawn entry '{entry.slug}' targets an entry outside its spawn plan."
            )
        if target_entry.order >= entry.order:
            raise serializers.ValidationError(
                f"Spawn entry '{entry.slug}' must target an entry with a lower order."
            )
        if entry.is_active and not target_entry.is_active:
            raise serializers.ValidationError(
                f"Active spawn entry '{entry.slug}' must target an active entry."
            )
        target = f"{_SPAWN_ENTRY_REF_PREFIX}{target_entry.slug}"

    data: dict[str, Any] = {
        "slug": entry.slug,
        "order": int(entry.order),
        "target": target,
        "count": copy.deepcopy(entry.count),
    }
    if entry.name:
        data["name"] = entry.name
    if not entry.is_active:
        data["is_active"] = False
    source = _canonicalize_spawn_source_for_export(
        world=world,
        source=copy.deepcopy(entry.source),
        field_name=(
            f"Spawn plan '{entry.plan.slug}' entry '{entry.slug}' source"
        ),
        source_ref_cache=source_ref_cache,
    )
    if isinstance(source, dict) and "pool" in source:
        data["source_pool"] = source["pool"]
    else:
        data["source"] = source
    placement = copy.deepcopy(entry.placement or {})
    if isinstance(placement, dict):
        cohort = placement.get("cohort") or placement.get("cohort_slug")
        if cohort:
            placement.pop("cohort", None)
            placement.pop("cohort_slug", None)
            cohort_role = placement.pop("cohort_role", None) or placement.pop("role", None)
            cohort_policy = placement.pop("cohort_policy", None) or placement.pop("policy", None)
            if isinstance(cohort, dict):
                cohort_slug = (
                    cohort.get("slug")
                    or cohort.get("name")
                    or cohort.get("id")
                )
                if cohort_slug:
                    data["cohort"] = copy.deepcopy(cohort_slug)
                    nested_role = cohort_role or cohort.get("role") or cohort.get("cohort_role")
                    nested_policy = cohort_policy or cohort.get("policy") or cohort.get("cohort_policy")
                    if nested_role:
                        data["cohort_role"] = nested_role
                    if nested_policy:
                        data["cohort_policy"] = nested_policy
            else:
                data["cohort"] = cohort
                if cohort_role:
                    data["cohort_role"] = cohort_role
                if cohort_policy:
                    data["cohort_policy"] = cohort_policy
    if placement:
        data["placement"] = placement
    if entry.traits:
        data["traits"] = _canonicalize_nested_conditions(
            copy.deepcopy(entry.traits),
            world=world,
            entity_ref_cache=source_ref_cache,
            room_ref_cache=room_ref_cache,
        )
    if entry.initial_state:
        data["initial_state"] = copy.deepcopy(entry.initial_state)
    if entry.loot:
        data["loot"] = _canonicalize_nested_conditions(
            copy.deepcopy(entry.loot),
            world=world,
            entity_ref_cache=source_ref_cache,
            room_ref_cache=room_ref_cache,
        )
    if entry.conditions:
        data["conditions"] = _canonicalize_condition_refs(
            copy.deepcopy(entry.conditions),
            world=world,
            room_ref_cache=room_ref_cache,
        )
    return data


def _serialize_spawn_plan_manifest(
    spawn_plan: SpawnPlan,
    *,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    source_ref_cache: dict[tuple[str, str, Any], str] | None = None,
) -> dict[str, Any]:
    if room_ref_cache is None:
        room_ref_cache = _build_room_ref_cache(spawn_plan.world)
    if source_ref_cache is None:
        source_world = (
            spawn_plan.world.instance_of
            if spawn_plan.world.instance_of_id
            else spawn_plan.world
        )
        source_ref_cache = _build_entity_ref_cache(
            item_definitions=list(
                source_world.item_definitions.only("id", "slug")
            ),
            item_bundles=list(
                source_world.item_bundles.only("id", "slug")
            ),
            mob_definitions=list(
                source_world.mob_definitions.only("id", "slug")
            ),
        )
    entries = getattr(spawn_plan, "_export_entries", None)
    if entries is None:
        entries = spawn_plan.entries.select_related(
            "plan",
            "target_room",
            "target_zone",
            "target_path",
            "target_entry",
        ).order_by("order", "created_ts", "id")
    spec: dict[str, Any] = {
        "zone": _zone_ref(spawn_plan.zone),
        "order": int(spawn_plan.order),
        "is_active": bool(spawn_plan.is_active),
        "respawn": copy.deepcopy(spawn_plan.respawn_policy),
        "entries": [
            _serialize_spawn_entry(
                entry,
                world=spawn_plan.world,
                room_ref_cache=room_ref_cache,
                source_ref_cache=source_ref_cache,
            )
            for entry in entries
        ],
    }
    if spawn_plan.randomization:
        spec["randomization"] = copy.deepcopy(spawn_plan.randomization)
    if spawn_plan.conditions:
        spec["conditions"] = _canonicalize_condition_refs(
            copy.deepcopy(spawn_plan.conditions),
            world=spawn_plan.world,
            room_ref_cache=room_ref_cache,
        )
    return {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
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
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    source_ref_cache: dict[tuple[str, str, Any], str] | None = None,
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
        manifest = _serialize_spawn_plan_manifest(
            spawn_plan,
            room_ref_cache=room_ref_cache,
            source_ref_cache=source_ref_cache,
        )
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
    try:
        manifest["spec"] = normalize_ability_definition(
            manifest["spec"],
            slug=ability.slug,
            name=ability.name,
        )
    except AbilityValidationError as exc:
        raise serializers.ValidationError(
            f"Ability '{ability.slug}' cannot be exported: {exc}"
        ) from exc
    return manifest


def _serialize_social_manifest(social: Social) -> dict[str, Any]:
    return _portable_authored_manifest(
        builder_manifests.social_to_manifest(social)
    )


def _build_entity_ref_cache(
    *,
    item_definitions: list[ItemDefinition],
    item_bundles: list[ItemBundle] | None = None,
    mob_definitions: list[MobDefinition],
) -> dict[tuple[str, str, Any], str]:
    cache: dict[tuple[str, str, Any], str] = {}
    for entity_type, definitions in (
        ("itemdefinition", item_definitions),
        ("itembundle", item_bundles or []),
        ("mobdefinition", mob_definitions),
    ):
        for definition in definitions:
            if not definition.slug:
                continue
            canonical = f"{entity_type}.{definition.slug}"
            cache[(entity_type, "id", definition.id)] = canonical
            cache[(entity_type, "slug", definition.slug)] = canonical
    return cache


def build_manifest_semantic_ref_caches(
    world: World,
) -> tuple[
    dict[tuple[str, str, Any], str],
    dict[tuple[Any, ...], str],
]:
    """Preload portable semantic-reference caches for list serializers."""

    definition_world_id = world.instance_of_id or world.id
    entity_ref_cache = _build_entity_ref_cache(
        item_definitions=list(
            ItemDefinition.objects.filter(
                world_id=definition_world_id,
            ).only("id", "slug")
        ),
        item_bundles=list(
            ItemBundle.objects.filter(
                world_id=definition_world_id,
            ).only("id", "slug")
        ),
        mob_definitions=list(
            MobDefinition.objects.filter(
                world_id=definition_world_id,
            ).only("id", "slug")
        ),
    )
    return entity_ref_cache, _build_room_ref_cache(world)


def _entity_ref_cache_key(
    value: Any,
    *,
    expected_type: str,
) -> tuple[str, str, Any] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return expected_type, "id", value

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return expected_type, "id", int(text)

    prefix, separator, raw_value = text.partition(".")
    if separator:
        if quest_entity_refs.canonical_entity_type(prefix) != expected_type:
            return None
        text = raw_value.strip()
        if not text:
            return None
        # Explicitly typed definition refs are canonical slug refs, including
        # numeric-only slugs. Bare numeric values above remain the legacy id
        # form.
    return expected_type, "slug", text


def _canonicalize_entity_ref(
    value: Any,
    *,
    world: World,
    expected_type: str,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
) -> Any:
    if value in (None, "", [], {}):
        return value
    if quest_entity_refs.is_dynamic_reference(value):
        return value

    if entity_ref_cache is not None:
        cache_key = _entity_ref_cache_key(value, expected_type=expected_type)
        if cache_key is None:
            return value
        return entity_ref_cache.get(cache_key, value)

    entity_id = quest_entity_refs.resolve_entity_ref_id(
        world=world,
        value=value,
        expected_type=expected_type,
    )
    if entity_id is None:
        return value

    model_cls = ItemDefinition if expected_type == "itemdefinition" else MobDefinition
    entity = model_cls.objects.filter(world=world, pk=entity_id).first()
    if not entity or not entity.slug:
        return value
    return f"{expected_type}.{entity.slug}"


def _build_room_ref_cache(
    world: World,
    *,
    rooms: list[Room] | None = None,
) -> dict[tuple[Any, ...], str]:
    if rooms is None:
        rooms = list(
            Room.objects.filter(world=world).only(
                "id",
                "relative_id",
                "name",
                "x",
                "y",
                "z",
            )
        )
    cache: dict[tuple[Any, ...], str] = {}
    for room in sorted(rooms, key=lambda candidate: candidate.id):
        canonical = _room_ref(room)
        cache[("database_id", room.id)] = canonical
        cache[("relative_id", room.relative_id)] = canonical
        cache[("coordinates", room.x, room.y, room.z)] = canonical
        if room.name:
            cache.setdefault(("name", room.name), canonical)
    return cache


def _room_ref_cache_key(
    value: Any,
    *,
    allow_bare_database_id: bool = False,
) -> tuple[Any, ...] | None:
    if isinstance(value, bool):
        return None
    is_bare_numeric, bare_database_id = _parse_bare_database_id(value)
    if is_bare_numeric:
        if allow_bare_database_id and bare_database_id is not None:
            return ("database_id", bare_database_id)
        return None
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_room_reference(text)
    if parsed is None:
        return None
    if parsed.kind == "relative_id":
        return ("relative_id", parsed.relative_id)
    if parsed.kind == "database_id":
        return ("database_id", parsed.database_id)
    return ("coordinates", parsed.x, parsed.y, parsed.z)


def _parse_bare_database_id(value: Any) -> tuple[bool, int | None]:
    """Recognize legacy bare IDs without unbounded or Unicode integer parsing."""

    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        return True, value if 0 < value <= BIGINT_MAX else None
    text = str(value or "").strip()
    if not text.isdigit():
        return False, None
    if not text.isascii() or not text.isdecimal():
        return True, None
    significant_digits = text.lstrip("0") or "0"
    if len(significant_digits) > len(str(BIGINT_MAX)):
        return True, None
    try:
        parsed = int(significant_digits)
    except ValueError:
        return True, None
    return True, parsed if 0 < parsed <= BIGINT_MAX else None


def _canonicalize_room_ref(
    value: Any,
    *,
    world: World,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    allow_bare_database_id: bool = False,
) -> Any:
    if value in (None, "", [], {}):
        return value
    if quest_entity_refs.is_dynamic_reference(value):
        return value

    is_bare_numeric, bare_database_id = _parse_bare_database_id(value)
    if is_bare_numeric:
        if not allow_bare_database_id:
            raise serializers.ValidationError(
                "Bare numeric room references are ambiguous; use "
                "'room@<relative_id>'."
            )
        if bare_database_id is None:
            raise serializers.ValidationError(
                "Legacy bare database room references must be supported "
                "positive 64-bit integers."
            )
        value = f"room.{bare_database_id}"

    cache_key = _room_ref_cache_key(
        value,
        allow_bare_database_id=allow_bare_database_id,
    )
    if room_ref_cache is not None and cache_key is not None:
        canonical = room_ref_cache.get(cache_key)
        if canonical is not None:
            return canonical
        raise serializers.ValidationError(
            f"Room reference '{value}' does not resolve in this world."
        )

    parsed = parse_room_reference(value)
    if parsed is None:
        return value
    room = resolve_room_reference(world, value)
    if room is None:
        raise serializers.ValidationError(
            f"Room reference '{value}' does not resolve in this world."
        )
    return _room_ref(room)


def _canonicalize_condition_refs(
    condition: Any,
    *,
    world: World,
    event_target_is_room: bool = False,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    canonicalize_entities: bool = True,
    allow_bare_room_database_ids: bool = False,
) -> Any:
    if condition in (None, {}, []):
        return condition
    if isinstance(condition, list):
        return [
            _canonicalize_condition_refs(
                item,
                world=world,
                event_target_is_room=event_target_is_room,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                canonicalize_entities=canonicalize_entities,
                allow_bare_room_database_ids=allow_bare_room_database_ids,
            )
            for item in condition
        ]
    if not isinstance(condition, dict):
        return condition

    canonical = copy.deepcopy(condition)

    if "mob_present" in canonical and canonicalize_entities:
        spec = canonical.get("mob_present")
        if isinstance(spec, dict):
            canonical_spec = {
                **spec,
                "ref": _canonicalize_entity_ref(
                    spec.get("ref"),
                    world=world,
                    expected_type="mobdefinition",
                    entity_ref_cache=entity_ref_cache,
                ),
            }
            if "where" in spec:
                canonical_spec["where"] = _canonicalize_condition_refs(
                    spec.get("where"),
                    world=world,
                    event_target_is_room=event_target_is_room,
                    entity_ref_cache=entity_ref_cache,
                    room_ref_cache=room_ref_cache,
                    canonicalize_entities=canonicalize_entities,
                    allow_bare_room_database_ids=allow_bare_room_database_ids,
                )
            canonical["mob_present"] = canonical_spec
        else:
            canonical["mob_present"] = _canonicalize_entity_ref(
                spec,
                world=world,
                expected_type="mobdefinition",
                entity_ref_cache=entity_ref_cache,
            )

    elif isinstance(canonical.get("mob_present"), dict):
        spec = canonical["mob_present"]
        if "where" in spec:
            canonical["mob_present"] = {
                **spec,
                "where": _canonicalize_condition_refs(
                    spec.get("where"),
                    world=world,
                    event_target_is_room=event_target_is_room,
                    entity_ref_cache=entity_ref_cache,
                    room_ref_cache=room_ref_cache,
                    canonicalize_entities=canonicalize_entities,
                    allow_bare_room_database_ids=allow_bare_room_database_ids,
                ),
            }

    if "item_present" in canonical and canonicalize_entities:
        spec = canonical.get("item_present")
        if isinstance(spec, dict):
            canonical["item_present"] = {
                **spec,
                "item": _canonicalize_entity_ref(
                    spec.get("item"),
                    world=world,
                    expected_type="itemdefinition",
                    entity_ref_cache=entity_ref_cache,
                ),
            }

    if "all" in canonical:
        child_conditions = canonical.get("all")
        canonical["all"] = _canonicalize_condition_refs(
            child_conditions,
            world=world,
            event_target_is_room=event_target_is_room or quest_manifests._condition_list_targets_room(child_conditions),
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )
    if "any" in canonical:
        canonical["any"] = _canonicalize_condition_refs(
            canonical.get("any"),
            world=world,
            event_target_is_room=event_target_is_room,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )
    if "not" in canonical:
        canonical["not"] = _canonicalize_condition_refs(
            canonical.get("not"),
            world=world,
            event_target_is_room=event_target_is_room,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )

    for operator in ("eq", "ne", "gte", "lte", "in"):
        raw_args = canonical.get(operator)
        if not isinstance(raw_args, list) or len(raw_args) != 2:
            continue

        left_path = raw_args[0]
        right_value = raw_args[1]
        uses_room_ref = str(left_path or "").strip() in {
            "actor.room_id",
            "actor.room.id",
            "player.room_id",
            "player.room.id",
        } or quest_manifests._condition_uses_room_ref(
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
                        _canonicalize_room_ref(
                            candidate,
                            world=world,
                            room_ref_cache=room_ref_cache,
                            allow_bare_database_id=allow_bare_room_database_ids,
                        )
                        for candidate in right_value
                    ],
                ]
            else:
                canonical[operator] = [
                    left_path,
                    _canonicalize_room_ref(
                        right_value,
                        world=world,
                        room_ref_cache=room_ref_cache,
                        allow_bare_database_id=allow_bare_room_database_ids,
                    ),
                ]
            continue

        if not canonicalize_entities:
            continue

        expected_type = quest_manifests._condition_expected_entity_type(left_path, right_value)
        if expected_type is None:
            expected_type = quest_manifests._condition_expected_entity_type(left_path)
        if not expected_type:
            continue

        if operator == "in" and isinstance(right_value, list):
            canonical[operator] = [
                left_path,
                [
                    _canonicalize_entity_ref(
                        candidate,
                        world=world,
                        expected_type=expected_type,
                        entity_ref_cache=entity_ref_cache,
                    )
                    for candidate in right_value
                ],
            ]
        else:
            canonical[operator] = [
                left_path,
                _canonicalize_entity_ref(
                    right_value,
                    world=world,
                    expected_type=expected_type,
                    entity_ref_cache=entity_ref_cache,
                ),
            ]

    return canonical


def _canonicalize_nested_conditions(
    value: Any,
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    canonicalize_entities: bool = True,
    allow_bare_room_database_ids: bool = False,
) -> Any:
    """Canonicalize semantic refs embedded in authored JSON structures."""

    if isinstance(value, list):
        return [
            _canonicalize_nested_conditions(
                item,
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
                canonicalize_entities=canonicalize_entities,
                allow_bare_room_database_ids=allow_bare_room_database_ids,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    if any(
        operator in value
        for operator in ("all", "any", "not", "eq", "ne", "gte", "lte", "in")
    ):
        return _canonicalize_condition_refs(
            value,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )
    canonical: dict[str, Any] = {}
    for key, child in value.items():
        if key in {"conditions", "when"}:
            if isinstance(child, (dict, list)):
                canonical[key] = _canonicalize_condition_refs(
                    child,
                    world=world,
                    entity_ref_cache=entity_ref_cache,
                    room_ref_cache=room_ref_cache,
                    canonicalize_entities=canonicalize_entities,
                    allow_bare_room_database_ids=allow_bare_room_database_ids,
                )
            elif isinstance(child, str):
                canonical[key] = _canonicalize_semantic_command_value(
                    child,
                    world=world,
                    room_ref_cache=room_ref_cache,
                    base_room_ref_cache=base_room_ref_cache,
                )
            else:
                canonical[key] = child
        elif key in {
            "command",
            "commands",
            "script",
            "on_use_cmd",
            "combat_script",
        }:
            canonical[key] = _canonicalize_semantic_command_value(
                child,
                world=world,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
            )
        elif (
            key in {
                "death_room",
                "destination",
                "room",
                "room_id",
                "room_ref",
                "starting_room",
                "to_room",
            }
            and _room_ref_cache_key(
                child,
                allow_bare_database_id=allow_bare_room_database_ids,
            ) is not None
        ):
            canonical[key] = _canonicalize_room_ref(
                child,
                world=world,
                room_ref_cache=room_ref_cache,
                allow_bare_database_id=allow_bare_room_database_ids,
            )
        else:
            canonical[key] = _canonicalize_nested_conditions(
                child,
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
                canonicalize_entities=canonicalize_entities,
                allow_bare_room_database_ids=allow_bare_room_database_ids,
            )
    return canonical


def _canonicalize_semantic_command_value(
    value: Any,
    *,
    world: World,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> Any:
    if isinstance(value, str):
        try:
            return canonicalize_command_room_references_in_text(
                world,
                value,
                strict=True,
                canonical_ref_cache=room_ref_cache,
                base_canonical_ref_cache=base_room_ref_cache,
            )
        except RoomReferenceError as exc:
            raise serializers.ValidationError(str(exc)) from exc
    if isinstance(value, list):
        return [
            _canonicalize_semantic_command_value(
                item,
                world=world,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _canonicalize_semantic_command_value(
                child,
                world=world,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
            )
            for key, child in value.items()
        }
    return value


def canonicalize_manifest_for_export(
    *,
    manifest: dict[str, Any],
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    include_api_version: bool = False,
) -> dict[str, Any]:
    """Return one authored manifest with portable semantic references."""

    canonical = copy.deepcopy(manifest)
    spec = canonical.get("spec")
    if isinstance(spec, dict):
        canonical["spec"] = _canonicalize_nested_conditions(
            spec,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            base_room_ref_cache=base_room_ref_cache,
            allow_bare_room_database_ids=True,
        )
    if include_api_version:
        canonical["apiVersion"] = CANONICAL_MANIFEST_API_VERSION
    return canonical


def _canonicalize_trigger_steps(
    steps: Any,
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    allow_bare_room_database_ids: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    canonical_steps = copy.deepcopy(steps)
    for step in canonical_steps:
        if not isinstance(step, dict):
            continue
        actions = step.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            if "item" in action:
                action["item"] = _canonicalize_entity_ref(
                    action.get("item"),
                    world=world,
                    expected_type="itemdefinition",
                    entity_ref_cache=entity_ref_cache,
                )
            if "with" in action:
                action["with"] = _canonicalize_entity_ref(
                    action.get("with"),
                    world=world,
                    expected_type="itemdefinition",
                    entity_ref_cache=entity_ref_cache,
                )
            if "mob" in action:
                action["mob"] = _canonicalize_entity_ref(
                    action.get("mob"),
                    world=world,
                    expected_type="mobdefinition",
                    entity_ref_cache=entity_ref_cache,
                )
            if "where" in action:
                action["where"] = _canonicalize_condition_refs(
                    action.get("where"),
                    world=world,
                    entity_ref_cache=entity_ref_cache,
                    room_ref_cache=room_ref_cache,
                    allow_bare_room_database_ids=allow_bare_room_database_ids,
                )
            if isinstance(action.get("command"), str):
                try:
                    action["command"] = canonicalize_command_room_references_in_text(
                        world,
                        action["command"],
                        strict=True,
                        canonical_ref_cache=room_ref_cache,
                        base_canonical_ref_cache=base_room_ref_cache,
                    )
                except RoomReferenceError as exc:
                    raise serializers.ValidationError(str(exc)) from exc
                command_tokens = action["command"].split()
                is_bare_transfer_destination = (
                    command_tokens
                    and command_tokens[0].lower() in {"/transfer", "transfer"}
                    and command_tokens[-1].isascii()
                    and command_tokens[-1].isdecimal()
                )
                if is_bare_transfer_destination:
                    if not allow_bare_room_database_ids:
                        raise serializers.ValidationError(
                            "Typed Trigger-step /transfer destinations must use "
                            "'room@<relative_id>'; bare numeric destinations are "
                            "ambiguous."
                        )
                    canonical_destination = _canonicalize_room_ref(
                        f"room@{command_tokens[-1]}",
                        world=world,
                        room_ref_cache=room_ref_cache,
                    )
                    command_prefix = action["command"].rsplit(None, 1)[0]
                    action["command"] = (
                        f"{command_prefix} {canonical_destination}"
                    )
            subject = action.get("subject")
            if (
                isinstance(subject, dict)
                and str(subject.get("type") or "").strip().lower() == "mob"
            ):
                subject_room = subject.get("room")
                if str(subject_room or "").strip().lower() != "trigger_room":
                    if _room_ref_cache_key(
                        subject_room,
                        allow_bare_database_id=allow_bare_room_database_ids,
                    ) is None:
                        raise serializers.ValidationError(
                            "Trigger command mob subject room must be "
                            "'trigger_room' or a typed room reference."
                        )
                    subject["room"] = _canonicalize_room_ref(
                        subject_room,
                        world=world,
                        room_ref_cache=room_ref_cache,
                        allow_bare_database_id=allow_bare_room_database_ids,
                    )
                subject["mob"] = _canonicalize_entity_ref(
                    subject.get("mob"),
                    world=world,
                    expected_type="mobdefinition",
                    entity_ref_cache=entity_ref_cache,
                )
                if "where" in subject:
                    subject["where"] = _canonicalize_condition_refs(
                        subject.get("where"),
                        world=world,
                        entity_ref_cache=entity_ref_cache,
                        room_ref_cache=room_ref_cache,
                        allow_bare_room_database_ids=allow_bare_room_database_ids,
                    )
    return canonical_steps


def _canonicalize_quest_node(
    node: Any,
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    canonicalize_entities: bool = True,
    allow_bare_room_database_ids: bool = False,
) -> Any:
    if isinstance(node, list):
        return [
            _canonicalize_quest_node(
                item,
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                canonicalize_entities=canonicalize_entities,
                allow_bare_room_database_ids=allow_bare_room_database_ids,
            )
            for item in node
        ]

    if not isinstance(node, dict):
        return node

    if any(key in node for key in ("all", "any", "not", "eq", "ne", "gte", "lte", "in")):
        return _canonicalize_condition_refs(
            node,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )

    canonical = {}
    for key, value in node.items():
        if (
            key == "command"
            and isinstance(value, str)
            and str(node.get("type") or "").strip().lower().endswith("_command")
        ):
            try:
                canonical[key] = canonicalize_command_room_references_in_text(
                    world,
                    value,
                    strict=True,
                    canonical_ref_cache=room_ref_cache,
                )
            except RoomReferenceError as exc:
                raise serializers.ValidationError(str(exc)) from exc
            continue
        if key in {"room", "room_id"} and (
            str(node.get("type") or "").strip().lower() == "room_prompt"
            or "item_definition" in node
            or "item_definition_id" in node
        ):
            canonical["room"] = _canonicalize_room_ref(
                value,
                world=world,
                room_ref_cache=room_ref_cache,
                allow_bare_database_id=allow_bare_room_database_ids,
            )
            continue
        if canonicalize_entities and key in {"mob_definition", "mob_definition_id"}:
            canonical["mob_definition"] = _canonicalize_entity_ref(
                value,
                world=world,
                expected_type="mobdefinition",
                entity_ref_cache=entity_ref_cache,
            )
            continue
        if canonicalize_entities and key in {"item_definition", "item_definition_id"}:
            canonical["item_definition"] = _canonicalize_entity_ref(
                value,
                world=world,
                expected_type="itemdefinition",
                entity_ref_cache=entity_ref_cache,
            )
            continue
        if canonicalize_entities and key in {"entity", "value"} and isinstance(value, str):
            prefix, sep, _ = value.strip().partition(".")
            expected_type = quest_entity_refs.canonical_entity_type(prefix) if sep == "." else None
            if expected_type:
                canonical[key] = _canonicalize_entity_ref(
                    value,
                    world=world,
                    expected_type=expected_type,
                    entity_ref_cache=entity_ref_cache,
                )
                continue
        canonical[key] = _canonicalize_quest_node(
            value,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=canonicalize_entities,
            allow_bare_room_database_ids=allow_bare_room_database_ids,
        )
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


def _serialize_quest_manifest(
    quest: QuestTemplate,
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str],
) -> dict[str, Any]:
    manifest = quest_manifests.quest_template_to_manifest(quest)
    spec = _canonicalize_quest_node(
        copy.deepcopy(manifest["spec"]),
        world=world,
        entity_ref_cache=entity_ref_cache,
        room_ref_cache=room_ref_cache,
        allow_bare_room_database_ids=True,
    )
    spec = quest_manifests.QuestSpec.model_validate(spec).model_dump()
    return {
        "kind": quest_manifests.QUEST_MANIFEST_KIND,
        "metadata": {
            "slug": quest.slug,
            "name": quest.name,
        },
        "spec": spec,
    }


def _serialize_trigger_target(trigger: Trigger, *, world: World) -> str:
    field_name = f"Trigger '{trigger.name}' target"
    if trigger.world_id != world.id:
        raise serializers.ValidationError(
            f"{field_name} does not belong to the selected world."
        )
    if not trigger.target_type_id or not trigger.target_id:
        raise serializers.ValidationError(f"{field_name} is missing.")

    target_model = trigger.target_type.model_class()
    target = trigger.target
    if target_model is None or target is None:
        raise serializers.ValidationError(f"{field_name} is dangling.")

    if target_model == Room:
        return _room_ref(
            target,
            world=trigger.world_id,
            field_name=field_name,
        )
    if target_model == Zone:
        if target.world_id != trigger.world_id:
            raise serializers.ValidationError(
                f"{field_name} points to a zone outside this world."
            )
        relative_id = target.relative_id
        if (
            isinstance(relative_id, bool)
            or not isinstance(relative_id, int)
            or relative_id <= 0
        ):
            raise serializers.ValidationError(
                f"{field_name} zone must have a positive relative_id."
            )
        return f"{_ZONE_REF_PREFIX}{relative_id}"
    if target_model == World:
        if target.id != trigger.world_id:
            raise serializers.ValidationError(
                f"{field_name} must point to the trigger's world."
            )
        return "world"

    definition_world_id = world.instance_of_id or world.id
    if target_model == MobDefinition:
        if target.world_id != definition_world_id:
            raise serializers.ValidationError(
                f"{field_name} points outside the authored definition world."
            )
        target_ref = _mob_definition_ref(target)
    elif target_model == ItemDefinition:
        if target.world_id != definition_world_id:
            raise serializers.ValidationError(
                f"{field_name} points outside the authored definition world."
            )
        target_ref = _item_definition_ref(target)
    else:
        raise serializers.ValidationError(
            f"{field_name} uses unsupported type "
            f"'{trigger.target_type.model}'."
        )
    if not target_ref:
        raise serializers.ValidationError(
            f"{field_name} must have a portable slug."
        )
    return target_ref


def _serialize_trigger_manifest(
    trigger: Trigger,
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
    base_room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    conditions = builder_manifests._deserialize_conditions_payload(
        trigger.conditions,
    )
    if isinstance(conditions, (dict, list)):
        conditions = _canonicalize_condition_refs(
            conditions,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            allow_bare_room_database_ids=True,
        )
    script = trigger.script or ""
    if script:
        try:
            script = canonicalize_command_room_references_in_text(
                world,
                script,
                strict=True,
                canonical_ref_cache=room_ref_cache,
                base_canonical_ref_cache=base_room_ref_cache,
            )
        except RoomReferenceError as exc:
            raise serializers.ValidationError(str(exc)) from exc
    return {
        "kind": builder_manifests.TRIGGER_MANIFEST_KIND,
        "metadata": {
            "name": trigger.name or "",
        },
        "spec": {
            "scope": trigger.scope,
            "kind": builder_manifests._canonical_trigger_kind(trigger.kind),
            "target": _serialize_trigger_target(trigger, world=world),
            "match": trigger.match or "",
            "script": script,
            "steps": _canonicalize_trigger_steps(
                copy.deepcopy(trigger.steps or []),
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
                allow_bare_room_database_ids=True,
            ),
            "on_step_error": trigger.on_step_error or "cancel",
            "conditions": conditions,
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
        room_reference_mode="manifest",
    )


def serialize_world_documents(world: World) -> list[dict[str, Any]]:
    if not world.config:
        raise serializers.ValidationError("World has no config to export.")

    item_definitions = list(
        world.item_definitions.select_related("currency").prefetch_related(
            Prefetch(
                "salvage_yields",
                queryset=ItemSalvageYield.objects.select_related("material"),
            ),
        ).order_by("slug", "id")
    )
    mob_definitions = list(
        world.mob_definitions.select_related(
            "merchant_profile",
            "crafting_profile",
            "trainer_profile",
        ).prefetch_related(
            "currency_rewards__currency",
            Prefetch(
                "faction_assignments",
                queryset=FactionAssignment.objects.select_related("faction"),
            ),
        ).order_by("slug", "id")
    )
    item_bundles = list(
        world.item_bundles.prefetch_related(
            "entries__item_definition",
        ).order_by("slug", "id")
    )
    if world.instance_of_id:
        ref_item_definitions = list(
            ItemDefinition.objects.filter(world_id=world.instance_of_id).only(
                "id",
                "slug",
            )
        )
        ref_mob_definitions = list(
            MobDefinition.objects.filter(world_id=world.instance_of_id).only(
                "id",
                "slug",
            )
        )
        ref_item_bundles = list(
            ItemBundle.objects.filter(world_id=world.instance_of_id).only(
                "id",
                "slug",
            )
        )
    else:
        ref_item_definitions = item_definitions
        ref_item_bundles = item_bundles
        ref_mob_definitions = mob_definitions
    entity_ref_cache = _build_entity_ref_cache(
        item_definitions=ref_item_definitions,
        item_bundles=ref_item_bundles,
        mob_definitions=ref_mob_definitions,
    )
    rooms = list(
        world.rooms.prefetch_related(
            Prefetch(
                "flags",
                queryset=RoomFlag.objects.order_by("code", "id"),
                to_attr="_export_flags",
            ),
            Prefetch(
                "details",
                queryset=RoomDetail.objects.order_by("created_ts", "id"),
                to_attr="_export_details",
            ),
            Prefetch(
                "doors_from",
                queryset=Door.objects.select_related(
                    "doorway__key",
                    "to_room",
                ).order_by("direction", "id"),
                to_attr="_export_door_faces",
            ),
        ).select_related(
            "zone",
            "north",
            "east",
            "south",
            "west",
            "up",
            "down",
            "merchant_profile",
            "crafting_profile",
            "trainer_profile",
        ).order_by("z", "y", "x", "id")
    )
    room_ref_cache = _build_room_ref_cache(world, rooms=rooms)
    triggers = list(
        world.triggers.select_related(
            "target_type"
        ).prefetch_related("target").order_by(
            "scope", "order", "created_ts", "id"
        )
    )
    trigger_uses_base_room_ref = any(
        "world@base/" in str(trigger.script or "").lower()
        or "world@base/" in json.dumps(trigger.steps or []).lower()
        for trigger in triggers
    )
    base_room_ref_cache = (
        _build_room_ref_cache(world.instance_of)
        if world.instance_of_id and trigger_uses_base_room_ref
        else None
    )
    zones = list(
        world.zones.select_related("center").order_by("name", "id")
    )
    paths = list(
        world.paths.select_related("zone", "entry_room").prefetch_related(
            Prefetch(
                "path_rooms",
                queryset=PathRoom.objects.select_related("room").order_by("id"),
                to_attr="_export_path_rooms",
            ),
        ).order_by("zone__name", "relative_id", "id")
    )
    spawn_plans = list(
        world.spawn_plans.select_related("zone").prefetch_related(
            Prefetch(
                "entries",
                queryset=SpawnEntry.objects.select_related(
                    "plan",
                    "target_room",
                    "target_zone",
                    "target_path",
                    "target_entry",
                ).order_by("order", "created_ts", "id"),
                to_attr="_export_entries",
            ),
        ).order_by("zone__name", "order", "slug", "id")
    )

    documents = [
        *[
            _serialize_currency_manifest(currency)
            for currency in world.currencies.all().order_by("code", "id")
        ],
        *[
            _serialize_craft_material_manifest(material)
            for material in world.craft_materials.all().order_by("order", "name", "id")
        ],
        *[
            _serialize_item_definition_manifest(item_definition)
            for item_definition in item_definitions
        ],
        *[
            _serialize_item_bundle_manifest(item_bundle)
            for item_bundle in item_bundles
        ],
        *[
            _serialize_merchant_profile_manifest(merchant_profile)
            for merchant_profile in world.merchant_profiles.prefetch_related(
                "stock_slots__item_definition",
                "stock_slots__item_bundle",
            ).select_related("settlement_currency").order_by("slug", "id")
        ],
        *[
            _serialize_crafting_recipe_manifest(recipe)
            for recipe in world.crafting_recipes.select_related(
                "output_item_definition",
                "currency",
            ).prefetch_related(
                Prefetch(
                    "ingredients",
                    queryset=CraftingIngredient.objects.select_related("material"),
                ),
            ).order_by("group", "order", "slug", "id")
        ],
        *[
            _serialize_crafting_profile_manifest(profile)
            for profile in world.crafting_profiles.prefetch_related(
                Prefetch(
                    "recipe_entries",
                    queryset=CraftingProfileRecipe.objects.select_related("recipe"),
                ),
            ).order_by("slug", "id")
        ],
        *[
            _serialize_ability_manifest(ability)
            for ability in world.ability_definitions.all().order_by("slug", "id")
        ],
        *[
            _serialize_trainer_profile_manifest(profile)
            for profile in world.trainer_profiles.prefetch_related(
                Prefetch(
                    "ability_entries",
                    queryset=TrainerProfileAbility.objects.select_related("ability"),
                ),
            ).order_by("slug", "id")
        ],
        *[
            _serialize_faction_manifest(faction)
            for faction in world.world_factions.prefetch_related("ranks").select_related(
                "starting_room",
                "death_room",
            ).order_by("type", "code", "id")
        ],
        *[
            _serialize_zone_manifest(zone)
            for zone in zones
        ],
        *[
            _serialize_room_manifest(room)
            for room in rooms
        ],
        *[
            _serialize_path_manifest(path)
            for path in paths
        ],
        *[
            _serialize_mob_definition_manifest(mob_definition)
            for mob_definition in mob_definitions
        ],
        *[
            _serialize_spawn_plan_manifest(
                spawn_plan,
                room_ref_cache=room_ref_cache,
                source_ref_cache=entity_ref_cache,
            )
            for spawn_plan in spawn_plans
        ],
        *[
            _serialize_social_manifest(social)
            for social in world.socials.all().order_by("cmd", "id")
        ],
        *[
            _serialize_quest_arc_manifest(quest_arc)
            for quest_arc in world.quest_arc_templates.all().order_by("slug", "id")
        ],
        *[
            _serialize_quest_manifest(
                quest,
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
            )
            for quest in world.quest_templates.select_related("arc").all().order_by("slug", "id")
        ],
        *[
            _serialize_trigger_manifest(
                trigger,
                world=world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
                base_room_ref_cache=base_room_ref_cache,
            )
            for trigger in triggers
        ],
        _serialize_world_manifest(world),
    ]
    return [
        canonicalize_manifest_for_export(
            manifest=document,
            world=world,
            entity_ref_cache=entity_ref_cache,
            room_ref_cache=room_ref_cache,
            base_room_ref_cache=base_room_ref_cache,
            include_api_version=True,
        )
        for document in documents
    ]


def normalize_manifest_room_references_for_import(
    *,
    world: World,
    manifest: dict[str, Any],
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    """Normalize condition DSL room refs before any manifest parser stores JSON."""

    if room_ref_cache is None:
        room_ref_cache = _build_room_ref_cache(world)
    normalized = copy.deepcopy(manifest)
    spec = normalized.get("spec")
    if isinstance(spec, dict):
        normalized["spec"] = _canonicalize_nested_conditions(
            spec,
            world=world,
            room_ref_cache=room_ref_cache,
            canonicalize_entities=False,
        )
    return normalized


def _summarize_documents(documents: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "documents": len(documents),
        "currencies": 0,
        "craft_materials": 0,
        "crafting_recipes": 0,
        "crafting_profiles": 0,
        "trainer_profiles": 0,
        "zones": 0,
        "rooms": 0,
        "paths": 0,
        "item_definitions": 0,
        "item_bundles": 0,
        "merchant_profiles": 0,
        "factions": 0,
        "mob_definitions": 0,
        "spawn_plans": 0,
        "abilities": 0,
        "socials": 0,
        "quest_arcs": 0,
        "quests": 0,
        "triggers": 0,
    }
    for document in documents:
        kind = parse_document_kind(document)
        if kind == CURRENCY_MANIFEST_KIND:
            counts["currencies"] += 1
        elif kind == builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND:
            counts["craft_materials"] += 1
        elif kind == builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND:
            counts["crafting_recipes"] += 1
        elif kind == builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND:
            counts["crafting_profiles"] += 1
        elif kind == builder_manifests.TRAINER_PROFILE_MANIFEST_KIND:
            counts["trainer_profiles"] += 1
        elif kind == ZONE_MANIFEST_KIND:
            counts["zones"] += 1
        elif kind == ROOM_MANIFEST_KIND:
            counts["rooms"] += 1
        elif kind == PATH_MANIFEST_KIND:
            counts["paths"] += 1
        elif kind == builder_manifests.ITEM_DEFINITION_MANIFEST_KIND:
            counts["item_definitions"] += 1
        elif kind == builder_manifests.ITEM_BUNDLE_MANIFEST_KIND:
            counts["item_bundles"] += 1
        elif kind == builder_manifests.MERCHANT_PROFILE_MANIFEST_KIND:
            counts["merchant_profiles"] += 1
        elif kind == builder_manifests.FACTION_MANIFEST_KIND:
            counts["factions"] += 1
        elif kind == builder_manifests.MOB_DEFINITION_MANIFEST_KIND:
            counts["mob_definitions"] += 1
        elif kind == SPAWN_PLAN_MANIFEST_KIND:
            counts["spawn_plans"] += 1
        elif kind == builder_manifests.ABILITY_MANIFEST_KIND:
            counts["abilities"] += 1
        elif kind == SOCIAL_MANIFEST_KIND:
            counts["socials"] += 1
        elif kind == quest_manifests.QUEST_ARC_MANIFEST_KIND:
            counts["quest_arcs"] += 1
        elif kind == quest_manifests.QUEST_MANIFEST_KIND:
            counts["quests"] += 1
        elif kind == builder_manifests.TRIGGER_MANIFEST_KIND:
            counts["triggers"] += 1
    return counts


def validate_room_door_stream_consistency(documents: list[dict[str, Any]]) -> None:
    """Reject conflicting reciprocal door settings before a batch mutates data."""
    faces: dict[tuple[str, str, str], tuple[str, bool, str]] = {}
    for document in documents:
        if parse_document_kind(document) != ROOM_MANIFEST_KIND:
            continue
        metadata = _manifest_metadata(document)
        spec = _manifest_spec(document)
        origin_ref = str(metadata.get("ref") or "").strip().lower()
        door_entries = spec.get("doors")
        if not origin_ref or not isinstance(door_entries, list):
            continue
        for door in door_entries:
            if not isinstance(door, dict):
                continue
            direction = str(door.get("direction") or "").strip().lower()
            to_room_ref = str(door.get("to_room") or "").strip().lower()
            if direction not in adv_consts.REVERSE_DIRECTIONS or not to_room_ref:
                continue
            faces[(origin_ref, to_room_ref, direction)] = (
                str(door.get("key") or "").strip().lower(),
                bool(door.get("destroy_key")),
                str(
                    door.get(
                        "default_state",
                        adv_consts.DOOR_STATE_CLOSED,
                    )
                ).strip().lower(),
            )

    checked: set[tuple[str, str, str]] = set()
    for face, config in faces.items():
        if face in checked:
            continue
        origin_ref, to_room_ref, direction = face
        reverse = (
            to_room_ref,
            origin_ref,
            adv_consts.REVERSE_DIRECTIONS[direction],
        )
        reverse_config = faces.get(reverse)
        if reverse_config is None:
            continue
        checked.update((face, reverse))
        if config != reverse_config:
            raise serializers.ValidationError(
                "Reciprocal room door manifests must use identical key, "
                "destroy_key, and default_state settings. Conflicting faces: "
                f"{origin_ref} {direction} and "
                f"{to_room_ref} {reverse[2]}."
            )


def _authored_instance_templates(base_world: World) -> list[World]:
    return list(
        World.objects.filter(
            instance_of=base_world,
            context__isnull=True,
        )
        .exclude(lifecycle=adv_consts.WORLD_STATE_ARCHIVED)
        .select_related("config", "config__exits_to")
        .order_by("instance_slug", "id")
    )


def _bundle_world_ref(world: World, *, base_world: World) -> str:
    if world.id == base_world.id:
        return _BASE_WORLD_BUNDLE_REF
    if (
        world.instance_of_id != base_world.id
        or world.context_id is not None
        or not world.instance_slug
    ):
        raise serializers.ValidationError(
            "World bundles may reference only their base world and direct "
            "authored instance templates with stable slugs."
        )
    return f"{_INSTANCE_BUNDLE_REF_PREFIX}{world.instance_slug}"


def _scoped_world_documents(
    world: World,
    *,
    world_ref: str,
) -> list[dict[str, Any]]:
    scoped_documents = []
    for document in serialize_world_documents(world):
        scoped = copy.deepcopy(document)
        metadata = scoped.get("metadata")
        if metadata is None:
            metadata = {}
            scoped["metadata"] = metadata
        if not isinstance(metadata, dict):
            raise serializers.ValidationError(
                "Manifest metadata must be a mapping."
            )
        metadata["world_ref"] = world_ref
        scoped_documents.append(scoped)
    return scoped_documents


def _serialize_world_family_links(
    *,
    base_world: World,
    templates: list[World],
) -> list[dict[str, Any]]:
    worlds = [base_world, *templates]
    world_ids = {world.id for world in worlds}
    ref_by_world_id = {
        world.id: _bundle_world_ref(
            world,
            base_world=base_world,
        )
        for world in worlds
    }
    template_ids = {template.id for template in templates}
    links: list[dict[str, Any]] = []

    rooms = (
        Room.objects.filter(world_id__in=world_ids)
        .select_related(
            "transfer_to__world",
            "exits_to__world",
            "enters_instance",
        )
        .only(
            "id",
            "world_id",
            "relative_id",
            "transfer_to_id",
            "transfer_to__world_id",
            "transfer_to__relative_id",
            "exits_to_id",
            "exits_to__world_id",
            "exits_to__relative_id",
            "enters_instance_id",
        )
        .order_by("world_id", "relative_id", "id")
    )
    for room in rooms:
        source = {
            "world": ref_by_world_id[room.world_id],
            "room": format_room_manifest_ref(room),
        }
        if room.transfer_to_id:
            target = room.transfer_to
            if (
                room.world_id != base_world.id
                or target.world_id not in template_ids
            ):
                raise serializers.ValidationError(
                    "room.transfer_to links must point from the bundled base "
                    "world to one of its direct instance templates."
                )
            links.append(
                {
                    "relation": "room.transfer_to",
                    "source": source,
                    "target": {
                        "world": ref_by_world_id[target.world_id],
                        "room": format_room_manifest_ref(target),
                    },
                }
            )
        if room.exits_to_id:
            target = room.exits_to
            if (
                room.world_id not in template_ids
                or target.world_id != base_world.id
            ):
                raise serializers.ValidationError(
                    "room.exits_to links must point from a bundled instance "
                    "template back to its base world."
                )
            links.append(
                {
                    "relation": "room.exits_to",
                    "source": source,
                    "target": {
                        "world": _BASE_WORLD_BUNDLE_REF,
                        "room": format_room_manifest_ref(target),
                    },
                }
            )
        if room.enters_instance_id:
            if (
                room.world_id != base_world.id
                or room.enters_instance_id not in template_ids
            ):
                raise serializers.ValidationError(
                    "room.enters_instance links must point from the bundled "
                    "base world to one of its direct instance templates."
                )
            links.append(
                {
                    "relation": "room.enters_instance",
                    "source": source,
                    "target": {
                        "world": ref_by_world_id[
                            room.enters_instance_id
                        ],
                    },
                }
            )

    for template in templates:
        config = template.config
        if config is None or not config.exits_to_id:
            continue
        exit_room = config.exits_to
        if exit_room.world_id != base_world.id:
            raise serializers.ValidationError(
                "world_config.exits_to must point from an instance template "
                "back to its bundled base world."
            )
        links.append(
            {
                "relation": "world_config.exits_to",
                "source": {
                    "world": ref_by_world_id[template.id],
                },
                "target": {
                    "world": _BASE_WORLD_BUNDLE_REF,
                    "room": format_room_manifest_ref(exit_room),
                },
            }
        )

    base_config = base_world.config
    if base_config is not None and base_config.exits_to_id:
        raise serializers.ValidationError(
            "A base world's config.exits_to cannot be represented as an "
            "instance-family link."
        )
    return sorted(
        links,
        key=lambda link: (
            link["relation"],
            link["source"].get("world", ""),
            link["source"].get("room", ""),
            link["target"].get("world", ""),
            link["target"].get("room", ""),
        ),
    )


def serialize_world_bundle_documents(
    base_world: World,
) -> list[dict[str, Any]]:
    if base_world.context_id or base_world.instance_of_id:
        raise serializers.ValidationError(
            "Instance templates and runtime worlds cannot be exported "
            "independently. Export the authored base world so the complete "
            "world family and its inherited references remain portable."
        )

    templates = _authored_instance_templates(base_world)
    if templates and not base_world.is_multiplayer:
        raise serializers.ValidationError(
            "A world with authored instance templates must be multiplayer "
            "before it can be exported."
        )
    links = _serialize_world_family_links(
        base_world=base_world,
        templates=templates,
    )
    if not templates:
        return serialize_world_documents(base_world)

    worlds = [
        {
            "ref": _BASE_WORLD_BUNDLE_REF,
            "role": "base",
            "name": base_world.name or "",
        },
        *[
            {
                "ref": _bundle_world_ref(
                    template,
                    base_world=base_world,
                ),
                "role": "instance",
                "slug": template.instance_slug,
                "name": template.name or "",
                "parent": _BASE_WORLD_BUNDLE_REF,
            }
            for template in templates
        ],
    ]
    header = {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
        "kind": WORLD_BUNDLE_MANIFEST_KIND,
        "metadata": {
            "name": base_world.name or "",
        },
        "spec": {
            "worlds": worlds,
            "links_mode": "replace",
            "links": links,
        },
    }
    documents = [header]
    documents.extend(
        _scoped_world_documents(
            base_world,
            world_ref=_BASE_WORLD_BUNDLE_REF,
        )
    )
    for template in templates:
        documents.extend(
            _scoped_world_documents(
                template,
                world_ref=_bundle_world_ref(
                    template,
                    base_world=base_world,
                ),
            )
        )
    return documents


def _summarize_world_bundle_documents(
    documents: list[dict[str, Any]],
) -> dict[str, int]:
    content_documents = [
        document
        for document in documents
        if parse_document_kind(document) != WORLD_BUNDLE_MANIFEST_KIND
    ]
    summary = _summarize_documents(content_documents)
    summary["documents"] = len(documents)
    header = documents[0]
    header_spec = header.get("spec") or {}
    worlds = header_spec.get("worlds") or []
    summary["worlds"] = len(worlds)
    summary["instances"] = max(0, len(worlds) - 1)
    summary["links"] = len(header_spec.get("links") or [])
    return summary


def parse_world_bundle_stream(
    documents: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
]:
    """Validate and partition one flat world-family manifest stream."""

    if not documents or parse_document_kind(
        documents[0]
    ) != WORLD_BUNDLE_MANIFEST_KIND:
        raise serializers.ValidationError(
            "A world bundle stream must begin with kind: worldbundle."
        )
    if len(documents) > 10_000:
        raise serializers.ValidationError(
            "World bundles cannot exceed 10,000 documents."
        )
    header = documents[0]
    spec = header.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError(
            "worldbundle.spec must be a mapping."
        )
    unknown_fields = sorted(
        set(spec) - {"worlds", "links_mode", "links"}
    )
    if unknown_fields:
        raise serializers.ValidationError(
            "worldbundle.spec has unsupported field(s): "
            f"{', '.join(unknown_fields)}."
        )
    if spec.get("links_mode", "replace") != "replace":
        raise serializers.ValidationError(
            "worldbundle.spec.links_mode must be replace."
        )
    raw_worlds = spec.get("worlds")
    if not isinstance(raw_worlds, list) or not raw_worlds:
        raise serializers.ValidationError(
            "worldbundle.spec.worlds must be a non-empty list."
        )
    if len(raw_worlds) > 50:
        raise serializers.ValidationError(
            "World bundles cannot exceed 50 authored worlds."
        )

    declarations: dict[str, dict[str, Any]] = {}
    base_count = 0
    for index, declaration in enumerate(raw_worlds):
        field_name = f"worldbundle.spec.worlds[{index}]"
        if not isinstance(declaration, dict):
            raise serializers.ValidationError(
                f"{field_name} must be a mapping."
            )
        world_ref = str(declaration.get("ref") or "").strip()
        role = str(declaration.get("role") or "").strip().lower()
        name = str(declaration.get("name") or "").strip()
        if not world_ref or world_ref in declarations:
            raise serializers.ValidationError(
                f"{field_name}.ref must be unique and non-empty."
            )
        normalized = {
            "ref": world_ref,
            "role": role,
            "name": name,
        }
        if role == "base":
            unknown_declaration_fields = sorted(
                set(declaration) - {"ref", "role", "name"}
            )
            if unknown_declaration_fields:
                raise serializers.ValidationError(
                    f"{field_name} has unsupported field(s): "
                    f"{', '.join(unknown_declaration_fields)}."
                )
            base_count += 1
            if world_ref != _BASE_WORLD_BUNDLE_REF:
                raise serializers.ValidationError(
                    f"{field_name}.ref must be "
                    f"{_BASE_WORLD_BUNDLE_REF} for the base world."
                )
        elif role == "instance":
            unknown_declaration_fields = sorted(
                set(declaration)
                - {"ref", "role", "slug", "name", "parent"}
            )
            if unknown_declaration_fields:
                raise serializers.ValidationError(
                    f"{field_name} has unsupported field(s): "
                    f"{', '.join(unknown_declaration_fields)}."
                )
            prefix = _INSTANCE_BUNDLE_REF_PREFIX
            if not world_ref.startswith(prefix):
                raise serializers.ValidationError(
                    f"{field_name}.ref must use instance.<slug>."
                )
            instance_slug = str(
                declaration.get("slug") or ""
            ).strip()
            if (
                not instance_slug
                or len(instance_slug) > 120
                or slugify(instance_slug) != instance_slug
                or world_ref != f"{prefix}{instance_slug}"
            ):
                raise serializers.ValidationError(
                    f"{field_name}.slug must be a valid lowercase slug "
                    "matching its ref."
                )
            if declaration.get("parent") != _BASE_WORLD_BUNDLE_REF:
                raise serializers.ValidationError(
                    f"{field_name}.parent must be "
                    f"{_BASE_WORLD_BUNDLE_REF}."
                )
            normalized["slug"] = instance_slug
        else:
            raise serializers.ValidationError(
                f"{field_name}.role must be base or instance."
            )
        declarations[world_ref] = normalized
    if base_count != 1:
        raise serializers.ValidationError(
            "A world bundle must declare exactly one base world."
        )

    grouped_documents = {
        world_ref: []
        for world_ref in declarations
    }
    for index, document in enumerate(documents[1:], start=2):
        if parse_document_kind(document) == WORLD_BUNDLE_MANIFEST_KIND:
            raise serializers.ValidationError(
                f"Document {index} cannot contain another worldbundle."
            )
        metadata = document.get("metadata")
        if not isinstance(metadata, dict):
            raise serializers.ValidationError(
                f"Document {index} metadata must include world_ref."
            )
        world_ref = str(metadata.get("world_ref") or "").strip()
        if world_ref not in declarations:
            raise serializers.ValidationError(
                f"Document {index} references undeclared bundle world "
                f"'{world_ref}'."
            )
        unscoped = copy.deepcopy(document)
        unscoped_metadata = unscoped["metadata"]
        unscoped_metadata.pop("world_ref", None)
        if not unscoped_metadata:
            unscoped.pop("metadata")
        grouped_documents[world_ref].append(unscoped)

    for world_ref, scoped_documents in grouped_documents.items():
        world_documents = [
            document
            for document in scoped_documents
            if parse_document_kind(document) == WORLD_MANIFEST_KIND
        ]
        if len(world_documents) != 1:
            raise serializers.ValidationError(
                f"Bundle scope '{world_ref}' must contain exactly one world "
                "document."
            )

    declared_room_refs: dict[str, set[str]] = {
        world_ref: set()
        for world_ref in declarations
    }
    for world_ref, scoped_documents in grouped_documents.items():
        for document in scoped_documents:
            if parse_document_kind(document) != ROOM_MANIFEST_KIND:
                continue
            if (
                builder_manifests.parse_manifest_operation(document)
                != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY
            ):
                continue
            metadata = _manifest_metadata(document)
            room_ref = str(metadata.get("ref") or "").strip()
            parsed = parse_room_reference(room_ref)
            if (
                parsed is None
                or parsed.kind != "relative_id"
                or not parsed.relative_id
                or parsed.relative_id <= 0
            ):
                raise serializers.ValidationError(
                    f"Room documents in bundle scope '{world_ref}' must use "
                    "metadata.ref: room@<relative_id>."
                )
            declared_room_refs[world_ref].add(
                f"{_ROOM_REF_PREFIX}{parsed.relative_id}"
            )

    links = spec.get("links", [])
    if links is None:
        links = []
    if not isinstance(links, list):
        raise serializers.ValidationError(
            "worldbundle.spec.links must be a list."
        )
    if len(links) > 20_000:
        raise serializers.ValidationError(
            "World bundles cannot exceed 20,000 cross-world links."
        )
    normalized_links = []
    seen_sources = set()
    allowed_relations = {
        "room.transfer_to",
        "room.exits_to",
        "room.enters_instance",
        "world_config.exits_to",
    }
    for index, link in enumerate(links):
        field_name = f"worldbundle.spec.links[{index}]"
        if not isinstance(link, dict):
            raise serializers.ValidationError(
                f"{field_name} must be a mapping."
            )
        unknown_link_fields = sorted(
            set(link) - {"relation", "source", "target"}
        )
        if unknown_link_fields:
            raise serializers.ValidationError(
                f"{field_name} has unsupported field(s): "
                f"{', '.join(unknown_link_fields)}."
            )
        relation = str(link.get("relation") or "").strip()
        source = link.get("source")
        target = link.get("target")
        if relation not in allowed_relations:
            raise serializers.ValidationError(
                f"{field_name}.relation is unsupported."
            )
        if not isinstance(source, dict) or not isinstance(target, dict):
            raise serializers.ValidationError(
                f"{field_name}.source and target must be mappings."
            )
        unknown_source_fields = sorted(
            set(source) - {"world", "room"}
        )
        unknown_target_fields = sorted(
            set(target) - {"world", "room"}
        )
        if unknown_source_fields or unknown_target_fields:
            endpoint_errors = []
            if unknown_source_fields:
                endpoint_errors.append(
                    "source: " + ", ".join(unknown_source_fields)
                )
            if unknown_target_fields:
                endpoint_errors.append(
                    "target: " + ", ".join(unknown_target_fields)
                )
            raise serializers.ValidationError(
                f"{field_name} has unsupported endpoint field(s) "
                f"({'; '.join(endpoint_errors)})."
            )
        source_world = str(source.get("world") or "").strip()
        target_world = str(target.get("world") or "").strip()
        if (
            source_world not in declarations
            or target_world not in declarations
        ):
            raise serializers.ValidationError(
                f"{field_name} references an undeclared bundle world."
            )
        source_room = str(source.get("room") or "").strip()
        target_room = str(target.get("room") or "").strip()
        source_role = declarations[source_world]["role"]
        target_role = declarations[target_world]["role"]
        if relation.startswith("room.") and not source_room:
            raise serializers.ValidationError(
                f"{field_name}.source.room is required."
            )
        if relation == "world_config.exits_to" and source_room:
            raise serializers.ValidationError(
                f"{field_name}.source.room is not allowed."
            )
        if relation == "room.enters_instance" and target_room:
            raise serializers.ValidationError(
                f"{field_name}.target.room is not allowed."
            )
        if relation != "room.enters_instance" and not target_room:
            raise serializers.ValidationError(
                f"{field_name}.target.room is required."
            )
        if relation in {
            "room.transfer_to",
            "room.enters_instance",
        }:
            expected_roles = ("base", "instance")
        else:
            expected_roles = ("instance", "base")
        if (source_role, target_role) != expected_roles:
            raise serializers.ValidationError(
                f"{field_name} must point from {expected_roles[0]} to "
                f"{expected_roles[1]}."
            )
        for label, room_ref in (
            ("source.room", source_room),
            ("target.room", target_room),
        ):
            if not room_ref:
                continue
            parsed = parse_room_reference(room_ref)
            if (
                parsed is None
                or parsed.kind != "relative_id"
                or not parsed.relative_id
                or parsed.relative_id <= 0
            ):
                raise serializers.ValidationError(
                    f"{field_name}.{label} must use "
                    "room@<relative_id>."
                )
            endpoint_world = (
                source_world
                if label == "source.room"
                else target_world
            )
            canonical_room_ref = (
                f"{_ROOM_REF_PREFIX}{parsed.relative_id}"
            )
            if canonical_room_ref not in declared_room_refs[endpoint_world]:
                raise serializers.ValidationError(
                    f"{field_name}.{label} must reference a room document "
                    f"declared in bundle scope '{endpoint_world}'."
                )
        source_key = (relation, source_world, source_room)
        if source_key in seen_sources:
            raise serializers.ValidationError(
                f"{field_name} duplicates a relation source."
            )
        seen_sources.add(source_key)
        normalized_links.append(
            {
                "relation": relation,
                "source": {
                    "world": source_world,
                    **({"room": source_room} if source_room else {}),
                },
                "target": {
                    "world": target_world,
                    **({"room": target_room} if target_room else {}),
                },
            }
        )
    return declarations, grouped_documents, normalized_links


def resolve_or_create_world_bundle_scopes(
    *,
    base_world: World,
    declarations: dict[str, dict[str, Any]],
    author,
) -> dict[str, World]:
    """Lock and materialize the authored worlds declared by a bundle."""

    if base_world.context_id or base_world.instance_of_id:
        raise serializers.ValidationError(
            "World bundles can only be imported into an authored base world."
        )
    World.objects.select_for_update().get(pk=base_world.pk)
    instance_declarations = [
        declaration
        for declaration in declarations.values()
        if declaration["role"] == "instance"
    ]
    if instance_declarations and not base_world.is_multiplayer:
        raise serializers.ValidationError(
            "This bundle contains instance templates. Import it into a "
            "multiplayer base world; converting a single-player world with "
            "runtime state is not safe."
        )

    existing_templates = {
        template.instance_slug: template
        for template in (
            World.objects.select_for_update(of=("self",))
            .filter(
                instance_of=base_world,
                context__isnull=True,
            )
            .select_related("config")
            .order_by("id")
        )
    }
    scope_worlds = {
        _BASE_WORLD_BUNDLE_REF: base_world,
    }
    from builders.instance_templates import create_instance_template

    for declaration in sorted(
        instance_declarations,
        key=lambda candidate: candidate["slug"],
    ):
        template = existing_templates.get(declaration["slug"])
        if (
            template is not None
            and template.lifecycle == adv_consts.WORLD_STATE_ARCHIVED
        ):
            raise serializers.ValidationError(
                "Bundle instance slug "
                f"'{declaration['slug']}' conflicts with an archived "
                "instance template. Restore or permanently remove that "
                "template before importing this bundle."
            )
        if template is None:
            template = create_instance_template(
                base_world=base_world,
                author=author,
                name=declaration["name"] or declaration["slug"],
                instance_slug=declaration["slug"],
            )
        scope_worlds[declaration["ref"]] = template
    return scope_worlds


@transaction.atomic
def apply_world_bundle_links(
    *,
    scope_worlds: dict[str, World],
    links: list[dict[str, Any]],
) -> int:
    """Replace all managed cross-world links using batched room lookups."""

    base_world = scope_worlds[_BASE_WORLD_BUNDLE_REF]
    room_ids_by_world: dict[int, set[int]] = {}
    for link in links:
        for endpoint in ("source", "target"):
            spec = link[endpoint]
            room_ref = spec.get("room")
            if not room_ref:
                continue
            parsed = parse_room_reference(room_ref)
            room_ids_by_world.setdefault(
                scope_worlds[spec["world"]].id,
                set(),
            ).add(parsed.relative_id)

    room_lookup: dict[tuple[int, int], Room] = {}
    if room_ids_by_world:
        room_filter = Q()
        for world_id, relative_ids in room_ids_by_world.items():
            room_filter |= Q(
                world_id=world_id,
                relative_id__in=relative_ids,
            )
        for room in Room.objects.filter(room_filter).only(
            "id",
            "world_id",
            "relative_id",
            "transfer_to_id",
            "exits_to_id",
            "enters_instance_id",
        ):
            room_lookup[(room.world_id, room.relative_id)] = room

    def endpoint_room(endpoint, *, field_name):
        world = scope_worlds[endpoint["world"]]
        parsed = parse_room_reference(endpoint["room"])
        room = room_lookup.get((world.id, parsed.relative_id))
        if room is None:
            raise serializers.ValidationError(
                f"{field_name} does not resolve inside its bundle world."
            )
        return room

    family_world_ids = [
        world.id
        for world in scope_worlds.values()
    ]
    Room.objects.filter(
        world_id__in=family_world_ids,
    ).exclude(transfer_to__isnull=True).update(transfer_to=None)
    Room.objects.filter(
        world_id__in=family_world_ids,
    ).exclude(exits_to__isnull=True).update(exits_to=None)
    Room.objects.filter(
        world_id__in=family_world_ids,
    ).exclude(enters_instance__isnull=True).update(
        enters_instance=None,
    )
    for room in room_lookup.values():
        room.transfer_to_id = None
        room.exits_to_id = None
        room.enters_instance_id = None
    configs = list(
        WorldConfig.objects.filter(
            pk__in=[
                world.config_id
                for world in scope_worlds.values()
                if world.config_id
            ]
        )
    )
    for config in configs:
        config.exits_to = None

    changed_rooms: dict[int, Room] = {}
    config_by_world_id = {
        world.id: next(
            (
                config
                for config in configs
                if config.id == world.config_id
            ),
            None,
        )
        for world in scope_worlds.values()
    }
    for index, link in enumerate(links):
        relation = link["relation"]
        source_world = scope_worlds[link["source"]["world"]]
        target_world = scope_worlds[link["target"]["world"]]
        if relation == "room.transfer_to":
            if (
                source_world.id != base_world.id
                or target_world.instance_of_id != base_world.id
            ):
                raise serializers.ValidationError(
                    "room.transfer_to must point from the bundle base to a "
                    "direct instance template."
                )
            source_room = endpoint_room(
                link["source"],
                field_name=f"links[{index}].source.room",
            )
            target_room = endpoint_room(
                link["target"],
                field_name=f"links[{index}].target.room",
            )
            source_room.transfer_to = target_room
            changed_rooms[source_room.id] = source_room
        elif relation == "room.exits_to":
            if (
                source_world.instance_of_id != base_world.id
                or target_world.id != base_world.id
            ):
                raise serializers.ValidationError(
                    "room.exits_to must point from an instance template back "
                    "to the bundle base."
                )
            source_room = endpoint_room(
                link["source"],
                field_name=f"links[{index}].source.room",
            )
            source_room.exits_to = endpoint_room(
                link["target"],
                field_name=f"links[{index}].target.room",
            )
            changed_rooms[source_room.id] = source_room
        elif relation == "room.enters_instance":
            if (
                source_world.id != base_world.id
                or target_world.instance_of_id != base_world.id
            ):
                raise serializers.ValidationError(
                    "room.enters_instance must point from the bundle base to "
                    "a direct instance template."
                )
            source_room = endpoint_room(
                link["source"],
                field_name=f"links[{index}].source.room",
            )
            source_room.enters_instance = target_world
            changed_rooms[source_room.id] = source_room
        else:
            if (
                source_world.instance_of_id != base_world.id
                or target_world.id != base_world.id
            ):
                raise serializers.ValidationError(
                    "world_config.exits_to must point from an instance "
                    "template back to the bundle base."
                )
            config = config_by_world_id.get(source_world.id)
            if config is None:
                raise serializers.ValidationError(
                    "world_config.exits_to source has no world config."
                )
            config.exits_to = endpoint_room(
                link["target"],
                field_name=f"links[{index}].target.room",
            )

    if changed_rooms:
        Room.objects.bulk_update(
            list(changed_rooms.values()),
            ["transfer_to", "exits_to", "enters_instance"],
        )
    if configs:
        WorldConfig.objects.bulk_update(configs, ["exits_to"])
    return len(links)


def serialize_world_export_payload(world: World) -> dict[str, Any]:
    documents = serialize_world_bundle_documents(world)
    is_bundle = (
        bool(documents)
        and parse_document_kind(documents[0])
        == WORLD_BUNDLE_MANIFEST_KIND
    )
    return {
        "documents": documents,
        "yaml": manifest_stream_to_yaml(documents),
        "summary": (
            _summarize_world_bundle_documents(documents)
            if is_bundle
            else _summarize_documents(documents)
        ),
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
    if zone.pvp_zone:
        return None
    if int(zone.respawn_wait) != 300:
        return None
    if get_initial_state_snapshot(STATE_SCOPE_ZONE, zone):
        return None
    return zone


def _placeholder_room_has_dependents(
    *,
    room: Room,
    config: WorldConfig,
    placeholder_zone: Zone,
    allow_import_scaffold_dependents: bool = False,
) -> bool:
    """
    Detect records that make a default-looking room authored or runtime data.

    The target config's starting/death pointers and the untouched starting
    zone's center are expected scaffold references. A complete-stream import
    may also rehome the offline Builder player and same-world editor bookmark
    created by the Lobby's Create World workflow.
    """

    from spawns.models import Mob, Player

    database = room._state.db
    for relation in Room._meta.related_objects:
        queryset = relation.related_model._base_manager.using(database).filter(
            **{relation.field.name: room}
        )
        if (
            relation.related_model is WorldConfig
            and relation.field.name in {"starting_room", "death_room"}
        ):
            queryset = queryset.exclude(pk=config.pk)
        elif (
            relation.related_model is Zone
            and relation.field.name == "center"
        ):
            queryset = queryset.exclude(pk=placeholder_zone.pk)
        elif (
            allow_import_scaffold_dependents
            and relation.related_model is LastViewedRoom
            and relation.field.name == "room"
        ):
            queryset = queryset.exclude(world_id=room.world_id)
        elif (
            allow_import_scaffold_dependents
            and relation.related_model is Player
            and relation.field.name == "room"
        ):
            queryset = queryset.exclude(
                is_builder=True,
                in_game=False,
                world__context_id=room.world_id,
            )
        if queryset.exists():
            return True

    if room.inventory.exists():
        return True

    room_content_type = ContentType.objects.get_for_model(Room)
    generic_dependents = (
        room_content_type.assignment_types.filter(
            assignment_id=room.id,
        ),
        room_content_type.trigger_target_types.filter(
            target_id=room.id,
        ),
        room_content_type.faction_assignments.filter(
            member_id=room.id,
        ),
    )
    if any(queryset.exists() for queryset in generic_dependents):
        return True

    # Mob.roams is a GenericForeignKey without a reverse GenericRelation.
    return Mob.objects.filter(
        roams_type=room_content_type,
        roams_id=room.id,
    ).exists()


def _find_placeholder_room(
    world: World,
    *,
    allow_import_scaffold_dependents: bool = False,
) -> Room | None:
    if world.rooms.count() != 1:
        return None
    room = world.rooms.order_by("id").first()
    if not room:
        return None
    if room.relative_id != 1:
        return None
    allocator = (
        World.objects.filter(pk=world.pk)
        .values_list("next_room_relative_id", flat=True)
        .first()
    )
    if allocator != 2:
        return None
    if room.name != "Starting Room":
        return None
    if (room.x, room.y, room.z) != (0, 0, 0):
        return None
    if room.type != adv_consts.ROOM_TYPE_INDOOR:
        return None
    if room.description or room.note or room.color or room.is_landmark:
        return None
    if get_initial_state_snapshot(STATE_SCOPE_ROOM, room):
        return None
    if (
        room.merchant_profile_id
        or room.crafting_profile_id
        or room.trainer_profile_id
        or room.enters_instance_id
        or room.transfer_to_id
        or room.exits_to_id
        or room.housing_block_id
        or room.ownership_type
        != adv_consts.ROOM_OWNERSHIP_TYPE_PRIVATE
    ):
        return None
    if any(getattr(room, direction + "_id") for direction in adv_consts.DIRECTIONS):
        return None
    if (
        room.flags.exists()
        or room.details.exists()
        or room.doors_from.exists()
        or room.doors_to.exists()
    ):
        return None
    config = world.config
    if config is None or (
        config.starting_room_id != room.id or config.death_room_id != room.id
    ):
        return None
    placeholder_zone = _find_placeholder_zone(world)
    if placeholder_zone is None or room.zone_id != placeholder_zone.id:
        return None
    if _placeholder_room_has_dependents(
        room=room,
        config=config,
        placeholder_zone=placeholder_zone,
        allow_import_scaffold_dependents=allow_import_scaffold_dependents,
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
    parsed = parse_room_reference(room_ref)
    if parsed is None:
        raise serializers.ValidationError(
            "Room references must use 'room@<relative_id>', legacy "
            "'room@x,y,z', or legacy 'room.<database_id>'."
        )
    if parsed.kind != "coordinates":
        room = resolve_room_reference(world, room_ref)
        if room is None:
            raise serializers.ValidationError(
                f"Room reference '{room_ref}' does not resolve in this world."
            )
        if zone is not None and room.zone_id != zone.id:
            room.zone = zone
            room.save(update_fields=["zone"])
        return room

    x, y, z = parsed.x, parsed.y, parsed.z
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


def _get_mob_definition(*, world: World, value: Any, field_name: str) -> MobDefinition:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        mob_definition = MobDefinition.objects.filter(world=world, pk=int(text)).first()
        if mob_definition:
            return mob_definition
        raise serializers.ValidationError(f"{field_name} references an unknown mob definition.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"mobdefinition", "mob_definition"}:
            raise serializers.ValidationError(
                f"{field_name} must reference a mob definition slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    mob_definition = MobDefinition.objects.filter(world=world, slug=slug).first()
    if mob_definition:
        return mob_definition
    raise serializers.ValidationError(f"{field_name} references an unknown mob definition.")


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
    if text.isascii() and text.isdecimal():
        raise serializers.ValidationError(
            f"{field_name} uses an ambiguous bare numeric room reference; "
            "use 'room@<relative_id>'."
        )
    room = resolve_room_reference(world, text)
    if room is None and parse_room_reference(text) is None:
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


def _validate_spawn_source(*, world: World, source: Any, field_name: str) -> list[Any]:
    from spawns.spawn_plans import resolve_source

    resolved_sources = []
    for index, source_ref in enumerate(_spawn_source_refs(source)):
        ref_field = f"{field_name}[{index}]" if isinstance(source, dict) and "pool" in source else field_name
        resolved_sources.append(
            resolve_source(
                world=world,
                source_spec=source_ref,
                field_name=ref_field,
            )
        )
    return resolved_sources


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


def _normalize_spawn_respawn_policy(
    value: Any,
    *,
    field_name: str = "spec.respawn",
) -> dict[str, Any]:
    from spawns.spawn_plans import (
        RESPAWN_MODE_FIXED,
        RESPAWN_MODE_INHERIT_ZONE,
        RESPAWN_MODE_NONE,
        RESPAWN_MODES,
    )

    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")

    unsupported = sorted(set(value) - {"mode", "seconds"})
    if unsupported:
        raise serializers.ValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unsupported)}."
        )

    raw_mode = value.get("mode", RESPAWN_MODE_FIXED)
    if not isinstance(raw_mode, str):
        raise serializers.ValidationError(f"{field_name}.mode must be a string.")
    mode = raw_mode.strip().lower()
    if mode not in RESPAWN_MODES:
        raise serializers.ValidationError(
            f"{field_name}.mode must be one of: "
            f"{', '.join(sorted(RESPAWN_MODES))}."
        )

    if mode == RESPAWN_MODE_NONE:
        if value.get("seconds") not in (None, ""):
            raise serializers.ValidationError(
                f"{field_name}.seconds is not supported when mode is none."
            )
        return {"mode": RESPAWN_MODE_NONE}

    normalized: dict[str, Any] = {"mode": mode}
    raw_seconds = value.get("seconds")
    if raw_seconds in (None, ""):
        if mode == RESPAWN_MODE_FIXED:
            normalized["seconds"] = 0
        return normalized
    if isinstance(raw_seconds, bool):
        raise serializers.ValidationError(
            f"{field_name}.seconds must be a non-negative integer."
        )
    if not isinstance(raw_seconds, (int, str)):
        raise serializers.ValidationError(
            f"{field_name}.seconds must be a non-negative integer."
        )
    try:
        seconds = int(raw_seconds)
    except (TypeError, ValueError):
        raise serializers.ValidationError(
            f"{field_name}.seconds must be a non-negative integer."
        )
    if seconds < 0:
        raise serializers.ValidationError(
            f"{field_name}.seconds must be a non-negative integer."
        )
    normalized["seconds"] = seconds
    return normalized


def _normalize_spawn_traits(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        return normalize_trait_table(value, field_name=field_name)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))


def _normalize_spawn_cohort(entry_spec: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    placement = copy.deepcopy(entry_spec.get("placement") or {})
    if not isinstance(placement, dict):
        raise serializers.ValidationError(f"{field_name}.placement must be a mapping when provided.")

    raw_cohort = entry_spec.get("cohort", placement.get("cohort"))
    raw_role = entry_spec.get("cohort_role", placement.get("cohort_role", placement.get("role")))
    raw_policy = entry_spec.get("cohort_policy", placement.get("cohort_policy", placement.get("policy")))
    if isinstance(raw_cohort, dict):
        cohort_slug = _slug_or_error(
            raw_cohort.get("slug") or raw_cohort.get("name") or raw_cohort.get("id"),
            f"{field_name}.cohort.slug",
        )
        raw_role = raw_role or raw_cohort.get("role") or raw_cohort.get("cohort_role")
        raw_policy = raw_policy or raw_cohort.get("policy") or raw_cohort.get("cohort_policy")
    elif raw_cohort not in (None, ""):
        cohort_slug = _slug_or_error(raw_cohort, f"{field_name}.cohort")
    else:
        return placement

    role = str(raw_role or "").strip().lower()
    if role:
        role = role.replace("-", "_")
        if role not in {"leader", "follower", "member"}:
            raise serializers.ValidationError(
                f"{field_name}.cohort_role must be leader, follower, or member."
            )

    policy = str(raw_policy or "refill_missing").strip().lower().replace("-", "_")
    if policy not in {"refill_missing"}:
        raise serializers.ValidationError(
            f"{field_name}.cohort_policy must be refill_missing."
        )

    placement["cohort"] = cohort_slug
    if role:
        placement["cohort_role"] = role
    placement["cohort_policy"] = policy
    return placement


def _entry_traits_spec(entry_spec: dict[str, Any], *, field_name: str) -> Any:
    has_traits = "traits" in entry_spec and entry_spec.get("traits") not in (None, "")
    has_affixes = "affixes" in entry_spec and entry_spec.get("affixes") not in (None, "")
    if has_traits and has_affixes:
        raise serializers.ValidationError(
            f"{field_name} cannot define both traits and affixes. Use traits."
        )
    if has_traits:
        return entry_spec.get("traits")
    return entry_spec.get("affixes")


def _validate_spawn_target(
    *,
    world: World,
    target: Any,
    entry_slugs: set[str],
    field_name: str,
) -> dict[str, Any]:
    """Resolve one portable target while accepting transition-era mappings."""

    if isinstance(target, str):
        target_ref = target.strip()
        if not target_ref:
            raise serializers.ValidationError(
                f"{field_name} must target a room, zone, path, or entry."
            )
        if target_ref.startswith(_SPAWN_ENTRY_REF_PREFIX):
            target = {
                "entry": target_ref[len(_SPAWN_ENTRY_REF_PREFIX):],
            }
        elif target_ref.startswith(_ZONE_REF_PREFIX):
            target = {"zone": target_ref}
        elif target_ref.startswith(_PATH_REF_PREFIX):
            target = {"path": target_ref}
        else:
            # Bare strings and historical room refs were the original scalar
            # form. Keep them as import aliases, but never emit them.
            target = {"room": target_ref}
    if not isinstance(target, dict):
        raise serializers.ValidationError(
            f"{field_name} must be a typed reference or legacy target mapping."
        )

    supported_fields = {
        "room",
        "room_ref",
        "zone",
        "path",
        "entry",
        "parent_entry",
        "name",
    }
    unsupported_fields = sorted(set(target) - supported_fields)
    if unsupported_fields:
        raise serializers.ValidationError(
            f"{field_name} has unsupported field(s): "
            f"{', '.join(unsupported_fields)}."
        )

    room_values = [
        (room_field, target.get(room_field))
        for room_field in ("room", "room_ref")
        if target.get(room_field) not in (None, "")
    ]
    entry_values = [
        (entry_field, target.get(entry_field))
        for entry_field in ("entry", "parent_entry")
        if target.get(entry_field) not in (None, "")
    ]
    target_families = [
        target_family
        for target_family, is_present in (
            ("room", bool(room_values)),
            ("zone", target.get("zone") not in (None, "")),
            ("path", target.get("path") not in (None, "")),
            ("entry", bool(entry_values)),
        )
        if is_present
    ]
    if len(target_families) != 1:
        raise serializers.ValidationError(
            f"{field_name} must contain exactly one room, zone, path, or entry target."
        )

    target_family = target_families[0]
    if target_family == "room":
        resolved_rooms = [
            _resolve_spawn_plan_room(
                world=world,
                value=room_value,
                field_name=f"{field_name}.{room_field}",
            )
            for room_field, room_value in room_values
        ]
        if len({room.id for room in resolved_rooms}) != 1:
            raise serializers.ValidationError(
                f"{field_name}.room and {field_name}.room_ref must reference the same room."
            )
        return {"kind": "room", "room": resolved_rooms[0]}
    if target_family == "zone":
        return {
            "kind": "zone",
            "zone": _resolve_spawn_plan_zone(
                world=world,
                value=target.get("zone"),
                field_name=f"{field_name}.zone",
            ),
        }
    if target_family == "path":
        return {
            "kind": "path",
            "path": _resolve_spawn_plan_path(
                world=world,
                value=target.get("path"),
                field_name=f"{field_name}.path",
            ),
        }

    normalized_entry_slugs = {
        _slug_or_error(
            (
                str(entry_value).strip()[len(_SPAWN_ENTRY_REF_PREFIX):]
                if str(entry_value).strip().startswith(_SPAWN_ENTRY_REF_PREFIX)
                else entry_value
            ),
            f"{field_name}.{entry_field}",
        )
        for entry_field, entry_value in entry_values
    }
    if len(normalized_entry_slugs) != 1:
        raise serializers.ValidationError(
            f"{field_name}.entry and {field_name}.parent_entry must reference the same entry."
        )
    entry_slug = normalized_entry_slugs.pop()
    if entry_slug not in entry_slugs:
        raise serializers.ValidationError(
            f"{field_name}.entry references an unknown entry slug."
        )
    return {"kind": "entry", "entry_slug": entry_slug}


def _spawn_target_entry_slug(target: Any) -> str:
    if not isinstance(target, dict):
        return ""
    return str(
        target.get("entry_slug")
        or target.get("entry")
        or target.get("parent_entry")
        or ""
    ).strip()


def _effective_spawn_cohort_role(entry: dict[str, Any]) -> str:
    placement = entry.get("placement") if isinstance(entry.get("placement"), dict) else {}
    cohort = str(placement.get("cohort") or "").strip()
    if not cohort:
        return ""
    role = str(placement.get("cohort_role") or "").strip().lower()
    if role:
        return role
    if _spawn_target_entry_slug(entry.get("target")):
        return "follower"
    return "leader"


def _validate_spawn_entry_relationships(entries: list[dict[str, Any]]) -> None:
    entries_by_slug = {
        entry["slug"]: entry
        for entry in entries
    }
    leader_by_cohort = {}

    for index, entry in enumerate(entries):
        entry_field = f"spec.entries[{index}]"
        target_entry_slug = _spawn_target_entry_slug(entry.get("target"))
        placement = entry.get("placement") if isinstance(entry.get("placement"), dict) else {}
        cohort = str(placement.get("cohort") or "").strip()
        role = _effective_spawn_cohort_role(entry)

        if target_entry_slug:
            parent = entries_by_slug[target_entry_slug]
            if entry.get("is_active") and not parent.get("is_active"):
                raise serializers.ValidationError(
                    f"{entry_field}.target.entry must reference an active entry."
                )
            if int(parent["order"]) >= int(entry["order"]):
                raise serializers.ValidationError(
                    f"{entry_field}.target.entry must reference an earlier entry with a lower order."
                )
            parent_placement = (
                parent.get("placement")
                if isinstance(parent.get("placement"), dict)
                else {}
            )
            parent_cohort = str(parent_placement.get("cohort") or "").strip()
            if cohort and parent_cohort and cohort != parent_cohort:
                raise serializers.ValidationError(
                    f"{entry_field}.cohort must match the target entry cohort."
                )

        if not entry.get("is_active"):
            continue

        if role == "follower" and not target_entry_slug:
            raise serializers.ValidationError(
                f"{entry_field}.cohort_role follower requires target.entry."
            )

        if role == "leader":
            if cohort in leader_by_cohort:
                raise serializers.ValidationError(
                    f"{entry_field}.cohort has more than one leader entry."
                )
            leader_by_cohort[cohort] = entry["slug"]


def apply_currency_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Currency, str]:
    if parse_document_kind(manifest) != CURRENCY_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'currency'.")
    operation = builder_manifests.parse_manifest_operation(manifest)
    if operation not in {
        builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY,
        builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE,
    }:
        raise serializers.ValidationError("Currency manifests support apply or delete.")

    metadata = _manifest_metadata(manifest)
    spec = _manifest_spec(manifest)
    code = str(metadata.get("code") or "").strip()
    if not code:
        raise serializers.ValidationError("metadata.code is required.")

    existing = Currency.objects.filter(world=world, code__iexact=code).first()
    if operation == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        if existing is None:
            raise serializers.ValidationError("Currency was not found.")
        from builders.currencies import delete_currency

        delete_currency(existing)
        return existing, "deleted"

    unknown_fields = sorted(set(spec) - {"name", "plural_name", "description"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}.")
    name = str(spec.get("name", existing.name if existing else "")).strip()
    if not name:
        raise serializers.ValidationError("spec.name is required.")

    plural_name = str(spec.get(
        "plural_name", existing.plural_name if existing else "") or "").strip()
    description = str(spec.get(
        "description", existing.description if existing else "") or "").strip()
    from builders.currencies import create_currency, update_currency

    if existing is None:
        currency = create_currency(
            world=world,
            code=code,
            name=name,
            plural_name=plural_name,
            description=description,
        )
        return currency, "created"
    currency = update_currency(
        existing,
        name=name,
        plural_name=plural_name,
        description=description,
    )
    return currency, "updated"


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
        if "respawn_wait" in spec or created:
            zone.respawn_wait = int(spec.get("respawn_wait", zone.respawn_wait if existing else 300))
        if "pvp_zone" in spec or created:
            zone.pvp_zone = bool(spec.get("pvp_zone", zone.pvp_zone if existing else False))
        zone.save()

        if (
            "initial_state" in spec
            or "state" in spec
            or "zone_data" in spec
            or created
        ):
            replace_initial_state_snapshot(
                STATE_SCOPE_ZONE,
                zone,
                _coerce_initial_state(
                    spec.get(
                        "initial_state",
                        spec.get(
                            "state",
                            spec.get(
                                "zone_data",
                                get_initial_state_snapshot(STATE_SCOPE_ZONE, zone)
                                if existing else {},
                            ),
                        ),
                    ),
                    field_name="spec.initial_state",
                ),
            )

        if "center" in spec:
            center_ref = str(spec.get("center") or "").strip()
            zone.center = _get_or_create_room(world=world, room_ref=center_ref, zone=zone) if center_ref else None
            zone.save(update_fields=["center"])

    return zone, created


def delete_zone_manifest(*, world: World, manifest: dict[str, Any]) -> Zone:
    if parse_document_kind(manifest) != ZONE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'zone'.")
    if builder_manifests.parse_manifest_operation(manifest) != builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Zone delete manifests require operation 'delete'.")
    metadata = _manifest_metadata(manifest)
    zone_ref = str(metadata.get("ref") or "").strip()
    if not zone_ref:
        raise serializers.ValidationError("metadata.ref is required.")
    relative_id = _parse_zone_ref(zone_ref, field_name="metadata.ref")
    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(
                world=world,
                config=world.config,
            ),
            shared=False,
        )
        zone = (
            Zone.objects.select_for_update()
            .filter(world=world, relative_id=relative_id)
            .first()
        )
        if zone is None:
            raise serializers.ValidationError(
                "Zone delete manifest does not resolve to an existing zone."
            )
        if zone.rooms.exists():
            raise serializers.ValidationError(
                "Cannot delete a zone with rooms assigned to it."
            )
        if zone.death_routing_snapshot_references.exists():
            raise serializers.ValidationError(
                "Cannot delete a zone used by active death routing."
            )
        zone._deleted_payload = serialize_zone_payload(
            zone,
            include_yaml=False,
        )
        try:
            zone.delete()
        except RestrictedError as exc:
            raise serializers.ValidationError(
                "Cannot delete a zone referenced by death routing or a spawn plan."
            ) from exc
        return zone


def _room_manifest_coordinates(
    *,
    room_ref: str,
    spec: dict[str, Any],
    existing: Room | None,
) -> tuple[int, int, int]:
    parsed = parse_room_reference(room_ref)
    if parsed is None:
        raise serializers.ValidationError(
            "metadata.ref must use 'room@<relative_id>', legacy "
            "'room@x,y,z', or legacy 'room.<database_id>'."
        )

    explicit_coordinates = None
    if "coordinates" in spec:
        explicit_coordinates = _coerce_room_coordinates(spec.get("coordinates"))

    if parsed.kind == "coordinates":
        referenced_coordinates = (parsed.x, parsed.y, parsed.z)
        if (
            explicit_coordinates is not None
            and explicit_coordinates != referenced_coordinates
        ):
            raise serializers.ValidationError(
                "spec.coordinates must match the legacy coordinates in metadata.ref."
            )
        return referenced_coordinates

    if explicit_coordinates is not None:
        return explicit_coordinates
    if existing is not None:
        return existing.x, existing.y, existing.z
    raise serializers.ValidationError(
        "spec.coordinates is required when creating a room with a stable reference."
    )


def _existing_room_for_manifest_ref(*, world: World, room_ref: str) -> Room | None:
    parsed = parse_room_reference(room_ref)
    if parsed is None:
        raise serializers.ValidationError(
            "metadata.ref must use 'room@<relative_id>', legacy "
            "'room@x,y,z', or legacy 'room.<database_id>'."
        )
    if parsed.kind == "relative_id" and (
        parsed.relative_id is None or parsed.relative_id <= 0
    ):
        raise serializers.ValidationError(
            "metadata.ref relative id must be a positive integer."
        )
    if parsed.kind == "database_id" and (
        parsed.database_id is None or parsed.database_id <= 0
    ):
        raise serializers.ValidationError(
            "metadata.ref database id must be a positive integer."
        )
    return resolve_room_reference(world, room_ref)


def _create_stable_manifest_room(
    *,
    world: World,
    relative_id: int,
    coordinates: tuple[int, int, int],
    name: str,
    coordinates_prevalidated: bool = False,
) -> Room:
    x, y, z = coordinates
    if not coordinates_prevalidated:
        coordinate_owner = Room.objects.filter(
            world=world,
            x=x,
            y=y,
            z=z,
        ).first()
        if coordinate_owner is not None:
            raise serializers.ValidationError(
                "spec.coordinates is already occupied by "
                f"{_room_ref(coordinate_owner)}."
            )
    try:
        return Room.objects.create_with_imported_relative_id(
            world=world,
            relative_id=relative_id,
            name=name or "Untitled Room",
            x=x,
            y=y,
            z=z,
        )
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc


_ROOM_COORDINATE_MAX = 2_147_483_647
_ROOM_COORDINATE_MIN = -2_147_483_648


def _temporary_room_coordinates(
    *,
    occupied: set[tuple[int, int, int]],
    count: int,
) -> list[tuple[int, int, int]]:
    """Choose deterministic, collision-free staging coordinates."""

    coordinates = []
    x = _ROOM_COORDINATE_MAX
    y = _ROOM_COORDINATE_MAX
    z = _ROOM_COORDINATE_MAX
    while len(coordinates) < count:
        candidate = (x, y, z)
        if candidate not in occupied:
            coordinates.append(candidate)
            occupied.add(candidate)
        z -= 1
        if z < _ROOM_COORDINATE_MIN:
            z = _ROOM_COORDINATE_MAX
            y -= 1
        if y < _ROOM_COORDINATE_MIN:
            y = _ROOM_COORDINATE_MAX
            x -= 1
        if x < _ROOM_COORDINATE_MIN:
            raise serializers.ValidationError(
                "No temporary room coordinates are available for this import."
            )
    return coordinates


def reserve_room_manifest_references(
    *,
    world: World,
    documents: list[dict[str, Any]],
) -> set[int]:
    """
    Reserve stable room identities before applying a manifest stream.

    Export order intentionally places factions and zones before rooms. This
    bounded prepass also makes circular room exits safe without creating
    identity-less ghost rooms.
    """

    with transaction.atomic():
        current_rooms = list(
            Room.objects.select_for_update()
            .filter(world=world)
            .only("id", "world_id", "relative_id", "x", "y", "z")
            .order_by("relative_id", "id")
        )
        existing_by_relative_id = {
            room.relative_id: room
            for room in current_rooms
        }

        room_specs: dict[int, tuple[tuple[int, int, int], str]] = {}
        coordinate_refs: dict[tuple[int, int, int], int] = {}
        has_world_document = False
        declared_starting_room_relative_id: int | None = None
        for document in documents:
            kind = parse_document_kind(document)
            if kind == WORLD_MANIFEST_KIND:
                has_world_document = True
                world_spec = _manifest_spec(document)
                if "starting_room" in world_spec:
                    starting_room_ref = str(
                        world_spec.get("starting_room") or ""
                    ).strip()
                    parsed_starting_room = parse_room_reference(
                        starting_room_ref
                    )
                    if (
                        parsed_starting_room is not None
                        and parsed_starting_room.kind == "relative_id"
                        and parsed_starting_room.relative_id is not None
                        and parsed_starting_room.relative_id > 0
                    ):
                        declared_starting_room_relative_id = (
                            parsed_starting_room.relative_id
                        )
                    else:
                        declared_starting_room_relative_id = None
            if kind != ROOM_MANIFEST_KIND:
                continue
            if (
                builder_manifests.parse_manifest_operation(document)
                != builder_manifests.TRIGGER_MANIFEST_OPERATION_APPLY
            ):
                continue
            metadata = _manifest_metadata(document)
            spec = _manifest_spec(document)
            room_ref = str(metadata.get("ref") or "").strip()
            parsed = parse_room_reference(room_ref)
            if parsed is None or parsed.kind != "relative_id":
                continue
            relative_id = parsed.relative_id
            if relative_id is None or relative_id <= 0:
                raise serializers.ValidationError(
                    "Room manifest relative ids must be positive integers."
                )
            existing = existing_by_relative_id.get(relative_id)
            coordinates = _room_manifest_coordinates(
                room_ref=room_ref,
                spec=spec,
                existing=existing,
            )
            prior = room_specs.get(relative_id)
            definition = (
                coordinates,
                str(metadata.get("name") or "").strip(),
            )
            if prior is not None and prior != definition:
                raise serializers.ValidationError(
                    f"Manifest stream defines room@{relative_id} more than "
                    "once with conflicting values."
                )
            coordinate_ref = coordinate_refs.get(coordinates)
            if coordinate_ref is not None and coordinate_ref != relative_id:
                raise serializers.ValidationError(
                    "Manifest stream assigns the same room coordinates to "
                    f"room@{coordinate_ref} and room@{relative_id}."
                )
            room_specs[relative_id] = definition
            coordinate_refs[coordinates] = relative_id

        placeholder = None
        if (
            has_world_document
            and declared_starting_room_relative_id in room_specs
        ):
            candidate = _find_placeholder_room(
                world,
                allow_import_scaffold_dependents=True,
            )
            if (
                candidate is not None
                and candidate.relative_id not in room_specs
            ):
                placeholder = candidate

        represented_relative_ids = set(room_specs)
        for room in current_rooms:
            if placeholder is not None and room.id == placeholder.id:
                continue
            desired_owner = coordinate_refs.get((room.x, room.y, room.z))
            if (
                desired_owner is not None
                and room.relative_id not in represented_relative_ids
            ):
                raise serializers.ValidationError(
                    "Manifest room coordinates are occupied by an existing "
                    f"room not present in the stream: {_room_ref(room)}."
                )

        movers = [
            existing_by_relative_id[relative_id]
            for relative_id, (coordinates, _name) in room_specs.items()
            if relative_id in existing_by_relative_id
            and (
                existing_by_relative_id[relative_id].x,
                existing_by_relative_id[relative_id].y,
                existing_by_relative_id[relative_id].z,
            ) != coordinates
        ]
        occupied = {
            (room.x, room.y, room.z)
            for room in current_rooms
        } | set(coordinate_refs)
        staged_rooms = [*movers]
        if placeholder is not None:
            staged_rooms.append(placeholder)
        staging_coordinates = _temporary_room_coordinates(
            occupied=occupied,
            count=len(staged_rooms),
        )
        for room, (x, y, z) in zip(staged_rooms, staging_coordinates):
            room.x = x
            room.y = y
            room.z = z
        if staged_rooms:
            Room.objects.bulk_update(staged_rooms, ["x", "y", "z"])

        created_relative_ids: set[int] = set()
        for relative_id in sorted(room_specs):
            if relative_id in existing_by_relative_id:
                continue
            coordinates, name = room_specs[relative_id]
            room = _create_stable_manifest_room(
                world=world,
                relative_id=relative_id,
                coordinates=coordinates,
                name=name,
                coordinates_prevalidated=True,
            )
            existing_by_relative_id[relative_id] = room
            created_relative_ids.add(relative_id)

        for room in movers:
            room.x, room.y, room.z = room_specs[room.relative_id][0]
        if movers:
            Room.objects.bulk_update(movers, ["x", "y", "z"])

        if placeholder is not None:
            from spawns.models import Player

            imported_starting_room = existing_by_relative_id[
                declared_starting_room_relative_id
            ]
            LastViewedRoom.objects.select_for_update().filter(
                room=placeholder,
                world=world,
            ).update(room=imported_starting_room)
            Player.objects.select_for_update().filter(
                room=placeholder,
                is_builder=True,
                in_game=False,
                world__context_id=world.id,
            ).update(room=imported_starting_room)

            config = world.config
            placeholder_zone = _find_placeholder_zone(world)
            if (
                config is None
                or placeholder_zone is None
                or _placeholder_room_has_dependents(
                    room=placeholder,
                    config=config,
                    placeholder_zone=placeholder_zone,
                )
            ):
                raise serializers.ValidationError(
                    "The Starting Room gained dependent records while the "
                    "manifest was being reserved."
                )

            update_fields = []
            if config.starting_room_id == placeholder.id:
                config.starting_room = imported_starting_room
                update_fields.append("starting_room")
            if config.death_room_id == placeholder.id:
                config.death_room = imported_starting_room
                update_fields.append("death_room")
            if update_fields:
                config.save(update_fields=update_fields)

            placeholder.delete()
            current_rooms = [
                room for room in current_rooms if room.id != placeholder.id
            ]
            existing_by_relative_id.pop(placeholder.relative_id, None)

        return created_relative_ids


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

    parsed_room_ref = parse_room_reference(room_ref)
    existing = _existing_room_for_manifest_ref(
        world=world,
        room_ref=room_ref,
    )
    if (
        parsed_room_ref is not None
        and parsed_room_ref.kind == "database_id"
        and existing is None
    ):
        raise serializers.ValidationError(
            "Legacy database-id room manifests can only update an existing "
            "room in this world."
        )
    x, y, z = _room_manifest_coordinates(
        room_ref=room_ref,
        spec=spec,
        existing=existing,
    )
    created = existing is None

    profile_world = world.instance_of or world

    merchant_profile = None
    update_merchant = "merchant" in spec
    if update_merchant:
        merchant = spec.get("merchant")
        if merchant in (None, ""):
            merchant = {}
        if not isinstance(merchant, dict):
            raise serializers.ValidationError("spec.merchant must be a mapping.")
        unknown_merchant_fields = sorted(set(merchant.keys()) - {"profile"})
        if unknown_merchant_fields:
            raise serializers.ValidationError(
                "Unsupported spec.merchant field(s): "
                f"{', '.join(unknown_merchant_fields)}."
            )
        profile_ref = merchant.get("profile")
        if profile_ref not in (None, ""):
            merchant_profile = builder_manifests.resolve_merchant_profile_ref(
                world=profile_world,
                value=profile_ref,
                field_name="spec.merchant.profile",
            )

    crafting_profile = None
    update_crafting = "crafting" in spec
    if update_crafting:
        crafting = spec.get("crafting")
        if crafting in (None, ""):
            crafting = {}
        if not isinstance(crafting, dict):
            raise serializers.ValidationError("spec.crafting must be a mapping.")
        unknown_crafting_fields = sorted(set(crafting.keys()) - {"profile"})
        if unknown_crafting_fields:
            raise serializers.ValidationError(
                "Unsupported spec.crafting field(s): "
                f"{', '.join(unknown_crafting_fields)}."
            )
        profile_ref = crafting.get("profile")
        if profile_ref not in (None, ""):
            crafting_profile = builder_manifests.resolve_crafting_profile_ref(
                world=profile_world,
                value=profile_ref,
                field_name="spec.crafting.profile",
            )

    trainer_profile = None
    update_trainer = "trainer" in spec
    if update_trainer:
        trainer = spec.get("trainer")
        if trainer in (None, ""):
            trainer = {}
        if not isinstance(trainer, dict):
            raise serializers.ValidationError("spec.trainer must be a mapping.")
        unknown_trainer_fields = sorted(set(trainer.keys()) - {"profile"})
        if unknown_trainer_fields:
            raise serializers.ValidationError(
                "Unsupported spec.trainer field(s): "
                f"{', '.join(unknown_trainer_fields)}."
            )
        profile_ref = trainer.get("profile")
        if profile_ref not in (None, ""):
            trainer_profile = builder_manifests.resolve_trainer_profile_ref(
                world=profile_world,
                value=profile_ref,
                field_name="spec.trainer.profile",
            )

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

        if existing is not None:
            room = existing
        elif parsed_room_ref is not None and parsed_room_ref.kind == "relative_id":
            room = _create_stable_manifest_room(
                world=world,
                relative_id=parsed_room_ref.relative_id,
                coordinates=(x, y, z),
                name=room_name,
            )
        else:
            room = _get_or_create_room(
                world=world,
                room_ref=room_ref,
                zone=zone,
            )
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

        coordinate_owner = resolve_room_reference(
            world,
            f"{_ROOM_REF_PREFIX}{x},{y},{z}",
        )
        if (
            coordinate_owner is not None
            and coordinate_owner.pk != room.pk
        ):
            raise serializers.ValidationError(
                "spec.coordinates is already occupied by "
                f"{_room_ref(coordinate_owner)}."
            )
        room.x, room.y, room.z = x, y, z
        merchant_changed = (
            update_merchant
            and room.merchant_profile_id
            != (merchant_profile.id if merchant_profile else None)
        )
        if update_merchant:
            room.merchant_profile = merchant_profile
        if update_crafting:
            room.crafting_profile = crafting_profile
        if update_trainer:
            room.trainer_profile = trainer_profile
        room.save()

        if merchant_changed:
            from spawns.merchants import invalidate_room_merchant_runtimes

            invalidate_room_merchant_runtimes(room)

        if "initial_state" in spec or created:
            replace_initial_state_snapshot(
                STATE_SCOPE_ROOM,
                room,
                _coerce_initial_state(
                    spec.get(
                        "initial_state",
                        get_initial_state_snapshot(STATE_SCOPE_ROOM, room)
                        if existing else {},
                    ),
                    field_name="spec.initial_state",
                ),
            )

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
            door_specs: list[DoorFaceSpec] = []
            seen_directions: set[str] = set()
            for door in doors:
                if not isinstance(door, dict):
                    raise serializers.ValidationError("spec.doors entries must be mappings.")
                direction = builder_manifests._coerce_choice(
                    door.get("direction"),
                    choices=adv_consts.DIRECTIONS,
                    field_name="spec.doors.direction",
                )
                if direction in seen_directions:
                    raise serializers.ValidationError(
                        f"spec.doors contains duplicate direction '{direction}'."
                    )
                seen_directions.add(direction)
                to_room_ref = str(door.get("to_room") or "").strip()
                if not to_room_ref:
                    raise serializers.ValidationError("spec.doors.to_room is required.")
                key_ref = str(door.get("key") or "").strip()
                key = (
                    _get_item_definition(
                        world=world,
                        value=key_ref,
                        field_name="spec.doors.key",
                    )
                    if key_ref else None
                )
                door_specs.append(DoorFaceSpec(
                    direction=direction,
                    to_room=_get_or_create_room(world=world, room_ref=to_room_ref),
                    name=str(door.get("name") or "door"),
                    key_id=key.id if key else None,
                    destroy_key=bool(door.get("destroy_key")),
                    default_state=builder_manifests._coerce_choice(
                        door.get("default_state", adv_consts.DOOR_STATE_CLOSED),
                        choices=adv_consts.DOOR_STATES,
                        field_name="spec.doors.default_state",
                    ),
                ))
            try:
                replace_room_door_faces(room=room, specs=door_specs)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc))

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
            room = _resolve_room_reference_or_error(
                world=world,
                value=room_ref_text,
                field_name=f"spec.rooms[{index}]",
            )
            canonical_ref = _room_ref(room)
            if canonical_ref in seen_room_refs:
                raise serializers.ValidationError(
                    f"spec.rooms[{index}] duplicates room ref '{room_ref_text}'."
                )
            seen_room_refs.add(canonical_ref)
            resolved_rooms.append(room)

        entry_room = None
        entry_room_ref = str(spec.get("entry_room") or "").strip()
        if entry_room_ref:
            entry_room = _resolve_room_reference_or_error(
                world=world,
                value=entry_room_ref,
                field_name="spec.entry_room",
            )

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
    with transaction.atomic():
        path = Path.objects.select_for_update().filter(
            world=world,
            relative_id=relative_id,
        ).first()
        if path is None:
            raise serializers.ValidationError(
                "Path delete manifest does not resolve to an existing path."
            )
        try:
            path.delete()
        except RestrictedError as exc:
            raise serializers.ValidationError(
                "Cannot delete a path referenced by a spawn plan."
            ) from exc
        return path


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


def apply_craft_material_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[CraftMaterial, bool]:
    if parse_document_kind(manifest) != builder_manifests.CRAFT_MATERIAL_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'craftmaterial'.")
    parsed = builder_manifests.parse_craft_material_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.material is None
    return builder_manifests.apply_craft_material_manifest(parsed), created


def apply_crafting_recipe_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[CraftingRecipe, bool]:
    if parse_document_kind(manifest) != builder_manifests.CRAFTING_RECIPE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'craftingrecipe'.")
    parsed = builder_manifests.parse_crafting_recipe_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.recipe is None
    return builder_manifests.apply_crafting_recipe_manifest(parsed), created


def apply_crafting_profile_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[CraftingProfile, bool]:
    if parse_document_kind(manifest) != builder_manifests.CRAFTING_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'craftingprofile'.")
    parsed = builder_manifests.parse_crafting_profile_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.profile is None
    return builder_manifests.apply_crafting_profile_manifest(parsed), created


def apply_trainer_profile_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[TrainerProfile, bool]:
    if parse_document_kind(manifest) != builder_manifests.TRAINER_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'trainerprofile'.")
    parsed = builder_manifests.parse_trainer_profile_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.profile is None
    return builder_manifests.apply_trainer_profile_manifest(parsed), created


def apply_social_manifest(*, world: World, manifest: dict[str, Any]) -> tuple[Social, bool]:
    if parse_document_kind(manifest) != SOCIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'social'."
        )
    parsed = builder_manifests.parse_social_manifest(
        world=world,
        manifest=manifest,
    )
    created = parsed.social is None
    return builder_manifests.apply_social_manifest(parsed), created


def delete_social_manifest(*, world: World, manifest: dict[str, Any]) -> Social:
    if parse_document_kind(manifest) != SOCIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'social'."
        )
    parsed = builder_manifests.parse_social_delete_manifest(
        world=world,
        manifest=manifest,
    )
    social = parsed.social
    social._deleted_payload = builder_manifests.serialize_social_payload(social)
    return builder_manifests.delete_social_manifest(parsed)


def _normalize_faction_manifest_for_import(
    *,
    world: World,
    manifest: dict[str, Any],
    room_references_normalized: bool = False,
) -> dict[str, Any]:
    normalized = (
        copy.deepcopy(manifest)
        if room_references_normalized
        else normalize_manifest_room_references_for_import(
            world=world,
            manifest=manifest,
        )
    )
    spec = normalized.get("spec") or {}
    if not isinstance(spec, dict):
        return normalized
    for field_name in ("starting_room", "death_room"):
        room_ref = str(spec.get(field_name) or "").strip()
        if not room_ref:
            continue
        parsed = parse_room_reference(room_ref)
        if parsed is None or parsed.kind != "relative_id":
            raise serializers.ValidationError(
                f"spec.{field_name} must use canonical "
                "'room@<relative_id>' syntax."
            )
        room = _get_or_create_room(world=world, room_ref=room_ref)
        spec[field_name] = f"room.{room.id}"
    normalized["spec"] = spec
    return normalized


def apply_faction_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
    room_references_normalized: bool = False,
) -> tuple[Faction, bool]:
    if parse_document_kind(manifest) != builder_manifests.FACTION_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'faction'.")

    normalized = _normalize_faction_manifest_for_import(
        world=world,
        manifest=manifest,
        room_references_normalized=room_references_normalized,
    )
    parsed = builder_manifests.parse_faction_manifest(
        world=world,
        manifest=normalized,
    )
    created = parsed.faction is None
    return builder_manifests.apply_faction_manifest(parsed), created


def delete_faction_manifest(*, world: World, manifest: dict[str, Any]) -> Faction:
    if parse_document_kind(manifest) != builder_manifests.FACTION_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'faction'.")
    parsed = builder_manifests.parse_faction_delete_manifest(
        world=world,
        manifest=manifest,
    )
    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(
                world=world,
                config=world.config,
            ),
            shared=False,
        )
        faction = Faction.objects.select_for_update().get(pk=parsed.faction.pk)
        if faction.death_routing_snapshot_references.exists():
            raise serializers.ValidationError(
                "Cannot delete a faction used by active death routing."
            )
        faction._deleted_payload = builder_manifests.serialize_faction_payload(
            faction
        )
        try:
            faction.delete()
        except RestrictedError as exc:
            raise serializers.ValidationError(
                "Cannot delete a faction referenced by death routing."
            ) from exc
        return faction


def apply_spawn_plan_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> tuple[SpawnPlan, bool]:
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
    if room_ref_cache is None:
        room_ref_cache = _build_room_ref_cache(world)
    conditions = _canonicalize_condition_refs(
        conditions,
        world=world,
        room_ref_cache=room_ref_cache,
    )
    respawn_policy = _normalize_spawn_respawn_policy(spec.get("respawn"))

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
        resolved_sources = _validate_spawn_source(
            world=world,
            source=source,
            field_name=f"{entry_field}.source",
        )
        try:
            initial_state = normalize_state_snapshot(
                entry_spec.get("initial_state"),
                field_name=f"{entry_field}.initial_state",
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        if "initial_state" in entry_spec and any(
            resolved.source_type != "mobdefinition"
            for resolved in resolved_sources
        ):
            raise serializers.ValidationError(
                f"{entry_field}.initial_state is only supported when every "
                "source is a mob definition."
            )
        target = _validate_spawn_target(
            world=world,
            target=entry_spec.get("target"),
            entry_slugs=entry_slugs,
            field_name=f"{entry_field}.target",
        )
        entry_conditions = copy.deepcopy(entry_spec.get("conditions") or {})
        _validate_condition_payload_or_error(entry_conditions, field_name=f"{entry_field}.conditions")
        entry_conditions = _canonicalize_condition_refs(
            entry_conditions,
            world=world,
            room_ref_cache=room_ref_cache,
        )
        traits = _normalize_spawn_traits(
            _entry_traits_spec(entry_spec, field_name=entry_field),
            field_name=f"{entry_field}.traits",
        )
        traits = _canonicalize_nested_conditions(
            traits,
            world=world,
            room_ref_cache=room_ref_cache,
        )
        loot = normalize_loot_table(
            entry_spec.get("loot", {}),
            world=world,
            field_name=f"{entry_field}.loot",
            allow_inherit_definition=True,
        )
        loot = _canonicalize_nested_conditions(
            loot,
            world=world,
            room_ref_cache=room_ref_cache,
        )
        normalized_entries.append({
            "slug": entry_slug,
            "name": str(entry_spec.get("name") or ""),
            "order": int(entry_spec.get("order", index + 1) or 0),
            "is_active": bool(entry_spec.get("is_active", True)),
            "source": source,
            "target": target,
            "count": _normalize_spawn_count(entry_spec.get("count", 1), field_name=f"{entry_field}.count"),
            "placement": _normalize_spawn_cohort(entry_spec, field_name=entry_field),
            "initial_state": initial_state,
            "traits": traits,
            "loot": loot,
            "conditions": entry_conditions,
        })

    _validate_spawn_entry_relationships(normalized_entries)

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
        spawn_plan.respawn_policy = respawn_policy
        spawn_plan.randomization = copy.deepcopy(spec.get("randomization") or {})
        spawn_plan.conditions = conditions
        spawn_plan.save()

        existing_entries = {
            entry.slug: entry
            for entry in spawn_plan.entries.select_for_update()
        }
        saved_entries: dict[str, SpawnEntry] = {}
        seen_entry_slugs = {entry["slug"] for entry in normalized_entries}
        ordered_entries = [
            normalized
            for _, normalized in sorted(
                enumerate(normalized_entries),
                key=lambda item: (int(item[1]["order"]), item[0]),
            )
        ]
        for normalized in ordered_entries:
            entry = existing_entries.get(normalized["slug"])
            if entry is None:
                entry = SpawnEntry(plan=spawn_plan, slug=normalized["slug"])
            for field_name in (
                "name",
                "order",
                "is_active",
                "source",
                "count",
                "placement",
                "initial_state",
                "traits",
                "loot",
                "conditions",
            ):
                setattr(entry, field_name, normalized[field_name])

            entry.target_room = None
            entry.target_zone = None
            entry.target_path = None
            entry.target_entry = None
            target = normalized["target"]
            target_kind = target["kind"]
            if target_kind == "room":
                entry.target_room = target["room"]
            elif target_kind == "zone":
                entry.target_zone = target["zone"]
            elif target_kind == "path":
                entry.target_path = target["path"]
            else:
                parent_slug = target["entry_slug"]
                parent_entry = saved_entries.get(parent_slug)
                if parent_entry is None:
                    raise serializers.ValidationError(
                        f"Spawn entry '{entry.slug}' target entry must have a lower order."
                    )
                entry.target_entry = parent_entry
            entry.save()
            saved_entries[entry.slug] = entry
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


def apply_world_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
    room_references_normalized: bool = False,
) -> None:
    if parse_document_kind(manifest) != WORLD_MANIFEST_KIND:
        raise serializers.ValidationError("Unsupported manifest kind. Expected 'world'.")

    with transaction.atomic():
        normalized = (
            copy.deepcopy(manifest)
            if room_references_normalized
            else normalize_manifest_room_references_for_import(
                world=world,
                manifest=manifest,
            )
        )
        spec = _manifest_spec(normalized)
        for field_name in ("starting_room", "death_room"):
            if field_name not in spec:
                continue
            room_ref = str(spec.get(field_name) or "").strip()
            if not room_ref:
                continue
            parsed = parse_room_reference(room_ref)
            if parsed is None or parsed.kind != "relative_id":
                raise serializers.ValidationError(
                    f"spec.{field_name} must use canonical "
                    "'room@<relative_id>' syntax."
                )
            room = _get_or_create_room(world=world, room_ref=room_ref)
            # WorldConfig still stores a database FK; this conversion is an
            # internal parser adapter after authored identity was validated.
            spec[field_name] = f"room.{room.id}"

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
        normalized = copy.deepcopy(manifest)
        normalized["spec"] = _canonicalize_quest_node(
            _manifest_spec(normalized),
            world=world,
            room_ref_cache=_build_room_ref_cache(world),
            canonicalize_entities=False,
        )
        parsed = quest_manifests.parse_quest_manifest(
            world=world,
            manifest=normalized,
        )
        created = parsed.quest is None
        quest = quest_manifests.apply_quest_manifest(parsed)
    return quest, created


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


def normalize_trigger_manifest_for_import(
    *,
    world: World,
    manifest: dict[str, Any],
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    normalized = copy.deepcopy(manifest)
    if builder_manifests.parse_manifest_operation(normalized) == builder_manifests.TRIGGER_MANIFEST_OPERATION_DELETE:
        return normalized

    metadata = _manifest_metadata(normalized)
    spec = _manifest_spec(normalized)
    if room_ref_cache is None:
        room_ref_cache = _build_room_ref_cache(world)
    raw_conditions = spec.get("conditions")
    if isinstance(raw_conditions, str):
        raw_conditions = builder_manifests._deserialize_conditions_payload(
            raw_conditions
        )
    if isinstance(raw_conditions, (dict, list)):
        spec["conditions"] = _canonicalize_condition_refs(
            raw_conditions,
            world=world,
            room_ref_cache=room_ref_cache,
        )
    if isinstance(spec.get("script"), str) and spec["script"]:
        try:
            spec["script"] = canonicalize_command_room_references_in_text(
                world,
                spec["script"],
                strict=True,
                canonical_ref_cache=room_ref_cache,
            )
        except RoomReferenceError as exc:
            raise serializers.ValidationError(str(exc)) from exc
    if isinstance(spec.get("steps"), list):
        spec["steps"] = _canonicalize_trigger_steps(
            _canonicalize_nested_conditions(
                spec["steps"],
                world=world,
                room_ref_cache=room_ref_cache,
            ),
            world=world,
            room_ref_cache=room_ref_cache,
        )

    if "target" not in spec:
        return normalized

    # Identified updates inherit omitted scope/kind fields from the stored
    # Trigger. Leave their target shape intact for the shared parser so legacy
    # untyped locators are resolved against that effective scope rather than a
    # create-time default.
    if any(
        metadata.get(field_name) not in (None, "")
        for field_name in ("id", "key")
    ):
        return normalized

    scope = str(
        spec.get("scope") or adv_consts.TRIGGER_SCOPE_ROOM
    ).strip().lower()
    default_type = builder_manifests._SCOPE_TO_TARGET_TYPE.get(scope)
    target_type, target_id = builder_manifests.resolve_trigger_manifest_target(
        world=world,
        target_data=spec.get("target"),
        default_type=default_type,
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
