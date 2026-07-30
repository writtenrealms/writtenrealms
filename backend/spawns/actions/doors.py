from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from config import constants as adv_consts
from config import game_settings as adv_config
from spawns.ability_prepare_state import active_prepared_ability_slugs
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent, enqueue_game_events, flush_game_event_outbox
from spawns.models import DoorState, Item, Player, PreparedGameAction
from spawns.request_segments import normalize_request_segment
from spawns.state_payloads import room_payload_key_for
from worlds.models import Door, Doorway, Room, World


DOOR_ACTION_DELAY_SECONDS = float(
    getattr(adv_config, "DOOR_ACTION_DELAY_SECONDS", 2.5)
)
MAX_DUE_DOOR_ACTIONS = 1_000

_DIRECTION_ALIASES = {
    "n": adv_consts.DIRECTION_NORTH,
    "e": adv_consts.DIRECTION_EAST,
    "s": adv_consts.DIRECTION_SOUTH,
    "w": adv_consts.DIRECTION_WEST,
    "u": adv_consts.DIRECTION_UP,
    "d": adv_consts.DIRECTION_DOWN,
}
_DIRECTION_ALIASES.update({direction: direction for direction in adv_consts.DIRECTIONS})


@dataclass(frozen=True)
class DoorTarget:
    face: Door
    doorway: Doorway


@dataclass(frozen=True)
class MovementDoorState:
    """The exact room-facing door and its locked runtime state."""

    face: Door
    runtime_state: DoorState

    @property
    def state(self) -> str:
        return self.runtime_state.state


@dataclass(frozen=True)
class _SystemDoorIssuer:
    key: str
    name: str
    model_type: str = "system"


def _doorway_key(doorway_id: int) -> str:
    # Doorway.key is the authored key-item relationship, so it intentionally
    # shadows AdventBaseModel.key.
    return f"doorway.{doorway_id}"


def _normalize_target(value) -> str:
    if isinstance(value, (list, tuple)):
        value = " ".join(str(token) for token in value)
    return " ".join(str(value or "").strip().lower().split())


def _runtime_authored_world_id(runtime_world: World | None) -> int | None:
    if runtime_world is None or runtime_world.context_id is None:
        return None
    return runtime_world.context_id


def validate_runtime_door_context(
    *,
    runtime_world: World | None,
    room: Room | None,
) -> None:
    if runtime_world is None or runtime_world.context_id is None:
        raise ActionError(
            "Door commands require an explicit live runtime world.",
            code="runtime_world_required",
        )
    if room is None:
        raise ActionError("There is no current room.", code="no_room")
    if _runtime_authored_world_id(runtime_world) != room.world_id:
        raise ActionError(
            "That room does not belong to this runtime world.",
            code="runtime_world_mismatch",
        )


def resolve_door_target(room: Room, selector) -> DoorTarget:
    normalized = _normalize_target(selector)
    if not normalized:
        raise ActionError(
            "Which door? Use a direction or door name.",
            code="door_target_required",
        )

    faces = list(
        Door.objects.filter(from_room=room)
        .select_related("doorway", "doorway__key", "from_room", "to_room")
        .order_by("direction", "id")
    )
    if not faces:
        raise ActionError("There is no door here.", code="door_not_found")

    requested_direction = _DIRECTION_ALIASES.get(normalized)
    if requested_direction:
        matches = [
            face for face in faces
            if face.direction == requested_direction
        ]
    else:
        # A final direction token can disambiguate a name, but it can also be
        # part of a legitimate name ("passage north"). Consider both readings
        # and require an unambiguous resulting face.
        matches_by_id = {
            face.id: face
            for face in faces
            if _normalize_target(face.name) == normalized
        }
        parts = normalized.split()
        if len(parts) > 1 and parts[-1] in _DIRECTION_ALIASES:
            qualified_direction = _DIRECTION_ALIASES[parts[-1]]
            qualified_name = " ".join(parts[:-1])
            for face in faces:
                if (
                    face.direction == qualified_direction
                    and _normalize_target(face.name) == qualified_name
                ):
                    matches_by_id[face.id] = face
        matches = list(matches_by_id.values())

    if not matches:
        raise ActionError("You do not see that door here.", code="door_not_found")
    if len(matches) > 1:
        raise ActionError(
            "That door name is ambiguous. Add its direction.",
            code="ambiguous_door",
            data={
                "doors": [
                    {
                        "key": f"door.{face.id}",
                        "name": face.name,
                        "direction": face.direction,
                    }
                    for face in matches
                ]
            },
        )
    return DoorTarget(face=matches[0], doorway=matches[0].doorway)


def lock_runtime_door_state(
    *,
    runtime_world: World,
    doorway: Doorway,
) -> DoorState:
    """Create-on-first-touch and lock one runtime doorway state."""
    if _runtime_authored_world_id(runtime_world) != doorway.world_id:
        raise ActionError(
            "That doorway does not belong to this runtime world.",
            code="runtime_world_mismatch",
        )

    state = (
        DoorState.objects.select_for_update()
        .filter(world=runtime_world, doorway=doorway)
        .first()
    )
    if state is not None:
        return state

    try:
        with transaction.atomic():
            state = DoorState.objects.create(
                world=runtime_world,
                doorway=doorway,
                state=doorway.default_state,
                revision=0,
            )
    except IntegrityError:
        # A concurrent first touch won the unique insert. The unique check
        # waits for that transaction, after which this row can be locked.
        state = DoorState.objects.select_for_update().get(
            world=runtime_world,
            doorway=doorway,
        )
    return state


def lock_door_state_for_movement(
    *,
    runtime_world: World,
    room_id: int,
    direction: str,
) -> MovementDoorState | None:
    """Lock the exact doorway whose passability controls this movement edge."""
    face = (
        Door.objects.filter(from_room_id=room_id, direction=direction)
        .select_related("doorway", "doorway__key")
        .first()
    )
    if face is None:
        return None
    return MovementDoorState(
        face=face,
        runtime_state=lock_runtime_door_state(
            runtime_world=runtime_world,
            doorway=face.doorway,
        ),
    )


def _door_faces(doorway: Doorway) -> list[Door]:
    return list(
        Door.objects.filter(doorway=doorway)
        .select_related("from_room", "to_room")
        .order_by("id")
    )


def door_state_deltas(
    doorway: Doorway,
    state: str,
    *,
    faces: list[Door] | None = None,
) -> list[dict]:
    faces = faces if faces is not None else _door_faces(doorway)
    return [
        {
            "room_id": face.from_room_id,
            "key": room_payload_key_for(face.from_room),
            "direction": face.direction,
            "name": face.name,
            "door_state": state,
        }
        for face in faces
    ]


def _door_endpoint_room_ids(faces: list[Door]) -> set[int]:
    return {
        room_id
        for face in faces
        for room_id in (face.from_room_id, face.to_room_id)
    }


def _issuer_payload(actor) -> dict:
    model_type = getattr(actor, "model_type", None)
    if model_type is None:
        model_type = actor.__class__.__name__.lower()
    return {
        "type": model_type,
        "key": actor.key,
        "name": getattr(actor, "name", "") or model_type,
    }


def _doorway_payload(doorway: Doorway) -> dict:
    return {"id": doorway.id, "key": _doorway_key(doorway.id)}


def _occupant_keys(
    *,
    runtime_world: World,
    faces: list[Door],
    exclude_player_id: int | None = None,
) -> list[str]:
    players = Player.objects.filter(
        world=runtime_world,
        room_id__in=_door_endpoint_room_ids(faces),
        in_game=True,
    )
    if exclude_player_id is not None:
        players = players.exclude(pk=exclude_player_id)
    return [
        f"player.{player_id}"
        for player_id in players.order_by("id").values_list("id", flat=True)
    ]


def _state_text(name: str, state: str) -> str:
    if state == adv_consts.DOOR_STATE_OPEN:
        return f"The {name} opens."
    if state == adv_consts.DOOR_STATE_LOCKED:
        return f"The {name} closes and locks."
    return f"The {name} closes."


def _transition_data(
    *,
    doorway: Doorway,
    previous_state: str,
    state: str,
    cause: str,
    issuer,
    faces: list[Door],
    revision: int,
    changed: bool,
) -> dict:
    return {
        "doorway": _doorway_payload(doorway),
        "previous_state": previous_state,
        "state": state,
        "cause": cause,
        "issuer": _issuer_payload(issuer),
        "runtime_world": {
            "id": None,
            "key": None,
        },
        "revision": revision,
        "changed": changed,
        "door_states": door_state_deltas(doorway, state, faces=faces),
    }


def _transition_events(
    *,
    command_type: str,
    runtime_world: World,
    doorway: Doorway,
    previous_state: str,
    state: str,
    cause: str,
    issuer,
    faces: list[Door],
    revision: int,
    changed: bool,
    consumed_key: dict | None = None,
    targeted_face_name: str | None = None,
) -> list[GameEvent]:
    data = _transition_data(
        doorway=doorway,
        previous_state=previous_state,
        state=state,
        cause=cause,
        issuer=issuer,
        faces=faces,
        revision=revision,
        changed=changed,
    )
    data["runtime_world"] = {
        "id": runtime_world.id,
        "key": runtime_world.key,
    }
    if consumed_key:
        data["consumed_key"] = consumed_key

    face_name = targeted_face_name or (faces[0].name if faces else "door")
    if changed:
        if command_type == "/open":
            actor_text = f"You force the {face_name} open."
        elif command_type == "/close":
            actor_text = f"You force the {face_name} closed."
        elif command_type == "/lock":
            actor_text = f"You force the {face_name} closed and locked."
        elif command_type == "/unlock":
            actor_text = f"You force the {face_name} closed and unlocked."
        elif command_type == "open" and previous_state == adv_consts.DOOR_STATE_LOCKED:
            actor_text = f"You unlock and open the {face_name}."
        elif command_type == "unlock":
            actor_text = f"You unlock the {face_name}."
        elif state == adv_consts.DOOR_STATE_OPEN:
            actor_text = f"You open the {face_name}."
        elif state == adv_consts.DOOR_STATE_LOCKED:
            actor_text = (
                f"You close and lock the {face_name}."
                if previous_state == adv_consts.DOOR_STATE_OPEN
                else f"You lock the {face_name}."
            )
        else:
            actor_text = f"You close the {face_name}."
    else:
        actor_text = f"The {face_name} is already {state}."

    actor_player_id = issuer.id if isinstance(issuer, Player) else None
    events = [
        GameEvent(
            type=f"cmd.{command_type}.success",
            recipients=[issuer.key],
            data=data,
            text=actor_text,
        )
    ]
    if consumed_key:
        events.append(
            GameEvent(
                type="affect.inventory.remove",
                recipients=[issuer.key],
                data={"items": [consumed_key]},
                text=f"The {consumed_key['name']} is consumed.",
            )
        )
    if changed:
        recipients = _occupant_keys(
            runtime_world=runtime_world,
            faces=faces,
            exclude_player_id=actor_player_id,
        )
        # Keep the transition event even when nobody else is present. Empty
        # recipient lists still flow through event subscriptions, allowing
        # room/mob reactions to observe every real state change without
        # making the actor receive duplicate command and state messages.
        events.append(
            GameEvent(
                type="door.state_changed",
                recipients=recipients,
                data=data,
                text=_state_text(face_name, state),
            )
        )
    return events


def _save_transition(
    state: DoorState,
    new_state: str,
    *,
    bump_on_noop: bool = False,
) -> tuple[str, bool]:
    previous_state = state.state
    changed = previous_state != new_state
    if changed or bump_on_noop:
        state.state = new_state
        state.revision += 1
        state.save(update_fields=["state", "revision", "modified_ts"])
    return previous_state, changed


def _request_uuid(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ActionError("Invalid request identifier.", code="invalid_request_id")


def _locked_player(player_id: int) -> Player:
    try:
        return (
            Player.objects.select_for_update(of=("self",))
            # Nullable room/context joins make PostgreSQL reject FOR UPDATE on
            # the outer-join side even when OF targets only Player.
            .select_related("world")
            .get(pk=player_id)
        )
    except Player.DoesNotExist:
        raise ActionError("Player not found.", code="player_not_found")


def _validate_active_player(player: Player) -> None:
    validate_runtime_door_context(runtime_world=player.world, room=player.room)
    if not player.in_game:
        raise ActionError("You are not currently in the game.", code="not_in_game")
    if int(getattr(player, "health", 0) or 0) <= 0:
        raise ActionError("You cannot do that while dead.", code="dead")


def _locked_pending_action(player: Player) -> PreparedGameAction | None:
    return (
        PreparedGameAction.objects.select_for_update(of=("self",))
        .filter(player=player, status=PreparedGameAction.STATUS_PENDING)
        .select_related("doorway")
        .first()
    )


def _request_receipt(
    *,
    player: Player,
    request_id: uuid.UUID | None,
    request_segment: str,
) -> PreparedGameAction | None:
    if request_id is None:
        return None
    return (
        PreparedGameAction.objects.select_for_update(of=("self",))
        .filter(
            player=player,
            request_id=request_id,
            request_segment=request_segment,
        )
        .first()
    )


def _key_item_payload(item: Item) -> dict:
    return {"key": item.key, "name": item.name}


def _lock_matching_key(player: Player, doorway: Doorway) -> Item:
    if not doorway.key_id:
        raise ActionError("That door has no key.", code="door_has_no_key")
    key_item = (
        player.inventory.select_for_update()
        .filter(
            definition_id=doorway.key_id,
            is_pending_deletion=False,
        )
        .select_related("definition")
        .order_by("id")
        .first()
    )
    if key_item is None:
        key_name = getattr(doorway.key, "name", None) or "matching key"
        raise ActionError(
            f"You need the {key_name}.",
            code="missing_key",
            data={"key_definition_id": doorway.key_id},
        )
    return key_item


def _consume_key_if_needed(doorway: Doorway, key_item: Item | None) -> dict | None:
    if not doorway.destroy_key or key_item is None:
        return None
    payload = _key_item_payload(key_item)
    key_item.delete()
    return payload


def _replay_event(
    *,
    player: Player,
    action: PreparedGameAction,
) -> ActionResult:
    result = dict(action.result or {})
    event_type = result.pop("_event_type", None)
    event_text = result.pop("_event_text", None)
    if not event_type:
        if action.status == PreparedGameAction.STATUS_PENDING:
            event_type = f"cmd.{_action_command(action.action_type)}.started"
        elif action.status == PreparedGameAction.STATUS_COMPLETED:
            event_type = f"cmd.{_action_command(action.action_type)}.success"
        else:
            event_type = f"cmd.{_action_command(action.action_type)}.cancelled"
    result["replayed"] = True
    return ActionResult(
        events=[
            GameEvent(
                type=event_type,
                recipients=[player.key],
                data=result,
                text=event_text,
            )
        ],
        data={"replayed": True, "action_id": action.id},
    )


def _action_command(action_type: str) -> str:
    return {
        PreparedGameAction.ACTION_OPEN_DOOR: "open",
        PreparedGameAction.ACTION_CLOSE_DOOR: "close",
        PreparedGameAction.ACTION_LOCK_DOOR: "lock",
        PreparedGameAction.ACTION_UNLOCK_DOOR: "unlock",
    }[action_type]


def _action_request_correlation(
    action: PreparedGameAction,
) -> dict[str, str]:
    if action.request_id is None:
        return {}
    return {
        "request_id": str(action.request_id),
        "request_segment": normalize_request_segment(
            action.request_segment
        ),
    }


def _correlate_action_actor_event(
    events: list[GameEvent],
    action: PreparedGameAction,
) -> list[GameEvent]:
    """Attach a durable request only to the prepared action's actor event."""
    correlation = _action_request_correlation(action)
    if not events or not correlation:
        return events
    actor_event = events[0]
    events[0] = GameEvent(
        type=actor_event.type,
        recipients=actor_event.recipients,
        data={**actor_event.data, **correlation},
        text=actor_event.text,
        group=actor_event.group,
        connection_id=actor_event.connection_id,
    )
    return events


def _command_action_type(command: str) -> str:
    return {
        "open": PreparedGameAction.ACTION_OPEN_DOOR,
        "close": PreparedGameAction.ACTION_CLOSE_DOOR,
        "lock": PreparedGameAction.ACTION_LOCK_DOOR,
        "unlock": PreparedGameAction.ACTION_UNLOCK_DOOR,
    }[command]


def _start_data(
    *,
    action: PreparedGameAction,
    player: Player,
    target: DoorTarget,
    state: DoorState,
    faces: list[Door],
) -> dict:
    return {
        "action": _action_command(action.action_type),
        "action_id": action.id,
        "doorway": _doorway_payload(target.doorway),
        "state": state.state,
        "expected_revision": state.revision,
        "run_at": action.run_at.isoformat(),
        "delay_seconds": DOOR_ACTION_DELAY_SECONDS,
        "issuer": _issuer_payload(player),
        "runtime_world": {
            "id": player.world_id,
            "key": player.world.key,
        },
        "door_states": door_state_deltas(
            target.doorway,
            state.state,
            faces=faces,
        ),
    }


def _prepared_room_event(
    *,
    event_type: str,
    action: PreparedGameAction,
    player: Player,
    runtime_world: World,
    faces: list[Door],
    data: dict,
    text: str,
) -> GameEvent | None:
    recipients = _occupant_keys(
        runtime_world=runtime_world,
        faces=faces,
        exclude_player_id=player.id,
    )
    if not recipients:
        return None
    return GameEvent(
        type=event_type,
        recipients=recipients,
        data=data,
        text=text,
    )


def _started_result(
    *,
    action: PreparedGameAction,
    player: Player,
    target: DoorTarget,
    state: DoorState,
    faces: list[Door],
    repeated: bool = False,
) -> ActionResult:
    command = _action_command(action.action_type)
    data = _start_data(
        action=action,
        player=player,
        target=target,
        state=state,
        faces=faces,
    )
    data["repeated"] = repeated
    actor_text = (
        f"You are already preparing to {command} the {target.face.name}."
        if repeated
        else f"You begin to {command} the {target.face.name}."
    )
    events = [
        GameEvent(
            type=f"cmd.{command}.started",
            recipients=[player.key],
            data=data,
            text=actor_text,
        )
    ]
    if not repeated:
        room_event = _prepared_room_event(
            event_type="door.action_started",
            action=action,
            player=player,
            runtime_world=player.world,
            faces=faces,
            data=data,
            text=f"{player.name} begins to {command} the {target.face.name}.",
        )
        if room_event:
            events.append(room_event)
    return ActionResult(events=events, data={"action_id": action.id})


def _schedule_prepared_action(action_id: int, run_at) -> None:
    def enqueue() -> None:
        from spawns.tasks import resolve_prepared_game_action

        resolve_prepared_game_action.apply_async(
            kwargs={"action_id": action_id},
            eta=run_at,
        )

    transaction.on_commit(enqueue, robust=True)


def _store_terminal_request_receipt(
    *,
    player: Player,
    target: DoorTarget,
    command: str,
    request_id: uuid.UUID | None,
    request_segment: str,
    request_selector: str,
    revision: int,
    actor_event: GameEvent,
) -> PreparedGameAction | None:
    if request_id is None:
        return None
    completed_ts = timezone.now()
    return PreparedGameAction.objects.create(
        player=player,
        runtime_world=player.world,
        room=player.room,
        doorway=target.doorway,
        action_type=_command_action_type(command),
        status=PreparedGameAction.STATUS_COMPLETED,
        run_at=completed_ts,
        expected_revision=revision,
        request_id=request_id,
        request_segment=request_segment,
        request_selector=request_selector,
        target_direction=target.face.direction,
        target_name=target.face.name,
        completed_ts=completed_ts,
        result={
            **actor_event.data,
            "_event_type": actor_event.type,
            "_event_text": actor_event.text,
        },
    )


def execute_player_door_command(
    *,
    player_id: int,
    command: str,
    selector,
    request_id=None,
    request_segment="r",
) -> ActionResult:
    if command not in {"open", "close", "lock", "unlock"}:
        raise ActionError("Unknown door command.", code="invalid_door_command")

    parsed_request_id = _request_uuid(request_id)
    segment = normalize_request_segment(request_segment)
    normalized_selector = _normalize_target(selector)

    with transaction.atomic():
        player = _locked_player(player_id)
        receipt = _request_receipt(
            player=player,
            request_id=parsed_request_id,
            request_segment=segment,
        )
        if receipt is not None:
            if (
                _action_command(receipt.action_type) != command
                or receipt.request_selector != normalized_selector
            ):
                raise ActionError(
                    "That request identifier was already used for a different "
                    "door command.",
                    code="idempotency_conflict",
                )
            return _replay_event(player=player, action=receipt)

        _validate_active_player(player)
        pending = _locked_pending_action(player)
        target = resolve_door_target(player.room, selector)
        state = lock_runtime_door_state(
            runtime_world=player.world,
            doorway=target.doorway,
        )
        faces = _door_faces(target.doorway)

        if pending is not None:
            pending_command = _action_command(pending.action_type)
            if (
                pending.doorway_id == target.doorway.id
                and pending_command == command
            ):
                repeated_result = _started_result(
                    action=pending,
                    player=player,
                    target=target,
                    state=state,
                    faces=faces,
                    repeated=True,
                )
                _store_terminal_request_receipt(
                    player=player,
                    target=target,
                    command=command,
                    request_id=parsed_request_id,
                    request_segment=segment,
                    request_selector=normalized_selector,
                    revision=state.revision,
                    actor_event=repeated_result.events[0],
                )
                return repeated_result

        # No-op assertions never disturb an existing wind-up. In particular,
        # opening an already-open door cannot grief another player's close.
        is_noop = (
            (command == "open" and state.state == adv_consts.DOOR_STATE_OPEN)
            or (
                command == "close"
                and state.state
                in (adv_consts.DOOR_STATE_CLOSED, adv_consts.DOOR_STATE_LOCKED)
            )
            or (command == "lock" and state.state == adv_consts.DOOR_STATE_LOCKED)
            or (
                command == "unlock"
                and state.state
                in (adv_consts.DOOR_STATE_OPEN, adv_consts.DOOR_STATE_CLOSED)
            )
        )
        if is_noop:
            events = _transition_events(
                command_type=command,
                runtime_world=player.world,
                doorway=target.doorway,
                previous_state=state.state,
                state=state.state,
                cause=command,
                issuer=player,
                faces=faces,
                revision=state.revision,
                changed=False,
                targeted_face_name=target.face.name,
            )
            _store_terminal_request_receipt(
                player=player,
                target=target,
                command=command,
                request_id=parsed_request_id,
                request_segment=segment,
                request_selector=normalized_selector,
                revision=state.revision,
                actor_event=events[0],
            )
            return ActionResult(events=events)

        if pending is not None:
            raise ActionError(
                "You are already preparing another physical action.",
                code="physical_action_pending",
                data={"action_id": pending.id},
            )
        if active_prepared_ability_slugs(player):
            raise ActionError(
                "You are already preparing an ability.",
                code="physical_action_pending",
            )

        key_item = None
        if command == "lock" or (
            command in {"open", "unlock"}
            and state.state == adv_consts.DOOR_STATE_LOCKED
        ):
            key_item = _lock_matching_key(player, target.doorway)

        if command == "close" or (
            command == "lock" and state.state == adv_consts.DOOR_STATE_OPEN
        ):
            action_type = (
                PreparedGameAction.ACTION_CLOSE_DOOR
                if command == "close"
                else PreparedGameAction.ACTION_LOCK_DOOR
            )
            run_at = timezone.now() + timedelta(seconds=DOOR_ACTION_DELAY_SECONDS)
            try:
                with transaction.atomic():
                    action = PreparedGameAction.objects.create(
                        player=player,
                        runtime_world=player.world,
                        room=player.room,
                        doorway=target.doorway,
                        action_type=action_type,
                        run_at=run_at,
                        expected_revision=state.revision,
                        request_id=parsed_request_id,
                        request_segment=segment,
                        request_selector=normalized_selector,
                        target_direction=target.face.direction,
                        target_name=target.face.name,
                    )
            except IntegrityError:
                existing = _locked_pending_action(player)
                if existing is None:
                    raise
                if (
                    existing.doorway_id != target.doorway.id
                    or _action_command(existing.action_type) != command
                ):
                    raise ActionError(
                        "You are already preparing another physical action.",
                        code="physical_action_pending",
                        data={"action_id": existing.id},
                    )
                action = existing
            result = _started_result(
                action=action,
                player=player,
                target=target,
                state=state,
                faces=faces,
                repeated=action.run_at != run_at,
            )
            if not result.events[0].data.get("repeated"):
                action.result = {
                    **result.events[0].data,
                    "_event_type": result.events[0].type,
                    "_event_text": result.events[0].text,
                }
                action.save(update_fields=["result", "modified_ts"])
                _schedule_prepared_action(action.id, action.run_at)
            return result

        new_state = {
            "open": adv_consts.DOOR_STATE_OPEN,
            "lock": adv_consts.DOOR_STATE_LOCKED,
            "unlock": adv_consts.DOOR_STATE_CLOSED,
        }[command]
        previous_state, changed = _save_transition(state, new_state)
        consumed_key = None
        if command in {"open", "unlock"}:
            consumed_key = _consume_key_if_needed(target.doorway, key_item)
        events = _transition_events(
            command_type=command,
            runtime_world=player.world,
            doorway=target.doorway,
            previous_state=previous_state,
            state=state.state,
            cause=command,
            issuer=player,
            faces=faces,
            revision=state.revision,
            changed=changed,
            consumed_key=consumed_key,
            targeted_face_name=target.face.name,
        )
        _store_terminal_request_receipt(
            player=player,
            target=target,
            command=command,
            request_id=parsed_request_id,
            request_segment=segment,
            request_selector=normalized_selector,
            revision=state.revision,
            actor_event=events[0],
        )
        return ActionResult(events=events)


def execute_forced_door_command(
    *,
    actor,
    actor_type: str,
    runtime_world: World | None,
    room: Room | None,
    command: str,
    selector,
) -> ActionResult:
    if command not in {"/open", "/close", "/lock", "/unlock"}:
        raise ActionError("Unknown door command.", code="invalid_door_command")
    validate_runtime_door_context(runtime_world=runtime_world, room=room)

    with transaction.atomic():
        target = resolve_door_target(room, selector)
        state = lock_runtime_door_state(
            runtime_world=runtime_world,
            doorway=target.doorway,
        )
        faces = _door_faces(target.doorway)
        if command == "/open":
            new_state = adv_consts.DOOR_STATE_OPEN
            bump_on_noop = True
        elif command == "/close":
            new_state = (
                adv_consts.DOOR_STATE_LOCKED
                if state.state == adv_consts.DOOR_STATE_LOCKED
                else adv_consts.DOOR_STATE_CLOSED
            )
            bump_on_noop = False
        elif command == "/lock":
            new_state = adv_consts.DOOR_STATE_LOCKED
            bump_on_noop = False
        else:
            new_state = adv_consts.DOOR_STATE_CLOSED
            bump_on_noop = False

        previous_state, changed = _save_transition(
            state,
            new_state,
            bump_on_noop=bump_on_noop,
        )
        return ActionResult(
            events=_transition_events(
                command_type=command,
                runtime_world=runtime_world,
                doorway=target.doorway,
                previous_state=previous_state,
                state=state.state,
                cause=f"force_{command.lstrip('/')}",
                issuer=actor,
                faces=faces,
                revision=state.revision,
                changed=changed,
                targeted_face_name=target.face.name,
            )
        )


def _cancel_data(
    *,
    action: PreparedGameAction,
    player: Player,
    faces: list[Door],
    code: str,
    message: str,
) -> dict:
    command = _action_command(action.action_type)
    current_state = (
        DoorState.objects.filter(
            world_id=action.runtime_world_id,
            doorway_id=action.doorway_id,
        )
        .values_list("state", flat=True)
        .first()
        or action.doorway.default_state
    )
    return {
        "action": command,
        "action_id": action.id,
        "doorway": _doorway_payload(action.doorway),
        "code": code,
        "error": message,
        "state": current_state,
        "issuer": _issuer_payload(player),
        "runtime_world": {
            "id": action.runtime_world_id,
            "key": action.runtime_world.key,
        },
        "door_states": door_state_deltas(
            action.doorway,
            current_state,
            faces=faces,
        ),
    }


def _mark_cancelled(
    *,
    action: PreparedGameAction,
    player: Player,
    code: str,
    message: str,
) -> list[GameEvent]:
    faces = _door_faces(action.doorway)
    data = _cancel_data(
        action=action,
        player=player,
        faces=faces,
        code=code,
        message=message,
    )
    command = _action_command(action.action_type)
    event_type = f"cmd.{command}.cancelled"
    actor_data = {
        **data,
        **_action_request_correlation(action),
    }
    action.status = PreparedGameAction.STATUS_CANCELLED
    action.failure_code = code
    action.completed_ts = timezone.now()
    action.result = {
        **actor_data,
        "_event_type": event_type,
        "_event_text": message,
    }
    action.save(
        update_fields=[
            "status",
            "failure_code",
            "completed_ts",
            "result",
            "modified_ts",
        ]
    )
    events = [
        GameEvent(
            type=event_type,
            recipients=[player.key],
            data=actor_data,
            text=message,
        )
    ]
    room_event = _prepared_room_event(
        event_type="door.action_cancelled",
        action=action,
        player=player,
        runtime_world=action.runtime_world,
        faces=faces,
        data=data,
        text=(
            f"{player.name} stops trying to {command} "
            f"the {action.target_name}."
        ),
    )
    if room_event:
        events.append(room_event)
    return events


def cancel_pending_player_door_action(
    *,
    player: Player,
    code: str,
    message: str,
) -> list[GameEvent]:
    """Cancel a prepared door action while the caller holds the player lock."""
    action = _locked_pending_action(player)
    if action is None:
        return []
    action = (
        PreparedGameAction.objects.select_related(
            "doorway",
            "runtime_world",
        )
        .get(pk=action.pk)
    )
    return _mark_cancelled(
        action=action,
        player=player,
        code=code,
        message=message,
    )


def cancel_pending_player_door_action_durably(
    *,
    player: Player,
    code: str,
    message: str,
) -> list[GameEvent]:
    """Cancel and enqueue feedback in the caller's player-owned transaction."""
    events = cancel_pending_player_door_action(
        player=player,
        code=code,
        message=message,
    )
    if events:
        enqueue_game_events(events)
        transaction.on_commit(flush_game_event_outbox, robust=True)
    return events


def _completion_cancel_reason(
    *,
    action: PreparedGameAction,
    player: Player,
    state: DoorState,
) -> tuple[str, str] | None:
    if not player.in_game:
        return "actor_logged_out", "You stop before finishing with the door."
    if int(getattr(player, "health", 0) or 0) <= 0:
        return "actor_dead", "You can no longer finish with the door."
    if player.world_id != action.runtime_world_id:
        return "actor_world_changed", "You are no longer in that world."
    if player.room_id != action.room_id:
        return "actor_room_changed", "You moved before finishing with the door."
    if not Door.objects.filter(
        doorway_id=action.doorway_id,
        from_room_id=action.room_id,
        direction=action.target_direction,
    ).exists():
        return (
            "doorway_reconfigured",
            "The doorway changed before you could finish.",
        )
    if state.revision != action.expected_revision:
        return "doorway_stale", "The door changed before you could finish."
    if state.state != adv_consts.DOOR_STATE_OPEN:
        return "doorway_state_changed", "The door is no longer open."
    if active_prepared_ability_slugs(player):
        return "physical_action_replaced", "You are preparing another action."
    return None


def resolve_prepared_door_action(
    action_id: int,
    *,
    now=None,
) -> str | None:
    """Resolve one durable wind-up. Safe to call repeatedly or concurrently."""
    due_at = now or timezone.now()
    candidate = (
        PreparedGameAction.objects.filter(pk=action_id)
        .values("player_id")
        .first()
    )
    if candidate is None:
        return None

    with transaction.atomic():
        player = _locked_player(candidate["player_id"])
        action = (
            PreparedGameAction.objects.select_for_update(of=("self",))
            .select_related("doorway", "runtime_world", "room")
            .filter(pk=action_id)
            .first()
        )
        if action is None:
            return None
        if action.status != PreparedGameAction.STATUS_PENDING:
            return action.status
        if action.run_at > due_at:
            return action.status

        state = lock_runtime_door_state(
            runtime_world=action.runtime_world,
            doorway=action.doorway,
        )
        cancel_reason = _completion_cancel_reason(
            action=action,
            player=player,
            state=state,
        )
        if cancel_reason:
            events = _mark_cancelled(
                action=action,
                player=player,
                code=cancel_reason[0],
                message=cancel_reason[1],
            )
            enqueue_game_events(events)
            transaction.on_commit(flush_game_event_outbox, robust=True)
            return action.status

        if action.action_type == PreparedGameAction.ACTION_LOCK_DOOR:
            try:
                _lock_matching_key(player, action.doorway)
            except ActionError as err:
                events = _mark_cancelled(
                    action=action,
                    player=player,
                    code=err.code,
                    message=err.message,
                )
                enqueue_game_events(events)
                transaction.on_commit(flush_game_event_outbox, robust=True)
                return action.status
            new_state = adv_consts.DOOR_STATE_LOCKED
        else:
            new_state = adv_consts.DOOR_STATE_CLOSED

        faces = _door_faces(action.doorway)
        previous_state, changed = _save_transition(state, new_state)
        command = _action_command(action.action_type)
        events = _transition_events(
            command_type=command,
            runtime_world=action.runtime_world,
            doorway=action.doorway,
            previous_state=previous_state,
            state=state.state,
            cause=command,
            issuer=player,
            faces=faces,
            revision=state.revision,
            changed=changed,
            targeted_face_name=action.target_name,
        )
        events = _correlate_action_actor_event(events, action)
        actor_event = events[0]
        action.status = PreparedGameAction.STATUS_COMPLETED
        action.completed_ts = due_at
        action.result = {
            **actor_event.data,
            "_event_type": actor_event.type,
            "_event_text": actor_event.text,
        }
        action.save(
            update_fields=[
                "status",
                "completed_ts",
                "result",
                "modified_ts",
            ]
        )
        enqueue_game_events(events)
        transaction.on_commit(flush_game_event_outbox, robust=True)
        return action.status


def process_due_prepared_door_actions(
    *,
    limit: int = 100,
    now=None,
) -> dict[str, int]:
    row_limit = max(1, min(int(limit or 1), MAX_DUE_DOOR_ACTIONS))
    due_at = now or timezone.now()
    candidate_ids = list(
        PreparedGameAction.objects.filter(
            status=PreparedGameAction.STATUS_PENDING,
            run_at__lte=due_at,
        )
        .order_by("run_at", "id")
        .values_list("id", flat=True)[:row_limit]
    )
    result = {"processed": 0, "completed": 0, "cancelled": 0}
    for action_id in candidate_ids:
        status = resolve_prepared_door_action(action_id, now=due_at)
        if status not in {
            PreparedGameAction.STATUS_COMPLETED,
            PreparedGameAction.STATUS_CANCELLED,
        }:
            continue
        result["processed"] += 1
        result[status] += 1
    return result


def prune_terminal_prepared_door_actions(
    *,
    retention_days: int = 7,
    batch_size: int = 5_000,
) -> int:
    try:
        days = max(1, int(retention_days))
    except (TypeError, ValueError):
        days = 7
    limit = max(1, min(int(batch_size or 1), 10_000))
    cutoff = timezone.now() - timedelta(days=days)
    action_ids = list(
        PreparedGameAction.objects.exclude(
            status=PreparedGameAction.STATUS_PENDING,
        )
        .filter(modified_ts__lt=cutoff)
        .order_by("modified_ts", "id")
        .values_list("id", flat=True)[:limit]
    )
    if not action_ids:
        return 0
    deleted, _ = PreparedGameAction.objects.filter(id__in=action_ids).delete()
    return deleted


@transaction.atomic
def reset_runtime_doorways(
    *,
    runtime_world: World,
    doorway_ids: list[int] | None = None,
) -> int:
    """Reset touched states while preserving monotonic revisions."""
    states = DoorState.objects.select_for_update().filter(world=runtime_world)
    if doorway_ids is not None:
        states = states.filter(doorway_id__in=doorway_ids)
    touched = list(states.select_related("doorway").order_by("doorway_id"))
    changed_previous_states = {
        state.doorway_id: state.state
        for state in touched
        if state.state != state.doorway.default_state
    }
    modified_ts = timezone.now()
    for state in touched:
        state.state = state.doorway.default_state
        state.revision += 1
        state.modified_ts = modified_ts
    if touched:
        DoorState.objects.bulk_update(
            touched,
            ["state", "revision", "modified_ts"],
        )

    if changed_previous_states:
        faces = list(
            Door.objects.filter(doorway_id__in=changed_previous_states)
            .select_related("from_room", "to_room")
            .order_by("doorway_id", "id")
        )
        faces_by_doorway: dict[int, list[Door]] = {}
        endpoint_room_ids: set[int] = set()
        for face in faces:
            faces_by_doorway.setdefault(face.doorway_id, []).append(face)
            endpoint_room_ids.update((face.from_room_id, face.to_room_id))

        recipients_by_room: dict[int, list[str]] = {}
        for player_id, room_id in (
            Player.objects.filter(
                world=runtime_world,
                room_id__in=endpoint_room_ids,
                in_game=True,
            )
            .order_by("id")
            .values_list("id", "room_id")
        ):
            recipients_by_room.setdefault(room_id, []).append(
                f"player.{player_id}"
            )

        issuer = _SystemDoorIssuer(
            key="system.door_reset",
            name="World reset",
        )
        events: list[GameEvent] = []
        states_by_doorway = {state.doorway_id: state for state in touched}
        for doorway_id, previous_state in changed_previous_states.items():
            state = states_by_doorway[doorway_id]
            doorway_faces = faces_by_doorway.get(doorway_id, [])
            recipient_keys = list(
                dict.fromkeys(
                    recipient
                    for room_id in _door_endpoint_room_ids(doorway_faces)
                    for recipient in recipients_by_room.get(room_id, [])
                )
            )
            data = _transition_data(
                doorway=state.doorway,
                previous_state=previous_state,
                state=state.state,
                cause="reset",
                issuer=issuer,
                faces=doorway_faces,
                revision=state.revision,
                changed=True,
            )
            data["runtime_world"] = {
                "id": runtime_world.id,
                "key": runtime_world.key,
            }
            face_name = doorway_faces[0].name if doorway_faces else "door"
            events.append(
                GameEvent(
                    type="door.state_changed",
                    recipients=recipient_keys,
                    data=data,
                    text=_state_text(face_name, state.state),
                )
            )
        if events:
            enqueue_game_events(events)
            transaction.on_commit(flush_game_event_outbox, robust=True)
    return len(touched)
