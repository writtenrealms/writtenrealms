# Spawn Plan Builder Guide

Spawn plans control what appears in a zone when a world or instance is running.
Use `kind: spawnplan` YAML in **Zone > Loads** or **World > Edit** to
create or update them.

A spawn plan does not create rooms or exits. Build the zone layout with `zone`
and `room` manifests first, add any path groupings with `path` manifests, then
use a spawn plan to decide which mobs or items populate that layout.

## Basic Shape

```yaml
kind: spawnplan
metadata:
  slug: training-grounds
  name: Training Grounds
spec:
  zone: zone@1
  respawn:
    mode: fixed
    seconds: 60
  entries:
    - slug: practice-dummy
      source: mobdefinition.practice-dummy
      target:
        room: room@1,0,0
      count: 1
```

`metadata.slug` is the stable id for updates. Applying another manifest with
the same slug updates the existing plan.

`spec.zone` is the authored zone the plan belongs to. It must use the portable
`zone@<relative_id>` form exported by zone manifests and shown on the zone
detail API as `manifest_ref`. Zone names are not accepted here because names
are not guaranteed to be unique. Database zone ids are also not accepted in
spawn-plan manifests because they are not portable across prod, dev, exports,
or imports.

`spec.entries` is the desired population list. Applying a plan replaces the
entry list with exactly the entries in the manifest, so remove an entry from the
YAML when it should stop spawning.

## Sources

Each entry needs either `source` or `source_pool`.

Use a single source when the entry should always spawn the same authored thing:

```yaml
source: mobdefinition.tidecaller-trainer
```

Supported source refs:

- `mobdefinition.<slug>`
- `mobtemplate.<slug>` for transitional legacy mobs
- `itemdefinition.<slug>`
- `itemtemplate.<slug>` for transitional legacy items
- `itembundle.<slug>`

Use `source_pool` when each generated slot should roll from a weighted list:

```yaml
source_pool:
  - ref: mobdefinition.skeleton-guard
    weight: 70
  - ref: mobdefinition.crypt-scout
    weight: 30
```

Weights are relative. A weight of `70` is not a percent by itself; it means
that entry is 70 parts of the total pool weight.

## Targets

A target says where the spawned copy should appear.

Spawn in one fixed room:

```yaml
target:
  room: room@2,0,2
```

Spawn somewhere eligible in the plan's zone or another zone:

```yaml
target:
  zone: zone@1
```

Spawn on a path:

```yaml
target:
  path: path@4
```

Path names are not accepted in spawn-plan targets because they are not
guaranteed to be unique. Use the path's `manifest_ref` value from the path
detail screen or path API response.

## Path Manifests

Use `kind: path` to define the authored room groupings that path-targeted
spawn entries can use:

```yaml
kind: path
metadata:
  ref: path@4
  name: Patrol Loop
spec:
  zone: zone@1
  notes: Harbor patrol route.
  entry_room: room@1,0,0
  max_per_room: 2
  max_per_path: 5
  rooms:
    - room@1,0,0
    - room@2,0,0
```

`metadata.ref` is the portable path identity. It uses the same relative-id
approach as zones: the value only has to be unique within the world, so exports
can import into another database without matching database ids.

Spawn inside or on the same room as another entry by using that entry slug:

```yaml
entries:
  - slug: chest
    source: itemdefinition.iron-chest
    target:
      room: room@3,0,0
    count: 1
  - slug: chest-loot
    source: itembundle.training-loot
    target:
      entry: chest
    count: 2
```

Current first-pass placement supports fixed room, zone, path, and entry targets.
Room tag selectors and deeper spacing rules are planned for guided dungeons but
are not part of the first implementation.

## Counts And Density

Use an integer for a fixed count:

```yaml
count: 3
```

Use `min` and `max` for guided variation:

```yaml
count:
  min: 4
  max: 7
```

The generated run stores the chosen count and placements. Ordinary
reconciliation fills missing copies for those placements; it does not reroll the
whole plan every tick.

## Respawn

`spec.respawn` controls how often missing placements are refilled.

```yaml
respawn:
  mode: fixed
  seconds: 60
```

Supported modes:

- `fixed`: use `seconds`.
- `inherit_zone`: use the zone respawn time when `seconds` is omitted.
- `none`: do not refill missing copies during ordinary loader ticks.

`seconds: 0` means a missing copy can be replaced on every reconciliation. Use
that only for content that should always be present.

## Guided Randomness

For a fixed-layout dungeon, keep rooms stable and add variation in the spawn
plan:

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
      affixes:
        chance: 35
        pool:
          - key: shielded
            weight: 40
            modifiers:
              armor: 2
          - key: frenzied
            weight: 35
            modifiers:
              attack_power_multiplier: 1.15
```

`randomization.seed_scope` controls repeatability:

- `instance`: each spawn world gets its own deterministic roll.
- `world`: every spawn world for the authored world uses the same roll until
  the plan changes.
- `explicit`: use `randomization.seed` for repeatable tests.

Affixes are stored on the generated placement and copied to spawned mobs/items
in `roll_metadata.spawn_plan`.

The first implementation also applies simple numeric modifiers to spawned
mobs/items when the key names a supported runtime field. A direct key adds to
the field, such as `armor: 2`. A key ending in `_multiplier` multiplies the base
field, such as `health_max_multiplier: 1.2` or
`attack_power_multiplier: 1.15`. Unsupported modifier keys are still preserved
as metadata for later systems, but they do not change combat by themselves.

## Conditions

Use `conditions` on a plan or on an entry when spawning should depend on world
state. Conditions use the WR2 structured condition DSL, the same condition
format used by other new WR2 systems.

```yaml
conditions:
  all:
    - eq: [world.state.chapter, 2]
    - ne: [world.state.crypt_sealed, true]
```

Do not use legacy condition strings in new spawn plans.

## Delete

Delete a whole spawn plan with:

```yaml
kind: spawnplan
operation: delete
metadata:
  slug: training-grounds
```

To delete one entry, apply the same plan without that entry.

## Builder UI Workflow

In the builder, open a zone and choose **Loads**. This screen lists the zone's
spawn plans. Choosing a plan opens its canonical YAML in an editor; saving that
YAML applies the manifest through the same manifest ingestion path used by
**World > Edit**.

Use **Add** on **Zone > Loads** to start a new spawn plan for the current zone.
The generated template includes the zone's `zone@<relative_id>` reference.

## Import And Export

World export includes spawn plans as `kind: spawnplan` documents. Reapplying the
export recreates the authored plans and entries.

Transition-only fields such as `metadata.legacy`, `spec.legacy`,
`source_legacy_ref`, or target display names are ignored by the runtime. They
can stay in local migration YAML for auditability, but spawn-plan ingestion
does not depend on the old ids.
