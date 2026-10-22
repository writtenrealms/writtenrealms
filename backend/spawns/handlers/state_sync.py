"""
State synchronization handler.

Handles the initial state sync when a player connects or reconnects to a world.
"""
from django.db import transaction

from spawns.events import GameEvent, publish_events
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
        from worlds.instances import emit_pending_initial_instance_entry

        if emit_pending_initial_instance_entry(ctx.player):
            # Entry reactions flush on commit. Queue the snapshot after them,
            # including an outer time-controlled instance clock transaction.
            transaction.on_commit(lambda: self._publish_snapshot(ctx, initial_entry=True))
        else:
            self._publish_snapshot(ctx)

    def _publish_snapshot(self, ctx: CommandContext, *, initial_entry=False) -> None:
        player = get_player_with_related(ctx.player.id)
        is_instance = bool(player.world.context.instance_of_id)
        returning = bool(
            is_instance and not initial_entry
            and ctx.payload.get('connection_entry') is True
            and ctx.payload.get('reconnect') is not True
        )
        events = []
        if returning:
            from worlds.entry_messages import returning_instance_message

            events.append(GameEvent(
                type='cmd.enter.success', recipients=[player.key],
                data={'world_id': player.world_id,
                      'instance_ref': player.world.instance_ref,
                      'first_entry': False},
                text=f'Resuming Instance ID: {player.world.instance_ref}',
            ))
            introduction = returning_instance_message(player)
            if introduction:
                events.append(GameEvent(
                    type='notification.instance.introduction',
                    recipients=[player.key], data={}, text=introduction,
                ))
        state = build_state_sync(player)
        payload = state.model_dump()
        if is_instance:
            payload['instance_entry'] = 'initial' if initial_entry else 'return' if returning else 'sync'
        text = render_event_text("cmd.state.sync.success", payload, viewer=player)
        publish_events(
            [
                *events,
                GameEvent(
                    type="cmd.state.sync.success",
                    recipients=[player.key],
                    data=payload,
                    text=text,
                )
            ],
            actor_key=player.key,
            connection_id=ctx.connection_id,
        )
