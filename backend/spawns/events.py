from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from fastapi_app.game_ws import publish_to_player


@dataclass(frozen=True)
class GameEvent:
    """Serializable event to publish to one or more players."""
    type: str
    data: dict
    recipients: Sequence[str] = field(default_factory=tuple)
    text: str | None = None
    connection_id: str | None = None

    def to_message(self) -> dict:
        message = {"type": self.type, "data": self.data}
        if self.text:
            message["text"] = self.text
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

        # Optionally forward selected events to an external AI sidecar.
        from spawns.ai_sidecar import maybe_enqueue_ai_sidecar_event_forwarding

        maybe_enqueue_ai_sidecar_event_forwarding(
            event_type=event.type,
            event_data=event.data,
            actor_key=actor_key,
        )
