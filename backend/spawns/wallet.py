"""Transactional player-wallet operations for authored WR2 currencies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from builders.models import Currency
from core.economy import (
    MAX_CURRENCY_AMOUNT,
    currency_payload,
    economy_world,
    money_payload,
    validate_currency_amount,
)
from spawns.events import GameEvent, enqueue_game_events
from spawns.models import Player, PlayerCurrencyBalance


class WalletError(ValueError):
    def __init__(self, message: str, *, code: str = "wallet_error"):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class WalletChange:
    currency: Currency
    delta: int
    before: int
    after: int

    def payload(self) -> dict:
        return {
            "currency": currency_payload(self.currency),
            "delta": self.delta,
            "before": self.before,
            "after": self.after,
            "money": money_payload(self.after, self.currency),
        }


@dataclass(frozen=True)
class WalletMutation:
    player: Player
    revision: int
    changes: tuple[WalletChange, ...]

    def payload(self, *, reason: str) -> dict:
        return {
            "player": self.player.key,
            "wallet_revision": self.revision,
            "reason": reason,
            "changes": [change.payload() for change in self.changes],
        }


def _normalize_deltas(deltas: Mapping[Currency | int, int] | Iterable[tuple[Currency | int, int]]):
    entries = deltas.items() if isinstance(deltas, Mapping) else deltas
    normalized = defaultdict(int)
    for reference, raw_delta in entries:
        if isinstance(raw_delta, bool):
            raise WalletError("Currency deltas must be integers.", code="invalid_amount")
        try:
            delta = int(raw_delta)
        except (TypeError, ValueError, OverflowError):
            raise WalletError("Currency deltas must be integers.", code="invalid_amount")
        if delta != raw_delta:
            raise WalletError("Currency deltas must be integers.", code="invalid_amount")
        currency_id = reference.pk if isinstance(reference, Currency) else int(reference)
        normalized[currency_id] += delta
    return {currency_id: delta for currency_id, delta in normalized.items() if delta}


def mutate_balances(
    player_or_id: Player | int,
    deltas: Mapping[Currency | int, int] | Iterable[tuple[Currency | int, int]],
    *,
    reason: str,
    emit_event: bool = True,
) -> WalletMutation:
    """Atomically apply a bounded batch after locking Player, then balance rows."""
    normalized = _normalize_deltas(deltas)
    player_id = player_or_id.pk if isinstance(player_or_id, Player) else int(player_or_id)

    with transaction.atomic():
        # Lock the player row on its own.  Joining through the nullable world
        # context here makes PostgreSQL reject FOR UPDATE on the outer join.
        player = Player.objects.select_for_update().get(pk=player_id)
        if not normalized:
            return WalletMutation(player, int(player.wallet_revision), ())

        base_world = economy_world(player.world)
        currencies = {
            currency.pk: currency
            for currency in Currency.objects.filter(pk__in=normalized).order_by("pk")
        }
        if set(currencies) != set(normalized) or any(
            currency.world_id != base_world.pk for currency in currencies.values()
        ):
            raise WalletError(
                "A currency does not belong to this player's world.",
                code="cross_world_currency",
            )

        rows = {
            row.currency_id: row
            for row in PlayerCurrencyBalance.objects.select_for_update()
            .filter(player=player, currency_id__in=sorted(normalized))
            .order_by("currency_id")
        }
        missing_ids = [currency_id for currency_id in sorted(normalized) if currency_id not in rows]
        if missing_ids:
            PlayerCurrencyBalance.objects.bulk_create([
                PlayerCurrencyBalance(player=player, currency_id=currency_id, amount=0)
                for currency_id in missing_ids
            ])
            rows = {
                row.currency_id: row
                for row in PlayerCurrencyBalance.objects.select_for_update()
                .filter(player=player, currency_id__in=sorted(normalized))
                .order_by("currency_id")
            }

        changes = []
        for currency_id in sorted(normalized):
            row = rows[currency_id]
            before = int(row.amount)
            after = before + normalized[currency_id]
            if after < 0:
                raise WalletError("Insufficient funds.", code="insufficient_funds")
            if after > MAX_CURRENCY_AMOUNT:
                raise WalletError(
                    "The resulting balance is too large.",
                    code="amount_out_of_range",
                )
            row.amount = after
            changes.append(WalletChange(
                currency=currencies[currency_id],
                delta=normalized[currency_id],
                before=before,
                after=after,
            ))

        modified_ts = timezone.now()
        changed_rows = list(rows.values())
        for row in changed_rows:
            row.modified_ts = modified_ts
        PlayerCurrencyBalance.objects.bulk_update(
            changed_rows,
            ["amount", "modified_ts"],
        )
        player.wallet_revision = int(player.wallet_revision) + 1
        player.save(update_fields=["wallet_revision", "modified_ts"])
        if isinstance(player_or_id, Player):
            player_or_id.wallet_revision = player.wallet_revision
            if hasattr(player_or_id, "_currency_condition_snapshot"):
                delattr(player_or_id, "_currency_condition_snapshot")
            prefetched = getattr(player_or_id, "_prefetched_objects_cache", None)
            if prefetched is not None:
                prefetched.pop("currency_balances", None)
        mutation = WalletMutation(player, player.wallet_revision, tuple(changes))
        if emit_event:
            enqueue_game_events([
                GameEvent(
                    type="currency.balances_changed",
                    recipients=[player.key],
                    data=mutation.payload(reason=reason),
                )
            ])
        return mutation


def replace_balances(
    player_or_id: Player | int,
    amounts: Mapping[Currency | int, int],
    *,
    reason: str,
    emit_event: bool = True,
) -> WalletMutation:
    """Replace configured balances exactly; omitted existing currencies become zero."""
    player_id = player_or_id.pk if isinstance(player_or_id, Player) else int(player_or_id)
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player_id)
        existing = {
            row.currency_id: int(row.amount)
            for row in PlayerCurrencyBalance.objects.filter(player=player)
        }
        targets = {}
        for currency, amount in amounts.items():
            try:
                currency_id = (
                    currency.pk if isinstance(currency, Currency) else int(currency)
                )
                targets[currency_id] = validate_currency_amount(amount)
            except (TypeError, ValueError, ValidationError):
                raise WalletError(
                    "Invalid replacement balance.",
                    code="invalid_amount",
                )
        deltas = {
            currency_id: targets.get(currency_id, 0) - before
            for currency_id, before in existing.items()
        }
        for currency_id, amount in targets.items():
            deltas.setdefault(currency_id, amount)
        return mutate_balances(
            player,
            deltas,
            reason=reason,
            emit_event=emit_event,
        )


def balance_map(player: Player, *, include_zero: bool = True) -> dict[str, int]:
    """Return a code-keyed wallet snapshot with missing catalog rows as zero."""
    base_world = economy_world(player.world)
    prefetched = getattr(player, "_prefetched_objects_cache", {})
    rows = prefetched.get("currency_balances")
    if rows is None:
        rows = PlayerCurrencyBalance.objects.filter(player=player).select_related(
            "currency")
    balances = {
        row.currency.code: int(row.amount)
        for row in rows
    }
    if include_zero:
        for code in Currency.objects.filter(world=base_world).values_list("code", flat=True):
            balances.setdefault(code, 0)
    return balances
