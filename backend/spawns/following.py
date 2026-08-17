"""Sequenced, post-commit propagation for movement-follow relationships."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any
import uuid

from django.db import OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from config import constants as adv_consts


logger = logging.getLogger(__name__)

FOLLOW_FANOUT_BATCH_SIZE = 100
MAX_FOLLOW_PROPAGATION_DEPTH = 16
FOLLOW_OUTBOX_LEASE_SECONDS = 300
MAX_FOLLOW_UNRESOLVED_SWEEPS = 8
MAX_FOLLOW_TASK_RETRIES = 5
FOLLOW_OUTBOX_RETRY_BASE_SECONDS = 60
FOLLOW_OUTBOX_RETRY_MAX_SECONDS = 3600
FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY = "_follow_leader_sequence_snapshot"
FOLLOW_LEADER_ROOM_SNAPSHOT_KEY = "_follow_leader_room_snapshot"
FOLLOW_FAILURE_RECORDED = "recorded"
FOLLOW_FAILURE_IGNORED = "ignored"
FOLLOW_FAILURE_DEFERRED = "deferred"
# An object key cannot arrive through a JSON/WebSocket payload.  Only the
# in-process fan-out service can attach trusted follow movement context to the
# ordinary movement command path.
FOLLOW_MOVEMENT_PAYLOAD_KEY = object()


class FollowMovementIgnored(Exception):
    """The edge was already processed or no longer belongs to this link."""


class FollowMovementDeferred(Exception):
    """An earlier leader edge must be processed before this one."""


class FollowMovementBlocked(Exception):
    """The edge is current, but this follower cannot attempt it."""

    def __init__(self, message: str, *, code: str = "follow_blocked"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class FollowMovementBatchResult:
    processed: int
    next_after_id: int | None
    retry_after_id: int | None


def _set_result(context: dict | None, status: str) -> None:
    if not isinstance(context, dict):
        return
    sink = context.get("_result_sink")
    if isinstance(sink, list):
        sink.append(status)


def set_follow_movement_result(context: dict | None, status: str) -> None:
    """Expose an in-process result to the bounded Celery fan-out service."""
    _set_result(context, status)


def _normalized_event_data(event_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(event_data, dict):
        return None
    actor = event_data.get("actor")
    if not isinstance(actor, dict):
        return None
    actor_type = str(actor.get("type") or "").strip().lower()
    if actor_type not in {"player", "mob"}:
        return None
    try:
        actor_id = int(actor.get("id"))
        runtime_world_id = int(event_data.get("runtime_world_id"))
        origin_room_id = int(event_data.get("origin_room_id"))
        destination_room_id = int(event_data.get("destination_room_id"))
        sequence = int(event_data.get("sequence"))
        depth = max(0, int(event_data.get("depth") or 0))
    except (TypeError, ValueError):
        return None
    direction = str(event_data.get("direction") or "").strip().lower()
    if (
        actor_id <= 0
        or runtime_world_id <= 0
        or origin_room_id <= 0
        or destination_room_id <= 0
        or sequence <= 0
        or direction not in adv_consts.DIRECTIONS
        or origin_room_id == destination_room_id
    ):
        return None
    return {
        "movement_id": str(event_data.get("movement_id") or ""),
        "root_id": str(event_data.get("root_id") or ""),
        "depth": depth,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "actor_name": str(actor.get("name") or "").strip(),
        "actor_is_invisible": bool(actor.get("is_invisible", False)),
        "runtime_world_id": runtime_world_id,
        "origin_room_id": origin_room_id,
        "destination_room_id": destination_room_id,
        "direction": direction,
        "source": str(event_data.get("source") or "").strip().lower(),
        "sequence": sequence,
    }


def _enqueue_follow_task(
    event_data: dict[str, Any],
    *,
    outbox_event_id: str | None = None,
    claim_token: str | None = None,
    after_id: int = 0,
    sweep_needs_retry: bool = False,
    attempt: int = 0,
    countdown: float = 0,
) -> None:
    from spawns.tasks import propagate_follow_movement

    event_data = _event_data_with_leader_snapshot(event_data)
    propagate_follow_movement.apply_async(
        kwargs={
            "event_data": event_data,
            "outbox_event_id": outbox_event_id,
            "claim_token": claim_token,
            "after_id": after_id,
            "sweep_needs_retry": sweep_needs_retry,
            "attempt": attempt,
        },
        **({"countdown": countdown} if countdown else {}),
    )


def _claim_and_enqueue_follow_movement(
    event_data: dict[str, Any],
    *,
    outbox_event_id: str,
) -> bool:
    """Claim a durable edge and hand it to Celery without losing failures."""
    from spawns.events import FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
    from spawns.models import GameEventOutbox

    now = timezone.now()
    claim_token = uuid.uuid4()
    with transaction.atomic():
        row = (
            GameEventOutbox.objects.select_for_update(of=("self",))
            .filter(
                event_id=outbox_event_id,
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                depends_on_batch_id__isnull=True,
            )
            .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lte=now))
            .first()
        )
        if row is None:
            return False
        claimed_event_data = _event_data_with_leader_snapshot(
            deepcopy(row.data or event_data),
        )
        row.data = deepcopy(claimed_event_data)
        row.claim_token = claim_token
        row.claimed_until = now + timedelta(seconds=FOLLOW_OUTBOX_LEASE_SECONDS)
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.last_error = ""
        row.save(update_fields=[
            "claim_token",
            "claimed_until",
            "attempt_count",
            "last_error",
            "data",
            "modified_ts",
        ])

    try:
        _enqueue_follow_task(
            claimed_event_data,
            outbox_event_id=outbox_event_id,
            claim_token=str(claim_token),
        )
    except Exception as exc:
        GameEventOutbox.objects.filter(
            event_id=outbox_event_id,
            claim_token=claim_token,
        ).update(
            available_ts=now + timedelta(
                seconds=min(300, 2 ** min(8, int(row.attempt_count or 1))),
            ),
            claim_token=None,
            claimed_until=None,
            last_error=str(exc)[:2000],
        )
        logger.exception(
            "Failed to enqueue durable follow movement %s.",
            outbox_event_id,
        )
        return False
    return True


def enqueue_claimed_follow_movement(
    event_data: dict[str, Any],
    *,
    outbox_event_id: str,
    claim_token: str,
) -> None:
    """Hand an edge already leased by the generic outbox flusher to Celery."""
    from spawns.events import FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
    from spawns.models import GameEventOutbox

    with transaction.atomic():
        row = (
            GameEventOutbox.objects.select_for_update(of=("self",))
            .filter(
                event_id=outbox_event_id,
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                claim_token=claim_token,
            )
            .first()
        )
        if row is None:
            return
        claimed_event_data = _event_data_with_leader_snapshot(
            deepcopy(row.data or event_data),
        )
        if row.data != claimed_event_data:
            row.data = deepcopy(claimed_event_data)
            row.save(update_fields=["data", "modified_ts"])

    _enqueue_follow_task(
        claimed_event_data,
        outbox_event_id=outbox_event_id,
        claim_token=claim_token,
    )


def extend_follow_outbox_lease(
    outbox_event_id: str | None,
    claim_token: str | None,
) -> bool:
    if not outbox_event_id:
        return True
    if not claim_token:
        return False
    from spawns.models import GameEventOutbox

    return bool(GameEventOutbox.objects.filter(
        event_id=outbox_event_id,
        claim_token=claim_token,
    ).update(
        claimed_until=timezone.now() + timedelta(
            seconds=FOLLOW_OUTBOX_LEASE_SECONDS,
        ),
    ))


def acknowledge_follow_outbox(
    outbox_event_id: str | None,
    claim_token: str | None,
) -> bool:
    if not outbox_event_id:
        return True
    if not claim_token:
        return False
    from spawns.events import FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
    from spawns.models import GameEventOutbox

    deleted, _ = GameEventOutbox.objects.filter(
        event_id=outbox_event_id,
        event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        claim_token=claim_token,
    ).delete()
    return bool(deleted)


def defer_follow_outbox_retry(
    outbox_event_id: str | None,
    claim_token: str | None,
    *,
    last_error: str,
) -> bool:
    """Release a poison edge to the durable heartbeat with slow backoff.

    The claim token is a fencing token: a delayed task must never release a
    row that a newer heartbeat claim owns. Legacy in-process edges have no
    durable row, so reaching the sweep cap simply ends their bounded chain.
    """
    if not outbox_event_id:
        return True
    if not claim_token:
        return False

    from spawns.events import FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
    from spawns.models import GameEventOutbox

    with transaction.atomic():
        row = (
            GameEventOutbox.objects.select_for_update(of=("self",))
            .filter(
                event_id=outbox_event_id,
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                claim_token=claim_token,
            )
            .first()
        )
        if row is None:
            return False

        claim_attempt = max(1, int(row.attempt_count or 1))
        backoff_seconds = min(
            FOLLOW_OUTBOX_RETRY_MAX_SECONDS,
            FOLLOW_OUTBOX_RETRY_BASE_SECONDS
            * (2 ** min(6, claim_attempt - 1)),
        )
        row.available_ts = timezone.now() + timedelta(
            seconds=backoff_seconds,
        )
        row.claim_token = None
        row.claimed_until = None
        row.last_error = str(last_error or "Follow movement unresolved.")[:2000]
        row.save(update_fields=[
            "available_ts",
            "claim_token",
            "claimed_until",
            "last_error",
            "modified_ts",
        ])
    return True


def schedule_follow_movement(event_data: dict[str, Any]) -> None:
    """Queue a durable private edge after visible leader output is published."""
    normalized = _normalized_event_data(event_data)
    if normalized is None or normalized["depth"] >= MAX_FOLLOW_PROPAGATION_DEPTH:
        return

    from spawns.events import (
        FOLLOW_HAS_FOLLOWERS_KEY,
        FOLLOW_OUTBOX_EVENT_ID_KEY,
    )

    has_followers = event_data.get(FOLLOW_HAS_FOLLOWERS_KEY)
    if has_followers is False:
        return
    outbox_event_id = str(
        event_data.get(FOLLOW_OUTBOX_EVENT_ID_KEY) or ""
    ).strip() or None

    # Legacy/test-created structural events have no durable marker. Keep the
    # safe fallback bounded to one indexed existence query, while production
    # movement paths always carry a durable outbox id.
    if has_followers is None:
        from spawns.models import MovementFollow

        if not MovementFollow.objects.filter(
            **_leader_filter(normalized),
        ).exists():
            return

    def _enqueue() -> None:
        if outbox_event_id:
            _claim_and_enqueue_follow_movement(
                event_data,
                outbox_event_id=outbox_event_id,
            )
        else:
            _enqueue_follow_task(event_data)

    # An ordinary command publishes after commit and executes immediately.
    # Nested Trigger transactions retain the ordering boundary until their
    # durable step commits. Broker failure is absorbed only because the
    # durable row remains available for heartbeat recovery.
    transaction.on_commit(_enqueue, robust=True)


def _leader_filter(normalized: dict[str, Any]) -> dict[str, int]:
    return {
        f"leader_{normalized['actor_type']}_id": normalized["actor_id"],
    }


def _leader_snapshot(normalized: dict[str, Any]) -> dict[str, int] | None:
    """Load the event leader once for an entire bounded follower page."""
    from spawns.models import Mob, Player

    leader_model = Player if normalized["actor_type"] == "player" else Mob
    leader_filter = {
        "pk": normalized["actor_id"],
        "world_id": normalized["runtime_world_id"],
    }
    if normalized["actor_type"] == "player":
        leader_filter["in_game"] = True
    else:
        leader_filter["is_pending_deletion"] = False
    row = leader_model.objects.filter(**leader_filter).values(
        "room_id",
        "follow_move_sequence",
    ).first()
    if row is None:
        return None
    return {
        "room_id": int(row["room_id"] or 0),
        "sequence": int(row["follow_move_sequence"] or 0),
    }


def _snapshotted_leader_state(
    event_data: dict[str, Any],
) -> dict[str, int] | None:
    if (
        FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY not in event_data
        or FOLLOW_LEADER_ROOM_SNAPSHOT_KEY not in event_data
    ):
        return None
    try:
        sequence = int(event_data[FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY])
        room_id = int(event_data[FOLLOW_LEADER_ROOM_SNAPSHOT_KEY])
    except (TypeError, ValueError):
        return None
    if sequence < 0 or room_id < 0:
        return None
    return {"sequence": sequence, "room_id": room_id}


def _event_data_with_leader_snapshot(
    event_data: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one leader-state decision for an edge's complete drain.

    A fan-out may span many pages and durable recovery attempts.  Persisting
    the first validation snapshot in the edge payload prevents followers on
    later pages from observing a different decision after the leader moves.
    """
    prepared = deepcopy(event_data) if isinstance(event_data, dict) else {}
    if _snapshotted_leader_state(prepared) is not None:
        return prepared
    normalized = _normalized_event_data(prepared)
    if normalized is None:
        return prepared
    snapshot = _leader_snapshot(normalized) or {"sequence": 0, "room_id": 0}
    prepared[FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY] = snapshot["sequence"]
    prepared[FOLLOW_LEADER_ROOM_SNAPSHOT_KEY] = snapshot["room_id"]
    return prepared


def propagate_follow_movement_batch(
    event_data: dict[str, Any],
    *,
    after_id: int = 0,
    batch_size: int = FOLLOW_FANOUT_BATCH_SIZE,
) -> FollowMovementBatchResult:
    """Attempt a bounded page of direct followers for one leader edge."""
    normalized = _normalized_event_data(event_data)
    if normalized is None or normalized["depth"] >= MAX_FOLLOW_PROPAGATION_DEPTH:
        return FollowMovementBatchResult(0, None, None)

    from spawns.handlers.registry import dispatch_command
    from spawns.models import MovementFollow

    limit = max(1, min(int(batch_size or 1), FOLLOW_FANOUT_BATCH_SIZE))
    relationships = list(
        MovementFollow.objects.filter(
            **_leader_filter(normalized),
            id__gt=max(0, int(after_id or 0)),
            last_processed_sequence__lt=normalized["sequence"],
        )
        .order_by("id")[: limit + 1]
    )
    has_more = len(relationships) > limit
    page = relationships[:limit]
    if not page:
        return FollowMovementBatchResult(0, None, None)

    # Most movement edges have no direct followers.  Query links first so
    # that common case costs one indexed lookup and no redundant leader read.
    leader_snapshot = _snapshotted_leader_state(event_data)
    if leader_snapshot is None:
        # Legacy/test-created edges do not have a durable payload. Preserve
        # their compatibility behavior while all production edges carry the
        # immutable snapshot installed by the enqueue boundary above.
        leader_snapshot = _leader_snapshot(normalized)
    if leader_snapshot is None:
        return FollowMovementBatchResult(0, None, None)
    if leader_snapshot["sequence"] < normalized["sequence"]:
        return FollowMovementBatchResult(0, None, max(0, int(after_id or 0)))

    processed = 0
    retry_required = False

    for relationship in page:
        last_processed = int(relationship.last_processed_sequence or 0)
        if normalized["sequence"] <= last_processed:
            continue
        if normalized["sequence"] != last_processed + 1:
            retry_required = True
            continue

        result_sink: list[str] = []
        follow_context = {
            **normalized,
            "relationship_id": relationship.id,
            "leader_sequence_snapshot": leader_snapshot["sequence"],
            "leader_room_id_snapshot": leader_snapshot["room_id"],
            "_result_sink": result_sink,
        }
        try:
            dispatch_command(
                "move",
                payload={
                    "direction": normalized["direction"],
                    FOLLOW_MOVEMENT_PAYLOAD_KEY: follow_context,
                },
                actor_type="player",
                actor_id=relationship.follower_id,
            )
        except Exception:
            # One corrupt or transient follower must not starve every later
            # relationship in this page. It remains unresolved for the next
            # single sweep while the rest of the page progresses.
            retry_required = True
            logger.exception(
                "Failed follow movement %s for relationship %s.",
                normalized["movement_id"],
                relationship.id,
            )
            continue
        if "deferred" in result_sink:
            retry_required = True
            continue
        processed += 1

    next_after_id = page[-1].id if has_more and page else None
    return FollowMovementBatchResult(
        processed=processed,
        next_after_id=next_after_id,
        retry_after_id=0 if retry_required else None,
    )


def has_unresolved_follow_movement(event_data: dict[str, Any]) -> bool:
    normalized = _normalized_event_data(event_data)
    if normalized is None:
        return False
    from spawns.models import MovementFollow

    return MovementFollow.objects.filter(
        **_leader_filter(normalized),
        last_processed_sequence__lt=normalized["sequence"],
    ).exists()


def _relationship_matches(relationship, context: dict[str, Any]) -> bool:
    actor_type = context["actor_type"]
    return (
        getattr(relationship, f"leader_{actor_type}_id", None)
        == context["actor_id"]
    )


def lock_follow_movement_attempt(*, player, context: dict[str, Any]):
    """Lock and validate one edge after the ordinary movement Player lock."""
    normalized = _normalized_event_data({
        "movement_id": context.get("movement_id"),
        "root_id": context.get("root_id"),
        "depth": context.get("depth"),
        "actor": {
            "type": context.get("actor_type"),
            "id": context.get("actor_id"),
            "name": context.get("actor_name"),
            "is_invisible": context.get("actor_is_invisible", False),
        },
        "runtime_world_id": context.get("runtime_world_id"),
        "origin_room_id": context.get("origin_room_id"),
        "destination_room_id": context.get("destination_room_id"),
        "direction": context.get("direction"),
        "source": context.get("source"),
        "sequence": context.get("sequence"),
    })
    if normalized is None:
        raise FollowMovementIgnored

    from spawns.models import MovementFollow

    try:
        relationship = (
            MovementFollow.objects.select_for_update(
                of=("self",),
                nowait=True,
            )
            .get(
                pk=int(context.get("relationship_id")),
                follower_id=player.id,
            )
        )
    except OperationalError as exc:
        # A concurrent follow/unfollow should defer just this edge rather
        # than hold an entire fan-out page behind a row-lock wait.
        raise FollowMovementDeferred from exc
    except (MovementFollow.DoesNotExist, TypeError, ValueError):
        raise FollowMovementIgnored
    if not _relationship_matches(relationship, normalized):
        raise FollowMovementIgnored

    last_processed = int(relationship.last_processed_sequence or 0)
    if normalized["sequence"] <= last_processed:
        raise FollowMovementIgnored
    if normalized["sequence"] != last_processed + 1:
        raise FollowMovementDeferred

    leader_name = normalized["actor_name"] or "your leader"
    if (
        not player.in_game
        or player.world_id != normalized["runtime_world_id"]
        or player.room_id != normalized["origin_room_id"]
    ):
        raise FollowMovementBlocked(
            f"You are no longer close enough to follow {leader_name}.",
            code="follow_origin_changed",
        )

    try:
        leader_sequence = int(context.get("leader_sequence_snapshot"))
        leader_room_id = int(context.get("leader_room_id_snapshot"))
    except (TypeError, ValueError):
        raise FollowMovementIgnored
    if leader_sequence < normalized["sequence"]:
        raise FollowMovementDeferred
    if (
        leader_sequence == normalized["sequence"]
        and leader_room_id != normalized["destination_room_id"]
    ):
        raise FollowMovementBlocked(
            f"You lose track of {leader_name}.",
            code="follow_leader_moved",
        )
    return relationship, normalized


def complete_follow_movement_attempt(relationship, *, sequence: int) -> None:
    relationship.last_processed_sequence = int(sequence)
    relationship.save(update_fields=["last_processed_sequence"])


def record_failed_follow_movement(
    *,
    player_id: int,
    context: dict[str, Any],
    message: str,
    code: str,
    data: dict[str, Any] | None = None,
    connection_id: str | None = None,
) -> str:
    """Atomically advance a failed edge and persist its private feedback."""
    from spawns.models import MovementFollow

    try:
        relationship_id = int(context.get("relationship_id"))
        sequence = int(context.get("sequence"))
    except (TypeError, ValueError):
        return FOLLOW_FAILURE_IGNORED
    try:
        with transaction.atomic():
            relationship = (
                MovementFollow.objects.select_for_update(
                    of=("self",),
                    nowait=True,
                )
                .filter(pk=relationship_id, follower_id=player_id)
                .first()
            )
            if (
                relationship is None
                or not _relationship_matches(relationship, context)
            ):
                return FOLLOW_FAILURE_IGNORED
            last_processed = int(relationship.last_processed_sequence or 0)
            if sequence != last_processed + 1:
                return FOLLOW_FAILURE_IGNORED
            relationship.last_processed_sequence = sequence
            relationship.save(update_fields=["last_processed_sequence"])

            # The parent follow-control row may be acknowledged as soon as
            # this relationship advances. Persist the player's explanation in
            # the same transaction so a worker or websocket failure cannot
            # leave the edge resolved while silently losing its feedback.
            from spawns.events import (
                PRIVATE_CONTROL_EVENT_KEY,
                GameEvent,
                persist_follow_dependent_game_events,
            )

            event_data = {
                "error": message,
                "code": code,
                **(deepcopy(data) if isinstance(data, dict) else {}),
                # Direct command errors do not drive Trigger or quest
                # subscriptions. Preserve that behavior through the outbox.
                PRIVATE_CONTROL_EVENT_KEY: True,
            }
            actor_key = f"player.{player_id}"
            remaining_events = persist_follow_dependent_game_events(
                [
                    GameEvent(
                        type="cmd.move.error",
                        recipients=[actor_key],
                        data=event_data,
                        text=message,
                        connection_id=connection_id,
                    )
                ],
                force=True,
                actor_key=actor_key,
                connection_id=connection_id,
            )
            if remaining_events:
                raise RuntimeError(
                    "Required follow failure event was not persisted."
                )
    except OperationalError:
        return FOLLOW_FAILURE_DEFERRED
    return FOLLOW_FAILURE_RECORDED


def follow_failure_message(context: dict[str, Any], message: str) -> str:
    leader_name = str(context.get("actor_name") or "your leader").strip()
    return f"You cannot follow {leader_name}: {message}"
