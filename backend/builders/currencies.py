"""Canonical builder operations for WR2 currency definitions and defaults."""

from __future__ import annotations

import json
import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q

from builders.models import Currency, WorldStartingCurrencyBalance
from config import constants as api_consts
from core.economy import economy_world, resolve_currency, validate_currency_amount
from worlds.models import World


def _assert_base_world(world) -> World:
    base_world = economy_world(world)
    if world.pk != base_world.pk:
        raise ValidationError("Currencies are inherited from the base world.")
    return base_world


def _assert_economy_editable(world) -> None:
    locked_worlds = list(
        # ``context__instance_of`` is a nullable self-join. PostgreSQL cannot
        # lock the nullable side of an outer join, so explicitly lock only the
        # concrete World rows returned by this query.
        World.objects.select_for_update(of=("self",))
        .filter(
            Q(pk=world.pk)
            | Q(instance_of_id=world.pk)
            | Q(context_id=world.pk)
            | Q(context__instance_of_id=world.pk)
        )
        .order_by("pk")
        .values_list("context_id", "lifecycle")
    )
    safe_runtime_states = {
        api_consts.WORLD_LIFECYCLE_NEW,
        api_consts.WORLD_LIFECYCLE_STOPPED,
        api_consts.WORLD_STATE_STORED,
        api_consts.WORLD_STATE_KILLED,
    }
    if any(
        context_id is not None and lifecycle not in safe_runtime_states
        for context_id, lifecycle in locked_worlds
    ):
        raise ValidationError(
            "Stop active or transitioning worlds before changing currencies.")


def _bump_revision(world) -> None:
    World.objects.filter(pk=world.pk).update(economy_revision=F("economy_revision") + 1)


@transaction.atomic
def create_currency(*, world, code: str, name: str, plural_name="", description="") -> Currency:
    base_world = _assert_base_world(world)
    _assert_economy_editable(base_world)
    code = str(code or "").strip().lower()
    name = str(name or "").strip()
    if not name:
        raise ValidationError({"name": "A currency name is required."})
    currency = Currency(
        world=base_world,
        code=code,
        name=name,
        plural_name=str(plural_name or "").strip(),
        description=str(description or "").strip(),
    )
    currency.full_clean()
    try:
        currency.save()
    except IntegrityError:
        raise ValidationError({"code": "That currency code is already in use."})
    if base_world.default_currency_id is None:
        base_world.default_currency = currency
        base_world.save(update_fields=["default_currency", "modified_ts"])
    _bump_revision(base_world)
    return currency


@transaction.atomic
def update_currency(currency: Currency, *, name=None, plural_name=None, description=None) -> Currency:
    base_world = _assert_base_world(currency.world)
    update_fields = []
    if name is not None:
        new_name = str(name).strip()
        if not new_name:
            raise ValidationError({"name": "A currency name is required."})
        if new_name != currency.name:
            currency.name = new_name
            update_fields.append("name")
    if plural_name is not None:
        new_plural_name = str(plural_name).strip()
        if new_plural_name != currency.plural_name:
            currency.plural_name = new_plural_name
            update_fields.append("plural_name")
    if description is not None:
        new_description = str(description).strip()
        if new_description != currency.description:
            currency.description = new_description
            update_fields.append("description")
    if update_fields:
        _assert_economy_editable(base_world)
        currency.save(update_fields=[*update_fields, "modified_ts"])
        _bump_revision(base_world)
    return currency


@transaction.atomic
def select_default_currency(*, world, currency) -> Currency:
    base_world = _assert_base_world(world)
    selected = resolve_currency(base_world, currency)
    if base_world.default_currency_id != selected.pk:
        _assert_economy_editable(base_world)
        base_world.default_currency = selected
        base_world.save(update_fields=["default_currency", "modified_ts"])
        _bump_revision(base_world)
    return selected


@transaction.atomic
def replace_starting_balances(*, world, balances: dict) -> None:
    base_world = _assert_base_world(world)
    resolved = {}
    for reference, raw_amount in (balances or {}).items():
        currency = resolve_currency(base_world, reference)
        resolved[currency.pk] = (
            currency,
            validate_currency_amount(raw_amount, field_name=str(reference)),
        )
    # All economy authoring takes the world aggregate lock before any balance
    # row locks. This also serializes a missing-row create with concurrent
    # single-balance edits.
    _assert_economy_editable(base_world)
    current = dict(
        WorldStartingCurrencyBalance.objects.select_for_update()
        .filter(world=base_world)
        .order_by("currency_id")
        .values_list("currency_id", "amount")
    )
    target = {
        currency_id: amount
        for currency_id, (_, amount) in resolved.items()
        if amount
    }
    if current == target:
        return
    WorldStartingCurrencyBalance.objects.filter(world=base_world).delete()
    WorldStartingCurrencyBalance.objects.bulk_create([
        WorldStartingCurrencyBalance(
            world=base_world,
            currency=currency,
            amount=amount,
        )
        for currency, amount in resolved.values()
        if amount
    ])
    _bump_revision(base_world)


@transaction.atomic
def set_starting_balance(*, currency: Currency, amount: int) -> None:
    """Set one authored starting balance without replacing the rest."""
    base_world = _assert_base_world(currency.world)
    amount = validate_currency_amount(amount, field_name=currency.code)
    _assert_economy_editable(base_world)
    existing = (
        WorldStartingCurrencyBalance.objects.select_for_update()
        .filter(world=base_world, currency=currency)
        .order_by("currency_id")
        .first()
    )
    current_amount = int(existing.amount) if existing else 0
    if current_amount == amount:
        return
    if amount:
        WorldStartingCurrencyBalance.objects.update_or_create(
            world=base_world,
            currency=currency,
            defaults={"amount": amount},
        )
    elif existing:
        existing.delete()
    _bump_revision(base_world)


def _payload_references_currency(value, *, code: str) -> bool:
    """Find typed currency references inside canonical structured payloads."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            if (
                normalized_key in {
                    "currency",
                    "currency_code",
                    "settlement_currency",
                    "death_currency",
                    "clan_registration_currency",
                }
                and isinstance(child, str)
                and child.strip().lower() == code
            ):
                return True
            if _payload_references_currency(child, code=code):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_references_currency(child, code=code) for child in value)
    if not isinstance(value, str):
        return False

    text = value.strip()
    if not text:
        return False
    if (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and _payload_references_currency(parsed, code=code):
            return True

    escaped = re.escape(code)
    balance_path = re.compile(
        rf"(?<![a-z0-9_-])(?:actor|player)\.balances\.{escaped}(?![a-z0-9_-])",
        re.IGNORECASE,
    )
    typed_assignment = re.compile(
        rf"(?<![a-z0-9_-])currency\s*[:=]\s*['\"]?{escaped}(?![a-z0-9_-])",
        re.IGNORECASE,
    )
    return bool(balance_path.search(text) or typed_assignment.search(text))


def _trigger_snapshot_currency_references(
    snapshot,
) -> tuple[set[int], set[str]]:
    """Collect debit refs from one bounded scheduled-step snapshot once."""
    currency_ids: set[int] = set()
    currency_codes: set[str] = set()
    if not isinstance(snapshot, list):
        return currency_ids, currency_codes
    for step in snapshot:
        if not isinstance(step, dict):
            continue
        actions = step.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if (
                not isinstance(action, dict)
                or action.get("type") != "debit_currency"
            ):
                continue
            raw_currency_id = action.get("currency_id")
            if isinstance(raw_currency_id, int) and not isinstance(
                raw_currency_id,
                bool,
            ):
                currency_ids.add(raw_currency_id)
            code = str(action.get("currency") or "").strip().lower()
            if code:
                currency_codes.add(code)
    return currency_ids, currency_codes


def currency_usage_map(
    *,
    world,
    currencies=None,
    include_active_trigger_sequences: bool = False,
) -> dict[int, list[dict]]:
    """Build one bounded usage registry for a world's entire currency catalog."""
    from builders.models import (
        AbilityDefinition,
        CraftingRecipe,
        ItemDefinition,
        MerchantProfile,
        MobCurrencyReward,
        MobDefinition,
        RoomAction,
        SpawnEntry,
        SpawnPlan,
        Trigger,
    )
    from quests.models import QuestArcTemplate, QuestTemplate
    from spawns.models import (
        Item,
        MerchantBuybackEntry,
        MerchantRuntime,
        MerchantStockEntry,
        Mob,
        PlayerCurrencyBalance,
        ScheduledTriggerRun,
    )
    from worlds.models import WorldConfig

    base_world = economy_world(world)
    currency_rows = list(
        currencies
        if currencies is not None
        else Currency.objects.filter(world=base_world).only("id", "code")
    )
    currency_by_id = {currency.pk: currency for currency in currency_rows}
    usages: dict[int, list[dict]] = {currency_id: [] for currency_id in currency_by_id}
    if not currency_by_id:
        return usages
    currency_ids = tuple(currency_by_id)

    def add_grouped(label, queryset, currency_field):
        rows = (
            queryset.filter(**{f"{currency_field}__in": currency_ids})
            .values(currency_field)
            .annotate(total=Count("pk"))
        )
        for row in rows:
            usages[row[currency_field]].append(
                {"type": label, "count": int(row["total"])}
            )

    add_grouped("default currency", World.objects.all(), "default_currency_id")
    add_grouped(
        "starting balance",
        WorldStartingCurrencyBalance.objects.all(),
        "currency_id",
    )
    add_grouped("item definition", ItemDefinition.objects.all(), "currency_id")
    add_grouped("crafting recipe cost", CraftingRecipe.objects.all(), "currency_id")
    add_grouped(
        "merchant profile",
        MerchantProfile.objects.all(),
        "settlement_currency_id",
    )
    add_grouped("mob reward", MobCurrencyReward.objects.all(), "currency_id")
    add_grouped("death policy", WorldConfig.objects.all(), "death_currency_id")
    add_grouped(
        "clan registration policy",
        WorldConfig.objects.all(),
        "clan_registration_currency_id",
    )
    add_grouped(
        "player balance",
        PlayerCurrencyBalance.objects.filter(amount__gt=0),
        "currency_id",
    )
    add_grouped("runtime item", Item.objects.all(), "currency_id")
    add_grouped(
        "merchant runtime",
        MerchantRuntime.objects.all(),
        "settlement_currency_id",
    )
    add_grouped("merchant stock", MerchantStockEntry.objects.all(), "currency_id")
    add_grouped(
        "merchant buyback",
        MerchantBuybackEntry.objects.all(),
        "currency_id",
    )

    code_by_id = {
        currency_id: currency.code
        for currency_id, currency in currency_by_id.items()
    }
    authored_worlds = World.objects.filter(
        Q(pk=base_world.pk) | Q(instance_of_id=base_world.pk)
    )

    def add_structured(label, queryset, fields):
        counts = {currency_id: 0 for currency_id in currency_ids}
        for payloads in queryset.values_list(*fields).iterator():
            for currency_id, code in code_by_id.items():
                if any(
                    _payload_references_currency(payload, code=code)
                    for payload in payloads
                ):
                    counts[currency_id] += 1
        for currency_id, count in counts.items():
            if count:
                usages[currency_id].append({"type": label, "count": count})

    add_structured(
        "quest template",
        QuestTemplate.objects.filter(world__in=authored_worlds),
        ("discovery_policy", "slot_schema", "graph", "reward_policy"),
    )
    add_structured(
        "quest arc",
        QuestArcTemplate.objects.filter(world__in=authored_worlds),
        ("journal_policy",),
    )
    add_structured(
        "trigger",
        Trigger.objects.filter(world__in=authored_worlds),
        ("conditions", "script", "steps"),
    )
    add_structured(
        "room action",
        RoomAction.objects.filter(room__world__in=authored_worlds),
        ("conditions", "actions", "commands"),
    )
    add_structured(
        "crafting recipe",
        CraftingRecipe.objects.filter(world__in=authored_worlds),
        ("conditions",),
    )
    add_structured(
        "mob definition",
        MobDefinition.objects.filter(world__in=authored_worlds),
        (
            "base_properties",
            "randomization",
            "traits",
            "loot",
            "combat_abilities",
            "trainer",
        ),
    )
    add_structured(
        "spawn plan",
        SpawnPlan.objects.filter(world__in=authored_worlds),
        ("respawn_policy", "randomization", "conditions"),
    )
    add_structured(
        "spawn entry",
        SpawnEntry.objects.filter(plan__world__in=authored_worlds),
        ("source", "target", "count", "placement", "traits", "loot", "conditions"),
    )
    add_structured(
        "ability",
        AbilityDefinition.objects.filter(world__in=authored_worlds),
        (
            "target",
            "availability",
            "requirements",
            "cost",
            "cast_time",
            "cooldown",
            "components",
        ),
    )

    economy_worlds = World.objects.filter(
        Q(pk=base_world.pk)
        | Q(context_id=base_world.pk)
        | Q(instance_of_id=base_world.pk)
        | Q(context__instance_of_id=base_world.pk)
    )
    if include_active_trigger_sequences:
        active_trigger_counts = {
            currency_id: 0
            for currency_id in currency_ids
        }
        catalog_currency_ids = set(currency_ids)
        currency_id_by_code = {
            code: currency_id
            for currency_id, code in code_by_id.items()
        }
        for snapshot in ScheduledTriggerRun.objects.filter(
            runtime_world__in=economy_worlds,
            status=ScheduledTriggerRun.STATUS_ACTIVE,
        ).values_list("steps", flat=True).iterator():
            snapshot_ids, snapshot_codes = (
                _trigger_snapshot_currency_references(snapshot)
            )
            referenced_ids = snapshot_ids & catalog_currency_ids
            referenced_ids.update(
                currency_id_by_code[code]
                for code in snapshot_codes
                if code in currency_id_by_code
            )
            for currency_id in referenced_ids:
                active_trigger_counts[currency_id] += 1
        for currency_id, count in active_trigger_counts.items():
            if count:
                usages[currency_id].append(
                    {"type": "active trigger sequence", "count": count}
                )

    snapshot_counts = {currency_id: 0 for currency_id in currency_ids}
    for snapshot in Mob.objects.filter(world__in=economy_worlds).values_list(
        "currency_reward_snapshot",
        flat=True,
    ).iterator():
        if not isinstance(snapshot, dict):
            continue
        snapshot_codes = {str(code).strip().lower() for code in snapshot}
        for currency_id, code in code_by_id.items():
            if code in snapshot_codes:
                snapshot_counts[currency_id] += 1
    for currency_id, count in snapshot_counts.items():
        if count:
            usages[currency_id].append(
                {"type": "runtime mob reward", "count": count}
            )
    return usages


def currency_usage(currency: Currency) -> list[dict]:
    """Return relational, authored, and runtime blockers for deletion."""
    return currency_usage_map(
        world=currency.world,
        currencies=[currency],
        include_active_trigger_sequences=True,
    ).get(currency.pk, [])


@transaction.atomic
def delete_currency(currency: Currency) -> None:
    base_world = _assert_base_world(currency.world)
    _assert_economy_editable(base_world)
    currency = Currency.objects.select_for_update().get(pk=currency.pk)
    usages = currency_usage(currency)
    if usages:
        summary = ", ".join(f"{entry['count']} {entry['type']}" for entry in usages)
        raise ValidationError(f"Currency is still in use: {summary}.")
    currency.player_balances.filter(amount=0).delete()
    currency.delete()
    _bump_revision(base_world)
