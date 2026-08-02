# WR2 Spawn Plan System

Status as of 2026-07-31: `SpawnPlan`, `SpawnEntry`, `SpawnPlanRun`, and
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

An authored `SpawnEntry` stores its target through exactly one of
`target_room`, `target_zone`, `target_path`, or `target_entry`. These foreign
keys are the database source of truth; there is no duplicate JSON locator to
drift out of sync. Room, zone, and path targets must belong to the plan's
authored world. An entry target must belong to the same spawn plan and have a
lower authored order so its placement is generated first. When the dependent
entry is active, its target entry must also be active.

## Manifest Contract

Spawn plans are authored through `kind: spawnplan` YAML.

Supported source families should remain definition-backed:

- `mobdefinition.<slug>`
- `itemdefinition.<slug>`
- `itembundle.<slug>`

Every entry has exactly one target. Canonical manifests express the closed
target union as one typed scalar:

- `room@<relative_id>`
- `zone@<relative_id>`
- `path@<relative_id>`
- `entry.<slug>`

For example:

```yaml
entries:
  - slug: patrol-leader
    source: mobdefinition.guard-captain
    target: path@4
  - slug: patrol-guard
    source: mobdefinition.guard
    target: entry.patrol-leader
```

`entry.<slug>` is plan-local: it identifies one other entry by its
`spec.entries[].slug`, not a world-wide resource. The reference type determines
placement semantics, including zone/path roaming and entry-parent placement;
no separate target type or display name is required.

Import accepts the former mapping forms keyed by `room`, `room_ref`, `zone`,
`path`, `entry`, or `parent_entry`, then resolves them to the same relational
target fields. Those mappings are compatibility aliases, not canonical output.
Import must reject a legacy mapping containing multiple target kinds instead
of selecting one by key precedence. Export always reads the target foreign key
and emits one scalar portable ref.

Builder APIs may expose database IDs for inspection, but import/export should
use portable refs so manifests round-trip across fresh databases. Relative ids
for rooms, zones, and paths are independent of destination database keys. A
room's relative id is also immutable and independent of its mutable
coordinates, so moving the room does not change its target foreign key or
exported reference. Legacy `room@x,y,z` and `room.<database_pk>` values are
accepted only as import aliases, resolved inside the selected authored world,
and rewritten by canonical export as `room@<relative_id>`.

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
- Each WR1 rule emits exactly one scalar WR2 target. Room, zone, and path
  destinations become portable `room@`, `zone@`, or `path@` refs; a dependency
  on another converted entry becomes its plan-local `entry.<slug>` ref.
- Unsupported condition strings should be reported as exporter warnings unless
  they can be expressed in the WR2 condition DSL.

The canonical WR1 export checklist lives in
[yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md).
