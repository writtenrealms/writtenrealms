# WR2 Scoped State System (World / Zone / Room / Character)

## Purpose

This document describes the desired replacement for WR1-era mutable local data
concepts such as:

- world `facts`
- player `marks`
- zone `zone_data`

The goal is to define a single WR2 concept that can cover local mutable state
at multiple scopes without inventing a different noun for each scope.

This is a future architecture target. It is intentionally directional and
replacement-oriented. It does not require immediate implementation.

Reference docs:

- `.codex/skills/wr-transition/wr2-architecture.md`
- `docs/architecture/ambient-command-issuers-plan.md`
- `docs/quest-system-roadmap.md`
- `docs/quest-system-endstate.md`
- `docs/yaml-manifest-system.md`

## Current Problem

Today the repository uses different words for the same general pattern:

- `World.facts` stores sparse world-level key/value state
- `spawns.Mark` stores sparse player-level key/value state
- `Zone.zone_data` stores sparse zone-level key/value state
- room `flags` store a separate set of enumerated room tags

This creates several problems:

- the vocabulary is inconsistent
- the names do not scale cleanly to more scopes
- new systems have to guess whether to invent more nouns or reuse legacy ones
- authoring and runtime code are forced to carry WR1 terminology forward
- the difference between "facts" and "marks" is mostly scope, not data shape

WR2 should fix this by making scope explicit and reducing the number of nouns.

## Recommendation

Use one term: `state`.

Preferred umbrella term:

- `scoped state`

Preferred scoped names:

- `world state`
- `zone state`
- `room state`
- `character state`

Do not add two new nouns to sit beside `facts` and `marks`.

Do not keep expanding the legacy pattern into something like:

- facts
- marks
- zone data
- room data

That would preserve the current inconsistency instead of removing it.

## Core Mental Model

Mutable local state should be modeled as:

- one shared concept
- explicit scope
- sparse key/value entries
- typed operations
- clear ownership and lifetime rules

In other words:

- scope answers "who owns this state?"
- key answers "which piece of state?"
- value answers "what is its current value?"

The noun should not encode the scope.

## Scope Definitions

### World Scope

State shared across the current world runtime context.

Examples:

- `season = "winter"`
- `invasion_active = true`
- `bridge_status = "raised"`

### Zone Scope

State shared across a zone within a world context.

Examples:

- `north_control = "orc"`
- `fog_level = 3`
- `boss_cycle = "cooldown"`

### Room Scope

State local to one room within a world context.

Examples:

- `lever_pulled = true`
- `altar_charged = 2`
- `hidden_door_revealed = true`

### Character Scope

State local to a specific character.

Use `character` as the future-facing term so the concept is not locked to the
legacy `mark` noun. If later implementation needs a narrower scope such as
`player`, that should be an explicit scope choice, not a return to `mark`.

Examples:

- `met_king = true`
- `visited_castle = true`
- `guild_warning_count = 2`

## What This System Is For

Use scoped state for mutable gameplay state that is:

- sparse
- local to a specific scope
- not well represented as dedicated relational columns
- important enough to preserve as canonical game state

Typical uses:

- quest or contract progression helpers
- world event toggles
- zone control or conflict status
- room interaction state
- character-local discovery or conversation memory

## What This System Is Not For

Scoped state should not absorb every other kind of data.

Do not use scoped state for:

- authored content definitions
- stable canonical fields that deserve explicit columns
- rebuildable runtime caches
- broad payload serialization buckets
- enumerated flags or tags

Examples that should stay separate:

- room flags such as peaceful or no_roam
- world config values
- trigger definitions
- derived caches in runtime tables

## Flags Remain Separate

`flags` and `state` are different concepts and should remain different.

Use `flags` when the value is:

- enumerated
- from a fixed allowlist
- tag-like
- typically boolean-by-presence

Use `state` when the value is:

- arbitrary within a supported type system
- mutable over time
- scoped and sparse
- not naturally modeled as a fixed tag

Examples:

- `room flag: peaceful` should stay a flag
- `room state: lever_pulled = true` should be state
- `zone flag: pvp_zone` should stay a flag or column
- `zone state: north_control = "orc"` should be state

## Scope Is Not Lifetime

Scope and lifetime are related but different.

Scope answers ownership:

- world
- zone
- room
- character

Lifetime answers reset behavior:

- survives reconnect
- survives world reload
- survives instance teardown
- resets on repop
- resets on script or quest reset

WR2 should not hide lifetime inside the noun. A future implementation should
track reset or persistence behavior explicitly.

Initial rule of thumb:

- world, zone, and room state are usually runtime-local to a world or instance
- character state often persists longer than room or zone state
- exceptional persistence behavior should be explicit, not implied

## Authoring Direction

New WR2 authoring should talk about `state`, not `facts` and `marks`.

This matters for:

- quest manifests
- trigger manifests
- builder tooling
- future automation or scheduler systems
- typed predicates and effects

New authored content should not introduce additional legacy-shaped surfaces
such as:

- `fact_check`
- `marked`
- `set fact`
- `set mark`
- raw Python-like WR1 loader condition expressions over ad hoc bags

Those can be adapted internally during migration, but they should not be the
target authoring model.

## Preferred Operation Shape

The runtime and authoring layers should use typed operations around a common
shape:

```yaml
scope: room
target: room.current
key: lever_pulled
op: equals
value: true
```

Example predicate:

```yaml
type: state
scope: zone
target: zone.current
key: north_control
op: equals
value: orc
```

Example effect:

```yaml
type: set_state
scope: room
target: room.current
key: lever_pulled
value: true
```

Example numeric mutation:

```yaml
type: increment_state
scope: character
target: actor
key: guild_warning_count
amount: 1
```

The exact manifest syntax can evolve later, but the important constraints are:

- state operations are typed
- scope is explicit
- target resolution is explicit
- the noun is always `state`

## Recommended Runtime API

A future implementation should provide one service layer rather than exposing
ad hoc reads and writes in many subsystems.

Example service surface:

```python
state_service.get(scope, owner_ref, key)
state_service.set(scope, owner_ref, key, value)
state_service.clear(scope, owner_ref, key)
state_service.increment(scope, owner_ref, key, amount=1)
state_service.snapshot(scope, owner_ref)
```

Optional but likely useful:

```python
state_service.exists(scope, owner_ref, key)
state_service.compare(scope, owner_ref, key, op, value)
```

This keeps quests, triggers, spawn plans, and command handlers from each inventing
their own storage rules.

## Value Types

The value model should be simple and predictable.

Preferred initial support:

- boolean
- integer
- float
- string
- null

Optional later support:

- small JSON objects
- small JSON arrays

Recommendation:

- start with JSON scalar values
- only add structured object values when a concrete use case justifies them

This keeps predicates, migrations, and builder tooling much simpler.

## Key Conventions

State keys should be:

- stable
- lowercase
- snake_case
- semantically specific

Good examples:

- `north_control`
- `met_king`
- `lever_pulled`
- `invasion_active`

Avoid vague keys such as:

- `data1`
- `status`
- `value`

If namespacing becomes necessary later, add it deliberately. Do not let the
system drift into uncontrolled key sprawl.

## Storage Direction

The new scoped state system should be canonical game state, not a rebuildable
cache.

That means:

- do not hide it inside runtime cache blobs
- do not continue storing it on unrelated legacy text fields
- do not spread the same concept across unrelated models

Preferred storage direction:

- dedicated per-scope state tables
- one sparse JSONB bucket per owning aggregate
- explicit foreign keys to the owning entity

Illustrative shape:

```python
class WorldState(models.Model):
    world = models.OneToOneField(World, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class ZoneState(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE)
    zone = models.OneToOneField(Zone, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class RoomState(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE)
    room = models.OneToOneField(Room, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class CharacterState(models.Model):
    character = models.OneToOneField(Character, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
```

This is illustrative, not final.

The names `World`, `Zone`, `Room`, and `Character` in the example above mean
"the owning WR2 aggregate rows". They are not a requirement to reuse today's
exact model boundaries if WR2 runtime ownership lands on instance-specific
tables or a different aggregate split.

The important design choices are:

- state lives in dedicated tables
- state is sparse
- state is JSONB, not text blobs
- state rows can participate cleanly in aggregate locking

## Why Per-Scope Tables Instead Of One Generic Table

Prefer per-scope tables over a single polymorphic `ScopedState` table.

Reasons:

- foreign keys stay explicit
- aggregate locking is easier to reason about
- deletion semantics are cleaner
- joins stay straightforward
- code remains aligned with world / zone / room / character aggregates

A generic service API can still sit on top of per-scope storage.

## Querying Guidance

Do not over-design the storage around arbitrary global queries on state keys.

Assume the common operations are:

- load one owner's state
- read or mutate one key
- evaluate predicates during command, trigger, or quest execution

If later we need fast global queries over specific keys, solve that with one of:

- dedicated indexes for known hot keys
- projection tables
- explicit relational columns for truly canonical fields

Do not force the base design into EAV complexity prematurely.

## Eventing Guidance

State mutation should be observable.

A future implementation should emit structured events when state changes,
for example:

- `world.state.changed`
- `zone.state.changed`
- `room.state.changed`
- `character.state.changed`

Payload should include at least:

- scope
- owner reference
- key
- old value
- new value
- source or cause if known

This is useful for:

- debugging
- quest progression
- trigger chaining
- audit visibility
- frontend refresh decisions

## Compatibility Rules

During migration, legacy names can survive as adapters only.

Compatibility mapping:

- `world facts` -> world state
- `player marks` -> character state
- `zone_data` -> zone state

Rules:

- new systems author against `state`
- old systems may read or write through compatibility adapters temporarily
- compatibility names should not expand to new scopes
- builder UI should eventually stop teaching `facts` and `marks` as the main
  model

## Suggested Migration Sequence

### Phase 1: Introduce the abstraction

- add scoped state terminology to docs and design discussions
- define the service interface
- define the typed predicate and effect shapes

Exit criteria:

- new design work talks about state, not facts and marks

### Phase 2: Add storage and service layer

- add dedicated state tables
- add read/write service methods
- add state change events

Exit criteria:

- backend code can load and mutate scoped state without touching legacy fields

### Phase 3: Add compatibility adapters

- world facts read through world state adapter
- player marks read through character state adapter
- zone data read through zone state adapter

Exit criteria:

- old systems continue to function while storage has a clear new home

### Phase 4: Move authored systems

- quests use typed state predicates and effects
- triggers use typed state operations where appropriate
- spawn-plan behavior stops depending on legacy fact vocabulary

Exit criteria:

- new WR2 authoring no longer depends on fact or mark language

### Phase 5: Update builder UX

- rename world facts screens to world state
- add zone and room state surfaces if needed
- expose character state through the appropriate builder or admin tools

Exit criteria:

- builders learn one coherent model

### Phase 6: Remove legacy vocabulary from internals

- retire direct dependency on `World.facts`
- retire `spawns.Mark` as the canonical home of character-local state
- retire `Zone.zone_data` as the canonical home of zone-local state

Exit criteria:

- facts and marks are legacy aliases at most, or fully removed

## Design Constraints

When this is implemented later, preserve these constraints:

- one noun for the system: `state`
- explicit scope instead of separate nouns per scope
- flags remain separate from state
- typed operations instead of string DSL expansion
- canonical persistence instead of cache-only storage
- compatibility paths are temporary

## Short Version

The future system should be:

- one WR2 concept called `scoped state`
- available at `world`, `zone`, `room`, and `character` scope
- stored in dedicated canonical state tables
- accessed through typed predicates, effects, and service methods
- clearly separated from flags, config, and caches

`facts`, `marks`, and `zone_data` should be treated as legacy storage and
legacy vocabulary, not as the long-term conceptual model.
