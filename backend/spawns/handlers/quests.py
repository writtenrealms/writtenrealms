"""
Quest runtime command handler.
"""
from quests.services.discovery import list_opportunities
from quests.services.engine import (
    QuestRuntimeError,
    abandon_instance,
    accept_template,
    choose_for_instance,
    list_active_instances,
    list_completed_instances,
    recap_for_player,
    resolve_template_for_player,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _render_quest_list(title: str, quests: list[dict]) -> str:
    lines = [title]
    if not quests:
        lines.append("None.")
        return "\n".join(lines)
    for quest in quests:
        template = quest.get("template") or quest
        slug = template.get("slug") or "quest"
        name = template.get("name") or slug
        lead = ((quest.get("current_step") or {}).get("lead") or quest.get("lead") or "").strip()
        line = f"- {slug}: {name}"
        if lead:
            line += f" - {lead}"
        lines.append(line)
    return "\n".join(lines)


@register_handler
class QuestCommandHandler(CommandHandler):
    command_type = "quest"
    text_commands = ("quest", "quests")
    help = {
        "name": "Quest",
        "format": "quest <subcommand>",
        "description": "Inspect and advance WR2 quests.",
        "examples": [
            "quest recap",
            "quest opportunities",
            "quest accept shrine_survey",
            "quest choose tiny_hello continue",
            "quest abandon tiny_hello",
        ],
    }

    def _args(self, ctx: CommandContext) -> list[str]:
        return list(ctx.payload.get("args") or [])

    def _publish_text(self, ctx: CommandContext, text: str, *, data: dict | None = None) -> None:
        ctx.publish(
            {
                "type": "cmd.quest.success",
                "text": text,
                "data": data or {},
            }
        )

    def _publish_error(self, ctx: CommandContext, exc: QuestRuntimeError) -> None:
        ctx.publish(
            {
                "type": "cmd.quest.error",
                "text": exc.message,
                "data": {"error": exc.message, "code": exc.code},
            }
        )

    def handle(self, ctx: CommandContext) -> None:
        args = self._args(ctx)
        subcommand = (args[0].lower() if args else "recap")

        try:
            if subcommand in {"opportunities", "offers"}:
                opportunities = list_opportunities(ctx.player, refresh=True)
                self._publish_text(
                    ctx,
                    _render_quest_list("Opportunities:", opportunities),
                    data={"opportunities": opportunities},
                )
                return

            if subcommand == "active":
                quests = list_active_instances(ctx.player)
                self._publish_text(
                    ctx,
                    _render_quest_list("Active quests:", quests),
                    data={"quests": quests},
                )
                return

            if subcommand in {"completed", "resolved"}:
                quests = list_completed_instances(ctx.player)
                self._publish_text(
                    ctx,
                    _render_quest_list("Resolved quests:", quests),
                    data={"quests": quests},
                )
                return

            if subcommand == "accept":
                if len(args) < 2:
                    raise QuestRuntimeError("Usage: quest accept <slug>", code="usage")
                opportunities = {op["slug"]: op for op in list_opportunities(ctx.player, refresh=True)}
                slug = args[1]
                if slug not in opportunities:
                    raise QuestRuntimeError("Quest opportunity was not found.", code="opportunity_not_found")
                template = resolve_template_for_player(ctx.player, slug)
                result = accept_template(ctx.player, template)
                publish_events(
                    result.events,
                    actor_key=ctx.player.key,
                    connection_id=ctx.connection_id,
                )
                return

            if subcommand == "choose":
                if len(args) < 3:
                    raise QuestRuntimeError("Usage: quest choose <slug-or-id> <choice_id>", code="usage")
                result = choose_for_instance(ctx.player, args[1], args[2])
                publish_events(
                    result.events,
                    actor_key=ctx.player.key,
                    connection_id=ctx.connection_id,
                )
                return

            if subcommand == "abandon":
                if len(args) < 2:
                    raise QuestRuntimeError("Usage: quest abandon <slug-or-id>", code="usage")
                result = abandon_instance(ctx.player, args[1])
                publish_events(
                    result.events,
                    actor_key=ctx.player.key,
                    connection_id=ctx.connection_id,
                )
                return

            if subcommand == "recap":
                identity = args[1] if len(args) > 1 else None
                payload, recap_text = recap_for_player(ctx.player, identity)
                self._publish_text(ctx, recap_text, data=payload)
                return

            raise QuestRuntimeError(f"Unknown quest subcommand: {subcommand}", code="unknown_subcommand")
        except QuestRuntimeError as exc:
            self._publish_error(ctx, exc)
