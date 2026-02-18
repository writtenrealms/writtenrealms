# Written Realms 2.0 Engine Architecture (Command → Action → Event)

This document describes the desired runtime architecture for WR2.0. It is a reference for implementation decisions and code review.

## Goals

- Use **Postgres as the source of truth** for canonical world data.
- Support high concurrency without a monolithic “world lock”.
- Make the engine deterministic and testable via pure functions where possible.
- Separate **player intent** from **queued work** from **results**.
- Allow “derived runtime caches” to improve performance, while remaining safely rebuildable.

Non-goals:
- Full event sourcing (may be added later).
- In-memory authoritative state (avoid Nexus-style fragility).

---

## Core Terminology

### Command (external intent)
A `Command` is an *actor’s intent* to do something:
- submitted by: players, mobs, room triggers, systems
- may be invalid / ambiguous / unauthorized
- may be rejected without mutating world state

Examples:
- `attack goblin`
- `move east`
- `look`
- mob AI decides `wander`
- room trigger issues `spawn mobs`

### Action (internal queued unit of work)
An `Action` is a **validated, explicit, executable unit of work**:
- always has fully specified parameters (no ambiguity)
- contains enough identifiers to determine what aggregates to lock
- is the **unit of work** stored in the queue and executed by the engine
- is idempotent or has idempotency keys to tolerate retries

Examples:
- `AttackAction(attacker_id, target_id, room_id)`
- `MoveAction(character_id, from_room_id, to_room_id)`
- `LookAction(character_id, room_id)` (read-only)
- `SpawnMobsAction(room_id, spawn_table_id)`

### Event (results)
An `Event` is an immutable record of something that happened:
- produced by executing an `Action`
- used for: websocket broadcast, logs, analytics, replay/debugging, downstream side effects
- should be safe to publish multiple times if consumers are idempotent

Examples:
- `CharacterMoved(character_id, from_room_id, to_room_id)`
- `DamageDealt(attacker_id, target_id, amount)`
- `MobDied(mob_id)`
- `ItemDropped(item_id, room_id)`

---

## High-Level Flow

1. **Ingress**
   - Actor submits a `Command` (player input, AI decision, trigger, etc.)

2. **Parse + Validate + Plan**
   - Parse text/UI input into a typed `Command`
   - Validate permissions and existence
   - Convert the `Command` into one or more `Action`s
     - usually 1:1, sometimes 1:N for multi-step behaviors

3. **Enqueue**
   - Persist `Action` to the Action Queue (DB table is the baseline; broker optional later)
   - Each `Action` includes:
     - `action_type`
     - `payload`
     - `actor_type/actor_id` (optional but recommended)
     - `idempotency_key` (recommended)
     - scheduling metadata (now/later), priority, shard/instance routing

4. **Execute**
   - A worker claims an `Action`
   - Engine loads + locks required aggregates (row-level locks)
   - Engine applies state transitions
   - Engine persists updated rows
   - Engine emits `Event`s

5. **Publish**
   - Events are recorded (DB) and published (websocket, logs, etc.)
   - Downstream workers may consume events for side effects (email, achievements, etc.)

---

## Data Model: Canonical vs Runtime

### Canonical data (relational)
Canonical data is stored in indexable columns and relations:
- Characters: location, hp, resources, currencies, etc.
- Items: ownership/location via FKs, equipped slot, template reference
- Rooms: template reference, instance reference, canonical tags/flags
- Templates: static authored content

Canonical data must be sufficient to reconstruct runtime state.

### Runtime/cached data (JSON)
Runtime/cached data is stored as JSONB and may be safely rebuilt:
- cooldown maps, buffs/debuffs, AI memory
- precomputed trigger indices
- denormalized lists that avoid expensive joins at runtime
- spawn controller state, timers, temporary effects

Runtime/cached data must include:
- `_cache_version`
- `_built_at` timestamp
- optionally `_dirty` flag or separate `cache_dirty` column

Preferred storage pattern:
- Canonical tables remain lean.
- Runtime/caches live in **separate 1:1 runtime tables** where possible:
  - `CharacterRuntime(character_id, data JSONB, updated_at)`
  - `RoomRuntime(room_id, data JSONB, updated_at)`
  - `MobRuntime(mob_id, data JSONB, updated_at)`
- Presence of a runtime row indicates “active/online”.

---

## Aggregates and Locking

State mutation is coordinated via **aggregate-level row locks**.

### Aggregates
Primary aggregates:
- Character aggregate (Character + CharacterRuntime + inventory rows as needed)
- Room aggregate (RoomInstance + RoomRuntime + contents references)
- Mob aggregate (MobInstance + MobRuntime)
- Instance aggregate (DungeonInstance/WorldInstance state)

### Locking Rules
- Determine involved aggregates from the `Action` payload.
- Acquire locks in a consistent global order to avoid deadlocks:
  1) Instance
  2) Rooms (sorted by id)
  3) Characters (sorted by id)
  4) Mobs (sorted by id)
  5) Items (sorted by id) if locking items is required

Use `SELECT ... FOR UPDATE` within a single DB transaction.

### Read-only Actions
Some `Action`s are read-only (e.g., Look). They should:
- avoid row locks where possible
- tolerate slightly stale reads if necessary (configurable)
- still produce Events (e.g. `RoomViewed`) if helpful for the client

---

## Engine Execution Contract

### Input
`Action` + a snapshot of required aggregates:
- canonical rows
- runtime JSON (validated via Pydantic)
- related rows (inventory/items/mobs) as needed

### Output
- updated canonical rows
- updated runtime JSON
- a list of emitted `Event`s

### Determinism
- Given the same initial state and same random seed, execution should be deterministic.
- Randomness should be injected explicitly (seed per action or per tick).

### Idempotency and Retries
Workers may retry actions. Therefore:
- Each action should have an `idempotency_key` derived from:
  - actor + client command id + monotonic sequence, or
  - queue entry id, when safe
- Engine should ensure "apply once" semantics for state mutation:
  - record `last_processed_action_id` per aggregate, OR
  - record applied keys in a small bounded set, OR
  - rely on queue guarantees only when robust

Events should also be publish-idempotent.

---

## Command → Action Planning

### Responsibilities
- Command parsing is allowed to be user-facing and flexible.
- Planning produces executable Actions that are strict and explicit.

### Common patterns
- `Command` can map to:
  - `[]` (rejected)
  - `[Action]` (typical)
  - `[Action, Action, ...]` (multi-step)
- Example multi-step: a move might enqueue follow-ups:
  - `MoveAction(...)`
  - `OnEnterTriggersAction(room_id, character_id)`
  - `AutoLookAction(...)` (optional)

Planning should be lightweight and avoid locks; execution is where locks happen.

---

## Events

### Event Store
Baseline:
- events are persisted in an `EventLog` table with:
  - event_type, payload JSONB, created_at
  - correlation ids: action_id, command_id, actor_id, instance_id
  - stream keys: room_id, character_id, etc.

### Publishing
- Events are broadcast to interested clients (websockets) based on routing keys
  - e.g., everyone in a room receives room-scoped events
  - the actor receives private events (errors, inventory updates, etc.)

### Consumer Safety
Event consumers must be idempotent:
- accept at-least-once delivery
- dedupe by event_id if needed

---

## Scheduling and Ticks

Not all Actions come from player input.

### Sources of Actions
- Players (commands)
- Mob AI (periodic decisions)
- Room triggers (on-enter, on-leave, timers)
- World systems (spawns, weather, decay, auctions, etc.)

### Scheduling Model
- Scheduled Actions are enqueued with `run_at`.
- A scheduler component periodically enqueues due work (or workers poll due rows).
- Avoid global tick locks: schedule per-instance or per-room work where possible.

---

## Cache / Derived Data Policy

Runtime/cached fields exist to avoid expensive queries, but must be safe to rebuild.

Rules:
- Cached JSONB must be versioned (`_cache_version`).
- Cached data can be invalidated (`cache_dirty`) and lazily rebuilt on demand.
- Canonical data must remain sufficient to rebuild caches.
- If data is frequently queried, consider denormalizing into real columns/arrays instead of deep JSON queries.

---

## Suggested Baseline Tables (conceptual)

- `Character` (canonical columns)
- `CharacterRuntime` (JSONB runtime)
- `RoomTemplate` (authored content)
- `RoomInstance` (canonical per world/instance)
- `RoomRuntime` (JSONB runtime/cache)
- `MobTemplate`
- `MobInstance`
- `MobRuntime`
- `ItemTemplate`
- `ItemInstance` (owner FK or room FK, plus per-item JSONB for rolled stats/sockets)
- `ActionQueue` (queued Actions)
- `EventLog` (Events)

---

## Operational Notes

- Start with a DB-backed queue (`ActionQueue`) for simplicity.
- If higher throughput is needed later, swap the queue transport without changing
  the Command/Action/Event semantics.
- Keep engine logic in pure functions as much as practical; DB code orchestrates locks and persistence.
- Prefer small aggregates and avoid world-sized locks.

---

## Summary

WR2.0 runs on a clear pipeline:

**Command (intent) → Action (queued unit of work) → Engine executes with locked aggregates → Event (immutable results)**

Canonical state lives in relational tables; runtime/cached state lives in versioned JSONB that can be invalidated and rebuilt.
