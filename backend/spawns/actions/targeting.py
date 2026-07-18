from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar

from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.models import Item, Mob, Player
from worlds.models import Room, World

_COUNTED_SELECTOR_RE = re.compile(r"^(?P<count>\d+)\.(?P<token>.+)$")
T = TypeVar("T")


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _normalize_selector(selector: str | None) -> str:
    return str(selector or "").strip().lower()


def _mob_tokens(mob: Mob) -> set[str]:
    keywords = mob.keywords or ""
    if not keywords and mob.definition:
        keywords = mob.definition.keywords or ""
    if not keywords:
        keywords = mob.name or ""
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("mob")
    return tokens


def mob_matches_selector(mob: Mob, selector: str) -> bool:
    selector = _normalize_selector(selector)
    if not selector:
        return False
    if mob.key and mob.key.lower() == selector:
        return True
    return selector in _mob_tokens(mob)


def _player_tokens(player: Player) -> set[str]:
    tokens = set(_tokenize_keywords(player.name or ""))
    tokens.add("player")
    if player.key:
        tokens.add(player.key.lower())
    return tokens


def room_char_matches_selector(char: Player | Mob, selector: str) -> bool:
    selector = _normalize_selector(selector)
    if not selector:
        return False
    if isinstance(char, Mob):
        return mob_matches_selector(char, selector)
    if char.key and char.key.lower() == selector:
        return True
    return selector in _player_tokens(char)


def _counted_match(
    values: list[T],
    selector: str,
    matcher,
) -> T | None:
    counted = _COUNTED_SELECTOR_RE.match(selector)
    if not counted:
        return None
    token = counted.group("token")
    index = int(counted.group("count"))
    matches = [value for value in values if matcher(value, token)]
    if index < 1 or index > len(matches):
        return None
    return matches[index - 1]


def find_room_char_target(
    room: Room,
    selector: str | None,
    *,
    viewer: Player | None = None,
    world: World | None = None,
    exclude: Player | Mob | None = None,
    lean: bool = False,
    include_invisible_players: bool = True,
    include_invisible_mobs: bool = True,
) -> Player | Mob | None:
    normalized = _normalize_selector(selector)
    if not normalized:
        return None

    if (
        normalized in {"self", "me"}
        and viewer
        and viewer != exclude
        and getattr(viewer, "room_id", None) == room.id
    ):
        return viewer

    room_players_qs = room.players.filter(in_game=True)
    room_mobs_qs = room.mobs.filter(is_pending_deletion=False)
    if not include_invisible_players:
        room_players_qs = room_players_qs.filter(is_invisible=False)
    if not include_invisible_mobs:
        room_mobs_qs = room_mobs_qs.filter(is_invisible=False)
    if world is not None:
        room_players_qs = room_players_qs.filter(world=world)
        room_mobs_qs = room_mobs_qs.filter(world=world)
    if isinstance(exclude, Player):
        room_players_qs = room_players_qs.exclude(pk=exclude.pk)
    elif isinstance(exclude, Mob):
        room_mobs_qs = room_mobs_qs.exclude(pk=exclude.pk)
    if lean:
        room_players_qs = room_players_qs.only(
            "id",
            "name",
            "title",
            "gender",
            "room_id",
            "world_id",
            "mute_list",
            "is_invisible",
            "is_builder",
        )
        room_mobs_qs = room_mobs_qs.select_related("definition").only(
            "id",
            "name",
            "title",
            "gender",
            "keywords",
            "room_id",
            "world_id",
            "is_invisible",
            "definition_id",
            "definition__name",
            "definition__keywords",
        )
    else:
        room_players_qs = room_players_qs.select_related("user", "equipment")
        room_mobs_qs = room_mobs_qs.select_related("definition", "equipment")
    room_players = list(room_players_qs.order_by("id"))
    room_mobs = list(room_mobs_qs.order_by("id"))
    chars: list[Player | Mob] = [*room_players, *room_mobs]

    if normalized.startswith("player.") or normalized.startswith("mob."):
        for char in chars:
            if getattr(char, "key", "").lower() == normalized:
                return char
        return None

    counted_match = _counted_match(chars, normalized, room_char_matches_selector)
    if counted_match is not None:
        return counted_match

    for char in chars:
        if room_char_matches_selector(char, normalized):
            return char
    return None


def _resolve_item_name(item: Item) -> str:
    instance_name = (item.name or "").strip()
    definition_name = (item.definition.name if item.definition else "") or ""
    if definition_name and (not instance_name or instance_name.lower() == "unnamed item"):
        return definition_name
    if instance_name:
        return instance_name
    if definition_name:
        return definition_name
    return "Unnamed item"


def _item_tokens(item: Item) -> set[str]:
    keywords = item.keywords or ""
    if not keywords and item.definition:
        keywords = item.definition.keywords or ""
    if not keywords:
        keywords = _resolve_item_name(item)
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("item")

    item_type = item.type or (
        item.definition.item_type if item.definition else ""
    )
    if item_type == adv_consts.ITEM_TYPE_CONTAINER:
        tokens.add("container")
    elif item_type == adv_consts.ITEM_TYPE_CORPSE:
        tokens.add("corpse")
    elif item_type == adv_consts.ITEM_TYPE_TRASH:
        tokens.add("trash")

    return tokens


def item_matches_selector(item: Item, selector: str) -> bool:
    selector = _normalize_selector(selector)
    if not selector:
        return False
    if item.key and item.key.lower() == selector:
        return True
    return selector in _item_tokens(item)


def find_accessible_item_target(
    player: Player,
    room: Room,
    selector: str | None,
) -> Item | None:
    normalized = _normalize_selector(selector)
    if not normalized or normalized == "all" or normalized.startswith("all."):
        return None

    room_items = list(
        room.inventory.filter(is_pending_deletion=False)
        .select_related("definition", "currency")
        .order_by("id")
    )
    carried_items = list(
        player.inventory.filter(is_pending_deletion=False)
        .select_related("definition", "currency")
        .order_by("id")
    )
    equipped_items = []
    if getattr(player, "equipment", None):
        equipped_items = list(
            player.equipment.inventory.filter(is_pending_deletion=False)
            .select_related("definition", "currency")
            .order_by("id")
        )

    items = [*room_items, *carried_items, *equipped_items]

    if normalized.startswith("item."):
        for item in items:
            if item.key and item.key.lower() == normalized:
                return item
        return None

    counted_match = _counted_match(items, normalized, item_matches_selector)
    if counted_match is not None:
        return counted_match

    for item in items:
        if item_matches_selector(item, normalized):
            return item
    return None


def resolve_room_mob_target(
    room: Room,
    selector: str | None,
    *,
    empty_error: str,
    not_found_error: str,
    allow_single_match_when_empty: bool = False,
    allow_first_match_when_empty: bool = False,
    empty_candidate_filter: Callable[[Mob], bool] | None = None,
) -> Mob:
    normalized = _normalize_selector(selector)
    room_mobs = list(room.mobs.select_related("definition").order_by("id"))
    if not normalized:
        if allow_single_match_when_empty:
            candidates = room_mobs
            if empty_candidate_filter:
                candidates = [mob for mob in room_mobs if empty_candidate_filter(mob)]
            if allow_first_match_when_empty and candidates:
                return candidates[0]
            if len(candidates) == 1:
                return candidates[0]
        raise ActionError(empty_error, code="missing_target")

    if normalized.startswith("mob."):
        try:
            mob_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            raise ActionError(not_found_error, code="target_not_found")
        mob = room.mobs.select_related("definition").filter(pk=mob_id).first()
        if not mob:
            raise ActionError(not_found_error, code="target_not_found")
        return mob

    for mob in room_mobs:
        if mob_matches_selector(mob, normalized):
            return mob
    raise ActionError(not_found_error, code="target_not_found")


def first_room_mob_with_definition(room: Room, definition_id: int | None) -> Mob | None:
    if not definition_id:
        return None
    return room.mobs.select_related("definition").filter(definition_id=definition_id).first()
