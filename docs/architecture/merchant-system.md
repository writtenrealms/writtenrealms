# WR2 Merchant System

## Purpose

WR2 needs merchants that behave like authored NPCs in the room without forcing
shop behavior to be normal mob inventory behavior.

This document specifies the target merchant model for WR2, including:

- non-killable shopkeepers
- killable shopkeepers
- fixed inventory
- random inventory from item bundles
- configurable restock intervals
- optional buyback, capped at 10 items
- unlimited merchant funds or finite purchasing power until restock

Reference docs:

- `.codex/skills/wr-transition/wr2-architecture.md`
- [guided-random-item-definitions.md](/Users/teebes/code/writtenrealms/docs/architecture/guided-random-item-definitions.md)
- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [currency-system.md](/Users/teebes/code/writtenrealms/docs/architecture/currency-system.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)

## Core Recommendation

Separate these concepts:

- NPC presence: the person or creature visible in the room
- combat capability: whether that NPC can be attacked and killed
- merchant profile: settlement currency, shop stock, prices, restock, buyback,
  and funds
- runtime stock: concrete item instances currently available for sale

A merchant should usually be an NPC-like room presence with a merchant profile.
Being a merchant must not imply being killable. Being killable must not imply
that shop stock becomes corpse loot.

The builder-facing distinction should be simple:

```yaml
merchant:
  profile: merchantprofile.garron_smithy
combat:
  attackable: false
```

versus:

```yaml
merchant:
  profile: merchantprofile.garron_smithy
combat:
  attackable: true
```

## Goals

- Let merchants look and target like NPCs in rooms.
- Let builders choose whether a shopkeeper is attackable.
- Keep shop stock separate from normal mob drops and corpse inventory.
- Support fixed authored stock with direct item definitions.
- Support random stock by rolling item bundles.
- Make restock timing configurable per merchant profile.
- Support buyback with a hard maximum of 10 items.
- Support both unlimited purchasing and finite purchasing power.
- Keep the model compatible with WR2's Command -> Action -> Event direction.
- Keep the solution clean and independent from WR1 merchant tables.

## Non-Goals

- Full economic simulation.
- Global auction house behavior.
- Player-owned shops.
- Haggling or dynamic supply-demand pricing.
- Arbitrary formula language in merchant stock definitions.
- Reusing WR1 merchant inventory or random item profile tables.

## Authoring Model

### MerchantProfile

`MerchantProfile` is the authored shop configuration. It is world-scoped and
can be attached to one or more NPC definitions.

It owns:

- stable slug/key
- display name for builder UI
- settlement currency
- pricing policy
- restock policy
- funds policy
- buyback policy
- stock slots

Example:

```yaml
kind: merchantprofile
metadata:
  key: merchantprofile.garron_smithy
spec:
  name: Garron's Smithy
  settlement_currency: crowns

  pricing:
    sell_markup: 1.2
    buy_multiplier: 0.4

  restock:
    interval_seconds: 10800

  funds:
    mode: finite
    purchase_budget: 5000

  buyback:
    enabled: true
    max_items: 10
    expires: on_restock

  stock:
    - key: iron_swords
      item_definition: itemdefinition.iron_sword
      count: 2

    - key: repair_kits
      item_definition: itemdefinition.repair_kit
      count: 5

    - key: rotating_specials
      item_bundle: itembundle.blacksmith_specials
      count: 3
      refresh: reroll_on_restock
```

`settlement_currency` is one concrete currency code from the base world's
catalog. It denominates every price, player-sale payment, buyback price, and
finite purchase budget for the profile. Monetary stock must use that same
currency. Choosing the world's default currency may prefill this field when a
profile is created, but the stored code remains explicit; changing the world
default later does not silently retarget an existing merchant.

### Stock Slots

A stock slot describes what the merchant tries to keep available.

Supported first-class slot sources:

- `item_definition`: fixed stock from a stable or guided-random item definition
- `item_bundle`: random stock from a weighted bundle of item definitions

Do not support `item_template`, `MerchantInventory`, or `RandomItemProfile` in
the new merchant authoring shape. If old content needs conversion, convert it
outside the runtime path into item definitions, item bundles, and merchant
profiles.

### Fixed Stock

Fixed stock uses `item_definition`.

```yaml
- key: torches
  item_definition: itemdefinition.torch
  count: 10
```

At restock, the merchant fills the slot back up to `count`.

If the item definition has guided randomization, each spawned item persists its
own rolled values. Fixed stock means the definition is fixed, not that every
spawned instance must have identical rolled attributes.

### Random Bundle Stock

Random stock uses `item_bundle`.

```yaml
- key: rotating_potions
  item_bundle: itembundle.common_potions
  count: 4
  refresh: reroll_on_restock
```

`count` means number of bundle rolls, not necessarily number of resulting item
instances. This matters because a bundle entry may intentionally spawn more
than one item.

Each bundle roll should produce stock entries with roll metadata:

- source stock slot key
- source bundle id/slug
- bundle roll id
- generated timestamp

Bundle stock supports two refresh modes:

- `fill_missing`: only replace sold or removed bundle rolls
- `reroll_on_restock`: remove unsold stock from that slot and roll new stock at
  each restock

Default recommendation:

- fixed direct stock defaults to `fill_missing`
- random bundle stock defaults to `reroll_on_restock`

## NPC Attachment

Merchant behavior is attached to an NPC definition with a `merchant.profile`
reference.

### Non-Killable Shopkeeper

```yaml
kind: mobdefinition
metadata:
  key: mobdefinition.garron_blacksmith
spec:
  name: Garron
  title: the Blacksmith
  room_description: Garron the Blacksmith works beside a smoking forge.
  keywords: garron blacksmith smith merchant

  combat:
    attackable: false

  merchant:
    profile: merchantprofile.garron_smithy
    availability: present
```

Behavior:

- `look garron` works.
- `talk garron` may work if authored.
- `buy sword from garron` works.
- `kill garron` rejects because Garron is not attackable.
- Shop stock is never corpse loot.

### Killable Shopkeeper

```yaml
kind: mobdefinition
metadata:
  key: mobdefinition.garron_blacksmith
spec:
  name: Garron
  title: the Blacksmith
  room_description: Garron the Blacksmith works beside a smoking forge.
  keywords: garron blacksmith smith merchant

  combat:
    attackable: true
    health: 120
    attack_power: 12
    fights_back: true

  merchant:
    profile: merchantprofile.garron_smithy
    availability: alive_and_present

  on_death:
    echo_room: Garron's hammer falls silent.
    disable_merchant: true
    drops:
      - item_definition: itemdefinition.cashbox_key
        chance: 100
```

Behavior:

- `buy sword from garron` works while Garron is alive and present.
- `kill garron` is legal because Garron is attackable.
- When Garron dies, his merchant runtime is deactivated.
- Shop stock does not drop unless the builder authors drops explicitly.
- If a spawn plan later respawns Garron, the new live NPC can open a new merchant
  runtime from the same profile.

## Runtime Model

### MerchantRuntime

`MerchantRuntime` is the live shop state for one active merchant presence.

It should track:

- merchant presence key/id
- merchant profile key/id
- active/inactive status
- last restocked timestamp
- next restock timestamp
- settlement-currency snapshot/reference
- funds state
- stock entries
- buyback entries

The runtime row is canonical mutable state, not a derived display cache.

### MerchantStockEntry

A stock entry represents a concrete item or bundle roll currently available for
purchase.

It should track:

- merchant runtime
- source stock slot
- item instance
- price snapshot as a canonical amount plus concrete settlement-currency
  reference
- bundle roll metadata, if applicable
- whether the entry is available, sold, expired, or retired

Shop stock must not be implemented as ordinary mob inventory. The target
behavior is a shop ledger from the start. This prevents merchant stock from
becoming corpse loot, accidental drops, or normal `get` targets.

Runtime responses expose prices as structured `Money` values with `amount`,
`currency`, and `display`. The stored amount and currency reference are
canonical; `display` is presentation derived from the currency definition.

### MerchantBuybackEntry

A buyback entry represents an item recently sold by a player to this merchant.

It should track:

- merchant runtime
- player
- item instance
- sold-price `Money` snapshot in the merchant's settlement currency
- buyback-price `Money` snapshot in the merchant's settlement currency
- created timestamp
- expiration policy

Buyback is per player. A player should not see another player's buyback list.

## Restock Semantics

Restock is a scheduled action:

```python
RestockMerchantAction(merchant_runtime_id)
```

The action should:

1. Lock the merchant runtime.
2. Lock related stock entries and item rows that may be replaced or created.
3. Reset finite purchasing power if configured.
4. Expire buyback entries if configured.
5. Fill fixed stock slots back to their target counts.
6. Fill or reroll bundle stock slots according to each slot's refresh mode.
7. Set the next restock timestamp.
8. Emit merchant restock events for interested clients.

`restock.interval_seconds` is required for automatic restock. If omitted or
null, the merchant only restocks when explicitly triggered by builder command,
script, or admin action.

The example profile above uses three hours:

```yaml
restock:
  interval_seconds: 10800
```

That is an authored choice, not a global default.

## Buyback

Buyback is optional.

```yaml
buyback:
  enabled: true
  max_items: 10
  expires: on_restock
```

Rules:

- `max_items` must be between 0 and 10.
- `enabled: false` is equivalent to `max_items: 0`.
- New sold items are inserted at the front of the player's buyback list.
- If the list exceeds `max_items`, the oldest buyback entry expires.
- Default expiration is `on_restock`.
- Expired buyback items are retired unless a later resale feature explicitly
  routes them into normal merchant stock.

The first pass should not put player-sold items into general shop inventory by
default. That can be added later as a separate `resale` policy.

## Funds

Merchant funds control whether a merchant can buy items from players.
`settlement_currency` belongs to the profile, not the funds policy, because it
also denominates stock prices and buyback transactions.

### Unlimited Funds

```yaml
settlement_currency: crowns
funds:
  mode: unlimited
```

Behavior:

- The merchant can always buy eligible items.
- No merchant funds ledger is checked.
- This is the simplest mode and should be the default.

### Finite Purchasing Power

```yaml
settlement_currency: crowns
funds:
  mode: finite
  purchase_budget: 5000
```

Behavior:

- The merchant starts each restock interval with `purchase_budget`.
- `purchase_budget` is denominated in the profile's `settlement_currency`.
- Buying items from players decreases the remaining purchasing power.
- If the merchant cannot afford the item, the sell command is rejected.
- At restock, remaining purchasing power resets to `purchase_budget`.
- The budget is a purchasing-power gate, not a full merchant accounting system.

For the first pass, player purchases from the merchant should not increase the
finite purchase budget unless a later design explicitly adds a shared wallet
mode. This keeps "finite purchasing power until restock" predictable for
builders.

## Commands And Actions

Recommended player commands:

- `shop <merchant>` or `list <merchant>`
- `buy <item> from <merchant>`
- `sell <item> to <merchant>`
- `buyback <merchant>`
- `buyback <item> from <merchant>`

Recommended actions:

- `ListMerchantStockAction(player_id, merchant_id)`
- `BuyMerchantItemAction(player_id, merchant_id, stock_entry_id)`
- `SellMerchantItemAction(player_id, merchant_id, item_id)`
- `BuybackMerchantItemAction(player_id, merchant_id, buyback_entry_id)`
- `RestockMerchantAction(merchant_runtime_id)`

Planning should reject merchant commands when:

- the merchant is not present
- the merchant profile is inactive
- the merchant NPC is dead and availability requires `alive_and_present`
- the requested item is not available
- the player lacks funds
- the merchant lacks purchasing power
- the item is not eligible for sale

Execution should lock the player first, then the merchant runtime and affected
items according to WR2's global aggregate order, followed by affected player
balance rows in stable currency-id order. Currency definitions are immutable
references in this transaction; the mutable rows are the code-keyed player
balances and merchant runtime state.

## Events

Recommended event types:

- `merchant.stock.viewed`
- `merchant.item.bought`
- `merchant.item.sold`
- `merchant.buyback.viewed`
- `merchant.item.bought_back`
- `merchant.restocked`
- `merchant.unavailable`

Player-facing command events can still use `cmd.buy.*`, `cmd.sell.*`, and
`cmd.buyback.*` wrappers if that fits the current frontend console pipeline.
The domain event should still exist so downstream systems, analytics, quests,
and triggers do not need to parse command text.

## Death Behavior

Killing a merchant NPC affects the merchant runtime, not the merchant profile.

Rules:

- Non-attackable merchants are rejected as combat targets.
- Attackable merchants enter normal combat.
- On death, live merchant runtime becomes inactive.
- Merchant stock does not become corpse inventory.
- Explicit `on_death.drops` or triggers can create loot.
- Respawn behavior follows normal spawn-plan rules.
- A newly spawned shopkeeper may create or resume a merchant runtime according
  to the profile and spawn-plan policy.

This gives builders the story flexibility of killable shopkeepers without
making every shopkeeper an accidental loot container.

## WR1 Boundary

The WR2 merchant system should not be implemented on top of WR1 merchant
inventory concepts.

Do not build new runtime behavior around:

- `MerchantInventory`
- WR1 `MobTemplate.merchant_inv`
- `merchant_profit`
- `RandomItemProfile`
- ordinary mob inventory as shop stock

The optional WR1 authored-world converter may emit clean WR2 manifests for
builders who choose to import old content:

- old merchant identity becomes a `mobdefinition`
- old merchant pricing becomes `MerchantProfile.pricing`
- old fixed stock becomes `item_definition` stock slots
- old random stock becomes `item_bundle` stock slots

That utility converts authored world content only. WR2 launches with an empty
database and does not migrate WR1 players, balances, inventories, or merchant
runtime state. It is import tooling, not a permanent compatibility layer.

## Validation Rules

- `MerchantProfile` slugs are unique per world.
- A stock slot must define exactly one source: `item_definition` or
  `item_bundle`.
- `count` must be greater than zero.
- `refresh` must be `fill_missing` or `reroll_on_restock`.
- `buyback.max_items` must be between 0 and 10.
- `settlement_currency` is required and must resolve to a currency in the base
  world's catalog.
- `funds.mode` must be `unlimited` or `finite`.
- Finite funds require `purchase_budget`.
- `purchase_budget` must be a non-negative safe integer.
- Every stock price and buyback value must use the profile's
  `settlement_currency`.
- `combat.attackable: false` means combat planning rejects the target.
- `merchant.availability: alive_and_present` requires an NPC presence with
  liveness state.

## Implementation Phases

### Phase 1: Authoring Shape

- Add `merchantprofile` manifest shape.
- Support direct `ItemDefinition` stock slots.
- Support `ItemBundle` stock slots.
- Attach merchant profiles to mob definitions.
- Add `combat.attackable` to mob definition authoring.

### Phase 2: Runtime Tables

- Add `MerchantRuntime`.
- Add `MerchantStockEntry`.
- Add `MerchantBuybackEntry`.
- Keep live stock out of ordinary mob inventory.

### Phase 3: Runtime Service

- Add merchant command handlers.
- Add merchant actions.
- Build a service that lists stock, buys, sells, and buys back items.
- Use `MerchantRuntime` and stock entries directly.

### Phase 4: Restock Scheduling

- Add scheduled `RestockMerchantAction`.
- Reset finite purchasing power at restock.
- Implement `fill_missing` and `reroll_on_restock`.
- Expire buyback entries on restock.

### Phase 5: Builder UX And Cleanup

- Add structured merchant profile UI.
- Add stock slot editor with direct item and item bundle selectors.
- Add buyback and funds controls.
- Add optional one-way conversion tooling for old merchant content.

## Open Questions

- Should buyback price always equal the price paid to the player, or should it
  use a configurable buyback markup?
- Should player-sold items optionally enter general resale stock after buyback
  expiration?
- Should finite funds later support a shared wallet where player purchases from
  the merchant increase purchasing power?
- Should restock avoid visible rerolls while players are in the room, or is
  deterministic action locking enough for the first pass?
