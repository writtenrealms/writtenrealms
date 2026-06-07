from __future__ import annotations

from config import constants as adv_consts
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.models import CombatEncounter, Player
from spawns.state_payloads import get_player_with_related, serialize_actor


def stand_player(player: Player) -> bool:
    if getattr(player, "state", adv_consts.CHARACTER_STATE_STANDING) == adv_consts.CHARACTER_STATE_STANDING:
        return False
    player.state = adv_consts.CHARACTER_STATE_STANDING
    player.save(update_fields=["state"])
    return True


def _active_player_encounter_exists(player: Player) -> bool:
    return CombatEncounter.objects.filter(
        player=player,
        status=CombatEncounter.STATUS_ACTIVE,
    ).exists()


def _state_event(*, player: Player, command_type: str, text: str) -> GameEvent:
    updated_player = get_player_with_related(player.id)
    return GameEvent(
        type=f"cmd.{command_type}.success",
        recipients=[updated_player.key],
        data={"actor": serialize_actor(updated_player, updated_player.room).model_dump()},
        text=text,
    )


class RestAction:
    def execute(self, player_id: int) -> ActionResult:
        player = Player.objects.select_for_update().get(pk=player_id)
        if _active_player_encounter_exists(player):
            raise ActionError("You cannot rest in combat.", code="in_combat")

        if player.state != adv_consts.CHARACTER_STATE_RESTING:
            player.state = adv_consts.CHARACTER_STATE_RESTING
            player.save(update_fields=["state"])

        return ActionResult(
            events=[
                _state_event(
                    player=player,
                    command_type="rest",
                    text="You begin resting.",
                )
            ]
        )


class StandAction:
    def execute(self, player_id: int) -> ActionResult:
        player = Player.objects.select_for_update().get(pk=player_id)
        stand_player(player)
        return ActionResult(
            events=[
                _state_event(
                    player=player,
                    command_type="stand",
                    text="You stand up.",
                )
            ]
        )
