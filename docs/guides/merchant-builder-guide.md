# Merchant Builder Guide

This guide explains the WR2 merchant model at a builder level.

Merchants are built from two clean WR2 concepts:

- `merchantprofile`: the shop configuration, including stock, prices, restock,
  buyback, and funds.
- `mobdefinition`: the NPC presence in the room. It can point at a merchant
  profile and can be either attackable or not attackable.

Merchant stock is not mob inventory. Killing a shopkeeper does not drop the shop
stock unless you explicitly author drops or death triggers.

## How It Works

Create the items first:

- Use `itemdefinition` for fixed stock.
- Use `itembundle` when a stock slot should roll from a weighted set of item
  definitions.

Then create a `merchantprofile`:

- `pricing.sell_markup`: multiplier applied when players buy from the merchant.
- `pricing.buy_multiplier`: multiplier applied when the merchant buys from
  players.
- `restock.interval_seconds`: how often stock and finite purchase budget reset.
- `funds.mode`: `unlimited` or `finite`.
- `buyback.max_items`: maximum recently sold items held for buyback, capped at
  10.
- `stock`: fixed item-definition slots or item-bundle slots.

Finally, attach the profile to a `mobdefinition`:

```yaml
merchant:
  profile: merchantprofile.garron-smithy
  availability: alive_and_present
combat:
  attackable: false
```

Use `combat.attackable: true` for a killable shopkeeper.

## Player Commands

Players use:

- `shop <merchant>` or `list <merchant>`
- `buy <item> from <merchant>`
- `sell <item> to <merchant>`
- `buyback <merchant>`
- `buyback <item> from <merchant>`

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
  currency: gold
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
  currency: gold
---
kind: merchantprofile
metadata:
  slug: garron-smithy
  name: Garron's Smithy
spec:
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
  currency: gold
---
kind: itemdefinition
metadata:
  slug: cracked-orb
  name: a cracked glass orb
spec:
  type: inert
  keywords: cracked glass orb curio
  cost: 60
  currency: gold
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
  pricing:
    sell_markup: 1.5
    buy_multiplier: 0.25
  restock:
    interval_seconds: 3600
  funds:
    mode: finite
    currency: gold
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
funds:
  mode: finite
  currency: gold
  purchase_budget: 500
```

Finite purchase budget resets at restock. Player purchases from the merchant do
not increase that budget.

Buyback is per player:

```yaml
buyback:
  enabled: true
  max_items: 10
```

`max_items` can be 0 through 10. When the list is full, the oldest buyback item
expires. Buyback entries also expire on restock.
