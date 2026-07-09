#!/usr/bin/env python3
"""Read and update Written Realms room descriptions through Django in Docker."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any


SENTENCE_RE = re.compile(r"[.!?]+(?:\s+|$)")
ORIGIN_ASSUMPTION_RE = re.compile(
    r"\b("
    r"back\s+(?:toward|to|into|through|down|up|along)|"
    r"returns?\s+(?:toward|to|into|through|down|up|along)|"
    r"continues?\s+from|"
    r"came\s+from|"
    r"entered\s+from|"
    r"arrived\s+from|"
    r"ahead"
    r")\b",
    re.IGNORECASE,
)


def sentence_count(text: str) -> int:
    return len([part for part in SENTENCE_RE.split(text.strip()) if part.strip()])


def validate_description(text: str) -> list[str]:
    warnings: list[str] = []
    if not text.strip():
        warnings.append("missing description")
        return warnings

    if "\n\n" in text:
        warnings.append("description contains a blank line")
    if "\n" in text:
        warnings.append("description spans multiple lines; generated descriptions should be one paragraph")

    words = re.findall(r"\b[\w'-]+\b", text)
    if not (25 <= len(words) <= 125):
        warnings.append(f"description has {len(words)} words; target is roughly 25-120")

    sentences = sentence_count(text)
    if not (2 <= sentences <= 4):
        warnings.append(f"description has {sentences} sentences; target is 2-4 based on room importance")

    if re.search(r"\b(you|your|you're|youve|you'll)\b", text, re.IGNORECASE):
        warnings.append("description uses second person")

    if ORIGIN_ASSUMPTION_RE.search(text):
        warnings.append("description may assume the player's arrival direction; keep navigation origin-neutral")

    return warnings


def validate_room_name(name: str) -> list[str]:
    warnings: list[str] = []
    stripped = name.strip()
    if not stripped:
        warnings.append("room name is empty")
    if "\n" in stripped:
        warnings.append("room name spans multiple lines")
    if stripped.lower() == "untitled room":
        warnings.append("room name still uses the placeholder title")
    return warnings


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def docker_command() -> list[str]:
    raw = os.environ.get("WR_ROOM_DESCRIBER_DOCKER_CMD", "docker compose")
    return shlex.split(raw)


def run_django(payload: dict[str, Any]) -> dict[str, Any]:
    payload_json = json.dumps(payload)
    inner_code = dedent(
        f"""
        import json
        from collections import deque

        from django.db import transaction

        from config import constants as adv_consts
        from worlds.models import Room

        payload = json.loads({payload_json!r})
        directions = list(adv_consts.DIRECTIONS)

        def room_queryset():
            return Room.objects.select_related("world", "zone", *directions)

        def load_room(room_id, world_id=None, for_update=False):
            if for_update:
                qs = Room.objects.select_for_update()
            else:
                qs = room_queryset()
            filters = {{"pk": room_id}}
            if world_id is not None:
                filters["world_id"] = world_id
            return qs.get(**filters)

        def serialize_room(room, distance=0, via="target"):
            exits = []
            for direction in directions:
                target = getattr(room, direction)
                if target is None:
                    continue
                exits.append({{
                    "direction": direction,
                    "target_id": target.id,
                    "target_name": target.name,
                }})
            return {{
                "distance": distance,
                "via": via,
                "id": room.id,
                "key": room.key,
                "world_id": room.world_id,
                "world_name": room.world.name if room.world_id else "",
                "zone_id": room.zone_id,
                "zone_name": room.zone.name if room.zone_id else "",
                "name": room.name or "",
                "note": room.note or "",
                "description": room.description or "",
                "type": room.type or "",
                "x": room.x,
                "y": room.y,
                "z": room.z,
                "exits": exits,
            }}

        def coordinate_neighbor(room, direction):
            diff = adv_consts.DIR_COORD_DIFF[direction]
            return room_queryset().filter(
                world_id=room.world_id,
                x=room.x + diff[0],
                y=room.y + diff[1],
                z=room.z + diff[2],
            ).first()

        def serialize_spatial_neighbor(source_room, direction):
            neighbor = coordinate_neighbor(source_room, direction)
            if neighbor is None:
                return None
            exit_target = getattr(source_room, direction)
            connected = bool(exit_target and exit_target.id == neighbor.id)
            return {{
                "direction": direction,
                "connected": connected,
                "exit_target_id": exit_target.id if exit_target else None,
                "room": serialize_room(neighbor, distance=1, via=f"coordinate {{direction}}"),
            }}

        command = payload["command"]
        world_id = payload.get("world_id")

        if command == "context":
            depth = int(payload.get("depth", 3))
            root_room = None
            queue = deque([(int(payload["room_id"]), 0, "target")])
            seen = set()
            rooms = []
            missing = []
            while queue:
                room_id, distance, via = queue.popleft()
                if room_id in seen or distance > depth:
                    continue
                seen.add(room_id)
                try:
                    room = load_room(room_id, world_id=world_id)
                except Room.DoesNotExist:
                    missing.append({{"room_id": room_id, "via": via}})
                    continue
                if distance == 0:
                    root_room = room
                rooms.append(serialize_room(room, distance=distance, via=via))
                if distance == depth:
                    continue
                for exit_info in rooms[-1]["exits"]:
                    queue.append((exit_info["target_id"], distance + 1, f"{{room.id}} {{exit_info['direction']}}"))
            spatial_neighbors = []
            if root_room is not None:
                for direction in directions:
                    neighbor = serialize_spatial_neighbor(root_room, direction)
                    if neighbor is not None:
                        spatial_neighbors.append(neighbor)
            print(json.dumps({{
                "rooms": rooms,
                "spatial_neighbors": spatial_neighbors,
                "missing": missing,
            }}, indent=2))

        elif command == "apply":
            description = payload["description"].strip()
            allow_overwrite = bool(payload.get("allow_overwrite"))
            with transaction.atomic():
                room = load_room(int(payload["room_id"]), world_id=world_id, for_update=True)
                old_description = room.description or ""
                if old_description.strip() and not allow_overwrite:
                    print(json.dumps({{
                        "updated": False,
                        "room": serialize_room(room),
                        "error": "description exists; rerun with --allow-overwrite to replace it",
                    }}, indent=2))
                else:
                    room.description = description
                    room.save()
                    room.update_live_instances()
                    print(json.dumps({{
                        "updated": True,
                        "old_description": old_description,
                        "room": serialize_room(room),
                    }}, indent=2))

        elif command == "rename":
            new_name = payload["name"].strip()
            allow_non_untitled = bool(payload.get("allow_non_untitled"))
            with transaction.atomic():
                room = load_room(int(payload["room_id"]), world_id=world_id, for_update=True)
                old_name = room.name or ""
                if old_name.strip() != "Untitled Room" and not allow_non_untitled:
                    print(json.dumps({{
                        "updated": False,
                        "room": serialize_room(room),
                        "error": "room is not named Untitled Room; rerun with --allow-non-untitled to rename it anyway",
                    }}, indent=2))
                else:
                    room.name = new_name
                    room.save()
                    room.update_live_instances()
                    print(json.dumps({{
                        "updated": True,
                        "old_name": old_name,
                        "room": serialize_room(room),
                    }}, indent=2))

        elif command == "validate":
            room = load_room(int(payload["room_id"]), world_id=world_id)
            print(json.dumps({{"room": serialize_room(room)}}, indent=2))

        else:
            raise SystemExit(f"Unsupported command: {{command}}")
        """
    )
    cmd = docker_command() + ["exec", "-T", "backend", "python", "manage.py", "shell", "--no-imports", "-c", inner_code]
    result = subprocess.run(
        cmd,
        cwd=repo_root(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit("Django command did not return JSON.")


def print_context(data: dict[str, Any]) -> None:
    rooms = data.get("rooms", [])
    if not rooms:
        print("No room context found.")
        return

    root = rooms[0]
    print(f"# Room Context: {root['id']} - {root['name']}")
    print(f"World: {root['world_id']} - {root['world_name']}")
    spatial_neighbors = data.get("spatial_neighbors", [])
    if spatial_neighbors:
        print()
        print("## Direct Coordinate Neighbors")
        for neighbor_info in spatial_neighbors:
            room = neighbor_info["room"]
            status = "exit-connected" if neighbor_info["connected"] else "not exit-connected"
            print()
            print(f"### {neighbor_info['direction']}: {room['id']} - {room['name']} ({status})")
            if neighbor_info["exit_target_id"] and not neighbor_info["connected"]:
                print(f"Exit in this direction points to room {neighbor_info['exit_target_id']}.")
            print(f"Zone: {room['zone_id'] or '[none]'} - {room['zone_name'] or '[none]'}")
            print(f"Coords: {room['x']}, {room['y']}, {room['z']}")
            print("Note:")
            print(room["note"] or "[empty]")
            print("Description:")
            print(room["description"] or "[empty]")
    for room in rooms:
        label = "Target" if room["distance"] == 0 else f"Depth {room['distance']}"
        print()
        print(f"## {label}: {room['id']} - {room['name']}")
        print(f"Via: {room['via']}")
        print(f"Zone: {room['zone_id'] or '[none]'} - {room['zone_name'] or '[none]'}")
        print(f"Coords: {room['x']}, {room['y']}, {room['z']}")
        print()
        print("Note:")
        print(room["note"] or "[empty]")
        print()
        print("Description:")
        print(room["description"] or "[empty]")
        print()
        print("Exits:")
        if room["exits"]:
            for exit_info in room["exits"]:
                print(f"- {exit_info['direction']}: {exit_info['target_id']} - {exit_info['target_name']}")
        else:
            print("- [none]")

    missing = data.get("missing", [])
    if missing:
        print()
        print("## Missing Linked Rooms")
        for item in missing:
            print(f"- {item['room_id']} via {item['via']}")


def cmd_context(args: argparse.Namespace) -> int:
    data = run_django(
        {
            "command": "context",
            "room_id": args.room_id,
            "world_id": args.world_id,
            "depth": args.depth,
        }
    )
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_context(data)
    return 0 if data.get("rooms") else 1


def cmd_apply(args: argparse.Namespace) -> int:
    if args.description_stdin:
        description = sys.stdin.read()
    elif args.description:
        description = args.description
    else:
        raise SystemExit("Provide --description or --description-stdin.")

    warnings = validate_description(description)
    if warnings and not args.force_style_warnings:
        print("Style warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print("Use --force-style-warnings to apply anyway.")
        return 1

    data = run_django(
        {
            "command": "apply",
            "room_id": args.room_id,
            "world_id": args.world_id,
            "description": description,
            "allow_overwrite": args.allow_overwrite,
        }
    )
    if args.json:
        print(json.dumps(data, indent=2))
    elif data.get("updated"):
        room = data["room"]
        print(f"updated {room['id']} - {room['name']}")
    else:
        print(data.get("error", "not updated"))
    return 0 if data.get("updated") else 1


def cmd_rename(args: argparse.Namespace) -> int:
    warnings = validate_room_name(args.name)
    if warnings:
        print("Title warnings:")
        for warning in warnings:
            print(f"- {warning}")
        return 1

    data = run_django(
        {
            "command": "rename",
            "room_id": args.room_id,
            "world_id": args.world_id,
            "name": args.name,
            "allow_non_untitled": args.allow_non_untitled,
        }
    )
    if args.json:
        print(json.dumps(data, indent=2))
    elif data.get("updated"):
        room = data["room"]
        print(f"renamed {room['id']} - {room['name']}")
    else:
        print(data.get("error", "not renamed"))
    return 0 if data.get("updated") else 1


def cmd_validate(args: argparse.Namespace) -> int:
    data = run_django(
        {
            "command": "validate",
            "room_id": args.room_id,
            "world_id": args.world_id,
        }
    )
    room = data["room"]
    warnings = []
    if room["name"].strip() == "Untitled Room":
        warnings.append("room still uses the Untitled Room placeholder; infer and apply a suitable title")
    warnings.extend(validate_description(room["description"]))
    if args.json:
        data["warnings"] = warnings
        print(json.dumps(data, indent=2))
    elif warnings:
        print(f"{room['id']} - {room['name']}: warnings")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print(f"{room['id']} - {room['name']}: ok")
    return 1 if warnings else 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--room-id", required=True, type=int)
    parser.add_argument("--world-id", type=int, help="Optional guard to require the room to belong to this world.")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="print database room and neighbor context")
    add_common_args(context)
    context.add_argument("--depth", type=int, default=3)
    context.set_defaults(func=cmd_context)

    apply_parser = subparsers.add_parser("apply", help="write a generated description to Room.description")
    add_common_args(apply_parser)
    apply_parser.add_argument("--description")
    apply_parser.add_argument("--description-stdin", action="store_true")
    apply_parser.add_argument("--allow-overwrite", action="store_true")
    apply_parser.add_argument("--force-style-warnings", action="store_true")
    apply_parser.set_defaults(func=cmd_apply)

    rename = subparsers.add_parser("rename", help="replace Room.name when the current title is Untitled Room")
    add_common_args(rename)
    rename.add_argument("--name", required=True)
    rename.add_argument("--allow-non-untitled", action="store_true")
    rename.set_defaults(func=cmd_rename)

    validate = subparsers.add_parser("validate", help="validate Room.description style shape")
    add_common_args(validate)
    validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
