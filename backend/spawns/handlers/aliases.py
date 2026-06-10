"""
Player alias commands.
"""
from spawns.aliases import (
    delete_player_alias,
    normalize_alias_match,
    serialize_alias,
    serialize_player_aliases,
    upsert_player_alias,
    validate_alias_match,
)
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.models import Alias


def _publish_alias_error(ctx: CommandContext, error: str, code: str, **data) -> None:
    ctx.publish(
        {
            "type": "cmd.alias.error",
            "text": error,
            "data": {"error": error, "code": code, **data},
        }
    )


def _alias_payload(ctx: CommandContext, alias: Alias | None = None) -> dict:
    data = {"aliases": serialize_player_aliases(ctx.player)}
    if alias:
        data["alias"] = serialize_alias(alias)
    return data


@register_handler
class AliasHandler(CommandHandler):
    command_type = "alias"
    text_commands = ("alias",)
    help = {
        "name": "Alias",
        "format": "alias | alias <name> | alias <name> <command>",
        "description": "List, show, or define command aliases.",
        "details": [
            "`alias <name> = <command>` is also accepted.",
        ],
        "examples": [
            "alias",
            "alias x kill bear",
            "alias y north ; x",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        raw_text = str(ctx.payload.get("raw_text") or ctx.payload.get("text") or "")
        remainder = raw_text.strip().split(maxsplit=1)
        alias_text = remainder[1].strip() if len(remainder) > 1 else ""

        if not alias_text:
            aliases = list(serialize_player_aliases(ctx.player).values())
            text = self._render_alias_list(aliases)
            ctx.publish(
                {
                    "type": "cmd.alias.success",
                    "text": text,
                    "data": {"aliases": serialize_player_aliases(ctx.player)},
                }
            )
            return

        parsed = self._parse_alias_definition(alias_text)
        if not parsed:
            self._show_alias(ctx, alias_text)
            return

        match, replacement = parsed

        error = validate_alias_match(match)
        if error:
            _publish_alias_error(ctx, error, "invalid_alias_match", match=match)
            return
        if not replacement:
            _publish_alias_error(
                ctx,
                "Alias replacement is required. Use unalias <name> to remove an alias.",
                "missing_alias_replacement",
                match=match,
            )
            return

        alias = upsert_player_alias(ctx.player, match, replacement)
        ctx.publish(
            {
                "type": "cmd.alias.success",
                "text": f"Alias {alias.match} set to {alias.replacement}.",
                "data": _alias_payload(ctx, alias),
            }
        )

    def _parse_alias_definition(self, alias_text: str) -> tuple[str, str] | None:
        if "=" in alias_text:
            match, replacement = alias_text.split("=", 1)
            return normalize_alias_match(match), replacement.strip()

        parts = alias_text.split(maxsplit=1)
        if len(parts) < 2:
            return None
        return normalize_alias_match(parts[0]), parts[1].strip()

    def _show_alias(self, ctx: CommandContext, alias_text: str) -> None:
        match = normalize_alias_match(alias_text)
        error = validate_alias_match(match)
        if error:
            _publish_alias_error(ctx, error, "invalid_alias_match", match=match)
            return

        alias = (
            Alias.objects.filter(player=ctx.player, match__iexact=match)
            .order_by("id")
            .first()
        )
        if not alias:
            _publish_alias_error(
                ctx,
                f"No alias named {match}.",
                "unknown_alias",
                match=match,
            )
            return

        ctx.publish(
            {
                "type": "cmd.alias.success",
                "text": f"{alias.match} -> {alias.replacement}",
                "data": _alias_payload(ctx, alias),
            }
        )

    def _render_alias_list(self, aliases: list[dict]) -> str:
        if not aliases:
            return "No aliases defined."
        lines = ["Aliases:"]
        lines.extend(
            f"{alias['match']} -> {alias['replacement']}"
            for alias in aliases
        )
        return "\n".join(lines)


@register_handler
class UnaliasHandler(CommandHandler):
    command_type = "unalias"
    text_commands = ("unalias",)
    help = {
        "name": "Unalias",
        "format": "unalias <name>",
        "description": "Remove a command alias.",
        "examples": [
            "unalias x",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = ctx.payload.get("args", [])
        match = normalize_alias_match(args[0] if args else "")
        error = validate_alias_match(match)
        if error:
            _publish_alias_error(ctx, error, "invalid_alias_match", match=match)
            return

        deleted = delete_player_alias(ctx.player, match)
        if not deleted:
            _publish_alias_error(
                ctx,
                f"No alias named {match}.",
                "unknown_alias",
                match=match,
            )
            return

        ctx.publish(
            {
                "type": "cmd.unalias.success",
                "text": f"Alias {match} removed.",
                "data": {"aliases": serialize_player_aliases(ctx.player)},
            }
        )
