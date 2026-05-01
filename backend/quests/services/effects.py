from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from builders.models import ItemTemplate
from core.scoped_state import (
    STATE_SCOPE_QUEST,
    clear_state_value,
    increment_state_value,
    normalize_state_scope,
    resolve_scope_owner,
    set_state_value,
)
from core.leveling import apply_experience
from core.utils import format_actor_msg
from quests.entity_refs import resolve_template_ref_id
from spawns.actions.base import ActionError
from spawns.actions.targeting import first_room_mob_with_template, resolve_room_mob_target
from spawns.models import Item, Mob
from quests.services.predicates import resolve_value


ALLOWED_MOB_COMMAND_TOKENS = {
    "say",
    "yell",
    "emote",
    "/echo",
    "/zecho",
    "/wecho",
}
GRANTED_ITEM_IDS_STATE_KEY = "granted_item_ids"


@dataclass
class QuestEffectResult:
    reward_summaries: list[str] = field(default_factory=list)


def _normalize_granted_item_ids(raw_ids: Any) -> list[int]:
    if not isinstance(raw_ids, list):
        return []
    granted_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if item_id > 0:
            granted_ids.append(item_id)
    return granted_ids


def granted_item_ids_for_instance(quest_instance) -> list[int]:
    local_state = dict(getattr(quest_instance, "local_state", {}) or {})
    return _normalize_granted_item_ids(local_state.get(GRANTED_ITEM_IDS_STATE_KEY) or [])


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


def _effect_world(*, player=None, template=None):
    return (
        getattr(template, "world", None)
        or getattr(getattr(player, "world", None), "context", None)
        or getattr(player, "world", None)
    )


def _collect_item_tree_ids(items: list[Item]) -> set[int]:
    item_ids: set[int] = set()
    for item in items:
        item_ids.add(item.id)
        item_ids.update(item.get_contained_ids())
    return item_ids


def _player_owned_item_ids(player) -> set[int]:
    top_level_items: list[Item] = list(player.inventory.all())
    if getattr(player, "equipment_id", None):
        top_level_items.extend(player.equipment.inventory.all())
    return _collect_item_tree_ids(top_level_items)


def _item_depth(item: Item) -> int:
    depth = 0
    seen_ids: set[int] = set()
    container = getattr(item, "container", None)
    while isinstance(container, Item) and container.id not in seen_ids:
        seen_ids.add(container.id)
        depth += 1
        container = getattr(container, "container", None)
    return depth


def _record_granted_item_ids(state: dict[str, Any], item_ids: set[int]) -> bool:
    if not item_ids:
        return False
    existing = set(_normalize_granted_item_ids(state.get(GRANTED_ITEM_IDS_STATE_KEY) or []))
    updated_ids = existing | {int(item_id) for item_id in item_ids if int(item_id) > 0}
    if updated_ids == existing:
        return False
    state[GRANTED_ITEM_IDS_STATE_KEY] = sorted(updated_ids)
    return True


def _resolve_effect_item_template(
    effect: dict[str, Any],
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> ItemTemplate | None:
    template_id = resolve_template_ref_id(
        world=_effect_world(player=player, template=template),
        value=resolve_value(
            effect.get("item_template", effect.get("item_template_id")),
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        ),
        expected_type="itemtemplate",
    )
    if not template_id:
        return None
    return ItemTemplate.objects.filter(pk=template_id).first()


def cleanup_player_owned_granted_items(quest_instance, *, player) -> int:
    granted_ids = set(granted_item_ids_for_instance(quest_instance))
    if not granted_ids:
        return 0

    removable_ids = granted_ids & _player_owned_item_ids(player)
    if not removable_ids:
        return 0

    removable_items = list(
        Item.objects.filter(pk__in=removable_ids).prefetch_related("inventory")
    )
    removed_count = 0

    for item in sorted(removable_items, key=_item_depth, reverse=True):
        parent_container = getattr(item, "container", None)
        for child in list(item.inventory.all()):
            if child.id in removable_ids:
                continue
            child.container = parent_container
            child.save(update_fields=["container_type", "container_id"])
        item.delete()
        removed_count += 1

    local_state = dict(getattr(quest_instance, "local_state", {}) or {})
    remaining_ids = sorted(granted_ids - removable_ids)
    if remaining_ids:
        local_state[GRANTED_ITEM_IDS_STATE_KEY] = remaining_ids
    else:
        local_state.pop(GRANTED_ITEM_IDS_STATE_KEY, None)
    quest_instance.local_state = local_state
    quest_instance.save(update_fields=["local_state", "modified_ts"])

    return removed_count


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


def _resolve_effect_mob(
    effect: dict[str, Any],
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> Mob | None:
    room = getattr(player, "room", None)
    if not room:
        return None

    mob_id = _parse_entity_id(effect.get("mob") or effect.get("issuer"), "mob")
    if mob_id:
        return room.mobs.select_related("template").filter(pk=mob_id).first()

    template_id = resolve_template_ref_id(
        world=getattr(template, "world", None) or getattr(getattr(player, "world", None), "context", None) or getattr(player, "world", None),
        value=resolve_value(
            effect.get("mob_template"),
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        ),
        expected_type="mobtemplate",
    )
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
    mob = _resolve_effect_mob(
        effect,
        player=player,
        template=template,
        quest_instance=quest_instance,
        event_data=event_data,
    )
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
        command_text = str(
            format_actor_msg(
                command_text,
                mob,
                character=player,
                quest_instance=quest_instance,
                extra_context={"event": event_data or {}},
            )
            or command_text
        ).strip()
        if not command_text:
            continue
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
    player_update_fields: set[str] = set()

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

        if effect_type == "set_state":
            key = str(effect.get("key") or "").strip()
            if not key:
                continue
            resolved_scope = str(effect.get("scope") or STATE_SCOPE_QUEST).strip().lower() or STATE_SCOPE_QUEST
            resolved_value = resolve_value(
                effect.get("value"),
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )
            if resolved_scope == STATE_SCOPE_QUEST:
                state[key] = resolved_value
                state_changed = True
                continue
            try:
                normalized_scope = normalize_state_scope(resolved_scope)
            except ValueError:
                continue
            owner = resolve_scope_owner(
                normalized_scope,
                actor=player,
                character=player,
                quest_instance=quest_instance,
            )
            if owner is not None:
                set_state_value(normalized_scope, owner, key, resolved_value)
            continue

        if effect_type == "increment_state":
            key = str(effect.get("key") or "").strip()
            if not key:
                continue
            resolved_scope = str(effect.get("scope") or STATE_SCOPE_QUEST).strip().lower() or STATE_SCOPE_QUEST
            amount = resolve_value(
                effect.get("amount", 1),
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )
            if resolved_scope == STATE_SCOPE_QUEST:
                try:
                    increment_amount = int(amount)
                except (TypeError, ValueError):
                    increment_amount = 1
                try:
                    state[key] = int(state.get(key, 0) or 0) + increment_amount
                except (TypeError, ValueError):
                    state[key] = increment_amount
                state_changed = True
                continue
            try:
                normalized_scope = normalize_state_scope(resolved_scope)
            except ValueError:
                continue
            owner = resolve_scope_owner(
                normalized_scope,
                actor=player,
                character=player,
                quest_instance=quest_instance,
            )
            if owner is not None:
                increment_state_value(normalized_scope, owner, key, amount)
            continue

        if effect_type == "clear_state":
            key = str(effect.get("key") or "").strip()
            if not key:
                continue
            resolved_scope = str(effect.get("scope") or STATE_SCOPE_QUEST).strip().lower() or STATE_SCOPE_QUEST
            if resolved_scope == STATE_SCOPE_QUEST:
                if key in state:
                    state.pop(key, None)
                    state_changed = True
                continue
            try:
                normalized_scope = normalize_state_scope(resolved_scope)
            except ValueError:
                continue
            owner = resolve_scope_owner(
                normalized_scope,
                actor=player,
                character=player,
                quest_instance=quest_instance,
            )
            if owner is not None:
                clear_state_value(normalized_scope, owner, key)
            continue

        if effect_type in {"grant_gold", "gold"} or "gold" in effect:
            amount = _coerce_amount(effect.get("amount", effect.get("gold")))
            if amount:
                player.gold = int(player.gold or 0) + amount
                player_update_fields.add("gold")
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
                leveling = apply_experience(player, amount)
                player_update_fields.add("experience")
                if leveling.leveled_up:
                    player_update_fields.add("level")
                result.reward_summaries.append(f"{amount} experience")
                if leveling.leveled_up:
                    result.reward_summaries.append(f"level {leveling.new_level}")
            continue

        if effect_type in {"grant_item", "spawn_item"}:
            item_template = _resolve_effect_item_template(
                effect,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )
            if not item_template or not player:
                continue
            count = _coerce_amount(
                effect.get(
                    "count",
                    effect.get(
                        "copies",
                        effect.get(
                            "quantity",
                            effect.get(
                                "num_copies",
                                effect.get("amount", 1),
                            ),
                        ),
                    ),
                )
            )
            if count <= 0:
                continue
            spawned_items: list[Item] = []
            for _ in range(count):
                spawned_items.append(item_template.spawn(player, player.world))
            if _record_granted_item_ids(state, _collect_item_tree_ids(spawned_items)):
                state_changed = True
            label = item_template.name or item_template.slug or "item"
            result.reward_summaries.append(
                label if count == 1 else f"{label} x{count}"
            )
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

    if player_update_fields:
        player.save(update_fields=sorted(player_update_fields))

    return result
