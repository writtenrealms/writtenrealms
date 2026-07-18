# WR2 Currency System

Status as of 2026-07-17: implemented WR2 currency foundation. Authored currency
definitions, a customizable world default, relational player balances,
transactional wallet mutations, manifests, runtime payloads, items, merchants,
mob rewards, quest rewards, death costs, conditions, and the player
`currencies` command use the generic system. Generic action receipts,
player-to-player transfers, exchange rates, and the other items called out as
deferred below are not implemented.

## Purpose

WR2 lets each world define its own money, choose any one of those
currencies as the default, and use every currency consistently across player
balances, items, merchants, rewards, costs, conditions, events, and frontend
presentation.

The builder-facing model should remain small:

1. Define the currencies used by the world.
2. Choose one default currency.
3. Give every persistent price, reward, or cost an amount and a concrete
   currency. Create surfaces may preselect/materialize the current default;
   canonical storage and exports remain explicit. Quest effects require the
   code directly.

Gold remains the world-creation API's convenience default only when the creator
does not supply initial currency fields. It is seed data rather than an engine
invariant. A creator can instead supply, for example, `obol` / `Obol` / `Obols`
and produce a world whose only currency is Obol. Such a world has no `gold`
definition or player field in its canonical runtime path.

Related documents:

- [merchant-system.md](merchant-system.md)
- [crafting-system.md](crafting-system.md)
- [instance-system.md](instance-system.md)
- [quest-system-endstate.md](quest-system-endstate.md)
- [yaml-manifest-system.md](yaml-manifest-system.md)
- [scoped-state-system.md](scoped-state-system.md)
- `.codex/skills/wr-transition/wr2-architecture.md`

## Decision Summary

The target design separates four concepts:

- `Currency`: one authored, base-world-owned currency definition.
- world default currency: the one currency selected when authoring or a command
  does not specify another.
- `PlayerCurrencyBalance`: one player's canonical balance in one currency.
- currency service: the only runtime path that reads or mutates spendable
  currency balances.

Important decisions:

- One centrally defined `economy_world(world)` resolver identifies the root
  authored world that owns an economy.
- The economy world owns currency definitions and one default-currency
  reference. Draft/importing worlds may briefly have none; playable worlds may
  not.
- Instance templates and instance runs inherit that catalog and default. They
  never clone it or own overrides.
- Currency codes are immutable portable identifiers; display names are editable.
- Player balances are normalized relational rows, not JSON serialized on the
  player and not special `gold` or `medals` columns.
- Missing balance rows mean zero. Amounts are nonnegative integers.
- Every wallet batch is transactional, locks the Player before its balance
  rows, rejects cross-world or out-of-range results, and increments one
  `wallet_revision` for a nonempty successful batch.
- Mutations emit one private `currency.balances_changed` outbox event by
  default. Initialization and reset deliberately suppress that event while
  still updating the wallet revision when a balance actually changes.
- Generic replay receipts are not yet implemented, so callers must not describe
  arbitrary wallet mutations as idempotent or retry-safe.
- Command planning resolves a currency code or the default to a concrete
  currency ID before queuing work.
- Changing the world default never converts existing balances or silently
  rewrites existing prices and rewards.
- Crafting materials, faction standing, Glory, combat resources, and arbitrary
  scoped state are not currencies merely because they are numeric.
- The first version has no exchange rates, fractional precision, denominations,
  debt, bank accounts, or simulated merchant wallets.

## Economy-World Ownership

Currency code resolution must have one canonical ownership rule. Introduce an
`economy_world(world)` helper with the same root-world result currently sought
by helpers such as `definition_world()` and `inherited_system_world()`:

- a root authored world resolves to itself
- a spawned ordinary world resolves through `context`
- an instance template resolves through `instance_of`
- a spawned instance resolves through `context.instance_of`

Currency code must not scatter variants of
`world.context.instance_of or world` through features. The shared resolver is
used by authoring, manifests, runtime actions, conditions, and payloads.

A player wallet may contain only currencies owned by the player's economy
world. Moving between the base world and its instances does not change the
wallet. Moving a player to a different economy world is invalid unless an
explicit cross-world transfer policy defines how every balance is handled;
ordinary room or instance transfer code must not perform that conversion.

## Legacy Baseline Replaced By This Implementation

Before this implementation, `Currency` was only a partial authoring concept and
the runtime still privileged Gold and Medals. The following subsections record
the problems the new model replaces; they are historical context, not a
description of the current WR2 contract.

### Three Player Balance Stores

Player money was split across:

- the inherited `gold` integer field
- the `medals` integer field
- a serialized `currencies` text field for other codes

Those fields are removed from the WR2 Player model. Spendable balances now live
only in `PlayerCurrencyBalance` and are changed through `spawns.wallet`.

### Default Currency Is Only A Partial Authoring Hint

The former `Currency.is_default` flag and Gold-specific paths were replaced by
`World.default_currency` and explicit currency references. The affected paths
included:

- starting money
- mob kill rewards
- death repair costs
- quest rewards
- clan registration
- merchant purchases, sales, and buyback
- player conditions
- inventory and stats presentation

One nullable world pointer makes multiple defaults unrepresentable. A world may
be defaultless only during bootstrap or repair; normal world creation installs
its chosen initial currency as the default.

### Currency Identity Is Inconsistent

Legacy code variously identified a currency by:

- database ID
- code string
- `Currency` foreign key
- special field name such as `gold` or `medals`
- a missing value that is later interpreted as Gold

Canonical authored references use stable codes and canonical relational runtime
references use foreign keys. Codes are normalized lowercase and immutable.

### Builder Write Paths Disagree

Currency CRUD, default selection, starting-balance replacement, and manifest
ingestion use the shared builder currency services for base-world ownership,
lifecycle checks, identity rules, and deletion protection. The REST serializer
exposes `is_default` as derived read state and accepts a per-currency
`starting_amount`; the authoritative default remains the world pointer.

### Performance And Correctness Limits

A serialized balance map was a poor canonical store for a frequently mutated
resource:

- one currency change rewrites a larger player value
- individual balances cannot be constrained or indexed
- concurrent read/modify/write paths can lose updates
- analytics and administrative queries require deserialization
- malformed values are discovered at runtime
- code frequently needs special cases for Gold and Medals

Default and code resolution can also introduce repeated currency queries while
spawning or serializing many entities. WR2 needs bounded, preloadable access
whose cost depends on currencies touched by one action, not on player count or
world size.

## Goals

- Let a builder use any authored currency as the world default.
- Let a world operate with no Gold or Medals definitions.
- Give builders a short, consistent manifest and UI workflow.
- Make prices, rewards, costs, balances, conditions, and messages use the same
  currency identities.
- Prevent negative balances and concurrent overspending.
- Make retries incapable of paying or charging twice.
- Preserve deterministic Command -> Action -> Event execution.
- Keep currency definitions and persistent player balances inherited across
  instances.
- Keep query counts bounded and avoid per-player or per-world fan-out when a
  definition or default changes.
- Keep the canonical manifest contract simple enough for the optional WR1
  authored-world conversion utility to target directly.

## Non-Goals

The initial generic currency system will not provide:

- exchange rates or automatic conversion
- copper/silver/gold denomination arithmetic
- fractional balances or arbitrary decimal precision
- debt, overdrafts, interest, loans, or bank accounts
- physical coin item stacks as the canonical wallet
- global auction-house accounting
- full merchant cash-flow simulation
- NPC wallets or arbitrary generic-content-type wallet owners
- automatic conversion when a builder changes the default currency
- a second condition language for money
- treating crafting materials or faction standing as spendable money

If a later feature needs one of these, it should build explicitly on the
currency service rather than expanding the first version speculatively.

## Builder Mental Model

Builders should not need to understand balance rows, row locks, or action
receipts.

The complete ordinary workflow is:

1. World creation accepts an initial code, singular name, and plural name. If
   omitted, those fields default to Gold for convenience; they are not fixed.
2. The builder may add more currencies.
3. The builder selects exactly one default.
4. Money fields show an amount and a currency selector, with the default
   preselected.
5. The builder may change display names at any time.
6. A code cannot be casually renamed after creation because it is a portable
   authored identifier.
7. A currency cannot be deleted while it is the default, referenced by authored
   content, held by a player, or needed by queued/retryable work.

The default answers only: "Which currency should WR2 select when none was
specified?" It does not mean "reinterpret every existing number as this new
currency."

## Authored Currency Model

### `Currency`

Conceptual fields:

| Field | Purpose |
| --- | --- |
| `world` | Base world that owns the definition. |
| `code` | Immutable portable identifier used by manifests and payloads. |
| `name` | Singular/display name, such as `Crown` or `Gold`. |
| `plural_name` | Optional plural, such as `Crowns`; blank reuses `name`. |
| `description` | Optional builder-facing explanation. |

Implemented constraints:

- `code` is normalized lowercase and bounded, for example by
  `[a-z][a-z0-9_-]{0,63}`.
- Code uniqueness is case-insensitive within the base world.
- `name` is required and nonblank.
- Currency definitions can only belong to base worlds.
- A code is immutable through ordinary update endpoints and manifests.

Do not infer singular names by trimming a trailing `s`. `Gold` is commonly the
same in singular and plural, while many currencies have irregular or authored
names. A shared formatter selects `name` for one and `plural_name` for other
amounts, falling back to `name` when no plural was authored.

Symbols, prefix/suffix formatting, and decimal precision are intentionally
deferred. They add localization and arithmetic policy that a text-first integer
currency system does not yet need.

### Base-World Default Currency

The root/base `World` row owns one nullable `default_currency` foreign key to
`Currency`. It does not live on shared/cloned `WorldConfig`.
The obsolete `Currency.is_default` field has been removed.

Required invariants:

- The selected currency belongs to that base world.
- A draft being created or imported may temporarily have no selection.
- Every playable base world has exactly one selected default. A single pointer
  makes multiple defaults unrepresentable.
- The selected definition cannot be deleted.
- Spawned worlds and instance templates/runs resolve the default through their
  base world rather than owning copies.

World construction may need a short bootstrap interval before a currency row
exists. Creation should occur in one transaction and set the default before the
world becomes usable. A defaultless draft permits only recovery operations:
create a currency, select a default, or apply an atomic import that will provide
one. Dependent authoring, canonical export, publication, and world start remain
blocked until repaired.

Because WR2 launches with an empty database, the implementation replaced
`Currency.is_default` and its readers directly. There is no production
compatibility mirror or data backfill.

An instance template or spawned world cannot set the pointer. The authoring
service also checks that the selected `Currency.world_id` equals the
`economy_world.id`; a foreign key alone cannot enforce that cross-row rule.

### Default Resolution Semantics

The default is resolved to a concrete currency as early as practical.

For authored content:

- On create, an omitted currency selects the current default and persists that
  concrete relation.
- On a minimal update, omission preserves the existing relation; it does not
  retarget the field to the current default.
- To change an existing value, the builder supplies another explicit code.
- Canonical exports always emit the concrete code so imports do not depend on
  a target world's current default.

For runtime work:

- Command parsing may allow currency omission for a convenient command.
- Planning resolves omission or code to a concrete currency ID.
- The queued Action carries the economy-world ID, currency ID, and amount.
- Execution never asks "what is the default now?"

That convenience applies only to a command entered and planned now. Persisted
trigger scripts, aliases, quest text commands, or other stored command strings
must name a currency explicitly if they can move money. A structured authoring
surface may accept omission only when ingestion materializes the then-current
default into canonical stored content. Stored free-form commands are never
allowed to change economic meaning merely because the default later changes,
and their currency references participate in usage/deletion checks.

If a builder changes the default after an Action was planned, the already
planned Action retains its original currency. This is deterministic and avoids
race-dependent economic behavior.

Changing the default affects:

- currency selectors for newly authored money values
- future creates that omit currency
- future ad hoc commands whose currency argument is optional
- display ordering that places the default first

It does not affect:

- existing player balances
- already-authored explicit prices, rewards, costs, or penalties
- already-spawned item value snapshots
- already-queued Actions

A future builder tool may preview and deliberately retarget selected authored
references or convert balances. That must be an explicit operation with a
defined rate, never a side effect of changing the default pointer.

### Currency References

Canonical manifests use the stable bare code in fields already typed as a
currency reference.

Examples:

```yaml
currency: crowns
```

```yaml
default_currency: crowns
```

Database IDs may be accepted by internal APIs for interoperability, but exports
must not depend on database IDs. Any accepted reference must resolve within the
selected economy world's catalog. Canonical WR2 manifests use bare codes; the
optional WR1 conversion utility must emit that form rather than requiring
legacy aliases in the WR2 importer.

An individual `Money` value has paired presence semantics:

- amount present, including zero: currency must resolve and be stored
- no monetary value: both amount and currency are absent under an explicitly
  nullable feature contract
- amount without currency: create resolves the default; canonical storage and
  export are still explicit
- currency without amount: invalid

Null currency by itself never means Gold or the current default.
Standalone currency selectors such as the world default, a merchant settlement
denomination, or a death-policy denomination are references rather than
individual `Money` values and do not require an adjacent amount.

### Rename, Retire, And Delete Policy

Display names and descriptions are ordinary edits. Codes are identity and are
not ordinary edits.

The implemented deletion service blocks the world default and registered
relational use by starting balances, item definitions, merchant profiles, mob
rewards, death/clan policies, runtime items/merchant rows, and nonzero player
balances. It may prune zero player rows after those checks pass. Database
`RESTRICT` constraints remain the final guard for relational references.

The same bounded usage registry scans canonical structured authoring fields once
per catalog load: quests, quest arcs, triggers, room actions, crafting recipes,
mob definitions, spawn plans and entries, and abilities. It also checks stopped
runtime mob reward snapshots. This keeps deletion safe for both relational
references and portable `actor.balances.<code>`/typed currency references
without repeating the audit once per currency on the builder list screen.

Foreign keys for canonical monetary references should use `RESTRICT`, not
`SET_NULL` followed by an implicit Gold or current-default fallback. `RESTRICT`
preserves references during ordinary deletion while still permitting a full
owning-world cascade to remove co-owned rows together.

Any future code-retargeting operation or forced deletion requires a
stopped/draft world,
drained pending Actions, expired replay receipts, and a complete reference
audit. Existing references and nonzero balances are never retargeted
automatically.

If worlds later need to stop issuing a currency while preserving old balances,
add an explicit retirement state. Do not overload deletion or a display-name
change with retirement semantics in the first implementation.

## Manifest Contract

### Currency Definitions

Example definitions:

```yaml
kind: currency
metadata:
  code: crowns
spec:
  name: Crown
  plural_name: Crowns
  description: The ordinary coin of the realm.
---
kind: currency
metadata:
  code: guild-marks
spec:
  name: Guild Mark
  plural_name: Guild Marks
```

`metadata.code` is both create identity and update identity. It is omitted from
ordinary editable fields because changing it means creating or retargeting an
identity, not renaming a label.

`spec.is_default` is not part of the target currency manifest. The default is a
world-level relationship.

### World Economy Settings

Example:

```yaml
kind: world
spec:
  default_currency: crowns
  starting_balances:
    crowns: 25
    guild-marks: 0
```

`starting_balances` is a mapping from currency code to nonnegative integer.
Omitted currencies start at zero. Canonical export may omit zero entries.

Canonical storage is relational rather than another JSON wallet-shaped field.
`WorldStartingCurrencyBalance` has unique `(world, currency)` rows and amounts
from zero through `9,007,199,254,740,991`. That cap is JavaScript's maximum safe
integer (`2^53 - 1`), so values round-trip exactly through JSON and the
frontend. The authoring service enforces that references resolve to the same
economy world. This storage detail remains invisible behind the simple manifest
mapping and currency-screen starting amount fields.

The default currency does not need to appear in `starting_balances`; a world may
intentionally start players with zero money.

### Delete

Example:

```yaml
kind: currency
operation: delete
metadata:
  code: guild-marks
```

Deletion must return useful blocking references rather than a generic database
constraint failure.

### Validation And Application

Currency manifest validation should reject:

- unknown fields
- invalid or case-colliding codes
- blank names
- instance-owned definitions
- cross-world references
- an update that attempts to change a code
- deletion of the default or a referenced currency
- negative, fractional, boolean, or nonnumeric money amounts
- world config that selects an unknown or foreign default
- obsolete WR1/early-WR2 fields such as currency `is_default` or
  `starting_gold`; the external conversion utility must emit target fields

Multi-document world ingestion applies the bundle atomically in document order.
Currency definitions must therefore appear before the world document or
dependent item, merchant, mob, and quest documents that reference them. Full
exports place currency definitions before dependent content and world economy
settings.

The normal builder UI, REST endpoints, and manifest ingestion must call the
same currency-authoring service for permissions, base-world resolution,
lifecycle checks, identity rules, and deletion protection.

WR2 ingestion accepts the target contract, not WR1 database shapes or legacy
manifest aliases. Conversion and ambiguity reporting belong in the optional
WR1 utility before the manifest reaches WR2.

## Runtime Balance Model

### `PlayerCurrencyBalance`

Conceptual fields:

| Field | Purpose |
| --- | --- |
| `player` | Player who owns the balance. |
| `currency` | Base-world currency definition. |
| `amount` | Current nonnegative integer balance. |

Required constraints:

- unique `(player, currency)`
- `amount >= 0`
- `amount` is a signed database `BIGINT` constrained to the inclusive range
  `0..9,007,199,254,740,991`; all arithmetic checks the JavaScript-safe cap
  before persistence
- the player's economy world equals the currency's owning world, enforced by
  the currency service because a simple foreign key cannot express it

The unique constraint also supplies the common indexed lookup by player and
currency. Add other indexes only for a demonstrated query such as a currency
leaderboard; do not burden every write speculatively.

A missing row means zero. The hot mutation path does not need to delete rows
when they reach zero. Retaining an existing zero row avoids create/delete churn
and simplifies locking; payloads may omit nondefault zero balances. An explicit
currency-deletion workflow may prune zero rows after proving no nonzero balance
or reference remains.

Balance rows are persistent player/global state. Entering an instance changes
neither their ownership nor currency catalog. Starting balance edits are not
retroactive and reconnect, runtime-world spawn, and instance entry never apply
them again.

### Why Balances Are Relational

Normalized rows provide:

- database nonnegative and uniqueness guarantees
- row-level locking of the balances touched by an Action
- atomic conditional debits
- indexed administrative and analytics queries
- bounded prefetching for player payloads
- direct foreign-key integrity to authored currencies
- administrative and audit visibility without JSON parsing

They also match the WR2 rule that canonical state is relational and derived
runtime data may be cached or serialized only when rebuildable.

### No Generic Wallet Owner In The Initial Version

The first implementation models player balances directly rather than
introducing a generic foreign-key wallet for players, mobs, clans, merchants,
rooms, and arbitrary future entities.

Current merchant finite funds are a restock budget, not a real cash account.
Mobs grant authored rewards but do not need wallets. If a later feature needs a
clan treasury or NPC cash inventory, add a typed owner model or dedicated
balance table with explicit integrity rather than weakening the player model in
advance.

## Currency Service

All runtime mutations go through one domain service. Snapshot/condition readers
may use bounded prefetched/query helpers, but feature code must not update
`PlayerCurrencyBalance.amount` directly.

The implemented service in `backend/spawns/wallet.py` exposes:

- `mutate_balances(player, deltas, reason, emit_event=True)` for one atomic
  signed-delta batch on one player
- `replace_balances(player, amounts, reason, emit_event=True)` for an exact
  snapshot replacement; omitted existing currencies become zero
- `balance_map(player, include_zero=True)` for a code-keyed snapshot; missing
  catalog rows are represented as zero when requested

Dedicated transfer and durable-operation APIs remain deferred.

### Mutation Contract

Every implemented mutation supplies one player, one or more concrete Currency
objects or IDs with integer deltas, a stable reason such as
`merchant.purchase`, `quest.reward`, or `mob.kill`, and whether to emit the
private balance event. Duplicate currency deltas in an iterable are aggregated
before locking.

The returned `WalletMutation` contains the locked Player, the resulting wallet
revision, and an ordered tuple of changes. Each change contains the Currency,
signed delta, balance before, and balance after.

Debits fail atomically when funds are insufficient. No partial batch is
committed. `mutate_balances` rejects boolean, fractional, malformed,
cross-economy, negative-result, and greater-than-safe-integer deltas.
`replace_balances` is an internal trusted-snapshot API; callers must pass
already-validated integers (authoring and manifest paths do so).

Before locking balances, the service aggregates duplicate deltas by currency
ID. It validates the entire resulting batch for same-economy ownership,
nonnegative final values, and the `9,007,199,254,740,991` safe-integer cap before
persisting any part. The service is a transactional primitive used inside a
feature Action, not an alternate command path.

A successful nonempty batch increments `Player.wallet_revision` exactly once,
regardless of how many currencies changed. A normalized empty batch changes no
rows, emits no event, and does not increment the revision. A failed batch also
leaves the revision unchanged.

### Locking Order

Currency balances are part of the character aggregate.

Currency integration extends the global WR2 lock order to:

1. `InstanceRun`
2. Room rows in ascending ID order
3. Player rows in ascending ID order
4. Mob rows in ascending ID order
5. `MerchantRuntime`, then stock/buyback rows in ascending ID order
6. Item rows in ascending ID order
7. `PlayerCurrencyBalance` rows in `(player_id, currency_id)` order
8. receipt and outbox inserts

An Action omits aggregate kinds it does not need but never changes their order.
A standalone wallet operation therefore locks Player rows and then balance
rows. A merchant purchase locks the affected Player, merchant runtime/stock,
Item, and finally balance. Feature code must not debit first and then acquire an
earlier aggregate lock.

Player-to-player transfer is not part of the implemented service. A future
transfer must lock both Player rows before either balance is changed. Locking a
Player first serializes missing-row creation for that player; the database
unique constraint remains the final race guard.

All wallet writes must acquire every affected Player row first. Code must not
`get_or_create` a balance or lock a balance before acquiring its owner lock.
Currency definition rows are resolved and validated but are not hot runtime
locks.

Implementations may use locked rows, conditional updates, and `F()` expressions
as appropriate. The observable rule is that affordability is checked and the
debit is committed in one transaction.

### Idempotency And Receipts

Generic currency idempotency receipts are deferred. The current wallet API has
no operation key and calling it twice applies the delta twice. Feature handlers
must therefore avoid retry claims unless their outer feature supplies its own
replay protection.

A later reusable durable `ActionMutationReceipt` should let the outer feature
Action own one receipt covering item/quest/merchant state, wallet deltas, and
emitted events. The receipt belongs at that logical-operation boundary rather
than as a wallet-only receipt.

The remainder of this subsection is deferred design direction, not an
implemented contract.

The receipt is stored in the same transaction and contains a stable idempotency
key, operation fingerprint, immutable operation facts, and retention metadata.
Existing crafting receipts may remain feature-specific in the first currency
change, but the new abstraction should be suitable for later convergence.

The key identifies the logical operation, not a worker delivery attempt:

- external requests use request UUID plus command segment
- event effects use source event ID plus effect index
- scheduled/system work uses its stable business identifier

Multi-balance operations share one receipt so they replay as a unit. The same
key and fingerprint returns the immutable operation facts and rebuilds current
actor/wallet state for the response; it must not resend an old
`balance_after` after later legitimate transactions. The same key with a
different fingerprint fails with `idempotency_conflict`. Receipt retention must
outlive every supported Action redelivery or regeneration window, after which a
bounded cleanup task may prune it.

The receipt is replay protection, not a full banking ledger. If economic audit,
dispute resolution, or double-entry accounting becomes a requirement, add an
explicit retained ledger rather than treating transient delivery records as
one.

### Events

By default, a committed nonempty mutation enqueues one private domain event
through the transactional outbox in the same transaction. Callers may set
`emit_event=False` for initialization/reset or when a larger feature owns its
event contract. Publication happens after commit. `GameEventOutbox` is deleted
after successful delivery, so it provides reliable delivery rather than a
retained audit log.

Implemented shape:

```json
{
  "type": "currency.balances_changed",
  "data": {
    "player": "player.42",
    "wallet_revision": 8,
    "reason": "quest.reward",
    "changes": [
      {
        "currency": {
          "code": "crowns",
          "name": "Crown",
          "plural_name": "Crowns",
          "description": "The ordinary coin of the realm."
        },
        "delta": 25,
        "before": 10,
        "after": 35,
        "money": {
          "amount": 35,
          "currency": "crowns",
          "display": "35 Crowns"
        }
      }
    ]
  }
}
```

All currencies changed by the batch appear in one event. The monotonically
increasing `wallet_revision` and absolute `after` values let a client reject an
older snapshot and converge after duplicate/out-of-order delivery. Because the
wallet currently has no generic replay receipt, replaying the mutation itself
would produce another revision and event. A public room event may say that a
player received a reward without broadcasting the player's wallet.

Feature events such as `merchant.item.bought`, `quest.completed`, or
`mob.killed` should still be emitted. Consumers should not parse text to learn
which currency moved.

## Command -> Action -> Event Integration

Currency follows the ordinary WR2 pipeline.

### Planning

Planning:

- resolves the actor and authorization
- resolves a supplied currency code within the base world
- resolves omission to the current default
- validates that the amount is an integer in the allowed range
- puts the runtime-world ID, economy-world ID, concrete currency ID, and
  authoritative aggregate IDs into the Action payload
- includes every aggregate identifier needed for locking

Example future fully queued Action (including the deferred receipt key):

```json
{
  "action_type": "merchant.buy",
  "payload": {
    "player_id": 42,
    "runtime_world_id": 17,
    "economy_world_id": 2,
    "merchant_runtime_id": 8,
    "stock_entry_id": 91,
    "currency_id": 3,
    "amount": 25
  },
  "idempotency_key": "..."
}
```

Execution rechecks authoritative price, availability, and balance under lock.
The amount in an Action must not let a stale or malicious planner override the
authoritative merchant/item price.

Human-facing currency codes and item or merchant selectors are Command inputs,
not queued Action identity. Planning-time affordability and eligibility checks
provide quick feedback only; execution repeats every state-dependent check
while holding the required locks.

### Execution

Execution:

- loads and locks required aggregates
- revalidates same-world currency ownership
- invokes the currency service within the feature transaction
- mutates the other feature state atomically with the balance
- stores feature events before commit; a future generic action-receipt layer
  will add the receipt at the outer feature boundary

Buying an item must not charge without transferring the item, and transferring
the item must not occur without charging. Quest completion must not be marked
paid unless its currency credit commits in the same logical operation.

### Publication

Published payloads carry stable currency codes, not database IDs. Database IDs
remain inside Action and persistence boundaries.

## Feature Integration

### Character Creation And Reset

`starting_gold` is removed; authored starting balances use:

```yaml
kind: world
spec:
  starting_balances:
    crowns: 25
    guild-marks: 2
```

Character initialization applies the configured balances in one batch. Reset
semantics are:

- a true character reset replaces the affected balances with configured
  starting values
- ordinary respawn or instance transfer does not reset balances
- world-specific administrative reset tools must say whether they preserve or
  replace money

Initialization and reset must be idempotent so a retry cannot grant starting
money twice. A reset replaces balances with the configured snapshot; it does
not add starting balances to what the player already owns. Reconnect, base-world
spawn, and instance entry are never initialization events.

### Items And Prices

Item manifests use adjacent fields:

```yaml
spec:
  cost: 100
  currency: crowns
```

The parser resolves the code to a same-world `Currency` relation. Canonical
item-definition storage uses the concrete relation rather than an unvalidated
code inside generic JSON.

Runtime item instances keep a concrete cost/currency snapshot so later
definition edits do not reinterpret existing items. Null currency never means
Gold. An item with no monetary value stores neither amount nor currency; an
explicit free value stores zero plus a concrete currency. This distinction
prevents null from carrying hidden fallback behavior.

Item values and every other monetary amount use the same checked
`0..9,007,199,254,740,991` safe-integer range as balances. Prices are integers
in v1; fractional subunits are not smuggled into one feature through floats.

### Merchants

The initial merchant model uses one settlement currency per profile.

```yaml
settlement_currency: crowns
funds:
  mode: finite
  purchase_budget: 5000
```

`settlement_currency` names the price denomination independently from whether
the merchant has a finite purchase budget. The optional WR1 conversion utility
maps legacy `funds.currency` to this field; WR2 manifest ingestion does not
accept the legacy alias.

Rules:

- The currency selector defaults during authoring but is persisted explicitly,
  including for `mode: unlimited`. Unlimited removes only the finite
  purchase-budget gate; it does not remove the merchant's price denomination.
- Shop listing, buy, sell, and buyback all use the profile's settlement
  currency and the currency service.
- Finite purchase budget is denominated in the same currency.
- A stock item's authored value must use that currency unless the stock entry
  supplies an explicit merchant price in the settlement currency.
- Mismatched currencies are rejected; WR2 does not silently convert them.
- Player purchases do not replenish a finite restock budget unless the merchant
  system later defines that behavior explicitly.

`MerchantRuntime` snapshots the concrete settlement currency and finite budget
for the current restock generation. Each stock entry snapshots its concrete
price amount and currency, and each buyback entry snapshots the currency paid
at sale alongside its sold and buyback amounts. A later profile or world-default
change cannot reinterpret an outstanding offer or obligation.

Changing a profile settlement currency takes effect only at a defined runtime
or restock boundary and resets a finite budget in the new denomination. Before
the change applies, old stock must be retired and every buyback obligation
resolved or expired under its declared policy. If that cannot be done safely,
the edit is blocked. One runtime never carries simultaneous old/new settlement
currencies. Merchant monetary columns use checked `BIGINT`; merchant arithmetic
must deterministically produce a checked integer amount rather than persist or
mutate money through `Float` values. The merchant architecture owns the exact
rounding policy.

Buy, sell, and buyback Actions carry entry IDs plus the concrete quoted amount
and currency. Execution locks the runtime and entry, revalidates the current
offer, and then performs item and wallet mutations atomically. Two buyers of
one stock entry yield one successful purchase and one rejection; concurrent
sells cannot overspend a finite purchase budget.

Restock remains a separate bounded merchant Action; the currency service never
triggers restock as a side effect of listing or moving money.

A merchant needing another currency can use another profile. Per-stock mixed
currencies and exchange merchants are deferred until a real builder use case
justifies the additional UI and arbitrage rules.

### Mob Rewards And Combat

The singular mob `gold` reward is removed. Mob definitions use zero or more
authored currency rewards.

Conceptual manifest shape:

```yaml
spec:
  rewards:
    currencies:
      crowns: 12
      guild-marks: 1
```

Canonical storage may use a small `MobCurrencyReward` relation with unique
`(mob_definition, currency)` rows. Runtime definition caches can include the
resolved rewards so a kill does not query once per currency.

Kill resolution credits all currency rewards in one batch and includes those
amounts in the combat reward event. Party sharing must be a later explicit
policy; the currency service should not guess how a reward is divided.

### Death Costs

Gold-specific death naming is replaced by a currency loss policy.

Conceptual fields:

```yaml
spec:
  death_mode: lose_currency
  death_currency: crowns
  death_currency_penalty: 0.2
```

The currency is selected explicitly when the policy is authored. The existing
penalty basis is the value of equipped items whose concrete snapshots use that
same currency, multiplied by the configured rate and capped by the player's
balance. Values in other currencies are not converted or added. Changing to a
percentage of wallet balance is a separate game-design decision, not part of
making the currency generic.
Percentage/rate policy uses fixed-point or `Decimal` arithmetic with documented
rounding before producing the integer debit; it never mutates balances with a
floating-point amount.

PvP exemptions and instance overrides remain death-policy concerns. The debit
always acts on the player's persistent base-world balance, including while the
player is inside an instance.

### Quests, Instances, And Other Rewards

Canonical reward effects use `grant_currency`:

```yaml
- type: grant_currency
  scope: player
  currency: crowns
  amount: 80
```

The manifest parser validates the code. Action planning resolves it to a
currency ID, and effect execution uses the currency service. Quest, instance,
achievement, and scripted reward systems should share this effect semantics
rather than each implementing credit logic.

The optional WR1 conversion utility maps a WR1 `grant_gold` reward to this
canonical shape with `currency: gold`. WR2 itself does not need a
`grant_gold` compatibility effect.

### Conditions

Currency conditions use the existing structured WR2 condition framework.
Do not create a currency-only predicate language.

Example:

```yaml
conditions:
  gte:
    - actor.balances.crowns
    - 10
```

The condition context should receive a preloaded balance snapshot so evaluating
several conditions does not query once per condition. Authored conditions name
a concrete code; the builder UI may preselect the default, but exported
conditions stay explicit. A missing configured balance resolves to zero. Any
condition that authorizes a mutation is evaluated again under the owning
Player/feature locks during Action execution.

The optional WR1 conversion utility translates representable legacy money
conditions into this structured form and reports unsupported or ambiguous
conditions for builder review. WR2 does not evaluate a second legacy condition
language.

### Clan Costs, Awards, And Transfers

Any clan registration fee or other system charge becomes an amount plus an
explicit currency and uses the service.

Builder/admin award commands should accept a currency code and may default it
only during command planning. Administrative adjustments require permission,
an audit reason, and an idempotency key.

Player-to-player currency transfer is safe to expose later because the service
already defines ordered two-player locking. Trade escrow and multi-asset trades
remain separate features; a plain transfer must not be mistaken for an atomic
item trade.

### Crafting Materials

Crafting materials remain separate authored resources and separate normalized
balances.

They are not currencies because the initial crafting system intentionally does
not make them merchant tender, direct trade assets, death penalties, general
quest money, or item-price denominations. The two systems may share low-level
patterns such as ordered row locking and idempotent receipts without sharing a
definition model or builder vocabulary.

### Glory, Faction Standing, And Combat Resources

Glory remains a score unless a world explicitly replaces its gameplay role
with an authored currency. Faction standing belongs to the faction system.
Health, energy, and stamina belong to the combat/resource system. Keeping these
domains separate prevents a generic numeric field from becoming an unbounded
and poorly constrained pseudo-currency framework.

## Instances

Currency definitions are inherited from the base world.

Instance templates and runs must not:

- create or rename currencies
- select another default
- clone player balances
- create instance-local versions of the same code

An instance may override an allowed death or reward policy, but every referenced
currency must resolve against the base-world catalog. Credits and debits affect
the same persistent player balances used outside the instance.

Reward and cost manifests authored inside an instance resolve codes against
`economy_world(instance)` and queued Actions still carry both the runtime
world/instance identity and the concrete base-owned currency ID. The runtime
identity preserves instance isolation; the economy identity preserves the
meaning of money. A default switch while an Action waits does not change that
Action, and instance teardown never deletes a balance.

This preserves the rule that rewards and prices have one meaning throughout a
world and prevents instance cleanup from deleting player money.

## Runtime Payloads And Presentation

### World Currency Catalog

World payloads key definitions by stable code rather than database ID.

```json
{
  "economy": {
    "revision": 7,
    "default_currency": "crowns",
    "currencies": {
      "crowns": {
        "name": "Crown",
        "plural_name": "Crowns",
        "description": "The ordinary coin of the realm."
      },
      "guild-marks": {
        "name": "Guild Mark",
        "plural_name": "Guild Marks",
        "description": ""
      }
    }
  }
}
```

The catalog is base-world configuration. Send it at world/session state load
and refresh it when its revision changes; do not repeat every definition in
every transaction event. A cache key such as
`(economy_world_id, economy_revision)` makes invalidation explicit.
Currency add/delete, display edits, starting-balance edits, and default changes
bump the revision in the same transaction as the authored change. Player
balance mutations do not.

### Player Balances

Player-private payloads include the wallet revision and one mapping:

```json
{
  "economy": {
    "wallet_revision": 12,
    "balances": {
      "crowns": 125,
      "guild-marks": 3
    }
  }
}
```

The state-sync snapshot and `currencies` command include the default even at
zero and include only positive nondefault balances. Frontends treat any omitted
configured balance as zero. Each successful nonempty wallet batch increments
`wallet_revision` once; catalog edits instead increment the world economy
`revision`.

WR2 launches its backend and frontend together against this shape. It does not
publish legacy `gold`, `medals`, or serialized `currencies` projections. A
no-Gold world is therefore a normal supported world, not a protocol edge case.

### Money Values

Structured feature payloads use a common shape:

```json
{
  "amount": 20,
  "currency": "crowns",
  "display": "20 Crowns"
}
```

The Pydantic `Money` container has exactly these three fields: integer `amount`,
currency code `currency`, and preformatted `display`. It applies to item values,
merchant prices, rewards, penalties, and transaction summaries. `display` is a
convenience string; clients should use `amount` and `currency` for logic. Text
rendering uses the authored singular/plural names:

Runtime item payloads expose this nullable container as `value`. They do not
also expose fallback `cost` or `currency` fields.

- `0 Crowns`
- `1 Crown`
- `2 Crowns`
- `1 Gold`
- `2 Gold`

Frontend and backend rendering should share the same naming rule. Do not infer
singular text by trimming characters from the code.

### Player Commands And Screens

The implemented `currencies` command:

- shows the default first
- shows nondefault positive balances
- shows an empty/default-zero state clearly
- uses authored names rather than codes when presenting text
- returns both console text and structured balances

Inventory, stats, merchant, lookup, and reward screens render dynamic currency
data and do not publish hardcoded Gold or Medals wallet rows.

## Builder UX

**World > Currencies** is the ordinary economy screen.

Each row should show:

- default badge
- code
- singular and plural display names
- optional description
- starting amount
- concise usage summary

Actions:

- **Add Currency**
- **Edit Display**
- **Make Default**
- **Copy YAML**
- **Delete**, disabled with an explanation when blocked

The default selector should behave like one radio selection, not independent
checkboxes that can all be cleared or selected.

Other builder screens should use a currency dropdown. The default is
preselected for new money fields, while existing selections remain unchanged
on edit.

Instance currency pages are read-only and explain that currencies come from the
base world, with a link back to the base-world screen.

### Live Editing Policy

REST and YAML share a conservative initial policy: currency creation, display
edits, default selection, starting-balance replacement, and deletion are
rejected while any ordinary spawn or instance run for the base world is
running. Codes cannot be edited. Deletion additionally requires every
registered authored/runtime usage to be absent. This stopped-world policy can
be relaxed later without changing currency identity or manifest shapes.

## Performance And Scalability

Performance requirements:

- Currency lookup is backed by the case-insensitive per-world unique index.
  The economy revision supports client/cache invalidation, although a shared
  backend catalog cache is not part of this implementation.
- A player wallet snapshot is one `select_related` query for positive balance
  rows, or no query when those rows were already prefetched. The default
  relation is selected with the owning world and is included at zero.
- A mutation locks only involved Player and balance rows.
- A batch touching `k` currencies performs bounded bulk reads/writes in `O(k)`;
  it does not issue one query per configured currency.
- Missing balance creation never scans other players.
- Default switching is an `O(1)` base-world reference update and does not fan
  out across player balances or authored content.
- Adding a currency does not create a zero row for every player.
- Definition edits do not broadcast separately to every player; world/session
  configuration invalidation refreshes the catalog.
- Analytics consume structured events or indexed offline queries rather than
  scanning wallets in a gameplay request.
- Public events do not carry complete private balance snapshots.
- Response payloads use the in-memory post-mutation values rather than
  reserializing the entire Player aggregate.
- A grant to many players is paged into bounded batches; it never locks a room
  or world's whole population in one transaction.
- A currency mutation is not one Celery task per balance row. One feature
  Action performs its bounded batch in one transaction.

For a `k`-currency mutation, the implemented shape is one Player-lock query,
one batched currency-validation query, one ordered balance-lock query, bounded
bulk create/update work, and at most one balance-event outbox insert. Exact
query counts may vary with missing-row creation, but growth must not become one
query per currency or one full Player reload. A future generic receipt adds one
outer-operation insert rather than one receipt per balance.

The normalized model creates at most one row per player/currency pair that has
been used. That is a small, predictable cost compared with rewriting serialized
maps or modeling money as physical item rows.

Representative performance tests should assert:

- bounded query count for wallet serialization
- bounded query count for a multi-currency reward
- no N+1 default lookup while spawning batches of items or mobs
- no negative result under concurrent debits
- no negative result under concurrent debit contention; duplicate-delivery
  protection remains deferred with generic action receipts

Production metrics should include wallet transaction duration, lock wait time,
deadlocks/retries, revision gaps, and outbox delivery lag. Once generic receipts
exist, add replay/idempotency-conflict metrics. Load tests must cover both
independent-player throughput and contention on one hot player or merchant;
averages alone will miss the economically dangerous cases.

## Authorization And Validation

Authoring operations must:

- require the same world-configuration permission in UI, REST, and manifest
  paths
- reject instance-world writes
- validate same-base-world ownership for every reference
- prevent code mutation and unsafe deletion
- apply the same stopped/maintenance policy everywhere

Runtime operations must:

- authorize the initiating actor or trusted system
- use an authoritative amount and currency from the feature under lock
- reject cross-world player/currency combinations
- reject negative, fractional, boolean, overflow, and malformed amounts
- audit privileged adjustments

Never trust a client-supplied price merely because its currency code is valid.

## Clean WR2 Launch And Optional WR1 Manifest Conversion

### Clean Database Rule

WR2 launches with a clean, empty database. There is no production WR1-to-WR2
data migration and no requirement to preserve transitional WR2 development
rows.

The currency implementation therefore does **not** need:

- player Gold, Medals, or custom-balance backfills
- per-player migration markers or reconciliation jobs
- old/new dual writes or compatibility projections
- a staged world-by-world data cutover
- legacy clients or payload shapes
- WR1 models, fields, effects, or conditions in the WR2 importer/runtime

Django schema migrations still establish the final WR2 schema on an empty
database. They are not a WR1 data-transition mechanism. Local development data
may be reset while the target schema is built rather than complicating the
runtime with temporary preservation logic.

Early-WR2 fields such as `Player.gold`, `Player.medals`, serialized
`Player.currencies`, `WorldConfig.starting_gold`, and `Currency.is_default` have
been removed from the target models. Their schema migrations define the clean
WR2 database; they do not read or transform WR1 production data.

### Optional Authored-World Conversion Utility

Builders who want to try moving a WR1 world may run the separate WR1 utility.
That utility reads WR1 **authored world content** and emits canonical WR2
manifests. The builder reviews those manifests and imports them into a newly
created WR2 world through the ordinary manifest pipeline.

The utility does not connect to the WR2 database or migrate:

- users or accounts
- player characters or balances
- inventories or runtime item instances
- quest progress, cooldowns, faction assignments, or other player state
- running worlds, mobs, merchant stock/buyback, queued work, or events

WR2 remains unaware of WR1 database shapes. The conversion utility resolves
WR1 database IDs to portable WR2 codes/slugs/refs, emits only current manifest
fields, and reports anything it cannot translate safely for builder review.
WR2 should not accumulate legacy aliases merely to make the utility easier to
write.

### Currency Conversion Contract

For the authored currency/economy portion of an optional WR1 world conversion,
the utility should:

1. Emit a `gold` currency definition and select `gold` as the world default,
   because Gold was WR1's effective default.
2. Emit every valid authored custom currency definition.
3. Emit `medals` only when authored world content references the built-in
   Medals concept; player Medals balances are runtime state and are ignored.
4. Normalize the known WR1 item-currency enum `medal` to `medals`, while never
   guessing that an arbitrary custom code named `medal` has the same meaning.
5. Map `starting_gold` to `starting_balances.gold`.
6. Map authored mob Gold rewards, Gold costs, Gold death policies, and
   representable quest rewards/effects to amount-plus-`gold` target contracts.
7. Emit structured `actor.balances.<code>` conditions only when the WR1 meaning
   can be preserved; otherwise report the source condition for author review.
8. Emit currencies before manifests that reference them and emit the world
   default in `kind: world`, never as `Currency.is_default`.

The utility must not inspect or export player balance fields to decide which
currencies exist. A world manifest bundle contains authored definitions and
configuration only, never live player state.

The WR1 manifest-conversion notes in
[yaml-manifest-system.md](yaml-manifest-system.md) describe these final currency,
world, item, merchant, mob, death, condition, and quest shapes directly.

## Implementation Status

### Implemented: Definitions And Invariants

- introduce the shared `economy_world()` resolver
- tighten currency code and name validation
- add plural name and description
- add the base-world default reference
- make it the only default reader/writer and remove `Currency.is_default`
- add relational starting balances and an economy revision
- make instance inheritance explicit
- introduce one authoring service used by REST and manifests
- add usage-aware deletion protection

### Implemented: Balance Foundation

- add `PlayerCurrencyBalance`
- add the currency service
- integrate ordered Player/balance locking and outbox events
- add wallet snapshot serialization

Generic idempotent receipts are deferred and are not part of the implemented
wallet API.

### Implemented: Runtime Integrations

- character starting/reset balances
- items and prices
- merchant buy/sell/buyback
- mob rewards and combat messages
- death costs
- quest and instance reward effects
- clan/system costs
- existing currency conditions
- route every writer through the service and remove direct special-field writes

Privileged generic currency adjustments/awards are deferred. The former
Gold-specific `/award` help entries have been removed until a permissioned,
audited command with an explicit currency code is designed and implemented.

### Implemented Foundation: Builder And Player Surfaces

- replace the currency list with the target economy screen
- add default selection and usage-aware delete
- add currency selectors to money-bearing editors
- make stats, inventory, merchants, lookup, and rewards dynamic
- implement the `currencies` wallet command
- add canonical copy/apply/delete YAML

The REST/manifests and player payload/command foundation is implemented.
Additional polish to currency selectors on every builder form may continue
without changing the contracts in this document.

### Implemented: Clean-Launch Schema

- remove Gold-specific runtime constants and branches
- remove old player balance/config fields and legacy manifest/effect aliases
- verify a fresh empty database reaches only the target schema
- reset non-production data rather than adding preservation/backfill machinery
- update the optional WR1 manifest-conversion notes and all builder/player guides

## Validation And Test Matrix

The implementation is not complete until it covers the following.

### Definition And Authoring Tests

- create, update display, make default, export, import, and delete
- reject invalid/case-colliding codes
- reject code rename through ordinary update
- reject zero-default and cross-world-default playable states
- reject deletion when default, referenced, balanced, or pending
- allow a draft import to be temporarily defaultless but reject publication
- prove create omission materializes the current default, update omission
  preserves the stored currency, and canonical export is explicit
- enforce identical REST and manifest permissions/lifecycle rules
- show inherited read-only currencies for instances
- roll back an invalid multi-document application completely

### Runtime Tests

- create a character with several starting balances
- buy, sell, and buy back using a non-Gold settlement currency
- grant non-Gold mob and quest rewards
- apply a non-Gold death cost
- evaluate a structured balance condition without N+1 queries
- transfer atomically between two players
- reject cross-world currency use
- display singular, plural, default-zero, and multiple balances correctly
- keep balances unchanged across instance entry, exit, and cleanup
- reject ordinary relocation between different economy worlds

### Concurrency And Retry Tests

- two simultaneous debits against one balance yield at most one overspend
- two simultaneous purchases of one stock entry yield one buyer
- simultaneous sells cannot overspend a finite merchant budget
- opposite-direction transfers cannot deadlock
- a retried credit pays once and emits one domain event
- a retried debit charges once
- a multi-currency batch commits all or none
- balance never becomes negative under concurrent load
- concurrent creation of the same missing zero row remains unique
- restock racing a purchase preserves stock and money invariants
- changing the default between planning and execution does not change the
  queued Action's currency

### Clean Launch And Conversion Utility Tests

- build the final schema from an empty database with no data backfill step
- verify old Gold/Medals/custom-map fields and legacy payload projections are
  absent from the target runtime
- reject obsolete WR1/early-WR2 manifest fields instead of normalizing them in
  the WR2 importer
- convert a representative WR1 authored world to canonical currency, world,
  item, merchant, mob, death, condition, and quest manifests
- select Gold as the converted WR1 world's default and preserve authored
  custom definitions and starting Gold configuration
- normalize the known built-in `medal` item enum without renaming an arbitrary
  authored custom code
- report ambiguous/unsupported authored source content for builder review
- prove the conversion utility exports no users, players, balances, inventories,
  quest progress, merchant runtime state, or other live data
- import the utility's output into a fresh WR2 world and round-trip it through
  canonical export

### Scalability Tests

- assert wallet serialization query count
- assert multi-currency mutation query count with dozens of currencies
- assert merchant listing/query counts with dozens of stock and buyback entries
- assert currency catalog caching/invalidation behavior
- assert adding or changing a default does not update every player
- benchmark independent-player load and hot-player/hot-merchant contention

### End-To-End Acceptance Test

Create a world with one currency:

```yaml
kind: currency
metadata:
  code: credits
spec:
  name: Credit
  plural_name: Credits
---
kind: world
spec:
  default_currency: credits
  starting_balances:
    credits: 50
```

Do not create a Gold definition.

That world must support:

- character creation and reset
- item values and merchant transactions
- mob, quest, and instance rewards
- death and system costs
- conditions and privileged awards
- player wallet, inventory, stats, lookup, and messages
- export/import round trip
- instance inheritance
- concurrent debit protection and monotonic wallet revisions; generic
  retry-safe credits remain deferred with action receipts

No canonical runtime path in that test may query, display, or mutate a special
Gold field. Passing this test demonstrates that the default currency is truly
customizable rather than a label layered over WR1 Gold behavior.

## Rejected Alternatives

| Alternative | Why it is rejected |
| --- | --- |
| Keep special Gold/Medals fields plus a custom JSON map | Preserves divergent code paths, weak concurrency, and hardcoded UI/runtime behavior. |
| Migrate live WR1 or transitional WR2 data into WR2 | WR2 deliberately starts with an empty database; only optional authored-world manifest conversion crosses the boundary. |
| Keep `Currency.is_default` booleans | Allows zero or several defaults and makes concurrent updates procedural rather than structural. |
| Store omitted currency as null and resolve the default at runtime | A later default change silently reinterprets existing prices, rewards, and queued work. |
| Store canonical balances in one JSON object | Requires read/modify/write of the whole map and cannot enforce per-currency constraints or locks. |
| Create a zero row for every player whenever a currency is added | Turns an `O(1)` definition edit into population-wide writes and creates mostly empty data. |
| Start with a generic wallet for every possible owner | Adds polymorphic integrity and locking complexity before a real non-player wallet requirement exists. |
| Treat the outbox as a permanent transaction ledger | The current outbox is deleted after delivery and lacks accounting semantics; replay protection and financial audit are different concerns. |
| Model balances as physical coin item stacks | Makes ordinary payment inventory-heavy and does not solve abstract reward, cost, or merchant accounting. |

## Deferred Extensions

Possible later additions, only when a concrete design requires them:

- retired-but-still-held currencies
- explicit builder-assisted code retargeting
- exchange merchants with authored rates and anti-arbitrage validation
- clan or organization treasuries
- player trade escrow
- merchant cash wallets distinct from restock budgets
- fractional currencies with fixed precision
- currency-specific icons or richer display formatting
- economy analytics dashboards and balance leaderboards

Each extension should continue to use explicit currency IDs in Actions,
transactional balance mutation, portable codes in manifests/events, and
base-world ownership.

## Final Invariants

The target system is correct when all of the following are true:

- A playable base world has exactly one selected default currency.
- Gold and Medals receive no engine-level privilege.
- One `economy_world()` rule owns all definition and instance resolution.
- Every spendable player amount lives in `PlayerCurrencyBalance`.
- Every mutation uses the currency service and cannot produce a negative
  balance.
- No runtime path interprets null, an unknown code, or a deleted reference as
  Gold or as the current default.
- Runtime feature work resolves a concrete base-world currency; the wallet
  primitive itself is not replay-safe until generic action receipts are added.
- Every authored or runtime reference resolves within the same base world.
- Instances inherit definitions and mutate persistent player balances.
- Balance changes, wallet revision, and any enabled outbox balance event commit
  atomically.
- Wallet query and lock work is bounded by the Action, never by world
  population.
- Builders can understand the system as definitions, one default, and
  amount-plus-currency fields.
- Changing the default never silently converts or reinterprets existing money.
- A world with no Gold definition works end to end.
- A fresh WR2 database requires no WR1 data, compatibility fields, or backfill;
  optional WR1 conversion imports authored manifests only.
