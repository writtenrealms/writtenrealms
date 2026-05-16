# WR2 Guided Random Item Definitions

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
      - key: brawn
        min: 10
        max: 20
        mode: uniform
      - key: grace
        min: 1
        max: 5
        mode: favor_low
```

The feature should support:

- deterministic item definitions with no variance
- guided random item definitions with explicit ranges and distribution modes
- world-authored input attributes from `spec.stats.input_attributes`
- silent runtime tolerance when an authored attribute becomes stale
- builder warnings and audit tools for stale definitions
- unique frontend inventory and room lines for randomized item instances
- random bundles that can be assigned to merchants, mobs, rewards, or loaders

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
- [input-attributes-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/input-attributes-builder-guide.md)
- `.codex/skills/wr-transition/wr2-architecture.md`

## Current Baseline

The repository already has relevant pieces, but they are implementation
constraints rather than the target builder model:

- `ItemTemplate` has a world-scoped `slug` and spawns concrete `Item` rows.
- `ItemTemplate` and `spawns.Item` already have JSON-backed
  `input_attributes`.
- State payloads already expose item `input_attributes` and canonical item
  combat fields such as `weapon_damage`, `attack_power`, `ability_power`,
  `armor`, `crit`, `dodge`, and `resilience`.
- State payloads do not yet expose `definition_slug`, `is_stackable`, or
  `stack_key`.
- The frontend still stacks inventory by `template_id` when the item is not a
  container.

There is also a WR1-era `RandomItemProfile` path. That system procedurally
generates equipment from broad categories such as weapon, shield, or armor. It
is useful reference material only. The WR2 feature should not extend
`RandomItemProfile`, `drops_random_items`, or merchant inventory's old
`random_item_profile` shape. Reusing those models would carry forward the wrong
mental model: broad procedural generation with hard-coded legacy stat names.

## Recommendation

Create a clean WR2 item definition layer and keep the old random item system out
of the new path.

Recommended target nouns:

- `ItemDefinition`: authored item content with a world-scoped slug
- `ItemInstance`: concrete spawned item with persisted rolled values
- `ItemBundle`: authored weighted bundle/table of item definitions

The implementation may bridge through current `ItemTemplate` and `spawns.Item`
plumbing where that keeps the first pass smaller, but those names should not be
the builder-facing concept for this feature. New authored guided-random
definitions should live in the new model, not in `RandomItemProfile` or the old
random-drop fields.

This keeps the mental model simple:

- stable item: an `ItemDefinition` with no randomization spec
- guided random item: an `ItemDefinition` with a randomization spec
- spawned item: an `ItemInstance` with copied base fields and persisted rolled
  values
- random bundle: an `ItemBundle` that chooses among authored definitions

`ItemDefinition.slug` is the stable reference for manifests, merchants, mob
drops, rewards, and bundles.

## Core Model

### ItemDefinition

Create a new authored model for WR2 item content:

```python
class ItemDefinition(models.Model):
    world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120)
    name = models.TextField()
    description = models.TextField(blank=True)
    ground_description = models.TextField(blank=True)
    keywords = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    item_type = models.TextField()
    base_properties = models.JSONField(default=dict, blank=True)
    base_input_attributes = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("world", "slug")
```

`base_properties` is for structured item fields that are not input attributes,
such as equipment type, weapon grip, food value, or container capacity. Fields
that become high-traffic query targets can be promoted to columns later.

`base_input_attributes` is for fixed authored input attributes. In manifests and
builder APIs this should appear as `spec.input_attributes`, matching current
`itemtemplate` manifests. The `base_` prefix is only an internal distinction
between fixed authored inputs and rolled inputs.

`randomization` is only for values that should roll at spawn time.

Recommended shape:

```json
{
  "version": 1,
  "attributes": [
    {
      "key": "brawn",
      "min": 10,
      "max": 20,
      "mode": "uniform"
    },
    {
      "key": "grace",
      "min": 1,
      "max": 5,
      "mode": "favor_low",
      "curve": 1.5
    }
  ]
}
```

The initial randomization version should support numeric input-attribute rolls
only. Rollable canonical item fields such as `weapon_damage`, `cost`, or
`armor` can be considered later, but they should not be part of phase 1. Avoid
name pieces, description fragments, sockets, affixes, or conditional formulas
in the first implementation.

### ItemInstance

Persist the roll result on the spawned item. Do not rely on recomputing the
definition later.

Conceptual target fields:

```python
class ItemInstance(models.Model):
    world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    definition = models.ForeignKey(ItemDefinition, null=True, on_delete=models.SET_NULL)
    definition_slug_snapshot = models.SlugField(max_length=120, blank=True)
    name = models.TextField()
    description = models.TextField(blank=True)
    ground_description = models.TextField(blank=True)
    base_properties = models.JSONField(default=dict, blank=True)
    base_input_attributes = models.JSONField(default=dict, blank=True)
    input_attributes = models.JSONField(default=dict, blank=True)
    roll_metadata = models.JSONField(default=dict, blank=True)
```

`input_attributes` holds effective authored input attributes for this concrete
item, such as:

```json
{
  "brawn": 17,
  "grace": 2
}
```

`input_attributes` is the final effective input-attribute contribution for this
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

During implementation, `ItemInstance` may be backed by the existing
`spawns.Item` table. That table already has JSON-backed `input_attributes`; the
guided-random feature still needs definition linkage and roll metadata. The
target behavior is clean WR2: item bonuses flow through JSON-backed
`input_attributes` and canonical item stat fields, not through fixed
STR/DEX/CON/INT columns.

## Roll Spec Semantics

Each random attribute entry should be small and declarative:

- `key`: input attribute key from the world's stat system, not a canonical
  derived stat
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
`spec.stats.input_attributes`.

## Stale Attribute Tolerance

World input attributes are configurable. New WR2 worlds start with no input
attributes at all. Builders may later remove or rename `brawn` after item
definitions already reference it. The runtime must survive that.

Runtime rule:

- when spawning an item, load the current world stat system
- for each randomization entry, check whether `key` exists in
  `spec.stats.input_attributes`
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
- attribute picker populated from current `spec.stats.input_attributes`
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

Current state payloads already include item `input_attributes` and
`template_id`. They do not yet include stack metadata, and
`frontend/src/core/utils.ts` still stacks by `template_id`.

The frontend should stop deciding stackability from `template_id` alone. In the
WR2 target payload, the corresponding field should be `definition_id` or
`definition_slug`; `template_id` can remain as transitional compatibility data
while old payload consumers are updated.

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
  "input_attributes": {
    "brawn": 17
  }
}
```

For stable non-container definition-backed items:

```json
{
  "key": "item.124",
  "definition_slug": "bronze-sword",
  "stack_key": "definition:bronze-sword",
  "is_stackable": true
}
```

The frontend stacking helper should group by `stack_key`, not by
`definition_slug` or `template_id`. If `stack_key` is empty, the item is always
rendered as a unique line using its item `key`.

The backend should own this policy because it has the full context:

- randomized definition or not
- container or not
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

Keep direct item-definition assignment and bundle assignment separate in the data
model. Avoid a generic "target can be anything" field for the first pass unless
the surrounding builder code already strongly prefers it.

Suggested progression:

1. Direct `ItemDefinition` assignment spawns stable or guided-random items.
2. Existing direct `ItemTemplate` assignment can continue until the new
   definition path replaces it.
3. Add `ItemBundle` assignment to merchant inventory and mob drops.
4. Bundle entries spawn their selected `ItemDefinition` through the same item
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
store rolled values immediately on the item instance row so later definition
edits do not mutate existing items.

This follows the WR2 architecture direction that execution should be
deterministic when the initial state and random seed are the same.

## Manifest Shape

World manifests should eventually support `kind: itemdefinition` and
`kind: itembundle` documents, following the existing multi-document manifest
flow used by `kind: itemtemplate`, `kind: world`, `kind: quest`, and other WR2
entities.

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  description: A practical blade with a simple leather grip.
  ground_description: A bronze sword lies here.
  keywords: bronze sword blade
  type: equippable
  equipment_type: weapon_1h
  weapon_grip: one_hand
  weapon_damage: 8
  input_attributes:
    brawn: 2
  randomization:
    attributes:
      - key: brawn
        min: 10
        max: 20
        mode: uniform
      - key: grace
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

`spec.input_attributes` is the fixed item contribution. `spec.randomization`
defines additional spawn-time rolls that merge into the concrete item's
persisted `input_attributes`.

Manifest import should hard-fail malformed randomization specs, but stale
attribute keys should be warnings. This matches runtime behavior while still
giving builders feedback when they are editing authored content.

## Implementation Path

### Phase 1: Spawn-Time Guided Rolls

- Add `ItemDefinition` as the authored WR2 item concept.
- Reuse the existing JSON-backed `input_attributes` storage on concrete items.
- Add definition linkage and `roll_metadata` storage to the concrete item
  instance path.
- Implement a pure roll service with seeded RNG support.
- Call the service from `spawn_item_from_definition`.
- Store rolled input attributes in JSON and let stat computation ignore stale or
  undeclared keys.
- Add WR2 tests for uniform rolls, biased rolls, stale keys, and persistence.

This phase proves the clean model without touching merchants, bundles, or the
builder UI heavily.

### Phase 2: Payload And Stacking

- Add `input_attributes`, `is_stackable`, and `stack_key` to item payloads.
- Update frontend stacking to group by `stack_key`.
- Make guided-random item instances return `stack_key: null`.
- Add frontend/unit coverage where available for deterministic stack grouping.

This phase prevents UI regressions before randomized items become widely
available.

### Phase 3: Builder Editor

- Add `kind: itemdefinition` manifest import/export.
- Add a structured randomization editor to the item definition form.
- Populate attribute options from the world's stat config.
- Display stale keys as ignored warnings.
- Return validation warnings from the item definition API.
- Include randomization in world export/import.

This phase makes the feature manageable by builders.

### Phase 4: Item Bundles

- Add `ItemBundle` and `ItemBundleEntry` with world-scoped slugs.
- Add `kind: itembundle` manifest import/export.
- Add builder list/detail screens.
- Allow mob drops and merchant inventories to reference either a direct
  `ItemDefinition` or an `ItemBundle`.
- Keep bundle execution as selection plus normal definition spawn.

This phase adds discrete random choice without expanding the stat roll language.

### Phase 5: Audits And Cleanup

- Add stale randomization audit endpoint or management command.
- Add manifest validation warnings.
- Keep WR1-era `RandomItemProfile` outside the new execution path. If content
  conversion is needed, do it as a one-way manifest/import conversion into
  `ItemDefinition` and `ItemBundle`, not as a runtime adapter.
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
- Should `weapon_damage` be a rollable canonical item field, or remain derived
  from item level and equipment type?
- Should bundle entries support "choose N distinct entries" immediately, or
  only one weighted roll per entry?
- How should merchant restock timing interact with generated unique items?
- Should player-facing item inspection show rolled input attributes directly,
  or only the derived effective stats after formulas apply?
