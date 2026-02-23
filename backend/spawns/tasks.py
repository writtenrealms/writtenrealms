import os
import json
import uuid
from urllib import error as urllib_error
from urllib import request as urllib_request

from celery import shared_task

from backend.config.exceptions import ServiceError
from django.conf import settings
from django.utils import timezone
from spawns.services import WorldGate
from spawns.models import Player
from spawns.serializers import PlayerConfigSerializer
from spawns.handlers import (
    ActorNotFoundError,
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


def _resolve_world_key_for_player(player: Player) -> str:
    player_world = getattr(player, "world", None)
    if not player_world:
        return ""

    context_world = getattr(player_world, "context", None)
    if context_world:
        root_world = getattr(context_world, "instance_of", None) or context_world
        return root_world.key

    root_world = getattr(player_world, "instance_of", None) or player_world
    return root_world.key


def _build_ai_forward_payload(
    *,
    event_type: str,
    event_data: dict,
    actor_key: str,
    player: Player,
) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4()}",
        "event_type": event_type,
        "world_key": _resolve_world_key_for_player(player),
        "room_key": player.room.key if player.room_id else "",
        "timestamp": timezone.now().isoformat(),
        "actor": {
            "key": actor_key,
            "name": player.name,
            "kind": "player",
        },
        "payload": event_data,
    }


@shared_task
def forward_event_to_ai_sidecar(
    *,
    event_type: str,
    event_data: dict | None = None,
    actor_key: str | None = None,
) -> None:
    """
    Forward selected game events to the WR AI sidecar.

    This task is intentionally fire-and-forget and should not raise errors
    that could impact gameplay flows.
    """
    forward_url = str(getattr(settings, "WR_AI_EVENT_FORWARD_URL", "") or "").strip()
    if not forward_url:
        return

    player_id = _parse_player_id(actor_key)
    if not player_id:
        return

    player = (
        Player.objects.select_related("room", "world__context__instance_of")
        .filter(pk=player_id)
        .first()
    )
    if not player:
        return

    payload = _build_ai_forward_payload(
        event_type=str(event_type or "").strip().lower(),
        event_data=event_data if isinstance(event_data, dict) else {},
        actor_key=str(actor_key or ""),
        player=player,
    )
    body = json.dumps(payload).encode("utf-8")

    request = urllib_request.Request(
        forward_url,
        data=body,
        method="POST",
    )
    request.add_header("Content-Type", "application/json")

    forward_token = str(getattr(settings, "WR_AI_EVENT_FORWARD_TOKEN", "") or "").strip()
    if forward_token:
        request.add_header("Authorization", f"Bearer {forward_token}")

    try:
        with urllib_request.urlopen(request, timeout=2.0):
            return
    except (urllib_error.URLError, ValueError):
        return


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
def execute_trigger_script_segments(
    actor_type: str,
    actor_id: int,
    segments: list[str],
    issuer_scope: str | None = None,
    connection_id: str | None = None,
):
    """
    Execute scripted trigger segments as a delayed trigger line.
    """
    for segment in segments or []:
        segment_text = str(segment or "").strip()
        if not segment_text:
            continue

        payload: dict[str, object] = {
            "text": segment_text,
            "skip_triggers": True,
            "__trigger_source": True,
        }
        if issuer_scope:
            payload["issuer_scope"] = issuer_scope

        try:
            dispatch_command(
                command_type="text",
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
                connection_id=connection_id,
            )
        except (ActorNotFoundError, HandlerNotFoundError, ValueError):
            return


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
