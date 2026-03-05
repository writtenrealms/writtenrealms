from __future__ import annotations

from django.conf import settings
from django.db import transaction

MOB_SPAWN_EVENT_TYPE = "mob.spawned"
MOB_DESTROYED_EVENT_TYPE = "mob.destroyed"


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


def _forwarding_enabled() -> bool:
    forward_url = str(getattr(settings, "WR_AI_EVENT_FORWARD_URL", "") or "").strip()
    return bool(forward_url)


def _is_event_type_allowed(event_type: str | None) -> bool:
    normalized_event_type = _normalize_event_type(event_type)
    allowed_event_types = _parse_forward_event_types(
        getattr(settings, "WR_AI_EVENT_TYPES", "")
    )
    return normalized_event_type in allowed_event_types


def _mob_identity_payload(mob) -> dict:
    return {
        "key": str(getattr(mob, "key", "") or "").strip(),
        "name": str(getattr(mob, "name", "") or "").strip(),
        "template_id": getattr(mob, "template_id", None),
    }


def _mob_world_room_payload(mob) -> tuple[str, str]:
    spawn_world_key = ""
    room_key = ""
    if getattr(mob, "world_id", None):
        spawn_world_key = str(getattr(mob.world, "key", "") or "").strip()
    if getattr(mob, "room_id", None):
        room_key = str(getattr(mob.room, "key", "") or "").strip()
    return spawn_world_key, room_key


def _mob_actor_snapshot(mob, *, spawn_world_key: str, room_key: str) -> dict:
    identity = _mob_identity_payload(mob)
    return {
        "key": identity["key"],
        "name": identity["name"],
        "kind": "mob",
        "world_key": spawn_world_key,
        "room_key": room_key,
    }


def _enqueue_forward_event(
    *,
    event_type: str,
    event_data: dict,
    actor_key: str,
    actor_snapshot: dict | None = None,
) -> None:
    from spawns import tasks as spawn_tasks

    def enqueue_forward() -> None:
        try:
            spawn_tasks.forward_event_to_ai_sidecar.delay(
                event_type=event_type,
                event_data=event_data,
                actor_key=actor_key,
                actor_snapshot=actor_snapshot,
            )
        except Exception:
            # Keep gameplay and spawn flow resilient even if queueing fails.
            return

    try:
        transaction.on_commit(enqueue_forward)
    except Exception:
        # Keep gameplay and spawn flow resilient even if commit hooks fail.
        return


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
    if not _forwarding_enabled():
        return

    normalized_event_type = _normalize_event_type(event_type)
    if not _is_event_type_allowed(normalized_event_type):
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


def maybe_enqueue_ai_sidecar_mob_spawned(
    *,
    mob,
    source: str,
    trigger_actor_key: str | None = None,
    loader_id: int | None = None,
    rule_id: int | None = None,
) -> None:
    """
    Enqueue a non-blocking sidecar signal when a mob is added to a world.
    """
    if not _forwarding_enabled():
        return
    if not _is_event_type_allowed(MOB_SPAWN_EVENT_TYPE):
        return
    if not mob:
        return

    identity = _mob_identity_payload(mob)
    mob_key = identity["key"]
    if not mob_key:
        return

    spawn_world_key, room_key = _mob_world_room_payload(mob)
    event_data = {
        "source": str(source or "").strip() or "unknown",
        "mob": identity,
        "spawn_world_key": spawn_world_key,
        "room_key": room_key,
    }
    if trigger_actor_key:
        event_data["trigger_actor_key"] = str(trigger_actor_key)
    if loader_id is not None:
        event_data["loader_id"] = int(loader_id)
    if rule_id is not None:
        event_data["rule_id"] = int(rule_id)

    _enqueue_forward_event(
        event_type=MOB_SPAWN_EVENT_TYPE,
        event_data=event_data,
        actor_key=mob_key,
        actor_snapshot=_mob_actor_snapshot(
            mob,
            spawn_world_key=spawn_world_key,
            room_key=room_key,
        ),
    )


def maybe_enqueue_ai_sidecar_mob_destroyed(
    *,
    mob,
    source: str,
    trigger_actor_key: str | None = None,
    reason: str | None = None,
) -> None:
    """
    Enqueue a non-blocking sidecar signal when a mob is removed from a world.
    """
    if not _forwarding_enabled():
        return
    if not _is_event_type_allowed(MOB_DESTROYED_EVENT_TYPE):
        return
    if not mob:
        return

    identity = _mob_identity_payload(mob)
    mob_key = identity["key"]
    if not mob_key:
        return

    spawn_world_key, room_key = _mob_world_room_payload(mob)
    event_data = {
        "source": str(source or "").strip() or "unknown",
        "mob": identity,
        "spawn_world_key": spawn_world_key,
        "room_key": room_key,
    }
    if trigger_actor_key:
        event_data["trigger_actor_key"] = str(trigger_actor_key)
    if reason:
        event_data["reason"] = str(reason)

    _enqueue_forward_event(
        event_type=MOB_DESTROYED_EVENT_TYPE,
        event_data=event_data,
        actor_key=mob_key,
        actor_snapshot=_mob_actor_snapshot(
            mob,
            spawn_world_key=spawn_world_key,
            room_key=room_key,
        ),
    )
