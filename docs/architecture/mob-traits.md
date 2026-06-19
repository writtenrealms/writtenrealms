# WR2 Mob Traits

This document describes the target architecture for mob traits in WR2. Traits
are what some systems call affixes: authored or rolled modifiers that make a
spawned mob behave differently, such as `exploder`, `tracker`, `enraged`,
`colossal`, `linker`, `armored`, or `resilient`.

Related combat-routine behavior, including dual-wielding mobs and multiattack
traits, is covered in
[attack-routines-and-dual-wielding.md](/Users/teebes/code/writtenrealms/docs/architecture/attack-routines-and-dual-wielding.md).

## Decision Summary

- Use the builder-facing name `traits`, not `affixes`.
- Rename spawn-plan manifest fields from `affixes` to `traits` before the
  spawn-plan authoring surface is widely used.
- Keep `affixes` as an import-only compatibility alias during the transition if
  existing tests, draft manifests, or early content already use it.
- Treat numeric/stat traits as data-driven modifiers.
- Treat behavior traits as manifest-configured data backed by hard-coded,
  reviewed runtime handlers.
- Do not introduce a new predicate language. Any conditional trait logic must
  use the WR2 condition DSL in `backend/core/condition_dsl.py`.

The rename is worth doing now because `trait` is already a more natural WR
builder/player term, and traits can be intrinsic to a mob definition. `Affix`
implies procedural item-style name decoration and makes less sense for mobs that
are always authored as explosive, linked, or tracking.

## Goals

- Let builders attach traits directly to mob definitions.
- Let spawn plans roll guaranteed or weighted traits during placement
  generation.
- Persist the exact trait snapshot chosen for each spawned mob.
- Keep ordinary spawn reconciliation idempotent: never reroll traits while a
  spawn-plan run is active.
- Support simple stat modifiers without bespoke code per trait.
- Support selected behavior traits through safe, explicit runtime hooks.
- Preserve a path toward WR2's Command -> Action -> Event architecture.

## Non-Goals

- Do not make traits arbitrary Python, formula, or script execution.
- Do not make a second condition/predicate format for trait compatibility or
  activation.
- Do not invest in legacy `Loader` / `Rule` as the rich trait-rolling path.
- Do not depend on runtime JSON as the only source of truth for authored traits.
- Do not require a `kind: mobtrait` manifest in the first implementation.

## Terminology

### Trait Definition

A known trait key plus its supported schema and defaults. In the first
implementation this can live in a hard-coded registry:

```python
exploder
tracker
linker
enraged
colossal
armored
resilient
```

Later, simple reusable data-only trait definitions can become their own
manifest kind.

### Trait Assignment

An authored place where a builder attaches or rolls traits:

- `MobDefinition.spec.traits` for intrinsic traits.
- `SpawnEntry.traits` for placement-time guaranteed or weighted traits.
- Future encounter, region, difficulty, or boss-phase systems.

### Trait Instance

The concrete trait snapshot on one spawned mob. It should include:

- key
- params
- modifiers
- source metadata
- visibility/display metadata
- runtime state, if any
- optional definition/schema version

### Trait Handler

Runtime code that knows how to apply a trait at supported lifecycle hooks. A
handler may be generic, such as numeric stat modifier application, or specific,
such as `exploder` scheduling delayed damage after death.

## Authored Manifest Shapes

### Intrinsic Traits On Mob Definitions

Use `spec.traits` when every spawned copy of a mob should have a trait.

```yaml
kind: mobdefinition
metadata:
  slug: volatile-sentry
  name: a volatile sentry
spec:
  type: construct
  combat:
    health: 80
    attack_power: 12
  traits:
    - key: exploder
      params:
        delay_rounds:
          min: 1
          max: 2
        damage:
          calc: percent_max_health
          amount: 35
    - key: armored
      modifiers:
        armor_multiplier: 1.5
```

### Rolled Traits On Spawn Plans

Use `entries[].traits` when a spawn plan should roll or guarantee traits for a
placement. This replaces the earlier draft name `affixes`.

```yaml
kind: spawnplan
metadata:
  slug: sunken-crypt-population
  name: Sunken Crypt Population
spec:
  zone: zone@3
  randomization:
    seed_scope: instance
  respawn:
    mode: none
  entries:
    - slug: hallway-patrols
      source_pool:
        - ref: mobdefinition.drowned-guard
          weight: 70
        - ref: mobdefinition.crypt-scout
          weight: 30
      target:
        zone: zone@3
      count:
        min: 4
        max: 7
      traits:
        guaranteed:
          - key: resilient
            modifiers:
              resilience_multiplier: 1.5
        chance: 35
        pool:
          - key: enraged
            weight: 40
            modifiers:
              attack_power_multiplier: 1.5
          - key: exploder
            weight: 15
            params:
              delay_rounds:
                min: 1
                max: 2
```

Recommended compatibility behavior:

- accept `traits`
- accept `affixes` only as an alias while importing transitional content
- reject manifests that define both `traits` and `affixes` on the same entry
- export only `traits`

### Future Reusable Trait Manifests

If local spawn-plan trait data becomes repetitive, add a reusable manifest kind:

```yaml
kind: mobtrait
metadata:
  slug: colossal
  name: Colossal
spec:
  category: size
  visibility: visible
  modifiers:
    health_max_multiplier: 2.0
  incompatible_with:
    - fragile
```

Behavior trait manifests should still bind to known safe handlers. They should
not introduce arbitrary scripts or formulas.

## Data Model Direction

### Authored Models

`MobDefinition` should gain a structured JSON field for intrinsic traits:

```python
traits = models.JSONField(default=list, blank=True)
```

The existing legacy `traits` text field inherited through `MobMixin` is not a
sufficient structured runtime contract. It can remain as compatibility/display
text during the transition, but new WR2 trait behavior should not depend on it.

`SpawnEntry` should use:

```python
traits = models.JSONField(default=dict, blank=True)
```

The current/draft `affixes` field should be renamed before the surface is used
by builders. If database migrations already exist in local development,
preserve data with a rename migration rather than dropping and recreating.

### Runtime Models

`SpawnPlacement` should store the chosen placement trait snapshots:

```python
traits = models.JSONField(default=list, blank=True)
modifiers = models.JSONField(default=dict, blank=True)
```

`modifiers` can remain separate while stat mutation is simple and efficient.
Each trait instance should also preserve its own modifiers so diagnostics can
explain why a mob has a changed stat.

`Mob` should store the resolved trait instances, preferably as structured JSON:

```python
trait_instances = models.JSONField(default=list, blank=True)
```

If a separate 1:1 `MobRuntime` table lands first, highly transient trait state
can live there. The canonical spawned-mob row still needs enough source data to
rebuild runtime state after cache invalidation.

Example resolved mob trait snapshot:

```json
[
  {
    "key": "enraged",
    "source": "spawn_plan",
    "source_ref": "spawnplan.sunken-crypt-population/hallway-patrols/3",
    "visibility": "visible",
    "params": {},
    "modifiers": {
      "attack_power_multiplier": 1.5
    },
    "runtime": {},
    "version": 1
  },
  {
    "key": "exploder",
    "source": "mob_definition",
    "source_ref": "mobdefinition.volatile-sentry",
    "visibility": "hidden_until_death",
    "params": {
      "delay_rounds": 2,
      "damage": {
        "calc": "percent_max_health",
        "amount": 35
      }
    },
    "modifiers": {},
    "runtime": {},
    "version": 1
  }
]
```

`roll_metadata.spawn_plan` may continue to include diagnostic trait data, but
runtime systems should eventually read the resolved `trait_instances` field or
runtime cache instead of parsing spawn-plan metadata.

## Loader And Spawn-Plan Integration

Spawn plans are the correct target path for random traits. Legacy loaders can
inherit intrinsic mob-definition traits, but they should not receive the richer
trait rolling architecture.

Generation flow:

1. Resolve active `SpawnPlan` rows for the root world or zone.
2. Create or reuse a `SpawnPlanRun` with a deterministic seed.
3. Generate `SpawnPlacement` rows.
4. For each placement, choose source, room, and rolled traits.
5. Persist selected traits on the placement.
6. Reconcile placements into concrete mobs/items without rerolling.

Materialization flow for mobs:

1. Spawn the base mob from `MobDefinition` or legacy `MobTemplate`.
2. Build resolved trait instances from intrinsic definition traits and placement
   traits.
3. Apply validated numeric modifiers to base stats.
4. Persist `trait_instances`, origin metadata, and any changed stat fields.
5. Run `on_spawn` trait hooks when needed.

Reset flow:

1. Mark or expire the current `SpawnPlanRun`.
2. Despawn or retire runtime output according to reset policy.
3. Generate a new run if policy requires a fresh roll.
4. Reconcile the new placements.

## Numeric Modifiers

Simple traits should be data-only when possible:

- `enraged`: `attack_power_multiplier: 1.5`
- `colossal`: `health_max_multiplier: 2.0`
- `armored`: `armor_multiplier: 1.5`
- `resilient`: `resilience_multiplier: 1.5`

Supported semantics:

- direct key adds to the field, for example `armor: 2`
- `_multiplier` multiplies the field, for example `health_max_multiplier: 2.0`
- current resource fields should follow max fields where appropriate, for
  example `health` follows `health_max`

Modifier application must use an allowlist of safe numeric fields. Unsupported
modifier keys can be preserved for diagnostics but should not affect combat.

## Behavior Trait Hooks

Behavior traits should be backed by explicit handlers. Suggested hook phases:

- `on_spawn`
- `before_damage_taken`
- `after_damage_applied`
- `after_mob_defeated`
- `after_player_flee`
- `on_combat_round_start`
- `on_combat_round_end`
- `before_action_execute`
- `after_action_execute`

Initial implementation can call these hooks directly from existing combat and
movement code. The target WR2 architecture should migrate behavior hooks toward
queued actions and emitted events where delayed or cross-aggregate behavior is
involved.

Handler registry sketch:

```python
MOB_TRAIT_HANDLERS = {
    "exploder": ExploderTraitHandler(),
    "tracker": TrackerTraitHandler(),
    "linker": LinkerTraitHandler(),
}
```

Handlers should validate params on manifest import or materialization, not at
the moment a combat round is resolving.

## Example Trait Behaviors

### Exploder

Intent: explodes one or two rounds after death.

Recommended design:

- Trait params define delay and damage shape.
- On mob defeat, capture the room, killer, spawn world, and trait snapshot before
  the mob row is deleted.
- Schedule a delayed trait action, such as `ResolveMobTraitAction`.
- When it runs, lock the room and affected characters/mobs in normal aggregate
  order.
- Emit room/player-visible events.

Avoid relying on the dead mob row still existing. Store enough action payload to
resolve deterministically after the corpse/mob cleanup path runs.

### Tracker

Intent: chases the player after they flee, usually after a round or short delay.

Recommended design:

- Hook into successful flee completion.
- Schedule a delayed mob movement or engagement action.
- Use the same command issuer direction as ambient/mob commands: the mob is the
  embodied subject issuing a movement or engagement command.
- Revalidate at execution time that the mob is alive, the player is in the
  expected world, and movement is still possible.

This should align with the ambient command issuer plan rather than crafting
ad-hoc movement mutations.

### Linker

Intent: one mob cannot take damage until a linked mob dies first.

Recommended design:

- Generate concrete links at placement generation or materialization time.
- Store a stable target reference in the trait instance runtime state.
- In `before_damage_taken`, if the linked mob is alive, reduce incoming damage
  to zero and emit feedback.
- Revalidate that the target is in the same spawn world and not pending deletion.

Do not make the manifest point directly at a volatile runtime mob id. Authored
selectors should resolve into runtime references during generation.

## Conditions

All conditional trait logic must use the WR2 structured condition DSL.

Examples:

```yaml
conditions:
  eq:
    - state.zone.crypt_cleansed
    - false
```

```yaml
conditions:
  all:
    - gte:
        - actor.level
        - 5
    - not:
        eq:
          - state.world.weather
          - calm
```

The condition context should include actor, target, room, zone, world, event
data, and trait data where relevant.

## Events And Actions

Target action/event concepts:

- `ResolveMobTraitAction`
- `MobTraitTriggered`
- `MobTraitPreventedDamage`
- `MobTraitDelayedEffectScheduled`
- `MobTraitDelayedEffectResolved`

For the first implementation, direct function calls from combat may be
acceptable for synchronous hooks. Delayed hooks like `exploder` and `tracker`
should use a scheduled task/action from the start if practical.

## Visibility And UI

Traits should support visibility:

- `visible`: shown in look/combat payloads.
- `hidden`: never shown directly.
- `hidden_until_triggered`: revealed when behavior occurs.
- `builder_only`: visible in builder diagnostics, not player payloads.

Builder diagnostics should show:

- traits available on a mob definition
- traits configured on spawn entries
- traits rolled onto generated placements
- traits present on spawned mobs

Player-facing text should be authored or derived from trait definitions, not
hard-coded in every hook.

## Migration And Rename Plan

Recommended field rename path:

1. Add `traits` to `SpawnEntry` and `SpawnPlacement`.
2. Data-migrate existing `affixes` JSON into `traits`.
3. Update manifest parsing to accept `traits`.
4. Accept `affixes` as a transitional alias only when `traits` is absent.
5. Update export to emit `traits` only.
6. Update builder guide examples and tests.
7. Remove the `affixes` alias after early transition content has been migrated.

The same terminology should be used in the code model unless there is a strong
compatibility reason not to. Keeping a code field named `affixes` while the
manifest says `traits` will create unnecessary translation overhead.

## Testing Requirements

Tests should live under `backend/wr2_tests/`.

Minimum coverage:

- mob definition manifest accepts intrinsic traits
- spawn plan manifest accepts `traits`
- `affixes` alias imports old content and exports as `traits`
- generated placements persist deterministic trait rolls
- reconciliation does not reroll active placements
- numeric modifiers update stats and current resources correctly
- unsupported modifier keys are preserved but inert
- `exploder` schedules and resolves delayed effects
- `tracker` follows a successful flee only when still valid
- `linker` blocks damage until the linked mob is dead
- trait conditions use the shared condition DSL

## Implementation Phases

### Phase 1: Rename And Structured Storage

- Rename spawn-plan `affixes` to `traits`.
- Add structured trait fields to mob definitions and spawned mobs.
- Preserve import compatibility for old `affixes` documents.
- Update export, tests, and builder docs.

### Phase 2: Numeric Traits

- Implement trait normalization and modifier application.
- Support `enraged`, `colossal`, `armored`, and `resilient` as data-only traits.
- Add diagnostics showing final trait snapshots.

### Phase 3: Behavior Trait Registry

- Add handler registry and synchronous combat hooks.
- Implement `linker` first because it is synchronous and easy to test.
- Add clear event payloads for prevented or modified damage.

### Phase 4: Delayed And Cross-Action Traits

- Add scheduled action support for `exploder` and `tracker`.
- Align tracker execution with mob/ambient command issuer rules.
- Emit durable trait events for debugging and client presentation.

### Phase 5: Optional Reusable Trait Manifests

- Add `kind: mobtrait` only after repeated local trait configs justify it.
- Keep behavior handlers hard-coded and parameterized, not arbitrary scripts.
