from __future__ import annotations

from typing import Any

from builders.models import ItemTemplate, MobTemplate
from worlds.models import World


_TEMPLATE_TYPE_ALIASES = {
    "itemtemplate": "itemtemplate",
    "item_template": "itemtemplate",
    "mobtemplate": "mobtemplate",
    "mob_template": "mobtemplate",
}

_TEMPLATE_MODELS = {
    "itemtemplate": ItemTemplate,
    "mobtemplate": MobTemplate,
}


def canonical_template_type(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _TEMPLATE_TYPE_ALIASES.get(text)


def is_dynamic_reference(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("{") and text.endswith("}") and len(text) >= 3


def resolve_template_ref_id(
    *,
    world: World | None,
    value: Any,
    expected_type: str,
) -> int | None:
    expected = canonical_template_type(expected_type)
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
        canonical_prefix = canonical_template_type(prefix)
        if not canonical_prefix or canonical_prefix != expected:
            return None
        if raw.isdigit():
            return int(raw)
        text = raw

    if not world:
        return None
    model_cls = _TEMPLATE_MODELS[expected]
    return model_cls.objects.filter(world=world, slug=text).values_list("id", flat=True).first()

