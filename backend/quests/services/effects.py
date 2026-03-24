from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from spawns.actions.base import ActionError
from spawns.actions.targeting import first_room_mob_with_template, resolve_room_mob_target
from spawns.models import Mob
from quests.services.predicates import resolve_value


ALLOWED_MOB_COMMAND_TOKENS = {
    "say",
    "yell",
    "emote",
    "/echo",
    "/zecho",
    "/wecho",
}


@dataclass
class QuestEffectResult:
    reward_summaries: list[str] = field(default_factory=list)


def _parse_entity_id(value: Any, expected_prefix: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    prefix, sep, raw_id = text.partition(".")
    if sep != "." or prefix != expected_prefix or not raw_id.isdigit():
        return None
    return int(raw_id)


def _coerce_amount(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _command_list(effect: dict[str, Any], *, player=None, template=None, quest_instance=None, event_data=None) -> list[str]:
    commands: list[str] = []
    raw_commands = effect.get("commands")
    if isinstance(raw_commands, str):
        raw_commands = [raw_commands]
    if isinstance(raw_commands, (list, tuple)):
        for raw_command in raw_commands:
            resolved = resolve_value(
                raw_command,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )
            if isinstance(resolved, str) and resolved.strip():
                commands.append(resolved.strip())

    raw_command = effect.get("command")
    resolved_command = resolve_value(
        raw_command,
        player=player,
        template=template,
        quest_instance=quest_instance,
        event_data=event_data,
    )
    if isinstance(resolved_command, str) and resolved_command.strip():
        commands.append(resolved_command.strip())
    return commands


def _resolve_effect_mob(effect: dict[str, Any], *, player=None, event_data: dict[str, Any] | None = None) -> Mob | None:
    room = getattr(player, "room", None)
    if not room:
        return None

    mob_id = _parse_entity_id(effect.get("mob") or effect.get("issuer"), "mob")
    if mob_id:
        return room.mobs.select_related("template").filter(pk=mob_id).first()

    template_id = _parse_entity_id(effect.get("mob_template"), "mobtemplate")
    if template_id:
        return first_room_mob_with_template(room, template_id)

    selector = effect.get("selector")
    if selector:
        try:
            return resolve_room_mob_target(
                room,
                selector,
                empty_error="",
                not_found_error="",
            )
        except ActionError:
            return None

    target = event_data.get("target") if isinstance(event_data, dict) else None
    if isinstance(target, dict):
        event_mob_id = _parse_entity_id(target.get("key") or target.get("id"), "mob")
        if event_mob_id:
            mob = room.mobs.select_related("template").filter(pk=event_mob_id).first()
            if mob:
                return mob
        event_template_id = _coerce_amount(target.get("template_id"))
        if event_template_id:
            mob = first_room_mob_with_template(room, event_template_id)
            if mob:
                return mob

    return None


def _run_allowed_mob_commands(
    effect: dict[str, Any],
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> None:
    mob = _resolve_effect_mob(effect, player=player, event_data=event_data)
    if not mob:
        return
    dispatch_command = import_module("spawns.handlers.registry").dispatch_command

    for command_text in _command_list(
        effect,
        player=player,
        template=template,
        quest_instance=quest_instance,
        event_data=event_data,
    ):
        command_token = command_text.split()[0].lower()
        if command_token not in ALLOWED_MOB_COMMAND_TOKENS:
            continue
        dispatch_command(
            command_type="text",
            actor_type="mob",
            actor_id=mob.id,
            payload={"text": command_text},
        )


def apply_quest_effects(
    quest_instance,
    effects: list[dict[str, Any]] | None,
    *,
    player=None,
    template=None,
    event_data: dict[str, Any] | None = None,
) -> QuestEffectResult:
    result = QuestEffectResult()
    if not effects:
        return result

    state = dict(quest_instance.local_state or {})
    state_changed = False
    player_changed = False

    for effect in effects:
        if not isinstance(effect, dict):
            continue

        effect_type = str(effect.get("type") or "").strip().lower()

        if effect_type == "set_local" or "set_local" in effect:
            raw_args = effect.get("set_local") if "set_local" in effect else None
            if isinstance(raw_args, (list, tuple)) and len(raw_args) == 2:
                key = str(raw_args[0] or "").strip()
                value = raw_args[1]
            else:
                key = str(effect.get("key") or "").strip()
                value = effect.get("value")
            if key:
                state[key] = resolve_value(
                    value,
                    player=player,
                    template=template,
                    quest_instance=quest_instance,
                    event_data=event_data,
                )
                state_changed = True
            continue

        if effect_type in {"grant_gold", "gold"} or "gold" in effect:
            amount = _coerce_amount(effect.get("amount", effect.get("gold")))
            if amount:
                player.gold = int(player.gold or 0) + amount
                player_changed = True
                result.reward_summaries.append(f"{amount} gold")
            continue

        if effect_type in {"grant_xp", "grant_exp", "grant_experience", "xp", "exp", "experience"} or any(
            key in effect for key in ("xp", "exp", "experience")
        ):
            amount = _coerce_amount(
                effect.get(
                    "amount",
                    effect.get("xp", effect.get("exp", effect.get("experience"))),
                )
            )
            if amount:
                player.experience = int(player.experience or 0) + amount
                player_changed = True
                result.reward_summaries.append(f"{amount} experience")
            continue

        if effect_type in {"mob_command", "run_mob_command"} or "mob_command" in effect:
            if "mob_command" in effect and "command" not in effect and "commands" not in effect:
                raw_command = effect.get("mob_command")
                if isinstance(raw_command, str):
                    effect = dict(effect)
                    effect["command"] = raw_command
            _run_allowed_mob_commands(
                effect,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )

    if state_changed:
        quest_instance.local_state = state
        quest_instance.save(update_fields=["local_state", "modified_ts"])

    if player_changed:
        player.save(update_fields=["gold", "experience"])

    return result
