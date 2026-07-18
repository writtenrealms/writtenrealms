from __future__ import annotations

from spawns.actions.base import ActionError
from spawns.actions.merchants import (
    BuyMerchantItemAction,
    BuybackMerchantItemAction,
    ListMerchantBuybackAction,
    ListMerchantStockAction,
    SellMerchantItemAction,
)
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _args_text(ctx: CommandContext) -> str:
    return " ".join(str(arg) for arg in (ctx.payload.get("args") or [])).strip()


def _split_on_marker(text: str, marker: str) -> tuple[str, str | None]:
    marker_text = f" {marker} "
    normalized = f" {text.strip()} "
    index = normalized.lower().find(marker_text)
    if index == -1:
        return text.strip(), None
    before = normalized[:index].strip()
    after = normalized[index + len(marker_text):].strip()
    return before, after


def _money_text(value: dict | None) -> str:
    value = value or {}
    return str(value.get("display") or f"{value.get('amount', 0)} {value.get('currency', '')}").strip()


def _stock_text(data: dict) -> str:
    merchant = (data.get("merchant") or {}).get("name") or "The merchant"
    stock = data.get("stock") or []
    if not stock:
        return f"{merchant} has nothing for sale."
    lines = [f"{merchant} offers:"]
    for entry in stock:
        item = entry.get("item") or {}
        lines.append(
            f"{entry.get('id')}. {item.get('name') or 'item'} - "
            f"{_money_text(entry.get('price'))}")
    return "\n".join(lines)


def _buyback_text(data: dict) -> str:
    merchant = (data.get("merchant") or {}).get("name") or "The merchant"
    entries = data.get("buyback") or []
    if not entries:
        return f"{merchant} is not holding any of your recently sold items."
    lines = [f"{merchant} can sell back:"]
    for entry in entries:
        item = entry.get("item") or {}
        lines.append(
            f"{entry.get('id')}. {item.get('name') or 'item'} - "
            f"{_money_text(entry.get('price'))}")
    return "\n".join(lines)


@register_handler
class ShopHandler(CommandHandler):
    command_type = "shop"
    text_commands = ("shop", "list")
    help = {
        "name": "Shop",
        "format": "shop <merchant> | list <merchant>",
        "description": "View a merchant's current stock.",
        "examples": [
            "shop garron",
            "list blacksmith",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = ListMerchantStockAction().execute(ctx.player.id, _args_text(ctx) or None)
        except ActionError as err:
            ctx.publish_error("shop", err.message)
            return
        ctx.publish_success("shop", result.data, text=_stock_text(result.data))


@register_handler
class BuyHandler(CommandHandler):
    command_type = "buy"
    text_commands = ("buy",)
    help = {
        "name": "Buy",
        "format": "buy <item> from <merchant>",
        "description": "Buy an item from a merchant.",
        "examples": [
            "buy sword from garron",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        item_selector, merchant_selector = _split_on_marker(_args_text(ctx), "from")
        try:
            result = BuyMerchantItemAction().execute(
                ctx.player.id,
                merchant_selector,
                item_selector,
            )
        except ActionError as err:
            ctx.publish_error("buy", err.message)
            return
        item_name = (result.data.get("item") or {}).get("name") or "item"
        ctx.publish_success(
            "buy",
            result.data,
            text=f"You buy {item_name} for {_money_text(result.data.get('price'))}.",
        )


@register_handler
class SellHandler(CommandHandler):
    command_type = "sell"
    text_commands = ("sell",)
    help = {
        "name": "Sell",
        "format": "sell <item> to <merchant>",
        "description": "Sell an inventory item to a merchant.",
        "examples": [
            "sell dagger to garron",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        item_selector, merchant_selector = _split_on_marker(_args_text(ctx), "to")
        try:
            result = SellMerchantItemAction().execute(
                ctx.player.id,
                merchant_selector,
                item_selector,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.sell.error",
                    "text": err.message,
                    "data": {
                        "error": err.message,
                        "code": err.code,
                        **err.data,
                    },
                }
            )
            return
        item_name = (result.data.get("item") or {}).get("name") or "item"
        ctx.publish_success(
            "sell",
            result.data,
            text=f"You sell {item_name} for {_money_text(result.data.get('price'))}.",
        )


@register_handler
class BuybackHandler(CommandHandler):
    command_type = "buyback"
    text_commands = ("buyback",)
    help = {
        "name": "Buyback",
        "format": "buyback <merchant> | buyback <item> from <merchant>",
        "description": "View or reclaim recently sold items from a merchant.",
        "examples": [
            "buyback garron",
            "buyback dagger from garron",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _args_text(ctx)
        item_selector, merchant_selector = _split_on_marker(text, "from")
        try:
            if merchant_selector is None:
                result = ListMerchantBuybackAction().execute(ctx.player.id, item_selector or None)
                ctx.publish_success("buyback", result.data, text=_buyback_text(result.data))
                return
            result = BuybackMerchantItemAction().execute(
                ctx.player.id,
                merchant_selector,
                item_selector,
            )
        except ActionError as err:
            ctx.publish_error("buyback", err.message)
            return
        item_name = (result.data.get("item") or {}).get("name") or "item"
        ctx.publish_success(
            "buyback",
            result.data,
            text=f"You buy back {item_name} for {_money_text(result.data.get('price'))}.",
        )
