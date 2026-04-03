import argparse
import json
import os
import sys
from pathlib import Path


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings.local")
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))
if '/code' not in sys.path:
    sys.path.append('/code')
if '/code/backend' not in sys.path:
    sys.path.append('/code/backend')


from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from builders import manifests as builder_manifests
from quests import manifests as quest_manifests
from quests.models import QuestArcTemplate, QuestTemplate
from worlds.models import World


def _load_world(world_id: int) -> World:
    return World.objects.get(pk=world_id)


def cmd_list(args):
    world = _load_world(args.world)
    if args.kind == "arc":
        arcs = QuestArcTemplate.objects.filter(world=world).order_by("name", "created_ts")
        for quest_arc in arcs:
            print(f"{quest_arc.id}\t{quest_arc.slug}\t{quest_arc.name}")
        if not arcs:
            print("No quest arcs found.")
        return

    quests = QuestTemplate.objects.filter(world=world).select_related("arc").order_by("name", "created_ts")
    for quest in quests:
        arc_slug = quest.arc.slug if quest.arc else "-"
        print(f"{quest.id}\t{quest.slug}\t{quest.name}\t{quest.quest_type}\t{arc_slug}")
    if not quests:
        print("No quest templates found.")


def cmd_show(args):
    world = _load_world(args.world)
    if args.kind == "arc":
        qs = QuestArcTemplate.objects.filter(world=world)
        obj = qs.get(slug=args.identity) if not str(args.identity).isdigit() else qs.get(pk=int(args.identity))
        payload = quest_manifests.serialize_quest_arc_payload(obj)
    else:
        qs = QuestTemplate.objects.filter(world=world)
        obj = qs.get(slug=args.identity) if not str(args.identity).isdigit() else qs.get(pk=int(args.identity))
        payload = quest_manifests.serialize_quest_template_payload(obj)

    if args.format == "json":
        print(json.dumps(payload["manifest"], indent=2))
    else:
        print(payload["yaml"])


def cmd_template(args):
    world = _load_world(args.world)
    if args.kind == "arc":
        payload = quest_manifests.serialize_quest_arc_template(world=world)
    else:
        payload = quest_manifests.serialize_quest_template_template(world=world)
    print(payload["yaml"])


def cmd_apply(args):
    world = _load_world(args.world)
    manifest_path = Path(args.file)
    manifest_text = manifest_path.read_text()
    manifest = builder_manifests.load_yaml_manifest(manifest_text)
    manifest_kind = builder_manifests.parse_manifest_kind(manifest)

    if manifest_kind == quest_manifests.QUEST_MANIFEST_KIND:
        operation = quest_manifests.parse_manifest_operation(manifest)
        if operation == quest_manifests.MANIFEST_OPERATION_DELETE:
            parsed = quest_manifests.parse_quest_delete_manifest(world=world, manifest=manifest)
            print(f"Deleting quest template {parsed.quest.slug} ({parsed.quest.id})")
            parsed.quest.delete()
            return
        parsed = quest_manifests.parse_quest_manifest(world=world, manifest=manifest)
        quest = quest_manifests.apply_quest_manifest(parsed)
        print(f"Applied quest template {quest.slug} ({quest.id})")
        print(quest_manifests.serialize_quest_template_payload(quest)["yaml"])
        return

    if manifest_kind == quest_manifests.QUEST_ARC_MANIFEST_KIND:
        operation = quest_manifests.parse_manifest_operation(manifest)
        if operation == quest_manifests.MANIFEST_OPERATION_DELETE:
            parsed = quest_manifests.parse_quest_arc_delete_manifest(world=world, manifest=manifest)
            print(f"Deleting quest arc {parsed.quest_arc.slug} ({parsed.quest_arc.id})")
            parsed.quest_arc.delete()
            return
        parsed = quest_manifests.parse_quest_arc_manifest(world=world, manifest=manifest)
        quest_arc = quest_manifests.apply_quest_arc_manifest(parsed)
        print(f"Applied quest arc {quest_arc.slug} ({quest_arc.id})")
        print(quest_manifests.serialize_quest_arc_payload(quest_arc)["yaml"])
        return

    raise SystemExit(f"Unsupported manifest kind for playground: {manifest_kind}")


def main():
    parser = argparse.ArgumentParser(
        description="Playground for Phase 1 quest manifests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List quest templates or arcs")
    list_parser.add_argument("--world", type=int, required=True)
    list_parser.add_argument("--kind", choices=["quest", "arc"], default="quest")
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show quest/arc manifest YAML")
    show_parser.add_argument("--world", type=int, required=True)
    show_parser.add_argument("--kind", choices=["quest", "arc"], default="quest")
    show_parser.add_argument("identity", help="Quest slug/id or arc slug/id")
    show_parser.add_argument("--format", choices=["yaml", "json"], default="yaml")
    show_parser.set_defaults(func=cmd_show)

    template_parser = subparsers.add_parser("template", help="Print a starter manifest")
    template_parser.add_argument("--world", type=int, required=True)
    template_parser.add_argument("--kind", choices=["quest", "arc"], default="quest")
    template_parser.set_defaults(func=cmd_template)

    apply_parser = subparsers.add_parser("apply", help="Apply a quest/arc manifest from disk")
    apply_parser.add_argument("--world", type=int, required=True)
    apply_parser.add_argument("--file", required=True)
    apply_parser.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
