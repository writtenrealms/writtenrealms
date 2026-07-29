from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.trigger_steps import (
    MAX_TRIGGER_COMMAND_LENGTH,
    SCRIPT_COMMAND_DEPTH_KEY,
    SCRIPT_COMMAND_PROVENANCE_KEY,
)
from core.utils import format_actor_msg, split_cmd
from spawns.events import GameEvent, capture_game_events
from spawns.handlers.base import TRIGGER_STEP_MODE_EVENTS_ONLY
from spawns.handlers.registry import (
    ActorNotFoundError,
    HandlerNotFoundError,
    dispatch_command,
    get_handler,
    resolve_text_handler,
)
from spawns.models import Mob, Player
from worlds.models import Room, World


MAX_SCRIPT_COMMAND_DEPTH = 8


class ScriptCommandError(ValueError):
    def __init__(self, message: str, *, code: str = "command_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScriptCommandResult:
    command: str
    events: tuple[GameEvent, ...]


def _actor_kind(actor: Player | Mob | Room) -> str:
    if isinstance(actor, Player):
        return "player"
    if isinstance(actor, Mob):
        return "mob"
    return "room"


def _message_error(message: dict[str, Any]) -> tuple[str, str] | None:
    message_type = str(message.get("type") or "").strip().lower()
    if not message_type.endswith(".error"):
        return None
    data = message.get("data")
    if not isinstance(data, dict):
        data = {}
    text = str(message.get("text") or data.get("error") or "Command failed.")
    code = str(data.get("code") or "command_failed")
    return text, code


def _message_event(
    message: dict[str, Any],
    *,
    actor_key: str,
) -> GameEvent | None:
    message_type = str(message.get("type") or "").strip()
    if not message_type:
        return None
    data = message.get("data")
    if not isinstance(data, dict):
        data = {}
    return GameEvent(
        type=message_type,
        recipients=[actor_key],
        data=data,
        text=(
            str(message["text"])
            if message.get("text") is not None
            else None
        ),
        group=(
            str(message["group"])
            if message.get("group") is not None
            else None
        ),
    )


def _normalize_one_command(command: str) -> str:
    normalized = str(command or "").strip()
    if not normalized:
        raise ScriptCommandError(
            "The command action has no command to execute.",
            code="invalid_command",
        )
    if len(normalized) > MAX_TRIGGER_COMMAND_LENGTH:
        raise ScriptCommandError(
            f"A command action cannot exceed {MAX_TRIGGER_COMMAND_LENGTH} characters.",
            code="command_too_long",
        )
    if "\n" in normalized or "\r" in normalized:
        raise ScriptCommandError(
            "A command action can execute only one command.",
            code="command_chain_not_allowed",
        )
    if "&&" in normalized or len(
        [segment for segment in split_cmd(normalized) if segment.strip()]
    ) != 1:
        raise ScriptCommandError(
            "Command chains are not allowed in a command action.",
            code="command_chain_not_allowed",
        )
    if normalized.startswith("!"):
        raise ScriptCommandError(
            "Command-history references are not allowed in a command action.",
            code="command_history_not_allowed",
        )
    return normalized


class ScriptCommandRunner:
    """
    Execute one audited command while capturing all output as GameEvents.

    A handler must explicitly declare ``trigger_step_mode = "events_only"``.
    That declaration is reserved for handlers which do not perform external
    side effects and whose database reads are safe inside a Trigger step.
    """

    def execute(
        self,
        *,
        issuer: Room,
        subject: Player | Mob | Room,
        command: str,
        render_actor: Player | Mob,
        runtime_world: World,
        provenance: dict[str, Any] | None = None,
    ) -> ScriptCommandResult:
        raw_depth = (provenance or {}).get("command_depth", 0)
        try:
            command_depth = max(0, int(raw_depth))
        except (TypeError, ValueError):
            command_depth = 0
        if command_depth >= MAX_SCRIPT_COMMAND_DEPTH:
            raise ScriptCommandError(
                "The scripted command recursion limit has been reached.",
                code="command_depth_exceeded",
            )

        rendered = str(
            format_actor_msg(
                command,
                render_actor,
                room=issuer,
                zone=issuer.zone,
                world=runtime_world,
            )
            or command
        ).strip()
        rendered = _normalize_one_command(rendered)
        command_token = rendered.split()[0].lower()
        subject_type = _actor_kind(subject)

        resolved = resolve_text_handler(command_token, include_builder=True)
        payload: dict[str, Any]
        command_type: str
        if resolved is None:
            if not isinstance(subject, (Player, Mob)):
                raise ScriptCommandError(
                    f"Unknown command: {command_token}",
                    code="unknown_command",
                )
            from spawns.socials import resolve_social_for_command

            social = resolve_social_for_command(runtime_world, command_token)
            if social is None:
                raise ScriptCommandError(
                    f"Unknown command: {command_token}",
                    code="unknown_command",
                )
            handler = get_handler("social")
            tokens = rendered.split()
            command_type = "social"
            payload = {
                "social": social["command"],
                "target": tokens[1] if len(tokens) > 1 else None,
            }
        else:
            resolved_command, handler = resolved
            if handler.command_type == "/cmd":
                raise ScriptCommandError(
                    "Nested /cmd dispatch is not allowed in a command action.",
                    code="nested_command_not_allowed",
                )
            command_type = "text"
            payload = {"text": rendered}

        if (
            getattr(handler, "trigger_step_mode", None)
            != TRIGGER_STEP_MODE_EVENTS_ONLY
        ):
            raise ScriptCommandError(
                f"The '{command_token}' command is not safe for Trigger steps.",
                code="command_not_step_safe",
            )
        if subject_type not in getattr(
            handler,
            "supported_actor_types",
            ("player",),
        ):
            raise ScriptCommandError(
                f"{subject_type.capitalize()}s cannot execute {command_token}.",
                code="unsupported_command_subject",
            )
        trigger_step_rejection = handler.validate_trigger_step_command(
            command=rendered,
            subject_type=subject_type,
        )
        if trigger_step_rejection is not None:
            message, code = trigger_step_rejection
            raise ScriptCommandError(message, code=code)

        payload.update({
            "skip_triggers": True,
            "suppress_aliases": True,
            "suppress_history": True,
            "issuer_scope": "room",
            "runtime_world_id": runtime_world.id,
        })
        if provenance:
            payload["_script_provenance"] = dict(provenance)

        captured_messages: list[dict[str, Any]] = []
        actor_kwargs: dict[str, Any]
        if subject_type == "room":
            actor_kwargs = {
                "actor_type": "room",
                "actor_id": subject.id,
            }
        else:
            actor_kwargs = {
                "subject_type": subject_type,
                "subject_id": subject.id,
            }
        try:
            with capture_game_events() as captured_events:
                dispatch_command(
                    command_type=command_type,
                    payload=payload,
                    issuer_type="room",
                    issuer_id=issuer.id,
                    published_messages=captured_messages,
                    script_source=True,
                    capture_only=True,
                    resolved_actor=subject,
                    resolved_issuer=issuer,
                    resolved_runtime_world=runtime_world,
                    **actor_kwargs,
                )
        except (ActorNotFoundError, HandlerNotFoundError, ValueError) as exc:
            raise ScriptCommandError(str(exc), code="command_dispatch_failed") from exc

        for message in captured_messages:
            error = _message_error(message)
            if error is not None:
                text, code = error
                raise ScriptCommandError(text, code=code)
        for event in captured_events:
            if str(event.type or "").lower().endswith(".error"):
                error = str(event.text or event.data.get("error") or "Command failed.")
                code = str(event.data.get("code") or "command_failed")
                raise ScriptCommandError(error, code=code)

        events = list(captured_events)
        for message in captured_messages:
            event = _message_event(message, actor_key=subject.key)
            if event is not None:
                events.append(event)
        next_depth = command_depth + 1
        script_provenance = {
            **deepcopy(provenance or {}),
            "source": "trigger_step",
            "issuer": {
                "type": "room",
                "id": issuer.id,
                "key": issuer.key,
            },
            "subject": {
                "type": subject_type,
                "id": subject.id,
                "key": subject.key,
            },
        }
        events = [
            GameEvent(
                type=event.type,
                recipients=event.recipients,
                data={
                    **deepcopy(event.data),
                    SCRIPT_COMMAND_DEPTH_KEY: next_depth,
                    SCRIPT_COMMAND_PROVENANCE_KEY: script_provenance,
                },
                text=event.text,
                group=event.group,
                connection_id=event.connection_id,
            )
            for event in events
        ]
        return ScriptCommandResult(
            command=rendered,
            events=tuple(events),
        )
