from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from spawns.events import GameEvent
from quests.models import (
    QuestInstance,
    QuestObjectiveState,
    QuestOfferState,
    QuestTemplate,
)
from quests.services.effects import apply_quest_effects
from quests.services.journal import (
    append_journal_entry,
    render_recap_text,
    serialize_objective_state,
)
from quests.services.predicates import evaluate_condition, resolve_value


RUNTIME_TEMPLATE_TYPES = {"questlet", "quest"}
RUNTIME_STEP_KINDS = {"storylet", "objective", "resolution"}


class QuestRuntimeError(Exception):
    def __init__(self, message: str, *, code: str = "quest_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _serialize_dt(value):
    if not value:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return value


@dataclass
class QuestTransitionResult:
    quest_instance: QuestInstance | None
    events: list[GameEvent]


def template_world_for_player(player):
    return player.world.context or player.world


def runtime_templates_qs(player):
    return QuestTemplate.objects.filter(
        world=template_world_for_player(player),
        status="active",
        scope="player",
        quest_type__in=RUNTIME_TEMPLATE_TYPES,
    ).select_related("arc")


def get_template_steps(template: QuestTemplate) -> list[dict[str, Any]]:
    graph = template.graph or {}
    steps = graph.get("steps") or []
    return [step for step in steps if isinstance(step, dict)]


def get_start_step(template: QuestTemplate) -> dict[str, Any]:
    steps = get_template_steps(template)
    if not steps:
        raise QuestRuntimeError("Quest template has no steps.", code="invalid_graph")
    return steps[0]


def get_step(template: QuestTemplate, step_id: str | None) -> dict[str, Any] | None:
    target = str(step_id or "").strip()
    if not target:
        return None
    for step in get_template_steps(template):
        if str(step.get("id") or "").strip() == target:
            return step
    return None


def serialize_choice(choice: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(choice.get("id") or "").strip(),
        "text": str(choice.get("text") or "").strip(),
        "goto": str(choice.get("goto") or "").strip(),
    }


def visible_choices(step: dict[str, Any], *, player, quest_instance) -> list[dict[str, Any]]:
    template = quest_instance.template if quest_instance else None
    visible: list[dict[str, Any]] = []
    for choice in step.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        choice_condition = choice.get("if")
        if not evaluate_condition(
            choice_condition,
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=None,
        ):
            continue
        visible.append(serialize_choice(choice))
    return visible


def _objective_specs_for_step(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [obj for obj in (step.get("objectives") or []) if isinstance(obj, dict)]


def _objective_target(objective_spec: dict[str, Any]) -> int:
    progress_spec = objective_spec.get("progress") or {}
    try:
        target = int(progress_spec.get("target") or 1)
    except (TypeError, ValueError):
        target = 1
    return max(target, 1)


def _sync_objective_state_for_step(quest_instance: QuestInstance, step: dict[str, Any]) -> None:
    existing = {
        state.objective_id: state
        for state in quest_instance.objective_states.all()
    }
    current_ids: list[str] = []

    for objective_spec in _objective_specs_for_step(step):
        objective_id = str(objective_spec.get("id") or "").strip()
        if not objective_id:
            continue
        current_ids.append(objective_id)
        state = existing.get(objective_id)
        if state is None:
            state = QuestObjectiveState.objects.create(
                quest_instance=quest_instance,
                objective_id=objective_id,
                text=str(objective_spec.get("text") or "").strip(),
                status="hidden" if objective_spec.get("hidden") else "active",
                progress_current=0,
                progress_target=_objective_target(objective_spec),
            )
        else:
            state.text = str(objective_spec.get("text") or "").strip()
            state.progress_target = _objective_target(objective_spec)
            if objective_spec.get("hidden") and state.status == "active":
                state.status = "hidden"
            elif not objective_spec.get("hidden") and state.status == "hidden":
                state.status = "active"
            state.save()

    quest_instance.objective_states.exclude(objective_id__in=current_ids).delete()
    quest_instance.visible_objective_ids = current_ids
    quest_instance.save(update_fields=["visible_objective_ids", "modified_ts"])


def _apply_effects(
    quest_instance: QuestInstance,
    effects: list[dict[str, Any]] | None,
    *,
    player=None,
    event_data: dict[str, Any] | None = None,
):
    return apply_quest_effects(
        quest_instance,
        effects,
        player=player,
        template=quest_instance.template,
        event_data=event_data,
    )


def _build_player_event(
    player,
    *,
    event_type: str,
    text: str,
    data: dict[str, Any] | None = None,
) -> GameEvent:
    return GameEvent(
        type=event_type,
        recipients=[player.key],
        data=data or {},
        text=text,
    )


def _build_step_payload(quest_instance: QuestInstance, *, player) -> dict[str, Any]:
    step = get_step(quest_instance.template, quest_instance.current_step_id) or {}
    objective_states = [
        serialize_objective_state(state)
        for state in quest_instance.objective_states.all().order_by("created_ts")
    ]
    return {
        "id": str(step.get("id") or ""),
        "kind": str(step.get("kind") or ""),
        "recap": str(step.get("recap") or ""),
        "lead": str(step.get("lead") or ""),
        "stakes": str(step.get("stakes") or ""),
        "text": step.get("text") or {},
        "choices": visible_choices(step, player=player, quest_instance=quest_instance),
        "objectives": objective_states,
    }


def serialize_opportunity(template: QuestTemplate, *, player) -> dict[str, Any]:
    start_step = get_start_step(template)
    return {
        "id": template.id,
        "key": template.key,
        "slug": template.slug,
        "name": template.name,
        "quest_type": template.quest_type,
        "scope": template.scope,
        "recap": str(start_step.get("recap") or ""),
        "lead": str(start_step.get("lead") or ""),
        "stakes": str(start_step.get("stakes") or ""),
        "text": start_step.get("text") or {},
    }


def serialize_instance(quest_instance: QuestInstance, *, player) -> dict[str, Any]:
    latest_entry = quest_instance.journal_entries.order_by("-created_ts").first()
    payload = _build_step_payload(quest_instance, player=player)
    return {
        "id": quest_instance.id,
        "key": quest_instance.key,
        "status": quest_instance.status,
        "resolution": quest_instance.resolution,
        "template": {
            "id": quest_instance.template.id,
            "key": quest_instance.template.key,
            "slug": quest_instance.template.slug,
            "name": quest_instance.template.name,
            "quest_type": quest_instance.template.quest_type,
        },
        "current_step_id": quest_instance.current_step_id,
        "current_step": payload,
        "latest_journal_entry": (
            {
                "id": latest_entry.id,
                "entry_type": latest_entry.entry_type,
                "recap": latest_entry.recap or "",
                "lead": latest_entry.lead or "",
                "stakes": latest_entry.stakes or "",
                "created_ts": _serialize_dt(latest_entry.created_ts),
            }
            if latest_entry else None
        ),
        "resolved_at": _serialize_dt(quest_instance.resolved_at),
    }


def _recap_for_instance(quest_instance: QuestInstance, *, player) -> tuple[dict[str, Any], str]:
    serialized = serialize_instance(quest_instance, player=player)
    current_step = serialized["current_step"]
    latest_entry = quest_instance.journal_entries.order_by("-created_ts").first()
    text = render_recap_text(
        title=quest_instance.template.name,
        status=quest_instance.status if quest_instance.status != "resolved" else (quest_instance.resolution or "resolved"),
        recap=current_step.get("recap") or "",
        lead=current_step.get("lead") or "",
        stakes=current_step.get("stakes") or "",
        objectives=current_step.get("objectives") or [],
        choices=current_step.get("choices") or [],
        latest_entry=latest_entry,
    )
    return serialized, text


def active_instances_qs(player):
    return (
        QuestInstance.objects.filter(player=player, status="active")
        .select_related("template", "template__arc", "world", "player")
        .prefetch_related("objective_states", "journal_entries")
        .order_by("-modified_ts", "-created_ts")
    )


def completed_instances_qs(player):
    return (
        QuestInstance.objects.filter(player=player, status="resolved")
        .select_related("template", "template__arc", "world", "player")
        .prefetch_related("objective_states", "journal_entries")
        .order_by("-resolved_at", "-modified_ts", "-created_ts")
    )


def list_active_instances(player) -> list[dict[str, Any]]:
    return [serialize_instance(instance, player=player) for instance in active_instances_qs(player)]


def list_completed_instances(player) -> list[dict[str, Any]]:
    return [serialize_instance(instance, player=player) for instance in completed_instances_qs(player)]


def resolve_instance_identity(player, identity: str, *, status: str | None = None) -> QuestInstance:
    text = str(identity or "").strip()
    if not text:
        raise QuestRuntimeError("Quest identifier is required.", code="missing_identifier")

    qs = QuestInstance.objects.filter(player=player).select_related("template", "template__arc", "world", "player").prefetch_related("objective_states", "journal_entries")
    if status:
        qs = qs.filter(status=status)
    if text.isdigit():
        instance = qs.filter(pk=int(text)).first()
    else:
        instance = qs.filter(template__slug=text).order_by("-created_ts").first()
    if not instance:
        raise QuestRuntimeError("Quest was not found.", code="quest_not_found")
    return instance


def resolve_template_for_player(player, slug: str) -> QuestTemplate:
    template = runtime_templates_qs(player).filter(slug=str(slug or "").strip()).first()
    if not template:
        raise QuestRuntimeError("Quest opportunity was not found.", code="opportunity_not_found")
    return template


def _can_start_template(player, template: QuestTemplate) -> bool:
    if active_instances_qs(player).filter(template=template).exists():
        return False

    resolved_qs = completed_instances_qs(player).filter(template=template)
    if template.repeatability_mode == "never" and resolved_qs.exists():
        return False
    if template.repeatability_mode == "cooldown":
        latest = resolved_qs.first()
        if latest and latest.resolved_at:
            cooldown_until = latest.resolved_at + timedelta(
                seconds=int(template.repeatability_cooldown_seconds or 0)
            )
            if cooldown_until > timezone.now():
                return False
    return True


def _resolve_fixed_slots(template: QuestTemplate) -> dict[str, Any]:
    bindings: dict[str, Any] = {}
    for slot_name, slot_spec in (template.slot_schema or {}).items():
        if not isinstance(slot_spec, dict):
            continue
        resolve_spec = slot_spec.get("resolve") if isinstance(slot_spec.get("resolve"), dict) else slot_spec
        if not isinstance(resolve_spec, dict):
            continue
        if str(resolve_spec.get("type") or "").strip().lower() != "fixed":
            continue
        if "value" in resolve_spec:
            bindings[slot_name] = resolve_spec.get("value")
        elif "entity" in resolve_spec:
            bindings[slot_name] = resolve_spec.get("entity")
    return bindings


def _transition_if_any(
    quest_instance: QuestInstance,
    *,
    player,
    event_data: dict[str, Any] | None = None,
    objective_state_map: dict[str, QuestObjectiveState] | None = None,
) -> QuestTransitionResult:
    step = get_step(quest_instance.template, quest_instance.current_step_id) or {}
    transitions = [transition for transition in (step.get("transitions") or []) if isinstance(transition, dict)]
    for transition in transitions:
        condition = transition.get("when")
        if not evaluate_condition(
            condition,
            player=player,
            template=quest_instance.template,
            quest_instance=quest_instance,
            event_data=event_data,
            objective_state_map=objective_state_map,
        ):
            continue
        _apply_effects(
            quest_instance,
            transition.get("effects") or [],
            player=player,
            event_data=event_data,
        )
        return enter_step(
            quest_instance,
            step_id=str(transition.get("goto") or "").strip(),
            player=player,
            entry_reason="transition",
            event_data=event_data,
        )
    return QuestTransitionResult(quest_instance=quest_instance, events=[])


def enter_step(
    quest_instance: QuestInstance,
    *,
    step_id: str,
    player,
    entry_reason: str,
    event_data: dict[str, Any] | None = None,
) -> QuestTransitionResult:
    step = get_step(quest_instance.template, step_id)
    if not step:
        raise QuestRuntimeError("Quest step was not found.", code="step_not_found")

    step_kind = str(step.get("kind") or "").strip().lower()
    if step_kind not in RUNTIME_STEP_KINDS:
        raise QuestRuntimeError(
            f"Step kind '{step_kind}' is not supported in Phase 2 runtime.",
            code="unsupported_step_kind",
        )

    with transaction.atomic():
        quest_instance = QuestInstance.objects.select_for_update().get(pk=quest_instance.pk)
        quest_instance.current_step_id = str(step.get("id") or "").strip()
        quest_instance.save(update_fields=["current_step_id", "modified_ts"])

        if step_kind == "objective":
            _sync_objective_state_for_step(quest_instance, step)
        else:
            quest_instance.objective_states.all().delete()
            quest_instance.visible_objective_ids = []
            quest_instance.save(update_fields=["visible_objective_ids", "modified_ts"])

        entry_type = "resolved" if step_kind == "resolution" else "step_entered"
        append_journal_entry(
            quest_instance,
            entry_type=entry_type,
            step_id=str(step.get("id") or "").strip(),
            recap=str(step.get("recap") or ""),
            lead=str(step.get("lead") or ""),
            stakes=str(step.get("stakes") or ""),
            payload={"reason": entry_reason},
        )

        if step_kind == "resolution":
            quest_instance.status = "resolved"
            quest_instance.resolution = str(step.get("resolution") or "complete")
            quest_instance.resolved_at = timezone.now()
            quest_instance.save(update_fields=["status", "resolution", "resolved_at", "modified_ts"])

            offer_state, _ = QuestOfferState.objects.get_or_create(
                player=player,
                template=quest_instance.template,
            )
            offer_state.is_visible = False
            offer_state.last_resolved_at = quest_instance.resolved_at
            offer_state.save(
                update_fields=["is_visible", "last_resolved_at", "modified_ts"]
            )

    refreshed = QuestInstance.objects.select_related("template", "template__arc", "world", "player").prefetch_related("objective_states", "journal_entries").get(pk=quest_instance.pk)
    step_effect_result = _apply_effects(
        refreshed,
        step.get("effects") or [],
        player=player,
        event_data=event_data,
    )
    reward_summaries = list(step_effect_result.reward_summaries)
    if step_kind == "resolution":
        reward_result = _apply_effects(
            refreshed,
            (refreshed.template.reward_policy or {}).get(refreshed.resolution or "complete") or [],
            player=player,
            event_data=event_data,
        )
        reward_summaries.extend(reward_result.reward_summaries)
    payload, recap_text = _recap_for_instance(refreshed, player=player)
    if step_kind == "resolution":
        event_type = "quest.instance.resolved"
        text = f"Quest resolved: {refreshed.template.name}\n{recap_text}"
    elif entry_reason in {"started", "auto_start"}:
        event_type = "quest.instance.started"
        text = f"Quest started: {refreshed.template.name}\n{recap_text}"
    else:
        event_type = "quest.instance.updated"
        text = recap_text
    if reward_summaries:
        text = f"{text}\nRewards: {', '.join(reward_summaries)}"

    return QuestTransitionResult(
        quest_instance=refreshed,
        events=[_build_player_event(player, event_type=event_type, text=text, data={"quest": payload})],
    )


def start_quest_instance(
    player,
    template: QuestTemplate,
    *,
    reason: str,
) -> QuestTransitionResult:
    if not _can_start_template(player, template):
        raise QuestRuntimeError("Quest cannot be started right now.", code="cannot_start")

    with transaction.atomic():
        quest_instance = QuestInstance.objects.create(
            world=player.world,
            template=template,
            player=player,
            status="active",
            current_step_id="",
            slot_bindings=_resolve_fixed_slots(template),
            local_state={},
            visible_objective_ids=[],
        )
        offer_state, _ = QuestOfferState.objects.get_or_create(
            player=player,
            template=template,
        )
        offer_state.is_visible = False
        offer_state.last_accepted_at = timezone.now()
        offer_state.save(update_fields=["is_visible", "last_accepted_at", "modified_ts"])

    return enter_step(
        quest_instance,
        step_id=str(get_start_step(template).get("id") or "").strip(),
        player=player,
        entry_reason=reason,
        event_data=None,
    )


def accept_template(player, template: QuestTemplate) -> QuestTransitionResult:
    discovery = template.discovery_policy or {}
    if not evaluate_condition(
        discovery.get("accept_if"),
        player=player,
        template=template,
        quest_instance=None,
        event_data=None,
    ):
        raise QuestRuntimeError("Quest cannot be accepted right now.", code="cannot_accept")
    return start_quest_instance(player, template, reason="started")


def choose_for_instance(player, identity: str, choice_id: str) -> QuestTransitionResult:
    quest_instance = resolve_instance_identity(player, identity, status="active")
    step = get_step(quest_instance.template, quest_instance.current_step_id)
    if not step:
        raise QuestRuntimeError("Quest step was not found.", code="step_not_found")
    if str(step.get("kind") or "").strip().lower() != "storylet":
        raise QuestRuntimeError("Current quest step does not accept choices.", code="not_storylet")

    selected_choice = None
    for choice in step.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if str(choice.get("id") or "").strip() != str(choice_id or "").strip():
            continue
        if evaluate_condition(
            choice.get("if"),
            player=player,
            template=quest_instance.template,
            quest_instance=quest_instance,
            event_data=None,
        ):
            selected_choice = choice
            break

    if not selected_choice:
        raise QuestRuntimeError("Choice was not found for this quest step.", code="choice_not_found")

    _apply_effects(
        quest_instance,
        selected_choice.get("effects") or [],
        player=player,
        event_data=None,
    )
    goto = str(selected_choice.get("goto") or "").strip()
    if not goto:
        raise QuestRuntimeError("Choice does not lead anywhere.", code="missing_goto")
    return enter_step(
        quest_instance,
        step_id=goto,
        player=player,
        entry_reason=f"choice:{selected_choice.get('id')}",
    )


def abandon_instance(player, identity: str) -> QuestTransitionResult:
    with transaction.atomic():
        quest_instance = QuestInstance.objects.select_for_update().select_related("template", "template__arc", "world", "player").get(
            pk=resolve_instance_identity(player, identity, status="active").pk
        )
        quest_instance.status = "resolved"
        quest_instance.resolution = "abandoned"
        quest_instance.resolved_at = timezone.now()
        quest_instance.save(update_fields=["status", "resolution", "resolved_at", "modified_ts"])
        append_journal_entry(
            quest_instance,
            entry_type="resolved",
            step_id=quest_instance.current_step_id or "",
            recap=f"You abandoned {quest_instance.template.name}.",
            lead="",
            stakes="",
            payload={"reason": "abandoned"},
        )
        offer_state, _ = QuestOfferState.objects.get_or_create(
            player=player,
            template=quest_instance.template,
        )
        offer_state.is_visible = False
        offer_state.last_resolved_at = quest_instance.resolved_at
        offer_state.save(update_fields=["is_visible", "last_resolved_at", "modified_ts"])

    quest_instance = QuestInstance.objects.select_related("template", "template__arc", "world", "player").prefetch_related("objective_states", "journal_entries").get(pk=quest_instance.pk)
    payload, recap_text = _recap_for_instance(quest_instance, player=player)
    return QuestTransitionResult(
        quest_instance=quest_instance,
        events=[
            _build_player_event(
                player,
                event_type="quest.instance.resolved",
                text=f"Quest abandoned: {quest_instance.template.name}\n{recap_text}",
                data={"quest": payload},
            )
        ],
    )


def recap_for_player(player, identity: str | None = None) -> tuple[dict[str, Any], str]:
    if identity:
        quest_instance = resolve_instance_identity(player, identity)
        return _recap_for_instance(quest_instance, player=player)

    active_instances = list(active_instances_qs(player)[:2])
    if not active_instances:
        raise QuestRuntimeError("You have no active quests.", code="no_active_quests")
    if len(active_instances) > 1:
        lines = ["Active quests:"]
        payload = {"quests": []}
        for quest_instance in active_instances_qs(player):
            serialized = serialize_instance(quest_instance, player=player)
            payload["quests"].append(serialized)
            lines.append(f"- {serialized['template']['slug']}: {serialized['template']['name']}")
        return payload, "\n".join(lines)
    return _recap_for_instance(active_instances[0], player=player)


def progress_active_instance_for_event(
    quest_instance: QuestInstance,
    *,
    player,
    event_type: str,
    event_data: dict[str, Any] | None,
) -> QuestTransitionResult:
    quest_instance = QuestInstance.objects.select_related("template", "template__arc", "world", "player").prefetch_related("objective_states", "journal_entries").get(pk=quest_instance.pk)
    step = get_step(quest_instance.template, quest_instance.current_step_id)
    if not step or str(step.get("kind") or "").strip().lower() != "objective":
        return QuestTransitionResult(quest_instance=quest_instance, events=[])

    updated = False
    objective_state_map = {
        state.objective_id: state
        for state in quest_instance.objective_states.all()
    }
    for objective_spec in _objective_specs_for_step(step):
        objective_id = str(objective_spec.get("id") or "").strip()
        state = objective_state_map.get(objective_id)
        if not state or state.status == "complete":
            continue

        tracker = objective_spec.get("tracker") or {}
        tracker_event = str(tracker.get("event") or "").strip()
        if tracker_event.lower() != str(event_type or "").strip().lower():
            continue
        if not evaluate_condition(
            tracker.get("where"),
            player=player,
            template=quest_instance.template,
            quest_instance=quest_instance,
            event_data=event_data,
            objective_state_map=objective_state_map,
        ):
            continue

        progress_spec = objective_spec.get("progress") or {}
        progress_mode = str(progress_spec.get("mode") or "boolean").strip().lower()
        progress_target = _objective_target(objective_spec)
        now = timezone.now()
        objective_updated = False

        if progress_mode == "boolean":
            if state.progress_current < progress_target:
                state.progress_current = progress_target
                objective_updated = True
        elif progress_mode == "count":
            state.progress_current = min(progress_target, int(state.progress_current or 0) + 1)
            objective_updated = True
        elif progress_mode == "unique_count":
            distinct_path = str(progress_spec.get("distinct_by") or "").strip()
            distinct_value = None
            if distinct_path:
                distinct_value = resolve_value(
                    "{" + distinct_path + "}",
                    player=player,
                    template=quest_instance.template,
                    quest_instance=quest_instance,
                    event_data=event_data,
                )
            if distinct_value is None:
                distinct_value = event_data
            distinct_values = list(state.distinct_values or [])
            if distinct_value not in distinct_values:
                distinct_values.append(distinct_value)
                state.distinct_values = distinct_values
                state.progress_current = min(progress_target, len(distinct_values))
                objective_updated = True

        if not objective_updated:
            continue
        updated = True

        state.progress_target = progress_target
        state.last_matching_event_type = event_type
        state.last_matching_event_at = now
        if int(state.progress_current or 0) >= progress_target:
            state.status = "complete"
        state.save()
        append_journal_entry(
            quest_instance,
            entry_type="objective_updated",
            step_id=str(step.get("id") or ""),
            recap=str(step.get("recap") or ""),
            lead=str(step.get("lead") or ""),
            stakes=str(step.get("stakes") or ""),
            payload={
                "objective_id": objective_id,
                "progress_current": int(state.progress_current or 0),
                "progress_target": progress_target,
            },
        )

    if not updated:
        return QuestTransitionResult(quest_instance=quest_instance, events=[])

    refreshed_instance = QuestInstance.objects.select_related("template", "template__arc", "world", "player").prefetch_related("objective_states", "journal_entries").get(pk=quest_instance.pk)
    objective_state_map = {
        state.objective_id: state
        for state in refreshed_instance.objective_states.all()
    }
    transition_result = _transition_if_any(
        refreshed_instance,
        player=player,
        event_data=event_data,
        objective_state_map=objective_state_map,
    )
    if transition_result.events:
        return transition_result

    payload, recap_text = _recap_for_instance(refreshed_instance, player=player)
    return QuestTransitionResult(
        quest_instance=refreshed_instance,
        events=[
            _build_player_event(
                player,
                event_type="quest.instance.updated",
                text=recap_text,
                data={"quest": payload},
            )
        ],
    )
