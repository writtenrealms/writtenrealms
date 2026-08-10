# Merchants

Merchants may be provided directly by a room or by a shopkeeper NPC. A room
merchant remains available without a mob, while an NPC merchant may become
unavailable when that character leaves or dies.

## Actions and Commands

Merchant rooms and merchant lookups show two actions:

- **LIST** shows the merchant's numbered stock. `list`, `shop`, and bare `buy`
  open the same view.
- **OFFER** shows the numbered items the merchant is currently willing to buy
  from your inventory. `offer` and bare `sell` open the same view.

When exactly one merchant is available, use:

- `buy <number-or-item>` to purchase an item from **LIST**.
- `sell <number-or-item>` to sell an item from **OFFER**.
- `buyback` to view recently sold items that this merchant still holds, when
  buyback is enabled.

When multiple merchants are available, identify the intended provider:

- `list <merchant>` or `offer <merchant>`
- `buy <number-or-item> from <merchant>`
- `sell <number-or-item> to <merchant>`
- `buyback <merchant>`
- `buyback <item> from <merchant>`

The room and lookup buttons choose their provider explicitly, so they remain
deterministic even when another merchant is present.

## Numbered Views

**LIST** ends with `buy # to purchase an item`; for example, `buy 2` purchases
the second displayed entry. **OFFER** similarly supports `sell 2`. Item names
remain valid when you prefer them.

Each view displays at most 100 entries and reports when more exist. Its numbered
selection remains available for ten minutes. Purchasing, selling, moving an
item, or a merchant restock can invalidate an entry; the command fails closed
instead of shifting that number onto another item, so open the view again when
prompted. Item links in older console output also become inactive after the
underlying item is no longer in that view or you leave the merchant's room.

## Prices, Eligibility, and Funds

Item lookups show the item's value and merchant action price with the currency
label in uppercase. LIST and OFFER show the player's current balance below the
entries.

OFFER includes only directly carried items the merchant can currently buy.
Quest items, captured spoils marked for salvage, unpriced items, items valued
in another currency, and nonempty containers are not eligible. A finite-funds
merchant also omits items it cannot presently afford.

Every purchase or sale checks the item, merchant budget, and player balance
again as one transaction. If any check fails, no item or currency changes
hands. A finite merchant's purchasing budget resets on its authored restock
schedule; buying from that merchant does not replenish the budget.

If buyback is enabled, the merchant retains a limited number of recently sold
items for that player. Older entries may expire as the list fills or its
authored expiration time passes.
