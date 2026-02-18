from __future__ import annotations

import re
from typing import Callable

from django.db import transaction

from config import constants as adv_consts
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.models import Item, Player
from spawns.state_payloads import (
    get_player_with_related,
    room_payload_key_for,
    resolve_item_name,
    serialize_actor,
    serialize_char_from_player,
    serialize_item,
    serialize_room,
)
from spawns.text_output import render_event_text
from worlds.models import Room


_COUNTED_ITEM_RE = re.compile(r"^(?P<count>\d+)\.(?P<token>.+)$")


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _item_tokens(item: Item) -> set[str]:
    keywords = item.keywords or ""
    if not keywords and item.template:
        keywords = item.template.keywords or ""
    if not keywords:
        keywords = resolve_item_name(item)
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("item")
    return tokens


def _item_matches(item: Item, token: str) -> bool:
    if not token:
        return False
    if item.key and item.key.lower() == token:
        return True
    return token in _item_tokens(item)


def _is_container_item(item: Item) -> bool:
    return item.type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    )


def _select_items(
    items: list[Item],
    selector: str,
    *,
    empty_error: str,
    not_found_error: Callable[[str], str],
) -> list[Item]:
    if not selector:
        raise ActionError(empty_error, code="missing_item")

    selector = selector.strip().lower()

    if selector == "all":
        if not items:
            raise ActionError(not_found_error(selector), code="item_not_found")
        return items

    if selector.startswith("all."):
        token = selector[4:]
        matches = [item for item in items if _item_matches(item, token)]
        if not matches:
            raise ActionError(not_found_error(token), code="item_not_found")
        return matches

    if selector.startswith("item."):
        matches = [item for item in items if item.key and item.key.lower() == selector]
        if not matches:
            raise ActionError(not_found_error(selector), code="item_not_found")
        return matches

    counted = _COUNTED_ITEM_RE.match(selector)
    if counted:
        token = counted.group("token")
        index = int(counted.group("count"))
        matches = [item for item in items if _item_matches(item, token)]
        if not matches or index < 1 or index > len(matches):
            raise ActionError(not_found_error(token), code="item_not_found")
        return [matches[index - 1]]

    matches = [item for item in items if _item_matches(item, selector)]
    if not matches:
        raise ActionError(not_found_error(selector), code="item_not_found")
    return [matches[0]]


def _select_inventory_items(player: Player, selector: str) -> list[Item]:
    inventory_qs = (
        player.inventory.filter(is_pending_deletion=False)
        .select_related("template", "currency")
        .order_by("id")
    )
    items = [
        item
        for item in inventory_qs
        if item.type != adv_consts.ITEM_TYPE_CORPSE
    ]

    if selector and selector.strip().lower() == "all" and not items:
        raise ActionError("You aren't carrying anything.", code="empty_inventory")

    return _select_items(
        items,
        selector,
        empty_error="Drop what?",
        not_found_error=lambda token: f"You don't seem to have a {token}.",
    )


def _container_items(container) -> list[Item]:
    return list(
        container.inventory.filter(is_pending_deletion=False)
        .select_related("template", "currency")
        .order_by("id")
    )


def _room_items(room: Room) -> list[Item]:
    return list(
        room.inventory.filter(is_pending_deletion=False)
        .select_related("template", "currency")
        .order_by("id")
    )


def _resolve_accessible_container(player: Player, room: Room, selector: str) -> Item:
    if not selector:
        raise ActionError("From where?", code="missing_container")

    selector = selector.strip().lower()
    if selector == "all" or selector.startswith("all."):
        raise ActionError("Specify a single container.", code="invalid_container")

    containers = [item for item in _room_items(room) if _is_container_item(item)]
    containers.extend(item for item in _container_items(player) if _is_container_item(item))

    if not containers:
        raise ActionError("You don't see any containers here.", code="no_containers")

    resolved = _select_items(
        containers,
        selector,
        empty_error="From where?",
        not_found_error=lambda token: f"You don't see a {token} here.",
    )
    return resolved[0]


def _room_visibility_target(item: Item | None, room: Room) -> bool:
    if item is None:
        return True
    if not item.container_type:
        return False
    return item.container_type.model == "room" and item.container_id == room.id


class DropAction:
    def execute(self, player_id: int, selector: str) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot drop items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            items = _select_inventory_items(player, selector)
            for item in items:
                item.container = room
                item.save(update_fields=["container_type", "container_id"])

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [serialize_item(item).model_dump() for item in items]

        data = {
            "actor": actor_payload.model_dump(),
            "items": item_payloads,
            "room": room_payload.model_dump(),
        }
        text = render_event_text("cmd.drop.success", data, viewer=updated_player)

        events = [
            GameEvent(
                type="cmd.drop.success",
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible:
            recipients = (
                Player.objects.filter(
                    room_id=room.id,
                    in_game=True,
                )
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                actor_char = serialize_char_from_player(updated_player).model_dump()
                notify_data = {
                    "actor": actor_char,
                    "items": item_payloads,
                }
                notify_text = render_event_text(
                    "notification.cmd.drop.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.drop.success",
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)


class GetAction:
    def execute(self, player_id: int, selector: str, source_selector: str | None = None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot get items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            source_container: Item | None = None

            if source_selector:
                source_container = _resolve_accessible_container(player, room, source_selector)
                source_items = [
                    item
                    for item in _container_items(source_container)
                    if item.is_pickable
                ]
                if not source_items:
                    raise ActionError("It is empty.", code="empty_container")
                selected_items = _select_items(
                    source_items,
                    selector,
                    empty_error="Get what?",
                    not_found_error=(
                        lambda token: f"You don't see a {token} in {source_container.name}."
                    ),
                )
            else:
                room_items = [item for item in _room_items(room) if item.is_pickable]
                if not room_items:
                    raise ActionError("There is nothing here to take.", code="empty_room")
                selected_items = _select_items(
                    room_items,
                    selector,
                    empty_error="Get what?",
                    not_found_error=lambda token: f"You don't see a {token} here.",
                )

            item_ids = [item.id for item in selected_items]
            locked_items = {
                item.id: item
                for item in Item.objects.select_for_update()
                .filter(pk__in=item_ids)
            }
            moved_items = [locked_items[item_id] for item_id in item_ids if item_id in locked_items]

            for item in moved_items:
                item.container = player
                item.save(update_fields=["container_type", "container_id"])

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [serialize_item(item).model_dump() for item in moved_items]

        data = {
            "actor": actor_payload.model_dump(),
            "items": item_payloads,
            "room": room_payload.model_dump(),
        }
        if source_container:
            data["source"] = serialize_item(source_container).model_dump()

        text = render_event_text("cmd.get.success", data, viewer=updated_player)

        events = [
            GameEvent(
                type="cmd.get.success",
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible and _room_visibility_target(source_container, room):
            recipients = (
                Player.objects.filter(room_id=room.id, in_game=True)
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                notify_data = {
                    "actor": serialize_char_from_player(updated_player).model_dump(),
                    "items": item_payloads,
                }
                if source_container:
                    notify_data["source"] = serialize_item(source_container).model_dump()

                notify_text = render_event_text(
                    "notification.cmd.get.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.get.success",
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)


class PutAction:
    def execute(self, player_id: int, selector: str, target_selector: str) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot put items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            target_container = _resolve_accessible_container(player, room, target_selector)

            inventory_items = [
                item
                for item in _container_items(player)
                if item.type != adv_consts.ITEM_TYPE_CORPSE
            ]
            if not inventory_items:
                raise ActionError("You aren't carrying anything.", code="empty_inventory")

            selected_items = _select_items(
                inventory_items,
                selector,
                empty_error="Put what?",
                not_found_error=lambda token: f"You don't seem to have a {token}.",
            )

            if any(item.id == target_container.id for item in selected_items):
                if len(selected_items) == 1:
                    raise ActionError("You cannot put an item inside itself.", code="invalid_target")
                selected_items = [item for item in selected_items if item.id != target_container.id]

            for item in selected_items:
                if item.type == adv_consts.ITEM_TYPE_CONTAINER:
                    contained_ids = item.get_contained_ids()
                    if target_container.id in contained_ids:
                        raise ActionError(
                            "You cannot place a container inside itself.",
                            code="invalid_target",
                        )

            if not selected_items:
                raise ActionError("Put what?", code="missing_item")

            item_ids = [item.id for item in selected_items]
            locked_items = {
                item.id: item
                for item in Item.objects.select_for_update()
                .filter(pk__in=item_ids)
            }
            moved_items = [locked_items[item_id] for item_id in item_ids if item_id in locked_items]

            for item in moved_items:
                item.container = target_container
                item.save(update_fields=["container_type", "container_id"])

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [serialize_item(item).model_dump() for item in moved_items]
        target_payload = serialize_item(target_container).model_dump()

        data = {
            "actor": actor_payload.model_dump(),
            "items": item_payloads,
            "target": target_payload,
            "room": room_payload.model_dump(),
        }
        text = render_event_text("cmd.put.success", data, viewer=updated_player)

        events = [
            GameEvent(
                type="cmd.put.success",
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible and _room_visibility_target(target_container, room):
            recipients = (
                Player.objects.filter(room_id=room.id, in_game=True)
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                notify_data = {
                    "actor": serialize_char_from_player(updated_player).model_dump(),
                    "items": item_payloads,
                    "target": target_payload,
                }
                notify_text = render_event_text(
                    "notification.cmd.put.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.put.success",
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)
