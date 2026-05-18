from __future__ import annotations

from config import constants as adv_consts
from spawns.models import Player


def _capfirst(value: str | None) -> str:
    if not value:
        return ""
    return value[0].upper() + value[1:]


def _room_brief_enabled(viewer: Player | None) -> bool:
    if not viewer or not getattr(viewer, "config", None):
        return False
    return bool(viewer.config.room_brief)


def _room_exit_line(room: dict) -> str:
    exits: list[str] = []
    for direction in adv_consts.DIRECTIONS:
        if room.get(direction):
            exits.append(direction[0].upper())
    return "[ exits: {} ]".format(" ".join(exits))


def _render_room_lines(
    room: dict,
    *,
    viewer: Player | None,
    show_description: bool,
) -> list[str]:
    lines: list[str] = []
    lines.append(room.get("name") or "Unknown Room")

    description = room.get("description") or ""
    if show_description and description:
        lines.append(description)

    lines.append(_room_exit_line(room))

    for item in room.get("inventory") or []:
        line = item.get("ground_description")
        if not line:
            item_name = item.get("name") or "item"
            line = "{} lies here.".format(_capfirst(item_name))
        indicator = str(item.get("indicator") or "").strip()
        if indicator:
            line = f"{line} [ {indicator} ]"
        lines.append(line)

    for char in room.get("chars") or []:
        if viewer and char.get("key") == viewer.key:
            continue
        line = char.get("room_description")
        if not line:
            char_name = char.get("name") or "someone"
            line = "{} is here.".format(_capfirst(char_name))
        if char.get("is_invisible"):
            line += " (invisible)"
        lines.append(line)

    for callout in room.get("quest_callouts") or []:
        callout_text = str(callout.get("text") or "").strip()
        if not callout_text:
            continue
        indicator = str(callout.get("indicator") or "!").strip() or "!"
        lines.append(f"{callout_text} [ {indicator} ]")

    actions = [action for action in (room.get("actions") or []) if action]
    if len(actions) == 1:
        lines.append("Action available: {}".format(actions[0]))
    elif len(actions) > 1:
        lines.append("Actions: {}".format(", ".join(actions)))

    return lines


def render_room_text(
    room: dict | None,
    *,
    viewer: Player | None,
    show_description: bool,
) -> str | None:
    if not room:
        return None
    lines = _render_room_lines(room, viewer=viewer, show_description=show_description)
    return "\n".join(lines) if lines else None


def _render_item_text(item: dict | None) -> str | None:
    if not item:
        return None
    lines: list[str] = []
    name = item.get("name") or "Item"
    lines.append(_capfirst(name))

    item_type = item.get("type") or ""
    description = item.get("description")
    if item_type not in ("container", "corpse") and description:
        lines.append(description)

    if item_type == "container":
        contents = [entry.get("name") for entry in (item.get("inventory") or []) if entry]
        if contents:
            lines.extend(contents)
        else:
            lines.append("Nothing.")

    return "\n".join(lines) if lines else None


def _render_char_text(char: dict | None) -> str | None:
    if not char:
        return None
    name = char.get("name")
    if not name:
        return None

    lines = [_capfirst(name)]
    description = char.get("description") or ""
    if description:
        lines.extend(line for line in str(description).splitlines() if line)

    equipment = char.get("equipment") or {}
    equipped_items = []
    for slot_name, item in equipment.items():
        if not item:
            continue
        item_name = item.get("name")
        if not item_name:
            continue
        equipped_items.append(f"{_capfirst(slot_name)}: {item_name}")

    if equipped_items:
        lines.append(f"{_capfirst(name)} is using:")
        lines.extend(equipped_items)

    return "\n".join(lines) if lines else None


def _render_room_detail_text(detail: object) -> str | None:
    if isinstance(detail, str):
        return detail
    return None


def _render_inventory_text(actor: dict | None) -> str | None:
    if not actor:
        return None
    items = actor.get("inventory") or []
    lines = [item.get("name") for item in items if item.get("name")]
    if lines:
        return "You are carrying:\n" + "\n".join(lines)
    return "You are carrying:\nNothing."


def _render_stats_text(data: dict) -> str | None:
    actor = data.get("actor") or {}
    if not actor.get("key"):
        return None
    return "You review your stats."


def _render_drop_text(event_type: str, data: dict) -> str | None:
    items = data.get("items") or []
    if not items:
        return None
    if event_type == "cmd.drop.success":
        prefix = "You drop "
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} drops "
    lines = []
    for item in items:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}{name}.")
    return "\n".join(lines) if lines else None


def _render_get_text(event_type: str, data: dict) -> str | None:
    items = data.get("items") or []
    if not items:
        return None

    source = data.get("source") or {}
    source_name = source.get("name")

    if event_type == "cmd.get.success":
        prefix = "You get "
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} gets "

    suffix = f" from {source_name}" if source_name else ""
    lines = []
    for item in items:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}{name}{suffix}.")
    return "\n".join(lines) if lines else None


def _render_put_text(event_type: str, data: dict) -> str | None:
    items = data.get("items") or []
    if not items:
        return None

    target = data.get("target") or {}
    target_name = target.get("name")
    if not target_name:
        return None

    if event_type == "cmd.put.success":
        prefix = "You put "
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} puts "

    lines = []
    for item in items:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}{name} in {target_name}.")
    return "\n".join(lines) if lines else None


def _render_give_text(event_type: str, data: dict) -> str | None:
    items = data.get("items") or []
    if not items:
        return None

    target = data.get("target") or {}
    target_name = target.get("name")
    if not target_name:
        return None

    if event_type == "cmd.give.success":
        prefix = "You give "
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} gives "

    lines = []
    for item in items:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}{name} to {target_name}.")
    return "\n".join(lines) if lines else None


def _equipment_action_line(prefix: str, item: dict, *, third_person: bool = False) -> str | None:
    name = item.get("name")
    if not name:
        return None

    eq_type = item.get("equipment_type") or ""
    if eq_type.startswith("weapon"):
        return f"{prefix}wields {name}." if third_person else f"{prefix}wield {name}."
    if eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
        if third_person:
            return f"{prefix}wears {name}."
        return f"{prefix}strap {name} on your arm."
    if eq_type == adv_consts.EQUIPMENT_TYPE_ACCESSORY:
        return f"{prefix}puts on {name}." if third_person else f"{prefix}put on {name}."
    if eq_type:
        suffix = f"their {eq_type}" if third_person else f"your {eq_type}"
        return f"{prefix}wears {name} on {suffix}." if third_person else f"{prefix}wear {name} on {suffix}."
    return f"{prefix}equips {name}." if third_person else f"{prefix}equip {name}."


def _render_equip_text(event_type: str, data: dict) -> str | None:
    is_self = event_type.startswith("cmd.")
    if is_self:
        prefix = "You "
        third_person = False
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} "
        third_person = True

    lines: list[str] = []
    for item in data.get("items") or []:
        line = _equipment_action_line(prefix, item, third_person=third_person)
        if line:
            lines.append(line)

    for swap in data.get("swapped_items") or []:
        removed = (swap.get("removed") or {}).get("name")
        equipped = (swap.get("equipped") or {}).get("name")
        if removed and equipped:
            lines.append(f"{prefix}swap {removed} for {equipped}." if is_self else f"{prefix}swaps {removed} for {equipped}.")

    if is_self:
        verb = "wield" if event_type == "cmd.wield.success" else "wear"
        for item in data.get("unequippable_items") or []:
            name = item.get("name")
            if name:
                lines.append(f"You can't {verb} {name}.")

    for item in data.get("removed_items") or []:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}remove {name}." if is_self else f"{prefix}removes {name}.")

    return "\n".join(lines) if lines else None


def _render_remove_text(event_type: str, data: dict) -> str | None:
    items = data.get("items") or []
    if not items:
        return None
    if event_type == "cmd.remove.success":
        prefix = "You stop using "
    else:
        actor = data.get("actor") or {}
        actor_name = _capfirst(actor.get("name"))
        if not actor_name:
            return None
        prefix = f"{actor_name} stops using "

    lines = []
    for item in items:
        name = item.get("name")
        if name:
            lines.append(f"{prefix}{name}.")
    return "\n".join(lines) if lines else None


def _render_inspect_text(data: dict) -> str | None:
    target_type = str(data.get("target_type") or "").strip().lower()
    if target_type == "room":
        return "You inspect the room."
    return None


def _render_roll_text(event_type: str, data: dict) -> str | None:
    die = data.get("die")
    outcome = data.get("outcome")
    if not die or outcome is None:
        return None

    if event_type == "cmd.roll.success":
        return f"You roll {die}: {outcome}"

    actor = data.get("actor") or {}
    actor_name = _capfirst(actor.get("name"))
    if not actor_name:
        return None
    return f"{actor_name} rolls {die}: {outcome}"


def _render_say_text(event_type: str, data: dict) -> str | None:
    text = data.get("text")
    if not text:
        return None
    if event_type == "cmd.say.success":
        return f"You say '{text}'"
    if event_type == "cmd.yell.success":
        return f"You yell '{text}'"

    actor = data.get("actor") or {}
    actor_name = _capfirst(actor.get("name"))
    if not actor_name:
        return None
    if event_type == "notification.cmd.yell.success":
        return f"{actor_name} yells '{text}'"
    return f"{actor_name} says '{text}'"


def _render_emote_text(data: dict) -> str | None:
    text = data.get("text")
    if not text:
        return None
    actor = data.get("actor") or {}
    actor_name = _capfirst(actor.get("name"))
    if not actor_name:
        return None
    return f"{actor_name} {text}"


def _render_talk_text(data: dict) -> str | None:
    target = data.get("target") or {}
    target_name = _capfirst(target.get("name"))
    if not target_name:
        return None
    return f"You talk to {target_name}."


def _render_kill_text(event_type: str, data: dict) -> str | None:
    target = data.get("target") or {}
    target_name = _capfirst(target.get("name"))
    if not target_name:
        return None
    if event_type == "cmd.kill.success":
        return f"You kill {target_name}."
    actor = data.get("actor") or {}
    actor_name = _capfirst(actor.get("name"))
    if not actor_name:
        return None
    return f"{actor_name} kills {target_name}."


def _render_notification_text(event_type: str, data: dict) -> str | None:
    actor = data.get("actor") or {}
    actor_name = _capfirst(actor.get("name"))
    direction = data.get("direction") or ""
    if event_type == "notification.movement.exit":
        if actor_name and direction:
            return "{} leaves {}.".format(actor_name, direction)
    if event_type == "notification.movement.enter":
        if actor_name and direction:
            if direction == "up":
                from_text = "above"
            elif direction == "down":
                from_text = "below"
            else:
                from_text = "the {}".format(direction)
            return "{} has arrived from {}.".format(actor_name, from_text)
    return None


def _should_show_description(event_type: str, viewer: Player | None) -> bool:
    if event_type in ("cmd.look.success", "cmd.state.sync.success"):
        return True
    if event_type == "cmd.move.success":
        return not _room_brief_enabled(viewer)
    return True


def render_event_text(
    event_type: str,
    data: dict,
    *,
    viewer: Player | None = None,
) -> str | None:
    if event_type == "cmd.look.success":
        target_type = data.get("target_type")
        target = data.get("target")
        if target_type == "room":
            return render_room_text(
                target,
                viewer=viewer,
                show_description=_should_show_description(event_type, viewer),
            )
        if target_type == "item":
            return _render_item_text(target)
        if target_type == "char":
            return _render_char_text(target)
        if target_type == "room_detail":
            return _render_room_detail_text(target)
        return None

    if event_type in ("cmd.move.success", "cmd.state.sync.success"):
        room = data.get("room") or data.get("target")
        return render_room_text(
            room,
            viewer=viewer,
            show_description=_should_show_description(event_type, viewer),
        )

    if event_type == "cmd.inventory.success":
        return _render_inventory_text(data.get("actor"))

    if event_type == "cmd.stats.success":
        return _render_stats_text(data)

    if event_type in ("cmd.roll.success", "notification.cmd.roll.success"):
        return _render_roll_text(event_type, data)

    if event_type in (
        "cmd.say.success",
        "notification.cmd.say.success",
        "cmd.yell.success",
        "notification.cmd.yell.success",
    ):
        return _render_say_text(event_type, data)

    if event_type in ("cmd.emote.success", "notification.cmd.emote.success"):
        return _render_emote_text(data)

    if event_type.startswith("notification.movement."):
        return _render_notification_text(event_type, data)

    if event_type in ("cmd.drop.success", "notification.cmd.drop.success"):
        return _render_drop_text(event_type, data)

    if event_type in ("cmd.get.success", "notification.cmd.get.success"):
        return _render_get_text(event_type, data)

    if event_type in ("cmd.put.success", "notification.cmd.put.success"):
        return _render_put_text(event_type, data)

    if event_type in ("cmd.give.success", "notification.cmd.give.success"):
        return _render_give_text(event_type, data)

    if event_type in (
        "cmd.equip.success",
        "notification.cmd.equip.success",
        "cmd.wear.success",
        "notification.cmd.wear.success",
        "cmd.wield.success",
        "notification.cmd.wield.success",
    ):
        return _render_equip_text(event_type, data)

    if event_type in ("cmd.remove.success", "notification.cmd.remove.success"):
        return _render_remove_text(event_type, data)

    if event_type == "cmd.talk.success":
        return _render_talk_text(data)

    if event_type == "cmd.inspect.success":
        return _render_inspect_text(data)

    if event_type in ("cmd.kill.success", "notification.cmd.kill.success"):
        return _render_kill_text(event_type, data)

    return None
