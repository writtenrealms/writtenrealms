# Currency Builder Guide

Every WR2 world owns its currency catalog. A catalog can contain one currency
or many, and any one of them can be the default. Gold and Medals are not
required or special.

The builder model is:

1. Define each currency.
2. Select one default currency on the world.
3. Put an amount and concrete currency code on prices, rewards, and costs.

## Minimal One-Currency World

Phalanx can use Obol and nothing else:

```yaml
kind: currency
metadata:
  code: obol
spec:
  name: Obol
  plural_name: Obols
  description: The common coin of Phalanx.
---
kind: world
spec:
  default_currency: obol
  starting_balances: {}
```

An empty `starting_balances` mapping means new and reset characters start with
zero of every currency. To start them with 12 Obols instead:

```yaml
kind: world
spec:
  starting_balances:
    obol: 12
```

World creation also accepts `initial_currency_code`, `initial_currency_name`,
and `initial_currency_plural_name`. Set those to `obol`, `Obol`, and `Obols`
when creating Phalanx so the new world starts with Obol rather than the
creation form's Gold convenience defaults.

## Currency Definitions

```yaml
kind: currency
metadata:
  code: guild-mark
spec:
  name: Guild Mark
  plural_name: Guild Marks
  description: Issued by the adventurers' guild.
```

Codes:

- are lowercase portable identifiers
- must match `[a-z][a-z0-9_-]{0,63}`
- are unique ignoring case within the world
- cannot be changed after creation

Edit `name`, `plural_name`, or `description` to change presentation without
changing identity. If `plural_name` is blank, WR2 uses `name` for every amount.

The first currency created for a defaultless world becomes its default. For
normal edits, select the default explicitly in the world manifest:

```yaml
kind: world
spec:
  default_currency: guild-mark
```

Changing the default affects future authoring that omits a currency. It does
not convert player balances or retarget existing item prices, recipe fees,
merchant profiles, rewards, or policies.

## Amount Rules

All canonical money amounts are whole numbers from `0` through
`9,007,199,254,740,991`. Negative values, fractions, booleans, and larger
numbers are rejected. Quest and mob grants must be positive where a zero reward
would have no meaning.

## Item Values

Put `cost` and `currency` next to one another:

```yaml
kind: itemdefinition
metadata:
  slug: bronze-spear
  name: a bronze spear
spec:
  type: equippable
  equipment_type: weapon_1h
  cost: 40
  currency: obol
```

On a new item, an omitted `currency` resolves the current default and stores
that concrete currency. Canonical exports always include the code. On update,
omitting both fields preserves the existing value; setting `currency` without
`cost` is invalid. Use `cost: null` to remove monetary value.

## Crafting Recipe Fees

A recipe may charge currency in addition to its material inputs:

```yaml
kind: craftingrecipe
metadata:
  slug: reinforced-helm
spec:
  cost: 150
  currency: obol
  output:
    item_definition: itemdefinition.reinforced-helm
  inputs:
    - material: craftmaterial.bronze
      quantity: 8
```

The money pair follows the same amount rules as an item value. When a cost is
first added, omitting `currency` resolves and stores the world's current
default. Canonical exports include both fields. On update, omitting both fields
preserves the existing fee, and `cost: null` clears both fields. A currency
without a cost, an unknown or cross-world currency, and an invalid amount are
rejected. A cost without a currency is also rejected if the world has no
default to resolve.

The player must have both the authored material inputs and the complete fee.
Crafting debits the wallet in the same transaction that consumes materials and
creates the item, so a failed craft cannot keep a partial payment.

## Merchants

One merchant profile trades in one settlement currency:

```yaml
kind: merchantprofile
metadata:
  slug: agora-smith
  name: Agora Smith
spec:
  settlement_currency: obol
  pricing:
    sell_markup: 1.2
    buy_multiplier: 0.4
  funds:
    mode: finite
    purchase_budget: 500
  buyback:
    enabled: true
    max_items: 10
  stock:
    - key: spears
      item_definition: itemdefinition.bronze-spear
      count: 2
```

`settlement_currency` defaults to the world default when a profile is first
created, then remains explicit. `purchase_budget` is denominated in that same
currency. Stock items with a monetary value must use the profile's settlement
currency.

## Mob Rewards

Mobs can grant one or more currencies:

```yaml
kind: mobdefinition
metadata:
  slug: persian-raider
  name: a Persian raider
spec:
  rewards:
    currencies:
      obol: 38
```

Applying `rewards.currencies` replaces the mob definition's complete currency
reward mapping. Omit the entire `rewards` patch when an update should preserve
existing rewards.

## Quest Rewards

Quest effects use `grant_currency` with an explicit code:

```yaml
effects:
  - type: grant_currency
    currency: obol
    amount: 20
```

`grant_gold` is not a WR2 compatibility alias. The optional WR1 authored-world
converter rewrites representable WR1 rewards before their manifests reach WR2.

## Death And System Costs

To charge a fraction of one balance on a non-PvP death:

```yaml
kind: world
spec:
  death_mode: lose_currency
  death_currency: obol
  death_currency_penalty: 0.2
```

`death_currency_penalty` is a fraction from `0` through `1`. A world using
another death mode can still retain a configured death currency for a later
policy change. `clan_registration_currency` similarly identifies the currency
for a nonzero `clan_registration_cost`.

## Conditions

Use the existing structured condition framework and a balance path:

```yaml
conditions:
  gte:
    - actor.balances.obol
    - 10
```

A missing sparse balance row evaluates as zero. Do not create currency-specific
condition syntax.

## Starting Balances And Reset

`starting_balances` is an exact replacement mapping for the world's starting
policy. Zero entries are not stored and omitted codes start at zero. Editing
the mapping affects new characters and explicit character resets; it does not
change existing wallets immediately. A reset replaces the wallet with the
configured snapshot rather than adding another grant.

## Instances

Currency definitions, the default, starting balances, and player wallets belong
to the base world. Instance templates and runs inherit them and cannot define
an instance-local catalog or default. Local instance rewards and costs still
resolve their codes against the base-world catalog.

## Delete A Currency

```yaml
kind: currency
operation: delete
metadata:
  code: guild-mark
```

Deletion is blocked while the currency is the default or is referenced by
starting balances, items, crafting-recipe fees, merchants, mob rewards,
death/clan policies, runtime merchant/item/mob snapshots, a nonzero player
balance, or a canonical structured reference in quests, triggers, room
actions, crafting recipe conditions, mob definitions, spawn plans, or
abilities. The builder response lists registered usage blockers. Select
another default and remove references before deleting.

Currency authoring is also blocked while an ordinary spawn or instance run for
the base world is running. Stop those worlds before changing the catalog,
default, starting policy, or display fields.

## Optional WR1 World Conversion

WR2 starts with an empty database. There is no account, player, balance,
inventory, or runtime-state migration from WR1. The separate optional WR1
utility can convert authored world content into these canonical manifests for
builder review and import into a fresh WR2 world.
