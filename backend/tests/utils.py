from contextlib import contextmanager
from copy import deepcopy
from typing import Generator
from unittest.mock import patch

from django.utils import timezone
from spawns.handlers import dispatch_command
from spawns.models import ActiveEffect, Mob, Player


BASIC_TEST_STAT_SYSTEM = {
    "attributes": [
        {"key": "brawn", "label": "Brawn"},
        {"key": "grit", "label": "Grit"},
        {"key": "focus", "label": "Focus"},
    ],
    "class_profiles": {
        "warrior": {
            "label": "Warrior",
            "main_attribute": "brawn",
            "attribute_weights": {
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


def create_active_effect(*, target, payload, source=None, encounter=None, scope=None):
    source_ref = payload.get("source") or {}
    if source is None and source_ref.get("id"):
        if source_ref.get("type") == "player":
            source = Player.objects.filter(pk=source_ref["id"]).first()
        elif source_ref.get("type") == "mob":
            source = Mob.objects.filter(pk=source_ref["id"]).first()
    duration = max(
        1,
        int(payload.get("duration_rounds") or payload.get("remaining_rounds") or 1),
    )
    return ActiveEffect.objects.create(
        world=target.world,
        encounter=encounter,
        source_player=source if isinstance(source, Player) else None,
        source_mob=source if isinstance(source, Mob) else None,
        target_player=target if isinstance(target, Player) else None,
        target_mob=target if isinstance(target, Mob) else None,
        scope=scope or payload.get("scope") or ActiveEffect.SCOPE_CHARACTER,
        effect=payload.get("effect") or "effect",
        category=payload.get("category") or "neutral",
        label=payload.get("label") or payload.get("effect") or "Effect",
        stack_key=payload.get("stack_key") or "",
        stacking=payload.get("stacking") or "independent",
        remaining_rounds=max(1, int(payload.get("remaining_rounds") or duration)),
        duration_rounds=duration,
        rounds_elapsed=max(0, int(payload.get("rounds_elapsed") or 0)),
        started_round=max(0, int(payload.get("started_round") or 0)),
        started_round_id=payload.get("started_round_id") or "",
        primitives=deepcopy(payload.get("primitives") or []),
        tick=deepcopy(payload.get("tick") or {}),
        source_snapshot={
            "ref": source_ref,
            "key": getattr(source, "key", ""),
            "name": getattr(source, "name", ""),
        },
        is_hostile=(payload.get("effect") == "dot"),
        next_tick_ts=timezone.now(),
    )


def replace_active_effects(*, target, payloads, source=None):
    if isinstance(target, Player):
        ActiveEffect.objects.filter(target_player=target).delete()
    else:
        ActiveEffect.objects.filter(target_mob=target).delete()
    return [
        create_active_effect(target=target, payload=payload, source=source)
        for payload in payloads
    ]
