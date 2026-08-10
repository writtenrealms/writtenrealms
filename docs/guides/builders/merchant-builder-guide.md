# Merchant Builder Guide

This guide explains the WR2 merchant model at a builder level.

Merchants are built from reusable shop configuration plus an optional physical
presence:

- `merchantprofile`: the shop configuration, including stock, prices, restock,
  buyback, and funds.
- `room`: the dependable, fixed-location provider. Attach a profile directly
  when the shop should always be available there.
- `mobdefinition`: an optional presence-controlled provider. Use it when a
  spawned NPC leaving or dying should close the shop.

Merchant stock is not mob inventory. Killing a shopkeeper does not drop the shop
stock unless you explicitly author drops or death triggers.

The examples use a previously defined `obol` currency. Replace that code with
the currency defined by your world. See
[currency-builder-guide.md](currency-builder-guide.md).

## Builder UI

World Config links to the world's Merchant Profiles. The list page shows the
authored shop configurations for that world and can filter them by funds mode
or buyback availability. `Add` opens manifest import with starter Merchant
Profile YAML.

Selecting a profile opens its canonical YAML in an inline editor. Save changes
there or copy the current YAML for reuse. To remove the profile, copy the
separate delete manifest, replace the editor contents with it, and save. The
summary above the editor shows its currency, funds, pricing, restock schedule,
buyback policy, and stock slots.

Runtime instances inherit Merchant Profiles and show them read-only. Follow the
source-world link to change the authored profile rather than trying to edit an
instance.

Room Config includes a **Shop** service selector. Choose a Merchant Profile and
save to make it available in that room without a mob or Spawn Plan. The room's
canonical YAML records the same attachment.

## How It Works

Create the items first:

- Use `itemdefinition` for fixed stock.
- Use `itembundle` when a stock slot should roll from a weighted set of item
  definitions.

Then create a `merchantprofile`:

- `settlement_currency`: the one authored currency used by this shop.
- `pricing.sell_markup`: multiplier applied when players buy from the merchant.
- `pricing.buy_multiplier`: multiplier applied when the merchant buys from
  players.
- `restock.interval_seconds`: how often stock and finite purchase budget reset.
- `funds.mode`: `unlimited` or `finite`.
- `buyback.max_items`: maximum recently sold items held for buyback, capped at
  10.
- `stock`: fixed item-definition slots or item-bundle slots.

For a fixed-location shop, attach the profile in Room Config or room YAML:

```yaml
kind: room
metadata:
  ref: room@42
  name: Garron's Smithy
spec:
  merchant:
    profile: merchantprofile.garron-smithy
```

This automatically exposes the room's **List** and **Offer** actions. A
decorative mob can still be placed in the room without a Merchant Profile.

When NPC presence should control availability, leave the room attachment blank
and attach the profile to a `mobdefinition` instead:

```yaml
merchant:
  profile: merchantprofile.garron-smithy
  availability: alive_and_present
combat:
  attackable: false
```

Use `combat.attackable: true` for a killable shopkeeper.

Use only one attachment for an ordinary shop. Attaching a profile to the room
and to a local merchant mob creates multiple providers and requires players to
name the one they intend.

## Player Commands

Players use:

- `list`, `shop`, or bare `buy` to view numbered stock
- `offer` or bare `sell` to view numbered inventory the shop can buy
- `buy <number-or-item>`, `sell <number-or-item>`, and `buyback` when exactly
  one shop is present
- `shop <merchant>` or `list <merchant>`
- `offer <merchant>`
- `buy <number-or-item> from <merchant>`
- `sell <number-or-item> to <merchant>`
- `buyback <merchant>`
- `buyback <item> from <merchant>`

Numbers refer to the most recent `list` or `offer` for that shop and remain
usable for ten minutes. If an item is bought, moved, or the list expires, the
player must list again rather than having the number shift to another item.

## Fixed Inventory Example

This merchant always tries to keep two iron swords and five repair kits in
stock.

```yaml
kind: itemdefinition
metadata:
  slug: iron-sword
  name: an iron sword
spec:
  type: equippable
  keywords: iron sword blade
  cost: 100
  currency: obol
  equipment_type: weapon_1h
  weapon_damage: 8
---
kind: itemdefinition
metadata:
  slug: repair-kit
  name: a repair kit
spec:
  type: inert
  keywords: repair kit tools
  cost: 25
  currency: obol
---
kind: merchantprofile
metadata:
  slug: garron-smithy
  name: Garron's Smithy
spec:
  settlement_currency: obol
  pricing:
    sell_markup: 1.2
    buy_multiplier: 0.4
  restock:
    interval_seconds: 10800
  funds:
    mode: unlimited
  buyback:
    enabled: true
    max_items: 10
  stock:
    - key: iron-swords
      item_definition: itemdefinition.iron-sword
      count: 2
    - key: repair-kits
      item_definition: itemdefinition.repair-kit
      count: 5
---
kind: mobdefinition
metadata:
  slug: garron-blacksmith
  name: Garron
spec:
  room_description: Garron the Blacksmith works beside a smoking forge.
  keywords: garron blacksmith smith merchant
  combat:
    attackable: false
  merchant:
    profile: merchantprofile.garron-smithy
    availability: alive_and_present
```

Garron appears as an NPC in the room, but `kill garron` is rejected because
`combat.attackable` is false.

## Variable Inventory Example

This merchant keeps three rotating curios in stock. Each restock rerolls the
curios from an item bundle.

```yaml
kind: itemdefinition
metadata:
  slug: lucky-charm
  name: a lucky charm
spec:
  type: inert
  keywords: lucky charm curio
  cost: 40
  currency: obol
---
kind: itemdefinition
metadata:
  slug: cracked-orb
  name: a cracked glass orb
spec:
  type: inert
  keywords: cracked glass orb curio
  cost: 60
  currency: obol
---
kind: itembundle
metadata:
  slug: roadside-curios
  name: Roadside Curios
spec:
  entries:
    - item_definition: itemdefinition.lucky-charm
      weight: 3
    - item_definition: itemdefinition.cracked-orb
      weight: 1
---
kind: merchantprofile
metadata:
  slug: mira-curio-cart
  name: Mira's Curio Cart
spec:
  settlement_currency: obol
  pricing:
    sell_markup: 1.5
    buy_multiplier: 0.25
  restock:
    interval_seconds: 3600
  funds:
    mode: finite
    purchase_budget: 500
  buyback:
    enabled: true
    max_items: 5
  stock:
    - key: rotating-curios
      item_bundle: itembundle.roadside-curios
      count: 3
      refresh: reroll_on_restock
---
kind: mobdefinition
metadata:
  slug: mira-curio-seller
  name: Mira
spec:
  room_description: Mira tends a cart of small oddities.
  keywords: mira curio merchant seller
  combat:
    attackable: true
    health: 80
    attack_power: 6
    fights_back: true
  merchant:
    profile: merchantprofile.mira-curio-cart
    availability: alive_and_present
```

Mira is a killable shopkeeper because `combat.attackable` is true. If Mira dies,
the shop closes. The rotating curio stock still does not become corpse loot.

## Funds And Buyback

Use unlimited funds for most simple shops:

```yaml
funds:
  mode: unlimited
```

Use finite funds when the merchant should only buy so much from players between
restocks:

```yaml
settlement_currency: obol
funds:
  mode: finite
  purchase_budget: 500
```

`settlement_currency` is a top-level profile field, not a child of `funds`.
It defaults to the world default when a new profile omits it, then remains a
concrete stored reference. Finite purchase budget uses that currency and resets
at restock. Player purchases from the merchant do not increase that budget.

Buyback is per player:

```yaml
buyback:
  enabled: true
  max_items: 10
```

`max_items` can be 0 through 10. When the list is full, the oldest buyback item
expires. Buyback entries also expire on restock.
