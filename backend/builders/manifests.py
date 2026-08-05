from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

import yaml
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify
from rest_framework import serializers

from builders import serializers as builder_serializers
from builders.item_definitions import (
    ItemDefinitionError,
    item_definition_property_fields,
    normalize_attribute_map,
    normalize_item_randomization,
)
from builders.loot_tables import normalize_loot_table
from builders.mob_definitions import mob_definition_property_fields
from builders.models import (
    AbilityDefinition,
    Currency,
    FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
    FACTION_TYPE_CORE,
    FACTION_TYPE_REPUTATION,
    FACTION_TYPES,
    Faction,
    FactionAssignment,
    FactionRank,
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingProfileRecipe,
    CraftingRecipe,
    ItemBundle,
    ItemBundleEntry,
    ItemDefinition,
    ItemSalvageYield,
    MerchantProfile,
    MerchantStockSlot,
    MobDefinition,
    Social,
    Trigger,
)
from config import constants as adv_consts
from core.abilities import (
    AbilityValidationError,
    normalize_ability_definition,
    normalize_ability_progression,
)
from core.condition_dsl import validate_condition_payload
from core.death_routing import (
    DEATH_ROUTING_SOURCE_BASE_WORLD,
    DEATH_ROUTING_SOURCE_LOCAL,
    DEATH_ROUTING_SOURCES,
    DeathRoutingCompilation,
    DeathRoutingValidationError,
    acquire_death_routing_config_locks,
    canonical_death_routing_manifest_value,
    compile_death_routing_policy,
    death_routing_config_ids_for_world,
    rebuild_compiled_policy_snapshot,
    replace_compiled_policy,
    validate_death_routing_archetype_dependencies,
)
from core.combat_formulas import (
    CombatFormulaValidationError,
    get_world_combat_system,
    normalize_combat_system,
)
from core.equipment_system import (
    EquipmentSystemValidationError,
    get_armor_class_keys,
    get_world_equipment_system,
    has_authored_armor_classes,
    has_authored_offhand_weapon_policy,
    normalize_equipment_system,
    validate_armor_class_reference,
)
from core.economy import (
    EconomyConfigurationError,
    default_currency,
    economy_world,
    money_payload,
    validate_currency_amount,
)
from core.factions import (
    faction_is_core,
    faction_is_reputation,
    faction_type_filter,
    normalize_faction_code,
    normalize_player_creation_config,
)
from core.leveling import (
    LevelingConfigError,
    normalize_leveling_curve,
    validate_leveling_config,
)
from core.scoped_state import (
    STATE_SCOPE_WORLD,
    normalize_state_snapshot,
    replace_initial_state_snapshot,
)
from core.mob_traits import normalize_trait_list
from core.socials import (
    SOCIAL_CATALOG_MAX_DEFINITIONS,
    SOCIAL_MESSAGE_FIELDS,
    SocialDefinitionError,
    normalize_social_priority,
    validate_social_command,
    validate_social_definition,
)
from core.stat_system import (
    StatSystemValidationError,
    get_world_stat_system,
    normalize_stat_system,
)
from core.trigger_steps import (
    TriggerStepSpecError,
    normalize_trigger_step_error_policy,
    normalize_trigger_steps,
)
from core.world_config import (
    INSTANCE_INHERITED_MANIFEST_FIELDS,
    INSTANCE_LOCAL_MANIFEST_FIELDS,
)
from spawns import trigger_matcher
from worlds.models import Room, World, WorldConfig, Zone
from worlds.room_refs import (
    format_room_manifest_ref,
    parse_room_reference,
    resolve_room_reference,
)


MANIFEST_API_VERSION = "v1alpha1"
LEGACY_MANIFEST_API_VERSION = "writtenrealms.com/v1alpha1"
STABLE_ROOM_REFS_API_VERSION = "v1alpha2"
STABLE_ROOM_REFS_NAMESPACED_API_VERSION = "writtenrealms.com/v1alpha2"
SCALAR_TRIGGER_TARGETS_API_VERSION = "v1alpha3"
CANONICAL_MANIFEST_API_VERSION = "writtenrealms.com/v1alpha3"
TRIGGER_MANIFEST_KIND = "trigger"
WORLD_MANIFEST_KIND = "world"
QUEST_MANIFEST_KIND = "quest"
QUEST_ARC_MANIFEST_KIND = "questarc"
ITEM_DEFINITION_MANIFEST_KIND = "itemdefinition"
ITEM_BUNDLE_MANIFEST_KIND = "itembundle"
MERCHANT_PROFILE_MANIFEST_KIND = "merchantprofile"
CRAFT_MATERIAL_MANIFEST_KIND = "craftmaterial"
CRAFTING_RECIPE_MANIFEST_KIND = "craftingrecipe"
CRAFTING_PROFILE_MANIFEST_KIND = "craftingprofile"
FACTION_MANIFEST_KIND = "faction"
MOB_DEFINITION_MANIFEST_KIND = "mobdefinition"
ABILITY_MANIFEST_KIND = "ability"
ABILITIES_MANIFEST_KIND = "abilities"
SOCIAL_MANIFEST_KIND = "social"
TRIGGER_MANIFEST_OPERATION_APPLY = "apply"
TRIGGER_MANIFEST_OPERATION_DELETE = "delete"

_TRIGGER_KEY_PREFIX = "trigger"
_SOCIAL_KEY_PREFIX = "social"
_WORLD_KEY_PREFIX = "world"

_QUEST_ARC_MANIFEST_KIND_ALIASES = {
    QUEST_ARC_MANIFEST_KIND,
    "quest-arc",
    "quest_arc",
}
_ITEM_DEFINITION_MANIFEST_KIND_ALIASES = {
    ITEM_DEFINITION_MANIFEST_KIND,
    "item-definition",
    "item_definition",
}
_ITEM_BUNDLE_MANIFEST_KIND_ALIASES = {
    ITEM_BUNDLE_MANIFEST_KIND,
    "item-bundle",
    "item_bundle",
}
_MERCHANT_PROFILE_MANIFEST_KIND_ALIASES = {
    MERCHANT_PROFILE_MANIFEST_KIND,
    "merchant-profile",
    "merchant_profile",
}
_CRAFT_MATERIAL_MANIFEST_KIND_ALIASES = {
    CRAFT_MATERIAL_MANIFEST_KIND,
    "craft-material",
    "craft_material",
}
_CRAFTING_RECIPE_MANIFEST_KIND_ALIASES = {
    CRAFTING_RECIPE_MANIFEST_KIND,
    "crafting-recipe",
    "crafting_recipe",
}
_CRAFTING_PROFILE_MANIFEST_KIND_ALIASES = {
    CRAFTING_PROFILE_MANIFEST_KIND,
    "crafting-profile",
    "crafting_profile",
}
_FACTION_MANIFEST_KIND_ALIASES = {
    FACTION_MANIFEST_KIND,
}
_MOB_DEFINITION_MANIFEST_KIND_ALIASES = {
    MOB_DEFINITION_MANIFEST_KIND,
    "mob-definition",
    "mob_definition",
}
_ABILITY_MANIFEST_KIND_ALIASES = {
    ABILITY_MANIFEST_KIND,
}
_ABILITIES_MANIFEST_KIND_ALIASES = {
    ABILITIES_MANIFEST_KIND,
    "ability-bundle",
    "ability_bundle",
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
    "can_select_gender",
    "auto_equip",
    "is_narrative",
    "players_can_set_title",
    "non_ascii_names",
    "decay_glory",
    "globals_enabled",
    "announce_duel_results",
)
_WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD = "allow_pvp"
_WORLD_CONFIG_LEGACY_BOOL_FIELDS = (
    "is_classless",
)
_WORLD_CONFIG_CONFIG_INT_FIELDS = (
    "starting_level",
    "max_level",
    "default_roam_chance",
    "clan_registration_cost",
)
_WORLD_CONFIG_CONFIG_FLOAT_FIELDS = (
    "combat_resolution_interval",
    "death_currency_penalty",
)
_WORLD_CONFIG_CONFIG_CHOICE_FIELDS = {
    "death_mode": adv_consts.DEATH_MODES,
    "death_route": adv_consts.DEATH_ROUTES,
    "pvp_mode": adv_consts.PVP_MODES,
    "default_gender": adv_consts.GENDERS,
}
_WORLD_CONFIG_CONFIG_ROOM_FIELDS = (
    "starting_room",
    "death_room",
)
_WORLD_CONFIG_STATS_FIELD = "stats"
_WORLD_CONFIG_COMBAT_FIELD = "combat"
_WORLD_CONFIG_EQUIPMENT_FIELD = "equipment"
_WORLD_CONFIG_LEVELING_FIELD = "leveling_curve"
_WORLD_CONFIG_ABILITY_PROGRESS_FIELD = "ability_progression"
_WORLD_CONFIG_PLAYER_CREATION_FIELD = "player_creation"
_WORLD_CONFIG_STARTING_EQUIPMENT_FIELD = "starting_equipment"
_WORLD_CONFIG_DEATH_ROUTING_FIELD = "death_routing"
_WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD = "death_routing_source"
_WORLD_FIELDS_PROPAGATED_TO_SPAWNS = {
    "name",
    "short_description",
    "description",
    "motd",
    "is_public",
}


def _has_authored_world_config_map(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def _export_stat_system(world: World) -> dict[str, Any] | None:
    config = world.config
    if not config or not _has_authored_world_config_map(config.stat_system):
        return None
    try:
        return get_world_stat_system(world)
    except StatSystemValidationError:
        return None


def _export_combat_system(world: World) -> dict[str, Any] | None:
    config = world.config
    if not config or not _has_authored_world_config_map(config.combat_system):
        return None
    try:
        return get_world_combat_system(world)
    except CombatFormulaValidationError:
        return None


def _export_equipment_system(world: World) -> dict[str, Any] | None:
    config = world.config
    if not config or not _has_authored_world_config_map(config.equipment_system):
        return None
    raw_equipment_system = (
        config.equipment_system
        if isinstance(config.equipment_system, dict)
        else {}
    )
    try:
        equipment_system = get_world_equipment_system(world)
    except EquipmentSystemValidationError:
        return None
    if (
        not has_authored_armor_classes(equipment_system)
        and not has_authored_offhand_weapon_policy(raw_equipment_system)
    ):
        return None
    return equipment_system

_SCOPE_TO_TARGET_TYPE = {
    adv_consts.TRIGGER_SCOPE_ROOM: "room",
    adv_consts.TRIGGER_SCOPE_ZONE: "zone",
    adv_consts.TRIGGER_SCOPE_WORLD: "world",
}

_CANONICAL_TRIGGER_ENTITY_TARGET_TYPES = {
    "item_definition": "itemdefinition",
    "mob_definition": "mobdefinition",
}

_TRIGGER_TARGET_MODELS = {
    "room": Room,
    "zone": Zone,
    "world": World,
    "itemdefinition": ItemDefinition,
    "mobdefinition": MobDefinition,
}

_TRIGGER_TARGET_TYPE_BY_MODEL = {
    model_cls: target_type
    for target_type, model_cls in _TRIGGER_TARGET_MODELS.items()
}

_LEGACY_TRIGGER_TARGET_FIELDS = {"type", "ref", "key", "id", "name"}


def _canonical_trigger_entity_target_type(value: Any) -> str:
    target_type = str(value or "").strip().lower()
    return _CANONICAL_TRIGGER_ENTITY_TARGET_TYPES.get(target_type, target_type)

_ITEM_DEFINITION_BASE_PROPERTY_FIELDS = item_definition_property_fields()
_ITEM_DEFINITION_SPEC_FIELDS = (
    "description",
    "room_description",
    "notes",
    "keywords",
    "type",
    "attributes",
    "randomization",
    "salvage",
    "cost",
    "currency",
    *_ITEM_DEFINITION_BASE_PROPERTY_FIELDS,
)
_MOB_DEFINITION_BASE_PROPERTY_FIELDS = mob_definition_property_fields()
_HIT_MESSAGE_FIELDS = {"hit_msg_first", "hit_msg_third"}
_MOB_DEFINITION_SPEC_FIELDS = (
    "description",
    "room_description",
    "notes",
    "keywords",
    "type",
    "assists",
    "attributes",
    "randomization",
    "initial_state",
    "traits",
    "loot",
    "rewards",
    "combat",
    "factions",
    "merchant",
    "crafting",
    "trainer",
    *_MOB_DEFINITION_BASE_PROPERTY_FIELDS,
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
    steps: list[dict[str, Any]]
    on_step_error: str
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
    default_currency: Currency | None = None
    update_default_currency: bool = False
    starting_balances: dict[Currency, int] | None = None
    initial_state: dict[str, Any] | None = None
    update_death_routing: bool = False
    death_routing: DeathRoutingCompilation | None = None
    death_routing_policy: Any = None
    update_death_routing_source: bool = False
    death_routing_source: str | None = None


@dataclass
class ParsedItemDefinitionManifest:
    world: World
    item_definition: ItemDefinition | None
    item_definition_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]
    salvage_yields: list[dict[str, Any]] | None


@dataclass
class ParsedItemDefinitionDeleteManifest:
    world: World
    item_definition: ItemDefinition
    item_definition_id: int


@dataclass
class ParsedItemBundleManifest:
    world: World
    item_bundle: ItemBundle | None
    item_bundle_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]
    entries: list[dict[str, Any]] | None


@dataclass
class ParsedItemBundleDeleteManifest:
    world: World
    item_bundle: ItemBundle
    item_bundle_id: int


@dataclass
class ParsedMerchantProfileManifest:
    world: World
    merchant_profile: MerchantProfile | None
    merchant_profile_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]
    stock_slots: list[dict[str, Any]] | None


@dataclass
class ParsedMerchantProfileDeleteManifest:
    world: World
    merchant_profile: MerchantProfile
    merchant_profile_id: int


@dataclass
class ParsedCraftMaterialManifest:
    world: World
    material: CraftMaterial | None
    material_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]


@dataclass
class ParsedCraftMaterialDeleteManifest:
    world: World
    material: CraftMaterial
    material_id: int


@dataclass
class ParsedCraftingRecipeManifest:
    world: World
    recipe: CraftingRecipe | None
    recipe_id: int | None
    slug: str
    fields: dict[str, Any]
    ingredients: list[dict[str, Any]] | None


@dataclass
class ParsedCraftingRecipeDeleteManifest:
    world: World
    recipe: CraftingRecipe
    recipe_id: int


@dataclass
class ParsedCraftingProfileManifest:
    world: World
    profile: CraftingProfile | None
    profile_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]
    recipes: list[CraftingRecipe] | None


@dataclass
class ParsedCraftingProfileDeleteManifest:
    world: World
    profile: CraftingProfile
    profile_id: int


@dataclass
class ParsedFactionManifest:
    world: World
    faction: Faction | None
    faction_id: int | None
    code: str
    name: str
    fields: dict[str, Any]
    ranks: list[dict[str, Any]] | None


@dataclass
class ParsedFactionDeleteManifest:
    world: World
    faction: Faction
    faction_id: int


@dataclass
class ParsedMobDefinitionManifest:
    world: World
    mob_definition: MobDefinition | None
    mob_definition_id: int | None
    slug: str
    name: str
    fields: dict[str, Any]
    factions: dict[str, Any] | None
    currency_rewards: dict[Currency, int] | None


@dataclass
class ParsedMobDefinitionDeleteManifest:
    world: World
    mob_definition: MobDefinition
    mob_definition_id: int


@dataclass
class ParsedAbilityManifest:
    world: World
    ability: AbilityDefinition | None
    ability_id: int | None
    slug: str
    name: str
    normalized_spec: dict[str, Any]


@dataclass
class ParsedAbilityDeleteManifest:
    world: World
    ability: AbilityDefinition
    ability_id: int


@dataclass
class ParsedAbilitiesManifest:
    world: World
    abilities: list[ParsedAbilityManifest]


@dataclass
class ParsedSocialManifest:
    world: World
    social: Social | None
    social_id: int | None
    command: str
    fields: dict[str, Any]


@dataclass
class ParsedSocialDeleteManifest:
    world: World
    social: Social
    social_id: int


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
    allowed_versions = {
        MANIFEST_API_VERSION,
        LEGACY_MANIFEST_API_VERSION,
        STABLE_ROOM_REFS_API_VERSION,
        STABLE_ROOM_REFS_NAMESPACED_API_VERSION,
        SCALAR_TRIGGER_TARGETS_API_VERSION,
        CANONICAL_MANIFEST_API_VERSION,
    }
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
    if manifest_kind in _ITEM_DEFINITION_MANIFEST_KIND_ALIASES:
        return ITEM_DEFINITION_MANIFEST_KIND
    if manifest_kind in _ITEM_BUNDLE_MANIFEST_KIND_ALIASES:
        return ITEM_BUNDLE_MANIFEST_KIND
    if manifest_kind in _MERCHANT_PROFILE_MANIFEST_KIND_ALIASES:
        return MERCHANT_PROFILE_MANIFEST_KIND
    if manifest_kind in _CRAFT_MATERIAL_MANIFEST_KIND_ALIASES:
        return CRAFT_MATERIAL_MANIFEST_KIND
    if manifest_kind in _CRAFTING_RECIPE_MANIFEST_KIND_ALIASES:
        return CRAFTING_RECIPE_MANIFEST_KIND
    if manifest_kind in _CRAFTING_PROFILE_MANIFEST_KIND_ALIASES:
        return CRAFTING_PROFILE_MANIFEST_KIND
    if manifest_kind in _FACTION_MANIFEST_KIND_ALIASES:
        return FACTION_MANIFEST_KIND
    if manifest_kind in _MOB_DEFINITION_MANIFEST_KIND_ALIASES:
        return MOB_DEFINITION_MANIFEST_KIND
    if manifest_kind in _ABILITY_MANIFEST_KIND_ALIASES:
        return ABILITY_MANIFEST_KIND
    if manifest_kind in _ABILITIES_MANIFEST_KIND_ALIASES:
        return ABILITIES_MANIFEST_KIND
    if manifest_kind == SOCIAL_MANIFEST_KIND:
        return SOCIAL_MANIFEST_KIND
    if manifest_kind == QUEST_MANIFEST_KIND:
        return QUEST_MANIFEST_KIND
    if manifest_kind in _QUEST_ARC_MANIFEST_KIND_ALIASES:
        return QUEST_ARC_MANIFEST_KIND
    raise serializers.ValidationError(
        f"Unsupported manifest kind '{manifest_kind}'. "
        f"Supported kinds: {TRIGGER_MANIFEST_KIND}, {WORLD_MANIFEST_KIND}, {ITEM_DEFINITION_MANIFEST_KIND}, {ITEM_BUNDLE_MANIFEST_KIND}, {MERCHANT_PROFILE_MANIFEST_KIND}, {CRAFT_MATERIAL_MANIFEST_KIND}, {CRAFTING_RECIPE_MANIFEST_KIND}, {CRAFTING_PROFILE_MANIFEST_KIND}, {FACTION_MANIFEST_KIND}, {MOB_DEFINITION_MANIFEST_KIND}, {ABILITY_MANIFEST_KIND}, {ABILITIES_MANIFEST_KIND}, {SOCIAL_MANIFEST_KIND}, {QUEST_MANIFEST_KIND}, {QUEST_ARC_MANIFEST_KIND}."
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


def _coerce_mob_aggression(value: Any, field_name: str) -> str:
    normalized = adv_consts.canonical_mob_aggression(value)
    if normalized not in adv_consts.MOB_AGGRESSION_OPTIONS:
        aliases = ", ".join(sorted(adv_consts.MOB_AGGRESSION_ALIASES.keys()))
        message = (
            f"{field_name} must be one of: "
            f"{', '.join(adv_consts.MOB_AGGRESSION_OPTIONS)}"
        )
        if aliases:
            message = f"{message}; accepted aliases: {aliases}"
        raise serializers.ValidationError(f"{message}.")
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
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or not re.fullmatch(r"[+-]?\d+", text):
            raise serializers.ValidationError(f"{field_name} must be an integer.")
        return int(text)
    try:
        coerced = int(value)
    except (TypeError, ValueError, OverflowError):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    if value != coerced:
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    return coerced


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


def _serialize_world_room_reference(
    *,
    room: Room | None,
    mode: str,
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
    if mode == "key":
        return _entity_key("room", room.id)
    if mode == "coords":
        return f"room@{room.x},{room.y},{room.z}"
    if mode in {"manifest", "relative"}:
        return format_room_manifest_ref(room)
    raise ValueError(f"Unsupported world room reference mode '{mode}'.")


def _canonicalize_payload_manifest(
    manifest: dict[str, Any],
    *,
    world: World,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    # Lazy import avoids a module cycle: world_export builds its source
    # documents from the raw *_to_manifest helpers in this module.
    from builders import world_export as builder_world_export

    return builder_world_export.canonicalize_manifest_for_export(
        manifest=manifest,
        world=world,
        entity_ref_cache=entity_ref_cache,
        room_ref_cache=room_ref_cache,
    )


def _lookup_item_definition_for_ref(*, world: World, value: Any) -> ItemDefinition | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return ItemDefinition.objects.filter(world=world, pk=value).first()

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return ItemDefinition.objects.filter(world=world, pk=int(text)).first()

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itemdefinition", "item_definition"}:
            return None
        text = raw.strip()
        if text.isdigit():
            return ItemDefinition.objects.filter(world=world, pk=int(text)).first()

    if not text:
        return None
    return ItemDefinition.objects.filter(world=world, slug=slugify(text)).first()


def _starting_equipment_ref(definition: ItemDefinition) -> str:
    return f"itemdefinition.{definition.slug}"


def _serialize_starting_equipment_entries(
    *,
    world: World,
    entries: Any,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    serialized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        raw_ref = entry.get("item_definition")
        if raw_ref in (None, ""):
            raw_ref = entry.get("item_definition_id")
        definition = _lookup_item_definition_for_ref(world=world, value=raw_ref)
        if not definition:
            continue

        raw_count = entry.get("count", entry.get("num", 1))
        try:
            count = int(raw_count or 1)
        except (TypeError, ValueError):
            count = 1
        if count < 1:
            continue

        normalized = {
            "item_definition": _starting_equipment_ref(definition),
            "count": count,
        }
        archetype = str(entry.get("archetype") or "").strip()
        if archetype:
            normalized["archetype"] = archetype
        if "equip" in entry:
            normalized["equip"] = _coerce_bool(
                entry.get("equip"),
                "starting_equipment[].equip",
            )
        serialized.append(normalized)

    return serialized


def _normalize_starting_equipment_entries(
    *,
    world: World,
    entries: Any,
) -> list[dict[str, Any]]:
    if entries in (None, ""):
        return []
    if not isinstance(entries, list):
        raise serializers.ValidationError("spec.starting_equipment must be a list.")

    normalized_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        field_name = f"spec.starting_equipment[{index}]"
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"{field_name} must be a mapping.")

        raw_ref = entry.get("item_definition")
        ref_field_name = f"{field_name}.item_definition"
        if raw_ref in (None, ""):
            raw_ref = entry.get("item_definition_id")
            ref_field_name = f"{field_name}.item_definition_id"
        if raw_ref in (None, ""):
            raise serializers.ValidationError(
                f"{field_name}.item_definition is required."
            )
        definition = _resolve_bundle_entry_definition(
            world=world,
            value=raw_ref,
            field_name=ref_field_name,
        )

        raw_count = entry.get("count", entry.get("num", 1))
        count = _coerce_int(raw_count, f"{field_name}.count")
        if count < 1:
            raise serializers.ValidationError(f"{field_name}.count must be >= 1.")

        normalized = {
            "item_definition": _starting_equipment_ref(definition),
            "count": count,
        }
        archetype = str(entry.get("archetype") or "").strip()
        if archetype:
            normalized["archetype"] = archetype
        if "equip" in entry:
            normalized["equip"] = _coerce_bool(
                entry.get("equip"),
                f"{field_name}.equip",
            )
        normalized_entries.append(normalized)

    return normalized_entries


def world_config_to_manifest(
    *,
    world: World,
    manifest_kind: str = WORLD_MANIFEST_KIND,
    include_metadata: bool = True,
    room_reference_mode: str = "manifest",
) -> dict[str, Any]:
    config = world.config
    if not config:
        raise serializers.ValidationError("World has no config to serialize.")

    is_instance_world = bool(getattr(world, "instance_of_id", None))
    spec = {
        "name": world.name or "",
        "short_description": world.short_description or "",
        "description": world.description or "",
        "motd": world.motd or "",
        "is_public": bool(world.is_public),
        "starting_room": _serialize_world_room_reference(
            room=config.starting_room,
            mode=room_reference_mode,
            world=world,
            field_name="World starting room",
        ),
        "death_room": _serialize_world_room_reference(
            room=config.death_room,
            mode=room_reference_mode,
            world=world,
            field_name="World death room",
        ),
        "death_mode": config.death_mode,
        "death_route": config.death_route,
        "death_currency": _serialize_currency_reference(config.death_currency),
        "death_currency_penalty": _serialize_number(config.death_currency_penalty),
        _WORLD_CONFIG_DEATH_ROUTING_FIELD: canonical_death_routing_manifest_value(
            config=config,
            serialize_room=lambda room: _serialize_world_room_reference(
                room=room,
                mode=room_reference_mode,
                world=world,
                field_name="World death-routing destination",
            ),
        ),
        "pvp_mode": config.pvp_mode,
        "built_by": config.built_by or "",
        "small_background": config.small_background or "",
        "large_background": config.large_background or "",
        "initial_state": dict(world.initial_state or {}),
    }
    if is_instance_world:
        spec[_WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD] = (
            config.death_routing_source or DEATH_ROUTING_SOURCE_LOCAL
        )
    if not is_instance_world:
        spec.update(
            {
                "default_currency": _serialize_currency_reference(world.default_currency),
                "starting_balances": {
                    row.currency.code: int(row.amount)
                    for row in world.starting_currency_balances.select_related("currency")
                    if row.amount
                },
                "clan_registration_currency": _serialize_currency_reference(
                    config.clan_registration_currency),
                "clan_registration_cost": int(config.clan_registration_cost),
                _WORLD_CONFIG_STARTING_EQUIPMENT_FIELD: _serialize_starting_equipment_entries(
                    world=world,
                    entries=config.starting_equipment,
                ),
                "starting_level": int(config.starting_level),
                _WORLD_CONFIG_LEVELING_FIELD: normalize_leveling_curve(
                    config.leveling_curve
                ),
                _WORLD_CONFIG_ABILITY_PROGRESS_FIELD: normalize_ability_progression(
                    config.ability_progression
                ),
                "max_level": int(config.max_level),
                "combat_resolution_interval": _serialize_number(
                    config.combat_resolution_interval
                ),
                "default_roam_chance": int(config.default_roam_chance),
                "is_narrative": bool(config.is_narrative),
                _WORLD_CONFIG_PLAYER_CREATION_FIELD: config.player_creation or {},
                "auto_equip": bool(config.auto_equip),
                "players_can_set_title": bool(config.players_can_set_title),
                "non_ascii_names": bool(config.non_ascii_names),
                "globals_enabled": bool(config.globals_enabled),
                "announce_duel_results": bool(config.announce_duel_results),
                "decay_glory": bool(config.decay_glory),
                "name_exclusions": config.name_exclusions or "",
            }
        )
        stat_system = _export_stat_system(world)
        if stat_system:
            spec[_WORLD_CONFIG_STATS_FIELD] = stat_system
        combat_system = _export_combat_system(world)
        if combat_system:
            spec[_WORLD_CONFIG_COMBAT_FIELD] = combat_system
        equipment_system = _export_equipment_system(world)
        if equipment_system:
            spec[_WORLD_CONFIG_EQUIPMENT_FIELD] = equipment_system

    manifest = {
        "kind": manifest_kind,
        "spec": spec,
    }
    if room_reference_mode in {"manifest", "relative"}:
        manifest = {
            "apiVersion": CANONICAL_MANIFEST_API_VERSION,
            **manifest,
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
    room_reference_mode: str = "manifest",
) -> dict[str, Any]:
    manifest = world_config_to_manifest(
        world=world,
        manifest_kind=manifest_kind,
        include_metadata=include_metadata,
        room_reference_mode=room_reference_mode,
    )
    if room_reference_mode in {"manifest", "relative"}:
        manifest = _canonicalize_payload_manifest(
            manifest,
            world=world,
        )
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def serialize_world_config_payload(*, world: World) -> dict[str, Any]:
    config = world.config
    if not config:
        raise serializers.ValidationError("World has no config to serialize.")

    is_instance_world = bool(getattr(world, "instance_of_id", None))
    manifest_data = serialize_world_config_manifest(
        world=world,
        manifest_kind=WORLD_MANIFEST_KIND,
        include_metadata=False,
        room_reference_mode="manifest",
    )
    config_payload = {
        "starting_room": _serialize_room_reference(config.starting_room),
        "death_room": _serialize_room_reference(config.death_room),
        "death_mode": config.death_mode,
        "death_route": config.death_route,
        "death_currency": _serialize_currency_reference(config.death_currency),
        "death_currency_penalty": _serialize_number(config.death_currency_penalty),
        _WORLD_CONFIG_DEATH_ROUTING_FIELD: canonical_death_routing_manifest_value(
            config=config,
            serialize_room=lambda room: _serialize_world_room_reference(
                room=room,
                mode="manifest",
                world=world,
                field_name="World death-routing destination",
            ),
        ),
        "small_background": config.small_background or "",
        "large_background": config.large_background or "",
        "pvp_mode": config.pvp_mode,
        "built_by": config.built_by or "",
    }
    if is_instance_world:
        config_payload[_WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD] = (
            config.death_routing_source or DEATH_ROUTING_SOURCE_LOCAL
        )
    if not is_instance_world:
        config_payload.update(
            {
                "default_currency": _serialize_currency_reference(world.default_currency),
                "starting_balances": {
                    row.currency.code: int(row.amount)
                    for row in world.starting_currency_balances.select_related("currency")
                    if row.amount
                },
                "clan_registration_currency": _serialize_currency_reference(
                    config.clan_registration_currency),
                "clan_registration_cost": int(config.clan_registration_cost),
                _WORLD_CONFIG_STARTING_EQUIPMENT_FIELD: _serialize_starting_equipment_entries(
                    world=world,
                    entries=config.starting_equipment,
                ),
                "starting_level": int(config.starting_level),
                _WORLD_CONFIG_LEVELING_FIELD: normalize_leveling_curve(
                    config.leveling_curve
                ),
                _WORLD_CONFIG_ABILITY_PROGRESS_FIELD: normalize_ability_progression(
                    config.ability_progression
                ),
                "max_level": int(config.max_level),
                "combat_resolution_interval": _serialize_number(
                    config.combat_resolution_interval
                ),
                "default_roam_chance": int(config.default_roam_chance),
                "allow_combat": bool(config.allow_combat),
                "is_narrative": bool(config.is_narrative),
                _WORLD_CONFIG_PLAYER_CREATION_FIELD: config.player_creation or {},
                "can_select_faction": bool(config.can_select_faction),
                "auto_equip": bool(config.auto_equip),
                "players_can_set_title": bool(config.players_can_set_title),
                "non_ascii_names": bool(config.non_ascii_names),
                "announce_duel_results": bool(config.announce_duel_results),
                "decay_glory": bool(config.decay_glory),
                "name_exclusions": config.name_exclusions or "",
                "globals_enabled": bool(config.globals_enabled),
                "stat_system": _export_stat_system(world) or {},
                "combat_system": _export_combat_system(world) or {},
                "equipment_system": _export_equipment_system(world) or {},
            }
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
            "instance_of_id": world.instance_of_id,
        },
        "config": config_payload,
        "manifest": manifest_data["manifest"],
        "yaml": manifest_data["yaml"],
    }


def _serialize_currency_reference(currency: Currency | None) -> str:
    if currency is None:
        return ""
    return currency.code or ""


def _item_definition_spec_from_instance(item_definition: ItemDefinition) -> dict[str, Any]:
    spec = {
        "description": item_definition.description or "",
        "room_description": item_definition.room_description or "",
        "notes": item_definition.notes or "",
        "keywords": item_definition.keywords or "",
        "type": item_definition.item_type or adv_consts.ITEM_TYPE_INERT,
    }
    for field_name, value in (item_definition.base_properties or {}).items():
        if value is None:
            spec[field_name] = ""
        else:
            spec[field_name] = value
    if item_definition.cost is not None:
        spec["cost"] = int(item_definition.cost)
        spec["currency"] = _serialize_currency_reference(item_definition.currency)
    if item_definition.attributes:
        spec["attributes"] = item_definition.attributes
    else:
        spec["attributes"] = {}
    if item_definition.randomization:
        spec["randomization"] = item_definition.randomization
    else:
        spec["randomization"] = {}
    salvage_yields = _item_salvage_entries(item_definition)
    if item_definition.salvage_only or salvage_yields:
        spec["salvage"] = {
            "only": bool(item_definition.salvage_only),
            "yields": salvage_yields,
        }
    return spec


def _item_salvage_entries(item_definition: ItemDefinition) -> list[dict[str, Any]]:
    prefetched = getattr(item_definition, "_prefetched_objects_cache", {})
    yields = prefetched.get("salvage_yields")
    if yields is None:
        yields = list(
            item_definition.salvage_yields.select_related("material").all()
        )
    else:
        yields = list(yields)
    yields.sort(key=lambda entry: (
        int(entry.material.order),
        (entry.material.name or "").lower(),
        entry.material_id,
    ))
    return [
        {
            "material": f"{CRAFT_MATERIAL_MANIFEST_KIND}.{entry.material.slug}",
            "quantity": int(entry.quantity),
        }
        for entry in yields
    ]


def item_definition_to_manifest(item_definition: ItemDefinition) -> dict[str, Any]:
    return {
        "kind": ITEM_DEFINITION_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_definition.world_id),
            "id": item_definition.id,
            "key": item_definition.key,
            "slug": item_definition.slug,
            "name": item_definition.name or "",
        },
        "spec": _item_definition_spec_from_instance(item_definition),
    }


def item_definition_delete_manifest(item_definition: ItemDefinition) -> dict[str, Any]:
    return {
        "kind": ITEM_DEFINITION_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_definition.world_id),
            "id": item_definition.id,
            "key": item_definition.key,
            "slug": item_definition.slug,
            "name": item_definition.name or "",
        },
    }


def serialize_item_definition_payload(
    item_definition: ItemDefinition,
    *,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    manifest = _canonicalize_payload_manifest(
        item_definition_to_manifest(item_definition),
        world=item_definition.world,
        entity_ref_cache=entity_ref_cache,
        room_ref_cache=room_ref_cache,
    )
    delete_manifest = item_definition_delete_manifest(item_definition)
    return {
        "id": item_definition.id,
        "key": item_definition.key,
        "slug": item_definition.slug,
        "name": item_definition.name or "",
        "description": item_definition.description or "",
        "room_description": item_definition.room_description or "",
        "keywords": item_definition.keywords or "",
        "notes": item_definition.notes or "",
        "type": item_definition.item_type,
        "base_properties": item_definition.base_properties or {},
        "attributes": item_definition.attributes or {},
        "randomization": item_definition.randomization or {},
        "salvage_only": bool(item_definition.salvage_only),
        "salvage": manifest["spec"].get("salvage", {}),
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def _faction_spec_from_instance(
    faction: Faction,
    *,
    room_reference_mode: str = "manifest",
) -> dict[str, Any]:
    faction_type = FACTION_TYPE_CORE if faction_is_core(faction) else FACTION_TYPE_REPUTATION
    spec: dict[str, Any] = {
        "type": faction_type,
        "description": faction.description or "",
    }
    if faction.notes:
        spec["notes"] = faction.notes
    if faction_type == FACTION_TYPE_CORE:
        spec["playable"] = bool(faction.playable or faction.is_selectable)
        spec["starting_room"] = _serialize_world_room_reference(
            room=faction.starting_room,
            mode=room_reference_mode,
            world=faction.world_id,
            field_name=f"Faction '{faction.code}' starting room",
        )
        spec["death_room"] = _serialize_world_room_reference(
            room=faction.death_room,
            mode=room_reference_mode,
            world=faction.world_id,
            field_name=f"Faction '{faction.code}' death room",
        )
        if faction.default_languages:
            spec["default_languages"] = list(faction.default_languages or [])
    else:
        ranks = [
            {
                "standing": int(rank.standing),
                "name": rank.name or "",
            }
            for rank in faction.ranks.all().order_by("standing", "id")
        ]
        if ranks:
            spec["ranks"] = ranks
    return spec


def faction_to_manifest(
    faction: Faction,
    *,
    room_reference_mode: str = "manifest",
) -> dict[str, Any]:
    return {
        "kind": FACTION_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, faction.world_id),
            "id": faction.id,
            "key": _entity_key(FACTION_MANIFEST_KIND, faction.id),
            "code": faction.code,
            "name": faction.name or "",
        },
        "spec": _faction_spec_from_instance(
            faction,
            room_reference_mode=room_reference_mode,
        ),
    }


def faction_delete_manifest(faction: Faction) -> dict[str, Any]:
    return {
        "kind": FACTION_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, faction.world_id),
            "id": faction.id,
            "key": _entity_key(FACTION_MANIFEST_KIND, faction.id),
            "code": faction.code,
            "name": faction.name or "",
        },
    }


def serialize_faction_payload(faction: Faction) -> dict[str, Any]:
    manifest = _canonicalize_payload_manifest(
        faction_to_manifest(
            faction,
            room_reference_mode="manifest",
        ),
        world=faction.world,
    )
    delete_manifest = faction_delete_manifest(faction)
    return {
        "id": faction.id,
        "key": _entity_key(FACTION_MANIFEST_KIND, faction.id),
        "code": faction.code,
        "name": faction.name or "",
        "description": faction.description or "",
        "notes": faction.notes or "",
        "type": manifest["spec"]["type"],
        "playable": bool(faction.playable),
        "default_languages": faction.default_languages or [],
        "starting_room": _serialize_room_reference(faction.starting_room),
        "death_room": _serialize_room_reference(faction.death_room),
        "ranks": [
            {
                "standing": int(rank.standing),
                "name": rank.name or "",
            }
            for rank in faction.ranks.all().order_by("standing", "id")
        ],
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def _mob_definition_aggression(mob_definition: MobDefinition) -> str:
    return adv_consts.canonical_mob_aggression(
        (mob_definition.base_properties or {}).get("aggression")
        or adv_consts.MOB_AGGRESSION_PASSIVE
    )


def faction_assignments_to_manifest_spec(member) -> dict[str, Any]:
    prefetched = getattr(member, "_prefetched_objects_cache", {})
    assignments = prefetched.get("faction_assignments")
    if assignments is None:
        assignments = member.faction_assignments.select_related("faction").all()

    core_code = None
    reputation: dict[str, int] = {}
    for assignment in assignments:
        faction = assignment.faction
        if not faction:
            continue
        if faction_is_core(faction):
            if core_code is None:
                core_code = faction.code
            continue
        reputation[faction.code] = int(assignment.value or 0)

    spec: dict[str, Any] = {}
    if core_code:
        spec["core"] = core_code
    if reputation:
        spec["reputation"] = {
            code: reputation[code]
            for code in sorted(reputation.keys())
        }
    return spec


def _mob_definition_spec_from_instance(mob_definition: MobDefinition) -> dict[str, Any]:
    spec = {
        "description": mob_definition.description or "",
        "room_description": mob_definition.room_description or "",
        "notes": mob_definition.notes or "",
        "keywords": mob_definition.keywords or "",
        "type": mob_definition.mob_type or adv_consts.MOB_TYPE_BEAST,
        "assists": bool(mob_definition.assists),
    }
    for field_name, value in (mob_definition.base_properties or {}).items():
        if field_name == "traits":
            continue
        if value is None:
            spec[field_name] = ""
        else:
            spec[field_name] = value
    spec["aggression"] = _mob_definition_aggression(mob_definition)
    spec["combat"] = {
        "attackable": bool(mob_definition.attackable),
    }
    if mob_definition.combat_abilities:
        spec["combat"]["abilities"] = mob_definition.combat_abilities or []
    if mob_definition.merchant_profile_id:
        spec["merchant"] = {
            "profile": f"merchantprofile.{mob_definition.merchant_profile.slug}",
            "availability": mob_definition.merchant_availability or "present",
        }
    if mob_definition.crafting_profile_id:
        spec["crafting"] = {
            "profile": f"{CRAFTING_PROFILE_MANIFEST_KIND}.{mob_definition.crafting_profile.slug}",
            "availability": mob_definition.crafting_availability or "present",
        }
    if mob_definition.trainer:
        spec["trainer"] = mob_definition.trainer or {}
    spec["attributes"] = mob_definition.attributes or {}
    spec["randomization"] = mob_definition.randomization or {}
    if mob_definition.initial_state:
        spec["initial_state"] = mob_definition.initial_state or {}
    if mob_definition.traits:
        spec["traits"] = mob_definition.traits or []
    if mob_definition.loot:
        spec["loot"] = mob_definition.loot or {}
    rewards = getattr(mob_definition, "_prefetched_objects_cache", {}).get(
        "currency_rewards")
    if rewards is None:
        rewards = mob_definition.currency_rewards.select_related("currency").all()
    currency_rewards = {
        reward.currency.code: int(reward.amount)
        for reward in rewards
        if reward.amount
    }
    if currency_rewards:
        spec["rewards"] = {"currencies": currency_rewards}
    factions = faction_assignments_to_manifest_spec(mob_definition)
    if factions:
        spec["factions"] = factions
    return spec


def mob_definition_to_manifest(mob_definition: MobDefinition) -> dict[str, Any]:
    return {
        "kind": MOB_DEFINITION_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, mob_definition.world_id),
            "id": mob_definition.id,
            "key": mob_definition.key,
            "slug": mob_definition.slug,
            "name": mob_definition.name or "",
        },
        "spec": _mob_definition_spec_from_instance(mob_definition),
    }


def mob_definition_delete_manifest(mob_definition: MobDefinition) -> dict[str, Any]:
    return {
        "kind": MOB_DEFINITION_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, mob_definition.world_id),
            "id": mob_definition.id,
            "key": mob_definition.key,
            "slug": mob_definition.slug,
            "name": mob_definition.name or "",
        },
    }


def serialize_mob_definition_payload(
    mob_definition: MobDefinition,
    *,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    manifest = _canonicalize_payload_manifest(
        mob_definition_to_manifest(mob_definition),
        world=mob_definition.world,
        entity_ref_cache=entity_ref_cache,
        room_ref_cache=room_ref_cache,
    )
    delete_manifest = mob_definition_delete_manifest(mob_definition)
    return {
        "id": mob_definition.id,
        "key": mob_definition.key,
        "slug": mob_definition.slug,
        "name": mob_definition.name or "",
        "description": mob_definition.description or "",
        "room_description": mob_definition.room_description or "",
        "keywords": mob_definition.keywords or "",
        "notes": mob_definition.notes or "",
        "type": mob_definition.mob_type,
        "assists": bool(mob_definition.assists),
        "aggression": _mob_definition_aggression(mob_definition),
        "base_properties": mob_definition.base_properties or {},
        "attributes": mob_definition.attributes or {},
        "randomization": mob_definition.randomization or {},
        "initial_state": mob_definition.initial_state or {},
        "loot": mob_definition.loot or {},
        "factions": faction_assignments_to_manifest_spec(mob_definition),
        "combat_abilities": mob_definition.combat_abilities or [],
        "attackable": bool(mob_definition.attackable),
        "trainer": mob_definition.trainer or {},
        "merchant_profile": (
            {
                "id": mob_definition.merchant_profile_id,
                "key": mob_definition.merchant_profile.key,
                "slug": mob_definition.merchant_profile.slug,
                "name": mob_definition.merchant_profile.name,
            }
            if mob_definition.merchant_profile_id else None
        ),
        "merchant_availability": mob_definition.merchant_availability or "present",
        "crafting_profile": (
            {
                "id": mob_definition.crafting_profile_id,
                "key": mob_definition.crafting_profile.key,
                "slug": mob_definition.crafting_profile.slug,
                "name": mob_definition.crafting_profile.name,
            }
            if mob_definition.crafting_profile_id else None
        ),
        "crafting_availability": mob_definition.crafting_availability or "present",
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def item_bundle_to_manifest(item_bundle: ItemBundle) -> dict[str, Any]:
    return {
        "kind": ITEM_BUNDLE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_bundle.world_id),
            "id": item_bundle.id,
            "key": item_bundle.key,
            "slug": item_bundle.slug,
            "name": item_bundle.name or "",
        },
        "spec": {
            "notes": item_bundle.notes or "",
            "entries": [
                {
                    "item_definition": entry.item_definition.slug,
                    "weight": int(entry.weight),
                    "min_quantity": int(entry.min_quantity),
                    "max_quantity": int(entry.max_quantity),
                    "probability": int(entry.probability),
                }
                for entry in item_bundle.entries.select_related("item_definition").all().order_by(
                    "created_ts", "id"
                )
            ],
        },
    }


def item_bundle_delete_manifest(item_bundle: ItemBundle) -> dict[str, Any]:
    return {
        "kind": ITEM_BUNDLE_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, item_bundle.world_id),
            "id": item_bundle.id,
            "key": item_bundle.key,
            "slug": item_bundle.slug,
            "name": item_bundle.name or "",
        },
    }


def serialize_item_bundle_payload(item_bundle: ItemBundle) -> dict[str, Any]:
    manifest = item_bundle_to_manifest(item_bundle)
    delete_manifest = item_bundle_delete_manifest(item_bundle)
    return {
        "id": item_bundle.id,
        "key": item_bundle.key,
        "slug": item_bundle.slug,
        "name": item_bundle.name or "",
        "notes": item_bundle.notes or "",
        "entries": manifest["spec"]["entries"],
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def merchant_profile_to_manifest(merchant_profile: MerchantProfile) -> dict[str, Any]:
    return {
        "kind": MERCHANT_PROFILE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, merchant_profile.world_id),
            "id": merchant_profile.id,
            "key": merchant_profile.key,
            "slug": merchant_profile.slug,
            "name": merchant_profile.name or "",
        },
        "spec": {
            "notes": merchant_profile.notes or "",
            "settlement_currency": merchant_profile.settlement_currency.code,
            "pricing": {
                "sell_markup": _serialize_number(merchant_profile.sell_markup),
                "buy_multiplier": _serialize_number(merchant_profile.buy_multiplier),
            },
            "restock": {
                "interval_seconds": merchant_profile.restock_interval_seconds,
            },
            "funds": {
                "mode": merchant_profile.funds_mode,
                "purchase_budget": int(merchant_profile.purchase_budget or 0),
            },
            "buyback": {
                "enabled": bool(merchant_profile.buyback_enabled),
                "max_items": int(merchant_profile.buyback_max_items or 0),
                "expires": merchant_profile.buyback_expires,
            },
            "stock": [
                {
                    **{
                        "key": slot.key,
                        "count": int(slot.count),
                        "refresh": slot.refresh,
                    },
                    **(
                        {"item_definition": slot.item_definition.slug}
                        if slot.item_definition_id
                        else {"item_bundle": slot.item_bundle.slug}
                    ),
                }
                for slot in merchant_profile.stock_slots.select_related(
                    "item_definition",
                    "item_bundle",
                ).all().order_by("created_ts", "id")
            ],
        },
    }


def merchant_profile_delete_manifest(merchant_profile: MerchantProfile) -> dict[str, Any]:
    return {
        "kind": MERCHANT_PROFILE_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, merchant_profile.world_id),
            "id": merchant_profile.id,
            "key": merchant_profile.key,
            "slug": merchant_profile.slug,
            "name": merchant_profile.name or "",
        },
    }


def serialize_merchant_profile_payload(merchant_profile: MerchantProfile) -> dict[str, Any]:
    manifest = merchant_profile_to_manifest(merchant_profile)
    delete_manifest = merchant_profile_delete_manifest(merchant_profile)
    return {
        "id": merchant_profile.id,
        "key": merchant_profile.key,
        "slug": merchant_profile.slug,
        "name": merchant_profile.name or "",
        "notes": merchant_profile.notes or "",
        "sell_markup": merchant_profile.sell_markup,
        "buy_multiplier": merchant_profile.buy_multiplier,
        "restock_interval_seconds": merchant_profile.restock_interval_seconds,
        "funds_mode": merchant_profile.funds_mode,
        "settlement_currency": merchant_profile.settlement_currency.code,
        "purchase_budget": merchant_profile.purchase_budget,
        "buyback_enabled": bool(merchant_profile.buyback_enabled),
        "buyback_max_items": merchant_profile.buyback_max_items,
        "buyback_expires": merchant_profile.buyback_expires,
        "stock": manifest["spec"]["stock"],
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def craft_material_to_manifest(material: CraftMaterial) -> dict[str, Any]:
    return {
        "kind": CRAFT_MATERIAL_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, material.world_id),
            "id": material.id,
            "key": material.key,
            "slug": material.slug,
            "name": material.name or "",
        },
        "spec": {
            "description": material.description or "",
            "order": int(material.order),
        },
    }


def craft_material_delete_manifest(material: CraftMaterial) -> dict[str, Any]:
    return {
        "kind": CRAFT_MATERIAL_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, material.world_id),
            "id": material.id,
            "key": material.key,
            "slug": material.slug,
            "name": material.name or "",
        },
    }


def serialize_craft_material_payload(material: CraftMaterial) -> dict[str, Any]:
    manifest = craft_material_to_manifest(material)
    delete_manifest = craft_material_delete_manifest(material)
    return {
        "id": material.id,
        "key": material.key,
        "slug": material.slug,
        "name": material.name or "",
        "description": material.description or "",
        "order": int(material.order),
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def _recipe_ingredient_entries(recipe: CraftingRecipe) -> list[dict[str, Any]]:
    prefetched = getattr(recipe, "_prefetched_objects_cache", {})
    ingredients = prefetched.get("ingredients")
    if ingredients is None:
        ingredients = list(recipe.ingredients.select_related("material").all())
    else:
        ingredients = list(ingredients)
    return [
        {
            "material": f"{CRAFT_MATERIAL_MANIFEST_KIND}.{ingredient.material.slug}",
            "quantity": int(ingredient.quantity),
        }
        for ingredient in ingredients
    ]


def crafting_recipe_to_manifest(recipe: CraftingRecipe) -> dict[str, Any]:
    spec = {
        "group": recipe.group or "",
        "order": int(recipe.order),
        "output": {
            "item_definition": (
                f"{ITEM_DEFINITION_MANIFEST_KIND}.{recipe.output_item_definition.slug}"
            ),
        },
        "inputs": _recipe_ingredient_entries(recipe),
        "conditions": recipe.conditions or {},
        "failure_message": recipe.failure_message or "",
    }
    if recipe.cost is not None:
        spec["cost"] = int(recipe.cost)
        spec["currency"] = _serialize_currency_reference(recipe.currency)
    return {
        "kind": CRAFTING_RECIPE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, recipe.world_id),
            "id": recipe.id,
            "key": recipe.key,
            "slug": recipe.slug,
        },
        "spec": spec,
    }


def crafting_recipe_delete_manifest(recipe: CraftingRecipe) -> dict[str, Any]:
    return {
        "kind": CRAFTING_RECIPE_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, recipe.world_id),
            "id": recipe.id,
            "key": recipe.key,
            "slug": recipe.slug,
        },
    }


def serialize_crafting_recipe_payload(recipe: CraftingRecipe) -> dict[str, Any]:
    manifest = _canonicalize_payload_manifest(
        crafting_recipe_to_manifest(recipe),
        world=recipe.world,
    )
    delete_manifest = crafting_recipe_delete_manifest(recipe)
    return {
        "id": recipe.id,
        "key": recipe.key,
        "slug": recipe.slug,
        "name": recipe.name or "",
        "group": recipe.group or "",
        "order": int(recipe.order),
        "cost": int(recipe.cost) if recipe.cost is not None else None,
        "currency": _serialize_currency_reference(recipe.currency) or None,
        "money": (
            money_payload(int(recipe.cost), recipe.currency)
            if recipe.cost is not None and recipe.currency is not None
            else None
        ),
        "conditions": recipe.conditions or {},
        "failure_message": recipe.failure_message or "",
        "output_item_definition": {
            "id": recipe.output_item_definition_id,
            "key": recipe.output_item_definition.key,
            "slug": recipe.output_item_definition.slug,
            "name": recipe.output_item_definition.name,
        },
        "inputs": manifest["spec"]["inputs"],
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def _profile_recipe_refs(profile: CraftingProfile) -> list[str]:
    prefetched = getattr(profile, "_prefetched_objects_cache", {})
    entries = prefetched.get("recipe_entries")
    if entries is None:
        entries = list(profile.recipe_entries.select_related("recipe").all())
    else:
        entries = list(entries)
    entries.sort(key=lambda entry: (int(entry.order), entry.id))
    return [
        f"{CRAFTING_RECIPE_MANIFEST_KIND}.{entry.recipe.slug}"
        for entry in entries
    ]


def crafting_profile_to_manifest(profile: CraftingProfile) -> dict[str, Any]:
    return {
        "kind": CRAFTING_PROFILE_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, profile.world_id),
            "id": profile.id,
            "key": profile.key,
            "slug": profile.slug,
            "name": profile.name or "",
        },
        "spec": {
            "keywords": profile.keywords or "",
            "recipes": _profile_recipe_refs(profile),
        },
    }


def crafting_profile_delete_manifest(profile: CraftingProfile) -> dict[str, Any]:
    return {
        "kind": CRAFTING_PROFILE_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, profile.world_id),
            "id": profile.id,
            "key": profile.key,
            "slug": profile.slug,
            "name": profile.name or "",
        },
    }


def serialize_crafting_profile_payload(profile: CraftingProfile) -> dict[str, Any]:
    manifest = crafting_profile_to_manifest(profile)
    delete_manifest = crafting_profile_delete_manifest(profile)
    return {
        "id": profile.id,
        "key": profile.key,
        "slug": profile.slug,
        "name": profile.name or "",
        "keywords": profile.keywords or "",
        "recipes": manifest["spec"]["recipes"],
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def ability_to_manifest(ability: AbilityDefinition) -> dict[str, Any]:
    return {
        "kind": ABILITY_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, ability.world_id),
            "id": ability.id,
            "key": _entity_key("ability", ability.id),
            "slug": ability.slug,
            "name": ability.name or "",
        },
        "spec": {
            "version": 1,
            "command": {"verbs": list(ability.command_verbs or [])},
            "consumes_primary_action_on_resolve": bool(
                ability.consumes_primary_action_on_resolve
            ),
            "consumes_primary_action_while_casting": bool(
                ability.consumes_primary_action_while_casting
            ),
            "target": ability.target or {},
            "availability": ability.availability or {},
            "requirements": ability.requirements or {},
            "cost": ability.cost or {},
            "cast_time": ability.cast_time or {},
            "cooldown": ability.cooldown or {},
            "help": ability.help or {},
            "components": ability.components or [],
            "is_active": bool(ability.is_active),
        },
    }


def ability_delete_manifest(ability: AbilityDefinition) -> dict[str, Any]:
    return {
        "kind": ABILITY_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, ability.world_id),
            "id": ability.id,
            "key": _entity_key("ability", ability.id),
            "slug": ability.slug,
            "name": ability.name or "",
        },
    }


def serialize_ability_payload(
    ability: AbilityDefinition,
    *,
    entity_ref_cache: dict[tuple[str, str, Any], str] | None = None,
    room_ref_cache: dict[tuple[Any, ...], str] | None = None,
) -> dict[str, Any]:
    manifest = _canonicalize_payload_manifest(
        ability_to_manifest(ability),
        world=ability.world,
        entity_ref_cache=entity_ref_cache,
        room_ref_cache=room_ref_cache,
    )
    delete_manifest = ability_delete_manifest(ability)
    return {
        "id": ability.id,
        "key": _entity_key("ability", ability.id),
        "slug": ability.slug,
        "name": ability.name or "",
        "command_verbs": list(ability.command_verbs or []),
        "consumes_primary_action_on_resolve": bool(
            ability.consumes_primary_action_on_resolve
        ),
        "consumes_primary_action_while_casting": bool(
            ability.consumes_primary_action_while_casting
        ),
        "target": ability.target or {},
        "availability": ability.availability or {},
        "requirements": ability.requirements or {},
        "cost": ability.cost or {},
        "cast_time": ability.cast_time or {},
        "cooldown": ability.cooldown or {},
        "help": ability.help or {},
        "components": ability.components or [],
        "is_active": bool(ability.is_active),
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def _validated_social_values(social: Social) -> tuple[str, dict[str, str]]:
    try:
        command = validate_social_command(
            social.cmd,
            field_name="metadata.command",
        )
        definition = validate_social_definition({
            field_name: getattr(social, field_name, "") or ""
            for field_name in SOCIAL_MESSAGE_FIELDS
        })
    except SocialDefinitionError as exc:
        raise serializers.ValidationError(
            f"Social '{social.cmd}' is invalid: {exc}"
        )
    return command, definition


def social_to_manifest(social: Social) -> dict[str, Any]:
    command, definition = _validated_social_values(social)
    return {
        "kind": SOCIAL_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, social.world_id),
            "id": social.id,
            "key": _entity_key(_SOCIAL_KEY_PREFIX, social.id),
            "command": command,
        },
        "spec": {
            "priority": int(social.priority),
            "targetless": {
                "self": definition["msg_targetless_self"],
                "others": definition["msg_targetless_other"],
            },
            "targeted": {
                "self": definition["msg_targeted_self"],
                "target": definition["msg_targeted_target"],
                "others": definition["msg_targeted_other"],
            },
        },
    }


def social_delete_manifest(social: Social) -> dict[str, Any]:
    try:
        command = validate_social_command(
            social.cmd,
            field_name="metadata.command",
        )
    except SocialDefinitionError as exc:
        raise serializers.ValidationError(
            f"Social '{social.cmd}' is invalid: {exc}"
        )
    return {
        "kind": SOCIAL_MANIFEST_KIND,
        "operation": TRIGGER_MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, social.world_id),
            "id": social.id,
            "key": _entity_key(_SOCIAL_KEY_PREFIX, social.id),
            "command": command,
        },
    }


def serialize_social_payload(social: Social) -> dict[str, Any]:
    manifest = social_to_manifest(social)
    delete_manifest = social_delete_manifest(social)
    spec = manifest["spec"]
    payload = {
        "id": social.id,
        "key": _entity_key(_SOCIAL_KEY_PREFIX, social.id),
        "world": _entity_key(_WORLD_KEY_PREFIX, social.world_id),
        "command": manifest["metadata"]["command"],
        "cmd": manifest["metadata"]["command"],
        "priority": spec["priority"],
        "targetless": dict(spec["targetless"]),
        "targeted": dict(spec["targeted"]),
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }
    payload.update({
        "msg_targetless_self": spec["targetless"]["self"],
        "msg_targetless_other": spec["targetless"]["others"],
        "msg_targeted_self": spec["targeted"]["self"],
        "msg_targeted_target": spec["targeted"]["target"],
        "msg_targeted_other": spec["targeted"]["others"],
    })
    return payload


def trigger_to_manifest(trigger: Trigger) -> dict[str, Any]:
    # Keep the single-trigger editor on the same portable contract as a full
    # world export. The import is intentionally lazy to avoid a module cycle.
    from builders import world_export as builder_world_export

    manifest = builder_world_export._serialize_trigger_manifest(
        trigger,
        world=trigger.world,
        entity_ref_cache=None,
        room_ref_cache=builder_world_export._build_room_ref_cache(
            trigger.world
        ),
    )
    manifest = {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
        **manifest,
    }
    manifest["metadata"] = {
        "world": _entity_key(_WORLD_KEY_PREFIX, trigger.world_id),
        "id": trigger.id,
        "key": trigger.key,
        **manifest["metadata"],
    }
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


def _normalize_trigger_item_definition_ref(
    *,
    world: World,
    value: Any,
    field_name: str,
) -> str:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must reference an itemdefinition in this world."
        )
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")

    item_definition = None
    definition_world_id = world.instance_of_id or world.id
    if isinstance(value, int):
        item_definition = ItemDefinition.objects.filter(
            world_id=definition_world_id,
            pk=value,
        ).first()
    else:
        prefix, separator, raw_value = text.partition(".")
        if separator:
            if prefix.strip().lower() not in {
                "itemdefinition",
                "item_definition",
            }:
                raise serializers.ValidationError(
                    f"{field_name} must reference an itemdefinition."
                )
            text = raw_value.strip()
            if not text:
                raise serializers.ValidationError(f"{field_name} is required.")
            # Typed refs are portable slug refs, including numeric-only slugs.
            item_definition = ItemDefinition.objects.filter(
                world_id=definition_world_id,
                slug=text,
            ).first()
        elif text.isdigit():
            item_definition = ItemDefinition.objects.filter(
                world_id=definition_world_id,
                pk=int(text),
            ).first()
        else:
            item_definition = ItemDefinition.objects.filter(
                world_id=definition_world_id,
                slug=text,
            ).first()

    if item_definition is None:
        raise serializers.ValidationError(
            f"{field_name} references an unknown item definition in this world."
        )
    return f"itemdefinition.{item_definition.slug}"


def _normalize_trigger_mob_definition_ref(
    *,
    world: World,
    value: Any,
    field_name: str,
) -> str:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must reference a mobdefinition in this world."
        )
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")

    mob_definition = None
    definition_world_id = world.instance_of_id or world.id
    if isinstance(value, int):
        mob_definition = MobDefinition.objects.filter(
            world_id=definition_world_id,
            pk=value,
        ).first()
    else:
        prefix, separator, raw_value = text.partition(".")
        if separator:
            if prefix.strip().lower() not in {
                "mobdefinition",
                "mob_definition",
            }:
                raise serializers.ValidationError(
                    f"{field_name} must reference a mobdefinition."
                )
            text = raw_value.strip()
            if not text:
                raise serializers.ValidationError(f"{field_name} is required.")
            mob_definition = MobDefinition.objects.filter(
                world_id=definition_world_id,
                slug=text,
            ).first()
        elif text.isdigit():
            mob_definition = MobDefinition.objects.filter(
                world_id=definition_world_id,
                pk=int(text),
            ).first()
        else:
            mob_definition = MobDefinition.objects.filter(
                world_id=definition_world_id,
                slug=text,
            ).first()

    if mob_definition is None:
        raise serializers.ValidationError(
            f"{field_name} references an unknown mob definition in this world."
        )
    return f"mobdefinition.{mob_definition.slug}"


def _normalize_trigger_currency_ref(
    *,
    world: World,
    value: Any,
    field_name: str,
) -> str:
    currency = _resolve_currency_reference(
        world=world,
        value=value,
        field_name=field_name,
    )
    if currency is None:
        raise serializers.ValidationError(f"{field_name} is required.")
    return currency.code


def _normalize_trigger_condition_refs(value: Any, *, world: World) -> Any:
    if isinstance(value, list):
        return [
            _normalize_trigger_condition_refs(item, world=world)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    mob_present = normalized.get("mob_present")
    if isinstance(mob_present, dict) and "ref" in mob_present:
        normalized["mob_present"] = {
            **mob_present,
            "ref": _normalize_trigger_mob_definition_ref(
                world=world,
                value=mob_present.get("ref"),
                field_name="spec.conditions.mob_present.ref",
            ),
        }
    elif mob_present not in (None, ""):
        normalized["mob_present"] = _normalize_trigger_mob_definition_ref(
            world=world,
            value=mob_present,
            field_name="spec.conditions.mob_present",
        )
    item_present = normalized.get("item_present")
    if isinstance(item_present, dict) and "item" in item_present:
        normalized["item_present"] = {
            **item_present,
            "item": _normalize_trigger_item_definition_ref(
                world=world,
                value=item_present.get("item"),
                field_name="spec.conditions.item_present.item",
            ),
        }
    for key, child in list(normalized.items()):
        if key in {"item_present", "mob_present"}:
            if key == "mob_present" and isinstance(child, dict) and "where" in child:
                normalized[key] = {
                    **child,
                    "where": _normalize_trigger_condition_refs(
                        child.get("where"),
                        world=world,
                    ),
                }
            continue
        normalized[key] = _normalize_trigger_condition_refs(child, world=world)
    return normalized


def _coerce_conditions_payload(raw_conditions: Any, *, world: World) -> str:
    if isinstance(raw_conditions, str):
        raw_conditions = _deserialize_conditions_payload(raw_conditions)
    if isinstance(raw_conditions, (dict, list)):
        normalized = _normalize_trigger_condition_refs(raw_conditions, world=world)
        builder_serializers.validate_conditions(None, normalized)
        return json.dumps(normalized)
    conditions = _coerce_text(raw_conditions)
    if conditions:
        builder_serializers.validate_conditions(None, conditions)
    return conditions


def serialize_trigger_manifest(trigger: Trigger) -> dict[str, Any]:
    manifest = trigger_to_manifest(trigger)
    delete_manifest = trigger_delete_manifest(trigger)
    target = trigger.target
    target_model = trigger.target_type.model_class() if trigger.target_type_id else None
    target_type = _TRIGGER_TARGET_TYPE_BY_MODEL.get(target_model, "")
    target_ref = manifest["spec"]["target"]
    return {
        "id": trigger.id,
        "key": trigger.key,
        "name": trigger.name or "",
        "scope": trigger.scope,
        "kind": _canonical_trigger_kind(trigger.kind),
        "event": trigger.event or "",
        "match": trigger.match or "",
        "target": {
            "type": target_type,
            "key": getattr(target, "key", "") if target else "",
            "ref": target_ref,
            "name": (getattr(target, "name", "") or "") if target else "",
        },
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def room_trigger_template_manifest(*, world: World, room: Room) -> dict[str, Any]:
    if room.world_id != world.id:
        raise serializers.ValidationError(
            "Trigger room target points to a room outside this world."
        )
    return {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
        "kind": TRIGGER_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
            "name": f"{room.name} Trigger",
        },
        "spec": {
            "scope": adv_consts.TRIGGER_SCOPE_ROOM,
            "kind": adv_consts.TRIGGER_KIND_COMMAND,
            "target": format_room_manifest_ref(room),
            "match": "pull lever",
            "script": (
                "/cmd room -- /echo *CLICK*.\n"
                "/cmd room -- /echo Something happens.\n"
            ),
            "steps": [],
            "on_step_error": "cancel",
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


def mob_trigger_template_manifest(*, world: World, mob_definition: MobDefinition) -> dict[str, Any]:
    definition_name = mob_definition.name or f"Mob {mob_definition.id}"
    definition_world_id = _trigger_definition_world_id(world)
    if mob_definition.world_id != definition_world_id:
        raise serializers.ValidationError(
            "Trigger mob target points outside the authored definition world."
        )
    if not mob_definition.slug:
        raise serializers.ValidationError(
            "Trigger mob target must have a portable slug."
        )
    return {
        "apiVersion": CANONICAL_MANIFEST_API_VERSION,
        "kind": TRIGGER_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key(_WORLD_KEY_PREFIX, world.id),
            "name": f"{definition_name} Reaction",
        },
        "spec": {
            "scope": adv_consts.TRIGGER_SCOPE_WORLD,
            "kind": adv_consts.TRIGGER_KIND_EVENT,
            "target": f"mobdefinition.{mob_definition.slug}",
            "event": adv_consts.MOB_REACTION_EVENT_SAYING,
            "match": "hello and (traveler or friend)",
            "script": "say Welcome, traveler.",
            "steps": [],
            "on_step_error": "cancel",
            "conditions": "",
            "show_details_on_failure": False,
            "failure_message": "",
            "display_action_in_room": False,
            "gate_delay": 10,
            "order": 0,
            "is_active": True,
        },
    }


def serialize_mob_trigger_template(*, world: World, mob_definition: MobDefinition) -> dict[str, Any]:
    manifest = mob_trigger_template_manifest(world=world, mob_definition=mob_definition)
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


def _social_validation_error(exc: SocialDefinitionError) -> serializers.ValidationError:
    return serializers.ValidationError(str(exc))


def _resolve_social_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[Social | None, int | None, str]:
    try:
        command = validate_social_command(
            metadata.get("command"),
            field_name="metadata.command",
        )
    except SocialDefinitionError as exc:
        raise _social_validation_error(exc)

    raw_id = metadata.get("id")
    raw_key = metadata.get("key")
    parsed_id = None
    parsed_key_id = None
    if raw_id is not None:
        parsed_id = _parse_entity_ref(
            raw_id,
            expected_type=_SOCIAL_KEY_PREFIX,
            field_name="metadata.id",
        )
    if raw_key not in (None, ""):
        parsed_key_id = _parse_entity_ref(
            raw_key,
            expected_type=_SOCIAL_KEY_PREFIX,
            field_name="metadata.key",
        )
    if parsed_id is not None and parsed_key_id is not None and parsed_id != parsed_key_id:
        raise serializers.ValidationError(
            "metadata.id and metadata.key refer to different socials."
        )

    referenced_id = parsed_key_id if parsed_key_id is not None else parsed_id
    referenced_social = None
    if referenced_id is not None:
        referenced_social = Social.objects.filter(
            world=world,
            pk=referenced_id,
        ).first()
        if referenced_social is None:
            raise serializers.ValidationError(
                "Social referenced by metadata.id/key was not found."
            )
        try:
            referenced_command = validate_social_command(
                referenced_social.cmd,
                field_name="metadata.command",
            )
        except SocialDefinitionError as exc:
            raise _social_validation_error(exc)
        if referenced_command != command:
            raise serializers.ValidationError(
                "metadata.id/key and metadata.command refer to different socials."
            )

    command_matches = list(
        Social.objects.filter(world=world, cmd__iexact=command).order_by("id")[:2]
    )
    if len(command_matches) > 1:
        raise serializers.ValidationError(
            "metadata.command matches multiple socials ignoring case. "
            "Resolve the duplicate commands before applying this manifest."
        )
    command_social = command_matches[0] if command_matches else None

    if (
        referenced_social is not None
        and command_social is not None
        and referenced_social.pk != command_social.pk
    ):
        raise serializers.ValidationError(
            "metadata.id/key and metadata.command refer to different socials."
        )

    social = referenced_social or command_social
    return social, social.pk if social is not None else None, command


def _coerce_social_message_group(
    *,
    raw_group: Any,
    field_name: str,
    key_to_model_field: dict[str, str],
    values: dict[str, Any],
) -> None:
    if raw_group in (None, "", {}):
        for model_field in key_to_model_field.values():
            values[model_field] = ""
        return
    if not isinstance(raw_group, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")

    unknown_fields = sorted(set(raw_group.keys()) - set(key_to_model_field.keys()))
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
        )
    for manifest_field, model_field in key_to_model_field.items():
        if manifest_field in raw_group:
            values[model_field] = _coerce_text(raw_group.get(manifest_field))


def parse_social_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedSocialManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != SOCIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{SOCIAL_MANIFEST_KIND}'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Social manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    social, social_id, command = _resolve_social_reference(
        world=world,
        metadata=metadata,
    )
    if (
        social is None
        and Social.objects.filter(world=world).count()
        >= SOCIAL_CATALOG_MAX_DEFINITIONS
    ):
        raise serializers.ValidationError(
            f"A world can define at most {SOCIAL_CATALOG_MAX_DEFINITIONS} socials."
        )

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if social is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating a social.")

    unknown_fields = sorted(
        set(spec_patch.keys()) - {"priority", "targetless", "targeted"}
    )
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    values = {
        field_name: getattr(social, field_name, "") or ""
        for field_name in SOCIAL_MESSAGE_FIELDS
    }
    if "targetless" in spec_patch:
        _coerce_social_message_group(
            raw_group=spec_patch.get("targetless"),
            field_name="spec.targetless",
            key_to_model_field={
                "self": "msg_targetless_self",
                "others": "msg_targetless_other",
            },
            values=values,
        )
    if "targeted" in spec_patch:
        _coerce_social_message_group(
            raw_group=spec_patch.get("targeted"),
            field_name="spec.targeted",
            key_to_model_field={
                "self": "msg_targeted_self",
                "target": "msg_targeted_target",
                "others": "msg_targeted_other",
            },
            values=values,
        )

    try:
        priority = normalize_social_priority(
            spec_patch.get("priority", social.priority if social else 0),
            field_name="spec.priority",
        )
        definition = validate_social_definition(values)
    except SocialDefinitionError as exc:
        raise _social_validation_error(exc)

    return ParsedSocialManifest(
        world=world,
        social=social,
        social_id=social_id,
        command=command,
        fields={
            "cmd": command,
            "priority": priority,
            **definition,
        },
    )


def parse_social_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedSocialDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != SOCIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{SOCIAL_MANIFEST_KIND}'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_DELETE:
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

    social, social_id, _command = _resolve_social_reference(
        world=world,
        metadata=metadata,
    )
    if social is None or social_id is None:
        raise serializers.ValidationError(
            "metadata.command must reference an existing social for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedSocialDeleteManifest(
        world=world,
        social=social,
        social_id=social_id,
    )


def _trigger_definition_world_id(world: World) -> int:
    """Return the authored definition scope used by an instance world."""

    return world.instance_of_id or world.id


def _infer_trigger_target_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered == "world":
        return "world"
    if parse_room_reference(text) is not None:
        return "room"
    if re.fullmatch(r"zone@\s*[+]?[0-9]+", lowered):
        return "zone"

    prefix, separator, _raw_value = lowered.partition(".")
    if separator:
        target_type = _canonical_trigger_entity_target_type(prefix)
        if target_type in _TRIGGER_TARGET_MODELS:
            return target_type
    return None


def _infer_canonical_trigger_target_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    lowered = text.lower()
    if lowered == "world":
        return "world"

    parsed_room = parse_room_reference(text)
    if (
        parsed_room is not None
        and parsed_room.kind == "relative_id"
        and parsed_room.relative_id is not None
        and parsed_room.relative_id > 0
    ):
        return "room"
    if re.fullmatch(r"zone@[1-9][0-9]*", lowered):
        return "zone"

    prefix, separator, raw_slug = lowered.partition(".")
    if (
        separator
        and prefix in {"mobdefinition", "itemdefinition"}
        and raw_slug
    ):
        return prefix
    return None


def _parse_trigger_target_database_id(
    value: Any,
    *,
    target_type: str,
    field_name: str,
) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a "
            f"'{target_type}.<id>' key."
        )
    if isinstance(value, int):
        target_id = value
    else:
        text = str(value or "").strip()
        if text.isdigit():
            target_id = int(text)
        else:
            prefix, separator, raw_id = text.partition(".")
            canonical_prefix = _canonical_trigger_entity_target_type(prefix)
            if (
                separator != "."
                or canonical_prefix != target_type
                or not raw_id.isdigit()
            ):
                raise serializers.ValidationError(
                    f"{field_name} must be an integer id or a "
                    f"'{target_type}.<id>' key."
                )
            target_id = int(raw_id)
    if target_id <= 0:
        raise serializers.ValidationError(f"{field_name} id must be positive.")
    return target_id


def _parse_trigger_zone_relative_id(value: Any, *, field_name: str) -> int:
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"zone@\s*([+]?[0-9]+)", text)
    if match is None:
        raise serializers.ValidationError(
            f"{field_name} must use the portable form 'zone@<relative_id>'."
        )
    relative_id = int(match.group(1))
    if relative_id <= 0:
        raise serializers.ValidationError(
            f"{field_name} relative id must be positive."
        )
    return relative_id


def _resolve_trigger_target_locator(
    *,
    world: World,
    target_type: str,
    value: Any,
    locator_kind: str,
    field_name: str,
) -> int:
    model_cls = _TRIGGER_TARGET_MODELS[target_type]
    is_database_locator = locator_kind in {"key", "id"}

    if target_type == "world":
        if not is_database_locator and str(value or "").strip().lower() == "world":
            target_id = world.id
        else:
            target_id = _parse_trigger_target_database_id(
                value,
                target_type=target_type,
                field_name=field_name,
            )
        if target_id != world.id:
            raise serializers.ValidationError(
                f"{field_name} must reference the selected world."
            )
        return target_id

    if target_type == "room":
        if is_database_locator:
            target_id = _parse_trigger_target_database_id(
                value,
                target_type=target_type,
                field_name=field_name,
            )
            room = Room.objects.filter(world=world, pk=target_id).first()
        else:
            if parse_room_reference(value) is None:
                raise serializers.ValidationError(
                    f"{field_name} must use 'room@<relative_id>', legacy "
                    "'room@x,y,z', or legacy 'room.<database_id>'."
                )
            room = resolve_room_reference(world, value)
        if room is None:
            raise serializers.ValidationError(
                f"{field_name} references an unknown room in this world."
            )
        return room.id

    if target_type == "zone":
        text = str(value or "").strip()
        if is_database_locator or text.lower().startswith("zone."):
            target_id = _parse_trigger_target_database_id(
                value,
                target_type=target_type,
                field_name=field_name,
            )
            zone = Zone.objects.filter(world=world, pk=target_id).first()
        elif text.lower().startswith("zone@"):
            relative_id = _parse_trigger_zone_relative_id(
                text,
                field_name=field_name,
            )
            zone = Zone.objects.filter(
                world=world,
                relative_id=relative_id,
            ).first()
        elif locator_kind == "ref":
            # Legacy trigger manifests allowed a zone name in target.ref.
            zone = Zone.objects.filter(world=world, name=text).order_by("id").first()
        else:
            zone = None
        if zone is None:
            raise serializers.ValidationError(
                f"{field_name} references an unknown zone in this world."
            )
        return zone.id

    definition_world_id = _trigger_definition_world_id(world)
    if is_database_locator:
        target_id = _parse_trigger_target_database_id(
            value,
            target_type=target_type,
            field_name=field_name,
        )
        target = model_cls.objects.filter(
            world_id=definition_world_id,
            pk=target_id,
        ).first()
    else:
        text = str(value or "").strip()
        prefix, separator, raw_slug = text.partition(".")
        if separator:
            canonical_prefix = _canonical_trigger_entity_target_type(prefix)
            if canonical_prefix != target_type:
                raise serializers.ValidationError(
                    f"{field_name} must reference a {target_type}."
                )
            slug = raw_slug.strip()
        else:
            # Bare slugs remain an import-only compatibility form for ref maps.
            slug = text
        if not slug:
            raise serializers.ValidationError(f"{field_name} is required.")
        target = model_cls.objects.filter(
            world_id=definition_world_id,
            slug=slug,
        ).first()
    if target is None:
        raise serializers.ValidationError(
            f"{field_name} references an unknown {target_type} in the "
            "authored definition world."
        )
    return target.id


def resolve_trigger_manifest_target(
    *,
    world: World,
    target_data: Any,
    default_type: str | None = None,
    allowed_types: set[str] | None = None,
) -> tuple[ContentType, int]:
    """Resolve canonical scalar and legacy mapping trigger targets."""

    canonical_default = _canonical_trigger_entity_target_type(default_type)
    if canonical_default not in _TRIGGER_TARGET_MODELS:
        canonical_default = ""
    canonical_allowed = {
        _canonical_trigger_entity_target_type(target_type)
        for target_type in (allowed_types or set(_TRIGGER_TARGET_MODELS))
    }

    declared_type = ""
    locators: list[tuple[str, str, Any]] = []
    if isinstance(target_data, str):
        if not target_data.strip():
            raise serializers.ValidationError("spec.target cannot be empty.")
        inferred_type = _infer_canonical_trigger_target_type(target_data)
        if inferred_type is None:
            raise serializers.ValidationError(
                "spec.target must be one of: room@<relative_id>, "
                "zone@<relative_id>, world, mobdefinition.<slug>, or "
                "itemdefinition.<slug>."
            )
        declared_type = inferred_type
        locators.append(("spec.target", "scalar", target_data))
    elif isinstance(target_data, dict):
        unknown_fields = sorted(
            set(target_data.keys()) - _LEGACY_TRIGGER_TARGET_FIELDS
        )
        if unknown_fields:
            raise serializers.ValidationError(
                "Unsupported spec.target field(s): "
                + ", ".join(unknown_fields)
                + "."
            )
        if "type" in target_data:
            declared_type = _canonical_trigger_entity_target_type(
                target_data.get("type")
            )
            if declared_type not in _TRIGGER_TARGET_MODELS:
                raise serializers.ValidationError(
                    f"Unsupported trigger target type '{declared_type}'."
                )
        for locator_kind in ("ref", "key", "id"):
            if locator_kind not in target_data:
                continue
            value = target_data.get(locator_kind)
            if value is None or (
                isinstance(value, str) and not value.strip()
            ):
                raise serializers.ValidationError(
                    f"spec.target.{locator_kind} cannot be empty."
                )
            locators.append(
                (f"spec.target.{locator_kind}", locator_kind, value)
            )
        if not locators and declared_type != "world":
            raise serializers.ValidationError(
                "Legacy spec.target mappings require ref, key, or id."
            )
    else:
        raise serializers.ValidationError(
            "spec.target must be a typed scalar string or a legacy mapping."
        )

    inferred_types = {
        inferred_type
        for _field_name, _locator_kind, value in locators
        if (inferred_type := _infer_trigger_target_type(value)) is not None
    }
    if len(inferred_types) > 1:
        raise serializers.ValidationError(
            "spec.target locators use conflicting target types."
        )
    inferred_type = next(iter(inferred_types), "")
    target_type = declared_type or inferred_type or canonical_default
    if not target_type:
        raise serializers.ValidationError(
            "spec.target.type is required when its locator is untyped."
        )
    if inferred_type and inferred_type != target_type:
        raise serializers.ValidationError(
            "spec.target.type conflicts with its ref, key, or id."
        )
    if target_type not in canonical_allowed:
        raise serializers.ValidationError(
            "spec.target type must be one of: "
            + ", ".join(sorted(canonical_allowed))
            + "."
        )

    if not locators:
        return ContentType.objects.get_for_model(World), world.id

    resolved_ids = {
        _resolve_trigger_target_locator(
            world=world,
            target_type=target_type,
            value=value,
            locator_kind=locator_kind,
            field_name=field_name,
        )
        for field_name, locator_kind, value in locators
    }
    if len(resolved_ids) != 1:
        raise serializers.ValidationError(
            "spec.target ref, key, and id refer to different targets."
        )
    target_id = next(iter(resolved_ids))
    return ContentType.objects.get_for_model(
        _TRIGGER_TARGET_MODELS[target_type]
    ), target_id


def _resolve_existing_trigger_target(
    *,
    world: World,
    trigger: Trigger,
    allowed_types: set[str],
) -> tuple[ContentType, int]:
    if not trigger.target_type_id or not trigger.target_id:
        raise serializers.ValidationError(
            "Existing trigger target is missing or invalid."
        )
    model_cls = trigger.target_type.model_class()
    target_type = _TRIGGER_TARGET_TYPE_BY_MODEL.get(model_cls)
    canonical_allowed = {
        _canonical_trigger_entity_target_type(value)
        for value in allowed_types
    }
    if target_type not in canonical_allowed:
        raise serializers.ValidationError(
            "Existing trigger target does not match its kind and scope."
        )
    resolved_id = _resolve_trigger_target_locator(
        world=world,
        target_type=target_type,
        value=trigger.target_id,
        locator_kind="id",
        field_name="Existing trigger target",
    )
    return trigger.target_type, resolved_id


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

    event = _coerce_text(spec.get("event", trigger.event if trigger else "")).strip().lower()
    if kind == adv_consts.TRIGGER_KIND_POLICY:
        if scope != adv_consts.TRIGGER_SCOPE_ROOM:
            raise serializers.ValidationError("Policy triggers must use scope 'room'.")
        if not event:
            raise serializers.ValidationError("spec.event is required for kind 'policy'.")
        event = _coerce_choice(
            event,
            choices=adv_consts.TRIGGER_POLICY_EVENTS,
            field_name="spec.event",
        )
    elif kind == adv_consts.TRIGGER_KIND_EVENT:
        if not event:
            raise serializers.ValidationError("spec.event is required for kind 'event'.")
        if scope == adv_consts.TRIGGER_SCOPE_ROOM:
            event_choices = adv_consts.TRIGGER_ROOM_EVENT_EVENTS
        elif scope == adv_consts.TRIGGER_SCOPE_WORLD:
            event_choices = adv_consts.MOB_REACTION_EVENTS
        else:
            raise serializers.ValidationError(
                "Event triggers must use scope 'room' or 'world'."
            )
        event = _coerce_choice(
            event,
            choices=event_choices,
            field_name="spec.event",
        )
    elif event:
        event = _coerce_choice(
            event,
            choices=adv_consts.TRIGGER_EVENTS,
            field_name="spec.event",
        )

    scope_target_type = _SCOPE_TO_TARGET_TYPE[scope]
    if (
        kind == adv_consts.TRIGGER_KIND_EVENT
        and scope == adv_consts.TRIGGER_SCOPE_WORLD
    ):
        allowed_target_types = {"mobdefinition"}
    elif kind == adv_consts.TRIGGER_KIND_COMMAND:
        allowed_target_types = {
            scope_target_type,
            "itemdefinition",
            "mobdefinition",
        }
    else:
        allowed_target_types = {scope_target_type}

    target_present = "target" in spec
    if target_present:
        target_type, target_id = resolve_trigger_manifest_target(
            world=world,
            target_data=spec.get("target"),
            default_type=scope_target_type,
            allowed_types=allowed_target_types,
        )
    elif trigger is not None:
        target_type, target_id = _resolve_existing_trigger_target(
            world=world,
            trigger=trigger,
            allowed_types=allowed_target_types,
        )
    elif (
        kind == adv_consts.TRIGGER_KIND_COMMAND
        and scope == adv_consts.TRIGGER_SCOPE_WORLD
    ):
        target_type = ContentType.objects.get_for_model(World)
        target_id = world.id
    else:
        raise serializers.ValidationError(
            "spec.target is required when creating a trigger."
        )

    name = _coerce_text(metadata.get("name", trigger.name if trigger else ""))

    conditions = _coerce_conditions_payload(
        spec.get("conditions", trigger.conditions if trigger else ""),
        world=world,
    )

    match = _coerce_text(spec.get("match", trigger.match if trigger else ""))
    if match:
        try:
            trigger_matcher.validate_match_expression(match)
        except trigger_matcher.MatchExpressionError as err:
            raise serializers.ValidationError(f"Invalid spec.match matcher expression: {err}")

    if kind == adv_consts.TRIGGER_KIND_COMMAND and not match.strip():
        raise serializers.ValidationError("spec.match is required for kind 'command'.")

    if (
        kind == adv_consts.TRIGGER_KIND_EVENT
        and event in (
            adv_consts.MOB_REACTION_EVENT_SAYING,
            adv_consts.MOB_REACTION_EVENT_RECEIVE,
            adv_consts.MOB_REACTION_EVENT_PERIODIC,
            adv_consts.MOB_REACTION_EVENT_SOCIAL,
        )
        and not match.strip()
    ):
        raise serializers.ValidationError(f"spec.match is required for event '{event}'.")

    script = _coerce_text(spec.get("script", trigger.script if trigger else ""))
    raw_steps = spec.get("steps", trigger.steps if trigger else [])
    if "steps" in spec and "script" not in spec and raw_steps:
        script = ""
    if "script" in spec and "steps" not in spec and script.strip():
        raw_steps = []
    try:
        steps = normalize_trigger_steps(
            raw_steps,
            item_ref_normalizer=lambda value, field_name: (
                _normalize_trigger_item_definition_ref(
                    world=world,
                    value=value,
                    field_name=field_name,
                )
            ),
            mob_ref_normalizer=lambda value, field_name: (
                _normalize_trigger_mob_definition_ref(
                    world=world,
                    value=value,
                    field_name=field_name,
                )
            ),
            currency_ref_normalizer=lambda value, field_name: (
                _normalize_trigger_currency_ref(
                    world=world,
                    value=value,
                    field_name=field_name,
                )
            ),
            condition_normalizer=lambda value, _field_name: (
                _normalize_trigger_condition_refs(value, world=world)
            ),
        )
        on_step_error = normalize_trigger_step_error_policy(
            spec.get(
                "on_step_error",
                trigger.on_step_error if trigger else "cancel",
            )
        )
    except TriggerStepSpecError as exc:
        raise serializers.ValidationError(str(exc))

    if script.strip() and steps:
        raise serializers.ValidationError(
            "spec.script and spec.steps are alternatives; only one may be non-empty."
        )
    if kind == adv_consts.TRIGGER_KIND_POLICY and steps:
        raise serializers.ValidationError("Policy triggers cannot define spec.steps.")

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
        script=script,
        steps=steps,
        on_step_error=on_step_error,
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
                trigger.display_action_in_room
                if trigger
                else kind == adv_consts.TRIGGER_KIND_COMMAND,
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


def _parse_item_definition_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item definition key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item definition key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item definition key."
        )
    if entity_type not in {"itemdefinition", "item_definition"}:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item definition key."
        )
    return int(raw_id)


def _resolve_item_definition_reference(
    *,
    world: World,
    metadata: dict[str, Any],
    require_existing_slug: bool = False,
) -> tuple[ItemDefinition | None, int | None]:
    definition_id = metadata.get("id")
    definition_key = metadata.get("key")
    raw_definition_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if definition_id is not None:
        parsed_id = _parse_item_definition_reference(definition_id, "metadata.id")
        resolved_by_id = ItemDefinition.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Item definition referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if definition_key not in (None, ""):
        parsed_key_id = _parse_item_definition_reference(definition_key, "metadata.key")
        resolved_by_key = ItemDefinition.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Item definition referenced by metadata.key was not found."
            )

    resolved_by_slug = None
    if raw_definition_slug:
        definition_slug = _slug_or_error(raw_definition_slug, "metadata.slug")
        resolved_by_slug = ItemDefinition.objects.filter(
            world=world,
            slug=definition_slug,
        ).first()
        if require_existing_slug and resolved_by_slug is None:
            raise serializers.ValidationError(
                f"Item definition with slug '{definition_slug}' was not found in this world."
            )

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_slug) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different item definitions."
        )

    item_definition = resolved_by_id or resolved_by_key or resolved_by_slug
    if item_definition is None:
        return None, None
    return item_definition, item_definition.id


def _parse_mob_definition_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a mob definition key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a mob definition key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a mob definition key."
        )
    if entity_type not in {"mobdefinition", "mob_definition"}:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a mob definition key."
        )
    return int(raw_id)


def _resolve_mob_definition_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[MobDefinition | None, int | None]:
    definition_id = metadata.get("id")
    definition_key = metadata.get("key")
    definition_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if definition_id is not None:
        parsed_id = _parse_mob_definition_reference(definition_id, "metadata.id")
        resolved_by_id = MobDefinition.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Mob definition referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if definition_key not in (None, ""):
        parsed_key_id = _parse_mob_definition_reference(definition_key, "metadata.key")
        resolved_by_key = MobDefinition.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Mob definition referenced by metadata.key was not found."
            )

    resolved_by_slug = None
    if definition_slug:
        resolved_by_slug = MobDefinition.objects.filter(world=world, slug=definition_slug).first()

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_slug) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different mob definitions."
        )

    mob_definition = resolved_by_id or resolved_by_key or resolved_by_slug
    if mob_definition is None:
        return None, None
    return mob_definition, mob_definition.id


def _parse_faction_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a faction key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a faction key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or entity_type != FACTION_MANIFEST_KIND or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a faction key."
        )
    return int(raw_id)


def _resolve_faction_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[Faction | None, int | None]:
    faction_id = metadata.get("id")
    faction_key = metadata.get("key")
    faction_code = str(metadata.get("code") or "").strip()

    resolved_by_id = None
    if faction_id is not None:
        parsed_id = _parse_faction_reference(faction_id, "metadata.id")
        resolved_by_id = Faction.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Faction referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if faction_key not in (None, ""):
        parsed_key_id = _parse_faction_reference(faction_key, "metadata.key")
        resolved_by_key = Faction.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Faction referenced by metadata.key was not found."
            )

    resolved_by_code = None
    if faction_code:
        resolved_by_code = Faction.objects.filter(world=world, code=faction_code).first()

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_code) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.code refer to different factions."
        )

    faction = resolved_by_id or resolved_by_key or resolved_by_code
    if faction is None:
        return None, None
    return faction, faction.id


def _resolve_faction_code_reference(
    *,
    world: World,
    value: Any,
    expected_type: str,
    field_name: str,
) -> Faction:
    if isinstance(value, int):
        faction = Faction.objects.filter(world=world, pk=value).first()
    else:
        raw_text = str(value or "").strip()
        faction = None
        if raw_text.startswith(f"{FACTION_MANIFEST_KIND}."):
            raw_ref = raw_text.split(".", 1)[1]
            if raw_ref.isdigit():
                faction = Faction.objects.filter(world=world, pk=int(raw_ref)).first()
            else:
                faction = Faction.objects.filter(
                    world=world,
                    code=normalize_faction_code(raw_ref, field_name=field_name),
                ).first()
        elif raw_text:
            faction = Faction.objects.filter(
                world=world,
                code=normalize_faction_code(raw_text, field_name=field_name),
            ).first()
    if faction is None:
        raise serializers.ValidationError(f"{field_name} references an unknown faction.")
    if expected_type == FACTION_TYPE_CORE and not faction_is_core(faction):
        raise serializers.ValidationError(f"{field_name} must reference a core faction.")
    if expected_type == FACTION_TYPE_REPUTATION and not faction_is_reputation(faction):
        raise serializers.ValidationError(f"{field_name} must reference a reputation faction.")
    return faction


def _parse_item_bundle_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item bundle key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item bundle key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item bundle key."
        )
    if entity_type not in {"itembundle", "item_bundle"}:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an item bundle key."
        )
    return int(raw_id)


def _resolve_item_bundle_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[ItemBundle | None, int | None]:
    bundle_id = metadata.get("id")
    bundle_key = metadata.get("key")
    bundle_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if bundle_id is not None:
        parsed_id = _parse_item_bundle_reference(bundle_id, "metadata.id")
        resolved_by_id = ItemBundle.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Item bundle referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if bundle_key not in (None, ""):
        parsed_key_id = _parse_item_bundle_reference(bundle_key, "metadata.key")
        resolved_by_key = ItemBundle.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Item bundle referenced by metadata.key was not found."
            )

    resolved_by_slug = None
    if bundle_slug:
        resolved_by_slug = ItemBundle.objects.filter(world=world, slug=bundle_slug).first()

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_slug) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different item bundles."
        )

    item_bundle = resolved_by_id or resolved_by_key or resolved_by_slug
    if item_bundle is None:
        return None, None
    return item_bundle, item_bundle.id


def _parse_merchant_profile_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a merchant profile key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a merchant profile key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit():
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a merchant profile key."
        )
    if entity_type not in {"merchantprofile", "merchant_profile"}:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or a merchant profile key."
        )
    return int(raw_id)


def _resolve_merchant_profile_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[MerchantProfile | None, int | None]:
    profile_id = metadata.get("id")
    profile_key = metadata.get("key")
    profile_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if profile_id is not None:
        parsed_id = _parse_merchant_profile_reference(profile_id, "metadata.id")
        resolved_by_id = MerchantProfile.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError(
                "Merchant profile referenced by metadata.id was not found."
            )

    resolved_by_key = None
    if profile_key not in (None, ""):
        parsed_key_id = _parse_merchant_profile_reference(profile_key, "metadata.key")
        resolved_by_key = MerchantProfile.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError(
                "Merchant profile referenced by metadata.key was not found."
            )

    resolved_by_slug = None
    if profile_slug:
        resolved_by_slug = MerchantProfile.objects.filter(world=world, slug=profile_slug).first()

    resolved = [item for item in (resolved_by_id, resolved_by_key, resolved_by_slug) if item]
    if len({item.pk for item in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different merchant profiles."
        )

    merchant_profile = resolved_by_id or resolved_by_key or resolved_by_slug
    if merchant_profile is None:
        return None, None
    return merchant_profile, merchant_profile.id


def _resolve_currency_reference(*, world: World, value: Any, field_name: str) -> Currency | None:
    currency_world = economy_world(world)
    if value is None:
        return None
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be a currency id, 'currency.<id>', or currency code."
        )
    if isinstance(value, int):
        currency = Currency.objects.filter(world=currency_world, pk=value).first()
        if currency:
            return currency
        raise serializers.ValidationError(f"{field_name} references an unknown currency.")

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        currency = Currency.objects.filter(world=currency_world, pk=int(text)).first()
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
            currency = Currency.objects.filter(
                world=currency_world,
                pk=int(text),
            ).first()
            if currency:
                return currency
            raise serializers.ValidationError(f"{field_name} references an unknown currency.")

    currency = Currency.objects.filter(world=currency_world, code=text).first()
    if currency:
        return currency
    raise serializers.ValidationError(f"{field_name} references an unknown currency.")


def _resolve_world_slug_reference(
    *,
    world: World,
    value: Any,
    field_name: str,
    model,
    prefixes: set[str],
    label: str,
):
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must reference {label}.")
    if isinstance(value, int):
        entity = model.objects.filter(world=world, pk=value).first()
        if entity:
            return entity
        raise serializers.ValidationError(f"{field_name} references unknown {label}.")

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        entity = model.objects.filter(world=world, pk=int(text)).first()
        if entity:
            return entity
        raise serializers.ValidationError(f"{field_name} references unknown {label}.")

    prefix, separator, raw_value = text.partition(".")
    if separator:
        if prefix not in prefixes:
            raise serializers.ValidationError(
                f"{field_name} must reference {label}."
            )
        text = raw_value.strip()
        if text.isdigit():
            entity = model.objects.filter(world=world, pk=int(text)).first()
            if entity:
                return entity
            raise serializers.ValidationError(f"{field_name} references unknown {label}.")

    slug = _slug_or_error(text, field_name)
    entity = model.objects.filter(world=world, slug=slug).first()
    if entity:
        return entity
    raise serializers.ValidationError(f"{field_name} references unknown {label}.")


def _resolve_craft_material_ref(*, world: World, value: Any, field_name: str) -> CraftMaterial:
    return _resolve_world_slug_reference(
        world=world,
        value=value,
        field_name=field_name,
        model=CraftMaterial,
        prefixes={CRAFT_MATERIAL_MANIFEST_KIND, "craft_material"},
        label="a craft material",
    )


def _coerce_crafting_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise serializers.ValidationError(f"{field_name} must be an integer.")
        return int(value)
    text = str(value or "").strip()
    if not text or not text.lstrip("+-").isdigit():
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    return int(text)


def _resolve_crafting_recipe_ref(*, world: World, value: Any, field_name: str) -> CraftingRecipe:
    return _resolve_world_slug_reference(
        world=world,
        value=value,
        field_name=field_name,
        model=CraftingRecipe,
        prefixes={CRAFTING_RECIPE_MANIFEST_KIND, "crafting_recipe"},
        label="a crafting recipe",
    )


def resolve_crafting_profile_ref(*, world: World, value: Any, field_name: str) -> CraftingProfile:
    return _resolve_world_slug_reference(
        world=world,
        value=value,
        field_name=field_name,
        model=CraftingProfile,
        prefixes={CRAFTING_PROFILE_MANIFEST_KIND, "crafting_profile"},
        label="a crafting profile",
    )


def _resolve_crafting_metadata(
    *,
    world: World,
    metadata: dict[str, Any],
    model,
    prefixes: set[str],
    label: str,
    require_existing_slug: bool = False,
):
    candidates = []
    if metadata.get("id") is not None:
        candidates.append(_resolve_world_slug_reference(
            world=world,
            value=metadata.get("id"),
            field_name="metadata.id",
            model=model,
            prefixes=prefixes,
            label=label,
        ))
    if metadata.get("key") not in (None, ""):
        candidates.append(_resolve_world_slug_reference(
            world=world,
            value=metadata.get("key"),
            field_name="metadata.key",
            model=model,
            prefixes=prefixes,
            label=label,
        ))
    raw_slug = str(metadata.get("slug") or "").strip()
    if raw_slug:
        slug = _slug_or_error(raw_slug, "metadata.slug")
        entity = model.objects.filter(
            world=world,
            slug=slug,
        ).first()
        if entity:
            candidates.append(entity)
        elif require_existing_slug:
            raise serializers.ValidationError(
                f"{label.capitalize()} with slug '{slug}' was not found in this world."
            )

    if len({candidate.pk for candidate in candidates}) > 1:
        raise serializers.ValidationError(
            f"metadata.id, metadata.key, and metadata.slug refer to different {label}s."
        )
    entity = candidates[0] if candidates else None
    return entity, entity.pk if entity else None


def _coerce_item_definition_fields(*, world: World, spec_patch: dict[str, Any], existing: ItemDefinition | None) -> dict[str, Any]:
    item_type = spec_patch.get(
        "type",
        existing.item_type if existing else adv_consts.ITEM_TYPE_INERT,
    )
    item_type = _coerce_choice(
        item_type,
        choices=adv_consts.ITEM_TYPES,
        field_name="spec.type",
    )

    base_properties = dict(existing.base_properties or {}) if existing else {}
    base_properties.pop("cost", None)
    base_properties.pop("currency", None)
    for field_name in _ITEM_DEFINITION_BASE_PROPERTY_FIELDS:
        if field_name not in spec_patch:
            continue
        value = spec_patch.get(field_name)
        if field_name in _HIT_MESSAGE_FIELDS:
            base_properties[field_name] = _coerce_text(value)
            continue
        base_properties[field_name] = value

    has_cost = "cost" in spec_patch
    has_currency = "currency" in spec_patch
    cost = existing.cost if existing else None
    currency = existing.currency if existing else None
    if has_currency and not has_cost:
        raise serializers.ValidationError(
            "spec.currency cannot be set without spec.cost.")
    if has_cost and spec_patch.get("cost") in (None, ""):
        if has_currency:
            raise serializers.ValidationError(
                "spec.currency cannot be set when spec.cost is null.")
        cost = None
        currency = None
    elif has_cost:
        from core.economy import validate_currency_amount

        try:
            cost = validate_currency_amount(
                spec_patch.get("cost"),
                field_name="spec.cost",
            )
        except ValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        if has_currency:
            currency = _resolve_currency_reference(
                world=world,
                value=spec_patch.get("currency"),
                field_name="spec.currency",
            )
        elif currency is None:
            from core.economy import default_currency

            currency = default_currency(world)

    if (
        existing is not None
        and cost is not None
        and currency is not None
        and MerchantStockSlot.objects.filter(
            Q(item_definition=existing)
            | Q(item_bundle__entries__item_definition=existing)
        ).exclude(profile__settlement_currency=currency).exists()
    ):
        raise serializers.ValidationError(
            "spec.currency must match every merchant profile that stocks this item.")

    if "armor_class" in base_properties:
        try:
            armor_class = validate_armor_class_reference(
                world=world,
                armor_class=base_properties.get("armor_class"),
                field_name="spec.armor_class",
            )
        except EquipmentSystemValidationError as exc:
            raise serializers.ValidationError(str(exc))
        if armor_class:
            base_properties["armor_class"] = armor_class
        else:
            base_properties.pop("armor_class", None)

    attributes = (
        normalize_attribute_map(
            spec_patch.get("attributes"),
            field_name="spec.attributes",
        )
        if "attributes" in spec_patch
        else dict(existing.attributes or {}) if existing else {}
    )
    randomization = (
        normalize_item_randomization(spec_patch.get("randomization"))
        if "randomization" in spec_patch
        else dict(existing.randomization or {}) if existing else {}
    )

    return {
        "description": _coerce_text(
            spec_patch.get(
                "description",
                existing.description if existing else "",
            )
        ),
        "room_description": _coerce_text(
            spec_patch.get(
                "room_description",
                existing.room_description if existing else "",
            )
        ),
        "notes": _coerce_text(
            spec_patch.get(
                "notes",
                existing.notes if existing else "",
            )
        ),
        "keywords": _coerce_text(
            spec_patch.get(
                "keywords",
                existing.keywords if existing else "",
            )
        ),
        "item_type": item_type,
        "base_properties": base_properties,
        "cost": cost,
        "currency": currency,
        "attributes": attributes,
        "randomization": randomization,
    }


def _resolve_ability_slug_reference(*, world: World, value: Any, field_name: str) -> str:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an ability slug, id, or ability.<id> key."
        )

    text = str(value or "").strip()
    if isinstance(value, int) or text.isdigit() or text.startswith("ability."):
        ability_id = _parse_entity_ref(
            value,
            expected_type="ability",
            field_name=field_name,
        )
        ability = AbilityDefinition.objects.filter(world=world, pk=ability_id).first()
        if not ability:
            raise serializers.ValidationError(f"Ability referenced by {field_name} was not found.")
        return ability.slug

    slug = _slug_or_error(text, field_name)
    ability = AbilityDefinition.objects.filter(world=world, slug=slug).first()
    if not ability:
        raise serializers.ValidationError(f"Ability referenced by {field_name} was not found.")
    return ability.slug


def _coerce_trainer_ability_slug(*, world: World, entry: Any, index: int) -> str:
    field_name = f"spec.trainer.abilities[{index}]"
    if isinstance(entry, dict):
        unknown_fields = sorted(set(entry.keys()) - {"ability"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
            )
        if "ability" not in entry:
            raise serializers.ValidationError(f"{field_name}.ability is required.")
        value = entry.get("ability")
    else:
        value = entry
    return _resolve_ability_slug_reference(
        world=world,
        value=value,
        field_name=field_name if not isinstance(entry, dict) else f"{field_name}.ability",
    )


def _normalize_existing_trainer(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    abilities = []
    for raw_slug in value.get("abilities") or []:
        slug = str(raw_slug or "").strip().lower()
        if slug and slug not in abilities:
            abilities.append(slug)
    if not abilities:
        return {}
    availability = str(value.get("availability") or "present").strip().lower()
    if availability not in {"present", "alive_and_present"}:
        availability = "present"
    return {
        "abilities": abilities,
        "availability": availability,
    }


def _coerce_trainer_config(
    *,
    world: World,
    spec_patch: dict[str, Any],
    existing: MobDefinition | None,
) -> dict[str, Any]:
    existing_trainer = _normalize_existing_trainer(
        existing.trainer if existing else {}
    )
    if "trainer" not in spec_patch:
        return existing_trainer

    raw_trainer = spec_patch.get("trainer")
    if raw_trainer in (None, ""):
        return {}
    if not isinstance(raw_trainer, dict):
        raise serializers.ValidationError("spec.trainer must be a mapping.")

    unknown_fields = sorted(set(raw_trainer.keys()) - {"abilities", "availability"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec.trainer field(s): {', '.join(unknown_fields)}."
        )

    if "abilities" in raw_trainer:
        raw_abilities = raw_trainer.get("abilities")
        if raw_abilities in (None, ""):
            raw_abilities = []
        elif isinstance(raw_abilities, (str, int)):
            raw_abilities = [raw_abilities]
        elif not isinstance(raw_abilities, list):
            raise serializers.ValidationError("spec.trainer.abilities must be a list.")

        abilities = []
        for index, entry in enumerate(raw_abilities):
            slug = _coerce_trainer_ability_slug(
                world=world,
                entry=entry,
                index=index,
            )
            if slug not in abilities:
                abilities.append(slug)
    else:
        abilities = list(existing_trainer.get("abilities") or [])

    availability = str(
        raw_trainer.get(
            "availability",
            existing_trainer.get("availability", "present"),
        )
    ).strip().lower() or "present"
    if availability not in {"present", "alive_and_present"}:
        raise serializers.ValidationError(
            "spec.trainer.availability must be one of: present, alive_and_present."
        )

    if not abilities:
        return {}
    return {
        "abilities": abilities,
        "availability": availability,
    }


def _coerce_mob_combat_ability_entry(
    *,
    world: World,
    entry: Any,
    index: int,
) -> dict[str, Any]:
    field_name = f"spec.combat.abilities[{index}]"
    if isinstance(entry, dict):
        unknown_fields = sorted(
            set(entry.keys())
            - {"ability", "slug", "weight", "chance", "when", "conditions"}
        )
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
            )
        ability_ref = entry.get("ability", entry.get("slug"))
        ability_slug = _resolve_ability_slug_reference(
            world=world,
            value=ability_ref,
            field_name=f"{field_name}.ability",
        )
        weight = _coerce_int(entry.get("weight", 1), f"{field_name}.weight")
        if weight <= 0:
            raise serializers.ValidationError(f"{field_name}.weight must be positive.")
        chance = _coerce_int(entry.get("chance", 100), f"{field_name}.chance")
        if chance < 0 or chance > 100:
            raise serializers.ValidationError(f"{field_name}.chance must be 0-100.")
        conditions = entry.get("when", entry.get("conditions", {}))
    else:
        ability_slug = _resolve_ability_slug_reference(
            world=world,
            value=entry,
            field_name=field_name,
        )
        weight = 1
        chance = 100
        conditions = {}

    if conditions in (None, "", []):
        conditions = {}
    try:
        validate_condition_payload(conditions, field_name=f"{field_name}.when")
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))

    normalized = {
        "ability": ability_slug,
        "weight": weight,
    }
    if isinstance(entry, dict) and "chance" in entry:
        normalized["chance"] = chance
    if conditions:
        normalized["when"] = conditions
    return normalized


def _coerce_mob_combat_abilities(
    *,
    world: World,
    combat: dict[str, Any],
    existing: MobDefinition | None,
) -> list[dict[str, Any]]:
    if "abilities" not in combat:
        return list(existing.combat_abilities or []) if existing else []

    raw_abilities = combat.get("abilities")
    if raw_abilities in (None, ""):
        return []
    if isinstance(raw_abilities, (str, int)):
        raw_abilities = [raw_abilities]
    if not isinstance(raw_abilities, list):
        raise serializers.ValidationError("spec.combat.abilities must be a list.")

    return [
        _coerce_mob_combat_ability_entry(
            world=world,
            entry=entry,
            index=index,
        )
        for index, entry in enumerate(raw_abilities)
    ]


def _coerce_mob_definition_fields(*, world: World, spec_patch: dict[str, Any], existing: MobDefinition | None) -> dict[str, Any]:
    mob_type = spec_patch.get(
        "type",
        existing.mob_type if existing else adv_consts.MOB_TYPE_BEAST,
    )
    mob_type = _coerce_choice(
        mob_type,
        choices=adv_consts.MOB_TYPES,
        field_name="spec.type",
    )

    base_properties = dict(existing.base_properties or {}) if existing else {}
    for field_name in _MOB_DEFINITION_BASE_PROPERTY_FIELDS:
        if field_name not in spec_patch:
            continue
        value = spec_patch.get(field_name)
        if field_name == "aggression":
            value = _coerce_mob_aggression(value, "spec.aggression")
        elif field_name == "target_priority":
            value = _coerce_int(value, "spec.target_priority")
        elif field_name in _HIT_MESSAGE_FIELDS:
            value = _coerce_text(value)
        base_properties[field_name] = value

    combat = spec_patch.get("combat", {})
    if combat in (None, ""):
        combat = {}
    if not isinstance(combat, dict):
        raise serializers.ValidationError("spec.combat must be a mapping.")
    combat_unknown = sorted(set(combat.keys()) - {"attackable", "health", "abilities", *_MOB_DEFINITION_BASE_PROPERTY_FIELDS})
    if combat_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.combat field(s): {', '.join(combat_unknown)}."
        )
    for field_name, value in combat.items():
        if field_name in {"attackable", "abilities"}:
            continue
        if field_name == "health":
            base_properties["health_max"] = value
        elif field_name in _MOB_DEFINITION_BASE_PROPERTY_FIELDS:
            if field_name == "aggression":
                value = _coerce_mob_aggression(value, "spec.combat.aggression")
            elif field_name == "target_priority":
                value = _coerce_int(value, "spec.combat.target_priority")
            elif field_name in _HIT_MESSAGE_FIELDS:
                value = _coerce_text(value)
            base_properties[field_name] = value

    merchant = spec_patch.get("merchant", {})
    if merchant in (None, ""):
        merchant = {}
    if not isinstance(merchant, dict):
        raise serializers.ValidationError("spec.merchant must be a mapping.")
    merchant_unknown = sorted(set(merchant.keys()) - {"profile", "availability"})
    if merchant_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.merchant field(s): {', '.join(merchant_unknown)}."
        )
    merchant_profile = existing.merchant_profile if existing else None
    if "profile" in merchant:
        merchant_profile = _resolve_profile_ref(
            world=world,
            value=merchant.get("profile"),
            field_name="spec.merchant.profile",
        )
    merchant_availability = str(
        merchant.get("availability", existing.merchant_availability if existing else "present")
    ).strip().lower() or "present"
    if merchant_availability not in {"present", "alive_and_present"}:
        raise serializers.ValidationError(
            "spec.merchant.availability must be one of: present, alive_and_present."
        )

    crafting_profile = existing.crafting_profile if existing else None
    crafting_availability = (
        existing.crafting_availability if existing else "present"
    )
    if "crafting" in spec_patch:
        crafting = spec_patch.get("crafting")
        if crafting in (None, ""):
            crafting = {}
            crafting_profile = None
        if not isinstance(crafting, dict):
            raise serializers.ValidationError("spec.crafting must be a mapping.")
        crafting_unknown = sorted(set(crafting.keys()) - {"profile", "availability"})
        if crafting_unknown:
            raise serializers.ValidationError(
                f"Unsupported spec.crafting field(s): {', '.join(crafting_unknown)}."
            )
        if "profile" in crafting:
            profile_ref = crafting.get("profile")
            crafting_profile = (
                resolve_crafting_profile_ref(
                    world=world,
                    value=profile_ref,
                    field_name="spec.crafting.profile",
                )
                if profile_ref not in (None, "")
                else None
            )
        crafting_availability = str(
            crafting.get("availability", crafting_availability or "present")
        ).strip().lower() or "present"
    if crafting_availability not in {"present", "alive_and_present"}:
        raise serializers.ValidationError(
            "spec.crafting.availability must be one of: present, alive_and_present."
        )

    attackable = _coerce_bool(
        combat.get("attackable", existing.attackable if existing else True),
        "spec.combat.attackable",
    )

    attributes = (
        normalize_attribute_map(
            spec_patch.get("attributes"),
            field_name="spec.attributes",
        )
        if "attributes" in spec_patch
        else dict(existing.attributes or {}) if existing else {}
    )
    randomization = (
        normalize_item_randomization(spec_patch.get("randomization"))
        if "randomization" in spec_patch
        else dict(existing.randomization or {}) if existing else {}
    )
    try:
        initial_state = (
            normalize_state_snapshot(
                spec_patch.get("initial_state"),
                field_name="spec.initial_state",
            )
            if "initial_state" in spec_patch
            else dict(existing.initial_state or {}) if existing else {}
        )
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))
    trainer = _coerce_trainer_config(
        world=world,
        spec_patch=spec_patch,
        existing=existing,
    )
    combat_abilities = _coerce_mob_combat_abilities(
        world=world,
        combat=combat,
        existing=existing,
    )
    try:
        traits = (
            normalize_trait_list(spec_patch.get("traits"), field_name="spec.traits")
            if "traits" in spec_patch
            else list(existing.traits or []) if existing else []
        )
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))

    loot = (
        normalize_loot_table(
            spec_patch.get("loot"),
            world=world,
            field_name="spec.loot",
        )
        if "loot" in spec_patch
        else dict(existing.loot or {}) if existing else {}
    )

    return {
        "description": _coerce_text(
            spec_patch.get(
                "description",
                existing.description if existing else "",
            )
        ),
        "room_description": _coerce_text(
            spec_patch.get(
                "room_description",
                existing.room_description if existing else "",
            )
        ),
        "notes": _coerce_text(
            spec_patch.get(
                "notes",
                existing.notes if existing else "",
            )
        ),
        "keywords": _coerce_text(
            spec_patch.get(
                "keywords",
                existing.keywords if existing else "",
            )
        ),
        "mob_type": mob_type,
        "assists": _coerce_bool(
            spec_patch.get("assists", existing.assists if existing else False),
            "spec.assists",
        ),
        "base_properties": base_properties,
        "attributes": attributes,
        "randomization": randomization,
        "initial_state": initial_state,
        "traits": traits,
        "loot": loot,
        "combat_abilities": combat_abilities,
        "attackable": attackable,
        "merchant_profile": merchant_profile,
        "merchant_availability": merchant_availability,
        "crafting_profile": crafting_profile,
        "crafting_availability": crafting_availability,
        "trainer": trainer,
    }


def _coerce_faction_room(
    *,
    world: World,
    value: Any,
    field_name: str,
) -> Room | None:
    if value in (None, ""):
        return None
    room_id = _parse_entity_ref(value, expected_type="room", field_name=field_name)
    room = Room.objects.filter(world=world, pk=room_id).first()
    if room is None:
        raise serializers.ValidationError(
            f"Room referenced by {field_name} was not found in this world."
        )
    return room


def _coerce_default_languages(value: Any, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise serializers.ValidationError(f"{field_name} must be a list.")
    languages: list[str] = []
    for index, raw_language in enumerate(value):
        language = str(raw_language or "").strip().lower()
        if not language:
            raise serializers.ValidationError(f"{field_name}[{index}] cannot be empty.")
        if language not in languages:
            languages.append(language)
    return languages


def _coerce_faction_ranks(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise serializers.ValidationError(f"{field_name} must be a list.")
    ranks: list[dict[str, Any]] = []
    seen_standings: set[int] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"{field_name}[{index}] must be a mapping.")
        unknown_fields = sorted(set(entry.keys()) - {"standing", "name"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_name}[{index}] field(s): {', '.join(unknown_fields)}."
            )
        standing = _coerce_int(entry.get("standing"), f"{field_name}[{index}].standing")
        if standing in seen_standings:
            raise serializers.ValidationError(
                f"{field_name}[{index}].standing duplicates another rank."
            )
        seen_standings.add(standing)
        name = _coerce_text(entry.get("name"))
        if not name.strip():
            raise serializers.ValidationError(f"{field_name}[{index}].name cannot be empty.")
        ranks.append({"standing": standing, "name": name})
    return ranks


def _coerce_faction_fields(
    *,
    world: World,
    spec_patch: dict[str, Any],
    existing: Faction | None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    faction_type = _coerce_choice(
        spec_patch.get("type", existing.type if existing else FACTION_TYPE_REPUTATION),
        choices=list(FACTION_TYPES),
        field_name="spec.type",
    )
    fields: dict[str, Any] = {
        "type": faction_type,
        "is_core": faction_type == FACTION_TYPE_CORE,
        "description": _coerce_text(
            spec_patch.get("description", existing.description if existing else "")
        ),
        "notes": _coerce_text(
            spec_patch.get("notes", existing.notes if existing else "")
        ),
    }
    if faction_type == FACTION_TYPE_CORE:
        playable = _coerce_bool(
            spec_patch.get("playable", existing.playable if existing else False),
            "spec.playable",
        )
        fields["playable"] = playable
        fields["is_selectable"] = playable
        fields["starting_room"] = (
            _coerce_faction_room(
                world=world,
                value=spec_patch.get("starting_room"),
                field_name="spec.starting_room",
            )
            if "starting_room" in spec_patch
            else existing.starting_room if existing else None
        )
        fields["death_room"] = (
            _coerce_faction_room(
                world=world,
                value=spec_patch.get("death_room"),
                field_name="spec.death_room",
            )
            if "death_room" in spec_patch
            else existing.death_room if existing else None
        )
        fields["default_languages"] = (
            _coerce_default_languages(
                spec_patch.get("default_languages"),
                "spec.default_languages",
            )
            if "default_languages" in spec_patch
            else list(existing.default_languages or []) if existing else []
        )
        ranks = None
    else:
        fields.update(
            {
                "playable": False,
                "is_selectable": False,
                "is_default": False,
                "starting_room": None,
                "death_room": None,
                "default_languages": [],
            }
        )
        ranks = (
            _coerce_faction_ranks(spec_patch.get("ranks"), "spec.ranks")
            if "ranks" in spec_patch
            else None
        )
    return fields, ranks


def _coerce_mob_definition_factions(
    *,
    world: World,
    spec_patch: dict[str, Any],
) -> dict[str, Any] | None:
    if "factions" not in spec_patch:
        return None
    raw_factions = spec_patch.get("factions")
    if raw_factions in (None, ""):
        return {"core": None, "reputation": {}}
    if not isinstance(raw_factions, dict):
        raise serializers.ValidationError("spec.factions must be a mapping.")

    unknown_fields = sorted(set(raw_factions.keys()) - {"core", "reputation"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec.factions field(s): {', '.join(unknown_fields)}."
        )

    core = None
    if "core" in raw_factions and raw_factions.get("core") not in (None, ""):
        core = _resolve_faction_code_reference(
            world=world,
            value=raw_factions.get("core"),
            expected_type=FACTION_TYPE_CORE,
            field_name="spec.factions.core",
        )

    reputation: dict[int, int] = {}
    raw_reputation = raw_factions.get("reputation") or {}
    if not isinstance(raw_reputation, dict):
        raise serializers.ValidationError("spec.factions.reputation must be a mapping.")
    for raw_code, raw_value in raw_reputation.items():
        faction = _resolve_faction_code_reference(
            world=world,
            value=raw_code,
            expected_type=FACTION_TYPE_REPUTATION,
            field_name=f"spec.factions.reputation.{raw_code}",
        )
        reputation[faction.id] = _coerce_int(
            raw_value,
            f"spec.factions.reputation.{raw_code}",
        )

    return {
        "core": core.id if core else None,
        "reputation": reputation,
    }


def parse_faction_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedFactionManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != FACTION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{FACTION_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Faction manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    faction, faction_id = _resolve_faction_reference(world=world, metadata=metadata)

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if faction is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating a faction.")

    allowed_fields = {
        "type",
        "description",
        "notes",
        "playable",
        "starting_room",
        "death_room",
        "default_languages",
        "ranks",
    }
    unknown_fields = sorted(set(spec_patch.keys()) - allowed_fields)
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    raw_code = metadata.get("code", faction.code if faction else None)
    code = normalize_faction_code(raw_code, field_name="metadata.code")
    if Faction.objects.filter(world=world, code=code).exclude(pk=faction_id).exists():
        raise serializers.ValidationError(
            "metadata.code is already used by another faction."
        )

    default_name = faction.name if faction else code.replace("_", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    fields, ranks = _coerce_faction_fields(
        world=world,
        spec_patch=spec_patch,
        existing=faction,
    )
    fields["code"] = code
    fields["name"] = name

    return ParsedFactionManifest(
        world=world,
        faction=faction,
        faction_id=faction_id,
        code=code,
        name=name,
        fields=fields,
        ranks=ranks,
    )


def parse_faction_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedFactionDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != FACTION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{FACTION_MANIFEST_KIND}'."
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

    faction, faction_id = _resolve_faction_reference(world=world, metadata=metadata)
    if faction is None or faction_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.code is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedFactionDeleteManifest(
        world=world,
        faction=faction,
        faction_id=faction_id,
    )


def _coerce_item_salvage(
    *,
    world: World,
    spec_patch: dict[str, Any],
    existing: ItemDefinition | None,
    item_fields: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]] | None]:
    existing_only = bool(existing.salvage_only) if existing else False
    if "salvage" not in spec_patch:
        if existing_only and (
            item_fields.get("item_type") == adv_consts.ITEM_TYPE_EQUIPPABLE
            or (item_fields.get("base_properties") or {}).get("equipment_type")
        ):
            raise serializers.ValidationError(
                "A salvage-only item definition cannot be equippable."
            )
        return existing_only, None

    raw_salvage = spec_patch.get("salvage")
    if raw_salvage in (None, ""):
        raw_salvage = {}
    if not isinstance(raw_salvage, dict):
        raise serializers.ValidationError("spec.salvage must be a mapping.")
    unknown_fields = sorted(set(raw_salvage.keys()) - {"only", "yields"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec.salvage field(s): {', '.join(unknown_fields)}."
        )

    salvage_only = _coerce_bool(
        raw_salvage.get("only", False),
        "spec.salvage.only",
    )
    raw_yields = raw_salvage.get("yields", [])
    if raw_yields in (None, ""):
        raw_yields = []
    if not isinstance(raw_yields, list):
        raise serializers.ValidationError("spec.salvage.yields must be a list.")

    yields = []
    seen_material_ids = set()
    for index, raw_yield in enumerate(raw_yields):
        field_prefix = f"spec.salvage.yields[{index}]"
        if not isinstance(raw_yield, dict):
            raise serializers.ValidationError(f"{field_prefix} must be a mapping.")
        unknown_yield_fields = sorted(set(raw_yield.keys()) - {"material", "quantity"})
        if unknown_yield_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_prefix} field(s): {', '.join(unknown_yield_fields)}."
            )
        material = _resolve_craft_material_ref(
            world=world,
            value=raw_yield.get("material"),
            field_name=f"{field_prefix}.material",
        )
        if material.id in seen_material_ids:
            raise serializers.ValidationError(
                f"{field_prefix}.material duplicates another salvage yield."
            )
        seen_material_ids.add(material.id)
        quantity = _coerce_crafting_int(
            raw_yield.get("quantity"),
            f"{field_prefix}.quantity",
        )
        if quantity <= 0:
            raise serializers.ValidationError(f"{field_prefix}.quantity must be positive.")
        yields.append({"material": material, "quantity": quantity})

    if salvage_only:
        if not yields:
            raise serializers.ValidationError(
                "spec.salvage.yields must not be empty when spec.salvage.only is true."
            )
        if (
            item_fields.get("item_type") == adv_consts.ITEM_TYPE_EQUIPPABLE
            or (item_fields.get("base_properties") or {}).get("equipment_type")
        ):
            raise serializers.ValidationError(
                "A salvage-only item definition cannot be equippable."
            )
    return salvage_only, yields


def parse_item_definition_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemDefinitionManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_DEFINITION_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Item definition manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    item_definition, item_definition_id = _resolve_item_definition_reference(
        world=world,
        metadata=metadata,
    )

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if item_definition is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating an item definition.")

    unknown_fields = sorted(set(spec_patch.keys()) - set(_ITEM_DEFINITION_SPEC_FIELDS))
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = item_definition.slug if item_definition else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if ItemDefinition.objects.filter(world=world, slug=slug).exclude(pk=item_definition_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another item definition."
        )

    default_name = item_definition.name if item_definition else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    try:
        fields = _coerce_item_definition_fields(
            world=world,
            spec_patch=spec_patch,
            existing=item_definition,
        )
    except ItemDefinitionError as exc:
        raise serializers.ValidationError(str(exc))
    salvage_only, salvage_yields = _coerce_item_salvage(
        world=world,
        spec_patch=spec_patch,
        existing=item_definition,
        item_fields=fields,
    )
    fields["salvage_only"] = salvage_only

    fields["slug"] = slug
    fields["name"] = name

    return ParsedItemDefinitionManifest(
        world=world,
        item_definition=item_definition,
        item_definition_id=item_definition_id,
        slug=slug,
        name=name,
        fields=fields,
        salvage_yields=salvage_yields,
    )


def parse_item_definition_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemDefinitionDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_DEFINITION_MANIFEST_KIND}'."
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

    item_definition, item_definition_id = _resolve_item_definition_reference(
        world=world,
        metadata=metadata,
        require_existing_slug=True,
    )
    if item_definition is None or item_definition_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedItemDefinitionDeleteManifest(
        world=world,
        item_definition=item_definition,
        item_definition_id=item_definition_id,
    )


def parse_mob_definition_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedMobDefinitionManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != MOB_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{MOB_DEFINITION_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Mob definition manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    mob_definition, mob_definition_id = _resolve_mob_definition_reference(
        world=world,
        metadata=metadata,
    )

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if mob_definition is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating a mob definition.")

    unknown_fields = sorted(set(spec_patch.keys()) - set(_MOB_DEFINITION_SPEC_FIELDS))
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = mob_definition.slug if mob_definition else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if MobDefinition.objects.filter(world=world, slug=slug).exclude(pk=mob_definition_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another mob definition."
        )

    default_name = mob_definition.name if mob_definition else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    try:
        fields = _coerce_mob_definition_fields(
            world=world,
            spec_patch=spec_patch,
            existing=mob_definition,
        )
        factions = _coerce_mob_definition_factions(
            world=world,
            spec_patch=spec_patch,
        )
        currency_rewards = None
        if "rewards" in spec_patch:
            raw_rewards = spec_patch.get("rewards") or {}
            if not isinstance(raw_rewards, dict):
                raise serializers.ValidationError("spec.rewards must be a mapping.")
            unknown_rewards = sorted(set(raw_rewards) - {"currencies"})
            if unknown_rewards:
                raise serializers.ValidationError(
                    f"Unsupported spec.rewards field(s): {', '.join(unknown_rewards)}.")
            raw_currencies = raw_rewards.get("currencies") or {}
            if not isinstance(raw_currencies, dict):
                raise serializers.ValidationError(
                    "spec.rewards.currencies must be a mapping.")
            currency_rewards = {}
            for reference, raw_amount in raw_currencies.items():
                currency = _resolve_currency_reference(
                    world=world,
                    value=reference,
                    field_name=f"spec.rewards.currencies.{reference}",
                )
                try:
                    amount = validate_currency_amount(
                        raw_amount,
                        allow_zero=True,
                        field_name=f"spec.rewards.currencies.{reference}",
                    )
                except ValidationError as exc:
                    raise serializers.ValidationError(exc.message_dict)
                if amount:
                    currency_rewards[currency] = amount
    except ItemDefinitionError as exc:
        raise serializers.ValidationError(str(exc))
    fields["slug"] = slug
    fields["name"] = name

    return ParsedMobDefinitionManifest(
        world=world,
        mob_definition=mob_definition,
        mob_definition_id=mob_definition_id,
        slug=slug,
        name=name,
        fields=fields,
        factions=factions,
        currency_rewards=currency_rewards,
    )


def parse_mob_definition_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedMobDefinitionDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != MOB_DEFINITION_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{MOB_DEFINITION_MANIFEST_KIND}'."
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

    mob_definition, mob_definition_id = _resolve_mob_definition_reference(
        world=world,
        metadata=metadata,
    )
    if mob_definition is None or mob_definition_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedMobDefinitionDeleteManifest(
        world=world,
        mob_definition=mob_definition,
        mob_definition_id=mob_definition_id,
    )


def _resolve_bundle_entry_definition(*, world: World, value: Any, field_name: str) -> ItemDefinition:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must reference an item definition.")
    if isinstance(value, int):
        definition = ItemDefinition.objects.filter(world=world, pk=value).first()
        if definition:
            return definition
        raise serializers.ValidationError(f"{field_name} references an unknown item definition.")

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        definition = ItemDefinition.objects.filter(world=world, pk=int(text)).first()
        if definition:
            return definition
        raise serializers.ValidationError(f"{field_name} references an unknown item definition.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itemdefinition", "item_definition"}:
            raise serializers.ValidationError(
                f"{field_name} must reference an item definition slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    definition = ItemDefinition.objects.filter(world=world, slug=slug).first()
    if definition:
        return definition
    raise serializers.ValidationError(f"{field_name} references an unknown item definition.")


def _resolve_stock_slot_bundle(*, world: World, value: Any, field_name: str) -> ItemBundle:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must reference an item bundle.")
    if isinstance(value, int):
        bundle = ItemBundle.objects.filter(world=world, pk=value).first()
        if bundle:
            return bundle
        raise serializers.ValidationError(f"{field_name} references an unknown item bundle.")

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        bundle = ItemBundle.objects.filter(world=world, pk=int(text)).first()
        if bundle:
            return bundle
        raise serializers.ValidationError(f"{field_name} references an unknown item bundle.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"itembundle", "item_bundle"}:
            raise serializers.ValidationError(
                f"{field_name} must reference an item bundle slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    bundle = ItemBundle.objects.filter(world=world, slug=slug).first()
    if bundle:
        return bundle
    raise serializers.ValidationError(f"{field_name} references an unknown item bundle.")


def _resolve_profile_ref(*, world: World, value: Any, field_name: str) -> MerchantProfile:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must reference a merchant profile.")
    if isinstance(value, int):
        profile = MerchantProfile.objects.filter(world=world, pk=value).first()
        if profile:
            return profile
        raise serializers.ValidationError(f"{field_name} references an unknown merchant profile.")

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.isdigit():
        profile = MerchantProfile.objects.filter(world=world, pk=int(text)).first()
        if profile:
            return profile
        raise serializers.ValidationError(f"{field_name} references an unknown merchant profile.")

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        if prefix not in {"merchantprofile", "merchant_profile"}:
            raise serializers.ValidationError(
                f"{field_name} must reference a merchant profile slug."
            )
        text = raw

    slug = _slug_or_error(text, field_name)
    profile = MerchantProfile.objects.filter(world=world, slug=slug).first()
    if profile:
        return profile
    raise serializers.ValidationError(f"{field_name} references an unknown merchant profile.")


def parse_item_bundle_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemBundleManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_BUNDLE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_BUNDLE_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Item bundle manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    item_bundle, item_bundle_id = _resolve_item_bundle_reference(
        world=world,
        metadata=metadata,
    )

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")

    unknown_fields = sorted(set(spec.keys()) - {"notes", "entries"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = item_bundle.slug if item_bundle else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if ItemBundle.objects.filter(world=world, slug=slug).exclude(pk=item_bundle_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another item bundle."
        )

    default_name = item_bundle.name if item_bundle else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    raw_entries = spec.get("entries", [] if item_bundle is None else None)
    entries: list[dict[str, Any]] | None = None
    if raw_entries is not None:
        entries = []
        if not isinstance(raw_entries, list):
            raise serializers.ValidationError("spec.entries must be a list.")
        for index, raw_entry in enumerate(raw_entries):
            field_prefix = f"spec.entries[{index}]"
            if not isinstance(raw_entry, dict):
                raise serializers.ValidationError(f"{field_prefix} must be a mapping.")
            definition = _resolve_bundle_entry_definition(
                world=world,
                value=raw_entry.get("item_definition"),
                field_name=f"{field_prefix}.item_definition",
            )
            min_quantity = _coerce_int(
                raw_entry.get("min_quantity", 1),
                field_name=f"{field_prefix}.min_quantity",
            )
            max_quantity = _coerce_int(
                raw_entry.get("max_quantity", min_quantity),
                field_name=f"{field_prefix}.max_quantity",
            )
            if min_quantity < 0:
                raise serializers.ValidationError(f"{field_prefix}.min_quantity cannot be negative.")
            if max_quantity < min_quantity:
                raise serializers.ValidationError(
                    f"{field_prefix}.max_quantity cannot be less than min_quantity."
                )
            probability = _coerce_int(
                raw_entry.get("probability", 100),
                field_name=f"{field_prefix}.probability",
            )
            if probability < 0 or probability > 100:
                raise serializers.ValidationError(f"{field_prefix}.probability must be 0-100.")
            weight = _coerce_int(raw_entry.get("weight", 1), field_name=f"{field_prefix}.weight")
            if weight <= 0:
                raise serializers.ValidationError(f"{field_prefix}.weight must be positive.")
            entries.append(
                {
                    "item_definition": definition,
                    "weight": weight,
                    "min_quantity": min_quantity,
                    "max_quantity": max_quantity,
                    "probability": probability,
                }
            )

    return ParsedItemBundleManifest(
        world=world,
        item_bundle=item_bundle,
        item_bundle_id=item_bundle_id,
        slug=slug,
        name=name,
        fields={
            "slug": slug,
            "name": name,
            "notes": _coerce_text(spec.get("notes", item_bundle.notes if item_bundle else "")),
        },
        entries=entries,
    )


def parse_item_bundle_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedItemBundleDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ITEM_BUNDLE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ITEM_BUNDLE_MANIFEST_KIND}'."
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

    item_bundle, item_bundle_id = _resolve_item_bundle_reference(
        world=world,
        metadata=metadata,
    )
    if item_bundle is None or item_bundle_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedItemBundleDeleteManifest(
        world=world,
        item_bundle=item_bundle,
        item_bundle_id=item_bundle_id,
    )


def _coerce_merchant_profile_fields(
    *,
    world: World,
    spec: dict[str, Any],
    existing: MerchantProfile | None,
) -> dict[str, Any]:
    unknown_fields = sorted(set(spec.keys()) - {
        "notes", "settlement_currency", "pricing", "restock", "funds",
        "buyback", "stock",
    })
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    pricing = spec.get("pricing", {})
    if pricing in (None, ""):
        pricing = {}
    if not isinstance(pricing, dict):
        raise serializers.ValidationError("spec.pricing must be a mapping.")
    pricing_unknown = sorted(set(pricing.keys()) - {"sell_markup", "buy_multiplier"})
    if pricing_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.pricing field(s): {', '.join(pricing_unknown)}."
        )

    restock = spec.get("restock", {})
    if restock in (None, ""):
        restock = {}
    if not isinstance(restock, dict):
        raise serializers.ValidationError("spec.restock must be a mapping.")
    restock_unknown = sorted(set(restock.keys()) - {"interval_seconds"})
    if restock_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.restock field(s): {', '.join(restock_unknown)}."
        )

    funds = spec.get("funds", {})
    if funds in (None, ""):
        funds = {}
    if not isinstance(funds, dict):
        raise serializers.ValidationError("spec.funds must be a mapping.")
    funds_unknown = sorted(set(funds.keys()) - {"mode", "purchase_budget"})
    if funds_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.funds field(s): {', '.join(funds_unknown)}."
        )

    buyback = spec.get("buyback", {})
    if buyback in (None, ""):
        buyback = {}
    if not isinstance(buyback, dict):
        raise serializers.ValidationError("spec.buyback must be a mapping.")
    buyback_unknown = sorted(set(buyback.keys()) - {"enabled", "max_items", "expires"})
    if buyback_unknown:
        raise serializers.ValidationError(
            f"Unsupported spec.buyback field(s): {', '.join(buyback_unknown)}."
        )

    interval = restock.get(
        "interval_seconds",
        existing.restock_interval_seconds if existing else None,
    )
    if interval in ("", None):
        interval = None
    else:
        interval = _coerce_int(interval, "spec.restock.interval_seconds")
        if interval <= 0:
            raise serializers.ValidationError("spec.restock.interval_seconds must be positive.")

    funds_mode = str(funds.get("mode", existing.funds_mode if existing else MerchantProfile.FUNDS_MODE_UNLIMITED)).strip().lower()
    if funds_mode not in MerchantProfile.FUNDS_MODES:
        raise serializers.ValidationError(
            f"spec.funds.mode must be one of: {', '.join(MerchantProfile.FUNDS_MODES)}."
        )
    settlement_currency = existing.settlement_currency if existing else None
    if "settlement_currency" in spec:
        settlement_currency = _resolve_currency_reference(
            world=world,
            value=spec.get("settlement_currency"),
            field_name="spec.settlement_currency",
        )
    elif settlement_currency is None:
        from core.economy import default_currency

        settlement_currency = default_currency(world)

    purchase_budget = _coerce_int(
        funds.get("purchase_budget", existing.purchase_budget if existing else 0),
        "spec.funds.purchase_budget",
    )
    try:
        purchase_budget = validate_currency_amount(
            purchase_budget,
            field_name="spec.funds.purchase_budget",
        )
    except ValidationError as exc:
        raise serializers.ValidationError(exc.message_dict)
    if settlement_currency is None:
        raise serializers.ValidationError("spec.settlement_currency is required.")

    buyback_max_items = _coerce_int(
        buyback.get("max_items", existing.buyback_max_items if existing else 0),
        "spec.buyback.max_items",
    )
    if buyback_max_items < 0 or buyback_max_items > 10:
        raise serializers.ValidationError("spec.buyback.max_items must be between 0 and 10.")

    buyback_enabled = _coerce_bool(
        buyback.get("enabled", existing.buyback_enabled if existing else False),
        "spec.buyback.enabled",
    )
    buyback_expires = str(
        buyback.get("expires", existing.buyback_expires if existing else MerchantProfile.BUYBACK_EXPIRES_ON_RESTOCK)
    ).strip().lower()
    if buyback_expires not in MerchantProfile.BUYBACK_EXPIRES_OPTIONS:
        raise serializers.ValidationError(
            f"spec.buyback.expires must be one of: {', '.join(MerchantProfile.BUYBACK_EXPIRES_OPTIONS)}."
        )

    sell_markup = _coerce_float(
        pricing.get("sell_markup", existing.sell_markup if existing else 1.0),
        "spec.pricing.sell_markup",
    )
    buy_multiplier = _coerce_float(
        pricing.get("buy_multiplier", existing.buy_multiplier if existing else 0.4),
        "spec.pricing.buy_multiplier",
    )
    if sell_markup < 0:
        raise serializers.ValidationError("spec.pricing.sell_markup cannot be negative.")
    if buy_multiplier < 0:
        raise serializers.ValidationError("spec.pricing.buy_multiplier cannot be negative.")

    return {
        "notes": _coerce_text(spec.get("notes", existing.notes if existing else "")),
        "sell_markup": sell_markup,
        "buy_multiplier": buy_multiplier,
        "restock_interval_seconds": interval,
        "funds_mode": funds_mode,
        "settlement_currency": settlement_currency,
        "purchase_budget": purchase_budget,
        "buyback_enabled": buyback_enabled,
        "buyback_max_items": buyback_max_items if buyback_enabled else 0,
        "buyback_expires": buyback_expires,
    }


def _coerce_merchant_stock_slots(*, world: World, raw_stock: Any) -> list[dict[str, Any]]:
    if raw_stock in (None, ""):
        return []
    if not isinstance(raw_stock, list):
        raise serializers.ValidationError("spec.stock must be a list.")

    slots: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, raw_slot in enumerate(raw_stock):
        field_prefix = f"spec.stock[{index}]"
        if not isinstance(raw_slot, dict):
            raise serializers.ValidationError(f"{field_prefix} must be a mapping.")
        unknown_fields = sorted(set(raw_slot.keys()) - {"key", "item_definition", "item_bundle", "count", "refresh"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_prefix} field(s): {', '.join(unknown_fields)}."
            )
        key = _slug_or_error(raw_slot.get("key"), f"{field_prefix}.key")
        if key in seen_keys:
            raise serializers.ValidationError(f"{field_prefix}.key is duplicated.")
        seen_keys.add(key)

        sources = [
            name
            for name in ("item_definition", "item_bundle")
            if raw_slot.get(name) not in (None, "")
        ]
        if len(sources) != 1:
            raise serializers.ValidationError(
                f"{field_prefix} must define exactly one of item_definition or item_bundle."
            )

        count = _coerce_int(raw_slot.get("count", 1), f"{field_prefix}.count")
        if count <= 0:
            raise serializers.ValidationError(f"{field_prefix}.count must be positive.")

        item_definition = None
        item_bundle = None
        if sources[0] == "item_definition":
            item_definition = _resolve_bundle_entry_definition(
                world=world,
                value=raw_slot.get("item_definition"),
                field_name=f"{field_prefix}.item_definition",
            )
            default_refresh = MerchantStockSlot.REFRESH_FILL_MISSING
        else:
            item_bundle = _resolve_stock_slot_bundle(
                world=world,
                value=raw_slot.get("item_bundle"),
                field_name=f"{field_prefix}.item_bundle",
            )
            default_refresh = MerchantStockSlot.REFRESH_REROLL_ON_RESTOCK

        refresh = str(raw_slot.get("refresh", default_refresh)).strip().lower()
        if refresh not in MerchantStockSlot.REFRESH_MODES:
            raise serializers.ValidationError(
                f"{field_prefix}.refresh must be one of: {', '.join(MerchantStockSlot.REFRESH_MODES)}."
            )

        slots.append(
            {
                "key": key,
                "item_definition": item_definition,
                "item_bundle": item_bundle,
                "count": count,
                "refresh": refresh,
            }
        )
    return slots


def _validate_merchant_stock_currency(*, settlement_currency, stock_slots) -> None:
    """Ensure all priced definitions in a merchant catalog use its denomination."""
    bundle_ids: set[int] = set()
    normalized_slots = []
    for index, slot in enumerate(stock_slots):
        if isinstance(slot, dict):
            item_definition = slot.get("item_definition")
            item_bundle = slot.get("item_bundle")
            key = slot.get("key") or str(index)
        else:
            item_definition = slot.item_definition
            item_bundle = slot.item_bundle
            key = slot.key or str(index)
        if item_bundle is not None:
            bundle_ids.add(item_bundle.pk)
        normalized_slots.append((key, item_definition, item_bundle))

    bundle_definitions: dict[int, list[ItemDefinition]] = {}
    if bundle_ids:
        for entry in ItemBundleEntry.objects.filter(
            bundle_id__in=bundle_ids,
        ).select_related("item_definition"):
            bundle_definitions.setdefault(entry.bundle_id, []).append(
                entry.item_definition)

    for key, item_definition, item_bundle in normalized_slots:
        definitions = (
            [item_definition]
            if item_definition is not None
            else bundle_definitions.get(item_bundle.pk, [])
        )
        for definition in definitions:
            if (
                definition.cost is not None
                and definition.currency_id != settlement_currency.pk
            ):
                raise serializers.ValidationError(
                    f"Merchant stock slot '{key}' includes item definition "
                    f"'{definition.slug}' priced in a different currency.")


def parse_merchant_profile_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedMerchantProfileManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != MERCHANT_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{MERCHANT_PROFILE_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Merchant profile manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    merchant_profile, merchant_profile_id = _resolve_merchant_profile_reference(
        world=world,
        metadata=metadata,
    )

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if merchant_profile is None and not spec:
        raise serializers.ValidationError("spec is required when creating a merchant profile.")

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = merchant_profile.slug if merchant_profile else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if MerchantProfile.objects.filter(world=world, slug=slug).exclude(pk=merchant_profile_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another merchant profile."
        )

    default_name = merchant_profile.name if merchant_profile else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    fields = _coerce_merchant_profile_fields(
        world=world,
        spec=spec,
        existing=merchant_profile,
    )
    fields["slug"] = slug
    fields["name"] = name
    stock_slots = (
        _coerce_merchant_stock_slots(world=world, raw_stock=spec.get("stock"))
        if "stock" in spec or merchant_profile is None
        else None
    )
    effective_stock_slots = (
        stock_slots
        if stock_slots is not None
        else merchant_profile.stock_slots.select_related(
            "item_definition",
            "item_bundle",
        ).all()
    )
    _validate_merchant_stock_currency(
        settlement_currency=fields["settlement_currency"],
        stock_slots=effective_stock_slots,
    )

    return ParsedMerchantProfileManifest(
        world=world,
        merchant_profile=merchant_profile,
        merchant_profile_id=merchant_profile_id,
        slug=slug,
        name=name,
        fields=fields,
        stock_slots=stock_slots,
    )


def parse_merchant_profile_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedMerchantProfileDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != MERCHANT_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{MERCHANT_PROFILE_MANIFEST_KIND}'."
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

    merchant_profile, merchant_profile_id = _resolve_merchant_profile_reference(
        world=world,
        metadata=metadata,
    )
    if merchant_profile is None or merchant_profile_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedMerchantProfileDeleteManifest(
        world=world,
        merchant_profile=merchant_profile,
        merchant_profile_id=merchant_profile_id,
    )


def _crafting_manifest_metadata(
    *,
    world: World,
    manifest: dict[str, Any],
) -> dict[str, Any]:
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
    return metadata


def parse_craft_material_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftMaterialManifest:
    if parse_manifest_kind(manifest) != CRAFT_MATERIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftmaterial'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            "Craft material manifests only support operation 'apply' in this parser."
        )
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    material, material_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftMaterial,
        prefixes={CRAFT_MATERIAL_MANIFEST_KIND, "craft_material"},
        label="craft material",
    )
    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    unknown_fields = sorted(set(spec.keys()) - {"description", "order"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )
    if material is None and not spec:
        raise serializers.ValidationError("spec is required when creating a craft material.")

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = material.slug if material else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if CraftMaterial.objects.filter(world=world, slug=slug).exclude(pk=material_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another craft material."
        )
    default_name = material.name if material else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")
    order = _coerce_crafting_int(
        spec.get("order", material.order if material else 0),
        "spec.order",
    )
    return ParsedCraftMaterialManifest(
        world=world,
        material=material,
        material_id=material_id,
        slug=slug,
        name=name,
        fields={
            "slug": slug,
            "name": name,
            "description": _coerce_text(
                spec.get("description", material.description if material else "")
            ),
            "order": order,
        },
    )


def parse_craft_material_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftMaterialDeleteManifest:
    if parse_manifest_kind(manifest) != CRAFT_MATERIAL_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftmaterial'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    material, material_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftMaterial,
        prefixes={CRAFT_MATERIAL_MANIFEST_KIND, "craft_material"},
        label="craft material",
    )
    if material is None or material_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )
    if manifest.get("spec") not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")
    return ParsedCraftMaterialDeleteManifest(
        world=world,
        material=material,
        material_id=material_id,
    )


def _coerce_recipe_inputs(
    *,
    world: World,
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise serializers.ValidationError("spec.inputs must be a list.")
    if not value:
        raise serializers.ValidationError("spec.inputs must not be empty.")
    ingredients = []
    seen_material_ids = set()
    for index, raw_input in enumerate(value):
        field_prefix = f"spec.inputs[{index}]"
        if not isinstance(raw_input, dict):
            raise serializers.ValidationError(f"{field_prefix} must be a mapping.")
        unknown_fields = sorted(set(raw_input.keys()) - {"material", "quantity"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_prefix} field(s): {', '.join(unknown_fields)}."
            )
        material = _resolve_craft_material_ref(
            world=world,
            value=raw_input.get("material"),
            field_name=f"{field_prefix}.material",
        )
        if material.id in seen_material_ids:
            raise serializers.ValidationError(
                f"{field_prefix}.material duplicates another recipe input."
            )
        seen_material_ids.add(material.id)
        quantity = _coerce_crafting_int(
            raw_input.get("quantity"),
            f"{field_prefix}.quantity",
        )
        if quantity <= 0:
            raise serializers.ValidationError(f"{field_prefix}.quantity must be positive.")
        ingredients.append({"material": material, "quantity": quantity})
    return ingredients


def parse_crafting_recipe_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftingRecipeManifest:
    if parse_manifest_kind(manifest) != CRAFTING_RECIPE_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftingrecipe'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            "Crafting recipe manifests only support operation 'apply' in this parser."
        )
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    recipe, recipe_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftingRecipe,
        prefixes={CRAFTING_RECIPE_MANIFEST_KIND, "crafting_recipe"},
        label="crafting recipe",
    )
    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    unknown_fields = sorted(set(spec.keys()) - {
        "group", "order", "cost", "currency", "output", "inputs",
        "conditions", "failure_message",
    })
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )
    if recipe is None and not spec:
        raise serializers.ValidationError("spec is required when creating a crafting recipe.")

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = recipe.slug if recipe else ""
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if CraftingRecipe.objects.filter(world=world, slug=slug).exclude(pk=recipe_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another crafting recipe."
        )

    output_item_definition = recipe.output_item_definition if recipe else None
    if "output" in spec:
        output = spec.get("output")
        if not isinstance(output, dict):
            raise serializers.ValidationError("spec.output must be a mapping.")
        output_unknown = sorted(set(output.keys()) - {"item_definition"})
        if output_unknown:
            raise serializers.ValidationError(
                f"Unsupported spec.output field(s): {', '.join(output_unknown)}."
            )
        if output.get("item_definition") in (None, ""):
            raise serializers.ValidationError("spec.output.item_definition is required.")
        output_item_definition = _resolve_bundle_entry_definition(
            world=world,
            value=output.get("item_definition"),
            field_name="spec.output.item_definition",
        )
    if output_item_definition is None:
        raise serializers.ValidationError("spec.output.item_definition is required.")

    group_source = spec.get("group", recipe.group if recipe else "")
    group = _slug_or_error(str(group_source or ""), "spec.group")
    conditions = spec.get("conditions", recipe.conditions if recipe else {})
    if conditions in (None, "", []):
        conditions = {}
    try:
        validate_condition_payload(conditions, field_name="spec.conditions")
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))

    has_cost = "cost" in spec
    has_currency = "currency" in spec
    cost = recipe.cost if recipe else None
    currency = recipe.currency if recipe else None
    if has_currency and not has_cost:
        raise serializers.ValidationError(
            "spec.currency cannot be set without spec.cost."
        )
    if has_cost and spec.get("cost") in (None, ""):
        if has_currency:
            raise serializers.ValidationError(
                "spec.currency cannot be set when spec.cost is null."
            )
        cost = None
        currency = None
    elif has_cost:
        try:
            cost = validate_currency_amount(
                spec.get("cost"),
                field_name="spec.cost",
            )
        except ValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        if has_currency:
            currency = _resolve_currency_reference(
                world=world,
                value=spec.get("currency"),
                field_name="spec.currency",
            )
            if currency is None:
                raise serializers.ValidationError("spec.currency is required.")
        elif currency is None:
            try:
                currency = default_currency(world)
            except EconomyConfigurationError as exc:
                raise serializers.ValidationError(
                    {"spec.currency": str(exc)}
                ) from exc

    ingredients = None
    if "inputs" in spec or recipe is None:
        ingredients = _coerce_recipe_inputs(
            world=world,
            value=spec.get("inputs", []),
        )
    return ParsedCraftingRecipeManifest(
        world=world,
        recipe=recipe,
        recipe_id=recipe_id,
        slug=slug,
        fields={
            "slug": slug,
            "output_item_definition": output_item_definition,
            "group": group,
            "order": _coerce_crafting_int(
                spec.get("order", recipe.order if recipe else 0),
                "spec.order",
            ),
            "conditions": conditions,
            "cost": cost,
            "currency": currency,
            "failure_message": _coerce_text(
                spec.get("failure_message", recipe.failure_message if recipe else "")
            ),
        },
        ingredients=ingredients,
    )


def parse_crafting_recipe_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftingRecipeDeleteManifest:
    if parse_manifest_kind(manifest) != CRAFTING_RECIPE_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftingrecipe'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    recipe, recipe_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftingRecipe,
        prefixes={CRAFTING_RECIPE_MANIFEST_KIND, "crafting_recipe"},
        label="crafting recipe",
        require_existing_slug=True,
    )
    if recipe is None or recipe_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )
    if manifest.get("spec") not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")
    return ParsedCraftingRecipeDeleteManifest(
        world=world,
        recipe=recipe,
        recipe_id=recipe_id,
    )


def parse_crafting_profile_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftingProfileManifest:
    if parse_manifest_kind(manifest) != CRAFTING_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftingprofile'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            "Crafting profile manifests only support operation 'apply' in this parser."
        )
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    profile, profile_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftingProfile,
        prefixes={CRAFTING_PROFILE_MANIFEST_KIND, "crafting_profile"},
        label="crafting profile",
    )
    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    unknown_fields = sorted(set(spec.keys()) - {"keywords", "recipes"})
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )
    if profile is None and not spec:
        raise serializers.ValidationError("spec is required when creating a crafting profile.")

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = profile.slug if profile else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if CraftingProfile.objects.filter(world=world, slug=slug).exclude(pk=profile_id).exists():
        raise serializers.ValidationError(
            "metadata.slug is already used by another crafting profile."
        )
    default_name = profile.name if profile else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    recipes = None
    if "recipes" in spec or profile is None:
        raw_recipes = spec.get("recipes", [])
        if raw_recipes in (None, ""):
            raw_recipes = []
        if not isinstance(raw_recipes, list):
            raise serializers.ValidationError("spec.recipes must be a list.")
        recipes = []
        seen_recipe_ids = set()
        for index, raw_recipe in enumerate(raw_recipes):
            recipe = _resolve_crafting_recipe_ref(
                world=world,
                value=raw_recipe,
                field_name=f"spec.recipes[{index}]",
            )
            if recipe.id in seen_recipe_ids:
                raise serializers.ValidationError(
                    f"spec.recipes[{index}] duplicates another recipe."
                )
            seen_recipe_ids.add(recipe.id)
            recipes.append(recipe)

    return ParsedCraftingProfileManifest(
        world=world,
        profile=profile,
        profile_id=profile_id,
        slug=slug,
        name=name,
        fields={
            "slug": slug,
            "name": name,
            "keywords": _coerce_text(
                spec.get("keywords", profile.keywords if profile else "")
            ),
        },
        recipes=recipes,
    )


def parse_crafting_profile_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedCraftingProfileDeleteManifest:
    if parse_manifest_kind(manifest) != CRAFTING_PROFILE_MANIFEST_KIND:
        raise serializers.ValidationError(
            "Unsupported manifest kind. Expected 'craftingprofile'."
        )
    if parse_manifest_operation(manifest) != TRIGGER_MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")
    metadata = _crafting_manifest_metadata(world=world, manifest=manifest)
    profile, profile_id = _resolve_crafting_metadata(
        world=world,
        metadata=metadata,
        model=CraftingProfile,
        prefixes={CRAFTING_PROFILE_MANIFEST_KIND, "crafting_profile"},
        label="crafting profile",
    )
    if profile is None or profile_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )
    if manifest.get("spec") not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")
    return ParsedCraftingProfileDeleteManifest(
        world=world,
        profile=profile,
        profile_id=profile_id,
    )


def _parse_ability_reference(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an ability key."
        )
    if isinstance(value, int):
        return value

    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an ability key."
        )
    if text.isdigit():
        return int(text)

    entity_type, sep, raw_id = text.partition(".")
    if sep != "." or not raw_id.isdigit() or entity_type != "ability":
        raise serializers.ValidationError(
            f"{field_name} must be an integer id or an ability key."
        )
    return int(raw_id)


def _resolve_ability_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[AbilityDefinition | None, int | None]:
    ability_id = metadata.get("id")
    ability_key = metadata.get("key")
    ability_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if ability_id is not None:
        parsed_id = _parse_ability_reference(ability_id, "metadata.id")
        resolved_by_id = AbilityDefinition.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError("Ability referenced by metadata.id was not found.")

    resolved_by_key = None
    if ability_key not in (None, ""):
        parsed_key_id = _parse_ability_reference(ability_key, "metadata.key")
        resolved_by_key = AbilityDefinition.objects.filter(world=world, pk=parsed_key_id).first()
        if not resolved_by_key:
            raise serializers.ValidationError("Ability referenced by metadata.key was not found.")

    resolved_by_slug = None
    if ability_slug:
        resolved_by_slug = AbilityDefinition.objects.filter(world=world, slug=ability_slug).first()

    resolved = [ability for ability in (resolved_by_id, resolved_by_key, resolved_by_slug) if ability]
    if len({ability.pk for ability in resolved}) > 1:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, and metadata.slug refer to different abilities."
        )

    ability = resolved_by_id or resolved_by_key or resolved_by_slug
    if ability is None:
        return None, None
    return ability, ability.id


def parse_ability_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedAbilityManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ABILITY_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ABILITY_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Ability manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}' in this parser."
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

    ability, ability_id = _resolve_ability_reference(world=world, metadata=metadata)

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if ability is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating an ability.")

    base_spec: dict[str, Any] = {}
    if ability is not None:
        base_spec = ability_to_manifest(ability)["spec"]
    merged_spec = _deep_merge(base_spec, spec_patch)

    slug_source = metadata.get("slug")
    if slug_source is None:
        slug_source = ability.slug if ability else metadata.get("name")
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if AbilityDefinition.objects.filter(world=world, slug=slug).exclude(pk=ability_id).exists():
        raise serializers.ValidationError("metadata.slug is already used by another ability.")

    default_name = ability.name if ability else slug.replace("-", " ").title()
    name = _coerce_text(metadata.get("name", default_name))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    try:
        normalized_spec = normalize_ability_definition(
            merged_spec,
            slug=slug,
            name=name,
        )
    except AbilityValidationError as exc:
        raise serializers.ValidationError(str(exc))

    return ParsedAbilityManifest(
        world=world,
        ability=ability,
        ability_id=ability_id,
        slug=slug,
        name=name,
        normalized_spec=normalized_spec,
    )


def parse_ability_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedAbilityDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ABILITY_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ABILITY_MANIFEST_KIND}'."
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

    ability, ability_id = _resolve_ability_reference(world=world, metadata=metadata)
    if ability is None or ability_id is None:
        raise serializers.ValidationError(
            "metadata.id, metadata.key, or metadata.slug is required for operation: delete."
        )

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedAbilityDeleteManifest(
        world=world,
        ability=ability,
        ability_id=ability_id,
    )


def _ability_manifest_from_bundle_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise serializers.ValidationError("spec.abilities entries must be mappings.")
    if "metadata" in entry or "spec" in entry:
        metadata = entry.get("metadata") or {}
        spec = entry.get("spec") or {}
        if not isinstance(metadata, dict) or not isinstance(spec, dict):
            raise serializers.ValidationError("Bundled ability metadata and spec must be mappings.")
        return {
            "kind": ABILITY_MANIFEST_KIND,
            "metadata": dict(metadata),
            "spec": dict(spec),
        }

    metadata = {
        "slug": entry.get("slug"),
        "name": entry.get("name"),
    }
    spec = {
        key: value
        for key, value in entry.items()
        if key not in {"slug", "name"}
    }
    return {
        "kind": ABILITY_MANIFEST_KIND,
        "metadata": metadata,
        "spec": spec,
    }


def parse_abilities_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedAbilitiesManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != ABILITIES_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{ABILITIES_MANIFEST_KIND}'."
        )

    operation = parse_manifest_operation(manifest)
    if operation != TRIGGER_MANIFEST_OPERATION_APPLY:
        raise serializers.ValidationError(
            f"Abilities manifests only support operation '{TRIGGER_MANIFEST_OPERATION_APPLY}'."
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

    spec = manifest.get("spec") or {}
    if not isinstance(spec, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    entries = spec.get("abilities")
    if not isinstance(entries, list) or not entries:
        raise serializers.ValidationError("spec.abilities must be a non-empty list.")

    parsed = [
        parse_ability_manifest(
            world=world,
            manifest=_ability_manifest_from_bundle_entry(entry),
        )
        for entry in entries
    ]
    return ParsedAbilitiesManifest(world=world, abilities=parsed)


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
    allowed_fields.update(_WORLD_CONFIG_LEGACY_BOOL_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_INT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_FLOAT_FIELDS)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_CHOICE_FIELDS.keys())
    allowed_fields.add(_WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD)
    allowed_fields.update(_WORLD_CONFIG_CONFIG_ROOM_FIELDS)
    allowed_fields.add(_WORLD_CONFIG_STATS_FIELD)
    allowed_fields.add(_WORLD_CONFIG_COMBAT_FIELD)
    allowed_fields.add(_WORLD_CONFIG_EQUIPMENT_FIELD)
    allowed_fields.add(_WORLD_CONFIG_LEVELING_FIELD)
    allowed_fields.add(_WORLD_CONFIG_ABILITY_PROGRESS_FIELD)
    allowed_fields.add(_WORLD_CONFIG_PLAYER_CREATION_FIELD)
    allowed_fields.add(_WORLD_CONFIG_STARTING_EQUIPMENT_FIELD)
    allowed_fields.add(_WORLD_CONFIG_DEATH_ROUTING_FIELD)
    allowed_fields.add(_WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD)
    allowed_fields.add("initial_state")
    allowed_fields.update({
        "default_currency",
        "starting_balances",
        "death_currency",
        "clan_registration_currency",
    })

    unknown_fields = sorted(set(spec.keys()) - allowed_fields)
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported spec field(s): {', '.join(unknown_fields)}."
        )

    if world.instance_of_id:
        requested_fields = set(spec.keys())
        if _WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD in requested_fields:
            requested_fields.remove(_WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD)
            requested_fields.add("pvp_mode")
        inherited_fields = sorted(requested_fields & INSTANCE_INHERITED_MANIFEST_FIELDS)
        if inherited_fields:
            raise serializers.ValidationError(
                "Instance worlds inherit core systems from their base world. "
                f"Cannot alter: {', '.join(inherited_fields)}."
            )
        disallowed_fields = sorted(requested_fields - INSTANCE_LOCAL_MANIFEST_FIELDS)
        if disallowed_fields:
            raise serializers.ValidationError(
                "Instance world config manifests can only alter local instance fields. "
                f"Cannot alter: {', '.join(disallowed_fields)}."
            )
    elif _WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD in spec:
        raise serializers.ValidationError(
            "spec.death_routing_source is only valid for instance worlds."
        )

    update_death_routing = _WORLD_CONFIG_DEATH_ROUTING_FIELD in spec
    death_routing = None

    update_death_routing_source = (
        _WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD in spec
    )
    death_routing_source = None
    if update_death_routing_source:
        raw_source = spec.get(_WORLD_CONFIG_DEATH_ROUTING_SOURCE_FIELD)
        if raw_source is None:
            raise serializers.ValidationError(
                "spec.death_routing_source cannot be null."
            )
        death_routing_source = str(raw_source).strip().lower()
        if death_routing_source not in DEATH_ROUTING_SOURCES:
            raise serializers.ValidationError(
                "spec.death_routing_source must be local or base_world."
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

    initial_state = None
    if "initial_state" in spec:
        try:
            initial_state = normalize_state_snapshot(
                spec.get("initial_state"),
                field_name="spec.initial_state",
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    config_updates: dict[str, Any] = {}
    base_world = economy_world(world)
    update_default_currency = "default_currency" in spec
    selected_default_currency = None
    if update_default_currency:
        if world.pk != base_world.pk:
            raise serializers.ValidationError(
                "Instance worlds inherit the base world's default currency.")
        selected_default_currency = _resolve_currency_reference(
            world=base_world,
            value=spec.get("default_currency"),
            field_name="spec.default_currency",
        )
        if selected_default_currency is None:
            raise serializers.ValidationError("spec.default_currency is required.")

    starting_balances = None
    if "starting_balances" in spec:
        if world.pk != base_world.pk:
            raise serializers.ValidationError(
                "Instance worlds inherit starting balances from the base world.")
        raw_balances = spec.get("starting_balances")
        if not isinstance(raw_balances, dict):
            raise serializers.ValidationError("spec.starting_balances must be a mapping.")
        starting_balances = {}
        for reference, raw_amount in raw_balances.items():
            currency = _resolve_currency_reference(
                world=base_world,
                value=reference,
                field_name=f"spec.starting_balances.{reference}",
            )
            try:
                amount = validate_currency_amount(
                    raw_amount,
                    field_name=f"spec.starting_balances.{reference}",
                )
            except ValidationError as exc:
                raise serializers.ValidationError(exc.message_dict)
            starting_balances[currency] = amount

    for field_name in ("death_currency", "clan_registration_currency"):
        if field_name not in spec:
            continue
        value = spec.get(field_name)
        config_updates[field_name] = (
            None if value in (None, "") else _resolve_currency_reference(
                world=base_world,
                value=value,
                field_name=f"spec.{field_name}",
            )
        )

    for field_name in _WORLD_CONFIG_CONFIG_TEXT_FIELDS:
        if field_name in spec:
            config_updates[field_name] = _coerce_text(spec.get(field_name))

    for field_name in _WORLD_CONFIG_CONFIG_BOOL_FIELDS:
        if field_name in spec:
            config_updates[field_name] = _coerce_bool(
                spec.get(field_name),
                f"spec.{field_name}",
            )

    for field_name in _WORLD_CONFIG_LEGACY_BOOL_FIELDS:
        if field_name in spec:
            config_updates[field_name] = _coerce_bool(
                spec.get(field_name),
                f"spec.{field_name}",
            )

    for field_name in _WORLD_CONFIG_CONFIG_INT_FIELDS:
        if field_name in spec:
            value = _coerce_int(spec.get(field_name), f"spec.{field_name}")
            min_value = 1 if field_name in {"starting_level", "max_level"} else 0
            if value < min_value:
                raise serializers.ValidationError(
                    f"spec.{field_name} must be >= {min_value}."
                )
            if field_name == "default_roam_chance" and value > 100:
                raise serializers.ValidationError(
                    "spec.default_roam_chance must be <= 100."
                )
            if (
                field_name == "clan_registration_cost"
                and value > 9007199254740991
            ):
                raise serializers.ValidationError(
                    "spec.clan_registration_cost is too large."
                )
            config_updates[field_name] = value

    for field_name in _WORLD_CONFIG_CONFIG_FLOAT_FIELDS:
        if field_name in spec:
            value = _coerce_float(spec.get(field_name), f"spec.{field_name}")
            if field_name == "death_currency_penalty" and not 0 <= value <= 1:
                raise serializers.ValidationError(
                    "spec.death_currency_penalty must be between 0 and 1."
                )
            if (
                field_name != "death_currency_penalty"
                and value < 0
                and value != -1
            ):
                raise serializers.ValidationError(
                    f"spec.{field_name} must be -1 or >= 0."
                )
            config_updates[field_name] = value

    if _WORLD_CONFIG_STARTING_EQUIPMENT_FIELD in spec:
        config_updates[_WORLD_CONFIG_STARTING_EQUIPMENT_FIELD] = (
            _normalize_starting_equipment_entries(
                world=world,
                entries=spec.get(_WORLD_CONFIG_STARTING_EQUIPMENT_FIELD),
            )
        )

    for field_name, choices in _WORLD_CONFIG_CONFIG_CHOICE_FIELDS.items():
        if field_name in spec:
            config_updates[field_name] = _coerce_choice(
                spec.get(field_name),
                choices=choices,
                field_name=f"spec.{field_name}",
            )

    if _WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD in spec:
        legacy_allow_pvp = _coerce_bool(
            spec.get(_WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD),
            f"spec.{_WORLD_CONFIG_LEGACY_ALLOW_PVP_FIELD}",
        )
        legacy_pvp_mode = (
            adv_consts.PVP_MODE_FFA
            if legacy_allow_pvp
            else adv_consts.PVP_MODE_DISABLED
        )
        if "pvp_mode" in spec:
            pvp_mode_allows_pvp = (
                config_updates["pvp_mode"] != adv_consts.PVP_MODE_DISABLED
            )
            if legacy_allow_pvp != pvp_mode_allows_pvp:
                raise serializers.ValidationError(
                    "spec.allow_pvp conflicts with spec.pvp_mode. "
                    "Use pvp_mode as the canonical PvP setting."
                )
        else:
            config_updates["pvp_mode"] = legacy_pvp_mode

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

    equipment_system = get_world_equipment_system(world)
    if _WORLD_CONFIG_EQUIPMENT_FIELD in spec:
        try:
            equipment_system = normalize_equipment_system(
                spec.get(_WORLD_CONFIG_EQUIPMENT_FIELD)
            )
            config_updates["equipment_system"] = equipment_system
        except EquipmentSystemValidationError as exc:
            raise serializers.ValidationError(str(exc))

    if _WORLD_CONFIG_STATS_FIELD in spec:
        try:
            armor_class_keys = (
                get_armor_class_keys(equipment_system)
                if has_authored_armor_classes(equipment_system)
                else None
            )
            stat_system = normalize_stat_system(
                spec.get(_WORLD_CONFIG_STATS_FIELD),
                armor_class_keys=armor_class_keys,
            )
            config_updates["stat_system"] = stat_system
            config_updates["is_classless"] = not bool(stat_system.get("class_profiles"))
        except StatSystemValidationError as exc:
            raise serializers.ValidationError(str(exc))

    if _WORLD_CONFIG_COMBAT_FIELD in spec:
        try:
            config_updates["combat_system"] = normalize_combat_system(
                spec.get(_WORLD_CONFIG_COMBAT_FIELD)
            )
        except CombatFormulaValidationError as exc:
            raise serializers.ValidationError(str(exc))

    if _WORLD_CONFIG_LEVELING_FIELD in spec:
        try:
            config_updates[_WORLD_CONFIG_LEVELING_FIELD] = normalize_leveling_curve(
                spec.get(_WORLD_CONFIG_LEVELING_FIELD)
            )
        except LevelingConfigError as exc:
            raise serializers.ValidationError(str(exc))

    if _WORLD_CONFIG_ABILITY_PROGRESS_FIELD in spec:
        try:
            config_updates[_WORLD_CONFIG_ABILITY_PROGRESS_FIELD] = normalize_ability_progression(
                spec.get(_WORLD_CONFIG_ABILITY_PROGRESS_FIELD)
            )
        except AbilityValidationError as exc:
            raise serializers.ValidationError(str(exc))

    if _WORLD_CONFIG_PLAYER_CREATION_FIELD in spec:
        config_updates[_WORLD_CONFIG_PLAYER_CREATION_FIELD] = normalize_player_creation_config(
            spec.get(_WORLD_CONFIG_PLAYER_CREATION_FIELD),
            world=world,
            existing=config.player_creation or {},
        )
        core_policy = config_updates[_WORLD_CONFIG_PLAYER_CREATION_FIELD].get("core_faction") or {}
        config_updates["can_select_faction"] = core_policy.get("mode") in {
            "choose_required",
            "choose_optional",
        }

    try:
        validate_leveling_config(
            starting_level=config_updates.get(
                "starting_level",
                config.starting_level,
            ),
            max_level=config_updates.get(
                "max_level",
                config.max_level,
            ),
            leveling_curve=config_updates.get(
                _WORLD_CONFIG_LEVELING_FIELD,
                config.leveling_curve,
            ),
        )
    except LevelingConfigError as exc:
        raise serializers.ValidationError(str(exc))

    effective_death_mode = config_updates.get("death_mode", config.death_mode)
    effective_death_currency = config_updates.get(
        "death_currency", config.death_currency)
    if (
        effective_death_mode == adv_consts.DEATH_MODE_LOSE_CURRENCY
        and effective_death_currency is None
    ):
        raise serializers.ValidationError(
            "spec.death_currency is required when death_mode is lose_currency.")

    effective_clan_cost = config_updates.get(
        "clan_registration_cost", config.clan_registration_cost)
    effective_clan_currency = config_updates.get(
        "clan_registration_currency", config.clan_registration_currency)
    if effective_clan_cost and effective_clan_currency is None:
        raise serializers.ValidationError(
            "spec.clan_registration_currency is required when clan_registration_cost is nonzero.")

    prospective_stat_system = config_updates.get("stat_system")
    if update_death_routing:
        routing_policy = spec.get(_WORLD_CONFIG_DEATH_ROUTING_FIELD)
    else:
        routing_policy = None

    if prospective_stat_system is not None:
        try:
            validate_death_routing_archetype_dependencies(
                base_world=world,
                stat_system=prospective_stat_system,
                excluded_config_ids=(
                    (config.pk,) if update_death_routing else ()
                ),
            )
        except DeathRoutingValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    if update_death_routing:
        try:
            death_routing = compile_death_routing_policy(
                world=world,
                policy=routing_policy,
                field_name=f"spec.{_WORLD_CONFIG_DEATH_ROUTING_FIELD}",
                stat_system=prospective_stat_system,
            )
        except DeathRoutingValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    return ParsedWorldConfigManifest(
        world=world,
        world_updates=world_updates,
        config_updates=config_updates,
        default_currency=selected_default_currency,
        update_default_currency=update_default_currency,
        starting_balances=starting_balances,
        initial_state=initial_state,
        update_death_routing=update_death_routing,
        death_routing=death_routing,
        death_routing_policy=routing_policy,
        update_death_routing_source=update_death_routing_source,
        death_routing_source=death_routing_source,
    )


def apply_world_config_manifest(parsed: ParsedWorldConfigManifest):
    world = parsed.world
    config = world.config
    if not config:
        raise serializers.ValidationError("Selected world has no world config.")

    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(
                world=world,
                config=config,
            ),
            shared=False,
        )
        config = WorldConfig.objects.select_for_update().get(pk=config.pk)
        if "stat_system" in parsed.config_updates:
            try:
                validate_death_routing_archetype_dependencies(
                    base_world=world,
                    stat_system=parsed.config_updates["stat_system"],
                    excluded_config_ids=(
                        (config.pk,) if parsed.update_death_routing else ()
                    ),
                )
            except DeathRoutingValidationError as exc:
                raise serializers.ValidationError(str(exc)) from exc

        death_routing = parsed.death_routing
        if parsed.update_death_routing:
            try:
                death_routing = compile_death_routing_policy(
                    world=world,
                    policy=parsed.death_routing_policy,
                    field_name=f"spec.{_WORLD_CONFIG_DEATH_ROUTING_FIELD}",
                    stat_system=parsed.config_updates.get("stat_system"),
                )
            except DeathRoutingValidationError as exc:
                raise serializers.ValidationError(str(exc)) from exc

        if parsed.initial_state is not None:
            replace_initial_state_snapshot(
                STATE_SCOPE_WORLD,
                world,
                parsed.initial_state,
            )
        if parsed.update_default_currency:
            from builders.currencies import select_default_currency

            select_default_currency(
                world=world,
                currency=parsed.default_currency,
            )
        if parsed.starting_balances is not None:
            from builders.currencies import replace_starting_balances

            replace_starting_balances(
                world=world,
                balances={
                    currency.code: amount
                    for currency, amount in parsed.starting_balances.items()
                },
            )
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
        if parsed.update_death_routing_source:
            if not world.instance_of_id:
                raise serializers.ValidationError(
                    "death_routing_source is only valid for instance worlds."
                )
            if config.death_routing_source != parsed.death_routing_source:
                config_updates["death_routing_source"] = (
                    parsed.death_routing_source
                )
                config_updates["death_routing_source_generation"] = (
                    int(config.death_routing_source_generation or 0) + 1
                )
        if "is_narrative" in config_updates:
            config_updates["allow_combat"] = not bool(config_updates["is_narrative"])

        if config_updates:
            for field_name, value in config_updates.items():
                setattr(config, field_name, value)
            config.save(update_fields=list(config_updates.keys()))

        if parsed.update_death_routing:
            config = replace_compiled_policy(
                world=world,
                config=config,
                compilation=death_routing,
            )
        elif "death_room" in config_updates:
            config = rebuild_compiled_policy_snapshot(
                world=world,
                config=config,
            )

    return config


def apply_item_definition_manifest(parsed: ParsedItemDefinitionManifest) -> ItemDefinition:
    with transaction.atomic():
        if parsed.item_definition is None:
            item_definition = ItemDefinition.objects.create(
                world=parsed.world,
                **parsed.fields,
            )
        else:
            item_definition = parsed.item_definition
            for field_name, value in parsed.fields.items():
                setattr(item_definition, field_name, value)
            item_definition.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if parsed.salvage_yields is not None:
            ItemSalvageYield.objects.filter(item_definition=item_definition).delete()
            ItemSalvageYield.objects.bulk_create([
                ItemSalvageYield(item_definition=item_definition, **salvage_yield)
                for salvage_yield in parsed.salvage_yields
            ])
        return item_definition


def apply_mob_definition_manifest(parsed: ParsedMobDefinitionManifest) -> MobDefinition:
    with transaction.atomic():
        was_existing = parsed.mob_definition is not None
        sync_spawned = False
        if parsed.mob_definition is None:
            mob_definition = MobDefinition.objects.create(world=parsed.world, **parsed.fields)
        else:
            mob_definition = parsed.mob_definition
            sync_spawned = any(
                field_name != "initial_state"
                and getattr(mob_definition, field_name) != value
                for field_name, value in parsed.fields.items()
            )
            for field_name, value in parsed.fields.items():
                setattr(mob_definition, field_name, value)
            mob_definition.save(
                update_fields=[*parsed.fields.keys(), "modified_ts"],
                sync_spawned=False,
            )

        _apply_faction_assignments(
            member=mob_definition,
            factions=parsed.factions,
            source=FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
        )
        if parsed.currency_rewards is not None:
            from builders.models import MobCurrencyReward

            MobCurrencyReward.objects.filter(mob_definition=mob_definition).delete()
            MobCurrencyReward.objects.bulk_create([
                MobCurrencyReward(
                    mob_definition=mob_definition,
                    currency=currency,
                    amount=amount,
                )
                for currency, amount in parsed.currency_rewards.items()
            ])
        sync_spawned = sync_spawned or (
            parsed.factions is not None
            or parsed.currency_rewards is not None
        )
        if was_existing and sync_spawned:
            from builders.mob_definitions import sync_spawned_mobs_from_definition

            sync_spawned_mobs_from_definition(mob_definition)
        return mob_definition


def _apply_faction_assignments(
    *,
    member,
    factions: dict[str, Any] | None,
    source: str,
) -> None:
    if factions is None:
        return

    member.faction_assignments.filter(source=source).delete()

    core_faction_id = factions.get("core")
    if core_faction_id:
        has_existing_core = (
            member.faction_assignments
            .filter(Q(faction__type=FACTION_TYPE_CORE) | Q(faction__is_core=True))
            .exists()
        )
        if not has_existing_core:
            member.faction_assignments.create(
                faction_id=core_faction_id,
                value=1,
                source=source,
            )

    for faction_id, value in (factions.get("reputation") or {}).items():
        if member.faction_assignments.filter(faction_id=faction_id).exists():
            continue
        member.faction_assignments.create(
            faction_id=faction_id,
            value=int(value or 0),
            source=source,
        )


def apply_faction_manifest(parsed: ParsedFactionManifest) -> Faction:
    with transaction.atomic():
        acquire_death_routing_config_locks(
            death_routing_config_ids_for_world(
                world=parsed.world,
                config=parsed.world.config,
            ),
            shared=False,
        )
        if parsed.faction is None:
            faction = Faction.objects.create(world=parsed.world, **parsed.fields)
        else:
            faction = Faction.objects.select_for_update().get(
                pk=parsed.faction.pk
            )
            target_type = parsed.fields.get("type", faction.type)
            target_code = parsed.fields.get("code", faction.code)
            if (
                (
                    target_type != faction.type
                    or target_code != faction.code
                )
                and faction.death_routing_snapshot_references.exists()
            ):
                raise serializers.ValidationError(
                    "Cannot change the code or type of a faction used by "
                    "active death routing."
                )
            if (
                faction_is_core(faction)
                and target_type != FACTION_TYPE_CORE
            ):
                from spawns.models import Player

                if Player.objects.filter(core_faction=faction).exists():
                    raise serializers.ValidationError(
                        "Cannot change a core faction used by characters to reputation."
                    )
            for field_name, value in parsed.fields.items():
                setattr(faction, field_name, value)
            faction.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if faction_is_core(faction):
            FactionRank.objects.filter(faction=faction).delete()
        elif parsed.ranks is not None:
            FactionRank.objects.filter(faction=faction).delete()
            for rank in parsed.ranks:
                FactionRank.objects.create(faction=faction, **rank)

        return faction


def apply_item_bundle_manifest(parsed: ParsedItemBundleManifest) -> ItemBundle:
    with transaction.atomic():
        if parsed.item_bundle is None:
            item_bundle = ItemBundle.objects.create(world=parsed.world, **parsed.fields)
        else:
            item_bundle = parsed.item_bundle
            for field_name, value in parsed.fields.items():
                setattr(item_bundle, field_name, value)
            item_bundle.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if parsed.entries is not None:
            ItemBundleEntry.objects.filter(bundle=item_bundle).delete()
            for entry in parsed.entries:
                ItemBundleEntry.objects.create(bundle=item_bundle, **entry)

        return item_bundle


def apply_merchant_profile_manifest(parsed: ParsedMerchantProfileManifest) -> MerchantProfile:
    with transaction.atomic():
        generation_fields = {
            "settlement_currency",
            "sell_markup",
            "buy_multiplier",
            "restock_interval_seconds",
            "funds_mode",
            "purchase_budget",
            "buyback_enabled",
            "buyback_max_items",
            "buyback_expires",
        }
        generation_changed = bool(
            parsed.merchant_profile is not None
            and (
                parsed.stock_slots is not None
                or any(
                    field_name in generation_fields
                    and getattr(parsed.merchant_profile, field_name) != value
                    for field_name, value in parsed.fields.items()
                )
            )
        )
        if parsed.merchant_profile is None:
            merchant_profile = MerchantProfile.objects.create(world=parsed.world, **parsed.fields)
        else:
            merchant_profile = parsed.merchant_profile
            for field_name, value in parsed.fields.items():
                setattr(merchant_profile, field_name, value)
            merchant_profile.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if parsed.stock_slots is not None:
            MerchantStockSlot.objects.filter(profile=merchant_profile).delete()
            for slot in parsed.stock_slots:
                MerchantStockSlot.objects.create(profile=merchant_profile, **slot)

        if generation_changed:
            from spawns.models import MerchantRuntime

            MerchantRuntime.objects.filter(profile=merchant_profile).update(
                last_restocked_ts=None,
                next_restock_ts=None,
            )

        return merchant_profile


def apply_craft_material_manifest(parsed: ParsedCraftMaterialManifest) -> CraftMaterial:
    if parsed.material is None:
        return CraftMaterial.objects.create(world=parsed.world, **parsed.fields)
    material = parsed.material
    for field_name, value in parsed.fields.items():
        setattr(material, field_name, value)
    material.save(update_fields=[*parsed.fields.keys(), "modified_ts"])
    return material


def apply_crafting_recipe_manifest(parsed: ParsedCraftingRecipeManifest) -> CraftingRecipe:
    with transaction.atomic():
        if parsed.recipe is None:
            recipe = CraftingRecipe.objects.create(world=parsed.world, **parsed.fields)
        else:
            recipe = parsed.recipe
            for field_name, value in parsed.fields.items():
                setattr(recipe, field_name, value)
            recipe.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if parsed.ingredients is not None:
            CraftingIngredient.objects.filter(recipe=recipe).delete()
            CraftingIngredient.objects.bulk_create([
                CraftingIngredient(recipe=recipe, **ingredient)
                for ingredient in parsed.ingredients
            ])
        return recipe


def apply_crafting_profile_manifest(parsed: ParsedCraftingProfileManifest) -> CraftingProfile:
    with transaction.atomic():
        if parsed.profile is None:
            profile = CraftingProfile.objects.create(world=parsed.world, **parsed.fields)
        else:
            profile = parsed.profile
            for field_name, value in parsed.fields.items():
                setattr(profile, field_name, value)
            profile.save(update_fields=[*parsed.fields.keys(), "modified_ts"])

        if parsed.recipes is not None:
            CraftingProfileRecipe.objects.filter(profile=profile).delete()
            CraftingProfileRecipe.objects.bulk_create([
                CraftingProfileRecipe(profile=profile, recipe=recipe, order=index)
                for index, recipe in enumerate(parsed.recipes)
            ])
        return profile


def apply_ability_manifest(parsed: ParsedAbilityManifest) -> AbilityDefinition:
    spec = parsed.normalized_spec
    fields = {
        "slug": parsed.slug,
        "name": parsed.name,
        "command_verbs": spec["command"]["verbs"],
        "consumes_primary_action_on_resolve": spec[
            "consumes_primary_action_on_resolve"
        ],
        "consumes_primary_action_while_casting": spec[
            "consumes_primary_action_while_casting"
        ],
        "target": spec["target"],
        "availability": spec["availability"],
        "requirements": spec["requirements"],
        "cost": spec["cost"],
        "cast_time": spec["cast_time"],
        "cooldown": spec["cooldown"],
        "help": spec["help"],
        "components": spec["components"],
        "is_active": spec["is_active"],
    }
    if parsed.ability is None:
        return AbilityDefinition.objects.create(world=parsed.world, **fields)

    ability = parsed.ability
    for field_name, value in fields.items():
        setattr(ability, field_name, value)
    ability.save(update_fields=[*fields.keys(), "modified_ts"])
    return ability


def apply_abilities_manifest(parsed: ParsedAbilitiesManifest) -> list[AbilityDefinition]:
    with transaction.atomic():
        return [
            apply_ability_manifest(parsed_ability)
            for parsed_ability in parsed.abilities
        ]


def apply_social_manifest(parsed: ParsedSocialManifest) -> Social:
    if parsed.social is None:
        return Social.objects.create(
            world=parsed.world,
            **parsed.fields,
        )

    social = parsed.social
    for field_name, value in parsed.fields.items():
        setattr(social, field_name, value)
    social.save(update_fields=[*parsed.fields.keys(), "modified_ts"])
    return social


def delete_social_manifest(parsed: ParsedSocialDeleteManifest) -> Social:
    social = parsed.social
    social.delete()
    return social


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
            steps=parsed.steps,
            on_step_error=parsed.on_step_error,
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
    trigger.steps = parsed.steps
    trigger.on_step_error = parsed.on_step_error
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
