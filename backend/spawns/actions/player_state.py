from __future__ import annotations

from config import constants as adv_consts
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.effects import actor_is_combat_tagged
from spawns.events import GameEvent
from spawns.models import CombatEncounter, CombatParticipant, Player
from spawns.state_payloads import get_player_with_related, serialize_actor


def stand_player(player: Player) -> bool:
    if getattr(player, "state", adv_consts.CHARACTER_STATE_STANDING) == adv_consts.CHARACTER_STATE_STANDING:
        return False
    player.state = adv_consts.CHARACTER_STATE_STANDING
    player.save(update_fields=["state"])
    return True


def _active_player_encounter_exists(player: Player) -> bool:
    if CombatEncounter.objects.filter(
        player=player,
        status=CombatEncounter.STATUS_ACTIVE,
        world_id=player.world_id,
        room_id=player.room_id,
        mob__world_id=player.world_id,
        mob__room_id=player.room_id,
        mob__is_pending_deletion=False,
        mob__health__gt=0,
    ).exists():
        return True
    return CombatParticipant.objects.filter(
        player=player,
        is_active=True,
        encounter__status=CombatEncounter.STATUS_ACTIVE,
        encounter__world_id=player.world_id,
        encounter__room_id=player.room_id,
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
        if _active_player_encounter_exists(player) or actor_is_combat_tagged(player):
            raise ActionError("You cannot rest in combat.", code="in_combat")

        from spawns.actions.doors import cancel_pending_player_door_action

        cancellation_events = cancel_pending_player_door_action(
            player=player,
            code="physical_action_replaced",
            message="You stop working with the door to rest.",
        )
        if player.state != adv_consts.CHARACTER_STATE_RESTING:
            player.state = adv_consts.CHARACTER_STATE_RESTING
            player.save(update_fields=["state"])

        return ActionResult(
            events=[
                *cancellation_events,
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
        from spawns.actions.doors import cancel_pending_player_door_action

        cancellation_events = cancel_pending_player_door_action(
            player=player,
            code="physical_action_replaced",
            message="You stop working with the door to stand.",
        )
        stand_player(player)
        return ActionResult(
            events=[
                *cancellation_events,
                _state_event(
                    player=player,
                    command_type="stand",
                    text="You stand up.",
                )
            ]
        )
