from contextlib import contextmanager
from copy import deepcopy
from typing import Generator
from unittest.mock import patch

from spawns.handlers import dispatch_command


BASIC_TEST_STAT_SYSTEM = {
    "input_attributes": [
        {"key": "brawn", "label": "Brawn"},
        {"key": "grit", "label": "Grit"},
        {"key": "focus", "label": "Focus"},
    ],
    "class_profiles": {
        "warrior": {
            "label": "Warrior",
            "main_attribute": "brawn",
            "base_attribute_weights": {
                "brawn": 2,
                "grit": 4,
                "focus": 2,
            },
        },
    },
    "formulas": {
        "base_resources": {
            "energy": {"source": "focus", "multiplier": 5},
            "stamina": {"flat": 100},
            "health": {},
        },
        "global_rules": [
            {"source": "brawn", "target": "attack_power", "multiplier": 1},
            {"source": "grit", "target": "health_max", "multiplier": 2},
            {"source": "focus", "target": "ability_power", "multiplier": 1},
        ],
    },
}


def apply_basic_stat_system(world) -> None:
    world.config.stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
    world.config.save(update_fields=["stat_system"])


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
