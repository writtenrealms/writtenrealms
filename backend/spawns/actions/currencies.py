"""Read-only player wallet actions."""

from __future__ import annotations

from builders.models import Currency
from core.economy import economy_world, format_currency
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.models import Player
from spawns.wallet import balance_map


class ListCurrenciesAction:
    """Return the configured balances that are useful to show a player."""

    def execute(self, player_id: int) -> ActionResult:
        player = (
            Player.objects.select_related(
                "world",
                "world__context",
                "world__context__instance_of",
                "world__instance_of",
            )
            .get(pk=player_id)
        )
        base_world = economy_world(player.world)
        currencies = list(
            Currency.objects.filter(world=base_world).order_by("code", "id")
        )
        currencies_by_id = {currency.id: currency for currency in currencies}
        default = currencies_by_id.get(base_world.default_currency_id)
        if default is None:
            raise ActionError(
                "This world does not have a default currency configured.",
                code="currency_not_configured",
            )

        balances_by_code = balance_map(player, include_zero=False)
        ordered_currencies = [
            default,
            *(currency for currency in currencies if currency.id != default.id),
        ]
        balances = {
            currency.code: int(balances_by_code.get(currency.code, 0))
            for currency in ordered_currencies
            if (
                currency.id == default.id
                or int(balances_by_code.get(currency.code, 0)) > 0
            )
        }
        data = {
            "wallet_revision": int(player.wallet_revision or 0),
            "balances": balances,
        }
        text = "Currencies:\n" + "\n".join(
            f"  {format_currency(amount, currencies_by_id[currency_id])}"
            for currency_id, amount in (
                (currency.id, balances[currency.code])
                for currency in ordered_currencies
                if currency.code in balances
            )
        )
        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.currencies.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ],
        )
