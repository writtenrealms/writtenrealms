# WR2 Guided Random Item Definitions

Status as of 2026-07-06: WR2 now uses `ItemDefinition` as the only item
authoring model. The legacy `ItemTemplate` model, manifest kind, runtime FK,
and `/load` fallback path have been removed. Historical references below
describe the transition period that led to the current definition-backed
implementation.

## Purpose

WR2 needs a builder-manageable way to define items whose stable identity comes
from authored content, but whose spawned instances can roll bounded stat
variance.

Example:

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  description: A practical blade with a simple leather grip.
  type: equippable
  equipment_type: weapon_1h
  randomization:
    attributes:
      - key: strength
        min: 10
        max: 20
        mode: uniform
      - key: dexterity
        min: 1
        max: 5
        mode: favor_low
```

The feature should support:

- deterministic item definitions with no variance
- guided random item definitions with explicit ranges and distribution modes
- world-authored attributes from `spec.stats.attributes`
- silent runtime tolerance when an authored attribute becomes stale
- builder warnings and audit tools for stale definitions
- unique frontend inventory and room lines for randomized spawned items
- random bundles that can be assigned to merchants, mobs, rewards, or spawn
  plans

The design goal is not a fully procedural loot engine. It is controlled
randomness that builders can understand.

First-pass non-goals:

- no randomized names, affixes, description fragments, sockets, or rarity tables
- no arbitrary formula language inside item randomization
- no hard-coded `strength`, `dexterity`, `constitution`, or `intelligence`
  assumptions
- no revival of WR1 `RandomItemProfile` as the runtime model

Reference docs:

- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [attributes-builder-guide.md](../guides/builders/attributes-builder-guide.md)
- [item-definition-builder-guide.md](../guides/builders/item-definition-builder-guide.md)
- `.codex/skills/wr-transition/wr2-architecture.md`

## Current Baseline

The repository now has the first guided-random implementation slice:

- `ItemDefinition` has a world-scoped `slug` and spawns concrete `Item` rows.
- `ItemBundle` and `ItemBundleEntry` provide weighted authored choices among
  item definitions.
- `ItemDefinition` and `spawns.Item` have JSON-backed `attributes`.
- `spawns.Item` has nullable `definition`, `definition_slug_snapshot`, and
  `roll_metadata` fields for definition-backed generated items.
- State payloads expose item `attributes`, `definition_slug`,
  `is_stackable`, `stack_key`, and canonical item combat fields such as
  `weapon_damage`, `attack_power`, `ability_power`, `armor`, `crit`, `dodge`,
  and `resilience`.
- The frontend stacks by backend-provided `stack_key` and definition identity.
- Stable definition-backed items resync unmodified spawned copies when the
  definition changes. Randomized items keep their rolled attributes, and
  augmented items are treated as modified instances.
- `/load item <slug-or-id>` spawns `ItemDefinition` rows.
- Merchant stock uses `MerchantProfile` stock slots with item definitions or
  item bundles, as described in
  [merchant-system.md](/Users/teebes/code/writtenrealms/docs/architecture/merchant-system.md).

Remaining gaps before this is builder-complete:

- no structured item definition editor yet
- no stale-randomization audit endpoint or management command yet

The WR1-era `RandomItemProfile` model, CRUD API, builder screen, procedural
generation helpers and system endpoint, and runtime item provenance FK have
been removed from WR2.
That system generated equipment from broad categories such as weapon, shield,
or armor and carried hard-coded legacy stat assumptions. It remains an optional
WR1 exporter input concept only; WR2 imports representable authored intent as
explicit item definitions and bundles rather than restoring the old runtime
shape.

## Recommendation

Create a clean WR2 item definition layer and keep the old random item system out
of the new path.

Current terms used in this document:

- `ItemDefinition`: the WR2 authored item model
- `ItemInstance`: a documentation term for a concrete spawned item with
  persisted rolled values; in code this is `spawns.Item`
- `ItemBundle`: authored weighted bundle/table of item definitions

New authored guided-random definitions live in `ItemDefinition`, not in
`RandomItemProfile` or the old random-drop fields.

This keeps the mental model simple:

- stable item: an `ItemDefinition` with no randomization spec
- guided random item: an `ItemDefinition` with a randomization spec
- spawned item: a `spawns.Item` row with copied base fields and persisted
  rolled values
- random bundle: an `ItemBundle` that chooses among authored definitions

`ItemDefinition.slug` is the stable reference for manifests, merchants, mob
drops, rewards, and bundles.

## Core Model

### Authored Item

WR2 authored item content lives in `ItemDefinition`.

```python
class ItemDefinition(models.Model):
    world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120)
    name = models.TextField()
    description = models.TextField(blank=True)
    room_description = models.TextField(blank=True)
    keywords = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    item_type = models.TextField()
    base_properties = models.JSONField(default=dict, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("world", "slug")
```

`base_properties` is for structured item fields that are not attributes,
such as equipment type, weapon grip, food value, or container capacity. Fields
that become high-traffic query targets can be promoted to columns later.

`attributes` is for fixed authored attributes. In manifests and
builder APIs this appears as `spec.attributes`. Rolled attributes are merged
into the concrete runtime item's `attributes` when spawned.

`randomization` is only for values that should roll at spawn time.

Recommended shape:

```json
{
  "version": 1,
  "attributes": [
    {
      "key": "strength",
      "min": 10,
      "max": 20,
      "mode": "uniform"
    },
    {
      "key": "dexterity",
      "min": 1,
      "max": 5,
      "mode": "favor_low",
      "curve": 1.5
    }
  ]
}
```

The initial randomization version should support numeric attribute rolls
only. Rollable canonical item fields such as `weapon_damage`, `cost`, or
`armor` can be considered later, but they should not be part of phase 1. Avoid
name pieces, description fragments, sockets, affixes, or conditional formulas
in the first implementation.

### Runtime Item

Persist the roll result on the spawned item. Do not rely on recomputing the
definition later.

The runtime item is `spawns.Item`. That is the concrete object loaded into
rooms, inventories, equipment slots, corpses, merchants, quest rewards, and
other gameplay surfaces. Player commands should continue to interact with
`spawns.Item`; do not add a second concrete runtime item table just because
this document uses the phrase "item instance."

Target additions or guarantees on `spawns.Item`:

```python
class Item(models.Model):
    definition = models.ForeignKey(ItemDefinition, null=True, on_delete=models.SET_NULL)
    definition_slug_snapshot = models.SlugField(max_length=120, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    roll_metadata = models.JSONField(default=dict, blank=True)
```

`attributes` holds effective authored attributes for this concrete
item, such as:

```json
{
  "strength": 17,
  "dexterity": 2
}
```

`attributes` is the final effective attribute contribution for this
concrete item after fixed and rolled attributes are merged. `roll_metadata`
should hold low-cardinality audit data, not gameplay logic:

```json
{
  "source_definition_slug": "bronze-sword",
  "randomization_version": 1,
  "rolled_at_definition_modified_ts": "2026-05-14T12:00:00Z",
  "ignored_attributes": ["luck"]
}
```

`spawns.Item` already has JSON-backed `attributes`; the guided-random
feature still needs definition linkage and roll metadata. The target behavior
is clean WR2: item bonuses flow through JSON-backed `attributes` and
canonical item stat fields, not through fixed STR/DEX/CON/INT columns.

## Roll Spec Semantics

Each random attribute entry should be small and declarative:

- `key`: attribute key from the world's stat system, not a canonical stat
- `min`: inclusive minimum integer
- `max`: inclusive maximum integer
- `mode`: distribution mode
- `curve`: optional numeric bias strength for biased modes, default `1.0`

Supported initial modes:

- `uniform`: every integer in the range is equally likely
- `favor_low`: low values are common and high values are rare
- `favor_high`: high values are common and low values are rare

For biased modes, use weighted discrete rolls instead of opaque probability
math. For example:

- `favor_low`: weight each value by `(max - value + 1) ** curve`
- `favor_high`: weight each value by `(value - min + 1) ** curve`

That gives builders a predictable model: larger curve means stronger bias.

Hard validation should only reject malformed definitions:

- non-numeric ranges
- `min > max`
- unknown distribution mode
- invalid `curve`

Missing attribute keys should not make runtime generation fail.

Do not hard-code `strength`, `dexterity`, `constitution`, or `intelligence` in
the feature. Those keys only work in worlds that explicitly define them in
`spec.stats.attributes`.

## Stale Attribute Tolerance

World attributes are configurable. New WR2 worlds start with no attributes at
all. Builders may later remove or rename `strength` after item
definitions already reference it. The runtime must survive that.

Runtime rule:

- when spawning an item, load the current world stat system
- for each randomization entry, check whether `key` exists in
  `spec.stats.attributes`
- if the key exists, roll and persist it
- if the key is missing, skip that entry and add it to
  `roll_metadata.ignored_attributes`
- never raise a player-facing error because of a stale attribute key

If a blank world imports an item definition that references `strength`, that
definition is stale until the world defines `strength`. The item can still be
created and spawned, but the `strength` roll is ignored for gameplay and
reported to builders through validation and audit tooling.

Existing spawned items should be treated the same way during stat aggregation:

- declared keys contribute normally
- unknown keys are ignored
- raw stored JSON remains available for audit and future repair

This is "fail silently" for gameplay, not "hide problems from builders."
Builder tools should surface stale definitions prominently.

## Builder Tooling

The builder UI should make the common case easy and the stale case visible.

Recommended editor:

- a "Randomized attributes" section on item definitions
- one row per attribute roll
- attribute picker populated from current `spec.stats.attributes`
- min/max numeric inputs
- mode dropdown with `Uniform`, `Favor low`, `Favor high`
- optional advanced curve input hidden behind row expansion

When a saved randomization entry references a missing attribute:

- keep the row visible
- show the raw stale key
- mark it as ignored at runtime
- allow the builder to replace it with a valid attribute or delete it

Do not force builders to edit raw JSON for the builder workflow. The API can
still store JSON because the data shape is simple and versioned.

## Audit Tools

Add an audit path after the runtime behavior is in place.

Useful outputs:

- item definition slug
- stale attribute keys
- invalid range definitions
- definitions with randomization but no rollable attributes
- bundles that reference missing definitions

Candidate surfaces:

- builder endpoint: `GET /builders/worlds/:id/item-randomization-audit/`
- management command: `audit_item_randomization --world <id-or-slug>`
- manifest validation warning during import/export

Audits should be warnings unless the malformed spec cannot be parsed. A world
with stale random definitions should still boot.

## Frontend Stacking

Current state payloads already include item `attributes` and
`definition_id`, `definition_slug`, `is_stackable`, and `stack_key`.

The frontend should decide stackability from backend-provided `stack_key`, not
from raw item ids or definition ids.

WR1 had two clear categories:

- templated items: identical by template and stackable in the UI
- procedural items: unique rows because stats could differ

Guided random item definitions sit between those categories. They have an
authored definition, but each spawned item must render as its own line because
it may have unique rolled stats. Even if two rolls happen to produce identical
values, they should still behave as unique generated instances.

Recommended payload change:

```json
{
  "key": "item.123",
  "definition_slug": "bronze-sword",
  "stack_key": null,
  "is_stackable": false,
  "attributes": {
    "strength": 17
  }
}
```

For stable non-container definition-backed items:

```json
{
  "key": "item.124",
  "definition_slug": "bronze-sword",
  "stack_key": "definition:bronze-sword:2026-05-16T20:00:00+00:00",
  "is_stackable": true
}
```

The frontend stacking helper should group by `stack_key`, not by
`definition_slug` or `definition_id`. If `stack_key` is empty, the item is always
rendered as a unique line using its item `key`.

The stack key is intentionally opaque to builders. The backend includes the
definition revision so stale stable copies cannot collapse with newer copies if
an item bypasses the normal definition resync path.

The backend should own this policy because it has the full context:

- randomized definition or not
- container or not
- augmented instance or not
- persistent/special item rules
- future item-instance metadata

## Random Bundles

Guided random item definitions solve "what does this spawned sword roll?" They do
not solve "which possible item does this mob or merchant produce?"

Add a separate high-level bundle/table concept for discrete authored choices.
Recommended name:

- `ItemBundle`

Conceptual model:

```python
class ItemBundle(models.Model):
    world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120)
    name = models.TextField()
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("world", "slug")

class ItemBundleEntry(models.Model):
    bundle = models.ForeignKey(
        ItemBundle,
        related_name="entries",
        on_delete=models.CASCADE,
    )
    item_definition = models.ForeignKey(ItemDefinition, on_delete=models.CASCADE)
    weight = models.PositiveIntegerField(default=1)
    min_quantity = models.PositiveIntegerField(default=1)
    max_quantity = models.PositiveIntegerField(default=1)
    probability = models.PositiveIntegerField(default=100)
```

Bundles are deliberately not stat generators. They only choose among authored
definitions. Those definitions may be deterministic or guided-random.

Example:

```yaml
kind: itembundle
metadata:
  slug: bandit-weapon-drop
  name: Bandit weapon drop
spec:
  entries:
    - item_definition: bronze-sword
      weight: 5
    - item_definition: chipped-axe
      weight: 3
    - item_definition: rusty-dagger
      weight: 2
```

This supports cases like:

- this mob can drop randomized item A, B, or C
- this merchant stocks one item from this curated bundle
- this quest reward chooses from a controlled set

## Merchant And Mob Integration

Keep direct authored-item assignment and bundle assignment separate in the data
model. Avoid a generic "target can be anything" field for the first pass unless
the surrounding builder code already strongly prefers it.

Merchant-specific authoring and restock behavior is specified in
[merchant-system.md](/Users/teebes/code/writtenrealms/docs/architecture/merchant-system.md).
New merchant work should target `MerchantProfile` stock slots, not WR1-era
merchant inventory tables.

Current progression:

1. Direct `ItemDefinition` assignment spawns stable or guided-random items.
2. `ItemBundle` assignment supports merchant stock, mob drops, and manifest
   authored reward/content choices.
3. Bundle entries spawn their selected `ItemDefinition` through the same item
   spawn path.

This prevents merchant/drop code from learning two different roll mechanisms.
Everything eventually calls:

```python
spawn_item_from_definition(definition, target, spawn_world, rng)
```

## Determinism

Roll logic should be implemented as a pure service that accepts an explicit RNG
object:

```python
roll_item_randomization(definition, world_stat_system, rng) -> RollResult
```

The spawn orchestration is responsible for choosing the RNG seed. Tests should
be able to pass a seeded RNG and assert exact roll results. Runtime code should
store rolled values immediately on the `spawns.Item` row so later definition
edits do not mutate existing items.

This follows the WR2 architecture direction that execution should be
deterministic when the initial state and random seed are the same.

## Manifest Shape

World manifests support separate documents for authored items and item
bundles, following the existing multi-document manifest flow used by
`kind: itemdefinition`, `kind: itembundle`, `kind: world`, `kind: quest`, and
other WR2 entities.

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  description: A practical blade with a simple leather grip.
  room_description: A bronze sword lies here.
  keywords: bronze sword blade
  type: equippable
  equipment_type: weapon_1h
  weapon_grip: one_hand
  weapon_damage: 8
  attributes:
    strength: 2
  randomization:
    attributes:
      - key: strength
        min: 10
        max: 20
        mode: uniform
      - key: dexterity
        min: 1
        max: 5
        mode: favor_low
        curve: 1.5
---
kind: itembundle
metadata:
  slug: bandit-weapon-drop
  name: Bandit weapon drop
spec:
  entries:
    - item_definition: bronze-sword
      weight: 5
    - item_definition: rusty-dagger
      weight: 3
```

`spec.attributes` is the fixed item contribution. `spec.randomization`
defines additional spawn-time rolls that merge into the concrete item's
persisted `attributes`.

Manifest import should hard-fail malformed randomization specs, but stale
attribute keys should be warnings. This matches runtime behavior while still
giving builders feedback when they are editing authored content.

## Implementation Path

### Phase 1: Spawn-Time Guided Rolls

- Done: add the clean authored WR2 item model under the transitional
  `ItemDefinition` name.
- Done: reuse the existing JSON-backed `attributes` storage on concrete
  items.
- Done: add definition linkage and `roll_metadata` storage to `spawns.Item`.
- Done: implement a pure roll service with seeded RNG support.
- Done: call the service from `spawn_item_from_definition`.
- Done: store rolled attributes in JSON and let stat computation ignore
  stale or undeclared keys.
- Done: add backend tests for rolls, stale keys, persistence, manifests, export, and
  `/load item` support.

This phase proves the clean model without touching merchants, bundles, or the
builder UI heavily.

### Phase 2: Payload And Stacking

- Done: add `attributes`, `is_stackable`, and `stack_key` to item
  payloads.
- Done: update frontend stacking to group by `stack_key`.
- Done: make guided-random spawned items return `stack_key: null`.
- Remaining: add frontend/unit coverage where available for deterministic stack
  grouping.

This phase prevents UI regressions before randomized items become widely
available.

### Phase 3: Builder Editor

- Done: add `kind: itemdefinition` manifest import/export.
- Add a structured randomization editor to the item definition form.
- Populate attribute options from the world's stat config.
- Display stale keys as ignored warnings.
- Return validation warnings from the item definition API.
- Done: include randomization in world export/import.

This phase makes the feature manageable by builders.

### Phase 4: Item Bundles

- Done: add `ItemBundle` and `ItemBundleEntry` with world-scoped slugs.
- Done: add `kind: itembundle` manifest import/export.
- Add builder list/detail screens.
- Done: allow mob drops and merchant inventories to reference either a direct
  `ItemDefinition` or an `ItemBundle`.
- Done: keep bundle execution as selection plus normal definition spawn.

This phase adds discrete random choice without expanding the stat roll language.

### Phase 5: Audits And Cleanup

- Add stale randomization audit endpoint or management command.
- Add manifest validation warnings.
- Done: remove WR1-era `RandomItemProfile` models, APIs, UI, procedural
  generation paths, and runtime provenance. If content conversion is needed,
  do it as a one-way manifest conversion into `ItemDefinition` and
  `ItemBundle`, flagging broad procedural rules that have no
  semantics-preserving mapping.
- Done: remove the old item-template implementation from builder/runtime
  surfaces.
- Remove any remaining fixed primary-stat assumptions from item generation and
  presentation.

## Complexity Boundaries

Keep these constraints unless a later design has a strong reason to relax them:

- Randomization happens once at item spawn, not every time the item is viewed.
- Roll specs are numeric and declarative.
- No arbitrary formulas in item randomization.
- No nested random tables inside item randomization.
- Bundles choose definitions; definitions roll attributes.
- Runtime ignores stale attribute keys.
- Builder tools surface stale attribute keys.
- Existing items keep their rolled values after definition edits.

These boundaries are what keep guided randomness from becoming an unbounded
procedural content engine.

## Open Questions

- Should cost be rollable in phase 1, or recomputed from rolled stats later?
- Should `weapon_damage` be a rollable canonical item field, or remain computed
  from item level and equipment type?
- Should bundle entries support "choose N distinct entries" immediately, or
  only one weighted roll per entry?
- Merchant restock timing is specified in
  [merchant-system.md](/Users/teebes/code/writtenrealms/docs/architecture/merchant-system.md).
- Should player-facing item inspection show rolled attributes directly,
  or only the effective stats after formulas apply?
