"""Text and structured command handlers for WR2 crafting."""

from __future__ import annotations

from spawns.actions.base import ActionError
from spawns.actions.crafting import (
    CraftItemAction,
    InspectRecipeAction,
    ListMaterialsAction,
    ListRecipesAction,
    ListSalvageItemsAction,
    SalvageItemAction,
)
from spawns.events import publish_events
from spawns.crafting import split_provider_suffix
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _args_text(ctx: CommandContext) -> str:
    return " ".join(str(arg) for arg in (ctx.payload.get("args") or [])).strip()


def _publish_action_error(ctx: CommandContext, command_type: str, err: ActionError) -> None:
    ctx.publish(
        {
            "type": f"cmd.{command_type}.error",
            "text": err.message,
            "data": {
                "error": err.message,
                "code": err.code,
                **err.data,
            },
        }
    )


def _publish_result(ctx: CommandContext, result) -> None:
    publish_events(
        result.events,
        actor_key=ctx.player.key,
        connection_id=ctx.connection_id,
    )


def _request_kwargs(ctx: CommandContext) -> dict:
    return {
        "request_id": ctx.payload.get("_request_id"),
        "request_segment": ctx.payload.get("_request_segment", "r"),
    }


@register_handler
class MaterialsHandler(CommandHandler):
    command_type = "materials"
    text_commands = ("materials",)
    help = {
        "name": "Materials",
        "format": "materials",
        "description": "Show your current unspent crafting materials.",
        "examples": ["materials"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = ListMaterialsAction().execute(ctx.player.id)
        except ActionError as err:
            _publish_action_error(ctx, "materials", err)
            return
        _publish_result(ctx, result)


@register_handler
class RecipesHandler(CommandHandler):
    command_type = "recipes"
    text_commands = ("recipes",)
    help = {
        "name": "Recipes",
        "format": "recipes [filter] [at <workshop>]",
        "description": "List numbered recipes offered by local workshops.",
        "details": [
            "Filters include authored recipe groups plus armor, weapons, and ready.",
        ],
        "examples": [
            "recipes",
            "recipes hoplite",
            "recipes ready at forge",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        raw = _args_text(ctx)
        filter_text, parsed_provider = split_provider_suffix(raw)
        filter_name = ctx.payload.get("filter")
        if filter_name is None:
            filter_name = filter_text or None
        provider_selector = ctx.payload.get("provider") or parsed_provider
        try:
            result = ListRecipesAction().execute(
                ctx.player.id,
                filter_name,
                provider_selector,
            )
        except ActionError as err:
            _publish_action_error(ctx, "recipes", err)
            return
        _publish_result(ctx, result)


@register_handler
class RecipeHandler(CommandHandler):
    command_type = "recipe"
    text_commands = ("recipe",)
    help = {
        "name": "Recipe",
        "format": "recipe [<number> | <item>] [at <workshop>]",
        "description": "List local recipes or inspect one recipe's requirements.",
        "details": [
            "Use recipe with no item to list local recipes.",
            "Use recipe <number> to inspect the corresponding listed recipe.",
        ],
        "examples": [
            "recipe",
            "recipe 2",
            "recipe blue-crested helm",
            "recipe guard helm at forge",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        raw_selector = ctx.payload.get("recipe")
        if raw_selector is None:
            raw_selector = ctx.payload.get("selector")
        if raw_selector is None:
            raw_selector = _args_text(ctx)
        raw_selector = "" if raw_selector is None else str(raw_selector)
        split_selector, parsed_provider = split_provider_suffix(raw_selector)
        provider_selector = ctx.payload.get("provider")
        if provider_selector is None and parsed_provider and not split_selector:
            provider_selector = parsed_provider
        recipe_selector = split_selector if not split_selector else raw_selector.strip()
        if not recipe_selector:
            try:
                result = ListRecipesAction().execute(
                    ctx.player.id,
                    ctx.payload.get("filter"),
                    provider_selector,
                )
            except ActionError as err:
                _publish_action_error(ctx, "recipe", err)
                return
            _publish_result(ctx, result)
            return
        try:
            result = InspectRecipeAction().execute(
                ctx.player.id,
                recipe_selector,
                provider_selector,
            )
        except ActionError as err:
            _publish_action_error(ctx, "recipe", err)
            return
        _publish_result(ctx, result)


@register_handler
class CraftHandler(CommandHandler):
    command_type = "craft"
    text_commands = ("craft",)
    help = {
        "name": "Craft",
        "format": "craft [<number> | <item>] [at <workshop>]",
        "description": "Spend materials to craft one randomly rolled item.",
        "details": [
            "Use craft with no item to list local recipes.",
            "Use craft <number> to make the corresponding listed recipe.",
        ],
        "examples": [
            "craft",
            "craft 2",
            "craft blue-crested helm",
            "craft guard helm at forge",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        raw_selector = ctx.payload.get("recipe")
        if raw_selector is None:
            raw_selector = ctx.payload.get("selector")
        if raw_selector is None:
            raw_selector = _args_text(ctx)
        raw_selector = "" if raw_selector is None else str(raw_selector)
        split_selector, parsed_provider = split_provider_suffix(raw_selector)
        provider_selector = ctx.payload.get("provider")
        if provider_selector is None and parsed_provider and not split_selector:
            provider_selector = parsed_provider
        recipe_selector = split_selector if not split_selector else raw_selector.strip()
        if not recipe_selector:
            try:
                result = ListRecipesAction().execute(
                    ctx.player.id,
                    ctx.payload.get("filter"),
                    provider_selector,
                )
            except ActionError as err:
                _publish_action_error(ctx, "craft", err)
                return
            _publish_result(ctx, result)
            return
        try:
            result = CraftItemAction().execute(
                ctx.player.id,
                recipe_selector,
                provider_selector,
                **_request_kwargs(ctx),
            )
        except ActionError as err:
            _publish_action_error(ctx, "craft", err)
            return
        _publish_result(ctx, result)


@register_handler
class SalvageHandler(CommandHandler):
    command_type = "salvage"
    text_commands = ("salvage",)
    help = {
        "name": "Salvage",
        "format": "salvage | salvage <number> | salvage <item> | salvage spoils",
        "description": "List salvageable gear or destroy it for crafting materials.",
        "details": [
            "Bare salvage lists eligible carried items with one-based numbers.",
            "Use salvage <number> to destroy the corresponding listed item.",
            "salvage spoils processes only items explicitly marked as captured spoils.",
        ],
        "examples": [
            "salvage",
            "salvage 2",
            "salvage scale coat",
            "salvage 2.helm",
            "salvage spoils",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        selector_value = ctx.payload.get("item")
        if selector_value is None:
            selector_value = ctx.payload.get("selector")
        if selector_value is None:
            selector_value = _args_text(ctx)
        selector = "" if selector_value is None else str(selector_value).strip()
        spoils = ctx.payload.get("spoils") is True or selector.lower() == "spoils"
        if not selector and not spoils:
            try:
                result = ListSalvageItemsAction().execute(ctx.player.id)
            except ActionError as err:
                _publish_action_error(ctx, "salvage", err)
                return
            _publish_result(ctx, result)
            return
        try:
            result = SalvageItemAction().execute(
                ctx.player.id,
                None if spoils else selector,
                spoils=spoils,
                **_request_kwargs(ctx),
            )
        except ActionError as err:
            _publish_action_error(ctx, "salvage", err)
            return
        _publish_result(ctx, result)
