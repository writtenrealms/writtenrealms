from __future__ import annotations

from spawns.actions.base import ActionResult
from spawns.models import MerchantRuntime, Player
from spawns import merchants


class ListMerchantStockAction:
    def execute(self, player_id: int, merchant_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.list_merchant_stock(player, merchant_selector))


class ListMerchantOffersAction:
    def execute(self, player_id: int, merchant_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.list_merchant_offers(player, merchant_selector))


class BuyMerchantItemAction:
    def execute(self, player_id: int, merchant_selector: str | None, item_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.buy_item(player, merchant_selector, item_selector))


class SellMerchantItemAction:
    def execute(self, player_id: int, merchant_selector: str | None, item_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.sell_item(player, merchant_selector, item_selector))


class ListMerchantBuybackAction:
    def execute(self, player_id: int, merchant_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.list_buyback(player, merchant_selector))


class BuybackMerchantItemAction:
    def execute(self, player_id: int, merchant_selector: str | None, item_selector: str | None) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        return ActionResult(data=merchants.buyback_item(player, merchant_selector, item_selector))


class RestockMerchantAction:
    def execute(self, merchant_runtime_id: int) -> ActionResult:
        runtime = MerchantRuntime.objects.get(pk=merchant_runtime_id)
        runtime = merchants.restock_merchant(runtime)
        return ActionResult(
            data={
                "merchant_runtime_id": runtime.id,
                "next_restock_ts": runtime.next_restock_ts.isoformat() if runtime.next_restock_ts else None,
                "remaining_purchase_budget": runtime.remaining_purchase_budget,
            }
        )
