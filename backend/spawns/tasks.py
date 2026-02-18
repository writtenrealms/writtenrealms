import os

from celery import shared_task

from backend.config.exceptions import ServiceError
from spawns.services import WorldGate
from spawns.models import Player
from spawns.serializers import PlayerConfigSerializer
from spawns.handlers import (
    dispatch_command,
    HandlerNotFoundError,
    PlayerNotFoundError,
)
from worlds.models import World
from worlds.serializers import WorldSerializer

from fastapi_app.game_ws import publish_to_player
from fastapi_app.forge_ws import complete_job, exit_world as notify_exit_world

@shared_task
def enter_world(player_id, world_id, client_id=None, ip=None):
    print("enter_world IDs: player_id=%s, world_id=%s" % (player_id, world_id))

    player = Player.objects.get(pk=player_id)

    if not player.world.context:
        raise RuntimeError('Player is not in a spawn world.')

    spawn_world = player.world

    print("%s [ %s ] entering %s [ %s ]" % (player.name, player.id, spawn_world.name, spawn_world.id))

    try:
        # Enter the world
        WorldGate(player=player, world=spawn_world).enter(ip=ip)

        # - Instance Follow system -
        # This whether this instance assignment that had followers with
        # it was created
        assignment = player.player_instances.filter(
            instance=spawn_world
        ).first()
        if assignment and assignment.member_ids:
            game_world = spawn_world.game_world
            for member_id in assignment.member_ids.split():
                print('sending out command for %s to %s', (member_id, spawn_world.instance_ref))
                # add_timing(
                #     type='timing.defer',
                #     world=game_world.key,
                #     data={
                #         'actor': 'player.%s' % member_id,
                #         'cmd': 'enter %s' % spawn_world.instance_ref
                #     },
                #     db=game_world.db,)
            assignment.member_ids = None
            assignment.save()

        if client_id:
            # Determine the websocket uri
            host = os.getenv('WR_HOST', 'localhost')
            if host == 'localhost':
                ws_uri = 'ws://localhost:8001/ws/game/cmd'
            else:
                ws_uri = f'wss://{host}/ws/game/cmd'

            complete_job(
                client_id=client_id,
                job="enter_world",
                data={
                    "world": WorldSerializer(spawn_world).data,
                    "player_config": PlayerConfigSerializer(player.config).data,
                    "player_id": player.id,
                    "ws_uri": ws_uri,
                    "motd": spawn_world.context.motd,
                })

    except ServiceError as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="enter_world",
                status='error',
                data={'error': str(e)})
        else:
            print('error entering world:', str(e))


@shared_task
def exit_world(player_id, world_id,
               player_data_id=None,
               transfer_to=None,
               transfer_from=None,
               ref=None,
               leave_instance=False,
               member_ids=None):
    """
    Unlike the enter_world task, there is no client id being passed in.
    This is because we can't rely on it. Users may use the 'quit' command
    or the quit option from the menu, but they also may just close out the
    tab. Because of that, the trigger to exit a world has to be the
    game websocket having been severed, not the forge websocket.
    """
    player = Player.objects.get(pk=player_id)
    world = World.objects.get(pk=world_id)

    print("%s [ %s ] exiting %s [ %s ]" % (
        player.name,
        player.id,
        world.name,
        world.id))

    world_gate = WorldGate(player=player, world=world)
    world_gate.exit(player_data_id=player_data_id,
                    transfer_to=transfer_to,
                    transfer_from=transfer_from,
                    ref=ref,
                    leave_instance=leave_instance,
                    member_ids=member_ids)

    # Notify frontend
    notify_exit_world(
        player_id=player_id,
        world_id=world_id,
        exit_to=world.context.id)


def _parse_player_id(player_key: str | None) -> int | None:
    if not player_key:
        return None
    if not player_key.startswith("player."):
        return None
    try:
        return int(player_key.split(".", 1)[1])
    except (IndexError, ValueError):
        return None


def _publish_game_error(player_key: str | None, command_type: str, text: str, connection_id: str | None = None):
    """Publish an error message to a player's WebSocket connection."""
    if not player_key:
        return
    publish_to_player(
        player_key,
        {
            "type": f"cmd.{command_type}.error",
            "text": text,
            "data": {"error": text},
        },
        connection_id=connection_id,
    )


@shared_task
def handle_game_command(
    command_type: str,
    player_id: int | None = None,
    player_key: str | None = None,
    payload: dict | None = None,
    connection_id: str | None = None,
):
    """
    Celery task entry point for game commands.

    This is a thin wrapper that:
    1. Resolves player_id/player_key
    2. Dispatches to the appropriate handler
    3. Catches and publishes errors

    All command logic lives in spawns.handlers package.
    """
    payload = payload or {}

    # Resolve player_key <-> player_id
    if not player_key and player_id:
        player_key = f"player.{player_id}"
    if not player_id and player_key:
        player_id = _parse_player_id(player_key)

    # Validate we have a player_id
    if not player_id:
        _publish_game_error(
            player_key,
            command_type,
            "Missing player_id for command.",
            connection_id=connection_id,
        )
        return

    # Dispatch to handler
    try:
        dispatch_command(
            command_type=command_type,
            player_id=player_id,
            payload=payload,
            connection_id=connection_id,
        )
    except PlayerNotFoundError as e:
        _publish_game_error(
            player_key,
            command_type,
            str(e),
            connection_id=connection_id,
        )
    except HandlerNotFoundError as e:
        _publish_game_error(
            player_key,
            command_type,
            f"Unhandled command: {command_type}",
            connection_id=connection_id,
        )
