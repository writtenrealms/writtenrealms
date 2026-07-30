# WR2 Spawn Plan System

Status as of 2026-07-06: `SpawnPlan`, `SpawnEntry`, `SpawnPlanRun`, and
`SpawnPlacement` are the canonical WR2 population system.

Legacy loader/rule rows are not a WR2 runtime or builder concept. WR1 export
scripts should translate those rows directly into `kind: spawnplan` manifests.
WR2 should not reintroduce loader/rule import paths, API endpoints, runtime
foreign keys, or builder screens.

## Core Model

A spawn plan is authored content. It describes how a world, zone, room, or path
can be populated.

A spawn-plan run is runtime content. It records the concrete placements
generated for one spawn world, dungeon instance, or reset.

Generation and reconciliation are separate steps:

1. Resolve active `SpawnPlan` rows for the target world/zone/room/path.
2. Create or reuse a `SpawnPlanRun` with a stored seed.
3. Generate deterministic `SpawnPlacement` rows.
4. Reconcile concrete mobs/items from placements without rerolling the plan.
5. Store runtime timestamps on spawn-plan runtime state and
   `World.last_spawn_plan_run_ts`, not authored entities.

For an ordinary running world, an authored spec-hash change is reconciled into
the active run rather than replacing all placement rows. Logical slots retain
their database identity, newly desired slots are added, and no-longer-desired
slots are marked retired. This is important because runtime mob and item links
use `SET_NULL`: deleting and regenerating every placement would orphan live
output and create duplicates.

The run stores per-entry count outcomes and hashes for independent source,
target, trait, placement, loot, and condition dimensions. Its base seed remains
immutable. Consequently, increasing an entry's count does not reroll the
source, room, or traits of its existing slots, and changing one entry does not
perturb another entry's random stream.

## Manifest Contract

Spawn plans are authored through `kind: spawnplan` YAML.

Supported source families should remain definition-backed:

- `mobdefinition.<slug>`
- `itemdefinition.<slug>`
- `itembundle.<slug>`

Supported target families should use portable manifest refs where possible:

- `world`
- `zone@<relative_id>`
- `room@<x>,<y>,<z>`
- `path@<relative_id>`

Builder APIs may expose database IDs for inspection, but import/export should
prefer portable refs so manifests round-trip across fresh databases.

## Runtime Rules

- Authored spawn plans do not store run timestamps.
- Runtime rows do not keep source template foreign keys.
- Runtime rows should point back to spawn-plan placements when provenance is
  needed.
- Reconciliation should create missing runtime entities and avoid duplicating
  existing live placements.
- Running-world plan edits should upsert deterministic slots by stable entry and
  slot identity. Existing output satisfies an edited slot until it leaves, so
  changing a mob slot to an item slot cannot create both at once.
- Retired placements are excluded from materialization. Their existing output
  is not forcibly destroyed, which avoids interrupting combat or deleting
  carried items.
- An authored edit may materialize newly added or changed slots before the
  ordinary respawn deadline, but it must not refill unrelated missing slots
  early.
- Active instance runs retain their initial spawn-plan snapshot. Template edits
  pause reconciliation for the stale plan in that active run and apply when a
  future instance run performs initial population.
- Initial population and deliberate repopulation belong to instance/world
  lifecycle services. Spawn plans have no separate reset policy; ordinary
  replacement timing belongs to their respawn policy.
- `/repop` is deliberate, zone-scoped reconciliation. It bypasses respawn mode
  and deadline checks but retains live-output deduplication, active-plan and
  condition checks, no-roam safety, and active-instance snapshot protection.
  Doors are unchanged unless `--doors` is supplied; that option resets
  materialized runtime doorway states in the selected zone to their authored
  defaults. Neither form consumes the separate authored-zone door-reset timer.
- Conditional logic must use the WR2 condition DSL in
  `backend/core/condition_dsl.py`.

## Builder Surface

Current builder-facing flows are:

- apply `kind: spawnplan` manifests through **World > Edit World**
- inspect room-scoped plans through room **Spawn Plans**
- inspect zone-scoped plans through zone **Spawn Plans**
- export/import spawn plans as YAML manifests
- force missing-placement reconciliation in the current runtime zone with
  `/repop`, optionally including runtime doorways with `/repop --doors`

Do not add builder screens or serializers around removed loader/rule tables.

## WR1 Export Notes

WR1 loader/rule export remains a one-way translation concern:

- WR1 loader identity becomes spawn-plan `metadata.slug` / `metadata.name`.
- WR1 rule rows become entries under `spec.entries`.
- WR1 source item/mob templates become `itemdefinition` / `mobdefinition` refs.
- WR1 room/path targets become portable WR2 room/path refs where available.
- Unsupported condition strings should be reported as exporter warnings unless
  they can be expressed in the WR2 condition DSL.

The canonical WR1 export checklist lives in
[yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md).
