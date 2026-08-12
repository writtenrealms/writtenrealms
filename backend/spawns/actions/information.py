from __future__ import annotations

from datetime import timedelta

from config import constants as adv_consts
from core.utils import roll_die
from django.utils import timezone
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import find_accessible_item_target, find_room_char_target
from spawns.events import GameEvent
from spawns.models import CombatEncounter, Mob, Player
from spawns.state_payloads import (
    build_map_payload,
    collect_map_room_ids,
    directional_door_payload,
    door_state_lookup,
    get_player_with_related,
    is_player_visible_on_who_list,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_item,
    serialize_room,
    serialize_world,
)
from quests.services.interactions import room_mob_quest_indicator_map
from quests.services.room_items import find_quest_room_item_target
from spawns.text_output import render_event_text
from worlds.room_refs import format_room_manifest_ref


_LOOK_DIRECTION_ALIASES = {
    direction: direction
    for direction in adv_consts.DIRECTIONS
}
_LOOK_DIRECTION_ALIASES.update(
    {
        direction[0]: direction
        for direction in adv_consts.DIRECTIONS
    }
)


def _look_direction(target_selector: str) -> str | None:
    return _LOOK_DIRECTION_ALIASES.get(target_selector.strip().lower())


def _normalize_roll_target(target: str | None) -> str:
    normalized_target = str(target).strip() if target is not None else "6"
    if not normalized_target:
        normalized_target = "6"
    if "d" not in normalized_target:
        return f"1d{normalized_target}"
    return normalized_target


class LookAction:
    def execute(
        self,
        player_id: int,
        target_selector: str | None = None,
        *,
        isolate_runtime_world: bool = False,
    ) -> ActionResult:
        player = get_player_with_related(player_id)
        world = player.world
        room = player.room

        if room is None:
            raise ActionError("You are nowhere. Cannot look around.", code="no_room")

        actor_payload = serialize_actor(player, room)
        normalized_target = str(target_selector or "").strip()

        if normalized_target:
            direction = _look_direction(normalized_target)
            if direction:
                door_target = directional_door_payload(
                    world,
                    room.id,
                    direction,
                )
                if door_target is not None:
                    data = {
                        "actor": actor_payload.model_dump(),
                        "target": door_target,
                        "target_type": "door",
                    }
                    text = render_event_text(
                        "cmd.look.success",
                        data,
                        viewer=player,
                    )
                    return ActionResult(
                        events=[
                            GameEvent(
                                type="cmd.look.success",
                                recipients=[player.key],
                                data=data,
                                text=text,
                            )
                        ]
                    )

            char_target = find_room_char_target(
                room,
                normalized_target,
                viewer=player,
                world=world,
            )
            if char_target is not None:
                target_payload = self._serialize_char_target(player, char_target)
                data = {
                    "actor": actor_payload.model_dump(),
                    "target": target_payload.model_dump(),
                    "target_type": "char",
                }
                text = render_event_text("cmd.look.success", data, viewer=player)
                return ActionResult(
                    events=[
                        GameEvent(
                            type="cmd.look.success",
                            recipients=[player.key],
                            data=data,
                            text=text,
                        )
                    ]
                )

            item_target = find_accessible_item_target(player, room, normalized_target)
            if item_target is not None:
                target_payload = serialize_item(
                    item_target,
                    viewer=player,
                    include_inventory=True,
                )
                data = {
                    "actor": actor_payload.model_dump(),
                    "target": target_payload.model_dump(),
                    "target_type": "item",
                }
                text = render_event_text("cmd.look.success", data, viewer=player)
                return ActionResult(
                    events=[
                        GameEvent(
                            type="cmd.look.success",
                            recipients=[player.key],
                            data=data,
                            text=text,
                        )
                    ]
                )

            quest_room_item_target = find_quest_room_item_target(
                player,
                room.id,
                normalized_target,
            )
            if quest_room_item_target is not None:
                data = {
                    "actor": actor_payload.model_dump(),
                    "target": quest_room_item_target.to_item_payload(),
                    "target_type": "item",
                }
                text = render_event_text("cmd.look.success", data, viewer=player)
                return ActionResult(
                    events=[
                        GameEvent(
                            type="cmd.look.success",
                            recipients=[player.key],
                            data=data,
                            text=text,
                        )
                    ]
                )

            raise ActionError("You don't see that here.", code="target_not_found")

        room_world = room.world or (world.context or world)
        room_ids, _ = collect_map_room_ids(player, room_world, room)
        door_states = door_state_lookup(world, room_ids)
        map_rooms, room_key_lookup = build_map_payload(room_world, room_ids, door_states)

        room_payload = serialize_room(
            room,
            room_key_lookup,
            door_states,
            viewer=player,
            runtime_world=world,
        )
        data = {
            "actor": actor_payload.model_dump(),
            "target": room_payload.model_dump(),
            "target_type": "room",
            "map": [mr.model_dump() for mr in map_rooms],
        }
        text = render_event_text("cmd.look.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.look.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )

    def _serialize_char_target(self, viewer: Player, target: Player | Mob):
        if isinstance(target, Player):
            return serialize_char_from_player(
                target,
                viewer=viewer,
                include_equipment=True,
            )

        quest_indicator_map = room_mob_quest_indicator_map(viewer, [target])
        return serialize_char_from_mob(
            target,
            viewer=viewer,
            quest_indicator_map=quest_indicator_map,
            include_equipment=True,
        )


class InspectAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        world = player.world
        room = player.room

        if room is None:
            raise ActionError("You are nowhere. Cannot inspect anything.", code="no_room")

        actor_payload = serialize_actor(player, room)
        room_world = room.world or (world.context or world)
        room_ids, _ = collect_map_room_ids(player, room_world, room)
        door_states = door_state_lookup(world, room_ids)
        _, room_key_lookup = build_map_payload(room_world, room_ids, door_states)
        room_payload = serialize_room(
            room,
            room_key_lookup,
            door_states,
            viewer=player,
            runtime_world=world,
        )
        data = {
            "actor": actor_payload.model_dump(),
            "target": room_payload.model_dump(),
            "target_type": "room",
        }
        text = render_event_text("cmd.inspect.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.inspect.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


def _resolve_scan_direction(direction: str | dict | None) -> str:
    if isinstance(direction, dict):
        direction = direction.get("name") or direction.get("direction")
    normalized_direction = str(direction or "").strip().lower()
    if not normalized_direction:
        raise ActionError("Scan in which direction?", code="missing_direction")

    for available_direction in adv_consts.DIRECTIONS:
        if available_direction.startswith(normalized_direction):
            return available_direction

    cap_direction = (
        normalized_direction[0].upper() + normalized_direction[1:]
        if normalized_direction
        else normalized_direction
    )
    raise ActionError(
        f"{cap_direction} is not a valid direction.",
        code="invalid_direction",
    )


class ScanAction:
    def execute(
        self,
        player_id: int,
        direction: str | dict | None = None,
    ) -> ActionResult:
        player = get_player_with_related(player_id)
        room = player.room

        if room is None:
            raise ActionError("You are nowhere. Cannot scan.", code="no_room")
        resolved_direction = _resolve_scan_direction(direction)
        if room.type in adv_consts.UNSCANNABLE_ROOM_TYPES:
            raise ActionError(
                f"Cannot scan in {room.type}s.",
                code="unscannable_room",
                data={"room_type": room.type},
            )

        exit_room = getattr(room, resolved_direction, None)
        if exit_room is None:
            raise ActionError(
                f"There is no exit {resolved_direction}.",
                code="no_exit",
                data={"direction": resolved_direction},
            )

        actor_payload = serialize_actor(player, room)
        target_lookup = self._active_target_lookup(
            exit_room,
            runtime_world=player.world,
        )
        chars = [
            self._serialize_scan_char(player, char, target_lookup)
            for char in self._visible_exit_room_chars(
                exit_room,
                runtime_world=player.world,
            )
            if char.key != player.key
        ]
        data = {
            "actor": actor_payload.model_dump(),
            "direction": resolved_direction,
            "chars": chars,
        }
        text = render_event_text("cmd.scan.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.scan.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )

    def _visible_exit_room_chars(
        self,
        exit_room,
        *,
        runtime_world,
    ) -> list[Player | Mob]:
        room_players = list(
            exit_room.players.filter(
                world=runtime_world,
                in_game=True,
            )
            .select_related("user", "equipment")
            .prefetch_related("faction_assignments__faction", "clan_memberships__clan")
        )
        room_mobs = list(
            exit_room.mobs.filter(
                world=runtime_world,
                is_pending_deletion=False,
            )
            .select_related("definition", "equipment")
            .prefetch_related("faction_assignments__faction")
        )
        chars: list[Player | Mob] = [*room_players, *room_mobs]
        chars.sort(
            key=lambda char: (
                getattr(char, "created_ts", None),
                getattr(char, "id", 0),
            ),
            reverse=True,
        )

        return [
            char for char in chars
            if not getattr(char, "is_invisible", False)
            and not getattr(char, "sneak_ts", None)
        ]

    def _active_target_lookup(
        self,
        exit_room,
        *,
        runtime_world,
    ) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        encounters = (
            CombatEncounter.objects.filter(
                world=runtime_world,
                room=exit_room,
                status=CombatEncounter.STATUS_ACTIVE,
                player__room=exit_room,
                player__in_game=True,
                mob__room=exit_room,
                mob__is_pending_deletion=False,
                mob__health__gt=0,
            )
            .select_related("player", "mob")
            .order_by("id")
        )
        for encounter in encounters:
            if not encounter.player or not encounter.mob:
                continue
            lookup.setdefault(
                encounter.player.key,
                self._target_payload(encounter.mob),
            )
            lookup.setdefault(
                encounter.mob.key,
                self._target_payload(encounter.player),
            )
        return lookup

    def _target_payload(self, char: Player | Mob) -> dict:
        if isinstance(char, Player):
            keywords = (
                getattr(char, "keywords", "")
                or f"{char.name.lower()} player {char.key}"
            )
        else:
            keywords = char.keywords or ""
            if not keywords and char.definition:
                keywords = char.definition.keywords or ""
            if not keywords:
                keywords = char.name or ""

        return {
            "id": char.id,
            "key": char.key,
            "name": char.name,
            "health": getattr(char, "health", 0),
            "health_max": getattr(char, "health_max", getattr(char, "health", 0)),
            "level": getattr(char, "level", 1),
            "keywords": keywords,
        }

    def _serialize_scan_char(
        self,
        viewer: Player,
        char: Player | Mob,
        target_lookup: dict[str, dict],
    ) -> dict:
        if isinstance(char, Player):
            payload = serialize_char_from_player(char, viewer=viewer).model_dump()
        else:
            payload = serialize_char_from_mob(char, viewer=viewer).model_dump()
        payload["target"] = target_lookup.get(char.key)
        return payload


class InventoryAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        actor_payload = serialize_actor(player, player.room)
        data = {"actor": actor_payload.model_dump()}
        text = render_event_text("cmd.inventory.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.inventory.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class EquipmentAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        actor_payload = serialize_actor(player, player.room)
        actor_data = actor_payload.model_dump()
        data = {
            "actor": actor_data,
            "equipment": actor_data.get("equipment") or {},
        }
        text = render_event_text("cmd.equipment.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.equipment.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class StatsAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        actor_payload = serialize_actor(player, player.room)
        world_payload = serialize_world(player.world)
        data = {
            "actor": actor_payload.model_dump(),
            "world": world_payload,
        }
        text = render_event_text("cmd.stats.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.stats.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class WhoAction:
    def execute(self, player_id: int) -> ActionResult:
        actor = get_player_with_related(player_id)
        players = self._players_for_actor(actor)
        data = {
            "players": [self._serialize_player(actor, player) for player in players],
            "grapevine": {},
        }
        text = render_event_text("cmd.who.success", data, viewer=actor)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.who.success",
                    recipients=[actor.key],
                    data=data,
                    text=text,
                )
            ]
        )

    def _players_for_actor(self, actor: Player) -> list[Player]:
        qs = (
            Player.objects.filter(world=actor.world, in_game=True)
            .select_related("user", "room", "core_faction")
            .prefetch_related("faction_assignments__faction", "clan_memberships__clan")
            .order_by("id")
        )
        return [
            player
            for player in qs
            if is_player_visible_on_who_list(actor, player)
        ]

    def _serialize_player(self, actor: Player, player: Player) -> dict:
        idle_cutoff = timezone.now() - timedelta(seconds=adv_consts.IDLE_THRESHOLD)
        player_data = {
            "id": player.id,
            "key": player.key,
            "name": player.name,
            "title": player.title,
            "level": player.level,
            "gender": player.gender or "male",
            "core_faction": (player.factions or {}).get("core"),
            "display_faction": player.display_faction or None,
            "is_builder": player.is_builder,
            "is_immortal": player.is_builder,
            "is_invisible": player.is_invisible,
            "is_idle": (
                not player.last_action_ts
                or player.last_action_ts <= idle_cutoff
            ),
            "is_linkless": False,
            "name_recognition": bool(getattr(player.user, "name_recognition", False)),
            "clan": player.clan,
        }
        if bool(getattr(actor.user, "is_staff", False)):
            player_data["link_id"] = player.user.link_id
        if actor.is_builder:
            player_data["room_manifest_ref"] = (
                format_room_manifest_ref(player.room)
                if player.room is not None
                else None
            )
        return player_data


class RollAction:
    def execute(self, player_id: int, target: str | None = None) -> ActionResult:
        player = get_player_with_related(player_id)
        die = _normalize_roll_target(target)
        outcome = roll_die(die)

        data = {
            "die": die,
            "outcome": outcome,
        }

        cmd_text = render_event_text("cmd.roll.success", data, viewer=player)
        events = [
            GameEvent(
                type="cmd.roll.success",
                recipients=[player.key],
                data=data,
                text=cmd_text,
            )
        ]

        if player.room_id and not player.is_invisible:
            recipient_ids = list(
                Player.objects.filter(
                    world=player.world,
                    room_id=player.room_id,
                    in_game=True,
                )
                .exclude(pk=player.id)
                .values_list("id", flat=True)
            )
            if recipient_ids:
                notify_data = {
                    "actor": serialize_char_from_player(player).model_dump(),
                    "die": die,
                    "outcome": outcome,
                }
                notify_text = render_event_text(
                    "notification.cmd.roll.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.roll.success",
                        recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)
