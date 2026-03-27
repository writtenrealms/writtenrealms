from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Iterable

from django.utils import timezone

from quests.entity_refs import resolve_template_ref_id
from spawns.events import GameEvent
from quests.models import QuestOfferState, QuestTemplate
from quests.services.engine import (
    QuestRuntimeError,
    accept_template,
    active_instances_qs,
    completed_instances_qs,
    runtime_templates_qs,
    serialize_opportunity,
)
from quests.services.predicates import evaluate_condition


@dataclass
class DiscoveryRefreshResult:
    opportunities: list[dict] = field(default_factory=list)
    events: list[GameEvent] = field(default_factory=list)


def _parse_entity_id(value, expected_prefix: str) -> int | None:
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


def _source_type(source: dict) -> str:
    return str(source.get("type") or "").strip().lower()


def _npc_dialogue_source_mob_template_id(template: QuestTemplate, source: dict) -> int | None:
    if _source_type(source) != "npc_dialogue":
        return None
    return resolve_template_ref_id(
        world=template.world,
        value=source.get("mob_template") or source.get("mob_template_id"),
        expected_type="mobtemplate",
    )


def _source_matches_player(
    player,
    template: QuestTemplate,
    source: dict,
    *,
    room_mob_template_ids: set[int] | None = None,
) -> bool:
    source_type = _source_type(source)
    if source_type == "auto_start":
        return True

    if source_type == "room_prompt":
        room_id = _parse_entity_id(source.get("room") or source.get("room_id"), "room")
        if room_id is None:
            return False
        return player.room_id == room_id

    if source_type == "npc_dialogue":
        mob_template_id = _npc_dialogue_source_mob_template_id(template, source)
        if not mob_template_id or not player.room_id:
            return False
        if room_mob_template_ids is not None:
            return mob_template_id in room_mob_template_ids
        return player.room.mobs.filter(template_id=mob_template_id).exists()

    return False


def _offer_state(player, template: QuestTemplate) -> QuestOfferState:
    offer_state, _ = QuestOfferState.objects.get_or_create(
        player=player,
        template=template,
    )
    return offer_state


def _template_available(player, template: QuestTemplate) -> bool:
    if template.status != "active" or template.scope != "player":
        return False
    if template.quest_type not in {"questlet", "quest"}:
        return False
    if active_instances_qs(player).filter(template=template).exists():
        return False

    completed = completed_instances_qs(player).filter(template=template)
    if template.repeatability_mode == "never" and completed.exists():
        return False

    offer_state = _offer_state(player, template)
    if offer_state.snoozed_until and offer_state.snoozed_until > timezone.now():
        return False
    if offer_state.cooldown_until and offer_state.cooldown_until > timezone.now():
        return False

    discovery = template.discovery_policy or {}
    if not evaluate_condition(
        discovery.get("visible_if"),
        player=player,
        template=template,
        quest_instance=None,
        event_data=None,
    ):
        return False
    return True


def _matching_sources_for_template(
    player,
    template: QuestTemplate,
    *,
    room_mob_template_ids: set[int] | None = None,
) -> list[dict]:
    if not _template_available(player, template):
        return []
    return [
        source
        for source in (template.discovery_policy or {}).get("sources", [])
        if (
            isinstance(source, dict)
            and _source_matches_player(
                player,
                template,
                source,
                room_mob_template_ids=room_mob_template_ids,
            )
        )
    ]


def _should_emit_available_event(matched_sources: list[dict]) -> bool:
    if not matched_sources:
        return False
    return any(_source_type(source) != "npc_dialogue" for source in matched_sources)


def available_npc_dialogue_opportunities_for_room_mobs(player, room_mobs: Iterable) -> dict[int, list[dict]]:
    room_mob_template_ids = {
        int(mob.template_id)
        for mob in room_mobs
        if getattr(mob, "template_id", None)
    }
    opportunities_by_template_id: dict[int, list[dict]] = {}
    if not room_mob_template_ids:
        return opportunities_by_template_id

    templates = list(runtime_templates_qs(player))
    for template in templates:
        matched_sources = _matching_sources_for_template(
            player,
            template,
            room_mob_template_ids=room_mob_template_ids,
        )
        if not matched_sources:
            continue
        opportunity_payload = serialize_opportunity(template, player=player)
        for source in matched_sources:
            mob_template_id = _npc_dialogue_source_mob_template_id(template, source)
            if not mob_template_id:
                continue
            opportunities_by_template_id.setdefault(mob_template_id, []).append(opportunity_payload)

    for template_id in list(opportunities_by_template_id.keys()):
        opportunities_by_template_id[template_id].sort(
            key=lambda opportunity: (opportunity.get("name") or "").lower()
        )
    return opportunities_by_template_id


def available_npc_dialogue_opportunities_for_mob_template(
    player,
    mob_template_id: int | None,
) -> list[dict]:
    if not mob_template_id:
        return []
    return list(
        available_npc_dialogue_opportunities_for_room_mobs(
            player,
            [SimpleNamespace(template_id=mob_template_id)],
        ).get(int(mob_template_id), [])
    )


def refresh_player_quests(player) -> DiscoveryRefreshResult:
    now = timezone.now()
    result = DiscoveryRefreshResult()
    previously_visible_ids = set(
        QuestOfferState.objects.filter(player=player, is_visible=True).values_list("template_id", flat=True)
    )
    currently_visible_ids: set[int] = set()

    templates = list(runtime_templates_qs(player))
    for template in templates:
        matched_sources = _matching_sources_for_template(player, template)
        if not matched_sources:
            continue

        offer_state = _offer_state(player, template)
        auto_start = any(_source_type(source) == "auto_start" for source in matched_sources)
        if auto_start:
            try:
                transition = accept_template(player, template)
            except QuestRuntimeError:
                continue
            offer_state.is_visible = False
            offer_state.last_accepted_at = now
            offer_state.save(update_fields=["is_visible", "last_accepted_at", "modified_ts"])
            result.events.extend(transition.events)
            continue

        currently_visible_ids.add(template.id)
        offer_state.is_visible = True
        offer_state.last_seen_at = now
        offer_state.save(update_fields=["is_visible", "last_seen_at", "modified_ts"])

        opportunity_payload = serialize_opportunity(template, player=player)
        result.opportunities.append(opportunity_payload)
        if template.id not in previously_visible_ids and _should_emit_available_event(matched_sources):
            result.events.append(
                GameEvent(
                    type="quest.opportunity.available",
                    recipients=[player.key],
                    data={"opportunity": opportunity_payload},
                    text=f"New opportunity: {template.name}\n{opportunity_payload.get('lead') or opportunity_payload.get('recap') or ''}".strip(),
                )
            )

    QuestOfferState.objects.filter(player=player, is_visible=True).exclude(
        template_id__in=currently_visible_ids
    ).update(is_visible=False, modified_ts=now)

    result.opportunities.sort(key=lambda opportunity: opportunity["name"].lower())
    return result


def list_opportunities(player, *, refresh: bool = True) -> list[dict]:
    if refresh:
        return refresh_player_quests(player).opportunities

    qs = (
        QuestOfferState.objects.filter(player=player, is_visible=True)
        .select_related("template", "template__arc")
        .order_by("template__name", "template__created_ts")
    )
    return [serialize_opportunity(state.template, player=player) for state in qs]
