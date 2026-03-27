from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from django.db import transaction
from django.utils.text import slugify
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rest_framework import serializers

from quests.entity_refs import (
    canonical_template_type,
    is_dynamic_reference,
    resolve_template_ref_id,
)
from quests.models import (
    QUEST_REPEATABILITY_MODES,
    QUEST_SCOPES,
    QUEST_TEMPLATE_STATUSES,
    QUEST_TEMPLATE_TYPES,
    QuestArcTemplate,
    QuestTemplate,
)
from worlds.models import World


MANIFEST_API_VERSION = "v1alpha1"
LEGACY_MANIFEST_API_VERSION = "writtenrealms.com/v1alpha1"
QUEST_MANIFEST_KIND = "quest"
QUEST_ARC_MANIFEST_KIND = "questarc"
QUEST_ARC_MANIFEST_KIND_ALIASES = {
    QUEST_ARC_MANIFEST_KIND,
    "quest-arc",
    "quest_arc",
}
MANIFEST_OPERATION_APPLY = "apply"
MANIFEST_OPERATION_DELETE = "delete"
STEP_KINDS = {"storylet", "objective", "branch", "timer", "resolution"}
RESOLUTION_KEYS = ("complete", "compromised", "failed_forward", "expired")


class _ManifestDumper(yaml.SafeDumper):
    pass


def _string_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_ManifestDumper.add_representer(str, _string_representer)


def manifest_to_yaml(manifest: dict[str, Any]) -> str:
    return yaml.dump(
        manifest,
        Dumper=_ManifestDumper,
        sort_keys=False,
        default_flow_style=False,
    )


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


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"{field_name} must be an integer.")


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
    operation = str(manifest.get("operation") or MANIFEST_OPERATION_APPLY).strip().lower()
    if operation not in {MANIFEST_OPERATION_APPLY, MANIFEST_OPERATION_DELETE}:
        raise serializers.ValidationError(
            f"Unsupported operation '{operation}'. Allowed: apply, delete."
        )
    return operation


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
        raise serializers.ValidationError(f"{field_name} must contain at least one slug-safe character.")
    return slug


def _template_ref_error(expected_type: str, field_name: str) -> str:
    return (
        f"{field_name} must be an integer id, a '{expected_type}.<id>' key, "
        f"a '{expected_type}.<slug>' key, or a bare slug."
    )


def _validate_template_ref(world: World, value: Any, expected_type: str, field_name: str) -> None:
    expected = canonical_template_type(expected_type)
    if not expected or value in (None, ""):
        return
    if isinstance(value, bool):
        raise serializers.ValidationError(_template_ref_error(expected, field_name))
    if isinstance(value, int) or is_dynamic_reference(value):
        return

    text = str(value or "").strip()
    if not text or text.isdigit():
        return

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        canonical_prefix = canonical_template_type(prefix)
        if not canonical_prefix or canonical_prefix != expected:
            raise serializers.ValidationError(_template_ref_error(expected, field_name))
        if raw.isdigit():
            return

    resolved_id = resolve_template_ref_id(
        world=world,
        value=value,
        expected_type=expected,
    )
    if resolved_id is None:
        raise serializers.ValidationError(f"{field_name} references an unknown {expected}.")


def _condition_expected_template_type(left_path: Any, right_value: Any = None) -> str | None:
    if isinstance(right_value, str):
        prefix, sep, _ = right_value.strip().partition(".")
        if sep == ".":
            explicit_type = canonical_template_type(prefix)
            if explicit_type:
                return explicit_type

    path = str(left_path or "").strip()
    if not path.endswith(".template_id"):
        return None
    if ".item.template_id" in path:
        return "itemtemplate"
    return "mobtemplate"


def _validate_condition_template_refs(world: World, condition: Any, field_name: str) -> None:
    if condition in (None, {}, []):
        return
    if isinstance(condition, list):
        for index, item in enumerate(condition):
            _validate_condition_template_refs(world, item, f"{field_name}[{index}]")
        return
    if not isinstance(condition, dict):
        return

    if "all" in condition:
        _validate_condition_template_refs(world, condition.get("all"), f"{field_name}.all")
    if "any" in condition:
        _validate_condition_template_refs(world, condition.get("any"), f"{field_name}.any")
    if "not" in condition:
        _validate_condition_template_refs(world, condition.get("not"), f"{field_name}.not")

    for operator in ("eq", "ne", "gte", "lte", "in"):
        raw_args = condition.get(operator)
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            continue

        left_path = raw_args[0]
        right_value = raw_args[1]
        base_expected_type = _condition_expected_template_type(left_path)

        if operator == "in" and isinstance(right_value, (list, tuple, set)):
            for index, candidate in enumerate(right_value):
                expected_type = _condition_expected_template_type(left_path, candidate) or base_expected_type
                if expected_type:
                    _validate_template_ref(
                        world,
                        candidate,
                        expected_type,
                        f"{field_name}.{operator}[1][{index}]",
                    )
            continue

        expected_type = _condition_expected_template_type(left_path, right_value) or base_expected_type
        if expected_type:
            _validate_template_ref(
                world,
                right_value,
                expected_type,
                f"{field_name}.{operator}[1]",
            )


def _validate_effect_template_refs(world: World, effects: list[dict[str, Any]] | None, field_name: str) -> None:
    if not effects:
        return
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict):
            continue
        if "mob_template" in effect:
            _validate_template_ref(
                world,
                effect.get("mob_template"),
                "mobtemplate",
                f"{field_name}[{index}].mob_template",
            )


def _validate_slot_schema_template_refs(world: World, slot_schema: dict[str, Any]) -> None:
    for slot_name, slot_spec in (slot_schema or {}).items():
        if not isinstance(slot_spec, dict):
            continue
        resolve_spec = slot_spec.get("resolve") if isinstance(slot_spec.get("resolve"), dict) else slot_spec
        if not isinstance(resolve_spec, dict):
            continue
        raw_value = resolve_spec.get("entity")
        if raw_value is None:
            raw_value = resolve_spec.get("value")
        if not isinstance(raw_value, str):
            continue
        prefix, sep, _ = raw_value.strip().partition(".")
        expected_type = canonical_template_type(prefix) if sep == "." else None
        if expected_type:
            _validate_template_ref(
                world,
                raw_value,
                expected_type,
                f"spec.slots.{slot_name}",
            )


def _validate_quest_template_refs(
    *,
    world: World,
    discovery_policy: dict[str, Any],
    slot_schema: dict[str, Any],
    steps: list[dict[str, Any]],
    reward_policy: dict[str, Any],
) -> None:
    for index, source in enumerate(discovery_policy.get("sources", []) or []):
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("type") or "").strip().lower()
        if source_type == "npc_dialogue":
            _validate_template_ref(
                world,
                source.get("mob_template") or source.get("mob_template_id"),
                "mobtemplate",
                f"spec.discovery.sources[{index}].mob_template",
            )

    _validate_condition_template_refs(world, discovery_policy.get("visible_if"), "spec.discovery.visible_if")
    _validate_condition_template_refs(world, discovery_policy.get("accept_if"), "spec.discovery.accept_if")
    _validate_slot_schema_template_refs(world, slot_schema)

    for step_index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        _validate_effect_template_refs(world, step.get("effects"), f"spec.steps[{step_index}].effects")
        for objective_index, objective in enumerate(step.get("objectives") or []):
            if not isinstance(objective, dict):
                continue
            tracker = objective.get("tracker") or {}
            _validate_condition_template_refs(
                world,
                tracker.get("where"),
                f"spec.steps[{step_index}].objectives[{objective_index}].tracker.where",
            )
        for choice_index, choice in enumerate(step.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            _validate_condition_template_refs(
                world,
                choice.get("if"),
                f"spec.steps[{step_index}].choices[{choice_index}].if",
            )
            _validate_effect_template_refs(
                world,
                choice.get("effects"),
                f"spec.steps[{step_index}].choices[{choice_index}].effects",
            )
        for transition_index, transition in enumerate(step.get("transitions") or []):
            if not isinstance(transition, dict):
                continue
            _validate_condition_template_refs(
                world,
                transition.get("when"),
                f"spec.steps[{step_index}].transitions[{transition_index}].when",
            )
            _validate_effect_template_refs(
                world,
                transition.get("effects"),
                f"spec.steps[{step_index}].transitions[{transition_index}].effects",
            )

    for resolution_key, effects in (reward_policy or {}).items():
        _validate_effect_template_refs(
            world,
            effects,
            f"spec.rewards.{resolution_key}",
        )


class QuestRepeatabilitySpec(BaseModel):
    mode: str = "never"
    cooldown_seconds: int = 0

    @model_validator(mode="after")
    def validate_values(self):
        if self.mode not in QUEST_REPEATABILITY_MODES:
            raise ValueError(f"mode must be one of: {', '.join(QUEST_REPEATABILITY_MODES)}")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        if self.mode != "cooldown" and self.cooldown_seconds:
            raise ValueError("cooldown_seconds is only valid when mode is 'cooldown'")
        return self


class QuestDiscoverySourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str


class QuestDiscoverySpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    sources: list[QuestDiscoverySourceSpec] = Field(default_factory=list)
    visible_if: Any = Field(default_factory=dict)
    accept_if: Any = Field(default_factory=dict)
    salience: int = 0
    cooldown_seconds: int = 0

    @model_validator(mode="after")
    def validate_values(self):
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        return self


class QuestStepSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    kind: str
    recap: str = ""
    text: dict[str, Any] = Field(default_factory=dict)
    objectives: list[dict[str, Any]] = Field(default_factory=list)
    choices: list[dict[str, Any]] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def validate_removed_fields(cls, data):
        if not isinstance(data, dict):
            return data
        unsupported = [field for field in ("lead", "stakes") if field in data]
        if unsupported:
            raise ValueError(
                f"{', '.join(unsupported)} are no longer supported on quest steps; use recap/text instead"
            )
        return data

    @model_validator(mode="after")
    def validate_values(self):
        step_id = self.id.strip()
        if not step_id:
            raise ValueError("step ids cannot be blank")
        self.id = step_id
        normalized_kind = self.kind.strip().lower()
        if normalized_kind not in STEP_KINDS:
            raise ValueError(f"step kind must be one of: {', '.join(sorted(STEP_KINDS))}")
        self.kind = normalized_kind
        return self


class QuestRewardsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: list[dict[str, Any]] = Field(default_factory=list)
    compromised: list[dict[str, Any]] = Field(default_factory=list)
    failed_forward: list[dict[str, Any]] = Field(default_factory=list)
    expired: list[dict[str, Any]] = Field(default_factory=list)


class QuestSpec(BaseModel):
    type: str = "quest"
    scope: str = "player"
    status: str = "draft"
    arc: str | None = None
    repeatability: QuestRepeatabilitySpec = Field(default_factory=QuestRepeatabilitySpec)
    max_active: int = 1
    discovery: QuestDiscoverySpec = Field(default_factory=QuestDiscoverySpec)
    slots: dict[str, Any] = Field(default_factory=dict)
    steps: list[QuestStepSpec] = Field(default_factory=list)
    rewards: QuestRewardsSpec = Field(default_factory=QuestRewardsSpec)

    @model_validator(mode="after")
    def validate_values(self):
        if self.type not in QUEST_TEMPLATE_TYPES:
            raise ValueError(f"type must be one of: {', '.join(QUEST_TEMPLATE_TYPES)}")
        if self.scope not in QUEST_SCOPES:
            raise ValueError(f"scope must be one of: {', '.join(QUEST_SCOPES)}")
        if self.status not in QUEST_TEMPLATE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(QUEST_TEMPLATE_STATUSES)}")
        if self.max_active < 0:
            raise ValueError("max_active must be >= 0")
        if not self.steps:
            raise ValueError("at least one step is required")

        seen_ids: set[str] = set()
        step_ids: set[str] = set()
        for step in self.steps:
            if step.id in seen_ids:
                raise ValueError(f"duplicate step id '{step.id}'")
            seen_ids.add(step.id)
            step_ids.add(step.id)

        for step in self.steps:
            for choice in step.choices:
                goto = str(choice.get("goto") or "").strip()
                if goto and goto not in step_ids:
                    raise ValueError(
                        f"step '{step.id}' choice references unknown goto '{goto}'"
                    )
            for transition in step.transitions:
                goto = str(transition.get("goto") or "").strip()
                if goto and goto not in step_ids:
                    raise ValueError(
                        f"step '{step.id}' transition references unknown goto '{goto}'"
                    )
        return self


class QuestArcSpec(BaseModel):
    summary: str = ""
    journal_policy: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ParsedQuestManifest:
    world: World
    quest: QuestTemplate | None
    quest_id: int | None
    slug: str
    name: str
    quest_type: str
    scope: str
    status: str
    arc: QuestArcTemplate | None
    repeatability_mode: str
    repeatability_cooldown_seconds: int
    max_active: int
    discovery_policy: dict[str, Any]
    slot_schema: dict[str, Any]
    graph: dict[str, Any]
    reward_policy: dict[str, Any]
    manifest_version: str


@dataclass
class ParsedQuestDeleteManifest:
    world: World
    quest: QuestTemplate
    quest_id: int


@dataclass
class ParsedQuestArcManifest:
    world: World
    quest_arc: QuestArcTemplate | None
    quest_arc_id: int | None
    slug: str
    name: str
    summary: str
    journal_policy: dict[str, Any]


@dataclass
class ParsedQuestArcDeleteManifest:
    world: World
    quest_arc: QuestArcTemplate
    quest_arc_id: int


def _sanitize_step_payloads(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    sanitized_steps: list[dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        sanitized_steps.append(
            {
                key: value
                for key, value in step.items()
                if key not in {"lead", "stakes"}
            }
        )
    return sanitized_steps


def parse_manifest_kind(manifest: dict[str, Any]) -> str:
    _validate_api_version(manifest)
    manifest_kind = _normalize_kind(manifest.get("kind"), "kind")
    if manifest_kind == QUEST_MANIFEST_KIND:
        return QUEST_MANIFEST_KIND
    if manifest_kind in QUEST_ARC_MANIFEST_KIND_ALIASES:
        return QUEST_ARC_MANIFEST_KIND
    raise serializers.ValidationError(
        f"Unsupported manifest kind '{manifest_kind}'. Supported kinds: {QUEST_MANIFEST_KIND}, {QUEST_ARC_MANIFEST_KIND}."
    )


def _validate_world_reference(*, world: World, metadata: dict[str, Any]) -> None:
    world_ref = metadata.get("world")
    if world_ref is None:
        return
    manifest_world_id = _parse_entity_ref(
        world_ref,
        expected_type="world",
        field_name="metadata.world",
    )
    if manifest_world_id != world.id:
        raise serializers.ValidationError("Manifest world does not match the selected world.")


def _resolve_quest_reference(*, world: World, metadata: dict[str, Any]) -> tuple[QuestTemplate | None, int | None]:
    quest_id = metadata.get("id")
    quest_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if quest_id is not None:
        parsed_id = _parse_entity_ref(quest_id, "questtemplate", "metadata.id")
        resolved_by_id = QuestTemplate.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError("Quest referenced by metadata.id was not found.")

    resolved_by_slug = None
    if quest_slug:
        resolved_by_slug = QuestTemplate.objects.filter(world=world, slug=quest_slug).first()
        if not resolved_by_slug and resolved_by_id is None:
            resolved_by_slug = None

    if resolved_by_id and resolved_by_slug and resolved_by_id.pk != resolved_by_slug.pk:
        raise serializers.ValidationError("metadata.id and metadata.slug refer to different quests.")

    quest = resolved_by_id or resolved_by_slug
    if quest is None:
        return None, None
    return quest, quest.id


def _resolve_quest_arc_reference(
    *,
    world: World,
    metadata: dict[str, Any],
) -> tuple[QuestArcTemplate | None, int | None]:
    quest_arc_id = metadata.get("id")
    quest_arc_slug = str(metadata.get("slug") or "").strip()

    resolved_by_id = None
    if quest_arc_id is not None:
        parsed_id = _parse_entity_ref(quest_arc_id, "questarctemplate", "metadata.id")
        resolved_by_id = QuestArcTemplate.objects.filter(world=world, pk=parsed_id).first()
        if not resolved_by_id:
            raise serializers.ValidationError("Quest arc referenced by metadata.id was not found.")

    resolved_by_slug = None
    if quest_arc_slug:
        resolved_by_slug = QuestArcTemplate.objects.filter(world=world, slug=quest_arc_slug).first()

    if resolved_by_id and resolved_by_slug and resolved_by_id.pk != resolved_by_slug.pk:
        raise serializers.ValidationError("metadata.id and metadata.slug refer to different quest arcs.")

    quest_arc = resolved_by_id or resolved_by_slug
    if quest_arc is None:
        return None, None
    return quest_arc, quest_arc.id


def quest_template_to_manifest(quest: QuestTemplate) -> dict[str, Any]:
    manifest = {
        "kind": QUEST_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key("world", quest.world_id),
            "id": quest.id,
            "key": quest.key,
            "slug": quest.slug,
            "name": quest.name,
        },
        "spec": {
            "type": quest.quest_type,
            "scope": quest.scope,
            "status": quest.status,
            "arc": quest.arc.slug if quest.arc else "",
            "repeatability": {
                "mode": quest.repeatability_mode,
                "cooldown_seconds": int(quest.repeatability_cooldown_seconds),
            },
            "max_active": int(quest.max_active),
            "discovery": quest.discovery_policy or {},
            "slots": quest.slot_schema or {},
            "steps": _sanitize_step_payloads((quest.graph or {}).get("steps", [])),
            "rewards": quest.reward_policy or {
                key: [] for key in RESOLUTION_KEYS
            },
        },
    }
    return manifest


def quest_template_delete_manifest(quest: QuestTemplate) -> dict[str, Any]:
    return {
        "kind": QUEST_MANIFEST_KIND,
        "operation": MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key("world", quest.world_id),
            "id": quest.id,
            "key": quest.key,
            "slug": quest.slug,
            "name": quest.name,
        },
    }


def serialize_quest_template_payload(quest: QuestTemplate) -> dict[str, Any]:
    manifest = quest_template_to_manifest(quest)
    delete_manifest = quest_template_delete_manifest(quest)
    return {
        "id": quest.id,
        "key": quest.key,
        "slug": quest.slug,
        "name": quest.name,
        "quest_type": quest.quest_type,
        "scope": quest.scope,
        "status": quest.status,
        "arc": (
            {
                "id": quest.arc.id,
                "key": quest.arc.key,
                "slug": quest.arc.slug,
                "name": quest.arc.name,
            }
            if quest.arc else None
        ),
        "manifest_version": quest.manifest_version,
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def quest_template_manifest_template(*, world: World) -> dict[str, Any]:
    return {
        "kind": QUEST_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key("world", world.id),
            "slug": "new_quest",
            "name": "New Quest",
        },
        "spec": {
            "type": "quest",
            "scope": "player",
            "status": "draft",
            "repeatability": {
                "mode": "never",
                "cooldown_seconds": 0,
            },
            "max_active": 1,
            "discovery": {
                "sources": [],
                "visible_if": {},
                "accept_if": {},
                "salience": 0,
                "cooldown_seconds": 0,
            },
            "slots": {},
            "steps": [
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "A new quest opportunity appears.",
                    "text": {
                        "body": "Replace this with authored prose.",
                    },
                    "choices": [
                        {
                            "id": "continue",
                            "text": "Continue.",
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The quest resolves.",
                },
            ],
            "rewards": {
                "complete": [],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        },
    }


def serialize_quest_template_template(*, world: World) -> dict[str, Any]:
    manifest = quest_template_manifest_template(world=world)
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def quest_arc_template_to_manifest(quest_arc: QuestArcTemplate) -> dict[str, Any]:
    return {
        "kind": QUEST_ARC_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key("world", quest_arc.world_id),
            "id": quest_arc.id,
            "key": quest_arc.key,
            "slug": quest_arc.slug,
            "name": quest_arc.name,
        },
        "spec": {
            "summary": quest_arc.summary or "",
            "journal_policy": quest_arc.journal_policy or {},
        },
    }


def quest_arc_delete_manifest(quest_arc: QuestArcTemplate) -> dict[str, Any]:
    return {
        "kind": QUEST_ARC_MANIFEST_KIND,
        "operation": MANIFEST_OPERATION_DELETE,
        "metadata": {
            "world": _entity_key("world", quest_arc.world_id),
            "id": quest_arc.id,
            "key": quest_arc.key,
            "slug": quest_arc.slug,
            "name": quest_arc.name,
        },
    }


def serialize_quest_arc_payload(quest_arc: QuestArcTemplate) -> dict[str, Any]:
    manifest = quest_arc_template_to_manifest(quest_arc)
    delete_manifest = quest_arc_delete_manifest(quest_arc)
    return {
        "id": quest_arc.id,
        "key": quest_arc.key,
        "slug": quest_arc.slug,
        "name": quest_arc.name,
        "summary": quest_arc.summary or "",
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
        "delete_manifest": delete_manifest,
        "delete_yaml": manifest_to_yaml(delete_manifest),
    }


def quest_arc_manifest_template(*, world: World) -> dict[str, Any]:
    return {
        "kind": QUEST_ARC_MANIFEST_KIND,
        "metadata": {
            "world": _entity_key("world", world.id),
            "slug": "new_arc",
            "name": "New Arc",
        },
        "spec": {
            "summary": "Replace this with an arc summary.",
            "journal_policy": {},
        },
    }


def serialize_quest_arc_template(*, world: World) -> dict[str, Any]:
    manifest = quest_arc_manifest_template(world=world)
    return {
        "manifest": manifest,
        "yaml": manifest_to_yaml(manifest),
    }


def parse_quest_manifest(*, world: World, manifest: dict[str, Any]) -> ParsedQuestManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != QUEST_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{QUEST_MANIFEST_KIND}'."
        )

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")
    _validate_world_reference(world=world, metadata=metadata)

    quest, quest_id = _resolve_quest_reference(world=world, metadata=metadata)

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")

    if quest is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating a quest.")

    base_spec = quest_template_manifest_template(world=world)["spec"]
    if quest is not None:
        base_spec = quest_template_to_manifest(quest)["spec"]
    merged_spec = _deep_merge(base_spec, spec_patch)

    try:
        validated_spec = QuestSpec.model_validate(merged_spec)
    except ValidationError as exc:
        raise serializers.ValidationError(exc.errors())

    slug_source = metadata.get("slug") or (quest.slug if quest else metadata.get("name"))
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if QuestTemplate.objects.filter(world=world, slug=slug).exclude(pk=quest_id).exists():
        raise serializers.ValidationError("metadata.slug is already used by another quest.")

    name = _coerce_text(metadata.get("name", quest.name if quest else slug.replace("_", " ").replace("-", " ").title()))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    manifest_version = str(manifest.get("apiVersion") or MANIFEST_API_VERSION).strip()

    arc = None
    arc_slug = str(validated_spec.arc or "").strip()
    if arc_slug:
        arc = QuestArcTemplate.objects.filter(world=world, slug=arc_slug).first()
        if not arc:
            raise serializers.ValidationError(f"Quest arc '{arc_slug}' was not found in this world.")

    discovery_policy = validated_spec.discovery.model_dump()
    slot_schema = validated_spec.slots
    steps = _sanitize_step_payloads([step.model_dump() for step in validated_spec.steps])
    reward_policy = validated_spec.rewards.model_dump()
    _validate_quest_template_refs(
        world=world,
        discovery_policy=discovery_policy,
        slot_schema=slot_schema,
        steps=steps,
        reward_policy=reward_policy,
    )

    return ParsedQuestManifest(
        world=world,
        quest=quest,
        quest_id=quest_id,
        slug=slug,
        name=name,
        quest_type=validated_spec.type,
        scope=validated_spec.scope,
        status=validated_spec.status,
        arc=arc,
        repeatability_mode=validated_spec.repeatability.mode,
        repeatability_cooldown_seconds=validated_spec.repeatability.cooldown_seconds,
        max_active=validated_spec.max_active,
        discovery_policy=discovery_policy,
        slot_schema=slot_schema,
        graph={"steps": steps},
        reward_policy=reward_policy,
        manifest_version=manifest_version,
    )


def parse_quest_delete_manifest(*, world: World, manifest: dict[str, Any]) -> ParsedQuestDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != QUEST_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{QUEST_MANIFEST_KIND}'."
        )
    operation = parse_manifest_operation(manifest)
    if operation != MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")
    _validate_world_reference(world=world, metadata=metadata)

    quest, quest_id = _resolve_quest_reference(world=world, metadata=metadata)
    if quest is None or quest_id is None:
        raise serializers.ValidationError("metadata.id or metadata.slug is required for operation: delete.")

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedQuestDeleteManifest(world=world, quest=quest, quest_id=quest_id)


def parse_quest_arc_manifest(*, world: World, manifest: dict[str, Any]) -> ParsedQuestArcManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != QUEST_ARC_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{QUEST_ARC_MANIFEST_KIND}'."
        )

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")
    _validate_world_reference(world=world, metadata=metadata)

    quest_arc, quest_arc_id = _resolve_quest_arc_reference(world=world, metadata=metadata)

    spec_patch = manifest.get("spec") or {}
    if not isinstance(spec_patch, dict):
        raise serializers.ValidationError("spec must be a mapping.")
    if quest_arc is None and not spec_patch:
        raise serializers.ValidationError("spec is required when creating a quest arc.")

    base_spec = quest_arc_manifest_template(world=world)["spec"]
    if quest_arc is not None:
        base_spec = quest_arc_template_to_manifest(quest_arc)["spec"]
    merged_spec = _deep_merge(base_spec, spec_patch)

    try:
        validated_spec = QuestArcSpec.model_validate(merged_spec)
    except ValidationError as exc:
        raise serializers.ValidationError(exc.errors())

    slug_source = metadata.get("slug") or (quest_arc.slug if quest_arc else metadata.get("name"))
    slug = _slug_or_error(str(slug_source or ""), "metadata.slug")
    if QuestArcTemplate.objects.filter(world=world, slug=slug).exclude(pk=quest_arc_id).exists():
        raise serializers.ValidationError("metadata.slug is already used by another quest arc.")

    name = _coerce_text(metadata.get("name", quest_arc.name if quest_arc else slug.replace("_", " ").replace("-", " ").title()))
    if not name.strip():
        raise serializers.ValidationError("metadata.name cannot be empty.")

    return ParsedQuestArcManifest(
        world=world,
        quest_arc=quest_arc,
        quest_arc_id=quest_arc_id,
        slug=slug,
        name=name,
        summary=validated_spec.summary,
        journal_policy=validated_spec.journal_policy,
    )


def parse_quest_arc_delete_manifest(
    *,
    world: World,
    manifest: dict[str, Any],
) -> ParsedQuestArcDeleteManifest:
    manifest_kind = parse_manifest_kind(manifest)
    if manifest_kind != QUEST_ARC_MANIFEST_KIND:
        raise serializers.ValidationError(
            f"Unsupported manifest kind '{manifest_kind}'. Expected '{QUEST_ARC_MANIFEST_KIND}'."
        )
    operation = parse_manifest_operation(manifest)
    if operation != MANIFEST_OPERATION_DELETE:
        raise serializers.ValidationError("Delete parser requires operation: delete.")

    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise serializers.ValidationError("metadata must be a mapping.")
    _validate_world_reference(world=world, metadata=metadata)

    quest_arc, quest_arc_id = _resolve_quest_arc_reference(world=world, metadata=metadata)
    if quest_arc is None or quest_arc_id is None:
        raise serializers.ValidationError("metadata.id or metadata.slug is required for operation: delete.")

    spec = manifest.get("spec")
    if spec not in (None, {}):
        raise serializers.ValidationError("spec is not allowed for operation: delete.")

    return ParsedQuestArcDeleteManifest(
        world=world,
        quest_arc=quest_arc,
        quest_arc_id=quest_arc_id,
    )


def apply_quest_manifest(parsed: ParsedQuestManifest) -> QuestTemplate:
    defaults = {
        "name": parsed.name,
        "quest_type": parsed.quest_type,
        "scope": parsed.scope,
        "status": parsed.status,
        "arc": parsed.arc,
        "repeatability_mode": parsed.repeatability_mode,
        "repeatability_cooldown_seconds": parsed.repeatability_cooldown_seconds,
        "max_active": parsed.max_active,
        "discovery_policy": parsed.discovery_policy,
        "slot_schema": parsed.slot_schema,
        "graph": parsed.graph,
        "reward_policy": parsed.reward_policy,
        "manifest_version": parsed.manifest_version,
    }

    with transaction.atomic():
        if parsed.quest is None:
            quest = QuestTemplate.objects.create(
                world=parsed.world,
                slug=parsed.slug,
                **defaults,
            )
        else:
            quest = parsed.quest
            quest.slug = parsed.slug
            for field_name, value in defaults.items():
                setattr(quest, field_name, value)
            quest.save()
    return quest


def apply_quest_arc_manifest(parsed: ParsedQuestArcManifest) -> QuestArcTemplate:
    defaults = {
        "name": parsed.name,
        "summary": parsed.summary,
        "journal_policy": parsed.journal_policy,
    }

    with transaction.atomic():
        if parsed.quest_arc is None:
            quest_arc = QuestArcTemplate.objects.create(
                world=parsed.world,
                slug=parsed.slug,
                **defaults,
            )
        else:
            quest_arc = parsed.quest_arc
            quest_arc.slug = parsed.slug
            for field_name, value in defaults.items():
                setattr(quest_arc, field_name, value)
            quest_arc.save()
    return quest_arc
