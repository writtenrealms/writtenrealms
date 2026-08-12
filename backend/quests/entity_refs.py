from __future__ import annotations

from typing import Any

from builders.models import ItemDefinition, MobDefinition
from quests.models import QuestTemplate
from worlds.models import World
from worlds.room_refs import parse_room_reference, resolve_room_reference_id


_ENTITY_TYPE_ALIASES = {
    "itemdefinition": "itemdefinition",
    "item_definition": "itemdefinition",
    "mobdefinition": "mobdefinition",
    "mob_definition": "mobdefinition",
    "questtemplate": "questtemplate",
    "quest_template": "questtemplate",
}

_ENTITY_MODELS = {
    "itemdefinition": ItemDefinition,
    "mobdefinition": MobDefinition,
    "questtemplate": QuestTemplate,
}

def canonical_entity_type(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _ENTITY_TYPE_ALIASES.get(text)


def canonical_template_type(value: str | None) -> str | None:
    return canonical_entity_type(value)


def is_dynamic_reference(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("{") and text.endswith("}") and len(text) >= 3


def resolve_entity_ref_id(
    *,
    world: World | None,
    value: Any,
    expected_type: str,
) -> int | None:
    expected = canonical_entity_type(expected_type)
    if not expected:
        return None
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if is_dynamic_reference(value):
        return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)

    prefix, sep, raw = text.partition(".")
    if sep == ".":
        canonical_prefix = canonical_entity_type(prefix)
        if not canonical_prefix or canonical_prefix != expected:
            return None
        text = raw.strip()
        if not text:
            return None
        # Explicitly typed definition references are portable slug refs, even
        # when the slug contains digits only. Bare numeric values above retain
        # the legacy database-id meaning.

    if not world:
        return None
    model_cls = _ENTITY_MODELS[expected]
    return model_cls.objects.filter(world=world, slug=text).values_list("id", flat=True).first()


def resolve_room_ref_id(
    *,
    world: World | None,
    value: Any,
) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if is_dynamic_reference(value):
        return None

    parsed = parse_room_reference(value)
    if parsed is None or parsed.kind != "relative_id":
        return None

    # Persisted authored payloads use stable room identity. Explicit database
    # and coordinate aliases are handled by the manifest import normalizer,
    # before those payloads reach this shared resolver.
    return resolve_room_reference_id(world, f"room@{parsed.relative_id}")
