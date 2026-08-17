from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
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
PLAYER_ROOM_ENTER_EVENT_TYPE = "lifecycle.player.room.enter"
PLAYER_ROOM_ENTER_EMITTED_KEY = "_player_room_enter_emitted"
PRIVATE_CONTROL_EVENT_KEY = "_private_control_event"
FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE = "private.follow.directional_move"
FOLLOW_OUTBOX_EVENT_ID_KEY = "_follow_outbox_event_id"
FOLLOW_HAS_FOLLOWERS_KEY = "_follow_has_followers"
COMMAND_REQUEST_COMPLETED_EVENT_TYPE = "cmd.request.completed"
COMMAND_RECEIPT_STATUS_COMPLETED = "completed"
COMMAND_RECEIPT_STATUS_FAILED = "failed"
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
            "receipt_status": COMMAND_RECEIPT_STATUS_COMPLETED,
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
        event_type == COMMAND_REQUEST_COMPLETED_EVENT_TYPE
        or event_type == "cmd.trigger.rejected"
        or (
            event_type.startswith("cmd.")
            and (
                event_type.endswith(".success")
                or event_type.endswith(".error")
                or event_type.endswith(".cancelled")
                or event_type.endswith(".completed")
            )
        )
    )


def _with_terminal_command_receipt(
    message: dict,
    *,
    event_type: str,
    request_id: str,
    request_segment: str,
) -> dict:
    """
    Stamp the actor-authoritative terminal result independently of its domain
    outcome.

    A ``cmd.*.error`` or ``cmd.*.cancelled`` normally means the server
    processed the command and declined or stopped the requested game action.
    Callers that caught a genuine processing failure must set
    ``receipt_status=failed`` explicitly; this boundary preserves it.
    """
    if not request_id or not _is_terminal_command_event(event_type):
        return message

    event_data = message.get("data") or {}
    explicit_status = str(
        event_data.get("receipt_status") or ""
    ).strip().lower()
    receipt_status = (
        COMMAND_RECEIPT_STATUS_FAILED
        if explicit_status == COMMAND_RECEIPT_STATUS_FAILED
        else COMMAND_RECEIPT_STATUS_COMPLETED
    )
    correlated = dict(message)
    correlated["data"] = {
        **event_data,
        "request_id": request_id,
        "request_segment": request_segment,
        "receipt_status": receipt_status,
    }
    return correlated


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
    event_type = str(message.get("type") or "").strip().lower()
    event_data = message.get("data") or {}
    event_request_id = _normalized_request_id(
        event_data.get("request_id")
    )
    event_request_segment = normalize_request_segment(
        event_data.get("request_segment")
    )

    scope = _active_command_request_scope(actor_key)
    if scope is None:
        # Durable asynchronous command results carry their stored identity
        # outside the original in-process ContextVar scope.
        return _with_terminal_command_receipt(
            message,
            event_type=event_type,
            request_id=event_request_id,
            request_segment=event_request_segment,
        )

    if event_request_id and (
        event_request_id != scope.request_id
        or event_request_segment != scope.request_segment
    ):
        # Durable async actions can finish while an unrelated command is
        # running. Their stored identity is authoritative and must not settle
        # the newer command's receipt.
        return _with_terminal_command_receipt(
            message,
            event_type=event_type,
            request_id=event_request_id,
            request_segment=event_request_segment,
        )

    if event_type in _TRIGGER_REQUEST_EVENT_TYPES:
        scope.deferred = True
        return _with_terminal_command_receipt(
            message,
            event_type=event_type,
            request_id=event_request_id,
            request_segment=event_request_segment,
        )
    if event_type.startswith("cmd.") and event_type.endswith(".started"):
        # A repeated prepared action is an immediate answer to the newer
        # request ("already preparing"), not ownership of the original
        # action's eventual result.
        if not event_data.get("repeated"):
            scope.deferred = True
        return message
    if not _is_terminal_command_event(event_type):
        return message

    scope.terminal_seen = True
    return _with_terminal_command_receipt(
        message,
        event_type=event_type,
        request_id=scope.request_id,
        request_segment=scope.request_segment,
    )


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


def player_room_enter_event(
    *,
    player,
    origin_room_id: int | None,
    destination_room_id: int,
    source: str,
    direction: str | None = None,
) -> GameEvent:
    """Build the structural event for one committed player-room arrival."""
    data = {
        "actor": {"key": player.key},
        "origin_room": (
            {"id": int(origin_room_id)}
            if origin_room_id is not None
            else None
        ),
        "destination_room": {"id": int(destination_room_id)},
        "runtime_world_id": int(player.world_id),
        "location_sequence": int(player.location_sequence or 0),
        "source": str(source or ""),
    }
    if direction:
        data["direction"] = str(direction)
    return GameEvent(
        type=PLAYER_ROOM_ENTER_EVENT_TYPE,
        recipients=[],
        data=data,
    )


def follow_directional_move_event(
    *,
    actor,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    source: str,
    root_id: str | None = None,
    depth: int = 0,
) -> GameEvent:
    """Build one private, sequenced edge for movement-follow propagation."""
    actor_key = str(actor.key)
    actor_type, separator, raw_actor_id = actor_key.partition(".")
    if separator != "." or actor_type not in {"player", "mob"}:
        raise ValueError("Follow movement actors must be players or mobs.")
    return GameEvent(
        type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        recipients=[],
        data={
            PRIVATE_CONTROL_EVENT_KEY: True,
            "movement_id": str(uuid.uuid4()),
            "root_id": str(root_id or uuid.uuid4()),
            "depth": max(0, int(depth or 0)),
            "actor": {
                "key": actor_key,
                "type": actor_type,
                "id": int(raw_actor_id),
                "name": str(getattr(actor, "name", "") or ""),
                # This is the visibility snapshot at the committed movement
                # edge. A later toggle must not reveal or conceal a route
                # retroactively while the durable task is waiting.
                "is_invisible": bool(
                    getattr(actor, "is_invisible", False)
                ),
            },
            "runtime_world_id": int(actor.world_id),
            "origin_room_id": int(origin_room_id),
            "destination_room_id": int(destination_room_id),
            "direction": str(direction or "").strip().lower(),
            "source": str(source or "").strip().lower(),
            "sequence": int(actor.follow_move_sequence or 0),
        },
    )


def durable_follow_directional_move_events(
    events: Iterable[GameEvent],
) -> list[GameEvent]:
    """Persist followed movement edges in the caller's transaction.

    Follower presence is resolved in at most one indexed query per leader
    type for the whole input batch.  Each durable edge gets its own outbox
    batch so acknowledging one leader can never strand a later sequence row.
    Events already prepared by this function are returned unchanged.
    """
    event_list = list(events)
    pending = [
        event
        for event in event_list
        if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
        and FOLLOW_HAS_FOLLOWERS_KEY not in event.data
    ]
    if not pending:
        return event_list

    from spawns.models import GameEventOutbox, MovementFollow
    from spawns.following import MAX_FOLLOW_PROPAGATION_DEPTH

    player_ids: set[int] = set()
    mob_ids: set[int] = set()
    for event in pending:
        actor = event.data.get("actor") or {}
        try:
            actor_id = int(actor.get("id"))
        except (TypeError, ValueError):
            continue
        if actor.get("type") == "player":
            player_ids.add(actor_id)
        elif actor.get("type") == "mob":
            mob_ids.add(actor_id)

    followed_players = set()
    followed_mobs = set()
    if player_ids:
        followed_players = set(
            MovementFollow.objects.filter(
                leader_player_id__in=player_ids,
            ).values_list("leader_player_id", flat=True).distinct()
        )
    if mob_ids:
        followed_mobs = set(
            MovementFollow.objects.filter(
                leader_mob_id__in=mob_ids,
            ).values_list("leader_mob_id", flat=True).distinct()
        )

    prepared_by_identity: dict[int, GameEvent] = {}
    outbox_rows = []
    for event in pending:
        actor = event.data.get("actor") or {}
        actor_type = str(actor.get("type") or "").strip().lower()
        try:
            actor_id = int(actor.get("id"))
        except (TypeError, ValueError):
            actor_id = 0
        try:
            depth = max(0, int(event.data.get("depth") or 0))
        except (TypeError, ValueError):
            depth = MAX_FOLLOW_PROPAGATION_DEPTH
        has_followers = depth < MAX_FOLLOW_PROPAGATION_DEPTH and (
            actor_id in followed_players
            if actor_type == "player"
            else actor_id in followed_mobs
            if actor_type == "mob"
            else False
        )
        data = {
            **event.data,
            FOLLOW_HAS_FOLLOWERS_KEY: has_followers,
        }
        if has_followers:
            event_id = uuid.uuid4()
            data[FOLLOW_OUTBOX_EVENT_ID_KEY] = str(event_id)
            # GameEventOutbox batches are discovered by sequence=0.  A unique
            # batch per follow edge makes completion acknowledgement local.
            outbox_rows.append(
                GameEventOutbox(
                    event_id=event_id,
                    batch_id=uuid.uuid4(),
                    sequence=0,
                    event_type=event.type,
                    data=deepcopy(data),
                    recipients=[],
                    text=None,
                    group=None,
                    connection_id=None,
                )
            )
        prepared_by_identity[id(event)] = replace(event, data=data)

    if outbox_rows:
        GameEventOutbox.objects.bulk_create(outbox_rows)
    return [prepared_by_identity.get(id(event), event) for event in event_list]


def durable_follow_directional_move_event(event: GameEvent) -> GameEvent:
    """Single-edge convenience wrapper around the batched durable recorder."""
    return durable_follow_directional_move_events([event])[0]


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

    follow_movement_data: list[dict] = []
    for event in event_list:
        event_type = str(event.type or "").strip().lower()
        message = event.to_message()
        if (
            SCRIPT_COMMAND_DEPTH_KEY in event.data
            or FINAL_TRANSFER_ENTER_KEY in event.data
            or TRANSFER_LOCATION_SEQUENCE_KEY in event.data
            or TRANSFER_RUNTIME_WORLD_KEY in event.data
            or PLAYER_ROOM_ENTER_EMITTED_KEY in event.data
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
            public_data.pop(PLAYER_ROOM_ENTER_EMITTED_KEY, None)
            public_data.pop(PRIVATE_CONTROL_EVENT_KEY, None)
            public_data.pop(FOLLOW_HAS_FOLLOWERS_KEY, None)
            public_data.pop(FOLLOW_OUTBOX_EVENT_ID_KEY, None)
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
        if event_type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE:
            follow_movement_data.append(deepcopy(event.data))
        uses_canonical_player_entry = bool(
            event_type == TRANSFER_ENTER_EVENT_TYPE
            and event.data.get(PLAYER_ROOM_ENTER_EMITTED_KEY)
        )
        dispatch_trigger_event = (
            not is_private_control_event
            and not uses_canonical_player_entry
            and (
                not is_trigger_step_event
                or event_type in {
                    TRANSFER_ENTER_EVENT_TYPE,
                    PLAYER_ROOM_ENTER_EVENT_TYPE,
                }
            )
        )
        dispatch_quest_event = (
            not is_private_control_event
            and event_type != PLAYER_ROOM_ENTER_EVENT_TYPE
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
                connection_id=event.connection_id or connection_id,
            )

        if dispatch_quest_event:
            from quests.subscriptions import (
                dispatch_quest_subscriptions_for_event,
            )

            dispatch_quest_subscriptions_for_event(
                event_type=event.type,
                event_data=event.data,
                actor_key=actor_key,
                connection_id=event.connection_id or connection_id,
            )

    # A follow edge is deliberately scheduled only after every visible event
    # in this publish batch has been delivered. This preserves leader-before-
    # follower room text and, through on_commit, keeps follower row locks out
    # of the leader's movement transaction.
    if follow_movement_data:
        from spawns.following import schedule_follow_movement

        for movement_data in follow_movement_data:
            schedule_follow_movement(movement_data)


def _prepare_follow_events(event_list: list[GameEvent]) -> list[GameEvent]:
    """Replace structural follow edges with their durable metadata copies."""
    follow_events = [
        event
        for event in event_list
        if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
    ]
    if not follow_events:
        return event_list
    prepared = iter(durable_follow_directional_move_events(follow_events))
    return [
        next(prepared)
        if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
        else event
        for event in event_list
    ]


def _schedule_follow_event_data(event_data: Sequence[dict]) -> None:
    """Schedule control rows only after their dependencies are activated."""
    from spawns.following import schedule_follow_movement

    for data in event_data:
        schedule_follow_movement(deepcopy(data))


def _enqueue_game_event_batch(
    events: Iterable[GameEvent],
) -> tuple[int, uuid.UUID | None]:
    """Persist one event batch and its follow-publication dependencies.

    Follow controls remain independent durable batches because their Celery
    task owns their acknowledgement. When visible events accompany them, the
    control row depends on that batch. The dependency is removed only by the
    visible batch's fenced acknowledgement, so neither the heartbeat nor a
    concurrent flusher can make follower movement overtake room output and
    room-entry subscribers.
    """
    from spawns.models import GameEventOutbox

    with transaction.atomic():
        event_list = _prepare_follow_events(list(events))
        followed_controls = [
            event
            for event in event_list
            if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
            and bool(event.data.get(FOLLOW_HAS_FOLLOWERS_KEY))
        ]
        control_event_ids = {
            uuid.UUID(str(event.data[FOLLOW_OUTBOX_EVENT_ID_KEY]))
            for event in followed_controls
        }

        rows = []
        batch_id = uuid.uuid4()
        sequence = 0
        for event in event_list:
            # Follow controls are persisted as independent batches by the
            # durable recorder. Mixing them with player-facing rows would make
            # their delayed task acknowledgement replay visible messages.
            if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE:
                continue
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
            sequence += 1

        if rows:
            GameEventOutbox.objects.bulk_create(rows)

        if rows and control_event_ids:
            attached = GameEventOutbox.objects.filter(
                event_id__in=control_event_ids,
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                depends_on_batch_id__isnull=True,
            ).update(depends_on_batch_id=batch_id)
            if attached != len(control_event_ids):
                raise RuntimeError(
                    "Could not attach every follow edge to its visible batch."
                )
        elif followed_controls:
            # A standalone private edge has no visible work to gate it. Hand it
            # off immediately after commit instead of waiting for the heartbeat.
            standalone_data = [
                deepcopy(event.data) for event in followed_controls
            ]
            transaction.on_commit(
                lambda: _schedule_follow_event_data(standalone_data),
                robust=True,
            )

    return (
        len(rows) + len(control_event_ids),
        batch_id if rows else None,
    )


def enqueue_game_events(events: Iterable[GameEvent]) -> int:
    """Persist events in the caller's transaction for later delivery."""
    count, _batch_id = _enqueue_game_event_batch(events)
    return count


def persist_follow_dependent_game_events(
    events: Iterable[GameEvent],
    *,
    force: bool = False,
    actor_key: str | None = None,
    connection_id: str | None = None,
) -> list[GameEvent]:
    """Durably consume an event list when follower ordering requires it.

    The caller must still be inside the transaction that changed room state.
    Returning an empty list tells an ordinary handler/task that publication is
    now owned by the outbox. ``force`` is used for a move *caused* by a follow
    edge: even a leaf follower needs durable visible/room-entry output before
    its parent task can acknowledge that edge.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Follow-dependent events must be persisted with their room state."
        )
    event_list = list(events)
    if actor_key and connection_id:
        event_list = [
            replace(event, connection_id=connection_id)
            if (
                event.connection_id is None
                and (
                    (
                        bool(event.recipients)
                        and all(
                            recipient == actor_key
                            for recipient in event.recipients
                        )
                    )
                    or (
                        not event.recipients
                        and isinstance(event.data.get("actor"), dict)
                        and event.data["actor"].get("key") == actor_key
                    )
                )
            )
            else event
            for event in event_list
        ]
    event_list = _prepare_follow_events(event_list)
    has_followers = any(
        event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
        and bool(event.data.get(FOLLOW_HAS_FOLLOWERS_KEY))
        for event in event_list
    )
    if not force and not has_followers:
        return event_list
    _count, batch_id = _enqueue_game_event_batch(event_list)
    if batch_id is not None:
        transaction.on_commit(
            lambda: flush_game_event_outbox_batch(batch_id),
            robust=True,
        )
    return []


def _acknowledge_visible_batch_and_activate_follow_edges(
    *,
    batch_id,
    claim_token,
    expected_event_ids: set[uuid.UUID],
) -> int:
    """Atomically fence-ack visible rows and release their control edges."""
    from spawns.models import GameEventOutbox

    activated_data: list[dict] = []
    with transaction.atomic():
        fenced_rows = list(
            GameEventOutbox.objects.select_for_update(of=("self",))
            .filter(batch_id=batch_id, claim_token=claim_token)
            .order_by("sequence", "id")
        )
        if {row.event_id for row in fenced_rows} != expected_event_ids:
            raise RuntimeError("Game event outbox batch claim was lost.")

        dependent_rows = list(
            GameEventOutbox.objects.select_for_update(of=("self",))
            .filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                depends_on_batch_id=batch_id,
            )
            .order_by("created_ts", "id")
        )
        dependent_ids = [row.id for row in dependent_rows]
        if dependent_ids:
            activated = GameEventOutbox.objects.filter(
                id__in=dependent_ids,
                depends_on_batch_id=batch_id,
            ).update(
                depends_on_batch_id=None,
                available_ts=timezone.now(),
            )
            if activated != len(dependent_ids):
                raise RuntimeError("Follow publication dependency fence was lost.")
            activated_data = [deepcopy(row.data or {}) for row in dependent_rows]

        deleted, _ = GameEventOutbox.objects.filter(
            batch_id=batch_id,
            claim_token=claim_token,
        ).delete()
        if deleted != len(fenced_rows):
            raise RuntimeError("Game event outbox acknowledgement was incomplete.")

        if activated_data:
            transaction.on_commit(
                lambda: _schedule_follow_event_data(activated_data),
                robust=True,
            )
    return deleted


def flush_game_event_outbox(
    *,
    limit: int = 500,
    publisher: Callable[..., None] | None = None,
    now=None,
    batch_id=None,
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
            first_query = (
                GameEventOutbox.objects.select_for_update(skip_locked=True)
                .filter(
                    sequence=0,
                    available_ts__lte=claim_now,
                    depends_on_batch_id__isnull=True,
                )
                .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lte=claim_now))
                .order_by("available_ts", "created_ts", "batch_id", "id")
            )
            if batch_id is not None:
                first_query = first_query.filter(batch_id=batch_id)
            first = first_query.first()
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
        follow_control_batch = bool(
            claimed_rows
            and all(
                row.event_type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
                for row in claimed_rows
            )
        )
        try:
            if follow_control_batch:
                from spawns.following import enqueue_claimed_follow_movement

                for row in claimed_rows:
                    enqueue_claimed_follow_movement(
                        deepcopy(row.data or {}),
                        outbox_event_id=str(row.event_id),
                        claim_token=str(claim_token),
                    )
            else:
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

        if follow_control_batch:
            # Celery now owns the lease. The final bounded propagation page is
            # the only code allowed to acknowledge/delete this durable row.
            delivered += len(claimed_rows)
            continue

        try:
            deleted = _acknowledge_visible_batch_and_activate_follow_edges(
                batch_id=first.batch_id,
                claim_token=claim_token,
                expected_event_ids={row.event_id for row in claimed_rows},
            )
        except Exception as exc:
            backoff_seconds = min(
                300,
                2 ** min(
                    8,
                    max(row.attempt_count for row in claimed_rows),
                ),
            )
            GameEventOutbox.objects.filter(
                batch_id=first.batch_id,
                claim_token=claim_token,
            ).update(
                available_ts=claim_now + timedelta(seconds=backoff_seconds),
                claim_token=None,
                claimed_until=None,
                last_error=str(exc)[:2000],
            )
            logger.exception(
                "Failed to acknowledge game event outbox batch %s",
                first.batch_id,
            )
            continue
        delivered += deleted
    return delivered


def flush_game_event_outbox_batch(
    batch_id,
    *,
    publisher: Callable[..., None] | None = None,
) -> int:
    """Fast-path exactly one committed batch; the heartbeat handles recovery."""
    return flush_game_event_outbox(
        limit=1,
        publisher=publisher,
        batch_id=batch_id,
    )
