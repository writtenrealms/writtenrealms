from __future__ import annotations

from functools import lru_cache
import logging
import re
from typing import Any, Iterable, Mapping

from jinja2 import TemplateError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment


logger = logging.getLogger(__name__)


SOCIAL_COMMAND_MAX_LENGTH = 64
SOCIAL_TEMPLATE_MAX_LENGTH = 2_000
SOCIAL_RENDERED_MESSAGE_MAX_LENGTH = 2_000
SOCIAL_PRIORITY_MAX = 1_000_000
SOCIAL_CATALOG_MAX_DEFINITIONS = 512
SOCIAL_TEMPLATE_CONTEXT_MAX_ITEMS = 128
SOCIAL_TEMPLATE_CONTEXT_MAX_DEPTH = 4

SOCIAL_MESSAGE_FIELDS = (
    "msg_targetless_self",
    "msg_targetless_other",
    "msg_targeted_self",
    "msg_targeted_target",
    "msg_targeted_other",
)
SOCIAL_TARGETLESS_FIELDS = SOCIAL_MESSAGE_FIELDS[:2]
SOCIAL_TARGETED_FIELDS = SOCIAL_MESSAGE_FIELDS[2:]

ACTOR_TEMPLATE_VARIABLES = frozenset(
    {
        "actor",
        "Actor",
        "actor_state",
        "actor_title",
        "actor_subject_pronoun",
        "actor_object_pronoun",
        "actor_possessive_adjective",
        "actor_possessive_pronoun",
        "actor_reflexive_pronoun",
    }
)
TARGET_TEMPLATE_VARIABLES = frozenset(
    {
        "target",
        "Target",
        "target_state",
        "target_title",
        "target_subject_pronoun",
        "target_object_pronoun",
        "target_possessive_adjective",
        "target_possessive_pronoun",
        "target_reflexive_pronoun",
    }
)
SOCIAL_TEMPLATE_VARIABLES = ACTOR_TEMPLATE_VARIABLES | TARGET_TEMPLATE_VARIABLES

_SOCIAL_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SOCIAL_TEMPLATE_ENV = SandboxedEnvironment(autoescape=False)
# Socials interpolate character data; they do not need access to Jinja's
# convenience globals (notably ``range``), which can create unbounded output.
_SOCIAL_TEMPLATE_ENV.globals.clear()
_PROHIBITED_TEMPLATE_NODES = (
    nodes.For,
    nodes.Macro,
    nodes.Call,
    nodes.CallBlock,
    nodes.Filter,
    nodes.FilterBlock,
    nodes.Assign,
    nodes.AssignBlock,
    nodes.Import,
    nodes.FromImport,
    nodes.Include,
    nodes.Extends,
    nodes.Block,
    nodes.Add,
    nodes.Sub,
    nodes.Mul,
    nodes.Div,
    nodes.FloorDiv,
    nodes.Mod,
    nodes.Pow,
    nodes.Concat,
    nodes.List,
    nodes.Dict,
    nodes.Tuple,
)


class SocialDefinitionError(ValueError):
    pass


def normalize_social_command(value: Any) -> str:
    return str(value or "").strip().lower()


def validate_social_command(value: Any, *, field_name: str = "command") -> str:
    command = normalize_social_command(value)
    if not _SOCIAL_COMMAND_RE.fullmatch(command):
        raise SocialDefinitionError(
            f"{field_name} must start with a letter and use at most "
            f"{SOCIAL_COMMAND_MAX_LENGTH} lowercase letters, numbers, hyphens, "
            "or underscores."
        )
    return command


def normalize_social_priority(value: Any, *, field_name: str = "priority") -> int:
    if isinstance(value, bool):
        raise SocialDefinitionError(f"{field_name} must be an integer.")
    raw_value = "0" if value in (None, "") else str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", raw_value):
        raise SocialDefinitionError(f"{field_name} must be an integer.")
    priority = int(raw_value)
    if priority < 0 or priority > SOCIAL_PRIORITY_MAX:
        raise SocialDefinitionError(
            f"{field_name} must be between 0 and {SOCIAL_PRIORITY_MAX}."
        )
    return priority


@lru_cache(maxsize=2_048)
def social_template_variables(template_text: str) -> frozenset[str]:
    try:
        parsed = _SOCIAL_TEMPLATE_ENV.parse(template_text)
    except TemplateError as exc:
        raise SocialDefinitionError(f"Invalid social message template: {exc}")
    prohibited = next(parsed.find_all(_PROHIBITED_TEMPLATE_NODES), None)
    if prohibited is not None:
        raise SocialDefinitionError(
            "Social message templates support bounded interpolation and "
            "conditionals, but not loops, calls, filters, imports, assignments, "
            "arithmetic, concatenation, or collection literals."
        )
    for access in parsed.find_all((nodes.Getattr, nodes.Getitem)):
        root = access
        while isinstance(root, (nodes.Getattr, nodes.Getitem)):
            root = root.node
        root_name = root.name if isinstance(root, nodes.Name) else ""
        if root_name not in {"actor_state", "target_state"}:
            raise SocialDefinitionError(
                "Social message templates allow attribute or key access only "
                "within actor_state and target_state."
            )
        accessed_name = (
            access.attr
            if isinstance(access, nodes.Getattr)
            else (
                access.arg.value
                if isinstance(access.arg, nodes.Const)
                else ""
            )
        )
        if str(accessed_name or "").startswith("_"):
            raise SocialDefinitionError(
                "Social message templates cannot access private attributes or keys."
            )
    return frozenset(meta.find_undeclared_variables(parsed))


@lru_cache(maxsize=2_048)
def _compiled_social_template(template_text: str):
    try:
        return _SOCIAL_TEMPLATE_ENV.from_string(template_text)
    except TemplateError as exc:
        raise SocialDefinitionError(f"Invalid social message template: {exc}")


def validate_social_template(
    value: Any,
    *,
    field_name: str,
    allow_target_variables: bool,
) -> str:
    template_text = str(value or "")
    if len(template_text) > SOCIAL_TEMPLATE_MAX_LENGTH:
        raise SocialDefinitionError(
            f"{field_name} cannot exceed {SOCIAL_TEMPLATE_MAX_LENGTH} characters."
        )
    if not template_text.strip():
        return ""

    variables = social_template_variables(template_text)
    allowed_variables = (
        SOCIAL_TEMPLATE_VARIABLES
        if allow_target_variables
        else ACTOR_TEMPLATE_VARIABLES
    )
    unsupported = sorted(variables - allowed_variables)
    if unsupported:
        raise SocialDefinitionError(
            f"{field_name} uses unsupported template variable(s): "
            f"{', '.join(unsupported)}."
        )
    # Compile at authoring time so syntax and sandbox errors do not first
    # surface on the game-processing path.
    _compiled_social_template(template_text)
    return template_text


def validate_social_definition(values: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field_name in SOCIAL_MESSAGE_FIELDS:
        normalized[field_name] = validate_social_template(
            values.get(field_name),
            field_name=field_name,
            allow_target_variables=field_name in SOCIAL_TARGETED_FIELDS,
        )

    targetless_present = [
        bool(normalized[field_name].strip())
        for field_name in SOCIAL_TARGETLESS_FIELDS
    ]
    if any(targetless_present) and not all(targetless_present):
        raise SocialDefinitionError(
            "Targetless socials require both self and other messages."
        )

    targeted_present = [
        bool(normalized[field_name].strip())
        for field_name in SOCIAL_TARGETED_FIELDS
    ]
    if any(targeted_present) and not all(targeted_present):
        raise SocialDefinitionError(
            "Targeted socials require self, target, and other messages."
        )

    if not all(targetless_present) and not all(targeted_present):
        raise SocialDefinitionError(
            "A social requires a complete targetless or targeted message set."
        )
    return normalized


def _capfirst(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _bounded_template_value(value: Any, *, depth: int = 0) -> Any:
    """Copy character-authored context into a small, render-safe value tree."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:SOCIAL_RENDERED_MESSAGE_MAX_LENGTH]
    if depth >= SOCIAL_TEMPLATE_CONTEXT_MAX_DEPTH:
        if isinstance(value, Mapping):
            return {}
        if isinstance(value, (list, tuple)):
            return []
        return str(value)[:SOCIAL_RENDERED_MESSAGE_MAX_LENGTH]
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= SOCIAL_TEMPLATE_CONTEXT_MAX_ITEMS:
                break
            key = str(raw_key)[:SOCIAL_COMMAND_MAX_LENGTH]
            if not key or key.startswith("_"):
                continue
            bounded[key] = _bounded_template_value(raw_value, depth=depth + 1)
        return bounded
    if isinstance(value, (list, tuple)):
        return [
            _bounded_template_value(item, depth=depth + 1)
            for item in value[:SOCIAL_TEMPLATE_CONTEXT_MAX_ITEMS]
        ]
    return str(value)[:SOCIAL_RENDERED_MESSAGE_MAX_LENGTH]


def _character_state(character: Any) -> dict[str, Any]:
    if character is None:
        return {}
    model_meta = getattr(character, "_meta", None)
    if getattr(model_meta, "label_lower", "") != "spawns.player":
        return {}

    from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_snapshot

    return get_state_snapshot(STATE_SCOPE_CHARACTER, character)


def _add_character_context(
    context: dict[str, Any],
    *,
    prefix: str,
    character: Any,
    include_state: bool,
) -> None:
    name = str(getattr(character, "name", "") or "someone")[
        :SOCIAL_RENDERED_MESSAGE_MAX_LENGTH
    ]
    context[prefix] = name
    context[prefix.capitalize()] = _capfirst(name)
    context[f"{prefix}_title"] = str(getattr(character, "title", "") or "")[
        :SOCIAL_RENDERED_MESSAGE_MAX_LENGTH
    ]
    context[f"{prefix}_state"] = (
        _bounded_template_value(_character_state(character))
        if include_state
        else {}
    )

    pronouns = tuple(getattr(character, "pronouns", ()) or ())
    if len(pronouns) != 5:
        pronouns = ("they", "them", "their", "theirs", "themselves")
    (
        context[f"{prefix}_subject_pronoun"],
        context[f"{prefix}_object_pronoun"],
        context[f"{prefix}_possessive_adjective"],
        context[f"{prefix}_possessive_pronoun"],
        context[f"{prefix}_reflexive_pronoun"],
    ) = tuple(
        str(pronoun)[:SOCIAL_RENDERED_MESSAGE_MAX_LENGTH]
        for pronoun in pronouns
    )


def build_social_template_context(
    *,
    actor: Any,
    target: Any = None,
    templates: Iterable[str] = (),
) -> dict[str, Any]:
    referenced_variables: set[str] = set()
    for template_text in templates:
        if template_text:
            referenced_variables.update(social_template_variables(str(template_text)))

    context: dict[str, Any] = {}
    _add_character_context(
        context,
        prefix="actor",
        character=actor,
        include_state="actor_state" in referenced_variables,
    )
    if target is not None:
        _add_character_context(
            context,
            prefix="target",
            character=target,
            include_state="target_state" in referenced_variables,
        )
    return context


def render_social_template(template_text: str, context: Mapping[str, Any]) -> str:
    try:
        template_text = str(template_text or "")
        if len(template_text) > SOCIAL_TEMPLATE_MAX_LENGTH:
            raise SocialDefinitionError(
                "Social message template exceeds the runtime length limit."
            )
        # Runtime defense-in-depth for rows created outside the supported
        # serializer/manifest authoring paths.
        social_template_variables(template_text)
        bounded_context = _bounded_template_value(context)
        chunks: list[str] = []
        remaining = SOCIAL_RENDERED_MESSAGE_MAX_LENGTH
        for chunk in _compiled_social_template(template_text).generate(
            dict(bounded_context) if isinstance(bounded_context, Mapping) else {}
        ):
            if remaining <= 0:
                break
            bounded_chunk = str(chunk)[:remaining]
            chunks.append(bounded_chunk)
            remaining -= len(bounded_chunk)
    except (SocialDefinitionError, TemplateError):
        logger.exception("Failed to render a validated social message template.")
        return "The social could not be displayed."
    return "".join(chunks)
