from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from builders.models import ItemTemplate
from config import constants as adv_consts
from quests.entity_refs import resolve_room_ref_id, resolve_template_ref_id
from quests.models import QuestInstance
from quests.services.engine import active_instances_qs, get_step
from spawns.models import Item


QUEST_ROOM_ITEM_CLAIMS_STATE_KEY = "room_item_claims"
QUEST_ROOM_ITEM_INDICATOR = "*"
_COUNTED_SELECTOR_RE = re.compile(r"^(?P<count>\d+)\.(?P<token>.+)$")


@dataclass
class QuestRoomItemProjection:
    quest_instance_id: int
    step_id: str
    spec_id: str
    room_id: int
    item_template_id: int
    item_template_slug: str
    name: str
    description: str
    ground_description: str
    keywords: str
    keyword: str | None
    claim_item_ids: list[int]

    @property
    def key(self) -> str:
        return f"questroomitem.{self.quest_instance_id}.{self.step_id}.{self.spec_id}"

    @property
    def type(self) -> str:
        return adv_consts.ITEM_TYPE_QUEST

    @property
    def is_pickable(self) -> bool:
        return True

    def to_item_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "cf_name": _capfirst(self.name),
            "type": adv_consts.ITEM_TYPE_QUEST,
            "description": self.description,
            "ground_description": self.ground_description,
            "template": self.item_template_slug,
            # Keep step-authored quest pickups unstacked even when several
            # specs point at the same item template.
            "template_id": None,
            "is_pickable": True,
            "keywords": self.keywords,
            "keyword": self.keyword,
            "indicator": QUEST_ROOM_ITEM_INDICATOR,
        }


def _capfirst(value: str | None) -> str:
    if not value:
        return ""
    return value[0].upper() + value[1:]


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _first_keyword(keywords: str, fallback_name: str) -> str | None:
    for token in str(keywords or "").split():
        token = token.strip()
        if token:
            return token
    fallback = str(fallback_name or "").strip().lower()
    return fallback.split()[0] if fallback else None


def _claim_key(step_id: str, spec_id: str) -> str:
    return f"{step_id}:{spec_id}"


def _normalize_claim_map(raw_claims: Any) -> dict[str, list[int]]:
    if not isinstance(raw_claims, dict):
        return {}

    normalized: dict[str, list[int]] = {}
    for raw_key, raw_ids in raw_claims.items():
        claim_key = str(raw_key or "").strip()
        if not claim_key or not isinstance(raw_ids, list):
            continue

        claim_ids: list[int] = []
        for raw_id in raw_ids:
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if item_id > 0:
                claim_ids.append(item_id)

        if claim_ids:
            normalized[claim_key] = claim_ids

    return normalized


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


def _record_granted_item_ids(state: dict[str, Any], item_ids: set[int]) -> bool:
    if not item_ids:
        return False

    existing: set[int] = set()
    raw_existing = state.get("granted_item_ids") or []
    if isinstance(raw_existing, list):
        for raw_id in raw_existing:
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if item_id > 0:
                existing.add(item_id)

    updated_ids = existing | {int(item_id) for item_id in item_ids if int(item_id) > 0}
    if updated_ids == existing:
        return False

    state["granted_item_ids"] = sorted(updated_ids)
    return True


def _room_item_projection(
    *,
    quest_instance: QuestInstance,
    step_id: str,
    spec_id: str,
    room_id: int,
    template: ItemTemplate,
    claim_item_ids: list[int],
    ground_description: str | None = None,
) -> QuestRoomItemProjection:
    name = str(template.name or template.slug or "Quest item").strip()
    keywords = str(template.keywords or name.lower()).strip()
    rendered_ground_description = str(
        ground_description
        or template.ground_description
        or f"{_capfirst(name)} lies here."
    ).strip()
    return QuestRoomItemProjection(
        quest_instance_id=quest_instance.id,
        step_id=step_id,
        spec_id=spec_id,
        room_id=room_id,
        item_template_id=template.id,
        item_template_slug=template.slug,
        name=name,
        description=str(template.description or "").strip(),
        ground_description=rendered_ground_description,
        keywords=keywords,
        keyword=_first_keyword(keywords, name),
        claim_item_ids=claim_item_ids,
    )


def room_item_specs_for_step(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [spec for spec in (step.get("room_items") or []) if isinstance(spec, dict)]


def _projection_from_spec(
    quest_instance: QuestInstance,
    step: dict[str, Any],
    room_item_spec: dict[str, Any],
    *,
    player_owned_item_ids: set[int],
) -> QuestRoomItemProjection | None:
    step_id = str(step.get("id") or "").strip()
    spec_id = str(room_item_spec.get("id") or "").strip()
    if not step_id or not spec_id:
        return None

    room_id = resolve_room_ref_id(
        world=quest_instance.template.world,
        value=room_item_spec.get("room") or room_item_spec.get("room_id"),
    )
    item_template_id = resolve_template_ref_id(
        world=quest_instance.template.world,
        value=room_item_spec.get("item_template") or room_item_spec.get("item_template_id"),
        expected_type="itemtemplate",
    )
    if not room_id or not item_template_id:
        return None

    template = ItemTemplate.objects.filter(
        pk=item_template_id,
        type=adv_consts.ITEM_TYPE_QUEST,
    ).first()
    if not template:
        return None

    claims = _normalize_claim_map(
        (quest_instance.local_state or {}).get(QUEST_ROOM_ITEM_CLAIMS_STATE_KEY)
    )
    claim_item_ids = claims.get(_claim_key(step_id, spec_id), [])
    if set(claim_item_ids) & player_owned_item_ids:
        return None

    return _room_item_projection(
        quest_instance=quest_instance,
        step_id=step_id,
        spec_id=spec_id,
        room_id=room_id,
        template=template,
        claim_item_ids=claim_item_ids,
        ground_description=room_item_spec.get("ground_description"),
    )


def quest_room_item_projections_for_room(player, room_id: int | None) -> list[QuestRoomItemProjection]:
    if not room_id:
        return []

    player_owned_item_ids = _player_owned_item_ids(player)
    projections: list[QuestRoomItemProjection] = []

    for quest_instance in active_instances_qs(player):
        step = get_step(quest_instance.template, quest_instance.current_step_id)
        if not step:
            continue
        for room_item_spec in room_item_specs_for_step(step):
            projection = _projection_from_spec(
                quest_instance,
                step,
                room_item_spec,
                player_owned_item_ids=player_owned_item_ids,
            )
            if projection is None or projection.room_id != int(room_id):
                continue
            projections.append(projection)

    return projections


def serialized_quest_room_items_for_room(player, room_id: int | None) -> list[dict[str, Any]]:
    return [projection.to_item_payload() for projection in quest_room_item_projections_for_room(player, room_id)]


def _projection_tokens(projection: QuestRoomItemProjection) -> set[str]:
    tokens = set(_tokenize_keywords(projection.keywords or projection.name))
    tokens.add("item")
    return tokens


def quest_room_item_matches_selector(projection: QuestRoomItemProjection, selector: str) -> bool:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        return False
    if projection.key.lower() == normalized:
        return True
    return normalized in _projection_tokens(projection)


def find_quest_room_item_target(player, room_id: int | None, selector: str | None) -> QuestRoomItemProjection | None:
    normalized = str(selector or "").strip().lower()
    if not normalized or normalized == "all" or normalized.startswith("all."):
        return None

    projections = quest_room_item_projections_for_room(player, room_id)
    if normalized.startswith("item."):
        return None

    counted = _COUNTED_SELECTOR_RE.match(normalized)
    if counted:
        token = counted.group("token")
        index = int(counted.group("count"))
        matches = [
            projection
            for projection in projections
            if quest_room_item_matches_selector(projection, token)
        ]
        if index < 1 or index > len(matches):
            return None
        return matches[index - 1]

    for projection in projections:
        if quest_room_item_matches_selector(projection, normalized):
            return projection
    return None


def claim_quest_room_item(player, projection: QuestRoomItemProjection) -> Item | None:
    quest_instance = (
        QuestInstance.objects.select_for_update()
        .select_related("template")
        .filter(pk=projection.quest_instance_id, player=player, status="active")
        .first()
    )
    if not quest_instance:
        return None

    step = get_step(quest_instance.template, quest_instance.current_step_id)
    if not step or str(step.get("id") or "").strip() != projection.step_id:
        return None

    matching_spec = None
    for room_item_spec in room_item_specs_for_step(step):
        if str(room_item_spec.get("id") or "").strip() == projection.spec_id:
            matching_spec = room_item_spec
            break
    if not matching_spec:
        return None

    room_id = resolve_room_ref_id(
        world=quest_instance.template.world,
        value=matching_spec.get("room") or matching_spec.get("room_id"),
    )
    if room_id != projection.room_id:
        return None

    item_template_id = resolve_template_ref_id(
        world=quest_instance.template.world,
        value=matching_spec.get("item_template") or matching_spec.get("item_template_id"),
        expected_type="itemtemplate",
    )
    item_template = ItemTemplate.objects.filter(
        pk=item_template_id,
        type=adv_consts.ITEM_TYPE_QUEST,
    ).first()
    if not item_template:
        return None

    local_state = dict(quest_instance.local_state or {})
    claims = _normalize_claim_map(local_state.get(QUEST_ROOM_ITEM_CLAIMS_STATE_KEY))
    claim_key = _claim_key(projection.step_id, projection.spec_id)
    if set(claims.get(claim_key, [])) & _player_owned_item_ids(player):
        return None

    spawned_item = item_template.spawn(player, player.world)
    _record_granted_item_ids(local_state, _collect_item_tree_ids([spawned_item]))

    updated_claim_ids = set(claims.get(claim_key, []))
    updated_claim_ids.add(spawned_item.id)
    claims[claim_key] = sorted(updated_claim_ids)
    local_state[QUEST_ROOM_ITEM_CLAIMS_STATE_KEY] = claims
    quest_instance.local_state = local_state
    quest_instance.save(update_fields=["local_state", "modified_ts"])

    return spawned_item
