# WR2 Spawn Plans and Guided Dungeon Loading

This document describes the target architecture and implementation path for
replacing the legacy Loader/Rule spawning system with a manifest-based WR2
spawn planning system.

The immediate motivation is to support dungeon zones with fixed room layouts
and guided random population. A dungeon's rooms, exits, and authored geography
should remain stable, while each dungeon instance or reset can vary which mobs
appear, where they appear, how dense the population is, and which traits or
modifiers are applied.

This is not a proposal to generate random room layouts. It is a proposal to
make authored spawn behavior richer, deterministic when needed, and compatible
with WR2's manifest and runtime architecture.

## Transition Positioning

This transition should mirror the current `ItemTemplate` to `ItemDefinition`
and `MobTemplate` to `MobDefinition` migration style.

The existing `Loader` and `Rule` models remain supported during the transition.
They are legacy authoring and runtime models. They should not receive major new
architecture beyond compatibility, migration, and possibly legacy manifest
round-trip support where useful.

Any manifest support added directly around legacy `Loader` / `Rule` should be
treated as compatibility support, not the target architecture. New authored
spawn work should use the clean replacement path described here.

Use new transitional names for the clean WR2 path:

- `SpawnPlan`: the new authored replacement for a legacy `Loader`
- `SpawnEntry`: the new authored replacement for a legacy `Rule`
- `SpawnPlanRun`: runtime state for one generated plan in one spawn world
- `SpawnPlacement`: one generated target/source/trait slot within a run

These names are intentionally transitional so they can coexist with the old
`Loader` and `Rule` tables. The long-term target is not to keep two parallel
systems. Once old `Loader` and `Rule` usage is gone, the new models should
become the canonical loader implementation and can take over the familiar
`Loader` / `Rule` names, or whatever final builder-facing vocabulary is clearest.

In other words:

- short term: old `Loader` / `Rule` and new `SpawnPlan` / `SpawnEntry` coexist
- medium term: builder tools prefer `SpawnPlan` / `SpawnEntry`
- long term: old `Loader` / `Rule` are phased out
- final state: the new implementation is the full replacement and may be renamed
  back to `Loader` / `Rule`

## Goals

- Support manifest-authored spawn plans that round-trip through world export and
  import.
- Support fixed-layout dungeon zones with guided random population.
- Keep authored data separate from runtime state.
- Make random generation deterministic under a stored seed.
- Allow builders to control density, source pools, placement constraints,
  difficulty budgets, and trait/modifier tables.
- Keep legacy Loader/Rule content working during migration.
- Provide a clear path to retire the legacy models.
- Align spawn execution with WR2's Command -> Action -> Event architecture over
  time.

## Non-Goals

- Do not generate random maps or room graphs in this system.
- Do not invent a second condition language. New conditional logic must use the
  WR2 structured condition DSL in `backend/core/condition_dsl.py`.
- Do not revive WR1 `RandomItemProfile` or the old broad procedural drop model.
- Do not make every spawn tick reroll dungeon population.
- Do not keep old `Loader` / `Rule` and new spawn plans as permanent parallel
  builder concepts.

## Current Problems With Legacy Loader/Rule

The existing system is compact and useful, but its model shape is legacy:

- `Loader` mixes authored configuration with runtime timestamps such as
  `last_processing_ts` and `last_removal_ts`.
- Zone reset timing is stored on authored `Zone` rows through `last_respawn_ts`.
- Runtime loader execution runs against spawn worlds but mutates root-world
  loader and zone records.
- `Rule` uses generic foreign keys for both source and target, so many
  invariants live in serializers rather than model-level or manifest-level
  validation.
- Zone/path targeting is simple random placement, not guided random placement.
- Nested rule execution depends on output produced during the current run,
  which makes reconciliation of existing nested spawns awkward.
- `loader_condition` is a separate Python-like mini language instead of the WR2
  structured condition DSL.
- `respawn_wait` semantics have drifted: comments suggest `0` means never
  respawn, while runtime behavior and tests use `0` as immediately eligible and
  `-1` as never.

These issues are manageable for legacy compatibility, but they are the wrong
foundation for richer dungeon population.

## Core Mental Model

A spawn plan is authored content. It says what kind of population a zone or
dungeon can have.

A spawn plan run is runtime content. It records what was actually generated for
one spawn world, dungeon instance, or reset.

Generation and reconciliation are separate steps:

1. Generate a deterministic set of placements from the authored plan.
2. Persist those placements.
3. Reconcile runtime mobs/items against those placements.
4. Spawn missing runtime entities without rerolling the plan.

This distinction is required for guided randomness. If the loader rerolls every
time it checks population, dungeon contents will be unstable and hard to test.

## Authored Model Direction

### SpawnPlan

`SpawnPlan` is the transitional clean replacement for `Loader`.

Recommended fields:

```python
class SpawnPlan(models.Model):
    world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    zone = models.ForeignKey("worlds.Zone", on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120)
    name = models.TextField()
    notes = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    reset_policy = models.JSONField(default=dict, blank=True)
    respawn_policy = models.JSONField(default=dict, blank=True)
    randomization = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("world", "slug")
```

`conditions` should use the structured WR2 condition DSL. Legacy text
conditions can be accepted only as import compatibility, not as the preferred
new authoring shape.

`randomization` describes seed scope, density, budgets, and generation options.

### SpawnEntry

`SpawnEntry` is the transitional clean replacement for `Rule`.

Recommended fields:

```python
class SpawnEntry(models.Model):
    plan = models.ForeignKey(SpawnPlan, on_delete=models.CASCADE, related_name="entries")
    slug = models.SlugField(max_length=120)
    name = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    source = models.JSONField(default=dict, blank=True)
    target = models.JSONField(default=dict, blank=True)
    count = models.JSONField(default=dict, blank=True)
    placement = models.JSONField(default=dict, blank=True)
    traits = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=dict, blank=True)
```

`source` may be a direct source or a weighted pool.

Supported source concepts:

- `mobdefinition`
- `itemdefinition`
- `itembundle`
- legacy `mobtemplate` during transition
- legacy `itemtemplate` during transition

`target` selects where generated placements may go.

Supported target concepts:

- fixed room
- room selector within the plan zone
- path
- zone
- another `SpawnEntry`, for nested loads into generated parent output

Nested targets should reference stable entry slugs, not database ids or fragile
ordering.

## Runtime Model Direction

### SpawnPlanRun

`SpawnPlanRun` stores runtime generation state for one spawn world and one
spawn plan.

Recommended fields:

```python
class SpawnPlanRun(models.Model):
    spawn_world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    plan = models.ForeignKey(SpawnPlan, on_delete=models.CASCADE)
    seed = models.TextField()
    spec_hash = models.TextField(blank=True)
    status = models.TextField(default="active")
    generated_at = models.DateTimeField()
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reset_at = models.DateTimeField(null=True, blank=True)
```

Runtime timestamps belong here, not on authored `SpawnPlan`, legacy `Loader`, or
authored `Zone` rows.

`spec_hash` lets the runtime detect whether authored content changed after a run
was generated. The first implementation can choose conservative behavior:
existing runs continue until reset, while new runs use the new spec.

### SpawnPlacement

`SpawnPlacement` stores one generated slot in a plan run.

Recommended fields:

```python
class SpawnPlacement(models.Model):
    run = models.ForeignKey(SpawnPlanRun, on_delete=models.CASCADE, related_name="placements")
    entry_slug = models.SlugField(max_length=120)
    slot_index = models.PositiveIntegerField()
    room = models.ForeignKey("worlds.Room", on_delete=models.CASCADE)

    source_type = models.TextField()
    source_slug = models.SlugField(max_length=120)
    source_id = models.PositiveIntegerField(null=True, blank=True)

    parent_entry_slug = models.SlugField(max_length=120, blank=True)
    parent_slot_index = models.PositiveIntegerField(null=True, blank=True)

    traits = models.JSONField(default=list, blank=True)
    modifiers = models.JSONField(default=dict, blank=True)
    state = models.JSONField(default=dict, blank=True)
```

This can start as a relational table or as a JSON payload on `SpawnPlanRun`.
Use a table if the runtime needs efficient reconciliation, inspection, or
builder diagnostics. A table is likely the better long-term shape.

### Runtime Origin Metadata

Spawned mobs and items need stable origin metadata so reconciliation can answer:

- Which placement created this entity?
- Is the desired slot already occupied?
- Which traits/modifiers were applied?
- Should this entity be replaced on reset?

Implementation options:

- nullable `spawn_placement` foreign keys on `spawns.Mob` and `spawns.Item`
- a generic `SpawnedEntityOrigin` join table
- transitional metadata fields while keeping legacy `rule` links

The target architecture should prefer explicit spawn placement linkage over
counting all live entities by legacy `Rule`.

## Guided Dungeon Randomness

Dungeon randomness should be guided by authored constraints, not fully
procedural.

The fixed layout remains in `zone` and `room` manifests. Spawn plans add
controlled variation:

- density: sparse, medium, dense, or explicit min/max encounter counts
- positioning: room tags, room roles, paths, depth bands, spacing rules
- difficulty: total budget, per-room caps, elite caps, party-size scaling later
- source pools: weighted mobs, item definitions, or item bundles
- traits: weighted or guaranteed modifiers with compatibility rules
- seed scope: instance, world reset, daily rotation, or explicit test seed

Example transitional manifest:

```yaml
kind: spawnplan
metadata:
  slug: sunken-crypt-population
  name: Sunken Crypt Population
spec:
  zone: zone@3
  reset:
    mode: instance_start
  respawn:
    mode: none
  randomization:
    seed_scope: instance
    density: medium
    difficulty_budget: 120
  entries:
    - slug: hallway-patrols
      source_pool:
        - ref: mobdefinition.drowned-guard
          weight: 70
          cost: 10
        - ref: mobdefinition.crypt-scout
          weight: 30
          cost: 8
      target:
        rooms:
          tags: [hallway]
          exclude_tags: [entrance, boss]
      count:
        min: 4
        max: 7
      placement:
        max_per_room: 1
        min_room_distance: 2
      traits:
        chance: 35
        pool:
          - key: shielded
            weight: 40
            cost: 3
          - key: frenzied
            weight: 35
            cost: 4
          - key: necrotic
            weight: 25
            cost: 5

    - slug: boss-guard
      source: mobdefinition.crypt-champion
      target:
        rooms:
          tags: [boss_approach]
      count: 1
      traits:
        guaranteed: [elite]
```

The exact field names can change during implementation, but the design
principles should hold:

- the layout is authored elsewhere
- the plan describes possible population
- the generated run stores actual population
- reconciliation does not reroll the run

## Room Tags And Placement Selectors

Guided placement needs a way to address room roles. WR2 currently has room
flags and room manifests. Spawn plans should support room selection by stable
authored metadata.

Acceptable first-pass options:

- use existing room flags where they already express placement behavior
- add a generic room `tags` or `roles` field to room manifests
- support path selectors where a dungeon path already captures authored flow

Recommended room role examples:

- `entrance`
- `hallway`
- `junction`
- `side_room`
- `treasure`
- `safe`
- `boss_approach`
- `boss`
- `no_load`
- `no_roam`

Selectors should be validated at import time when possible. A selector that
matches no rooms should be a warning or validation error depending on whether
the entry is required.

## Mob Traits And Modifiers

Mob traits are authored modifiers or behaviors applied to a spawned mob at
definition or placement generation time. This replaces the earlier draft term
`affixes`; import code may accept `affixes` as a transitional alias, but
manifests should export `traits`.

The dedicated mob trait architecture lives in
[mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md).

Phase 1 should keep spawn-plan traits simple:

- trait keys are authored strings
- placement stores the chosen keys
- mob spawn code receives chosen traits
- modifiers are persisted on the runtime entity
- combat/runtime systems interpret only supported modifier keys

Do not make traits an arbitrary formula language in the first pass. They
should be structured data with known operators and bounded effects.

Example generated placement state:

```json
{
  "entry_slug": "hallway-patrols",
  "slot_index": 3,
  "room_ref": "room@2,0,0",
  "source": "mobdefinition.drowned-guard",
  "traits": ["shielded"],
  "modifiers": {
    "health_max_multiplier": 1.2,
    "armor": 2
  }
}
```

Longer term, traits can become their own manifest-backed definition type if
they grow beyond spawn-local modifiers.

## Conditions

All new spawn plan conditions must use the WR2 structured condition DSL.

Examples:

```yaml
conditions:
  eq:
    - state.zone.north_control
    - orc
```

```yaml
conditions:
  all:
    - eq:
        - state.world.weather
        - storm
    - not:
        eq:
          - state.zone.crypt_cleansed
          - true
```

Do not add another predicate format for spawn plans. Existing
`Loader.conditions` and `Loader.loader_condition` can be migrated or bridged,
but they should not define the new manifest shape.

## Execution Flow

### Initial Generation

When a spawn world or dungeon instance starts:

1. Find active `SpawnPlan` rows for the root world/zone.
2. For each due plan, create or reuse a `SpawnPlanRun`.
3. Compute a deterministic seed from the configured seed scope.
4. Generate `SpawnPlacement` rows from entries, room selectors, budgets, and
   trait rules.
5. Reconcile placements into concrete mobs/items.

### Reconciliation

Reconciliation should be idempotent:

1. Lock the `SpawnPlanRun` row.
2. Load active placements.
3. For each placement, check whether the desired runtime entity exists by
   origin metadata.
4. Spawn missing entities.
5. Do not reroll source, room, or traits during ordinary reconciliation.

This supports reload behavior without losing the generated identity of the
dungeon instance.

### Reset

On dungeon reset:

1. Mark the current run inactive, expired, or reset.
2. Despawn or mark eligible runtime entities according to reset policy.
3. Generate a new run with a new seed if the seed scope calls for it.
4. Reconcile the new placements.

This gives each reset a fresh guided roll while keeping each active run stable.

## Locking And Runtime State

Runtime locks should be taken on runtime rows, not authored rows.

Preferred lock order:

1. spawn world
2. spawn plan run
3. placements or affected rooms
4. mobs/items as needed

The current global loader task lock can remain as operational protection during
transition, but correctness should come from row-level runtime locks and
idempotent origin keys.

## Relationship To WR2 Actions

The first implementation can call spawn reconciliation from existing world
start and loader task paths.

The target WR2 architecture should move toward explicit queued actions:

- `GenerateSpawnPlanRunAction`
- `ReconcileSpawnPlanRunAction`
- `ResetSpawnPlanRunAction`

Those actions should emit events such as:

- `SpawnPlanGenerated`
- `SpawnPlacementMaterialized`
- `SpawnPlanReset`

This keeps spawning aligned with WR2's Command -> Action -> Event direction
without requiring a full action queue refactor before the first manifest slice.

## Manifest Kind

Use `kind: spawnplan` during the transition.

This avoids confusing the clean new model with the old `Loader` model while both
systems exist. After old `Loader` and `Rule` are retired, the manifest kind can:

- keep `spawnplan` as the precise technical name
- add `loader` as a builder-facing alias
- or migrate fully to `loader` if that remains the clearest product language

The key requirement is that the new manifest kind maps to the new
`SpawnPlan`/`SpawnEntry` implementation, not the legacy `Loader`/`Rule` tables.

## Import And Export

World export should include path manifests alongside zones, rooms, spawn plans,
item definitions, mob definitions, abilities, quests, and triggers.

Import should:

- resolve `metadata.slug` within the selected world
- validate `spec.zone` as a portable `zone@<relative_id>` reference, not a zone
  name or database id
- validate path manifests with portable `metadata.ref: path@<relative_id>`
- validate spawn-plan path targets as portable `path@<relative_id>` references,
  not path names or database ids
- validate entry slugs are unique within the plan
- validate source refs
- validate room/path/entry target refs
- validate structured conditions through `condition_dsl`
- validate count ranges and budgets
- preserve stable entry slugs across updates

Delete manifests should support deleting a whole spawn plan. Entry deletion can
be represented by applying the plan with the desired entry list, or by a later
entry-level operation if needed.

## Migration From Legacy Loader/Rule

Add a migration command or service that converts common legacy shapes:

- `Loader` -> `SpawnPlan`
- `Rule` -> `SpawnEntry`
- `Rule.template` -> direct `source`
- room target -> fixed room target
- zone target -> room selector for that zone
- path target -> path selector
- rule target -> nested `target.entry`
- `num_copies` -> `count`
- `is_group` -> group behavior on the plan or entry
- `inherit_zone_wait` and `respawn_wait` -> `respawn_policy`
- legacy conditions -> structured conditions where conversion is safe

Unsafe or ambiguous legacy content should be reported, not silently converted.
Examples:

- invalid generic foreign keys
- old transformation-template rules
- condition strings that cannot be represented as structured conditions
- missing targets that used to imply legacy world-wide behavior

Migration should be repeatable and auditable. A dry-run mode should summarize
converted plans, skipped loaders, warnings, and unsupported rule shapes.

## Implementation Path

### Phase 0: Documentation And Agreement

- Land this architecture document.
- Confirm model names, manifest kind, and minimum first-pass feature set.
- Decide whether room tags/roles are needed before spawn plan manifests.

### Phase 1: Authored Models And Manifest Round Trip

- Add `SpawnPlan` and `SpawnEntry`.
- Add `kind: spawnplan` manifest parse/apply/export.
- Support direct sources for `mobdefinition`, `itemdefinition`, and `itembundle`.
- Support legacy source refs only for migration compatibility.
- Support fixed room, zone, path, and entry targets.
- Validate structured conditions through the shared condition DSL.
- Add WR2 tests under `backend/wr2_tests/`.
- Update builder/player guide docs for the new manifest shape.

### Phase 2: Runtime State And Simple Reconciliation

- Add `SpawnPlanRun` and `SpawnPlacement`.
- Generate deterministic placements for simple fixed targets.
- Reconcile placements into mobs/items.
- Add origin metadata to runtime mobs/items or a join table.
- Keep legacy Loader execution unchanged.

### Phase 3: Guided Dungeon Randomness

- Add room tag/role selectors.
- Add weighted source pools.
- Add count ranges and density profiles.
- Add difficulty budgets and basic placement constraints.
- Add trait/modifier selection and persistence.
- Add deterministic seeded test coverage.

### Phase 4: Lifecycle Integration

- Run spawn plans on world start and instance start.
- Add reset behavior for generated runs.
- Add periodic or scheduled reconciliation where needed.
- Move correctness to runtime row locks and origin idempotency.
- Keep old Loader task support for legacy worlds.

### Phase 5: Builder Experience And Migration

- Add builder UI surfaces for viewing and copying spawn plan YAML.
- Prefer spawn plans in new dungeon and zone authoring flows.
- Add a conversion command/service for legacy Loader/Rule data.
- Make old Loader/Rule screens read-only or clearly legacy where appropriate.

### Phase 6: Deprecation And Rename

- Stop creating old `Loader` and `Rule` rows from new builder flows.
- Convert remaining legacy content or mark it unsupported.
- Remove old runtime loader execution once no supported world depends on it.
- Rename the new implementation to the canonical loader vocabulary if desired,
  including reclaiming `Loader` / `Rule` as model names once the old tables and
  code paths are gone.

This final phase is important. `SpawnPlan` and `SpawnEntry` are transitional
names, not permanent parallel concepts. The end state should have one primary
loader/spawn authoring system.

## Open Questions

- Should room tags live on `Room`, `RoomDetail`, a separate relation, or only in
  manifest/runtime JSON at first?
- Should reusable mob traits become their own authored definition type, or stay
  local to mob definitions and spawn plans until repeated configs justify it?
- Should generated placements be one row per desired entity, or one row per
  entry plus a JSON list of selected slots?
- Should spawn plan reset be tied to zone reset, instance lifecycle, explicit
  commands, or all of the above?
- How much of legacy `Loader.conditions` can be automatically converted to
  structured conditions?
- What builder-facing name should replace "Loader" during the transition:
  Spawn Plan, Population Plan, Encounter Plan, or Loader v2?
