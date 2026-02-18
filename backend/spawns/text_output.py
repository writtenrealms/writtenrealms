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
    return _capfirst(name) if name else None


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

    return None
