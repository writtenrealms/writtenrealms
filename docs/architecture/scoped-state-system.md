# WR2 Scoped State System

## Purpose

Scoped state is the canonical WR2 concept for sparse, mutable gameplay values
owned by a world, zone, room, or character.

It replaces the conceptual role of WR1 world `facts`, player `marks`, and zone
`zone_data` without carrying those separate nouns into new systems. It is
implemented across runtime commands, conditions, templates, effects, manifests,
spawn plans, and instance resets.

Related docs:

- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [instance-system.md](/Users/teebes/code/writtenrealms/docs/architecture/instance-system.md)
- [state-builder-guide.md](../guides/builders/state-builder-guide.md)
- [ambient-command-issuers-plan.md](/Users/teebes/code/writtenrealms/docs/architecture/ambient-command-issuers-plan.md)

## Core Model

State has three parts:

- a scope, which says who owns the value
- a key, which identifies the value
- a JSON-compatible value

The builder-facing scopes are:

| Scope | Owner | Lifetime |
| --- | --- | --- |
| `world` | One exact runtime world | Until that runtime world is deleted or reset |
| `zone` | A zone inside one exact runtime world | Until that runtime world is deleted or reset |
| `room` | A room inside one exact runtime world | Until that runtime world is deleted or reset |
| `character` for a player | The player | Follows the player across base worlds and instance runs |
| `character` for a mob | The spawned mob | Deleted with that mob |
| `quest` | One active quest instance | Owned by the quest runtime |

Examples:

- `state.world.season = "winter"`
- `state.zone.north_control = "orc"`
- `state.room.lever_pulled = true`
- `state.character.met_king = true`

The public scope is `character` for both players and mobs. Builders and content
do not need a second `mob_state` vocabulary.

## Authored Defaults And Live State

Authored content stores `initial_state`. A running game stores current `state`.
They are deliberately different surfaces.

Use `spec.initial_state` on `kind: world`, `kind: zone`, and `kind: room`:

```yaml
kind: world
spec:
  initial_state:
    weather: clear
    invasion_active: false
---
kind: zone
metadata:
  ref: zone@1
  name: Harbor District
spec:
  initial_state:
    fog_level: 2
---
kind: room
metadata:
  ref: room@4,2,0
  name: Prison Cell
spec:
  zone: zone@1
  initial_state:
    cell_door_open: false
```

These mappings are seeds:

- creating a runtime world copies the authored defaults into runtime state
- applying a manifest changes future seeds, not current live values
- an instance reset discards the run's world/zone/room values and reseeds them
- missing keys are absent; the runtime does not dynamically fall through to
  an authored row

Copying the seed gives a runtime a stable snapshot. A builder can edit a
template without silently changing a playthrough already in progress.

## Base Worlds, Instance Templates, And Runs

WR2 distinguishes three concepts:

| Concept | State responsibility |
| --- | --- |
| Authored base world | Owns defaults for ordinary runtime worlds |
| Authored instance template | Owns its own defaults for instance runs |
| Spawned runtime world | Owns the live world, zone, and room values for one running context |

`/state world`, `state.world.*`, and the equivalent zone/room surfaces always
resolve from the exact current runtime world.

Inside an instance:

- `state.world.*` is instance-run-local
- zone and room values are also instance-run-local
- two parallel runs of one template never share mutable values
- the base world's live values are not an implicit parent or fallback
- the template's defaults are not a hidden live scope

An instance template does not inherit the base world's `initial_state`.
Builders author any initial values required by the instance explicitly. If WR2
later needs mutable values shared across every run, that should be a new,
explicitly named scope with explicit access rules. It must not be implemented
as an invisible base-world lookup.

## Character Ownership

Player and mob state intentionally have different persistence while sharing one
builder-facing scope.

Player state:

- belongs to the player aggregate
- follows that player into and out of instances
- survives ordinary runtime-world teardown
- is preserved by an instance reset

Mob state:

- belongs to one spawned mob
- can be read as `state.character.*` when that mob is the actor or target
- is deleted when the mob is deleted
- is freshly seeded when a new mob materializes

This lets a trigger treat players and mobs uniformly without making mob state
accidentally permanent.

## Mob Initial State

A mob definition may seed every new copy:

```yaml
kind: mobdefinition
metadata:
  slug: greek-captive-commander
  name: a Greek commander
spec:
  initial_state:
    captive: true
```

A spawn entry may add or override values for a particular placement:

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

The mapping is valid only when every source the entry can select is a mob
definition. Item entries and mixed mob/item pools cannot have character state.

Materialization rules:

- each new mob receives an independent copy
- definition values are merged first and entry values override matching keys
- a respawned mob receives the seed again because it is a new mob
- reconciliation never overwrites a surviving mob's current state
- editing definition or entry `initial_state` affects future materializations,
  not live mobs
- resetting an instance deletes its mobs, reruns initial population, and
  therefore gives replacement mobs their authored initial state

## Runtime Access

The state service is the single storage boundary for reads and mutations. It
resolves public scope plus owner into the appropriate per-scope table and keeps
runtime-world context explicit for zone and room operations.

Supported operations include:

- read one key
- snapshot one owner's mapping
- set or replace a value
- clear a key
- increment a numeric value

Mutations lock the one owner row involved. Callers should not manipulate state
JSON directly because that would bypass ownership resolution, versioning, and
concurrency rules.

Builder commands use the same service:

```text
/state show world
/state set room lever_pulled true
/state add character self warning_count 1
/state set character mob.42 captive false
```

Room, zone, and world ambient issuers are limited to scopes they own. Character
targets may be a player or mob resolvable in the current runtime context.

## Conditions And Templates

The shared condition DSL exposes state paths:

```yaml
conditions:
  all:
    - eq: [state.world.weather, stormy]
    - eq: [state.room.lever_pulled, true]
    - ne: [state.character.captive, true]
```

The same ownership rules apply while rendering:

```text
The weather is {{ state.world.weather }}.
The lever is {{ state.room.lever_pulled }}.
```

If the actor is a mob, `state.character` resolves to that mob. If the actor is a
player, it resolves to the player. Zone and room resolution carries the current
runtime world so two instance runs cannot see each other's rows.

Quest-local `state.quest` remains owned by the quest runtime. It participates
in the same condition and effect vocabulary but is not stored in the
world/zone/room/character tables.

## Storage

State uses dedicated per-scope tables rather than one generic polymorphic
table:

- `World.initial_state`, `Zone.initial_state`, and `Room.initial_state`: authored
  seed mappings
- `WorldState`: one live mapping for a spawned runtime world
- `ZoneState`: one mapping for `(runtime_world, authored_zone)`
- `RoomState`: one mapping for `(runtime_world, authored_room)`
- `CharacterState`: one mapping for a player
- `MobState`: one mapping for a mob

Authored seed values do not live in the runtime tables. This keeps the template
and live concepts visibly separate in both the model layer and service API.

The composite zone and room ownership is essential. Authored zone and room rows
can be reused by multiple spawned worlds, so a state row keyed only by zone or
room would leak values between ordinary worlds or parallel instance runs.

Each row contains:

- owner foreign key or keys
- a sparse JSON mapping
- a monotonically increasing version
- an update timestamp

Foreign-key deletion gives the desired lifetime:

- deleting a runtime world deletes its world/zone/room state
- deleting a mob deletes its mob state
- deleting a player deletes its player state

## Performance And Concurrency

The common access pattern is one owner lookup, not a global query by arbitrary
key. The implementation therefore favors:

- sparse rows, created only when an owner has state
- composite uniqueness and lookup indexes for `(world, zone)` and
  `(world, room)`
- bounded seed copying when a runtime world starts
- row-level locking for mutations
- no per-read fallback query into authored defaults
- no eager loading of state for every room, mob, or player when a command does
  not need it

State should not become an entity-attribute-value query system. If gameplay
needs a globally searchable hot field, use a dedicated indexed column or
projection rather than scanning JSON state under concurrent load.

## State Versus Other Systems

Use state for mutable, sparse gameplay values:

- a lever has been pulled
- a captive has been freed
- an invasion is active
- a character remembers a conversation

Do not use state for:

- stable canonical fields that deserve relational columns
- authored definitions
- room flags such as `peaceful` or `no_roam`
- derived caches or broad serialization buckets
- active combat effects with duration and stacking semantics
- currency balances

Traits and state are also distinct:

- a trait describes authored identity, capability, behavior, or a modifier
- state records a mutable value that gameplay may change

For example, a placement trait such as `boss` can identify a completion cohort.
`captive: true` should be state because freeing the mob changes it.

## Values And Keys

State values must be JSON-compatible and should remain small. Prefer scalars:

- boolean
- integer
- float
- string
- null

Small lists or mappings are available when a concrete system needs them, but a
large or frequently queried structure usually deserves its own model.

Keys should be stable lowercase `snake_case`:

- `north_control`
- `met_king`
- `lever_pulled`
- `invasion_active`

Avoid vague keys such as `data1`, `status`, or `value`.

## Compatibility And WR1 Conversion

Legacy concepts have these conversion targets:

- authored world facts -> world `initial_state`
- authored zone data -> zone `initial_state`
- player marks conceptually correspond to player character state, but are not
  part of authored-world conversion

New code and authored content use only `state` and `initial_state`.

The optional WR1 authored-world converter may translate authored default world
facts and zone data into the corresponding manifest `initial_state` mappings
when their semantics are clear. It must not export:

- player marks
- current live facts
- current live zone or room mutations
- runtime mobs or their state
- quest progress or any other runtime record

WR2 launches with a clean database, so this compatibility direction is for
optional authored-content conversion, not production runtime migration or
dual-write support.

## Reset Invariants

An instance reset:

1. keeps the same run and active participants
2. preserves player inventory, equipment, and player character state
3. clears only that runtime world's world/zone/room state
4. reseeds those scopes from the instance template's `initial_state`
5. deletes and rematerializes spawned mobs, so mob state starts from the spawn
   entry seed
6. leaves every other active run unchanged

These invariants are part of the ownership model, not a best-effort cleanup
optimization.

## Design Constraints

Future state work must preserve these rules:

- one public noun: `state`
- explicit owner scope
- authored `initial_state` distinct from current runtime values
- exact runtime-world ownership for world, zone, and room
- player state follows the player
- mob state dies with the mob
- no implicit base-world shared state inside instances
- typed conditions and effects through the shared WR2 condition framework
- per-scope relational ownership rather than a generic polymorphic owner
- bounded, indexed access suitable for hundreds or thousands of concurrent
  players
