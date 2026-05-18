"""
Item command handlers.
"""
from spawns.actions.base import ActionError
from spawns.actions.items import (
    DropAction,
    EquipAction,
    GetAction,
    GiveAction,
    PutAction,
    RemoveEquipmentAction,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _selector_from_payload(ctx: CommandContext) -> str | None:
    selector = ctx.payload.get("selector")
    if not selector:
        selector = ctx.payload.get("item")

    if isinstance(selector, dict):
        selector = selector.get("key") or selector.get("name")

    if not selector:
        args = ctx.payload.get("args", [])
        if args:
            selector = " ".join(args)

    return str(selector).strip() if selector else None


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


class _EquipHandlerBase(CommandHandler):
    command_type = "equip"
    text_commands = ()
    help = {
        "name": "Equip",
        "format": "equip <item>",
        "description": "Equip an item from your inventory, swapping occupied slots when needed.",
        "examples": [
            "equip sword",
            "equip helmet",
            "equip all.armor",
        ],
    }
    wield_only = False

    def handle(self, ctx: CommandContext) -> None:
        selector = _selector_from_payload(ctx)

        if not selector:
            ctx.publish(
                {
                    "type": f"cmd.{self.command_type}.error",
                    "text": "Equip what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        try:
            result = EquipAction().execute(
                ctx.player.id,
                selector,
                command_type=self.command_type,
                wield_only=self.wield_only,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": f"cmd.{self.command_type}.error",
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
class EquipHandler(_EquipHandlerBase):
    command_type = "equip"
    text_commands = ("equip",)


@register_handler
class WearHandler(_EquipHandlerBase):
    command_type = "wear"
    text_commands = ("wear",)
    help = {
        "name": "Wear",
        "format": "wear <item>",
        "description": "Wear an equippable item from your inventory.",
        "examples": [
            "wear helmet",
            "wear all.armor",
        ],
    }


@register_handler
class WieldHandler(_EquipHandlerBase):
    command_type = "wield"
    text_commands = ("wield",)
    wield_only = True
    help = {
        "name": "Wield",
        "format": "wield <weapon>",
        "description": "Equip a weapon from your inventory.",
        "examples": [
            "wield sword",
            "wield 2.dagger",
        ],
    }


@register_handler
class RemoveHandler(CommandHandler):
    command_type = "remove"
    text_commands = ("remove",)
    help = {
        "name": "Remove",
        "format": "remove <item>",
        "description": "Remove an equipped item and return it to your inventory.",
        "examples": [
            "remove helmet",
            "remove all",
            "remove all.armor",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = _selector_from_payload(ctx)

        if not selector:
            ctx.publish(
                {
                    "type": "cmd.remove.error",
                    "text": "Remove what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        try:
            result = RemoveEquipmentAction().execute(
                ctx.player.id,
                selector,
                command_type=self.command_type,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.remove.error",
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


@register_handler
class GiveHandler(CommandHandler):
    command_type = "give"
    text_commands = ("give",)
    help = {
        "name": "Give",
        "format": "give <item> <mob>",
        "description": "Hand an inventory item to a mob in the current room.",
        "examples": [
            "give apple guard",
            "give 2.pelt quartermaster",
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
                    "type": "cmd.give.error",
                    "text": "Give what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        if not target:
            ctx.publish(
                {
                    "type": "cmd.give.error",
                    "text": "Give to whom?",
                    "data": {"error": "Missing target.", "code": "missing_target"},
                }
            )
            return

        try:
            result = GiveAction().execute(ctx.player.id, selector, target)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.give.error",
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
