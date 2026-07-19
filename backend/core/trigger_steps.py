from __future__ import annotations

import json
import re
from typing import Any, Callable


TRIGGER_STEP_ERROR_POLICY_CANCEL = "cancel"
TRIGGER_STEP_ERROR_POLICIES = (TRIGGER_STEP_ERROR_POLICY_CANCEL,)

TRIGGER_STEP_ACTION_CONSUME_ITEM = "consume_item"
TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM = "spawn_room_item"
TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM = "replace_room_item"
TRIGGER_STEP_ACTION_ECHO = "echo"
TRIGGER_STEP_ACTION_TYPES = (
    TRIGGER_STEP_ACTION_CONSUME_ITEM,
    TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM,
    TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM,
    TRIGGER_STEP_ACTION_ECHO,
)

TRIGGER_ACTOR_REF = "trigger_actor"
TRIGGER_ROOM_REF = "trigger_room"

MAX_TRIGGER_STEPS = 32
MAX_TRIGGER_STEP_ACTIONS = 16
MAX_TRIGGER_STEP_DELAY_SECONDS = 31_536_000
MAX_TRIGGER_SEQUENCE_DURATION_SECONDS = 31_536_000
MAX_TRIGGER_CONSUME_ITEM_COUNT = 1_000
MAX_TRIGGER_ECHO_LENGTH = 4_000
# Keeps cached hooks and durable run snapshots bounded even when every action
# uses its maximum field size.
MAX_TRIGGER_STEPS_SERIALIZED_BYTES = 256 * 1024

_BINDING_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TriggerStepSpecError(ValueError):
    pass


ItemRefNormalizer = Callable[[Any, str], str]


def normalize_trigger_step_error_policy(value: Any) -> str:
    policy = str(value or TRIGGER_STEP_ERROR_POLICY_CANCEL).strip().lower()
    if policy not in TRIGGER_STEP_ERROR_POLICIES:
        raise TriggerStepSpecError(
            "spec.on_step_error must be one of: "
            f"{', '.join(TRIGGER_STEP_ERROR_POLICIES)}."
        )
    return policy


def _integer(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TriggerStepSpecError(f"{field_name} must be an integer.")
    if value < minimum:
        qualifier = "non-negative" if minimum == 0 else "positive"
        raise TriggerStepSpecError(f"{field_name} must be a {qualifier} integer.")
    if maximum is not None and value > maximum:
        raise TriggerStepSpecError(
            f"{field_name} cannot exceed {maximum}."
        )
    return value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TriggerStepSpecError(f"{field_name} must be a mapping.")
    return value


def _exact_fields(
    value: dict[str, Any],
    *,
    field_name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - set(value))
    if missing:
        raise TriggerStepSpecError(
            f"{field_name} is missing required field(s): {', '.join(missing)}."
        )
    unsupported = sorted(set(value) - required - optional)
    if unsupported:
        raise TriggerStepSpecError(
            f"{field_name} has unsupported field(s): {', '.join(unsupported)}."
        )


def _context_ref(value: Any, *, field_name: str, expected: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized != expected:
        raise TriggerStepSpecError(f"{field_name} must be '{expected}'.")
    return normalized


def _item_ref(
    value: Any,
    *,
    field_name: str,
    item_ref_normalizer: ItemRefNormalizer | None,
) -> str:
    if item_ref_normalizer is not None:
        return item_ref_normalizer(value, field_name)
    normalized = str(value or "").strip()
    if not normalized:
        raise TriggerStepSpecError(f"{field_name} is required.")
    prefix, separator, _ = normalized.partition(".")
    if separator and prefix.strip().lower() not in {
        "itemdefinition",
        "item_definition",
    }:
        raise TriggerStepSpecError(
            f"{field_name} must reference an itemdefinition."
        )
    return normalized


def _binding(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _BINDING_RE.fullmatch(normalized):
        raise TriggerStepSpecError(
            f"{field_name} must start with a lowercase letter and contain only "
            "lowercase letters, numbers, and underscores (maximum 64 characters)."
        )
    return normalized


def _normalize_action(
    value: Any,
    *,
    field_name: str,
    bindings: set[str],
    item_ref_normalizer: ItemRefNormalizer | None,
) -> dict[str, Any]:
    action = _mapping(value, field_name=field_name)
    action_type = str(action.get("type") or "").strip().lower()
    if action_type not in TRIGGER_STEP_ACTION_TYPES:
        raise TriggerStepSpecError(
            f"{field_name}.type must be one of: "
            f"{', '.join(TRIGGER_STEP_ACTION_TYPES)}."
        )

    if action_type == TRIGGER_STEP_ACTION_CONSUME_ITEM:
        _exact_fields(
            action,
            field_name=field_name,
            required={"type", "actor", "item"},
            optional={"count"},
        )
        return {
            "type": action_type,
            "actor": _context_ref(
                action.get("actor"),
                field_name=f"{field_name}.actor",
                expected=TRIGGER_ACTOR_REF,
            ),
            "item": _item_ref(
                action.get("item"),
                field_name=f"{field_name}.item",
                item_ref_normalizer=item_ref_normalizer,
            ),
            "count": _integer(
                action.get("count", 1),
                field_name=f"{field_name}.count",
                minimum=1,
                maximum=MAX_TRIGGER_CONSUME_ITEM_COUNT,
            ),
        }

    if action_type == TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM:
        _exact_fields(
            action,
            field_name=field_name,
            required={"type", "room", "item"},
            optional={"bind"},
        )
        normalized: dict[str, Any] = {
            "type": action_type,
            "room": _context_ref(
                action.get("room"),
                field_name=f"{field_name}.room",
                expected=TRIGGER_ROOM_REF,
            ),
            "item": _item_ref(
                action.get("item"),
                field_name=f"{field_name}.item",
                item_ref_normalizer=item_ref_normalizer,
            ),
        }
        if "bind" in action:
            binding = _binding(action.get("bind"), field_name=f"{field_name}.bind")
            if binding in bindings:
                raise TriggerStepSpecError(
                    f"{field_name}.bind duplicates the binding '{binding}'."
                )
            bindings.add(binding)
            normalized["bind"] = binding
        return normalized

    if action_type == TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM:
        _exact_fields(
            action,
            field_name=field_name,
            required={"type", "target", "with"},
        )
        target = _binding(action.get("target"), field_name=f"{field_name}.target")
        if target not in bindings:
            raise TriggerStepSpecError(
                f"{field_name}.target must name a binding created by an earlier "
                "spawn_room_item action."
            )
        return {
            "type": action_type,
            "target": target,
            "with": _item_ref(
                action.get("with"),
                field_name=f"{field_name}.with",
                item_ref_normalizer=item_ref_normalizer,
            ),
        }

    _exact_fields(
        action,
        field_name=field_name,
        required={"type", "room", "text"},
    )
    raw_text = action.get("text")
    if not isinstance(raw_text, str):
        raise TriggerStepSpecError(f"{field_name}.text must be a string.")
    text = raw_text.strip()
    if not text:
        raise TriggerStepSpecError(f"{field_name}.text is required.")
    if len(text) > MAX_TRIGGER_ECHO_LENGTH:
        raise TriggerStepSpecError(
            f"{field_name}.text cannot exceed {MAX_TRIGGER_ECHO_LENGTH} characters."
        )
    return {
        "type": action_type,
        "room": _context_ref(
            action.get("room"),
            field_name=f"{field_name}.room",
            expected=TRIGGER_ROOM_REF,
        ),
        "text": text,
    }


def normalize_trigger_steps(
    value: Any,
    *,
    item_ref_normalizer: ItemRefNormalizer | None = None,
) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise TriggerStepSpecError("spec.steps must be a list.")
    if not value:
        return []
    if len(value) > MAX_TRIGGER_STEPS:
        raise TriggerStepSpecError(
            f"spec.steps cannot contain more than {MAX_TRIGGER_STEPS} steps."
        )

    bindings: set[str] = set()
    normalized_steps: list[dict[str, Any]] = []
    cumulative_delay_seconds = 0
    for step_index, raw_step in enumerate(value):
        field_name = f"spec.steps[{step_index}]"
        step = _mapping(raw_step, field_name=field_name)
        _exact_fields(
            step,
            field_name=field_name,
            required={"after_seconds", "actions"},
        )
        after_seconds = _integer(
            step.get("after_seconds"),
            field_name=f"{field_name}.after_seconds",
            minimum=0,
            maximum=MAX_TRIGGER_STEP_DELAY_SECONDS,
        )
        if step_index == 0 and after_seconds != 0:
            raise TriggerStepSpecError(
                "spec.steps[0].after_seconds must be 0 so the initial step can "
                "run atomically with trigger conditions."
            )
        if step_index > 0 and after_seconds == 0:
            raise TriggerStepSpecError(
                f"{field_name}.after_seconds must be positive; combine immediate "
                "actions into the preceding step."
            )
        cumulative_delay_seconds += after_seconds
        if cumulative_delay_seconds > MAX_TRIGGER_SEQUENCE_DURATION_SECONDS:
            raise TriggerStepSpecError(
                "spec.steps cumulative after_seconds cannot exceed "
                f"{MAX_TRIGGER_SEQUENCE_DURATION_SECONDS}."
            )

        raw_actions = step.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise TriggerStepSpecError(f"{field_name}.actions must be a non-empty list.")
        if len(raw_actions) > MAX_TRIGGER_STEP_ACTIONS:
            raise TriggerStepSpecError(
                f"{field_name}.actions cannot contain more than "
                f"{MAX_TRIGGER_STEP_ACTIONS} actions."
            )
        normalized_steps.append({
            "after_seconds": after_seconds,
            "actions": [
                _normalize_action(
                    action,
                    field_name=f"{field_name}.actions[{action_index}]",
                    bindings=bindings,
                    item_ref_normalizer=item_ref_normalizer,
                )
                for action_index, action in enumerate(raw_actions)
            ],
        })
    serialized_size = len(
        json.dumps(
            normalized_steps,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if serialized_size > MAX_TRIGGER_STEPS_SERIALIZED_BYTES:
        raise TriggerStepSpecError(
            "spec.steps normalized serialized size cannot exceed "
            f"{MAX_TRIGGER_STEPS_SERIALIZED_BYTES} bytes."
        )
    return normalized_steps
