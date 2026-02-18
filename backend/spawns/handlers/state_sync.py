"""
State synchronization handler.

Handles the initial state sync when a player connects or reconnects to a world.
"""
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.state_payloads import build_state_sync, get_player_with_related
from spawns.text_output import render_event_text


@register_handler
class StateSyncHandler(CommandHandler):
    """
    Handle state.sync command - synchronize full game state to client.

    This is called when:
    - Player first connects to a world
    - Player reconnects after disconnect
    - Client explicitly requests state refresh
    """
    command_type = "state.sync"

    def handle(self, ctx: CommandContext) -> None:
        player = get_player_with_related(ctx.player.id)
        state = build_state_sync(player)
        payload = state.model_dump()
        text = render_event_text("cmd.state.sync.success", payload, viewer=player)
        ctx.publish_success("state.sync", payload, text=text)
