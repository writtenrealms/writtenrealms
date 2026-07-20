from __future__ import annotations

from datetime import timedelta
from math import ceil

from django.db.models import Exists, OuterRef, Prefetch, Subquery
from django.utils import timezone

from core.scoped_state import build_state_context
from quests.models import (
    QuestInstance,
    QuestJournalEntry,
    QuestObjectiveState,
)
from quests.services.engine import get_step, serialize_instance


QUEST_LOG_ACTIVE_LIMIT = 50
QUEST_LOG_REPEATABLE_LIMIT = 100
QUEST_LOG_RESOLVED_LIMIT = 100


def _serialization_prefetches():
    return (
        Prefetch(
            "objective_states",
            queryset=QuestObjectiveState.objects.order_by("created_ts"),
            to_attr="_serialization_objective_states",
        ),
        Prefetch(
            "journal_entries",
            queryset=QuestJournalEntry.objects.order_by("-created_ts")[:1],
            to_attr="_serialization_latest_journal_entries",
        ),
    )


def _with_serialization_data(qs):
    return (
        qs.select_related("template", "template__arc", "world", "player")
        .prefetch_related(*_serialization_prefetches())
    )


def _active_instances(player):
    qs = QuestInstance.objects.filter(
        player=player,
        status="active",
    ).order_by("-modified_ts", "-created_ts", "-pk")
    instances = list(_with_serialization_data(qs)[:QUEST_LOG_ACTIVE_LIMIT + 1])
    return instances[:QUEST_LOG_ACTIVE_LIMIT], len(instances) > QUEST_LOG_ACTIVE_LIMIT


def _latest_completed_instances(player):
    latest_completed_pk = (
        QuestInstance.objects.filter(
            player=player,
            template_id=OuterRef("template_id"),
            status="resolved",
        )
        .exclude(resolution="abandoned")
        .order_by("-resolved_at", "-modified_ts", "-created_ts", "-pk")
        .values("pk")[:1]
    )
    active_for_template = QuestInstance.objects.filter(
        player=player,
        template_id=OuterRef("template_id"),
        status="active",
    )
    return (
        QuestInstance.objects.filter(
            player=player,
            status="resolved",
            pk=Subquery(latest_completed_pk),
        )
        .exclude(resolution="abandoned")
        .annotate(_has_active_instance=Exists(active_for_template))
        .filter(_has_active_instance=False)
    )


def _completed_instances(player, *, repeatable: bool):
    qs = _latest_completed_instances(player)
    if repeatable:
        qs = qs.filter(template__repeatability_mode__in=("always", "cooldown"))
        limit = QUEST_LOG_REPEATABLE_LIMIT
    else:
        qs = qs.filter(template__repeatability_mode="never")
        limit = QUEST_LOG_RESOLVED_LIMIT
    qs = qs.order_by("-resolved_at", "-modified_ts", "-created_ts", "-pk")
    instances = list(_with_serialization_data(qs)[:limit + 1])
    return instances[:limit], len(instances) > limit


def _serialize_datetime(value):
    return value.isoformat() if value else None


def _repeatability_payload(instance: QuestInstance, *, now) -> dict:
    template = instance.template
    mode = template.repeatability_mode
    cooldown_seconds = int(template.repeatability_cooldown_seconds or 0)
    payload = {
        "mode": mode,
        "cooldown_seconds": cooldown_seconds,
        "state": "unavailable",
        "ready_at": None,
        "remaining_seconds": None,
        "template_status": template.status,
    }

    if instance.status == "active" or template.status != "active" or mode == "never":
        return payload

    if mode == "always":
        payload.update(state="ready", remaining_seconds=0)
        return payload

    if mode != "cooldown" or not instance.resolved_at:
        return payload

    ready_at = instance.resolved_at + timedelta(seconds=cooldown_seconds)
    remaining_seconds = max(0, ceil((ready_at - now).total_seconds()))
    payload.update(
        state="waiting" if remaining_seconds else "ready",
        ready_at=_serialize_datetime(ready_at),
        remaining_seconds=remaining_seconds,
    )
    return payload


def _serialize_log_entry(
    instance: QuestInstance,
    *,
    player,
    now,
    shared_state_context=None,
) -> dict:
    state_context = None
    if shared_state_context is not None:
        state_context = dict(shared_state_context)
        state_context["quest"] = dict(instance.local_state or {})
    payload = serialize_instance(
        instance,
        player=player,
        state_context=state_context,
    )
    payload["repeatability"] = _repeatability_payload(instance, now=now)
    return payload


def _contains_template_markup(value) -> bool:
    if isinstance(value, str):
        return "{{" in value or "{%" in value
    if isinstance(value, dict):
        return any(_contains_template_markup(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_template_markup(item) for item in value)
    return False


def _rendering_context(player, instances):
    if not any(
        _contains_template_markup(
            get_step(instance.template, instance.current_step_id) or {}
        )
        for instance in instances
    ):
        return player, None
    rendering_player = (
        type(player).objects.select_related("world", "room", "room__zone").get(pk=player.pk)
    )
    return rendering_player, build_state_context(
        actor=rendering_player,
        character=rendering_player,
    )


def build_quest_log(player, *, now=None) -> dict:
    """Project current quest-log buckets from runtime history and live templates.

    Completed buckets contain at most one non-abandoned instance per template. The
    current template policy determines the bucket and readiness, so builder
    changes take effect without rewriting historical quest instances.
    """
    now = now or timezone.now()
    active, active_truncated = _active_instances(player)
    repeatable, repeatable_truncated = _completed_instances(player, repeatable=True)
    resolved, resolved_truncated = _completed_instances(player, repeatable=False)
    rendering_player, shared_state_context = _rendering_context(
        player,
        [*active, *repeatable, *resolved],
    )
    return {
        "server_time": _serialize_datetime(now),
        "limits": {
            "active": {
                "limit": QUEST_LOG_ACTIVE_LIMIT,
                "truncated": active_truncated,
            },
            "repeatable": {
                "limit": QUEST_LOG_REPEATABLE_LIMIT,
                "truncated": repeatable_truncated,
            },
            "resolved": {
                "limit": QUEST_LOG_RESOLVED_LIMIT,
                "truncated": resolved_truncated,
            },
        },
        "active": [
            _serialize_log_entry(
                instance,
                player=rendering_player,
                now=now,
                shared_state_context=shared_state_context,
            )
            for instance in active
        ],
        "repeatable": [
            _serialize_log_entry(
                instance,
                player=rendering_player,
                now=now,
                shared_state_context=shared_state_context,
            )
            for instance in repeatable
        ],
        "resolved": [
            _serialize_log_entry(
                instance,
                player=rendering_player,
                now=now,
                shared_state_context=shared_state_context,
            )
            for instance in resolved
        ],
    }
