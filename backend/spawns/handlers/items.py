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
from spawns.actions.information import EquipmentAction
from spawns.events import publish_events
from spawns.command_syntax import item_relation, selector_words, strip_leading_relation
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _selector_value(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("key") or value.get("name")

    if value is None:
        return None

    selector = str(value).strip()
    return selector or None


def _selector_from_payload(ctx: CommandContext) -> str | None:
    selector = (
        _selector_value(ctx.payload.get("selector"))
        or _selector_value(ctx.payload.get("item"))
    )

    if selector is None:
        args = ctx.payload.get("args", [])
        if args:
            selector = selector_words(args)

    return _selector_value(selector)


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
        selector = _selector_from_payload(ctx)

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
        "format": "wear <item> | put on <item> | put <item> on",
        "description": "Wear an equippable item from your inventory.",
        "examples": [
            "wear helmet",
            "put helmet on",
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
        "format": "remove <item> | take off <item> | take <item> off",
        "description": "Remove an equipped item and return it to your inventory.",
        "examples": [
            "remove helmet",
            "take helmet off",
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
class EquipmentHandler(CommandHandler):
    command_type = "equipment"
    text_commands = ("equipment",)
    help = {
        "name": "Equipment",
        "format": "equipment | eq",
        "description": "Show items currently equipped by your character.",
        "examples": ["equipment", "eq"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = EquipmentAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish_error("equipment", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class GetHandler(CommandHandler):
    command_type = "get"
    text_commands = ("get", "loot")
    # Exact aliases leave established abbreviations (such as `ta` for talk)
    # unchanged.
    text_aliases = {"take": "get", "pickup": "get"}
    help = {
        "name": "Get",
        "format": "get <item> [from <container>] | get <item> <container> | loot [all | <corpse>]",
        "description": "Take an item from the room, or from a container in the room/inventory.",
        "details": [
            "You can also use `take <item>` or `pick up <item>`, and `out of` "
            "instead of `from`. Include `from` when using a multiword item name.",
            "`loot` is shorthand for `get all corpse`. Add a corpse selector "
            "to choose among multiple corpses. `loot all` means `get all all.corpse`.",
            "Use `all.<container>` to take items from every matching container. "
            "The item selector applies separately to each container; empty or "
            "nonmatching containers are skipped.",
        ],
        "examples": [
            "get lantern",
            "get spear from pouch",
            "take the shieldguard spear out of the leather pouch",
            "get all chest",
            "get all all.corpse",
            "get all.coin all.pouch",
            "get 2.apple backpack",
            "loot",
            "loot all",
            "loot 2.corpse",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = (
            _selector_value(ctx.payload.get("selector"))
            or _selector_value(ctx.payload.get("item"))
        )
        source = (
            _selector_value(ctx.payload.get("source"))
            or _selector_value(ctx.payload.get("from"))
        )
        args = ctx.payload.get("args", [])
        explicit_source = False

        if ctx.payload.get("command") == "loot":
            selector = "all"
            args, explicit_source = strip_leading_relation(args, (("from",),))
            source = selector_words(args)
            if not source and not explicit_source:
                source = "corpse"
            if source.lower() == "all":
                source = "all.corpse"
        elif not selector:
            selector, parsed_source, explicit_source = item_relation(
                args, (("out", "of"), ("from",)),
            )
            source = parsed_source or source

        if not selector:
            ctx.publish(
                {
                    "type": "cmd.get.error",
                    "text": "Get what?",
                    "data": {"error": "Missing item.", "code": "missing_item"},
                }
            )
            return

        if explicit_source and not source:
            ctx.publish({
                "type": "cmd.get.error",
                "text": "Get from where?",
                "data": {"error": "Missing container.", "code": "missing_container"},
            })
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
        "format": "put <item> [in|into|inside] <container>",
        "description": "Put an inventory item into a container in the room or in your inventory.",
        "examples": [
            "put apple backpack",
            "put spear into pouch",
            "put all chest",
            "put 2.coin pouch",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = _selector_value(ctx.payload.get("selector"))
        target = _selector_value(ctx.payload.get("target"))

        if not selector or not target:
            parsed_selector, parsed_target, _ = item_relation(
                ctx.payload.get("args", []),
                (("inside", "of"), ("into",), ("in",), ("inside",)),
            )
            selector = selector or parsed_selector
            target = target or parsed_target

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
        "format": "give <item> [to] <mob>",
        "description": "Hand an inventory item to a mob in the current room.",
        "examples": [
            "give apple guard",
            "give the apple to the guard",
            "give 2.pelt quartermaster",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector = _selector_value(ctx.payload.get("selector"))
        target = _selector_value(ctx.payload.get("target"))

        if not selector or not target:
            parsed_selector, parsed_target, _ = item_relation(
                ctx.payload.get("args", []), (("to",),),
            )
            selector = selector or parsed_selector
            target = target or parsed_target

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
