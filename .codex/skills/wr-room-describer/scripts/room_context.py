#!/usr/bin/env python3
"""Parse Written Realms room text files and print local generation context."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXIT_NAMES = {
    "n",
    "s",
    "e",
    "w",
    "ne",
    "nw",
    "se",
    "sw",
    "u",
    "d",
    "up",
    "down",
    "in",
    "out",
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
}
EXIT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]{0,24}):\s*(\S.*?)\s*$")
SENTENCE_RE = re.compile(r"[.!?]+(?:\s+|$)")


@dataclass(frozen=True)
class Exit:
    direction: str
    target_id: str
    raw: str


@dataclass(frozen=True)
class Room:
    room_id: str
    path: Path
    title: str
    body: str
    exits: tuple[Exit, ...]


def is_exit_line(line: str) -> re.Match[str] | None:
    match = EXIT_RE.match(line.strip())
    if not match:
        return None
    direction = match.group(1).strip().lower().replace(" ", "")
    if direction not in EXIT_NAMES:
        return None
    return match


def parse_room(path: Path) -> Room:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"{path} is empty")

    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1

    exit_start = end
    parsed_exits: list[Exit] = []
    for index in range(end - 1, 0, -1):
        match = is_exit_line(lines[index])
        if not match:
            break
        exit_start = index
        parsed_exits.append(
            Exit(
                direction=match.group(1).strip(),
                target_id=match.group(2).strip(),
                raw=lines[index],
            )
        )

    parsed_exits.reverse()
    body_lines = lines[1:exit_start]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    return Room(
        room_id=path.stem,
        path=path,
        title=lines[0].strip(),
        body="\n".join(body_lines).strip(),
        exits=tuple(parsed_exits),
    )


def find_room_path(rooms_dir: Path, room_id: str) -> Path:
    direct = rooms_dir / f"{room_id}.txt"
    if direct.exists():
        return direct

    matches = sorted(rooms_dir.rglob(f"{room_id}.txt"))
    if not matches:
        raise FileNotFoundError(f"Could not find {room_id}.txt under {rooms_dir}")
    if len(matches) > 1:
        joined = "\n".join(str(match) for match in matches)
        raise ValueError(f"Multiple files found for {room_id}.txt:\n{joined}")
    return matches[0]


def collect_rooms(rooms_dir: Path, root_id: str, depth: int) -> tuple[list[tuple[int, str, Room]], list[str]]:
    queue: deque[tuple[str, int, str]] = deque([(root_id, 0, "target")])
    seen: set[str] = set()
    rooms: list[tuple[int, str, Room]] = []
    missing: list[str] = []

    while queue:
        room_id, distance, via = queue.popleft()
        if room_id in seen or distance > depth:
            continue
        seen.add(room_id)

        try:
            room = parse_room(find_room_path(rooms_dir, room_id))
        except (FileNotFoundError, ValueError) as exc:
            missing.append(f"{room_id} ({via}): {exc}")
            continue

        rooms.append((distance, via, room))
        if distance == depth:
            continue
        for exit_ in room.exits:
            queue.append((exit_.target_id, distance + 1, f"{room.room_id} {exit_.direction}"))

    return rooms, missing


def room_to_dict(room: Room, distance: int, via: str) -> dict[str, object]:
    return {
        "distance": distance,
        "via": via,
        "room_id": room.room_id,
        "path": str(room.path),
        "title": room.title,
        "body": room.body,
        "exits": [
            {"direction": exit_.direction, "target_id": exit_.target_id, "raw": exit_.raw}
            for exit_ in room.exits
        ],
    }


def print_markdown(rooms: Iterable[tuple[int, str, Room]], missing: list[str]) -> None:
    rooms = list(rooms)
    if not rooms:
        print("No room context found.")
        return

    root = rooms[0][2]
    print(f"# Room Context: {root.room_id} - {root.title}")
    for distance, via, room in rooms:
        label = "Target" if distance == 0 else f"Depth {distance}"
        print()
        print(f"## {label}: {room.room_id} - {room.title}")
        print(f"Path: {room.path}")
        print(f"Via: {via}")
        print()
        print("Body / notes:")
        print(room.body or "[empty]")
        print()
        print("Exits:")
        if room.exits:
            for exit_ in room.exits:
                print(f"- {exit_.direction}: {exit_.target_id}")
        else:
            print("- [none]")

    if missing:
        print()
        print("## Missing Linked Rooms")
        for item in missing:
            print(f"- {item}")


def sentence_count(text: str) -> int:
    return len([part for part in SENTENCE_RE.split(text.strip()) if part.strip()])


def validate_room(room: Room) -> list[str]:
    warnings: list[str] = []
    if not room.title:
        warnings.append("missing title")
    if not room.body:
        warnings.append("missing body")
    if "\n\n" in room.body:
        warnings.append("body contains a blank line")
    if "\n" in room.body:
        warnings.append("body spans multiple lines; generated descriptions should be one paragraph")

    words = re.findall(r"\b[\w'-]+\b", room.body)
    if room.body and not (25 <= len(words) <= 125):
        warnings.append(f"body has {len(words)} words; target is roughly 25-120")

    sentences = sentence_count(room.body)
    if room.body and not (2 <= sentences <= 4):
        warnings.append(f"body has {sentences} sentences; target is 2-4 based on room importance")

    if re.search(r"\b(you|your|you're|youve|you'll)\b", room.body, re.IGNORECASE):
        warnings.append("body uses second person")

    if not room.exits:
        warnings.append("no trailing exit lines parsed")

    return warnings


def cmd_context(args: argparse.Namespace) -> int:
    rooms, missing = collect_rooms(args.rooms_dir, args.room_id, args.depth)
    if args.json:
        print(
            json.dumps(
                {
                    "root_room_id": args.room_id,
                    "depth": args.depth,
                    "rooms": [room_to_dict(room, distance, via) for distance, via, room in rooms],
                    "missing": missing,
                },
                indent=2,
            )
        )
    else:
        print_markdown(rooms, missing)
    return 0 if rooms else 1


def cmd_validate(args: argparse.Namespace) -> int:
    room = parse_room(find_room_path(args.rooms_dir, args.room_id))
    warnings = validate_room(room)
    if warnings:
        print(f"{room.room_id} - {room.title}: warnings")
        for warning in warnings:
            print(f"- {warning}")
        return 1

    print(f"{room.room_id} - {room.title}: ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="print room and neighbor context")
    context.add_argument("--rooms-dir", type=Path, required=True)
    context.add_argument("--room-id", required=True)
    context.add_argument("--depth", type=int, default=3)
    context.add_argument("--json", action="store_true")
    context.set_defaults(func=cmd_context)

    validate = subparsers.add_parser("validate", help="validate one parsed room file")
    validate.add_argument("--rooms-dir", type=Path, required=True)
    validate.add_argument("--room-id", required=True)
    validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.rooms_dir = args.rooms_dir.expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
