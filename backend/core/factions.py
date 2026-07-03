from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Q
from rest_framework import serializers


CORE_FACTION_MODE_NONE = "none"
CORE_FACTION_MODE_FIXED_DEFAULT = "fixed_default"
CORE_FACTION_MODE_CHOOSE_REQUIRED = "choose_required"
CORE_FACTION_MODE_CHOOSE_OPTIONAL = "choose_optional"
CORE_FACTION_MODES = {
    CORE_FACTION_MODE_NONE,
    CORE_FACTION_MODE_FIXED_DEFAULT,
    CORE_FACTION_MODE_CHOOSE_REQUIRED,
    CORE_FACTION_MODE_CHOOSE_OPTIONAL,
}


@dataclass(frozen=True)
class CoreFactionPolicy:
    mode: str
    default: str | None
    options: list[str]


def faction_type_filter(faction_type: str) -> Q:
    from builders.models import FACTION_TYPE_CORE, FACTION_TYPE_REPUTATION

    if faction_type == FACTION_TYPE_CORE:
        return Q(type=FACTION_TYPE_CORE) | Q(is_core=True)
    if faction_type == FACTION_TYPE_REPUTATION:
        return Q(type=FACTION_TYPE_REPUTATION, is_core=False)
    return Q(type=faction_type)


def faction_is_core(faction: Any | None) -> bool:
    from builders.models import FACTION_TYPE_CORE

    return bool(faction and (getattr(faction, "type", None) == FACTION_TYPE_CORE or getattr(faction, "is_core", False)))


def faction_is_reputation(faction: Any | None) -> bool:
    return bool(faction and not faction_is_core(faction))


def authored_world(world: Any) -> Any:
    return getattr(world, "context", None) or getattr(world, "instance_of", None) or world


def normalize_faction_code(value: Any, *, field_name: str) -> str:
    code = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not code:
        raise serializers.ValidationError(f"{field_name} is required.")
    return code


def _normalize_code_list(value: Any, *, field_name: str) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise serializers.ValidationError(f"{field_name} must be a list of faction codes.")
    codes: list[str] = []
    for index, raw_code in enumerate(value):
        code = normalize_faction_code(raw_code, field_name=f"{field_name}[{index}]")
        if code not in codes:
            codes.append(code)
    return codes


def _available_core_faction_codes(world: Any, *, options: list[str] | None = None) -> list[str]:
    from builders.models import FACTION_TYPE_CORE, Faction

    qs = (
        Faction.objects
        .filter(world=authored_world(world))
        .filter(faction_type_filter(FACTION_TYPE_CORE))
    )
    if options is None:
        qs = qs.filter(playable=True)
    else:
        qs = qs.filter(code__in=options)
    by_code = {faction.code: faction for faction in qs.order_by("created_ts", "id")}
    if options is not None:
        return [code for code in options if code in by_code]
    return list(by_code.keys())


def validate_core_faction_codes(
    *,
    world: Any,
    codes: list[str],
    field_name: str,
    require_playable: bool = True,
) -> None:
    from builders.models import FACTION_TYPE_CORE, Faction

    if not codes:
        return
    qs = (
        Faction.objects
        .filter(world=authored_world(world), code__in=codes)
        .filter(faction_type_filter(FACTION_TYPE_CORE))
    )
    if require_playable:
        qs = qs.filter(playable=True)
    found = set(qs.values_list("code", flat=True))
    missing = [code for code in codes if code not in found]
    if missing:
        type_label = FACTION_TYPE_CORE
        playable_label = " playable" if require_playable else ""
        raise serializers.ValidationError(
            f"{field_name} references unknown or non-{type_label}{playable_label} faction(s): {', '.join(missing)}."
        )


def normalize_player_creation_config(
    value: Any,
    *,
    world: Any | None = None,
    existing: dict[str, Any] | None = None,
    validate_factions: bool = True,
) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("spec.player_creation must be a mapping.")

    unknown_top = sorted(set(value.keys()) - {"core_faction"})
    if unknown_top:
        raise serializers.ValidationError(
            f"Unsupported spec.player_creation field(s): {', '.join(unknown_top)}."
        )

    existing = existing or {}
    normalized = dict(existing)
    if "core_faction" not in value:
        return normalized

    raw_core = value.get("core_faction") or {}
    if not isinstance(raw_core, dict):
        raise serializers.ValidationError("spec.player_creation.core_faction must be a mapping.")

    unknown_core = sorted(set(raw_core.keys()) - {"mode", "default", "options"})
    if unknown_core:
        raise serializers.ValidationError(
            f"Unsupported spec.player_creation.core_faction field(s): {', '.join(unknown_core)}."
        )

    current_core = dict((existing or {}).get("core_faction") or {})
    mode = str(raw_core.get("mode", current_core.get("mode", CORE_FACTION_MODE_CHOOSE_REQUIRED)) or "").strip().lower()
    if mode not in CORE_FACTION_MODES:
        raise serializers.ValidationError(
            "spec.player_creation.core_faction.mode must be one of: "
            f"{', '.join(sorted(CORE_FACTION_MODES))}."
        )

    default = current_core.get("default")
    if "default" in raw_core:
        raw_default = raw_core.get("default")
        default = None if raw_default in (None, "") else normalize_faction_code(
            raw_default,
            field_name="spec.player_creation.core_faction.default",
        )

    options = current_core.get("options")
    if "options" in raw_core:
        options = _normalize_code_list(
            raw_core.get("options"),
            field_name="spec.player_creation.core_faction.options",
        )

    if mode == CORE_FACTION_MODE_NONE:
        core = {"mode": mode}
    else:
        core = {"mode": mode}
        if default:
            core["default"] = default
        if options is not None:
            core["options"] = options

    if validate_factions and world is not None:
        validation_codes: list[str] = []
        if core.get("default"):
            validation_codes.append(core["default"])
        validation_codes.extend(core.get("options") or [])
        validate_core_faction_codes(
            world=world,
            codes=validation_codes,
            field_name="spec.player_creation.core_faction",
        )

    if mode == CORE_FACTION_MODE_FIXED_DEFAULT and not core.get("default"):
        raise serializers.ValidationError(
            "spec.player_creation.core_faction.default is required when mode is fixed_default."
        )
    if mode == CORE_FACTION_MODE_CHOOSE_REQUIRED and not core.get("default"):
        option_count = len(core.get("options") or [])
        if option_count != 1 and world is not None and options is None:
            available_options = _available_core_faction_codes(world)
            option_count = len(available_options)
            if option_count == 1:
                core["default"] = available_options[0]
        if not core.get("default") and option_count != 1:
            raise serializers.ValidationError(
                "spec.player_creation.core_faction.default is required for choose_required unless exactly one option is available."
            )
        if not core.get("default"):
            core["default"] = core["options"][0]

    normalized["core_faction"] = core
    return normalized


def core_faction_policy(world: Any) -> CoreFactionPolicy:
    config = getattr(authored_world(world), "config", None)
    raw_player_creation = dict(getattr(config, "player_creation", {}) or {})
    raw_core = dict(raw_player_creation.get("core_faction") or {})
    mode = str(raw_core.get("mode") or CORE_FACTION_MODE_CHOOSE_REQUIRED).strip().lower()
    if mode not in CORE_FACTION_MODES:
        mode = CORE_FACTION_MODE_CHOOSE_REQUIRED

    configured_options = _normalize_code_list(
        raw_core.get("options"),
        field_name="player_creation.core_faction.options",
    )
    options = _available_core_faction_codes(world, options=configured_options)
    default = raw_core.get("default")
    default = normalize_faction_code(
        default,
        field_name="player_creation.core_faction.default",
    ) if default else None
    if default and default not in options and mode != CORE_FACTION_MODE_FIXED_DEFAULT:
        default = None
    if mode == CORE_FACTION_MODE_CHOOSE_REQUIRED and not default and len(options) == 1:
        default = options[0]
    if mode == CORE_FACTION_MODE_FIXED_DEFAULT and default and default not in options:
        options = [default]
    if not options and not default:
        mode = CORE_FACTION_MODE_NONE
    if mode == CORE_FACTION_MODE_NONE:
        options = []
        default = None

    return CoreFactionPolicy(mode=mode, default=default, options=options)


def selectable_core_factions(world: Any):
    from builders.models import FACTION_TYPE_CORE, Faction

    policy = core_faction_policy(world)
    if not policy.options:
        return Faction.objects.none()
    preserved_order = {code: index for index, code in enumerate(policy.options)}
    factions = list(
        Faction.objects
        .filter(world=authored_world(world), code__in=policy.options)
        .filter(faction_type_filter(FACTION_TYPE_CORE))
    )
    factions.sort(key=lambda faction: preserved_order.get(faction.code, len(preserved_order)))
    return factions


def resolve_player_creation_core_faction(world: Any, submitted_code: Any):
    from builders.models import FACTION_TYPE_CORE, Faction

    policy = core_faction_policy(world)
    submitted = str(submitted_code or "").strip()

    if policy.mode == CORE_FACTION_MODE_NONE:
        return None
    if policy.mode == CORE_FACTION_MODE_FIXED_DEFAULT:
        submitted = policy.default or ""
    elif not submitted:
        if policy.mode == CORE_FACTION_MODE_CHOOSE_OPTIONAL:
            return None
        submitted = policy.default or ""

    code = normalize_faction_code(submitted, field_name="faction")
    if code not in policy.options:
        raise serializers.ValidationError("Invalid core faction for this world.")

    faction = (
        Faction.objects
        .filter(world=authored_world(world), code=code)
        .filter(faction_type_filter(FACTION_TYPE_CORE))
        .first()
    )
    if faction is None:
        raise serializers.ValidationError("Invalid core faction for this world.")
    return faction
