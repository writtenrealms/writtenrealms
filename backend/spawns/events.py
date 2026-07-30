from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Callable, Iterable, Iterator, Sequence
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.trigger_steps import (
    SCRIPT_COMMAND_DEPTH_KEY,
    SCRIPT_COMMAND_PROVENANCE_KEY,
)
from fastapi_app.game_ws import publish_to_player
from spawns.request_segments import normalize_request_segment


logger = logging.getLogger(__name__)

FINAL_TRANSFER_ENTER_KEY = "_final_transfer_enter"
TRANSFER_LOCATION_SEQUENCE_KEY = "_transfer_location_sequence"
TRANSFER_RUNTIME_WORLD_KEY = "_transfer_runtime_world_id"
TRANSFER_ENTER_EVENT_TYPE = "notification./transfer.enter"
PRIVATE_CONTROL_EVENT_KEY = "_private_control_event"
COMMAND_REQUEST_COMPLETED_EVENT_TYPE = "cmd.request.completed"
_TRIGGER_REQUEST_EVENT_TYPES = frozenset({
    "cmd.trigger.accepted",
    "cmd.trigger.completed",
    "cmd.trigger.cancelled",
    "cmd.trigger.rejected",
})

_captured_game_events: ContextVar[list[GameEvent] | None] = ContextVar(
    "captured_game_events",
    default=None,
)
_inherited_script_command_depth: ContextVar[int] = ContextVar(
    "inherited_script_command_depth",
    default=0,
)
_command_request_scopes: ContextVar[tuple[CommandRequestScope, ...]] = (
    ContextVar(
        "command_request_scopes",
        default=(),
    )
)


@dataclass
class CommandRequestScope:
    """In-process result ownership for one client command receipt segment."""

    request_id: str
    request_segment: str
    actor_key: str
    terminal_seen: bool = False
    deferred: bool = False
    delegated: bool = False

    @property
    def needs_completion_event(self) -> bool:
        return not (
            self.terminal_seen
            or self.deferred
            or self.delegated
        )


@dataclass(frozen=True)
class CommandRequestScopeHandle:
    scope: CommandRequestScope | None
    owner: bool = False

    @property
    def needs_completion_event(self) -> bool:
        return bool(
            self.owner
            and self.scope is not None
            and self.scope.needs_completion_event
        )


def _normalized_request_id(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return ""


@contextmanager
def command_request_scope(
    *,
    request_id,
    request_segment,
    actor_key: str,
    enabled: bool = True,
) -> Iterator[CommandRequestScopeHandle]:
    """
    Track one client receipt while its command dispatch runs in-process.

    Alias and history redispatches share the same scope. Command-chain child
    segments get independent scopes and mark their parent as a container so
    the root cannot settle before the complete segment plan is processed.
    """
    normalized_request_id = (
        _normalized_request_id(request_id)
        if enabled
        else ""
    )
    if not normalized_request_id:
        yield CommandRequestScopeHandle(scope=None)
        return

    normalized_segment = normalize_request_segment(request_segment)
    scopes = _command_request_scopes.get()
    active_scope = scopes[-1] if scopes else None
    scope = (
        active_scope
        if (
            active_scope is not None
            and active_scope.request_id == normalized_request_id
            and active_scope.request_segment == normalized_segment
            and active_scope.actor_key == actor_key
        )
        else None
    )
    owner = scope is None
    if scope is None:
        scope = CommandRequestScope(
            request_id=normalized_request_id,
            request_segment=normalized_segment,
            actor_key=actor_key,
        )
        for parent in scopes:
            if (
                parent.request_id == normalized_request_id
                and parent.actor_key == actor_key
                and parent.request_segment != normalized_segment
            ):
                parent.delegated = True

    token = _command_request_scopes.set((*scopes, scope))
    try:
        yield CommandRequestScopeHandle(scope=scope, owner=owner)
    finally:
        _command_request_scopes.reset(token)


def command_request_completed_message(
    scope: CommandRequestScope,
) -> dict:
    """Build a private terminal control for a command with no visible result."""
    return {
        "type": COMMAND_REQUEST_COMPLETED_EVENT_TYPE,
        "data": {
            "request_id": scope.request_id,
            "request_segment": scope.request_segment,
            "status": "completed",
        },
    }


def _active_command_request_scope(
    actor_key: str,
) -> CommandRequestScope | None:
    scopes = _command_request_scopes.get()
    if not scopes or scopes[-1].actor_key != actor_key:
        return None
    return scopes[-1]


def defer_actor_command_result(actor_key: str) -> None:
    """Give an asynchronous lifecycle ownership of the active receipt."""
    scope = _active_command_request_scope(actor_key)
    if scope is not None:
        scope.deferred = True


def _is_terminal_command_event(event_type: str) -> bool:
    return (
        event_type.startswith("cmd.")
        and (
            event_type.endswith(".success")
            or event_type.endswith(".error")
        )
    )


def correlate_actor_command_message(
    message: dict,
    *,
    actor_key: str,
) -> dict:
    """
    Add receipt identity only to the actor's terminal command response.

    This operates on the final recipient message rather than mutating a shared
    ``GameEvent``, so room and third-party notifications cannot inherit the
    private correlation fields.
    """
    scope = _active_command_request_scope(actor_key)
    if scope is None:
        return message

    event_type = str(message.get("type") or "").strip().lower()
    event_data = message.get("data") or {}
    event_request_id = _normalized_request_id(
        event_data.get("request_id")
    )
    event_request_segment = normalize_request_segment(
        event_data.get("request_segment")
    )
    if event_request_id and (
        event_request_id != scope.request_id
        or event_request_segment != scope.request_segment
    ):
        # Durable async actions can finish while an unrelated command is
        # running. Their stored identity is authoritative and must not settle
        # the newer command's receipt.
        return message

    if event_type in _TRIGGER_REQUEST_EVENT_TYPES:
        scope.deferred = True
        return message
    if event_type.startswith("cmd.") and event_type.endswith(".started"):
        # A repeated prepared action is an immediate answer to the newer
        # request ("already preparing"), not ownership of the original
        # action's eventual result.
        if not event_data.get("repeated"):
            scope.deferred = True
        return message
    if (
        event_type.startswith("cmd.")
        and event_type.endswith(".cancelled")
        and event_request_id
    ):
        scope.terminal_seen = True
        return message
    if not _is_terminal_command_event(event_type):
        return message

    scope.terminal_seen = True
    correlated = dict(message)
    correlated["data"] = {
        **event_data,
        "request_id": scope.request_id,
        "request_segment": scope.request_segment,
    }
    return correlated


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


def _with_inherited_script_command_depth(
    event: GameEvent,
    depth: int,
) -> GameEvent:
    try:
        event_depth = max(
            0,
            int(event.data.get(SCRIPT_COMMAND_DEPTH_KEY) or 0),
        )
    except (TypeError, ValueError):
        event_depth = 0
    if event_depth >= depth:
        return event
    return GameEvent(
        type=event.type,
        recipients=event.recipients,
        data={
            **event.data,
            SCRIPT_COMMAND_DEPTH_KEY: depth,
        },
        text=event.text,
        group=event.group,
        connection_id=event.connection_id,
    )


def _with_final_transfer_enter_markers(
    events: list[GameEvent],
) -> list[GameEvent]:
    """Mark only the last transfer arrival per actor in one event batch."""
    last_index_by_actor: dict[str, int] = {}
    for index, event in enumerate(events):
        if str(event.type or "").strip().lower() != TRANSFER_ENTER_EVENT_TYPE:
            continue
        actor = event.data.get("actor")
        actor_key = actor.get("key") if isinstance(actor, dict) else None
        if actor_key:
            last_index_by_actor[str(actor_key)] = index
    if not last_index_by_actor:
        return events

    marked_events: list[GameEvent] = []
    for index, event in enumerate(events):
        if str(event.type or "").strip().lower() != TRANSFER_ENTER_EVENT_TYPE:
            marked_events.append(event)
            continue
        actor = event.data.get("actor")
        actor_key = actor.get("key") if isinstance(actor, dict) else None
        if not actor_key:
            marked_events.append(event)
            continue
        marked_events.append(GameEvent(
            type=event.type,
            recipients=event.recipients,
            data={
                **event.data,
                FINAL_TRANSFER_ENTER_KEY: (
                    last_index_by_actor[str(actor_key)] == index
                ),
            },
            text=event.text,
            group=event.group,
            connection_id=event.connection_id,
        ))
    return marked_events


@contextmanager
def inherit_script_command_depth(depth: int) -> Iterator[None]:
    """Attach a bounded reaction-command depth to events published in scope."""
    try:
        normalized_depth = max(0, int(depth))
    except (TypeError, ValueError):
        normalized_depth = 0
    token = _inherited_script_command_depth.set(normalized_depth)
    try:
        yield
    finally:
        _inherited_script_command_depth.reset(token)


@contextmanager
def capture_game_events() -> Iterator[list[GameEvent]]:
    """
    Capture events from audited command handlers without publishing them.

    Trigger steps use this boundary to keep command output inside the step
    transaction. The caller is responsible for enqueuing the returned events
    only after every action in the step succeeds.
    """
    captured: list[GameEvent] = []
    token = _captured_game_events.set(captured)
    try:
        yield captured
    finally:
        _captured_game_events.reset(token)


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
    event_list = _with_final_transfer_enter_markers(list(events))
    inherited_depth = _inherited_script_command_depth.get()
    if inherited_depth:
        event_list = [
            _with_inherited_script_command_depth(event, inherited_depth)
            for event in event_list
        ]
    capture_sink = _captured_game_events.get()
    if capture_sink is not None:
        capture_sink.extend(event_list)
        return

    for event in event_list:
        message = event.to_message()
        if (
            SCRIPT_COMMAND_DEPTH_KEY in event.data
            or FINAL_TRANSFER_ENTER_KEY in event.data
            or TRANSFER_LOCATION_SEQUENCE_KEY in event.data
            or TRANSFER_RUNTIME_WORLD_KEY in event.data
            or PRIVATE_CONTROL_EVENT_KEY in event.data
            or isinstance(
                event.data.get(SCRIPT_COMMAND_PROVENANCE_KEY),
                dict,
            )
        ):
            # Script depth and provenance are durable server metadata used for
            # routing and auditing, not player-facing command payload.
            public_data = deepcopy(event.data)
            public_data.pop(SCRIPT_COMMAND_DEPTH_KEY, None)
            public_data.pop(SCRIPT_COMMAND_PROVENANCE_KEY, None)
            public_data.pop(FINAL_TRANSFER_ENTER_KEY, None)
            public_data.pop(TRANSFER_LOCATION_SEQUENCE_KEY, None)
            public_data.pop(TRANSFER_RUNTIME_WORLD_KEY, None)
            public_data.pop(PRIVATE_CONTROL_EVENT_KEY, None)
            message["data"] = public_data
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
                correlate_actor_command_message(
                    message,
                    actor_key=recipient,
                ),
                connection_id=recipient_connection_id,
            )

        # Speech/social output forced by a Trigger is not voluntary player
        # input. Structural transfer events are different: the character
        # really changed rooms, so destination reactions and location quest
        # refreshes must observe that committed state change.
        is_trigger_step_event = isinstance(
            event.data.get(SCRIPT_COMMAND_PROVENANCE_KEY),
            dict,
        )
        is_private_control_event = bool(
            event.data.get(PRIVATE_CONTROL_EVENT_KEY)
        )
        event_type = str(event.type or "").strip().lower()
        dispatch_trigger_event = (
            not is_private_control_event
            and (
                not is_trigger_step_event
                or event_type == "notification./transfer.enter"
            )
        )
        dispatch_quest_event = (
            not is_private_control_event
            and (
                not is_trigger_step_event
                or event_type == "affect.transfer"
            )
        )

        # Late imports avoid trigger/state payload import cycles during app
        # bootstrap.
        if dispatch_trigger_event:
            from spawns.trigger_subscriptions import (
                dispatch_trigger_subscriptions_for_event,
            )

            dispatch_trigger_subscriptions_for_event(
                event_type=event.type,
                event_data=event.data,
                actor_key=actor_key,
                connection_id=connection_id,
            )

        if dispatch_quest_event:
            from quests.subscriptions import (
                dispatch_quest_subscriptions_for_event,
            )

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
            publisher([
                GameEvent(
                    type=row.event_type,
                    recipients=list(row.recipients or []),
                    data=deepcopy(row.data or {}),
                    text=row.text,
                    group=row.group,
                    connection_id=row.connection_id,
                )
                for row in claimed_rows
            ])
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
