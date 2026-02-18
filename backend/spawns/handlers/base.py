"""
Base classes for game command handlers.

Handlers follow the Command → Action → Event pattern from WR2 architecture.
Each handler processes a specific command type and publishes results via WebSocket.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from spawns.models import Mob, Player
from fastapi_app.game_ws import publish_to_player


CommandActor = Player | Mob


@dataclass
class CommandContext:
    """
    Context passed to command handlers containing resolved actor and request metadata.
    """
    actor: CommandActor
    actor_type: str
    actor_id: int
    actor_key: str
    payload: dict
    connection_id: str | None = None
    player: Player | None = None
    mob: Mob | None = None
    published_messages: list[dict] | None = None

    def publish(self, message: dict) -> None:
        """Publish a message to this actor channel (if connected) and optional capture sink."""
        if self.published_messages is not None:
            self.published_messages.append(message)
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
    builder_only: bool = False
    supported_actor_types: tuple[str, ...] = ("player",)
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
