from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Callable, Iterable, Sequence
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fastapi_app.game_ws import publish_to_player


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameEvent:
    """Serializable event to publish to one or more players."""
    type: str
    data: dict
    recipients: Sequence[str] = field(default_factory=tuple)
    text: str | None = None
    group: str | None = None
    connection_id: str | None = None

    def to_message(self) -> dict:
        message = {"type": self.type, "data": self.data}
        if self.text:
            message["text"] = self.text
        if self.group:
            message["group"] = self.group
        return message


def publish_events(
    events: Iterable[GameEvent],
    *,
    actor_key: str | None = None,
    connection_id: str | None = None,
) -> None:
    """
    Publish a list of events. If actor_key/connection_id is provided, only
    events targeting the actor will be pinned to that connection.
    """
    for event in events:
        message = event.to_message()
        for recipient in event.recipients:
            recipient_connection_id = event.connection_id
            if (
                recipient_connection_id is None
                and actor_key
                and connection_id
                and recipient == actor_key
            ):
                recipient_connection_id = connection_id
            publish_to_player(
                recipient,
                message,
                connection_id=recipient_connection_id,
            )

        # Late import avoids trigger/state payload import cycles during app bootstrap.
        from spawns.trigger_subscriptions import dispatch_trigger_subscriptions_for_event

        dispatch_trigger_subscriptions_for_event(
            event_type=event.type,
            event_data=event.data,
            actor_key=actor_key,
            connection_id=connection_id,
        )

        from quests.subscriptions import dispatch_quest_subscriptions_for_event

        dispatch_quest_subscriptions_for_event(
            event_type=event.type,
            event_data=event.data,
            actor_key=actor_key,
            connection_id=connection_id,
        )


def enqueue_game_events(events: Iterable[GameEvent]) -> int:
    """Persist events in the caller's transaction for later delivery."""
    from spawns.models import GameEventOutbox

    rows = []
    batch_id = uuid.uuid4()
    for sequence, event in enumerate(events):
        event_id = uuid.uuid4()
        data = deepcopy(event.data)
        data["_event_id"] = str(event_id)
        rows.append(
            GameEventOutbox(
                event_id=event_id,
                batch_id=batch_id,
                sequence=sequence,
                event_type=event.type,
                data=data,
                recipients=list(event.recipients),
                text=event.text,
                group=event.group,
                connection_id=event.connection_id,
            )
        )
    if rows:
        GameEventOutbox.objects.bulk_create(rows)
    return len(rows)


def flush_game_event_outbox(
    *,
    limit: int = 500,
    publisher: Callable[..., None] | None = None,
    now=None,
) -> int:
    """Claim due batches, publish outside locks, and acknowledge successes."""
    from spawns.models import GameEventOutbox

    publisher = publisher or publish_events
    delivered = 0
    batches_examined = 0
    row_limit = max(1, int(limit or 1))
    while delivered < row_limit and batches_examined < row_limit:
        claim_now = now or timezone.now()
        claim_token = uuid.uuid4()
        with transaction.atomic():
            first = (
                GameEventOutbox.objects.select_for_update(skip_locked=True)
                .filter(sequence=0, available_ts__lte=claim_now)
                .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lte=claim_now))
                .order_by("created_ts", "batch_id", "sequence", "id")
                .first()
            )
            if first is None:
                break
            claimed_rows = list(
                GameEventOutbox.objects.select_for_update()
                .filter(batch_id=first.batch_id)
                .order_by("sequence", "id")
            )
            if any(
                row.claimed_until and row.claimed_until > claim_now
                for row in claimed_rows
            ):
                batches_examined += 1
                continue
            lease_until = claim_now + timedelta(minutes=5)
            for row in claimed_rows:
                row.claim_token = claim_token
                row.claimed_until = lease_until
                row.attempt_count += 1
            GameEventOutbox.objects.bulk_update(
                claimed_rows,
                ["claim_token", "claimed_until", "attempt_count"],
            )

        batches_examined += 1
        try:
            for row in claimed_rows:
                publisher(
                    [
                        GameEvent(
                            type=row.event_type,
                            recipients=list(row.recipients or []),
                            data=deepcopy(row.data or {}),
                            text=row.text,
                            group=row.group,
                            connection_id=row.connection_id,
                        )
                    ]
                )
        except Exception as exc:
            backoff_seconds = min(300, 2 ** min(8, max(row.attempt_count for row in claimed_rows)))
            GameEventOutbox.objects.filter(
                batch_id=first.batch_id,
                claim_token=claim_token,
            ).update(
                available_ts=claim_now + timedelta(seconds=backoff_seconds),
                claim_token=None,
                claimed_until=None,
                last_error=str(exc)[:2000],
            )
            logger.exception("Failed to publish game event outbox batch %s", first.batch_id)
            continue

        deleted, _ = GameEventOutbox.objects.filter(
            batch_id=first.batch_id,
            claim_token=claim_token,
        ).delete()
        delivered += deleted
    return delivered
