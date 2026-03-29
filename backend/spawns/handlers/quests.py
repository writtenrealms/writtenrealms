"""
Quest runtime command handler.
"""
from quests.services.discovery import list_opportunities
from quests.services.engine import (
    QuestRuntimeError,
    abandon_instance,
    accept_template,
    choose_for_instance,
    info_for_player,
    list_active_instances,
    list_completed_instances,
    resolve_template_for_player,
)
from spawns.events import publish_events
from spawns.handlers.base import (
    ChoiceResolutionError,
    CommandContext,
    CommandHandler,
    resolve_unambiguous_choice,
)
from spawns.handlers.registry import register_handler

QUEST_SUBCOMMANDS = (
    "opportunities",
    "active",
    "completed",
    "accept",
    "choose",
    "abandon",
    "info",
)
QUEST_SUBCOMMAND_ALIASES = {
    "offers": "opportunities",
    "resolved": "completed",
}


def _render_quest_list(title: str, quests: list[dict]) -> str:
    lines = [title]
    if not quests:
        lines.append("None.")
        return "\n".join(lines)
    for quest in quests:
        template = quest.get("template") or quest
        slug = template.get("slug") or "quest"
        name = template.get("name") or slug
        recap = ((quest.get("current_step") or {}).get("recap") or quest.get("recap") or "").strip()
        line = f"- {slug}: {name}"
        if recap:
            line += f" - {recap}"
        lines.append(line)
    return "\n".join(lines)


@register_handler
class QuestCommandHandler(CommandHandler):
    command_type = "quest"
    text_commands = ("quest", "quests")
    help = {
        "name": "Quest",
        "format": "quest [subcommand]",
        "description": "Review your quests, quest opportunities, and quest choices.",
        "details": [
            "If you omit the subcommand, `quest` defaults to `quest info`.",
            "`info [slug-or-id]`: Show quest information for all active quests, or one specific quest.",
            "`opportunities`: List quests you can currently accept.",
            "`active`: List your active quests.",
            "`completed`: List quests you have already finished.",
            "`accept <slug>`: Accept an available quest opportunity.",
            "`choose <slug-or-id> <choice_id>`: Make a quest choice for an active quest.",
            "`abandon <slug-or-id>`: Abandon an active quest.",
        ],
        "examples": [
            "quest",
            "quest info",
            "quest opportunities",
            "quest active",
            "quest completed",
            "quest accept shrine_survey",
            "quest choose tiny_hello continue",
            "quest abandon tiny_hello",
        ],
    }

    def _args(self, ctx: CommandContext) -> list[str]:
        return list(ctx.payload.get("args") or [])

    def _resolve_subcommand(self, raw_subcommand: str | None) -> str:
        if raw_subcommand is None:
            return "info"
        try:
            return resolve_unambiguous_choice(
                raw_subcommand,
                choices=QUEST_SUBCOMMANDS,
                aliases=QUEST_SUBCOMMAND_ALIASES,
            )
        except ChoiceResolutionError as exc:
            if exc.code == "ambiguous_choice":
                raise QuestRuntimeError(
                    f"Ambiguous quest subcommand: {exc.token}. Matches: {', '.join(exc.matches)}",
                    code="ambiguous_subcommand",
                )
            raise QuestRuntimeError(
                f"Unknown quest subcommand: {exc.token}",
                code="unknown_subcommand",
            )

    def _publish_text(
        self,
        ctx: CommandContext,
        text: str,
        *,
        subcommand: str,
        data: dict | None = None,
    ) -> None:
        payload = {"subcommand": subcommand}
        if data:
            payload.update(data)
        ctx.publish(
            {
                "type": "cmd.quest.success",
                "text": text,
                "data": payload,
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

        try:
            subcommand = self._resolve_subcommand(args[0] if args else None)
            if subcommand == "opportunities":
                opportunities = list_opportunities(ctx.player, refresh=True)
                self._publish_text(
                    ctx,
                    _render_quest_list("Opportunities:", opportunities),
                    subcommand=subcommand,
                    data={"opportunities": opportunities},
                )
                return

            if subcommand == "active":
                quests = list_active_instances(ctx.player)
                self._publish_text(
                    ctx,
                    _render_quest_list("Active quests:", quests),
                    subcommand=subcommand,
                    data={"quests": quests},
                )
                return

            if subcommand == "completed":
                quests = list_completed_instances(ctx.player)
                self._publish_text(
                    ctx,
                    _render_quest_list("Resolved quests:", quests),
                    subcommand=subcommand,
                    data={"quests": quests},
                )
                return

            if subcommand == "accept":
                opportunities_list = list_opportunities(ctx.player, refresh=True)
                opportunities = {op["slug"]: op for op in opportunities_list}
                slug = args[1] if len(args) > 1 else None
                if not slug:
                    if len(opportunities_list) == 1:
                        slug = opportunities_list[0]["slug"]
                    elif not opportunities_list:
                        raise QuestRuntimeError(
                            "You have no quest opportunities to accept.",
                            code="opportunity_not_found",
                        )
                    else:
                        raise QuestRuntimeError(
                            "Multiple quest opportunities are available. Use: quest accept <slug>",
                            code="ambiguous_opportunity",
                        )
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

            if subcommand == "info":
                identity = args[1] if len(args) > 1 else None
                payload, info_text = info_for_player(ctx.player, identity)
                data = payload if "quests" in payload else {"quest": payload}
                self._publish_text(ctx, info_text, subcommand=subcommand, data=data)
                return

            raise QuestRuntimeError(f"Unknown quest subcommand: {subcommand}", code="unknown_subcommand")
        except QuestRuntimeError as exc:
            self._publish_error(ctx, exc)
