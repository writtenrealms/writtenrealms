from contextlib import contextmanager
from typing import Generator
from unittest.mock import patch

from spawns.handlers import dispatch_command


@contextmanager
def capture_game_messages() -> Generator[list[dict], None, None]:
    messages: list[dict] = []

    def _capture(player_key: str, message: dict, connection_id: str | None = None) -> None:
        messages.append(
            {
                "player_key": player_key,
                "message": message,
                "connection_id": connection_id,
            }
        )

    with patch("spawns.events.publish_to_player", side_effect=_capture), patch(
        "spawns.handlers.base.publish_to_player",
        side_effect=_capture,
    ):
        yield messages


def dispatch_text_command(player_id: int, text: str) -> None:
    dispatch_command(
        command_type="text",
        player_id=player_id,
        payload={"text": text},
    )


def dispatch_text_command_as_mob(mob_id: int, text: str) -> None:
    dispatch_command(
        command_type="text",
        actor_type="mob",
        actor_id=mob_id,
        payload={"text": text},
    )
