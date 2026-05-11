from spawns.actions.abilities import (
    AbilityAction,
    LearnAbilityAction,
    SetAbilityHotkeyAction,
    UnlearnAbilityAction,
    resolve_ability_for_hotkey,
    resolve_ability_for_command,
)
from spawns.actions.base import ActionError
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def handle_dynamic_ability_command(ctx: CommandContext) -> bool:
    if ctx.actor_type != "player" or not ctx.player:
        return False

    command = ctx.payload.get("command")
    ability = resolve_ability_for_hotkey(ctx.player, command)
    if not ability:
        ability = resolve_ability_for_command(ctx.player.world, command)
    if not ability:
        return False

    args = ctx.payload.get("args", [])
    try:
        result = AbilityAction().execute(
            ctx.player.id,
            ability=ability,
            command=ctx.payload.get("command") or ability.slug,
            args=args,
        )
    except ActionError as err:
        ctx.publish(
            {
                "type": "cmd.ability.error",
                "text": err.message,
                "data": {"error": err.message, "code": err.code, **err.data},
            }
        )
        return True

    publish_events(
        result.events,
        actor_key=ctx.player.key,
        connection_id=ctx.connection_id,
    )
    return True


@register_handler
class LearnAbilityHandler(CommandHandler):
    command_type = "ability.learn"
    text_commands = ("learn",)
    help = {
        "name": "Learn Ability",
        "format": "learn <ability>",
        "description": "Add an available ability to your known ability list.",
        "examples": ["learn power strike"],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = " ".join(ctx.payload.get("args", []))
        try:
            result = LearnAbilityAction().execute(ctx.player.id, selector)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.ability.learn.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class SetAbilityHotkeyHandler(CommandHandler):
    command_type = "ability.hotkey"
    text_commands = ("hotkey", "hotkeys")
    help = {
        "name": "Ability Hotkey",
        "format": "hotkey <1-8> <ability>",
        "description": "Assign a known ability to a numbered action bar hotkey.",
        "examples": [
            "hotkey 1 power strike",
            "hotkey 4 mend",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = ctx.payload.get("args", [])
        hotkey = args[0] if args else None
        selector = " ".join(args[1:]) if len(args) > 1 else None
        try:
            result = SetAbilityHotkeyAction().execute(ctx.player.id, hotkey, selector)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.ability.hotkey.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class UnlearnAbilityHandler(CommandHandler):
    command_type = "ability.unlearn"
    text_commands = ("unlearn",)
    help = {
        "name": "Unlearn Ability",
        "format": "unlearn <ability>",
        "description": "Remove an ability from your known ability list.",
        "examples": ["unlearn power strike"],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = " ".join(ctx.payload.get("args", []))
        try:
            result = UnlearnAbilityAction().execute(ctx.player.id, selector)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.ability.unlearn.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )
