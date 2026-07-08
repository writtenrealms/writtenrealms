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
- Reset behavior belongs to spawn-plan policy and instance/world lifecycle
  services, not ad hoc command handlers.
- Conditional logic must use the WR2 condition DSL in
  `backend/core/condition_dsl.py`.

## Builder Surface

Current builder-facing flows are:

- apply `kind: spawnplan` manifests through **World > Edit World**
- inspect room-scoped plans through room **Spawn Plans**
- inspect zone-scoped plans through zone **Spawn Plans**
- export/import spawn plans as YAML manifests

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
