from __future__ import annotations

from django.conf import settings


def _normalize_event_type(event_type: str | None) -> str:
    return str(event_type or "").strip().lower()


def _parse_forward_event_types(raw_types: str | None) -> set[str]:
    values: set[str] = set()
    for token in str(raw_types or "").split(","):
        normalized = _normalize_event_type(token)
        if normalized:
            values.add(normalized)
    return values


def _extract_player_actor_key(*, event_data: dict | None, actor_key: str | None) -> str | None:
    if isinstance(event_data, dict):
        actor = event_data.get("actor")
        if isinstance(actor, dict):
            actor_ref = actor.get("key")
            if actor_ref:
                actor_key = str(actor_ref)

    actor_ref = str(actor_key or "").strip()
    if not actor_ref.startswith("player."):
        return None
    return actor_ref


def maybe_enqueue_ai_sidecar_event_forwarding(
    *,
    event_type: str,
    event_data: dict | None = None,
    actor_key: str | None = None,
) -> None:
    """
    Enqueue a non-blocking forward of selected player-originated events to an
    external AI sidecar service.
    """
    forward_url = str(getattr(settings, "WR_AI_EVENT_FORWARD_URL", "") or "").strip()
    if not forward_url:
        return

    normalized_event_type = _normalize_event_type(event_type)
    allowed_event_types = _parse_forward_event_types(
        getattr(settings, "WR_AI_EVENT_TYPES", "")
    )
    if normalized_event_type not in allowed_event_types:
        return

    player_actor_key = _extract_player_actor_key(
        event_data=event_data,
        actor_key=actor_key,
    )
    if not player_actor_key:
        return

    from spawns import tasks as spawn_tasks

    payload = event_data if isinstance(event_data, dict) else {}
    try:
        spawn_tasks.forward_event_to_ai_sidecar.delay(
            event_type=normalized_event_type,
            event_data=payload,
            actor_key=player_actor_key,
        )
    except Exception:
        # Keep event publication resilient even if queueing fails.
        return
