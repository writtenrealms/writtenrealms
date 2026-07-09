#!/usr/bin/env python3
"""Read, fill, and validate Written Realms mob definitions through Django in Docker."""

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


PLACEHOLDER_NAMES = {"a new mob", "unnamed mob"}
TARGET_FIELDS = ("name", "room_description", "description")


def sentence_count(text: str) -> int:
    return len(re.findall(r"[^.!?]+(?:[.!?]+|$)", text.strip()))


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def name_is_missing(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped.casefold() in PLACEHOLDER_NAMES


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def room_description_is_missing(name: str, value: str) -> bool:
    normalized_value = normalize_whitespace(value)
    if not normalized_value:
        return True
    normalized_name = normalize_whitespace(name)
    if not normalized_name:
        return False
    default_value = f"{normalized_name} is here."
    return normalized_value.casefold() == default_value.casefold()


def validate_name(value: str) -> list[str]:
    warnings: list[str] = []
    stripped = value.strip()
    if name_is_missing(stripped):
        warnings.append("name is missing or still uses a placeholder")
        return warnings
    if "\n" in stripped:
        warnings.append("name spans multiple lines")
    if stripped.endswith((".", "!", "?")):
        warnings.append("name should be a noun fragment without terminal punctuation")
    if re.match(r"^(A|An|The)\s", stripped):
        warnings.append("common and unique unnamed mobs should use a lowercase article")
    if stripped[:1].islower() and not re.match(r"^(a|an|the)\s", stripped):
        warnings.append("a lowercase common-mob name should begin with a, an, or the")
    return warnings


def validate_room_description(value: str, name: str = "") -> list[str]:
    warnings: list[str] = []
    stripped = value.strip()
    if not stripped:
        warnings.append("room_description is missing")
        return warnings
    if room_description_is_missing(name, stripped):
        warnings.append("room_description still uses the generated '<name> is here.' placeholder")
        return warnings
    if "\n" in stripped:
        warnings.append("room_description should be one line")
    if not stripped.endswith("."):
        warnings.append("room_description should end with a period")
    sentences = sentence_count(stripped)
    if sentences != 1:
        warnings.append(f"room_description has {sentences} sentences; target is exactly 1")
    words = word_count(stripped)
    if not 6 <= words <= 15:
        warnings.append(f"room_description has {words} words; target is roughly 6-15")
    return warnings


def validate_description(value: str) -> list[str]:
    warnings: list[str] = []
    stripped = value.strip()
    if not stripped:
        warnings.append("description is missing")
        return warnings
    if "\n\n" in stripped:
        warnings.append("description contains a blank line; target is one paragraph")
    elif "\n" in stripped:
        warnings.append("description spans multiple lines; target is one paragraph")
    sentences = sentence_count(stripped)
    if not 1 <= sentences <= 4:
        warnings.append(f"description has {sentences} sentences; target is 1-4")
    return warnings


def validate_updates(updates: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    validators = {
        "name": validate_name,
        "room_description": validate_room_description,
        "description": validate_description,
    }
    for field_name, value in updates.items():
        if field_name == "room_description":
            field_warnings = validate_room_description(value, updates.get("name", ""))
        else:
            field_warnings = validators[field_name](value)
        warnings.extend(f"{field_name}: {warning}" for warning in field_warnings)
    return warnings


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def docker_command() -> list[str]:
    raw = os.environ.get("WR_MOB_DESCRIBER_DOCKER_CMD", "docker compose")
    return shlex.split(raw)


def run_django(payload: dict[str, Any]) -> dict[str, Any]:
    payload_json = json.dumps(payload)
    inner_code = dedent(
        f"""
        import json

        from django.db import transaction

        from builders.models import MobDefinition

        payload = json.loads({payload_json!r})
        placeholder_names = {{"a new mob", "unnamed mob"}}
        target_fields = ("name", "room_description", "description")

        def mob_queryset(for_update=False):
            queryset = MobDefinition.objects.select_related("world")
            if for_update:
                queryset = queryset.select_for_update()
            return queryset

        def load_mob(mob_definition_id, world_id=None, for_update=False):
            filters = {{"pk": mob_definition_id}}
            if world_id is not None:
                filters["world_id"] = world_id
            return mob_queryset(for_update=for_update).get(**filters)

        def text(value):
            return value or ""

        def name_missing(value):
            stripped = text(value).strip()
            return not stripped or stripped.casefold() in placeholder_names

        def normalize_whitespace(value):
            return " ".join(text(value).split())

        def room_description_missing(name, value):
            normalized_value = normalize_whitespace(value)
            if not normalized_value:
                return True
            normalized_name = normalize_whitespace(name)
            if not normalized_name:
                return False
            default_value = f"{{normalized_name}} is here."
            return normalized_value.casefold() == default_value.casefold()

        def field_missing(mob, field_name):
            if field_name == "name":
                return name_missing(mob.name)
            if field_name == "room_description":
                return room_description_missing(mob.name, mob.room_description)
            return not text(getattr(mob, field_name)).strip()

        def source_fields(mob):
            fields = []
            if not name_missing(mob.name):
                fields.append("name")
            if text(mob.notes).strip():
                fields.append("notes")
            if text(mob.description).strip():
                fields.append("description")
            if not room_description_missing(mob.name, mob.room_description):
                fields.append("room_description")
            return fields

        def serialize_mob(mob):
            sources = source_fields(mob)
            missing = [field_name for field_name in target_fields if field_missing(mob, field_name)]
            return {{
                "id": mob.id,
                "key": mob.key,
                "slug": mob.slug,
                "world_id": mob.world_id,
                "world_name": mob.world.name if mob.world_id else "",
                "name": text(mob.name),
                "notes": text(mob.notes),
                "room_description": text(mob.room_description),
                "description": text(mob.description),
                "source_fields": sources,
                "missing_fields": missing,
                "can_generate": bool(sources),
            }}

        command = payload["command"]
        mob_definition_id = int(payload["mob_definition_id"])
        world_id = payload.get("world_id")

        if command in {{"context", "validate"}}:
            mob = load_mob(mob_definition_id, world_id=world_id)
            print(json.dumps({{"mob": serialize_mob(mob)}}, indent=2))

        elif command == "apply":
            updates = payload.get("updates") or {{}}
            allow_overwrite = bool(payload.get("allow_overwrite"))
            with transaction.atomic():
                mob = load_mob(mob_definition_id, world_id=world_id, for_update=True)
                before = serialize_mob(mob)
                if not before["can_generate"]:
                    print(json.dumps({{
                        "updated": False,
                        "mob": before,
                        "error": "no usable name, notes, description, or room_description source is defined",
                    }}, indent=2))
                else:
                    updated_fields = []
                    preserved_fields = []
                    unchanged_fields = []
                    missing_before = set(before["missing_fields"])
                    for field_name in target_fields:
                        if field_name not in updates:
                            continue
                        value = str(updates[field_name]).strip()
                        if field_name not in missing_before and not allow_overwrite:
                            preserved_fields.append(field_name)
                            continue
                        if text(getattr(mob, field_name)) == value:
                            unchanged_fields.append(field_name)
                            continue
                        setattr(mob, field_name, value)
                        updated_fields.append(field_name)
                    if updated_fields:
                        mob.save(update_fields=updated_fields)
                    after = serialize_mob(mob)
                    print(json.dumps({{
                        "updated": bool(updated_fields),
                        "updated_fields": updated_fields,
                        "preserved_fields": preserved_fields,
                        "unchanged_fields": unchanged_fields,
                        "before": before,
                        "mob": after,
                    }}, indent=2))

        else:
            raise SystemExit(f"Unsupported command: {{command}}")
        """
    )
    cmd = docker_command() + [
        "exec",
        "-T",
        "backend",
        "python",
        "manage.py",
        "shell",
        "--no-imports",
        "-c",
        inner_code,
    ]
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
    mob = data["mob"]
    print(f"# Mob Definition Context: {mob['id']} - {mob['name'] or '[blank]'}")
    print(f"World: {mob['world_id']} - {mob['world_name']}")
    print(f"Slug: {mob['slug'] or '[blank]'}")
    print(f"Source fields: {', '.join(mob['source_fields']) or '[none]'}")
    print(f"Missing target fields: {', '.join(mob['missing_fields']) or '[none]'}")
    print(f"Can generate: {'yes' if mob['can_generate'] else 'no'}")
    print()
    print("## Name")
    print(mob["name"] or "[empty]")
    print()
    print("## Notes")
    print(mob["notes"] or "[empty]")
    print()
    print("## Room Description")
    print(mob["room_description"] or "[empty]")
    print()
    print("## Description")
    print(mob["description"] or "[empty]")


def load_updates(args: argparse.Namespace) -> dict[str, str]:
    direct = {
        "name": args.name,
        "room_description": args.room_description,
        "description": args.description,
    }
    direct = {field_name: value for field_name, value in direct.items() if value is not None}
    if args.input_json and direct:
        raise SystemExit("Use either --input-json or direct field arguments, not both.")

    if args.input_json:
        raw = sys.stdin.read() if args.input_json == "-" else Path(args.input_json).read_text()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid input JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SystemExit("Input JSON must be an object.")
        if isinstance(parsed.get("updates"), dict):
            parsed = parsed["updates"]
        unknown = set(parsed) - set(TARGET_FIELDS)
        if unknown:
            raise SystemExit(f"Unsupported update fields: {', '.join(sorted(unknown))}")
        direct = {field_name: value for field_name, value in parsed.items() if value is not None}

    if not direct:
        raise SystemExit("Provide at least one generated field.")

    updates: dict[str, str] = {}
    for field_name, value in direct.items():
        if not isinstance(value, str):
            raise SystemExit(f"{field_name} must be a string.")
        stripped = value.strip()
        if not stripped:
            raise SystemExit(f"{field_name} cannot be blank.")
        updates[field_name] = stripped
    return updates


def cmd_context(args: argparse.Namespace) -> int:
    data = run_django({
        "command": "context",
        "mob_definition_id": args.mob_definition_id,
        "world_id": args.world_id,
    })
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_context(data)
    return 0 if data["mob"]["can_generate"] else 1


def cmd_apply(args: argparse.Namespace) -> int:
    updates = load_updates(args)
    warnings = validate_updates(updates)
    if warnings and not args.force_style_warnings:
        print("Style warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print("Use --force-style-warnings to apply anyway.")
        return 1

    data = run_django({
        "command": "apply",
        "mob_definition_id": args.mob_definition_id,
        "world_id": args.world_id,
        "updates": updates,
        "allow_overwrite": args.allow_overwrite,
    })
    if args.json:
        print(json.dumps(data, indent=2))
    elif data.get("error"):
        print(data["error"])
    elif data.get("updated"):
        mob = data["mob"]
        fields = ", ".join(data["updated_fields"])
        print(f"updated {mob['id']} - {mob['name']}: {fields}")
        if data["preserved_fields"]:
            print(f"preserved: {', '.join(data['preserved_fields'])}")
    else:
        print("no fields changed")
    return 0 if data.get("updated") else 1


def cmd_validate(args: argparse.Namespace) -> int:
    data = run_django({
        "command": "validate",
        "mob_definition_id": args.mob_definition_id,
        "world_id": args.world_id,
    })
    mob = data["mob"]
    warnings: list[str] = []
    if not mob["can_generate"]:
        warnings.append("no usable authoring source is defined")
    warnings.extend(f"name: {warning}" for warning in validate_name(mob["name"]))
    warnings.extend(
        f"room_description: {warning}"
        for warning in validate_room_description(mob["room_description"], mob["name"])
    )
    warnings.extend(
        f"description: {warning}"
        for warning in validate_description(mob["description"])
    )
    if args.json:
        data["warnings"] = warnings
        print(json.dumps(data, indent=2))
    elif warnings:
        print(f"{mob['id']} - {mob['name'] or '[blank]'}: warnings")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print(f"{mob['id']} - {mob['name']}: ok")
    return 1 if warnings else 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mob-definition-id", required=True, type=int)
    parser.add_argument("--world-id", type=int, help="Require the mob definition to belong to this world.")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="print mob definition authoring fields")
    add_common_args(context)
    context.set_defaults(func=cmd_context)

    apply_parser = subparsers.add_parser("apply", help="fill missing mob definition display fields")
    add_common_args(apply_parser)
    apply_parser.add_argument("--input-json", help="Read a JSON object from this path, or use - for stdin.")
    apply_parser.add_argument("--name")
    apply_parser.add_argument("--room-description")
    apply_parser.add_argument("--description")
    apply_parser.add_argument("--allow-overwrite", action="store_true")
    apply_parser.add_argument("--force-style-warnings", action="store_true")
    apply_parser.set_defaults(func=cmd_apply)

    validate = subparsers.add_parser("validate", help="validate the three mob display fields")
    add_common_args(validate)
    validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
