"""
Item command handlers.
"""
from spawns.actions.base import ActionError
from spawns.actions.items import DropAction, GetAction, PutAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


@register_handler
class DropHandler(CommandHandler):
    command_type = "drop"
    text_commands = ("drop",)
    help = {
        "name": "Drop",
        "format": "drop <item>",
        "description": "Drop an item from your inventory into the room.",
        "examples": [
            "drop lantern",
            "drop 2.sword",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = ctx.payload.get("item")
        if not selector:
            args = ctx.payload.get("args", [])
            if args:
                selector = " ".join(args)

        if not selector:
            ctx.publish(
                {
                    "type": "cmd.drop.error",
                    "text": "Drop what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        try:
            result = DropAction().execute(ctx.player.id, selector)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.drop.error",
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
class GetHandler(CommandHandler):
    command_type = "get"
    text_commands = ("get",)
    help = {
        "name": "Get",
        "format": "get <item> | get <item> <container>",
        "description": "Take an item from the room, or from a container in the room/inventory.",
        "examples": [
            "get lantern",
            "get all chest",
            "get 2.apple backpack",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = ctx.payload.get("selector")
        source = ctx.payload.get("source")

        if not selector:
            args = ctx.payload.get("args", [])
            if args:
                selector = args[0]
                if len(args) > 1:
                    source = " ".join(args[1:])

        if not selector:
            ctx.publish(
                {
                    "type": "cmd.get.error",
                    "text": "Get what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        try:
            result = GetAction().execute(ctx.player.id, selector, source_selector=source)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.get.error",
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
class PutHandler(CommandHandler):
    command_type = "put"
    text_commands = ("put",)
    help = {
        "name": "Put",
        "format": "put <item> <container>",
        "description": "Put an inventory item into a container in the room or in your inventory.",
        "examples": [
            "put apple backpack",
            "put all chest",
            "put 2.coin pouch",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = ctx.payload.get("selector")
        target = ctx.payload.get("target")

        if not selector or not target:
            args = ctx.payload.get("args", [])
            if args:
                selector = args[0]
            if len(args) > 1:
                target = " ".join(args[1:])

        if not selector:
            ctx.publish(
                {
                    "type": "cmd.put.error",
                    "text": "Put what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        if not target:
            ctx.publish(
                {
                    "type": "cmd.put.error",
                    "text": "Put where?",
                    "data": {"error": "Missing container.", "code": "missing_container"},
                }
            )
            return

        try:
            result = PutAction().execute(ctx.player.id, selector, target_selector=target)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.put.error",
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
