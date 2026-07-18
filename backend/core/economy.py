"""Shared currency identity, validation, and presentation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError


MAX_CURRENCY_AMOUNT = 9_007_199_254_740_991


class EconomyConfigurationError(ValueError):
    """Raised when a world does not have a usable economy definition."""


def economy_world(world):
    """Return the base world that owns currency definitions for ``world``."""
    if world is None:
        raise EconomyConfigurationError("A world is required.")

    current = world
    seen = set()
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        if current.context_id:
            current = current.context
            continue
        if current.instance_of_id:
            current = current.instance_of
            continue
        return current
    raise EconomyConfigurationError("The world's economy ownership is cyclic.")


def default_currency(world):
    base_world = economy_world(world)
    currency = base_world.default_currency
    if currency is None:
        raise EconomyConfigurationError(
            f"World {base_world.pk} does not have a default currency.")
    if currency.world_id != base_world.pk:
        raise EconomyConfigurationError(
            "The default currency does not belong to the economy world.")
    return currency


def resolve_currency(world, reference=None):
    """Resolve a Currency object, ID, or bare code within one economy world."""
    from builders.models import Currency

    base_world = economy_world(world)
    if reference in (None, ""):
        return default_currency(base_world)
    if isinstance(reference, Currency):
        currency = reference
    elif isinstance(reference, int) or str(reference).strip().isdigit():
        currency = Currency.objects.filter(pk=int(reference)).first()
    else:
        currency = Currency.objects.filter(
            world=base_world,
            code__iexact=str(reference).strip(),
        ).first()
    if currency is None or currency.world_id != base_world.pk:
        raise EconomyConfigurationError(
            f"Unknown currency for world {base_world.pk}.")
    return currency


def validate_currency_amount(value, *, allow_zero=True, field_name="amount") -> int:
    if isinstance(value, bool):
        raise ValidationError({field_name: "Must be an integer."})
    try:
        amount = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({field_name: "Must be an integer."})
    if str(value).strip() != str(amount) and not isinstance(value, int):
        raise ValidationError({field_name: "Must be an integer."})
    minimum = 0 if allow_zero else 1
    if amount < minimum:
        raise ValidationError({field_name: f"Must be at least {minimum}."})
    if amount > MAX_CURRENCY_AMOUNT:
        raise ValidationError({
            field_name: f"Must not exceed {MAX_CURRENCY_AMOUNT}.",
        })
    return amount


def currency_name(currency, amount: int) -> str:
    if amount == 1:
        return currency.name
    return currency.plural_name or currency.name


def format_currency(amount: int, currency) -> str:
    return f"{int(amount)} {currency_name(currency, int(amount))}"


def currency_payload(currency) -> dict:
    return {
        "code": currency.code,
        "name": currency.name,
        "plural_name": currency.plural_name or currency.name,
        "description": currency.description or "",
    }


def money_payload(amount: int, currency) -> dict:
    return {
        "amount": int(amount),
        "currency": currency.code,
        "display": format_currency(int(amount), currency),
    }
