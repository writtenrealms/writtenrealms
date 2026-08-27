"""
Base classes for game command handlers.

Handlers follow the Command → Action → Event pattern from WR2 architecture.
Each handler processes a specific command type and publishes results via WebSocket.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from spawns.events import correlate_actor_command_message
from spawns.models import Mob, Player
from worlds.models import Room, World, Zone
from fastapi_app.game_ws import publish_to_player


CommandActor = Player | Mob | Room | Zone | World
TRIGGER_STEP_MODE_EVENTS_ONLY = "events_only"
TRIGGER_STEP_MODE_TRANSACTIONAL = "transactional"
TRIGGER_STEP_MODES = frozenset({
    TRIGGER_STEP_MODE_EVENTS_ONLY,
    TRIGGER_STEP_MODE_TRANSACTIONAL,
})


@dataclass
class CommandContext:
    """
    Resolved command context.

    ``issuer`` records who initiated the intent. ``subject`` records the
    embodied player or mob performing it, when present. ``actor`` remains the
    compatibility execution object used by existing handlers.
    ``builder_force`` is trusted dispatcher provenance for an interactive
    builder commanding a selected character; it is never read from payloads.
    """
    actor: CommandActor
    actor_type: str
    actor_id: int
    actor_key: str
    payload: dict
    connection_id: str | None = None
    player: Player | None = None
    mob: Mob | None = None
    room: Room | None = None
    zone: Zone | None = None
    world: World | None = None
    published_messages: list[dict] | None = None
    script_source: bool = False
    capture_only: bool = False
    trigger_step: bool = False
    builder_force: bool = False
    issuer: CommandActor | None = None
    issuer_type: str | None = None
    issuer_id: int | None = None
    issuer_key: str | None = None
    subject: CommandActor | None = None
    subject_type: str | None = None
    subject_id: int | None = None
    subject_key: str | None = None

    def publish(self, message: dict) -> None:
        """Publish a message to this actor channel (if connected) and optional capture sink."""
        message = correlate_actor_command_message(
            message,
            actor_key=self.actor_key,
        )
        if self.published_messages is not None:
            self.published_messages.append(message)
        if self.capture_only:
            return
        publish_to_player(self.actor_key, message, connection_id=self.connection_id)

    def publish_success(self, command_type: str, data: dict, text: str | None = None) -> None:
        """Publish a success response for the given command type."""
        message = {
            "type": f"cmd.{command_type}.success",
            "data": data,
        }
        if text:
            message["text"] = text
        self.publish(message)

    def publish_error(self, command_type: str, error: str) -> None:
        """Publish an error response for the given command type."""
        self.publish({
            "type": f"cmd.{command_type}.error",
            "text": error,
            "data": {"error": error},
        })


class CommandHandler(ABC):
    """
    Base class for game command handlers.

    Subclasses must implement:
    - command_type: class attribute identifying the command (e.g., "state.sync")
    - handle(): method containing the command logic

    Example:
        @register_handler
        class LookHandler(CommandHandler):
            command_type = "look"

            def handle(self, ctx: CommandContext) -> None:
                room = ctx.player.room
                ctx.publish_success("look", {"room": serialize_room(room)})
    """
    command_type: str
    text_commands: tuple[str, ...] = ()
    text_aliases: Mapping[str, str] = {}
    builder_only: bool = False
    allow_script_source: bool = False
    allow_mob_actor: bool = False
    supported_actor_types: tuple[str, ...] = ("player",)
    # Only audited handlers whose output is capturable and whose work is fully
    # covered by the caller's transaction may opt into Trigger-step execution.
    # ``events_only`` handlers do not mutate durable game state.
    # ``transactional`` handlers may mutate state but must not perform
    # irreversible external work before commit.
    trigger_step_mode: str | None = None
    help: dict[str, Any] | None = None

    @abstractmethod
    def handle(self, ctx: CommandContext) -> None:
        """
        Process the command.

        Args:
            ctx: CommandContext with player, payload, and publish utilities.

        The handler should call ctx.publish_success() or ctx.publish_error()
        to send results back to the client.
        """
        pass

    def validate_trigger_step_command(
        self,
        *,
        command: str,
        subject_type: str,
        subject_key: str,
        render_actor_key: str,
    ) -> tuple[str, str] | None:
        """Return an optional (message, code) rejection for Trigger steps."""
        return None

    @classmethod
    def get_help_data(cls, *, command_name: str | None = None) -> dict[str, Any] | None:
        if not cls.help:
            return None

        help_data = dict(cls.help)
        if command_name and "command" not in help_data:
            help_data["command"] = command_name
        if "name" not in help_data:
            if command_name:
                help_data["name"] = command_name.lstrip("/").replace(".", " ").title()
            else:
                help_data["name"] = cls.__name__.replace("Handler", "")
        if command_name and "format" not in help_data:
            help_data["format"] = command_name
        return help_data

    @classmethod
    def get_help_text(cls, *, command_name: str | None = None) -> str:
        help_data = cls.get_help_data(command_name=command_name)
        if not help_data:
            label = command_name or cls.__name__
            return f"No help available for {label}."

        lines = [help_data.get("name", cls.__name__)]
        help_format = help_data.get("format")
        if help_format:
            lines.append(f"Format: {help_format}")
        description = help_data.get("description")
        if description:
            lines.append(f"Description: {description}")
        details = help_data.get("details") or []
        if details:
            lines.append("Details:")
            lines.extend(str(detail) for detail in details)
        examples = help_data.get("examples") or []
        if examples:
            lines.append("Examples:")
            lines.extend(str(example) for example in examples)
        return "\n".join(lines)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Ensure subclasses define command_type
        if not getattr(cls, 'command_type', None) and not getattr(cls, '__abstractmethods__', None):
            raise TypeError(f"{cls.__name__} must define 'command_type' class attribute")


class ChoiceResolutionError(ValueError):
    def __init__(self, token: str, *, code: str, matches: Sequence[str] | None = None):
        self.token = token
        self.code = code
        self.matches = list(matches or [])
        super().__init__(token)


def resolve_unambiguous_choice(
    token: str | None,
    *,
    choices: Sequence[str],
    aliases: Mapping[str, str] | None = None,
) -> str:
    normalized = str(token or "").strip().lower()
    if not normalized:
        raise ChoiceResolutionError(normalized, code="missing_choice")

    canonical = {str(choice).strip().lower(): str(choice).strip().lower() for choice in choices if str(choice).strip()}
    alias_map = {
        str(alias).strip().lower(): str(target).strip().lower()
        for alias, target in (aliases or {}).items()
        if str(alias).strip() and str(target).strip()
    }

    if normalized in canonical:
        return canonical[normalized]
    if normalized in alias_map:
        return alias_map[normalized]

    matches = [choice for choice in canonical.values() if choice.startswith(normalized)]
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ChoiceResolutionError(normalized, code="ambiguous_choice", matches=matches)
    raise ChoiceResolutionError(normalized, code="unknown_choice")
