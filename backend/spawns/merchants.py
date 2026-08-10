from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import logging
import re
from typing import Iterable

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from builders.models import MerchantProfile, MerchantStockSlot
from config import constants as adv_consts
from core.abilities import definition_world
from core.economy import MAX_CURRENCY_AMOUNT, money_payload
from spawns.actions.base import ActionError
from spawns.actions.targeting import item_matches_selector, mob_matches_selector
from spawns.models import (
    Item,
    MerchantBuybackEntry,
    MerchantRuntime,
    MerchantStockEntry,
    Mob,
    Player,
)
from spawns.wallet import WalletError, balance_map, mutate_balances
from worlds.models import Room, World


_TOKEN_RE = re.compile(r"[a-z0-9]+")
logger = logging.getLogger(__name__)
MAX_BIGINT_ID = (1 << 63) - 1
MAX_MERCHANT_OFFER_ITEMS = 100
MAX_MERCHANT_PROVIDER_DISCOVERY = 100
MAX_MERCHANT_STOCK_LIST_ITEMS = 100
MERCHANT_SELECTION_CACHE_TIMEOUT_SECONDS = 10 * 60
MERCHANT_PURCHASE_HINT = "buy # to purchase an item"
MERCHANT_OFFER_HINT = "sell # to sell an item"
_STOCK_ORDERING = ("stock_slot__created_ts", "stock_slot__id", "id")


def _item_price(item: Item, markup: float = 1.0) -> int:
    base_cost = max(0, int(item.cost or 0))
    if base_cost <= 0:
        return 0
    price = int(
        (Decimal(base_cost) * Decimal(str(markup or 0))).to_integral_value(
            rounding=ROUND_CEILING))
    if price > MAX_CURRENCY_AMOUNT:
        raise ActionError("That price is too large.", code="price_out_of_range")
    return max(0, price)


def _serialized_item_payload(item: Item, *, viewer: Player) -> dict:
    from spawns.state_payloads import serialize_item

    return serialize_item(item, viewer=viewer).model_dump()


def _serialize_stock_entry(
    entry: MerchantStockEntry,
    *,
    number: int,
    viewer: Player,
    item_payload: dict | None = None,
) -> dict:
    return {
        "id": entry.id,
        "number": number,
        "key": f"merchant_stock_entry.{entry.id}",
        "price": money_payload(int(entry.price or 0), entry.currency),
        "item": (
            item_payload
            if item_payload is not None
            else _serialized_item_payload(entry.item, viewer=viewer)
        ),
        "source_slot": entry.stock_slot.key if entry.stock_slot else "",
    }


def _serialize_offer_entry(
    item: Item,
    *,
    number: int,
    price: int,
    currency,
    item_payload: dict,
) -> dict:
    return {
        "id": item.id,
        "number": number,
        "key": item.key,
        "price": money_payload(price, currency),
        "item": item_payload,
    }


def _serialize_buyback_entry(
    entry: MerchantBuybackEntry,
    *,
    viewer: Player,
    item_payload: dict | None = None,
) -> dict:
    return {
        "id": entry.id,
        "key": f"merchant_buyback_entry.{entry.id}",
        "price": money_payload(int(entry.buyback_price or 0), entry.currency),
        "item": (
            item_payload
            if item_payload is not None
            else _serialized_item_payload(entry.item, viewer=viewer)
        ),
    }


def _serialized_item_payload_map(
    items: Iterable[Item],
    *,
    viewer: Player | None,
) -> dict[int, dict]:
    from spawns.state_payloads import serialize_inventory

    item_list = list(items)
    payloads = serialize_inventory(item_list, viewer=viewer)
    return {
        item.id: payload.model_dump()
        for item, payload in zip(item_list, payloads)
    }


@dataclass(frozen=True)
class MerchantProvider:
    provider_type: str
    provider_id: int
    key: str
    name: str
    keywords: str
    profile: MerchantProfile
    mob: Mob | None = None
    room: Room | None = None

    def payload(self) -> dict:
        return {
            "type": self.provider_type,
            "id": self.provider_id,
            "key": self.key,
            "name": self.name,
        }


def _normalized_tokens(value: object) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").lower()))


def _room_provider_matches(provider: MerchantProvider, selector: str) -> bool:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        return False
    if normalized == provider.key.lower():
        return True
    normalized_text = " ".join(_TOKEN_RE.findall(normalized))
    if normalized_text in {
        " ".join(_TOKEN_RE.findall(provider.name.lower())),
        " ".join(_TOKEN_RE.findall(provider.profile.name.lower())),
        " ".join(_TOKEN_RE.findall(provider.profile.slug.lower())),
    }:
        return True
    selector_tokens = _normalized_tokens(normalized)
    return bool(selector_tokens) and selector_tokens.issubset(
        _normalized_tokens(provider.keywords)
    )


def _merchant_mob_queryset(player: Player, *, source_world):
    return (
        Mob.objects.filter(
            world_id=player.world_id,
            room_id=player.room_id,
            is_pending_deletion=False,
            definition__merchant_profile__isnull=False,
            definition__merchant_profile__world_id=source_world.id,
        )
        .select_related(
            "definition",
            "definition__merchant_profile",
            "definition__merchant_profile__settlement_currency",
        )
        .order_by("id")
    )


def _merchant_provider_from_mob(mob: Mob) -> MerchantProvider:
    profile = mob.definition.merchant_profile
    return MerchantProvider(
        provider_type="mob",
        provider_id=mob.id,
        key=mob.key,
        name=mob.name,
        keywords=" ".join(
            part
            for part in (
                mob.keywords,
                mob.definition.keywords,
                profile.name,
                profile.slug,
                mob.name,
            )
            if part
        ),
        profile=profile,
        mob=mob,
    )


def _room_merchant_provider(
    player: Player,
    *,
    source_world,
) -> MerchantProvider | None:
    room = player.room
    room_profile = getattr(room, "merchant_profile", None)
    if not room_profile or room_profile.world_id != source_world.id:
        return None
    return MerchantProvider(
        provider_type="room",
        provider_id=room.id,
        key=room.key,
        name=room_profile.name or room.name,
        keywords=" ".join(
            part
            for part in (
                room_profile.name,
                room_profile.slug,
                room.name,
                "shop merchant",
            )
            if part
        ),
        profile=room_profile,
        room=room,
    )


def _discover_merchant_providers(
    player: Player,
    *,
    max_mobs: int | None,
) -> tuple[list[MerchantProvider], bool]:
    if not player.room_id:
        return [], False

    source_world = definition_world(player.world)
    mobs_queryset = _merchant_mob_queryset(
        player,
        source_world=source_world,
    )
    if max_mobs is None:
        mobs = list(mobs_queryset)
        truncated = False
    else:
        mob_candidates = list(mobs_queryset[:max_mobs + 1])
        truncated = len(mob_candidates) > max_mobs
        mobs = mob_candidates[:max_mobs]

    providers = [_merchant_provider_from_mob(mob) for mob in mobs]
    room_provider = _room_merchant_provider(
        player,
        source_world=source_world,
    )
    if room_provider is not None:
        providers.append(room_provider)
    return providers, truncated


def available_merchant_providers(player: Player) -> list[MerchantProvider]:
    """Return all merchant-backed mobs plus the room provider."""
    providers, _truncated = _discover_merchant_providers(
        player,
        max_mobs=None,
    )
    return providers


def resolve_merchant_provider(
    providers: list[MerchantProvider],
    selector: str | None,
    *,
    discovery_truncated: bool = False,
) -> MerchantProvider:
    if not selector:
        if len(providers) == 1:
            return providers[0]
        if not providers:
            raise ActionError(
                "You don't see a merchant here.",
                code="target_not_found",
            )
        raise ActionError("Which merchant?", code="missing_target")

    normalized = str(selector).strip().lower()
    for provider in providers:
        if normalized == provider.key.lower():
            return provider
    matches = [
        provider
        for provider in providers
        if (
            mob_matches_selector(provider.mob, normalized)
            if provider.mob is not None
            else _room_provider_matches(provider, normalized)
        )
    ]
    if len(matches) > 1:
        raise ActionError(
            "Which merchant do you mean?",
            code="ambiguous_merchant_provider",
            data={"providers": [provider.payload() for provider in matches]},
        )
    if discovery_truncated:
        raise ActionError(
            "There are too many merchants here; use an exact merchant key.",
            code="merchant_provider_discovery_limit",
            data={
                "limit": MAX_MERCHANT_PROVIDER_DISCOVERY,
                "providers": [provider.payload() for provider in matches],
            },
        )
    if matches:
        return matches[0]
    raise ActionError(
        "You don't see that merchant here.",
        code="target_not_found",
    )


def _resolve_merchant_provider_for_player(
    player: Player,
    selector: str | None,
) -> MerchantProvider:
    """Resolve exact provider keys cheaply, then bound fuzzy discovery."""
    normalized = str(selector or "").strip().lower()
    source_world = definition_world(player.world)

    if normalized and normalized == player.room.key.lower():
        room_provider = _room_merchant_provider(
            player,
            source_world=source_world,
        )
        if room_provider is not None:
            return room_provider
        raise ActionError(
            "You don't see that merchant here.",
            code="target_not_found",
        )

    if normalized.startswith("mob."):
        mob_id = _bounded_model_id(normalized.split(".", 1)[1])
        if mob_id is not None:
            mob = _merchant_mob_queryset(
                player,
                source_world=source_world,
            ).filter(pk=mob_id).first()
            if mob is not None:
                return _merchant_provider_from_mob(mob)
        raise ActionError(
            "You don't see that merchant here.",
            code="target_not_found",
        )

    providers, truncated = _discover_merchant_providers(
        player,
        max_mobs=MAX_MERCHANT_PROVIDER_DISCOVERY,
    )
    return resolve_merchant_provider(
        providers,
        selector,
        discovery_truncated=truncated,
    )


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


@transaction.atomic
def create_or_update_merchant_runtime(mob: Mob) -> MerchantRuntime | None:
    definition = mob.definition
    profile = definition.merchant_profile if definition else None
    if not profile:
        existing = getattr(mob, "merchant_runtime", None)
        if existing:
            existing.is_active = False
            existing.save(update_fields=["is_active", "modified_ts"])
        return None
    if profile.settlement_currency_id is None:
        raise ActionError(
            "That merchant has no settlement currency.",
            code="merchant_currency_missing",
        )

    runtime, created = MerchantRuntime.objects.get_or_create(
        mob=mob,
        defaults={
            "world": mob.world,
            "profile": profile,
            "settlement_currency": profile.settlement_currency,
            "is_active": True,
            "remaining_purchase_budget": _profile_budget(profile),
        },
    )
    runtime = (
        MerchantRuntime.objects.select_for_update(of=("self",))
        .select_related("profile", "settlement_currency", "mob", "room")
        .get(pk=runtime.pk)
    )
    if not created:
        _reconcile_merchant_runtime(
            runtime,
            profile=profile,
            world=mob.world,
        )

    if created or not runtime.last_restocked_ts:
        runtime = restock_merchant(runtime, only_if_due=True)
    return runtime


def _reconcile_merchant_runtime(
    runtime: MerchantRuntime,
    *,
    profile: MerchantProfile,
    world: World,
) -> None:
    update_fields = []
    if runtime.profile_id != profile.id:
        runtime.profile = profile
        update_fields.append("profile")
        runtime.last_restocked_ts = None
        update_fields.append("last_restocked_ts")
        runtime.next_restock_ts = None
        update_fields.append("next_restock_ts")
    if runtime.settlement_currency_id != profile.settlement_currency_id:
        runtime.settlement_currency = profile.settlement_currency
        update_fields.append("settlement_currency")
        if "last_restocked_ts" not in update_fields:
            runtime.last_restocked_ts = None
            update_fields.append("last_restocked_ts")
        if "next_restock_ts" not in update_fields:
            runtime.next_restock_ts = None
            update_fields.append("next_restock_ts")
    if runtime.world_id != world.id:
        runtime.world = world
        update_fields.append("world")
    if not runtime.is_active:
        runtime.is_active = True
        update_fields.append("is_active")
    if update_fields:
        runtime.save(update_fields=[*update_fields, "modified_ts"])


def _room_with_merchant_profile(room_id: int) -> Room:
    return (
        Room.objects.select_related(
            "merchant_profile",
            "merchant_profile__settlement_currency",
        )
        .get(pk=room_id)
    )


@transaction.atomic
def _reconcile_created_room_merchant_runtime(
    runtime_id: int,
    *,
    expected_profile_id: int,
    expected_profile_modified_ts,
) -> None:
    """Close first-runtime races with concurrent room/profile authoring."""
    runtime = (
        MerchantRuntime.objects.select_for_update(of=("self",))
        .select_related("world", "room")
        .filter(pk=runtime_id)
        .first()
    )
    if runtime is None or runtime.room_id is None:
        return

    room = _room_with_merchant_profile(runtime.room_id)
    profile = room.merchant_profile
    if (
        profile is None
        or profile.world_id != definition_world(runtime.world).id
    ):
        if runtime.is_active:
            runtime.is_active = False
            runtime.save(update_fields=["is_active", "modified_ts"])
        return

    update_fields = []
    if runtime.profile_id != profile.id:
        runtime.profile = profile
        update_fields.append("profile")
    if runtime.settlement_currency_id != profile.settlement_currency_id:
        runtime.settlement_currency = profile.settlement_currency
        update_fields.append("settlement_currency")
    if (
        profile.id != expected_profile_id
        or profile.modified_ts != expected_profile_modified_ts
    ):
        runtime.last_restocked_ts = None
        runtime.next_restock_ts = None
        update_fields.extend(["last_restocked_ts", "next_restock_ts"])
    if not runtime.is_active:
        runtime.is_active = True
        update_fields.append("is_active")
    if update_fields:
        runtime.save(update_fields=[*dict.fromkeys(update_fields), "modified_ts"])


@transaction.atomic
def create_or_update_room_merchant_runtime(
    room: Room,
    world: World,
) -> MerchantRuntime | None:
    # Authored rooms are shared by every live copy of a world. Never lock the
    # room here: doing so would serialize otherwise independent shops across
    # all parallel runtimes. The runtime row below is the mutable, world-local
    # lock boundary. A fresh attachment read before and after that lock keeps
    # builder changes coherent without creating a global hot row.
    room = _room_with_merchant_profile(room.pk)
    if world.id != room.world_id and world.context_id != room.world_id:
        raise ActionError(
            "That merchant is outside this runtime world.",
            code="merchant_unavailable",
        )

    runtime = None
    profile = room.merchant_profile
    if not profile:
        runtime = (
            MerchantRuntime.objects.select_for_update(of=("self",))
            .select_related("profile", "settlement_currency", "mob", "room")
            .filter(world=world, room=room)
            .first()
        )
        room = _room_with_merchant_profile(room.pk)
        profile = room.merchant_profile
        if not profile:
            if runtime is not None and runtime.is_active:
                runtime.is_active = False
                runtime.save(update_fields=["is_active", "modified_ts"])
            return None
    if profile.world_id != definition_world(world).id:
        raise ActionError(
            "That merchant profile is not available in this world.",
            code="merchant_unavailable",
        )
    if profile.settlement_currency_id is None:
        raise ActionError(
            "That merchant has no settlement currency.",
            code="merchant_currency_missing",
        )

    created = False
    if runtime is None:
        runtime, created = MerchantRuntime.objects.get_or_create(
            world=world,
            room=room,
            defaults={
                "profile": profile,
                "settlement_currency": profile.settlement_currency,
                "is_active": True,
                "remaining_purchase_budget": _profile_budget(profile),
            },
        )
        runtime = (
            MerchantRuntime.objects.select_for_update(of=("self",))
            .select_related("profile", "settlement_currency", "mob", "room")
            .get(pk=runtime.pk)
        )

    current_room = _room_with_merchant_profile(room.pk)
    profile = current_room.merchant_profile
    if profile is None:
        if runtime.is_active:
            runtime.is_active = False
            runtime.save(update_fields=["is_active", "modified_ts"])
        return None
    if profile.world_id != definition_world(world).id:
        raise ActionError(
            "That merchant profile is not available in this world.",
            code="merchant_unavailable",
        )
    if profile.settlement_currency_id is None:
        raise ActionError(
            "That merchant has no settlement currency.",
            code="merchant_currency_missing",
        )

    if not created:
        _reconcile_merchant_runtime(runtime, profile=profile, world=world)
    elif runtime.profile_id != profile.id:
        _reconcile_merchant_runtime(runtime, profile=profile, world=world)
    if created or not runtime.last_restocked_ts:
        runtime = restock_merchant(runtime, only_if_due=True)
    if created:
        transaction.on_commit(
            lambda runtime_id=runtime.id,
            profile_id=profile.id,
            profile_modified_ts=profile.modified_ts: (
                _reconcile_created_room_merchant_runtime(
                    runtime_id,
                    expected_profile_id=profile_id,
                    expected_profile_modified_ts=profile_modified_ts,
                )
            )
        )
    return runtime


@transaction.atomic
def _sync_room_merchant_runtimes(room_id: int) -> int:
    try:
        # This runs only after an authoring transaction commits. Serializing
        # callbacks on the authored room makes their fresh read + runtime
        # update coherent without putting the shared room lock on gameplay's
        # steady-state path.
        room = (
            Room.objects.select_for_update(of=("self",))
            .select_related(
                "merchant_profile",
                "merchant_profile__settlement_currency",
            )
            .get(pk=room_id)
        )
    except Room.DoesNotExist:
        return 0
    runtimes = MerchantRuntime.objects.filter(room_id=room_id)
    profile = room.merchant_profile
    modified_ts = timezone.now()
    if profile is None:
        return runtimes.update(is_active=False, modified_ts=modified_ts)
    return runtimes.update(
        profile_id=profile.id,
        settlement_currency_id=profile.settlement_currency_id,
        is_active=True,
        last_restocked_ts=None,
        next_restock_ts=None,
        modified_ts=modified_ts,
    )


def invalidate_room_merchant_runtimes(room: Room) -> int:
    """Invalidate live room shops after an authored attachment changes."""
    runtimes = MerchantRuntime.objects.filter(room_id=room.pk)
    profile = getattr(room, "merchant_profile", None)
    modified_ts = timezone.now()
    if profile is None:
        updated = runtimes.update(is_active=False, modified_ts=modified_ts)
    else:
        updated = runtimes.update(
            profile_id=profile.id,
            settlement_currency_id=profile.settlement_currency_id,
            is_active=True,
            last_restocked_ts=None,
            next_restock_ts=None,
            modified_ts=modified_ts,
        )
    transaction.on_commit(
        lambda room_id=room.pk: _sync_room_merchant_runtimes(room_id)
    )
    return updated


def merchant_runtime_payload(runtime: MerchantRuntime) -> dict:
    if runtime.mob_id:
        return {
            "type": "mob",
            "id": runtime.mob_id,
            "key": runtime.mob.key,
            "name": runtime.mob.name,
        }
    return {
        "type": "room",
        "id": runtime.room_id,
        "key": runtime.room.key,
        "name": runtime.profile.name or runtime.room.name,
    }


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
    if item.cost is not None and item.currency_id != runtime.settlement_currency_id:
        raise ActionError(
            "Merchant stock uses a different currency.",
            code="merchant_currency_mismatch",
        )
    return MerchantStockEntry.objects.create(
        runtime=runtime,
        stock_slot=slot,
        item=item,
        bundle_roll_id=roll_metadata.get("source_bundle_roll_id") or "",
        price=_item_price(item, runtime.profile.sell_markup),
        currency=runtime.settlement_currency,
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


@transaction.atomic
def restock_merchant(
    runtime: MerchantRuntime,
    *,
    only_if_due: bool = False,
) -> MerchantRuntime:
    runtime = (
        MerchantRuntime.objects.select_for_update(of=("self",))
        .select_related(
            "profile",
            "profile__settlement_currency",
            "settlement_currency",
            "mob",
            "room",
        )
        .get(pk=runtime.pk)
    )
    now = timezone.now()
    if runtime.profile.settlement_currency_id is None:
        raise ActionError(
            "That merchant has no settlement currency.",
            code="merchant_currency_missing",
        )
    settlement_changed = (
        runtime.settlement_currency_id
        != runtime.profile.settlement_currency_id
    )
    generation_changed = runtime.last_restocked_ts is None or settlement_changed
    if (
        only_if_due
        and not generation_changed
        and (
            runtime.next_restock_ts is None
            or runtime.next_restock_ts > now
        )
    ):
        return runtime

    if generation_changed:
        _retire_stock_entries(
            runtime.stock_entries.filter(
                status=MerchantStockEntry.STATUS_AVAILABLE,
            ).select_related("item")
        )
    runtime.remaining_purchase_budget = _profile_budget(runtime.profile)
    runtime.settlement_currency = runtime.profile.settlement_currency
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
            "settlement_currency",
            "last_restocked_ts",
            "next_restock_ts",
            "modified_ts",
        ]
    )
    return runtime


def restock_if_due(runtime: MerchantRuntime) -> MerchantRuntime:
    if (
        runtime.last_restocked_ts is None
        or runtime.settlement_currency_id
        != runtime.profile.settlement_currency_id
        or (
            runtime.next_restock_ts
            and runtime.next_restock_ts <= timezone.now()
        )
    ):
        return restock_merchant(runtime, only_if_due=True)
    return runtime


def resolve_merchant_runtime(player: Player, selector: str | None) -> MerchantRuntime:
    if not player.room_id:
        raise ActionError("You are nowhere.", code="no_room")
    provider = _resolve_merchant_provider_for_player(player, selector)
    if provider.mob is not None:
        runtime = create_or_update_merchant_runtime(provider.mob)
    else:
        runtime = create_or_update_room_merchant_runtime(
            provider.room,
            player.world,
        )
    if runtime is None:
        raise ActionError(
            "That merchant is not available.",
            code="merchant_unavailable",
        )
    if not runtime.is_active:
        raise ActionError("That merchant is not available.", code="merchant_unavailable")
    if (
        provider.mob is not None
        and provider.mob.definition
        and provider.mob.definition.merchant_availability == "alive_and_present"
        and provider.mob.is_pending_deletion
    ):
        raise ActionError("That merchant is not available.", code="merchant_unavailable")
    return restock_if_due(runtime)


def _available_stock_queryset(runtime: MerchantRuntime):
    return (
        runtime.stock_entries.filter(
            status=MerchantStockEntry.STATUS_AVAILABLE,
            item__is_pending_deletion=False,
        )
        .select_related(
            "item",
            "item__definition",
            "item__currency",
            "stock_slot",
            "currency",
        )
        .order_by(*_STOCK_ORDERING)
    )


def _merchant_offer_inventory_queryset(
    player: Player,
    runtime: MerchantRuntime,
):
    """The canonical numbered OFFER inventory in stable item-id order."""
    queryset = (
        player.inventory.filter(
            is_pending_deletion=False,
            cost__isnull=False,
            currency_id=runtime.settlement_currency_id,
        )
        .exclude(definition__salvage_only=True)
        .exclude(type=adv_consts.ITEM_TYPE_QUEST)
        .exclude(inventory__isnull=False)
        .select_related("definition", "currency")
        .order_by("id")
    )
    multiplier = Decimal(str(runtime.profile.buy_multiplier or 0))
    if multiplier > 0:
        maximum_price = MAX_CURRENCY_AMOUNT
        if runtime.profile.funds_mode == MerchantProfile.FUNDS_MODE_FINITE:
            maximum_price = min(
                maximum_price,
                max(0, int(runtime.remaining_purchase_budget or 0)),
            )
        maximum_base_cost = min(
            MAX_CURRENCY_AMOUNT,
            int(
                (Decimal(maximum_price) / multiplier).to_integral_value(
                    rounding=ROUND_FLOOR,
                )
            ),
        )
        queryset = queryset.filter(cost__lte=maximum_base_cost)
    return queryset


def _bounded_ordinal(value: str, *, limit: int) -> int | None:
    if not value.isascii() or not value.isdigit():
        return None
    if len(value) > len(str(limit)):
        return None
    number = int(value)
    return number if 0 < number <= limit else None


def _bounded_model_id(value: str) -> int | None:
    if not value.isascii() or not value.isdigit() or len(value) > 19:
        return None
    number = int(value)
    return number if 0 < number <= MAX_BIGINT_ID else None


def _merchant_selection_cache_key(
    *,
    player_id: int,
    runtime_id: int,
    view: str,
) -> str:
    return f"spawns.merchant.selection.v1.{view}.{player_id}.{runtime_id}"


def _cache_merchant_selection(
    *,
    player_id: int,
    runtime_id: int,
    view: str,
    object_ids: Iterable[int],
) -> None:
    bounded_ids = [
        int(object_id)
        for object_id in object_ids
    ][:MAX_MERCHANT_STOCK_LIST_ITEMS]
    try:
        cache.set(
            _merchant_selection_cache_key(
                player_id=player_id,
                runtime_id=runtime_id,
                view=view,
            ),
            {"ids": bounded_ids},
            timeout=MERCHANT_SELECTION_CACHE_TIMEOUT_SECONDS,
        )
    except Exception:
        # Selection snapshots are an optimization/safety token, never
        # authoritative state. Listing still succeeds if the cache is down;
        # subsequent numeric mutations fail closed.
        logger.warning(
            "Merchant selection cache write failed for player=%s runtime=%s view=%s.",
            player_id,
            runtime_id,
            view,
            exc_info=True,
        )


def _cached_merchant_selection_id(
    *,
    player_id: int,
    runtime_id: int,
    view: str,
    number: int,
) -> int | None:
    try:
        snapshot = cache.get(
            _merchant_selection_cache_key(
                player_id=player_id,
                runtime_id=runtime_id,
                view=view,
            )
        )
    except Exception:
        logger.warning(
            "Merchant selection cache read failed for player=%s runtime=%s view=%s.",
            player_id,
            runtime_id,
            view,
            exc_info=True,
        )
        return None

    ids = snapshot.get("ids") if isinstance(snapshot, dict) else None
    if not isinstance(ids, (list, tuple)) or len(ids) > MAX_MERCHANT_STOCK_LIST_ITEMS:
        return None
    if number > len(ids):
        return None
    try:
        object_id = int(ids[number - 1])
    except (TypeError, ValueError):
        return None
    return object_id if 0 < object_id <= MAX_BIGINT_ID else None


def _player_settlement_balance(player: Player, runtime: MerchantRuntime) -> dict:
    amount = (
        player.currency_balances.filter(
            currency_id=runtime.settlement_currency_id,
        )
        .values_list("amount", flat=True)
        .first()
    )
    return money_payload(int(amount or 0), runtime.settlement_currency)


def _merchant_funds_payload(runtime: MerchantRuntime) -> dict:
    return {
        "mode": runtime.profile.funds_mode,
        "remaining_purchase_budget": runtime.remaining_purchase_budget,
        "currency": runtime.settlement_currency.code,
    }


def list_merchant_stock(player: Player, merchant_selector: str | None) -> dict:
    runtime = resolve_merchant_runtime(player, merchant_selector)
    candidates = list(
        _available_stock_queryset(runtime)[:MAX_MERCHANT_STOCK_LIST_ITEMS + 1]
    )
    truncated = len(candidates) > MAX_MERCHANT_STOCK_LIST_ITEMS
    entries = candidates[:MAX_MERCHANT_STOCK_LIST_ITEMS]
    item_payloads = _serialized_item_payload_map(
        (entry.item for entry in entries),
        viewer=None,
    )
    _cache_merchant_selection(
        player_id=player.id,
        runtime_id=runtime.id,
        view="stock",
        object_ids=(entry.id for entry in entries),
    )
    return {
        "merchant": merchant_runtime_payload(runtime),
        "stock": [
            _serialize_stock_entry(
                entry,
                number=number,
                viewer=player,
                item_payload=item_payloads[entry.item_id],
            )
            for number, entry in enumerate(entries, start=1)
        ],
        "funds": _merchant_funds_payload(runtime),
        "balance": _player_settlement_balance(player, runtime),
        "hint": MERCHANT_PURCHASE_HINT,
        "truncated": truncated,
        "limit": MAX_MERCHANT_STOCK_LIST_ITEMS,
    }


def _find_stock_entry(
    player: Player,
    runtime: MerchantRuntime,
    selector: str | None,
) -> MerchantStockEntry:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Buy what?", code="missing_item")
    if normalized.startswith("merchant_stock_entry."):
        raw_id = normalized.split(".", 1)[1]
        entry_id = _bounded_model_id(raw_id)
        if entry_id is not None:
            entry = runtime.stock_entries.filter(
                pk=entry_id,
                status=MerchantStockEntry.STATUS_AVAILABLE,
                item__is_pending_deletion=False,
            ).first()
            if entry:
                return entry

    number = _bounded_ordinal(
        normalized,
        limit=MAX_MERCHANT_STOCK_LIST_ITEMS,
    )
    if number is not None:
        entry_id = _cached_merchant_selection_id(
            player_id=player.id,
            runtime_id=runtime.id,
            view="stock",
            number=number,
        )
        if entry_id is None:
            raise ActionError(
                "List this merchant's stock again before buying by number.",
                code="merchant_stock_selection_stale",
            )
        entry = _available_stock_queryset(runtime).filter(pk=entry_id).first()
        if entry:
            return entry
        raise ActionError("That item is no longer for sale.", code="stock_not_found")
    elif normalized.isascii() and normalized.isdigit():
        raise ActionError("That item is not for sale.", code="stock_not_found")

    for entry in _available_stock_queryset(runtime)[:MAX_MERCHANT_STOCK_LIST_ITEMS]:
        if item_matches_selector(entry.item, normalized):
            return entry
    raise ActionError("That item is not for sale.", code="stock_not_found")


def list_merchant_offers(player: Player, merchant_selector: str | None) -> dict:
    runtime = resolve_merchant_runtime(player, merchant_selector)
    candidates = list(
        _merchant_offer_inventory_queryset(player, runtime)[
            :MAX_MERCHANT_OFFER_ITEMS + 1
        ]
    )
    truncated = len(candidates) > MAX_MERCHANT_OFFER_ITEMS
    items = candidates[:MAX_MERCHANT_OFFER_ITEMS]
    item_payloads = _serialized_item_payload_map(items, viewer=None)
    _cache_merchant_selection(
        player_id=player.id,
        runtime_id=runtime.id,
        view="offer",
        object_ids=(item.id for item in items),
    )
    return {
        "merchant": merchant_runtime_payload(runtime),
        "offers": [
            _serialize_offer_entry(
                item,
                number=number,
                price=_item_price(item, runtime.profile.buy_multiplier),
                currency=runtime.settlement_currency,
                item_payload=item_payloads[item.id],
            )
            for number, item in enumerate(items, start=1)
        ],
        "funds": _merchant_funds_payload(runtime),
        "balance": _player_settlement_balance(player, runtime),
        "hint": MERCHANT_OFFER_HINT,
        "truncated": truncated,
        "limit": MAX_MERCHANT_OFFER_ITEMS,
    }


def buy_item(player: Player, merchant_selector: str | None, item_selector: str | None) -> dict:
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        runtime = resolve_merchant_runtime(player, merchant_selector)
        runtime = MerchantRuntime.objects.select_for_update(of=("self",)).select_related(
            "profile", "mob", "room", "settlement_currency").get(pk=runtime.pk)
        entry = _find_stock_entry(player, runtime, item_selector)
        entry = MerchantStockEntry.objects.select_for_update(of=("self",)).select_related(
            "item", "currency").get(pk=entry.pk)
        if (
            entry.status != MerchantStockEntry.STATUS_AVAILABLE
            or entry.item.is_pending_deletion
        ):
            raise ActionError(
                "That item is not for sale.",
                code="stock_not_found",
            )
        entry.item = (
            Item.objects.select_for_update(of=("self",))
            .select_related("definition", "currency")
            .get(pk=entry.item_id)
        )
        price = int(entry.price or 0)
        try:
            mutation = mutate_balances(
                player,
                {entry.currency: -price},
                reason="merchant.purchase",
            )
        except WalletError as error:
            raise ActionError(str(error), code=error.code)
        entry.status = MerchantStockEntry.STATUS_SOLD
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.container = player
        entry.item.save(update_fields=["container_type", "container_id", "modified_ts"])

    return {
        "merchant": merchant_runtime_payload(runtime),
        "item": _serialized_item_payload(entry.item, viewer=player),
        "price": money_payload(price, entry.currency),
        "economy": {
            "wallet_revision": mutation.revision,
            "balances": balance_map(mutation.player),
        },
    }


def _find_player_inventory_item(
    player: Player,
    runtime: MerchantRuntime,
    selector: str | None,
) -> Item:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Sell what?", code="missing_item")

    number = _bounded_ordinal(
        normalized,
        limit=MAX_MERCHANT_OFFER_ITEMS,
    )
    if number is not None:
        item_id = _cached_merchant_selection_id(
            player_id=player.id,
            runtime_id=runtime.id,
            view="offer",
            number=number,
        )
        if item_id is None:
            raise ActionError(
                "List this merchant's offers again before selling by number.",
                code="merchant_offer_selection_stale",
            )
        item = _merchant_offer_inventory_queryset(
            player,
            runtime,
        ).filter(pk=item_id).first()
        if item:
            return item
        raise ActionError("You are no longer carrying that.", code="item_not_found")
    elif normalized.isascii() and normalized.isdigit():
        raise ActionError("You are not carrying that.", code="item_not_found")

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
        runtime = MerchantRuntime.objects.select_for_update(of=("self",)).select_related(
            "profile", "mob", "room", "settlement_currency").get(pk=runtime.pk)
        item = _find_player_inventory_item(player, runtime, item_selector)
        item = (
            Item.objects.select_for_update(of=("self",))
            .select_related("definition", "currency")
            .get(pk=item.pk)
        )
        if item.is_pending_deletion or item.container != player:
            raise ActionError(
                "You are not carrying that.",
                code="item_not_found",
            )
        if item.definition and item.definition.salvage_only:
            raise ActionError(
                "Captured spoils must be salvaged, not sold.",
                code="salvage_only",
            )
        if item.type == adv_consts.ITEM_TYPE_QUEST:
            raise ActionError(
                "Quest items cannot be sold.",
                code="item_not_sellable",
            )
        if item.cost is None or item.currency_id is None:
            raise ActionError(
                "That item has no sale value.",
                code="item_not_sellable",
            )
        if item.inventory.exists():
            raise ActionError(
                "Empty that container before selling it.",
                code="container_not_empty",
            )
        price = _item_price(item, runtime.profile.buy_multiplier)
        if item.currency_id != runtime.settlement_currency_id:
            raise ActionError(
                "The merchant does not trade in that currency.",
                code="merchant_currency_mismatch",
            )

        if runtime.profile.funds_mode == MerchantProfile.FUNDS_MODE_FINITE:
            remaining = int(runtime.remaining_purchase_budget or 0)
            if remaining < price:
                raise ActionError("The merchant cannot afford that right now.", code="merchant_funds_exhausted")
            runtime.remaining_purchase_budget = remaining - price
            runtime.save(update_fields=["remaining_purchase_budget", "modified_ts"])

        try:
            mutation = mutate_balances(
                player,
                {runtime.settlement_currency: price},
                reason="merchant.sale",
            )
        except WalletError as error:
            raise ActionError(str(error), code=error.code)
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
                currency=runtime.settlement_currency,
            )
            _enforce_buyback_cap(runtime, player)
        else:
            item.is_pending_deletion = True
            item.pending_deletion_ts = timezone.now()
            item.save(update_fields=["is_pending_deletion", "pending_deletion_ts", "modified_ts"])

    return {
        "merchant": merchant_runtime_payload(runtime),
        "item": _serialized_item_payload(item, viewer=player),
        "price": money_payload(price, runtime.settlement_currency),
        "economy": {
            "wallet_revision": mutation.revision,
            "balances": balance_map(mutation.player),
        },
        "remaining_purchase_budget": runtime.remaining_purchase_budget,
        "buyback_entry_id": buyback_entry.id if buyback_entry else None,
    }


def list_buyback(player: Player, merchant_selector: str | None) -> dict:
    runtime = resolve_merchant_runtime(player, merchant_selector)
    entries = list(
        runtime.buyback_entries
        .filter(player=player, status=MerchantBuybackEntry.STATUS_ACTIVE, item__is_pending_deletion=False)
        .select_related("item", "item__definition", "item__currency", "currency")
        .order_by("-created_ts", "-id")
    )
    item_payloads = _serialized_item_payload_map(
        (entry.item for entry in entries),
        viewer=player,
    )
    return {
        "merchant": merchant_runtime_payload(runtime),
        "buyback": [
            _serialize_buyback_entry(
                entry,
                viewer=player,
                item_payload=item_payloads[entry.item_id],
            )
            for entry in entries
        ],
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
        runtime = MerchantRuntime.objects.select_for_update(of=("self",)).select_related(
            "profile", "mob", "room", "settlement_currency").get(pk=runtime.pk)
        entry = _find_buyback_entry(runtime, player, item_selector)
        entry = MerchantBuybackEntry.objects.select_for_update(of=("self",)).select_related(
            "item", "currency").get(pk=entry.pk)
        if (
            entry.status != MerchantBuybackEntry.STATUS_ACTIVE
            or entry.player_id != player.pk
            or entry.item.is_pending_deletion
        ):
            raise ActionError(
                "That item is not available for buyback.",
                code="buyback_not_found",
            )
        entry.item = (
            Item.objects.select_for_update(of=("self",))
            .select_related("definition", "currency")
            .get(pk=entry.item_id)
        )
        price = int(entry.buyback_price or 0)
        try:
            mutation = mutate_balances(
                player,
                {entry.currency: -price},
                reason="merchant.buyback",
            )
        except WalletError as error:
            raise ActionError(str(error), code=error.code)
        entry.status = MerchantBuybackEntry.STATUS_BOUGHT_BACK
        entry.save(update_fields=["status", "modified_ts"])
        entry.item.container = player
        entry.item.save(update_fields=["container_type", "container_id", "modified_ts"])

    return {
        "merchant": merchant_runtime_payload(runtime),
        "item": _serialized_item_payload(entry.item, viewer=player),
        "price": money_payload(price, entry.currency),
        "economy": {
            "wallet_revision": mutation.revision,
            "balances": balance_map(mutation.player),
        },
    }
