from __future__ import annotations

import re
from typing import Callable

from django.db import transaction

from config import constants as adv_consts
from core.utils.items import type_to_slot
from quests.services.room_items import (
    QuestRoomItemProjection,
    claim_quest_room_item,
    quest_room_item_projections_for_room,
)
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.events import GameEvent
from spawns.models import Equipment, Item, Mob, Player
from spawns.state_payloads import (
    get_player_with_related,
    room_payload_key_for,
    resolve_item_name,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_item,
    serialize_room,
)
from spawns.text_output import render_event_text
from worlds.models import Room


_COUNTED_ITEM_RE = re.compile(r"^(?P<count>\d+)\.(?P<token>.+)$")


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _candidate_name(item: Item | QuestRoomItemProjection) -> str:
    if isinstance(item, Item):
        return resolve_item_name(item)
    return str(item.name or "").strip()


def _candidate_keywords(item: Item | QuestRoomItemProjection) -> str:
    keywords = str(getattr(item, "keywords", "") or "").strip()
    if keywords:
        return keywords
    if isinstance(item, Item) and item.template:
        keywords = str(item.template.keywords or "").strip()
        if keywords:
            return keywords
    return _candidate_name(item)


def _candidate_type(item: Item | QuestRoomItemProjection) -> str:
    if isinstance(item, Item):
        return item.type or (item.template.type if item.template else "")
    return str(item.type or "").strip()


def _candidate_equipment_type(item: Item | QuestRoomItemProjection) -> str:
    if isinstance(item, Item):
        return item.equipment_type or (item.template.equipment_type if item.template else "")
    return ""


def _item_tokens(item: Item | QuestRoomItemProjection) -> set[str]:
    keywords = _candidate_keywords(item)
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("item")
    item_type = _candidate_type(item)
    if item_type == adv_consts.ITEM_TYPE_CONTAINER:
        tokens.add("container")
    elif item_type == adv_consts.ITEM_TYPE_CORPSE:
        tokens.add("corpse")
    elif item_type == adv_consts.ITEM_TYPE_TRASH:
        tokens.add("trash")
    eq_type = _candidate_equipment_type(item)
    if eq_type:
        tokens.add(eq_type)
        if eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
            tokens.add("shield")
        elif eq_type.startswith("weapon"):
            tokens.add("weapon")
        elif eq_type in adv_consts.EQUIPMENT_ARMOR:
            tokens.add("armor")
    return tokens


def _item_matches(item: Item | QuestRoomItemProjection, token: str) -> bool:
    if not token:
        return False
    item_key = str(getattr(item, "key", "") or "").lower()
    if item_key and item_key == token:
        return True
    return token in _item_tokens(item)


def _is_container_item(item: Item) -> bool:
    return item.type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    )


def _select_items(
    items: list[Item | QuestRoomItemProjection],
    selector: str,
    *,
    empty_error: str,
    not_found_error: Callable[[str], str],
) -> list[Item | QuestRoomItemProjection]:
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


def _inventory_items(player: Player) -> list[Item]:
    return [
        item
        for item in _container_items(player)
        if item.type != adv_consts.ITEM_TYPE_CORPSE
    ]


def _select_equipment_candidates(player: Player, selector: str) -> list[Item]:
    inventory_items = _inventory_items(player)
    if selector and selector.strip().lower().startswith("all"):
        inventory_items = [
            item for item in inventory_items
            if _candidate_equipment_type(item)
        ]
        if not inventory_items:
            raise ActionError(
                "You don't seem to have anything that can be equipped.",
                code="nothing_equippable",
            )

    if not inventory_items:
        raise ActionError("You aren't carrying anything.", code="empty_inventory")

    return _select_items(
        inventory_items,
        selector,
        empty_error="Equip what?",
        not_found_error=lambda token: f"You don't seem to have a {token}.",
    )


def _equipment_items(player: Player) -> list[Item]:
    equipment = player.equipment
    if not equipment:
        return []
    items: list[Item] = []
    seen_ids: set[int] = set()
    for slot in adv_consts.EQUIPMENT_SLOTS:
        item = getattr(equipment, slot, None)
        if item and item.id not in seen_ids:
            items.append(item)
            seen_ids.add(item.id)
    return items


def _select_equipped_items(player: Player, selector: str) -> list[Item]:
    equipped_items = _equipment_items(player)
    if selector and selector.strip().lower() == "all" and not equipped_items:
        raise ActionError("You're not using anything.", code="empty_equipment")

    return _select_items(
        equipped_items,
        selector,
        empty_error="Remove what?",
        not_found_error=lambda token: f"You don't seem to be using a {token}.",
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


def _visible_room_items(
    player: Player,
    room: Room,
) -> list[Item | QuestRoomItemProjection]:
    room_items: list[Item | QuestRoomItemProjection] = [
        item for item in _room_items(room) if item.is_pickable
    ]
    room_items.extend(quest_room_item_projections_for_room(player, room.id))
    return room_items


def _room_backed_candidates(
    items: list[Item | QuestRoomItemProjection],
) -> list[Item]:
    return [item for item in items if isinstance(item, Item)]


def _quest_room_item_candidates(
    items: list[Item | QuestRoomItemProjection],
) -> list[QuestRoomItemProjection]:
    return [item for item in items if isinstance(item, QuestRoomItemProjection)]


def _is_bound_quest_item(item: Item) -> bool:
    return item.type == adv_consts.ITEM_TYPE_QUEST


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


def _find_equipment_slot(equipment, item: Item) -> str | None:
    for slot in adv_consts.EQUIPMENT_SLOTS:
        if getattr(equipment, f"{slot}_id", None) == item.id:
            return slot
        slot_item = getattr(equipment, slot, None)
        if slot_item and slot_item.id == item.id:
            return slot
    return None


def _is_equippable_for_player(player: Player, item: Item, *, wield_only: bool = False) -> bool:
    eq_type = _candidate_equipment_type(item)
    item_type = _candidate_type(item)
    if item_type != adv_consts.ITEM_TYPE_EQUIPPABLE or not eq_type:
        return False

    if wield_only and eq_type not in (
        adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
    ):
        return False

    if (
        eq_type == adv_consts.EQUIPMENT_TYPE_WEAPON_2H
        and player.archetype == adv_consts.ARCHETYPE_ASSASSIN
    ):
        return False

    if (
        eq_type in (*adv_consts.EQUIPMENT_ARMOR, adv_consts.EQUIPMENT_TYPE_SHIELD)
        and item.armor_class == adv_consts.ARMOR_CLASS_HEAVY
        and player.archetype != adv_consts.ARCHETYPE_WARRIOR
    ):
        return False

    slot = _resolve_equipment_slot(player, item)
    return slot in adv_consts.EQUIPMENT_SLOTS


def _resolve_equipment_slot(player: Player, item: Item) -> str | None:
    eq_type = _candidate_equipment_type(item)
    equipment = player.equipment
    if eq_type == adv_consts.EQUIPMENT_TYPE_WEAPON_1H:
        if not getattr(equipment, "weapon", None):
            return adv_consts.EQUIPMENT_SLOT_WEAPON
        if (
            not getattr(equipment, "offhand", None)
            and player.archetype == adv_consts.ARCHETYPE_ASSASSIN
        ):
            return adv_consts.EQUIPMENT_SLOT_OFFHAND
        return adv_consts.EQUIPMENT_SLOT_WEAPON

    return type_to_slot(
        eq_type=eq_type,
        has_weapon=bool(getattr(player.equipment, "weapon", None)),
        has_offhand=bool(getattr(player.equipment, "offhand", None)),
        archetype=player.archetype,
    )


class DropAction:
    def execute(self, player_id: int, selector: str) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot drop items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            items = _select_inventory_items(player, selector)
            for item in items:
                if _is_bound_quest_item(item):
                    raise ActionError(
                        "Quest items stay with you until you turn them in.",
                        code="quest_item_bound",
                    )
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


class EquipAction:
    def execute(
        self,
        player_id: int,
        selector: str,
        *,
        command_type: str = "equip",
        wield_only: bool = False,
    ) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot equip items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            equipment = Equipment.objects.select_for_update().get(pk=player.equipment_id)
            player.equipment = equipment
            selected_items = _select_equipment_candidates(player, selector)
            selected_ids = [item.id for item in selected_items]
            locked_items = {
                item.id: item
                for item in Item.objects.select_for_update()
                .filter(pk__in=selected_ids)
            }
            selected_items = [locked_items[item_id] for item_id in selected_ids if item_id in locked_items]

            equipped_items: list[Item] = []
            swapped_items: list[dict[str, Item]] = []
            unequippable_items: list[Item] = []
            removed_items: list[Item] = []
            touched_slots: set[str] = set()

            def unequip(slot: str, item: Item) -> None:
                setattr(equipment, slot, None)
                touched_slots.add(slot)
                item.container = player
                item.save(update_fields=["container_type", "container_id"])

            for item in selected_items:
                if not _is_equippable_for_player(player, item, wield_only=wield_only):
                    unequippable_items.append(item)
                    continue

                slot = _resolve_equipment_slot(player, item)
                if slot not in adv_consts.EQUIPMENT_SLOTS:
                    unequippable_items.append(item)
                    continue

                eq_type = _candidate_equipment_type(item)
                extra_conflicts: list[tuple[str, Item]] = []
                if eq_type == adv_consts.EQUIPMENT_TYPE_WEAPON_2H:
                    offhand = getattr(equipment, adv_consts.EQUIPMENT_SLOT_OFFHAND, None)
                    if offhand:
                        extra_conflicts.append((adv_consts.EQUIPMENT_SLOT_OFFHAND, offhand))
                elif eq_type == adv_consts.EQUIPMENT_TYPE_SHIELD:
                    weapon = getattr(equipment, adv_consts.EQUIPMENT_SLOT_WEAPON, None)
                    if (
                        weapon
                        and _candidate_equipment_type(weapon) == adv_consts.EQUIPMENT_TYPE_WEAPON_2H
                    ):
                        extra_conflicts.append((adv_consts.EQUIPMENT_SLOT_WEAPON, weapon))

                replacement = getattr(equipment, slot, None)
                if replacement and replacement.id == item.id:
                    continue

                primary_removed = replacement
                primary_slot = slot
                if primary_removed is None and extra_conflicts:
                    primary_slot, primary_removed = extra_conflicts.pop(0)

                if primary_removed:
                    unequip(primary_slot, primary_removed)
                    swapped_items.append({
                        "equipped": item,
                        "removed": primary_removed,
                    })

                for conflict_slot, conflict_item in extra_conflicts:
                    if conflict_item.id == getattr(primary_removed, "id", None):
                        continue
                    unequip(conflict_slot, conflict_item)
                    removed_items.append(conflict_item)

                setattr(equipment, slot, item)
                touched_slots.add(slot)
                item.container = equipment
                item.save(update_fields=["container_type", "container_id"])
                if not primary_removed:
                    equipped_items.append(item)

            if touched_slots:
                equipment.save(update_fields=sorted(touched_slots))

            if not (equipped_items or swapped_items or removed_items or unequippable_items):
                raise ActionError("You can't equip that.", code="not_equippable")

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )

        def item_payload(item: Item) -> dict:
            return serialize_item(item).model_dump()

        data = {
            "actor": actor_payload.model_dump(),
            "items": [item_payload(item) for item in equipped_items],
            "swapped_items": [
                {
                    "equipped": item_payload(swap["equipped"]),
                    "removed": item_payload(swap["removed"]),
                }
                for swap in swapped_items
            ],
            "unequippable_items": [item_payload(item) for item in unequippable_items],
            "removed_items": [item_payload(item) for item in removed_items],
            "room": room_payload.model_dump(),
        }
        event_type = f"cmd.{command_type}.success"
        text = render_event_text(event_type, data, viewer=updated_player)

        events = [
            GameEvent(
                type=event_type,
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible:
            recipients = (
                Player.objects.filter(room_id=room.id, in_game=True)
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                notify_data = {
                    "actor": serialize_char_from_player(updated_player).model_dump(),
                    "items": data["items"],
                    "swapped_items": data["swapped_items"],
                    "removed_items": data["removed_items"],
                }
                notify_type = f"notification.cmd.{command_type}.success"
                notify_text = render_event_text(notify_type, notify_data, viewer=None)
                events.append(
                    GameEvent(
                        type=notify_type,
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)


class RemoveEquipmentAction:
    def execute(self, player_id: int, selector: str, *, command_type: str = "remove") -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot remove equipment.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            equipment = Equipment.objects.select_for_update().get(pk=player.equipment_id)
            player.equipment = equipment
            selected_items = _select_equipped_items(player, selector)
            touched_slots: set[str] = set()
            removed_items: list[Item] = []

            for item in selected_items:
                slot = _find_equipment_slot(equipment, item)
                if not slot:
                    raise ActionError(
                        f"You don't seem to be using {resolve_item_name(item)}.",
                        code="item_not_found",
                    )
                setattr(equipment, slot, None)
                touched_slots.add(slot)
                item.container = player
                item.save(update_fields=["container_type", "container_id"])
                removed_items.append(item)

            if touched_slots:
                equipment.save(update_fields=sorted(touched_slots))

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [serialize_item(item).model_dump() for item in removed_items]
        data = {
            "actor": actor_payload.model_dump(),
            "items": item_payloads,
            "room": room_payload.model_dump(),
        }
        event_type = f"cmd.{command_type}.success"
        text = render_event_text(event_type, data, viewer=updated_player)

        events = [
            GameEvent(
                type=event_type,
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible:
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
                notify_type = f"notification.cmd.{command_type}.success"
                notify_text = render_event_text(notify_type, notify_data, viewer=None)
                events.append(
                    GameEvent(
                        type=notify_type,
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
                room_items = _visible_room_items(player, room)
                if not room_items:
                    raise ActionError("There is nothing here to take.", code="empty_room")
                selected_items = _select_items(
                    room_items,
                    selector,
                    empty_error="Get what?",
                    not_found_error=lambda token: f"You don't see a {token} here.",
                )

            room_backed_items = _room_backed_candidates(selected_items)
            quest_room_items = _quest_room_item_candidates(selected_items)

            item_ids = [item.id for item in room_backed_items]
            locked_items = {
                item.id: item
                for item in Item.objects.select_for_update()
                .filter(pk__in=item_ids)
            }
            moved_items = [locked_items[item_id] for item_id in item_ids if item_id in locked_items]

            for item in moved_items:
                item.container = player
                item.save(update_fields=["container_type", "container_id"])

            claimed_items: list[Item] = []
            for quest_room_item in quest_room_items:
                claimed_item = claim_quest_room_item(player, quest_room_item)
                if claimed_item is not None:
                    claimed_items.append(claimed_item)

            if not moved_items and not claimed_items:
                raise ActionError("You don't see that here.", code="item_not_found")

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [
            serialize_item(item).model_dump()
            for item in [*moved_items, *claimed_items]
        ]

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

        if (
            not updated_player.is_invisible
            and moved_items
            and _room_visibility_target(source_container, room)
        ):
            recipients = (
                Player.objects.filter(room_id=room.id, in_game=True)
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                moved_item_payloads = [serialize_item(item).model_dump() for item in moved_items]
                notify_data = {
                    "actor": serialize_char_from_player(updated_player).model_dump(),
                    "items": moved_item_payloads,
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
                if _is_bound_quest_item(item):
                    target_is_player_container = (
                        getattr(target_container, "container_type", None) is not None
                        and target_container.container_type.model == "player"
                        and target_container.container_id == player.id
                    )
                    if not target_is_player_container:
                        raise ActionError(
                            "Quest items can only be carried or turned in.",
                            code="quest_item_bound",
                        )
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


class GiveAction:
    def execute(self, player_id: int, selector: str, target_selector: str) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot give items.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            target_mob = resolve_room_mob_target(
                room,
                target_selector,
                empty_error="Give to whom?",
                not_found_error="You don't see them here.",
            )

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
                empty_error="Give what?",
                not_found_error=lambda token: f"You don't seem to have a {token}.",
            )
            if not selected_items:
                raise ActionError("Give what?", code="missing_item")

            item_ids = [item.id for item in selected_items]
            locked_items = {
                item.id: item
                for item in Item.objects.select_for_update().filter(pk__in=item_ids)
            }
            moved_items = [locked_items[item_id] for item_id in item_ids if item_id in locked_items]

            for item in moved_items:
                item.container = target_mob
                item.save(update_fields=["container_type", "container_id"])

        updated_player = get_player_with_related(player_id)
        refreshed_target_mob = Mob.objects.select_related("template").get(pk=target_mob.id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        item_payloads = [serialize_item(item).model_dump() for item in moved_items]
        target_payload = serialize_char_from_mob(refreshed_target_mob, viewer=updated_player).model_dump()

        data = {
            "actor": actor_payload.model_dump(),
            "items": item_payloads,
            "target": target_payload,
            "room": room_payload.model_dump(),
        }
        text = render_event_text("cmd.give.success", data, viewer=updated_player)

        events = [
            GameEvent(
                type="cmd.give.success",
                recipients=[updated_player.key],
                data=data,
                text=text,
            )
        ]

        if not updated_player.is_invisible:
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
                    "notification.cmd.give.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.give.success",
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        for item_payload in item_payloads:
            events.append(
                GameEvent(
                    type="quest.item.delivered",
                    recipients=[],
                    data={
                        "actor": actor_payload.model_dump(),
                        "item": item_payload,
                        "target": target_payload,
                        "room": room_payload.model_dump(),
                    },
                )
            )

        return ActionResult(events=events)
