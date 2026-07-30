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
numbers are rejected. Quest and Trigger grants must be positive. Mob reward
manifests accept zero as an explicit absence and normalize it away, while stored
and runtime mob rewards remain positive.

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
reward mapping. Positive amounts create rewards; a zero amount explicitly
omits or removes that currency reward and is not stored or exported. Negative
amounts are invalid. Omit the entire `rewards` patch when an update should
preserve existing rewards.

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

## Trigger Currency Grants And Debits

A typed Trigger step can award its triggering player:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: grant_currency
        actor: trigger_actor
        currency: obol
        amount: 15
```

Use the symmetric action when the step should charge the player:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: debit_currency
        actor: trigger_actor
        currency: obol
        amount: 10
```

Both actions require exactly `actor: trigger_actor`, an explicit currency code,
and a positive integer amount no greater than
`9,007,199,254,740,991`. Only a player Trigger actor may be targeted.
Unlike a new money-bearing relational field, these stored actions never resolve
an omitted currency from the current default. The code is validated against the
base-world catalog when the Trigger manifest is applied and snapshotted to the
concrete currency id when the sequence starts.

Use an `actor.balances.<code>` condition when the action should be hidden or
show a builder-authored failure message to players who cannot afford it. That
condition is not the debit's concurrency guard: execution rechecks the wallet
under lock and rolls back the entire step if the starting balance cannot cover
the gross total of all same-currency debits. Same-step grants never subsidize
those charges. The final net balance after grants and debits must also remain
within the safe-integer limit.

On success, a grant tells the player `You receive 15 obols.` and a debit tells
them `You part with 10 obols.` Visible in-game witnesses in the player's current
room receive the corresponding third-person text. Witness text is suppressed
while the player is invisible or logged out, and the full wallet update remains
private.

Put item and mob mutations first as one uninterrupted prefix. After that,
`grant_currency`, `debit_currency`, `command`, `echo`, `send`, and
`send_except` may interleave in the authored narrative order you want. The
runtime applies all grants and debits as one signed wallet mutation, captures
approved commands and messaging events transactionally, and writes balance rows
last. A nonzero net change increments the wallet revision once and emits one
authoritative `currency.balances_changed` state event after every authored
action event. An exact net-zero batch still emits the authored grant/debit
narratives but changes no revision and emits no wallet-state event.

Step-safe commands do not branch on or mutate the wallet. `/transfer` can
nevertheless serialize the pre-mutation wallet inside its full player snapshot,
so the final aggregate wallet event deliberately follows authored action
output. Currency witness text uses the actor's room at that action's authored
position: a transfer before the action notifies the destination, while a
transfer after it leaves the text in the origin room. If any currency action,
command, or native messaging action fails, no balance, transactional command
effect, or success text commits.

## Starting Balances And Reset

`starting_balances` is an exact replacement mapping for the world's starting
policy. Zero entries are not stored and omitted codes start at zero. Editing
the mapping affects new characters and explicit character resets; it does not
change existing wallets immediately. A reset replaces the wallet with the
configured snapshot rather than adding another grant.

## Testing Existing Wallets

A builder can set one exact live balance without resetting the character:

```text
/setcurrency obol 100
/setcurrency player.123 guild-mark 25
```

The first form targets the builder. The second targets a player by key anywhere
in the same runtime world; an unambiguous player name in the builder's current
room also works. The amount is the desired final balance, may be zero, and does
not affect the player's other currencies. A real change increments the wallet
revision, sends the target a private update, and uses the wallet event reason
`builder.set_currency`. Setting the current value again is a no-op.

`/setcurrency` is direct-builder-only. It cannot be used by Triggers or other
scripts as a gameplay reward or charge; use authored reward effects or typed
Trigger `grant_currency` and `debit_currency` actions for those cases.

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
balance, an active Trigger sequence, or a canonical structured reference in
quests, Trigger conditions and steps, room actions, crafting recipe conditions,
mob definitions, spawn plans, or abilities. The builder response lists
registered usage blockers. Select another default and remove references before
deleting.

Currency authoring is also blocked while an ordinary spawn or instance run for
the base world is running. Stop those worlds before changing the catalog,
default, starting policy, or display fields.

## Optional WR1 World Conversion

WR2 starts with an empty database. There is no account, player, balance,
inventory, or runtime-state migration from WR1. The separate optional WR1
utility can convert authored world content into these canonical manifests for
builder review and import into a fresh WR2 world.
