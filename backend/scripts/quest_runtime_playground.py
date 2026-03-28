import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings.local")
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))
if "/code" not in sys.path:
    sys.path.append("/code")
if "/code/backend" not in sys.path:
    sys.path.append("/code/backend")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from quests.services.discovery import list_opportunities
from quests.services.engine import (
    QuestRuntimeError,
    info_for_player,
    list_active_instances,
    list_completed_instances,
)
from spawns.handlers import dispatch_command
from spawns.models import Player
from users.models import User
from worlds.models import World


@contextmanager
def _capture_messages():
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


def _player(player_id: int) -> Player:
    return Player.objects.get(pk=player_id)


def _print_json(payload):
    print(json.dumps(payload, indent=2, default=str))


def cmd_players(args):
    qs = Player.objects.select_related("world", "room", "user").order_by("id")
    if args.world is not None:
        qs = qs.filter(world_id=args.world)
    for player in qs:
        print(
            f"{player.id}\t{player.key}\t{player.name}\tworld={player.world_id}\troom={player.room_id}\tin_game={player.in_game}"
        )
    if not qs.exists():
        print("No players found.")


def cmd_ensure_player(args):
    world = World.objects.get(pk=args.world)
    template_world = world.context or world
    spawn_world = world if world.context else world.spawned_worlds.filter(is_multiplayer=True).first()
    if spawn_world is None:
        spawn_world = world.create_spawn_world()

    room = template_world.rooms.order_by("id").first()
    if room is None:
        raise SystemExit("World has no rooms.")

    user = User.objects.filter(email=args.email).first()
    if user is None:
        user = User.objects.create(
            email=args.email,
            is_temporary=False,
        )
        if hasattr(user, "set_unusable_password"):
            user.set_unusable_password()
            user.save(update_fields=["password"])
    player = Player.objects.filter(
        user=user,
        world=spawn_world,
        name=args.name,
    ).first()
    if player is None:
        player = Player.objects.create(
            name=args.name,
            room=room,
            user=user,
            world=spawn_world,
            in_game=True,
        )
    else:
        player.room = room
        player.in_game = True
        player.save(update_fields=["room", "in_game"])

    print(
        f"{player.id}\t{player.key}\t{player.name}\tuser={user.email}\tspawn_world={spawn_world.id}\troom={room.id}"
    )


def cmd_dispatch(args):
    with _capture_messages() as messages:
        dispatch_command(
            command_type="text",
            player_id=args.player,
            payload={"text": args.text},
        )

    if not messages:
        print("No messages published.")
        return

    for item in messages:
        message = item["message"]
        print(f"[{message.get('type')}]")
        text = message.get("text")
        if text:
            print(text)
        else:
            _print_json(message.get("data") or {})
        print()


def cmd_opportunities(args):
    player = _player(args.player)
    _print_json({"opportunities": list_opportunities(player, refresh=True)})


def cmd_active(args):
    player = _player(args.player)
    _print_json({"quests": list_active_instances(player)})


def cmd_resolved(args):
    player = _player(args.player)
    _print_json({"quests": list_completed_instances(player)})


def cmd_info(args):
    player = _player(args.player)
    try:
        _, text = info_for_player(player, args.identity)
    except QuestRuntimeError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}")
    print(text)


def main():
    parser = argparse.ArgumentParser(description="Playground for Phase 2 quest runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    players_parser = subparsers.add_parser("players", help="List players")
    players_parser.add_argument("--world", type=int)
    players_parser.set_defaults(func=cmd_players)

    ensure_player_parser = subparsers.add_parser("ensure-player", help="Create or reuse a dev player")
    ensure_player_parser.add_argument("--world", type=int, required=True)
    ensure_player_parser.add_argument("--email", required=True)
    ensure_player_parser.add_argument("--name", required=True)
    ensure_player_parser.set_defaults(func=cmd_ensure_player)

    dispatch_parser = subparsers.add_parser("cmd", help="Dispatch a text command as a player")
    dispatch_parser.add_argument("--player", type=int, required=True)
    dispatch_parser.add_argument("text")
    dispatch_parser.set_defaults(func=cmd_dispatch)

    opp_parser = subparsers.add_parser("opportunities", help="List current quest opportunities")
    opp_parser.add_argument("--player", type=int, required=True)
    opp_parser.set_defaults(func=cmd_opportunities)

    active_parser = subparsers.add_parser("active", help="List active quests")
    active_parser.add_argument("--player", type=int, required=True)
    active_parser.set_defaults(func=cmd_active)

    resolved_parser = subparsers.add_parser("resolved", help="List resolved quests")
    resolved_parser.add_argument("--player", type=int, required=True)
    resolved_parser.set_defaults(func=cmd_resolved)

    info_parser = subparsers.add_parser("info", help="Print quest info")
    info_parser.add_argument("--player", type=int, required=True)
    info_parser.add_argument("identity", nargs="?")
    info_parser.set_defaults(func=cmd_info)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
