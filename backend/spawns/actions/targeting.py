from __future__ import annotations

import re

from spawns.actions.base import ActionError
from spawns.models import Mob
from worlds.models import Room


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _mob_tokens(mob: Mob) -> set[str]:
    keywords = mob.keywords or ""
    if not keywords and mob.template:
        keywords = mob.template.keywords or ""
    if not keywords:
        keywords = mob.name or ""
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("mob")
    return tokens


def mob_matches_selector(mob: Mob, selector: str) -> bool:
    if not selector:
        return False
    selector = selector.strip().lower()
    if not selector:
        return False
    if mob.key and mob.key.lower() == selector:
        return True
    return selector in _mob_tokens(mob)


def resolve_room_mob_target(
    room: Room,
    selector: str | None,
    *,
    empty_error: str,
    not_found_error: str,
) -> Mob:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        raise ActionError(empty_error, code="missing_target")

    if normalized.startswith("mob."):
        try:
            mob_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            raise ActionError(not_found_error, code="target_not_found")
        mob = room.mobs.select_related("template").filter(pk=mob_id).first()
        if not mob:
            raise ActionError(not_found_error, code="target_not_found")
        return mob

    room_mobs = list(room.mobs.select_related("template"))
    for mob in room_mobs:
        if mob_matches_selector(mob, normalized):
            return mob
    raise ActionError(not_found_error, code="target_not_found")


def first_room_mob_with_template(room: Room, template_id: int | None) -> Mob | None:
    if not template_id:
        return None
    return room.mobs.select_related("template").filter(template_id=template_id).first()
