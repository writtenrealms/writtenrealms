from __future__ import annotations

import math
from datetime import timedelta
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from builders.models import MerchantProfile, MerchantStockSlot
from spawns.actions.base import ActionError
from spawns.actions.targeting import item_matches_selector, resolve_room_mob_target
from spawns.models import (
    Item,
    MerchantBuybackEntry,
    MerchantRuntime,
    MerchantStockEntry,
    Mob,
    Player,
)


def _item_price(item: Item, markup: float = 1.0) -> int:
    base_cost = max(0, int(item.cost or 0))
    if base_cost <= 0:
        return 0
    return max(0, int(math.ceil(base_cost * float(markup or 0))))


def _serialized_item_payload(item: Item, *, viewer: Player) -> dict:
    from spawns.state_payloads import serialize_item

    return serialize_item(item, viewer=viewer).model_dump()


def _serialize_stock_entry(entry: MerchantStockEntry, *, viewer: Player) -> dict:
    return {
        "id": entry.id,
        "key": f"merchant_stock_entry.{entry.id}",
        "price": int(entry.price or 0),
        "currency": (
            entry.runtime.profile.funds_currency.code
            if entry.runtime.profile.funds_currency
            else "gold"
        ),
        "item": _serialized_item_payload(entry.item, viewer=viewer),
        "source_slot": entry.stock_slot.key if entry.stock_slot else "",
    }


def _serialize_buyback_entry(entry: MerchantBuybackEntry, *, viewer: Player) -> dict:
    return {
        "id": entry.id,
        "key": f"merchant_buyback_entry.{entry.id}",
        "price": int(entry.buyback_price or 0),
        "currency": (
            entry.runtime.profile.funds_currency.code
            if entry.runtime.profile.funds_currency
            else "gold"
        ),
        "item": _serialized_item_payload(entry.item, viewer=viewer),
    }


def _profile_budget(profile: MerchantProfile) -> int | None:
    if profile.funds_mode != MerchantProfile.FUNDS_MODE_FINITE:
        return None
    return max(0, int(profile.purchase_budget or 0))


def _set_next_restock(runtime: MerchantRuntime, *, now=None) -> None:
    now = now or timezone.now()
    interval = runtime.profile.restock_interval_seconds
    if interval:
        runtime.next_restock_ts = now + timedelta(seconds=int(interval))
    else:
        runtime.next_restock_ts = None


def create_or_update_merchant_runtime(mob: Mob) -> MerchantRuntime | None:
    definition = mob.definition
    profile = definition.merchant_profile if definition else None
    if not profile:
        existing = getattr(mob, "merchant_runtime", None)
        if existing:
            existing.is_active = False
            existing.save(update_fields=["is_active", "modified_ts"])
        return None

    runtime, created = MerchantRuntime.objects.get_or_create(
        mob=mob,
        defaults={
            "world": mob.world,
            "profile": profile,
            "is_active": True,
            "remaining_purchase_budget": _profile_budget(profile),
        },
    )
    if not created:
        update_fields = []
        profile_changed = False
        if runtime.profile_id != profile.id:
            runtime.profile = profile
            update_fields.append("profile")
            runtime.last_restocked_ts = None
            update_fields.append("last_restocked_ts")
            profile_changed = True
        if runtime.world_id != mob.world_id:
            runtime.world = mob.world
            update_fields.append("world")
        if not runtime.is_active:
            runtime.is_active = True
            update_fields.append("is_active")
        if update_fields:
            runtime.save(update_fields=[*update_fields, "modified_ts"])
        if profile_changed:
            _retire_stock_entries(
                runtime.stock_entries.filter(
                    status=MerchantStockEntry.STATUS_AVAILABLE,
                ).select_related("item")
            )

    if created or not runtime.last_restocked_ts:
        restock_merchant(runtime)
    return runtime


def deactivate_merchant_runtime(mob: Mob) -> None:
    runtime = getattr(mob, "merchant_runtime", None)
    if not runtime or not runtime.is_active:
        return
    runtime.is_active = False
    runtime.save(update_fields=["is_active", "modified_ts"])


def _retire_stock_entries(entries: Iterable[MerchantStockEntry]) -> None:
    for entry in entries:
        if entry.status != MerchantStockEntry.STATUS_AVAILABLE:
            continue
        entry.status = MerchantStockEntry.STATUS_RETIRED
        entry.save(update_fields=["status", "modified_ts"])
        item = entry.item
        item.is_pending_deletion = True
        item.pending_deletion_ts = timezone.now()
        item.save(update_fields=["is_pending_deletion", "pending_deletion_ts", "modified_ts"])


def _expire_buyback_entries(runtime: MerchantRuntime) -> None:
    if runtime.profile.buyback_expires != MerchantProfile.BUYBACK_EXPIRES_ON_RESTOCK:
        return
    entries = runtime.buyback_entries.filter(status=MerchantBuybackEntry.STATUS_ACTIVE)
    for entry in entries.select_related("item"):
        entry.status = MerchantBuybackEntry.STATUS_EXPIRED
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.is_pending_deletion = True
        entry.item.pending_deletion_ts = timezone.now()
        entry.item.save(update_fields=["is_pending_deletion", "pending_deletion_ts", "modified_ts"])


def _create_stock_entry(runtime: MerchantRuntime, slot: MerchantStockSlot, item: Item) -> MerchantStockEntry:
    roll_metadata = item.roll_metadata if isinstance(item.roll_metadata, dict) else {}
    return MerchantStockEntry.objects.create(
        runtime=runtime,
        stock_slot=slot,
        item=item,
        bundle_roll_id=roll_metadata.get("source_bundle_roll_id") or "",
        price=_item_price(item, runtime.profile.sell_markup),
    )


def _restock_definition_slot(runtime: MerchantRuntime, slot: MerchantStockSlot) -> None:
    available_count = runtime.stock_entries.filter(
        stock_slot=slot,
        status=MerchantStockEntry.STATUS_AVAILABLE,
    ).count()
    missing = max(0, int(slot.count or 0) - available_count)
    for _index in range(missing):
        item = slot.item_definition.spawn(target=runtime, spawn_world=runtime.world)
        _create_stock_entry(runtime, slot, item)


def _bundle_roll_count(runtime: MerchantRuntime, slot: MerchantStockSlot) -> int:
    roll_ids = set()
    for entry in runtime.stock_entries.filter(
        stock_slot=slot,
        status=MerchantStockEntry.STATUS_AVAILABLE,
    ).exclude(bundle_roll_id=""):
        roll_ids.add(entry.bundle_roll_id)
    return len(roll_ids)


def _restock_bundle_slot(runtime: MerchantRuntime, slot: MerchantStockSlot) -> None:
    if slot.refresh == MerchantStockSlot.REFRESH_REROLL_ON_RESTOCK:
        _retire_stock_entries(
            runtime.stock_entries.filter(
                stock_slot=slot,
                status=MerchantStockEntry.STATUS_AVAILABLE,
            ).select_related("item")
        )

    available_rolls = _bundle_roll_count(runtime, slot)
    missing = max(0, int(slot.count or 0) - available_rolls)
    for _index in range(missing):
        items = slot.item_bundle.spawn(target=runtime, spawn_world=runtime.world)
        for item in items:
            _create_stock_entry(runtime, slot, item)


def restock_merchant(runtime: MerchantRuntime) -> MerchantRuntime:
    runtime = MerchantRuntime.objects.select_related("profile", "profile__funds_currency").get(pk=runtime.pk)
    now = timezone.now()
    runtime.remaining_purchase_budget = _profile_budget(runtime.profile)
    _expire_buyback_entries(runtime)

    for slot in runtime.profile.stock_slots.select_related("item_definition", "item_bundle").order_by("created_ts", "id"):
        if slot.item_definition_id:
            _restock_definition_slot(runtime, slot)
        elif slot.item_bundle_id:
            _restock_bundle_slot(runtime, slot)

    runtime.last_restocked_ts = now
    _set_next_restock(runtime, now=now)
    runtime.save(
        update_fields=[
            "remaining_purchase_budget",
            "last_restocked_ts",
            "next_restock_ts",
            "modified_ts",
        ]
    )
    return runtime


def restock_if_due(runtime: MerchantRuntime) -> MerchantRuntime:
    if runtime.next_restock_ts and runtime.next_restock_ts <= timezone.now():
        return restock_merchant(runtime)
    return runtime


def resolve_merchant_runtime(player: Player, selector: str | None) -> MerchantRuntime:
    if not player.room_id:
        raise ActionError("You are nowhere.", code="no_room")
    merchant = resolve_room_mob_target(
        player.room,
        selector,
        empty_error="Which merchant?",
        not_found_error="You don't see that merchant here.",
        allow_single_match_when_empty=True,
    )
    try:
        runtime = merchant.merchant_runtime
    except MerchantRuntime.DoesNotExist:
        raise ActionError("They are not a merchant.", code="not_merchant")
    if not runtime.is_active:
        raise ActionError("That merchant is not available.", code="merchant_unavailable")
    if (
        merchant.definition
        and merchant.definition.merchant_availability == "alive_and_present"
        and merchant.is_pending_deletion
    ):
        raise ActionError("That merchant is not available.", code="merchant_unavailable")
    return restock_if_due(runtime)


def list_merchant_stock(player: Player, merchant_selector: str | None) -> dict:
    runtime = resolve_merchant_runtime(player, merchant_selector)
    entries = (
        runtime.stock_entries
        .filter(status=MerchantStockEntry.STATUS_AVAILABLE, item__is_pending_deletion=False)
        .select_related("item", "item__definition", "item__currency", "stock_slot", "runtime__profile__funds_currency")
        .order_by("stock_slot__created_ts", "stock_slot__id", "id")
    )
    return {
        "merchant": {
            "id": runtime.mob_id,
            "key": runtime.mob.key,
            "name": runtime.mob.name,
        },
        "stock": [_serialize_stock_entry(entry, viewer=player) for entry in entries],
        "funds": {
            "mode": runtime.profile.funds_mode,
            "remaining_purchase_budget": runtime.remaining_purchase_budget,
        },
    }


def _find_stock_entry(runtime: MerchantRuntime, selector: str | None) -> MerchantStockEntry:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Buy what?", code="missing_item")
    if normalized.startswith("merchant_stock_entry."):
        raw_id = normalized.split(".", 1)[1]
        if raw_id.isdigit():
            entry = runtime.stock_entries.filter(
                pk=int(raw_id),
                status=MerchantStockEntry.STATUS_AVAILABLE,
                item__is_pending_deletion=False,
            ).first()
            if entry:
                return entry

    matches = [
        entry
        for entry in runtime.stock_entries.filter(
            status=MerchantStockEntry.STATUS_AVAILABLE,
            item__is_pending_deletion=False,
        ).select_related("item", "item__definition")
        if item_matches_selector(entry.item, normalized)
    ]
    if not matches:
        raise ActionError("That item is not for sale.", code="stock_not_found")
    return matches[0]


def buy_item(player: Player, merchant_selector: str | None, item_selector: str | None) -> dict:
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        runtime = resolve_merchant_runtime(player, merchant_selector)
        runtime = MerchantRuntime.objects.select_for_update().select_related("profile", "mob").get(pk=runtime.pk)
        entry = _find_stock_entry(runtime, item_selector)
        entry = MerchantStockEntry.objects.select_for_update().select_related("item").get(pk=entry.pk)
        price = int(entry.price or 0)
        if int(player.gold or 0) < price:
            raise ActionError("You cannot afford that.", code="insufficient_funds")

        player.gold = int(player.gold or 0) - price
        player.save(update_fields=["gold", "modified_ts"])
        entry.status = MerchantStockEntry.STATUS_SOLD
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.container = player
        entry.item.save(update_fields=["container_type", "container_id", "modified_ts"])

    return {
        "merchant": {"id": runtime.mob_id, "key": runtime.mob.key, "name": runtime.mob.name},
        "item": _serialized_item_payload(entry.item, viewer=player),
        "price": price,
        "gold": player.gold,
    }


def _find_player_inventory_item(player: Player, selector: str | None) -> Item:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Sell what?", code="missing_item")
    items = list(
        player.inventory.filter(is_pending_deletion=False)
        .select_related("definition", "currency")
        .order_by("id")
    )
    for item in items:
        if item_matches_selector(item, normalized):
            return item
    raise ActionError("You are not carrying that.", code="item_not_found")


def _enforce_buyback_cap(runtime: MerchantRuntime, player: Player) -> None:
    max_items = int(runtime.profile.buyback_max_items or 0)
    if not runtime.profile.buyback_enabled or max_items <= 0:
        return
    active_entries = list(
        runtime.buyback_entries
        .filter(player=player, status=MerchantBuybackEntry.STATUS_ACTIVE)
        .select_related("item")
        .order_by("-created_ts", "-id")
    )
    for entry in active_entries[max_items:]:
        entry.status = MerchantBuybackEntry.STATUS_EXPIRED
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.is_pending_deletion = True
        entry.item.pending_deletion_ts = timezone.now()
        entry.item.save(update_fields=["is_pending_deletion", "pending_deletion_ts", "modified_ts"])


def sell_item(player: Player, merchant_selector: str | None, item_selector: str | None) -> dict:
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        runtime = resolve_merchant_runtime(player, merchant_selector)
        runtime = MerchantRuntime.objects.select_for_update().select_related("profile", "mob").get(pk=runtime.pk)
        item = _find_player_inventory_item(player, item_selector)
        price = _item_price(item, runtime.profile.buy_multiplier)

        if runtime.profile.funds_mode == MerchantProfile.FUNDS_MODE_FINITE:
            remaining = int(runtime.remaining_purchase_budget or 0)
            if remaining < price:
                raise ActionError("The merchant cannot afford that right now.", code="merchant_funds_exhausted")
            runtime.remaining_purchase_budget = remaining - price
            runtime.save(update_fields=["remaining_purchase_budget", "modified_ts"])

        player.gold = int(player.gold or 0) + price
        player.save(update_fields=["gold", "modified_ts"])
        item.container = runtime
        item.save(update_fields=["container_type", "container_id", "modified_ts"])

        buyback_entry = None
        if runtime.profile.buyback_enabled and int(runtime.profile.buyback_max_items or 0) > 0:
            buyback_entry = MerchantBuybackEntry.objects.create(
                runtime=runtime,
                player=player,
                item=item,
                sold_price=price,
                buyback_price=price,
            )
            _enforce_buyback_cap(runtime, player)
        else:
            item.is_pending_deletion = True
            item.pending_deletion_ts = timezone.now()
            item.save(update_fields=["is_pending_deletion", "pending_deletion_ts", "modified_ts"])

    return {
        "merchant": {"id": runtime.mob_id, "key": runtime.mob.key, "name": runtime.mob.name},
        "item": _serialized_item_payload(item, viewer=player),
        "price": price,
        "gold": player.gold,
        "remaining_purchase_budget": runtime.remaining_purchase_budget,
        "buyback_entry_id": buyback_entry.id if buyback_entry else None,
    }


def list_buyback(player: Player, merchant_selector: str | None) -> dict:
    runtime = resolve_merchant_runtime(player, merchant_selector)
    entries = (
        runtime.buyback_entries
        .filter(player=player, status=MerchantBuybackEntry.STATUS_ACTIVE, item__is_pending_deletion=False)
        .select_related("item", "item__definition", "item__currency", "runtime__profile__funds_currency")
        .order_by("-created_ts", "-id")
    )
    return {
        "merchant": {"id": runtime.mob_id, "key": runtime.mob.key, "name": runtime.mob.name},
        "buyback": [_serialize_buyback_entry(entry, viewer=player) for entry in entries],
    }


def _find_buyback_entry(runtime: MerchantRuntime, player: Player, selector: str | None) -> MerchantBuybackEntry:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Buy back what?", code="missing_item")
    if normalized.startswith("merchant_buyback_entry."):
        raw_id = normalized.split(".", 1)[1]
        if raw_id.isdigit():
            entry = runtime.buyback_entries.filter(
                pk=int(raw_id),
                player=player,
                status=MerchantBuybackEntry.STATUS_ACTIVE,
                item__is_pending_deletion=False,
            ).first()
            if entry:
                return entry

    matches = [
        entry
        for entry in runtime.buyback_entries.filter(
            player=player,
            status=MerchantBuybackEntry.STATUS_ACTIVE,
            item__is_pending_deletion=False,
        ).select_related("item", "item__definition")
        if item_matches_selector(entry.item, normalized)
    ]
    if not matches:
        raise ActionError("That item is not available for buyback.", code="buyback_not_found")
    return matches[0]


def buyback_item(player: Player, merchant_selector: str | None, item_selector: str | None) -> dict:
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        runtime = resolve_merchant_runtime(player, merchant_selector)
        runtime = MerchantRuntime.objects.select_for_update().select_related("profile", "mob").get(pk=runtime.pk)
        entry = _find_buyback_entry(runtime, player, item_selector)
        entry = MerchantBuybackEntry.objects.select_for_update().select_related("item").get(pk=entry.pk)
        price = int(entry.buyback_price or 0)
        if int(player.gold or 0) < price:
            raise ActionError("You cannot afford that.", code="insufficient_funds")

        player.gold = int(player.gold or 0) - price
        player.save(update_fields=["gold", "modified_ts"])
        entry.status = MerchantBuybackEntry.STATUS_BOUGHT_BACK
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.container = player
        entry.item.save(update_fields=["container_type", "container_id", "modified_ts"])

    return {
        "merchant": {"id": runtime.mob_id, "key": runtime.mob.key, "name": runtime.mob.name},
        "item": _serialized_item_payload(entry.item, viewer=player),
        "price": price,
        "gold": player.gold,
    }
