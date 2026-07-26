from spawns.actions.base import ActionError
from spawns.actions.targeting import find_room_player_target
from spawns.duels import (
    accept_duel,
    cancel_duel,
    challenge_duel,
    decline_duel,
    duel_status_text,
    surrender_duel,
)
from spawns.events import GameEvent, publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.models import Player
from spawns.state_payloads import build_state_sync, get_player_with_related
from spawns.text_output import render_event_text


def _state_sync_events(player_ids: tuple[int, ...]) -> list[GameEvent]:
    events: list[GameEvent] = []
    for player_id in player_ids:
        player = get_player_with_related(player_id)
        payload = build_state_sync(player).model_dump()
        events.append(
            GameEvent(
                type="cmd.state.sync.success",
                recipients=[player.key],
                data=payload,
                text=render_event_text(
                    "cmd.state.sync.success",
                    payload,
                    viewer=player,
                ),
            )
        )
    return events


def _room_player_target(
    player: Player,
    selector: str,
    *,
    required: bool,
) -> Player | None:
    if not player.room_id:
        if required:
            raise ActionError("You are nowhere.", code="no_room")
        return None
    target = find_room_player_target(
        player.room,
        selector,
        world=player.world,
        exclude=player,
    )
    if target is None and required:
        raise ActionError("You do not see them here.", code="target_missing")
    return target


@register_handler
class DuelHandler(CommandHandler):
    command_type = "duel"
    text_commands = ("duel",)
    help = {
        "name": "Duel",
        "format": (
            "duel <player> | duel status | duel accept [challenger] | "
            "duel decline [challenger] | duel cancel | duel surrender"
        ),
        "description": "Challenge another player to a private instanced duel.",
        "details": [
            "Both players must stand at the same dueling arena entrance.",
            "Both players must be out of combat and free of hostile ongoing effects when accepting.",
            "Accepting creates a fresh private arena run for the two contestants.",
            "`flee` breaks the current combat engagement but does not forfeit the duel.",
            "`duel status` shows the current match and lifetime fought, won, and lost totals.",
            "Use `duel surrender` to concede an active duel.",
        ],
        "examples": [
            "duel alex",
            "duel status",
            "duel accept",
            "duel accept alex",
            "duel decline",
            "duel cancel",
            "duel surrender",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = [str(arg) for arg in (ctx.payload.get("args") or [])]
        player = (
            Player.objects.select_related("world", "room")
            .get(pk=ctx.player.id)
        )
        subcommand = args[0].strip().lower() if args else ""
        selector = " ".join(args[1:]).strip()

        try:
            if subcommand == "accept":
                challenger = _room_player_target(
                    player,
                    selector,
                    required=True,
                ) if selector else None
                result = accept_duel(
                    player.id,
                    challenger_id=challenger.id if challenger else None,
                )
            elif subcommand == "decline":
                challenger = _room_player_target(
                    player,
                    selector,
                    required=True,
                ) if selector else None
                result = decline_duel(
                    player.id,
                    challenger_id=challenger.id if challenger else None,
                )
            elif subcommand == "cancel":
                result = cancel_duel(player.id)
            elif subcommand in {"surrender", "concede", "forfeit"}:
                result = surrender_duel(player.id)
            elif not args or subcommand in {"status", "info"}:
                ctx.publish_success(
                    "duel",
                    {"status": "info"},
                    duel_status_text(player),
                )
                return
            else:
                target = _room_player_target(
                    player,
                    " ".join(args).strip(),
                    required=True,
                )
                result = challenge_duel(player.id, target.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.duel.error",
                    "text": err.message,
                    "data": {
                        "error": err.message,
                        "code": err.code,
                        **err.data,
                    },
                }
            )
            return

        events = [
            *result.events,
            *_state_sync_events(result.state_sync_player_ids),
        ]
        if events:
            publish_events(
                events,
                actor_key=ctx.player.key,
                connection_id=ctx.connection_id,
            )
        elif subcommand in {"surrender", "concede", "forfeit"}:
            ctx.publish_success(
                "duel",
                {"match_id": result.match_id, "status": "surrendered"},
                "You surrender the duel.",
            )
