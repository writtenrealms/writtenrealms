from __future__ import annotations

from typing import Any

from django.utils import timezone

from quests.models import QuestJournalEntry, QuestObjectiveState


def append_journal_entry(
    quest_instance,
    *,
    entry_type: str,
    step_id: str | None = None,
    recap: str = "",
    payload: dict[str, Any] | None = None,
):
    entry = QuestJournalEntry.objects.create(
        quest_instance=quest_instance,
        step_id=step_id or "",
        entry_type=entry_type,
        recap=recap or "",
        payload=payload or {},
    )
    quest_instance.last_journal_entry_at = entry.created_ts or timezone.now()
    quest_instance.save(update_fields=["last_journal_entry_at", "modified_ts"])
    return entry


def serialize_objective_state(objective_state: QuestObjectiveState) -> dict[str, Any]:
    return {
        "id": objective_state.objective_id,
        "text": objective_state.text or "",
        "status": objective_state.status,
        "progress_current": int(objective_state.progress_current or 0),
        "progress_target": int(objective_state.progress_target or 0),
    }


def render_recap_text(
    *,
    title: str,
    status: str,
    recap: str,
    objectives: list[dict[str, Any]] | None = None,
    choices: list[dict[str, Any]] | None = None,
    latest_entry: QuestJournalEntry | None = None,
) -> str:
    lines = [title, f"Status: {status}"]
    if recap:
        lines.append(f"Recap: {recap}")

    visible_objectives = [obj for obj in (objectives or []) if obj.get("status") != "hidden"]
    if visible_objectives:
        lines.append("Objectives:")
        for objective in visible_objectives:
            current = int(objective.get("progress_current") or 0)
            target = int(objective.get("progress_target") or 0)
            label = objective.get("text") or objective.get("id") or "Objective"
            lines.append(f"- [{current}/{target}] {label}")

    if choices:
        lines.append("Choices:")
        for choice in choices:
            lines.append(f"- {choice['id']}: {choice['text']}")

    if latest_entry and latest_entry.recap:
        lines.append(f"Last change: {latest_entry.recap}")

    return "\n".join(lines)
