# Spawn Plan Builder Guide

Spawn plans control what appears in a zone when a world or instance is running.
Use `kind: spawnplan` YAML in **Zone > Spawns** or **World > Edit** to
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

Spawn plans do not have a `reset` setting. World and instance lifecycle
services perform initial population when they start. Use `spec.respawn` to
control replacement of missing placements while a world is running. Legacy
manifests containing `spec.reset` are accepted for import compatibility, but
the key is ignored and omitted from exported YAML.

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
- `itemdefinition.<slug>`
- `itembundle.<slug>`

When the spawn plan is authored on an instance template, these refs resolve
against the base world's definitions and bundles. The plan's targets still
resolve inside the instance template, so copy `zone@`, `room@`, and `path@`
values from the instance template's own zone, room, and path screens.

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

## Initial Mob State

Use `entries[].initial_state` when every mob created for that placement should
begin with mutable character state. Put `spec.initial_state` on the mob
definition instead when every copy of the definition should use the values.

```yaml
kind: spawnplan
metadata:
  slug: camp-spawns
  name: Camp Spawns
spec:
  zone: zone@3
  respawn:
    mode: none
  entries:
    - slug: greek-commander
      source: mobdefinition.greek-captive-commander
      target:
        room: room@4,2,0
      count: 1
      initial_state:
        captive: true
```

`initial_state` is accepted only when every possible source for the entry is a
mob definition. It is rejected for item definitions, item bundles, and mixed
source pools.

Each newly materialized or respawned mob receives its own copy. The state can
then change through `/state`, triggers, quests, abilities, or other typed state
effects. Definition state is merged first and the spawn entry overrides
matching keys. Editing or reapplying a spawn plan never overwrites the current
state of a surviving mob.

Use state for mutable facts such as `captive`, `alerted`, or
`conversation_stage`. Use traits for authored capabilities, modifiers, and
placement identity. A state key is not a substitute for a stable trait, and a
trait should not be used merely to hold a mutable boolean.

## Spawn-Specific Loot

Mob definitions can define loot that applies to every copy of that mob. A
spawn-plan mob entry can add or replace loot for mobs produced by that
particular entry.

```yaml
kind: spawnplan
metadata:
  slug: sewer-population
  name: Sewer Population
spec:
  zone: zone@1
  entries:
    - slug: sewer-rats
      source: mobdefinition.cave-rat
      target:
        path: path@2
      count: 6
      loot:
        inherit_definition: true
        entries:
          - slug: sewer-key
            source: itemdefinition.rusty-sewer-key
            probability: 5
```

`inherit_definition` defaults to `true`, so the mob keeps its definition loot
and also gets the spawn-entry loot. Set it to `false` when this spawn-plan
version should use only the entry loot:

```yaml
loot:
  inherit_definition: false
  entries:
    - slug: event-token
      source: itemdefinition.event-token
      probability: 100
```

Spawn-entry loot uses the same `entries`, `source`, `source_pool`,
`probability`, `quantity`, and `conditions` fields as mob-definition loot.

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

For mob entries, a zone target also becomes the mob's roaming area. The mob
spawns in one eligible room in that zone, then can wander to adjacent rooms
inside the same zone on heartbeat ticks. Rooms flagged `no_roam` are excluded
both at spawn time and while wandering.

Spawn on a path:

```yaml
target:
  path: path@4
```

Path names are not accepted in spawn-plan targets because they are not
guaranteed to be unique. Use the path's `manifest_ref` value from the path
detail screen or path API response.

For mob entries, a path target also becomes the mob's roaming path. The mob can
only wander to adjacent rooms that belong to that path. If the path has an
eligible `entry_room`, the initial placement uses that room. An `entry_room`
flagged `no_roam` is not eligible for a path-targeted mob, so WR2 chooses from
the path's other eligible rooms instead.

For both zone and path targets, `no_roam` is also checked when a missing mob is
repopulated. If a room is flagged after its deterministic placement was
generated, WR2 keeps the placement but skips loading the mob there. Removing
the flag makes that placement eligible again. The check is shared across the
whole world reconciliation, so it does not add one room-flag query per mob.

A fixed room target is static:

```yaml
target:
  room: room@2,0,2
```

Mobs loaded into a specific room do not roam by default. Ambient roam chance is
configured on the world manifest with `default_roam_chance`, which defaults to
`10` percent per heartbeat. An explicit room target remains allowed when that
room is flagged `no_roam`; the flag only excludes zone- and path-targeted mobs.

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

## Cohorts And Patrols

Use a cohort when multiple mobs should spawn and roam as one patrol unit. Give
each mob entry the same `cohort` value, mark one mob as the `leader`, and have
the follower entries target the leader entry.

```yaml
kind: spawnplan
metadata:
  slug: harbor-patrols
  name: Harbor Patrols
spec:
  zone: zone@1
  respawn:
    mode: fixed
    seconds: 300
  entries:
    - slug: sparabara
      source: mobdefinition.sparabara
      target:
        path: path@4
      count: 1
      cohort: west-harbor-patrol
      cohort_role: leader
      cohort_policy: refill_missing

    - slug: archer
      source: mobdefinition.harbor-archer
      target:
        entry: sparabara
      count: 1
      cohort: west-harbor-patrol
      cohort_role: follower
      cohort_policy: refill_missing
```

The leader's `path` or `zone` target is inherited by follower mobs that target
the leader entry, so the archer above roams on `path@4` even though its direct
target is `entry: sparabara`. If the leader targets a fixed room, the cohort
spawns together but stays static.

`cohort` is the authored patrol name. It is not the runtime group id; each
generated cohort slot gets its own runtime group id so two copies of the same
patrol template do not merge.

`cohort_role` can be `leader`, `follower`, or `member`. Mark one mob as
`leader` for predictable roaming. If no live leader exists, the runtime picks
the first surviving member as the temporary anchor.

Follower entries must target another entry with `target.entry`. The target
entry must be active and have a lower `order` than the follower, because the
runtime generates parent placements before child placements. When both entries
declare a `cohort`, the cohort values must match. A cohort template can have
only one leader entry; increase that leader entry's `count` to create multiple
copies of the same patrol.

`cohort_policy` currently supports `refill_missing`, which is also the default.
When a cohort is due to respawn:

- If at least one member is still alive, missing members spawn at the leader's
  current room, or at another surviving member's room if the leader is dead.
- If the whole cohort is dead, all members respawn at their original generated
  placement.
- Surviving members are not despawned or reset just because another member is
  missing.

On heartbeat roaming, the cohort rolls once using the leader's roam chance. If
any live cohort member is in active combat, the whole cohort stays put for that
heartbeat. Otherwise, the leader picks the direction, and live members in the
leader's current room move with the leader when that destination is valid for
their path or zone. Members that are already separated stay where they are; the
next `refill_missing` respawn can restore dead members to the surviving patrol.

For multiple copies of the same patrol, increase the leader entry's `count`.
Follower entries targeting the leader are generated once per leader placement.
For example, a leader count of `3` and one follower entry creates three separate
two-mob patrols, not one six-mob group.

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

- `fixed`: use the non-negative integer in `seconds`; an omitted value is
  normalized to `0`.
- `inherit_zone`: use the zone respawn time when `seconds` is omitted.
- `none`: do not refill missing copies during ordinary spawn-plan reconciliation.

`seconds: 0` means a missing copy can be replaced on every reconciliation. Use
that only for content that should always be present. `none` must not include a
`seconds` value.

These mode names are strict. `never` is not an alias for `none`, and malformed
policies, unknown modes, unsupported fields, non-integer seconds, and negative
seconds are rejected when the manifest is saved.

### Editing A Running World

Saving a spawn-plan change updates ordinary running worlds on the next
spawn-plan scheduler pass, normally within about 15 seconds. The save request
does not synchronously rebuild every spawned world; the existing background
reconciler detects the changed plan hash and applies the update with bounded
work per world.

Live edits are rolling and non-destructive:

- Adding an entry or increasing its count creates the new logical slots on the
  next pass. New slots are populated once even when the plan uses
  `respawn.mode: none` or its ordinary respawn deadline has not arrived.
  Existing randomized slots retain their source, room, and traits when only the
  count changes.
- Existing logical slots keep their placement identity and live output. Editing
  a source, target, traits, initial state, or loot does not kill or move a mob
  in combat and does not delete an item a player may be carrying. The new
  settings take effect when that slot next needs to be materialized.
- Cohort membership metadata is refreshed during reconciliation so newly added
  followers can join a surviving patrol without duplicating its leader.
- Removing or disabling an entry, or decreasing its count, retires the excess
  slots. Existing mobs or items may finish their natural lifecycle, but retired
  slots are never refilled. Re-adding a still-live slot reuses it instead of
  creating a duplicate.
- Entry slugs are the stable live-edit identity. Renaming a slug is treated as
  removing the old entry and adding a new one.

Active dungeon instances keep the population snapshot generated when that run
started. Once an edited template differs from that snapshot, reconciliation for
that plan is paused in the active instance; the new spec applies to future runs
without changing a completion cohort in progress. Worlds configured with
`never_reload` are also excluded from scheduled reconciliation and require a
restart to pick up edits.

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
      traits:
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
- `world`: every spawn world for the authored world uses the same deterministic
  rolls.
- `explicit`: use `randomization.seed` for repeatable tests.

Random streams are independent by entry, slot, and dimension. Editing a count
does not reroll existing sources, rooms, or traits, and editing one entry does
not perturb the rolls of another. Changing the corresponding source, target,
traits, or randomization configuration intentionally produces a new roll for
that dimension. Changing `initial_state` changes the seed used by a future
materialization; it does not reroll anything or mutate a surviving mob.

Traits are stored on the generated placement and copied to spawned mobs in
`trait_instances` and `roll_metadata.spawn_plan`.

The first implementation also applies simple numeric modifiers to spawned
mobs/items when the key names a supported runtime field. A direct key adds to
the field, such as `armor: 2`. A key ending in `_multiplier` multiplies the base
field, such as `health_max_multiplier: 1.2` or
`attack_power_multiplier: 1.15`. Unsupported modifier keys and behavior trait
params are still preserved as metadata for later systems, but they do not
change combat by themselves.

The older draft field name `affixes` is accepted as an import alias during the
transition. New manifests should use `traits`, and exported spawn plans use
`traits`.

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

In the builder, open a zone and choose **Spawns**. This screen lists the
zone's spawn plans. Choosing a plan opens its canonical YAML in an editor;
saving that YAML applies the manifest through the same manifest ingestion path
used by **World > Edit**.

Use **Add** on **Zone > Spawns** to start a new spawn plan for the current
zone. The generated template includes the zone's `zone@<relative_id>`
reference.

## Import And Export

World export includes spawn plans as `kind: spawnplan` documents. Reapplying the
export recreates the authored plans and entries.

Transition-only fields such as `metadata.legacy`, `spec.legacy`,
`source_legacy_ref`, or target display names are ignored by the runtime. They
can stay in local migration YAML for auditability, but spawn-plan ingestion
does not depend on the old ids.
