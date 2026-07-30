# Deterministic Player Death Routing

Status: implemented WR2 baseline.

This document defines ordered, condition-based player death routing for WR2.
It covers authoring, compilation, runtime resolution, concurrency, caching,
failure handling, and instance delegation.

Related documents:

- [WR2 engine architecture](../../.codex/skills/wr-transition/wr2-architecture.md)
- [YAML manifest system](yaml-manifest-system.md)
- [Scoped state system](scoped-state-system.md)
- [Faction system](faction-system.md)
- [Instance system](instance-system.md)

## Goals

- Route players by core faction.
- Route players by their class/profile key.
- Route players by current level or level band.
- Route players by arbitrary character state established during play.
- Route players by the zone containing the room where they died.
- Give builders explicit precedence through ordered, first-match rules.
- Keep runtime work bounded and independent of world size.
- Keep death penalties, movement, receipts, and events atomic.
- Make retries safe.
- Keep instance deaths local by default while allowing explicit base-world
  delegation.

## Non-Goals

The death hot path does not evaluate:

- inventory or equipment membership
- quest completion
- mob or player presence
- reputation values or faction ranks
- currency balances
- arbitrary database-backed condition paths
- pathfinding, nearest-room searches, randomness, or weighted destinations

When one of those gameplay facts should influence a later death, a trigger or
action should record the consequence in `state.character.*`. Death routing then
reads that already-established state.

There is no player-facing death-route choice command and no reserved
death-routing state key. Builders own both the state names and the gameplay
that changes them.

## Authoring Contract

`WorldConfig.death_room` remains the unconditional fail-safe destination.
`spec.death_routing` optionally adds an ordered `routes` list:

```yaml
kind: world
spec:
  death_room: room@99,0,0

  death_routing:
    routes:
      # Zone overrides everything below it.
      - when:
          eq: [zone.id, zone@7]
        destination: room@10,0,0

      # Levels 20 and above use the veteran infirmary.
      - when:
          gte: [player.level, 20]
        destination: room@11,0,0

      # A consequence recorded earlier by gameplay.
      - when:
          eq: [state.character.divine_patron, poseidon]
        destination: room@12,0,0

      # Class/profile keys are stored on Player.archetype.
      - when:
          eq: [player.archetype, warlord]
        destination: room@13,0,0

      - when:
          eq: [player.core_faction, orc]
        destination: room@14,0,0
```

The resolver examines routes from top to bottom and stops at the first match.
Overlapping conditions are valid and intentional. If no route matches, the
player goes to `death_room`.

Setting `death_routing: null` or supplying `routes: []` disables conditional
routing. Omitting `death_routing` from a patch preserves the current policy.

## Supported Condition Subset

Death routing uses the existing WR2 structured condition syntax. A
death-specific compiler accepts only bounded, query-free operations.

Supported containers and operators:

- `always`
- `all`
- `any`
- `not`
- `eq`
- `in`
- `gte` and `lte` for `player.level`

Supported paths:

- `player.core_faction`
- `player.archetype`
- `player.level`
- `state.character.<path>`
- `zone.id`

Examples:

```yaml
# Shared destination for multiple factions.
when:
  in: [player.core_faction, [human, orc]]
```

```yaml
# Inclusive level band for level 20 and above.
when:
  gte: [player.level, 20]
```

```yaml
# A class route limited to one origin zone.
when:
  all:
    - eq: [player.archetype, tidecaller]
    - eq: [zone.id, zone@4]
```

```yaml
# Either of two independent gameplay consequences.
when:
  any:
    - eq: [state.character.oath, ares]
    - eq: [state.character.blessing, war]
```

```yaml
# An explicit final catch-all. The normal death_room fallback is usually
# simpler, so `always` is accepted only as the final route.
when:
  always: true
```

### Literal semantics

- Faction operands are builder-facing core-faction codes. Manifest apply
  resolves them to base-world faction ids.
- Archetype operands are exact class/profile keys and must exist in the base
  world's `stat_system.class_profiles`.
- Level operands are positive integer literals. `gte` and `lte` are inclusive;
  because levels are integers, strictly above level 10 is `gte: [player.level,
  11]` and strictly below level 10 is `lte: [player.level, 9]`.
- Zone operands use portable `zone@<relative_id>` references and resolve
  against the policy-owning world or instance template.
- Character-state operands are JSON scalars and compare without string
  coercion. `true`, `1`, and `"1"` are different values.
- A missing state path and explicit JSON null both resolve to null.
- Dynamic right-hand path references are not accepted.

### Bounds

- At most 32 routes per policy.
- At most 32 values in one `in` operand.
- At most 256 condition nodes and 256 literal values across one policy.
- Condition nesting depth is at most 16.
- Character-state paths are at most 255 characters and eight segments after
  `state.character`.
- Individual string literals are at most 256 characters.
- Numbers must be finite and within the JSON-safe integer range
  (`-9007199254740991` through `9007199254740991`).
- `always: true` may appear only on the final route.
- Destinations must belong to the world or instance template owning the
  policy.

These bounds make worst-case runtime work small and predictable.

## Rule Precedence

Authored list order is the only precedence rule.

Given:

```yaml
routes:
  - when:
      eq: [zone.id, zone@7]
    destination: room@1,0,0

  - when:
      eq: [player.core_faction, orc]
    destination: room@2,0,0
```

an orc dying in `zone@7` goes to `room@1,0,0`. Reversing the two routes sends
that player to `room@2,0,0`.

The compiler does not reject overlaps or reorder rules by specificity.
Manifest export preserves route order.

## Canonical Storage

One policy belongs to one `WorldConfig`.

```text
DeathRoutingPolicy
  config_id
  enabled

DeathRoutingRoute
  policy_id
  position
  condition
  compiled_version
  compiled_condition
  destination_room_id
```

`condition` is the normalized shared-DSL form used for canonical export.
`compiled_condition` is a rebuildable query-free representation containing
resolved faction and zone identifiers.

The destination is a relational `RESTRICT` foreign key. Route positions are
unique within one policy.

Each policy replacement:

1. acquires exclusive transaction locks for the routing config family
2. validates the shared condition structure and death-specific subset
3. resolves faction, class, zone, and room references
4. replaces ordered canonical route rows atomically
5. increments `WorldConfig.death_routing_generation`
6. writes an immutable compiled snapshot for that generation
7. retires older generations and releases their references
8. retains at most eight snapshot records for bounded diagnostics

The current snapshot has relational `RESTRICT` references for its fallback,
destinations, faction ids, and zone ids. Deaths take shared transaction locks,
while publication and routed-entity deletion take the exclusive form. Shared
locks coexist, so deaths do not serialize one another. Exclusive publication
waits for already-running deaths before it retires their generation and
releases those references.

Generation numbers are monotonic and part of the process-cache key, so
publication does not require a process-wide cache flush.

## Compiled Plan

A compiled plan contains:

```text
CompiledDeathRoutingPlan
  config_id
  generation
  cache_version
  fallback_room_id
  routes[]
    position
    compiled predicate
    destination_room_id
  required_state_paths[]
  load_error
```

The compiled predicate is a small immutable instruction tree. It contains no
ORM objects, query callbacks, arbitrary Python, or general condition
evaluation.

The process cache is keyed by:

```text
(config_id, generation, cache_version, fallback_room_id)
```

Loads are single-flight per key. A warm resolution reads no policy, route, or
snapshot rows.

## Runtime Inputs

The resolver receives only:

- `Player.core_faction_id`
- `Player.archetype`
- `Player.level`
- the origin room's authored `zone_id`
- the Player's character-state mapping when the plan references state

Faction codes and zone references were converted to ids during compilation.
Class/profile keys, player level, and character-state values are already local
scalars.

Origin zone is captured before penalties or movement. Zone routing therefore
means “the zone containing the room where the death occurred.”

## Runtime Algorithm

The death coordinator performs:

1. Normalize the death idempotency token.
2. Return an existing receipt for an identical retry.
3. Resolve the preflight authored world and its candidate config ids.
4. Begin the death transaction.
5. Acquire shared routing locks in sorted config-id order: one for a base
   world, or local plus base for an instance.
6. Lock the Player aggregate.
7. Recheck the idempotency receipt.
8. Verify the Player is still in the preflight runtime, then re-read the
   authoritative source mode, configs, and generations under the shared locks.
9. Load the selected generation's compiled plan through the process cache.
10. Capture the origin room and its zone.
11. If needed, lock and load the Player's one character-state row once.
12. Evaluate compiled routes in order and stop at the first match.
13. Validate the destination room against the selected policy owner.
14. Apply origin-world penalties and corpse/drop behavior.
15. Set current health, energy, and stamina to 1, then clear effects and
    encounters.
16. Move locally or perform the exact recorded-runtime instance transfer.
17. Increment death and location sequences.
18. Persist the receipt and transactional outbox events.
19. Commit before publishing client-facing events and destination triggers.

No route evaluation performs a database query. Character state is loaded at
most once regardless of route count or the number of referenced state paths.

## Character-State Concurrency

Character state remains ordinary builder/gameplay-owned state. No key is
reserved for death routing.

State mutation services and death processing serialize through the
`CharacterState` row. This gives concurrent state mutation and death a clear
commit order:

- a state write committed before the locked death read can affect that death
- a state write ordered after the death read affects later deaths

Builders and triggers should use the scoped-state services rather than direct
model updates.

The 64 KiB encoded character-state bound remains in place. It bounds database
transfer, serialization, state synchronization, and state-backed death routing.

## Core Faction

`Player.core_faction` is the canonical identity used by death routing.
Reputation `FactionAssignment` rows are not consulted.

Manifest faction codes resolve to faction ids when the policy is applied.
Runtime comparison therefore uses the Player's direct foreign-key id and adds
no faction query.

A route without a faction condition naturally applies to every faction,
including factionless players. Several factions may share one destination by
using `in` or separate routes.

## Class Routing

The current canonical Player field for class/profile selection is
`Player.archetype`. Builder-facing route conditions therefore use:

```yaml
eq: [player.archetype, warlord]
```

The compiler validates the operand against the base world's declared
`stat_system.class_profiles`. Runtime comparison uses the Player row's direct
text value and adds no query.

## Level Routing

Level routing reads `Player.level` from the authoritative locked Player row:

```yaml
gte: [player.level, 20]
```

`eq` selects one exact level, `in` selects a bounded list of exact levels, and
`gte` / `lte` define inclusive bands. Thresholds are validated as positive
integers but are not coupled to the world's current `max_level`, so later
level-cap changes do not require route recompilation. Level-only predicates add
no query and do not lock or load character state during route evaluation.

## Zone Routing

Zone routing uses:

```yaml
eq: [zone.id, zone@7]
```

The portable zone reference resolves at manifest apply time. Runtime comparison
uses the origin Room's `zone_id`.

For a base-world death, zone references belong to the base world. For an
instance using local routing, they belong to the instance template.

An instance configured with `death_routing_source: base_world` evaluates the
base world's complete policy. Its class, faction, level, and character-state
routes apply normally. A base-world zone reference does not match an
instance-template origin zone. Use local routing when an instance's own zones
must select local death destinations.

## Instance Routing

Instance templates configure:

```yaml
death_routing_source: local
```

or:

```yaml
death_routing_source: base_world
```

`local` is the default:

- select the instance template's complete ordered policy
- use the instance `death_room` as fallback
- keep the Player in the spawned instance runtime

`base_world`:

- select the direct base world's complete ordered policy
- use the base `death_room` as routing fallback
- apply the instance's configured penalty in the origin instance
- create drops/corpses in the origin instance
- atomically return the Player and surviving carried assets to the exact base
  runtime recorded at instance entry
- mark participation exited with `death_delegated`

Policies are never merged.

Every active `InstanceParticipant` records
`return_runtime_world`. Delegation validates:

```text
participant is active
participant.run.spawned_world_id == player.world_id
participant.run.base_world_id == selected base policy owner
participant.return_runtime_world.context_id == participant.run.base_world_id
```

If transport linkage is invalid, the Player stays in the instance and uses the
instance's local `death_room` fail-safe. The coordinator does not guess a base
runtime or evaluate a second policy.

## Idempotency And Receipts

Every lethal incident supplies a stable `death_token`.

`DeathResolutionReceipt` is unique on `(player_id, death_token)` and records:

- request fingerprint
- origin and destination identities
- instance run/participant identities when applicable
- selected routing source and generations
- matched route position, or null for fallback
- core-faction identity
- death and location sequences
- penalty/corpse result
- compact routing inputs and fallback reason

An identical retry returns the committed result without repeating penalties,
movement, corpse creation, or events. Reusing a token for a different incident
is rejected.

Receipts retain a 30-day retry and audit window. An hourly task deletes at most
10,000 expired rows per run, bounding both table growth and cleanup lock time.

## Events

The transaction enqueues:

- `player.died`
- `instance.left` for successful base-world delegation

The existing client event remains `affect.death`. Its room payload is the final
committed destination, while `origin_room` identifies where the death and
penalty occurred.

Death also emits the canonical player-room-entry lifecycle with
`event.source: death`. Destination mob-definition `enter` reactions and the
room-scoped `event: enter` triggers run first;
`after_death_room_enter` then runs as the death-only compatibility hook. All
observe only the final committed destination and cannot select or correct the
authoritative destination.

Death always advances the player's location sequence, so this lifecycle runs
even when routing returns the player to the same authored room id. Before
running destination behavior, queued delivery rechecks the player's in-game
state, runtime world, room, and location sequence. A later relocation therefore
suppresses stale death-arrival work, and a retried durable event is deduplicated
by its subscription receipt. Reaction output inherits the eight-layer
script-command depth bound and, for durable delivery, is captured into one
bounded derived outbox batch.

## Failure Handling

| Failure | Result |
| --- | --- |
| No route matches | Selected policy owner's `death_room` |
| Policy disabled | Selected policy owner's `death_room` |
| Missing/corrupt compiled snapshot | Selected policy owner's fail-safe |
| Matched destination is invalid | Selected policy owner's fail-safe |
| Invalid state value type for a predicate | Predicate is false |
| Missing core faction/archetype/level/zone/state | Relevant predicate is false |
| Invalid instance return linkage | Instance-local `death_room`; participation remains active |
| Missing `death_room` in corrupt data | `starting_room`, with an alert/fallback reason |
| No valid fail-safe room | Reject and roll back the complete death transaction |

## Performance Contract

For a warm plan:

- policy lookup performs zero canonical-policy queries
- route evaluation performs zero queries
- route evaluation is bounded by 32 small predicate trees
- each death takes one shared advisory-lock call for a base world or two for an
  instance; these shared locks do not block other deaths
- core faction, archetype, level, and zone add no routing-specific query
- state-backed policies read at most one bounded CharacterState row
- no work scales with room population, world size, faction count, trigger
  count, or instance count
- instance asset transfer uses bounded, set-based traversal rather than one
  query per container

Performance coverage should verify:

- one-route and maximum-route plans have the same query count
- repeated pure resolution executes zero queries
- state policies load state once even when several keys are referenced
- non-state policies do not lock or load CharacterState for route evaluation
- warm deaths do not read canonical policy, route, or snapshot tables

## Validation And Test Matrix

Required coverage includes:

- route order controls overlapping faction/class/level/state/zone outcomes
- first match stops evaluation
- state-only routes work for multiple factions
- faction `in` groups share destinations
- archetype values validate against declared classes
- level `eq`, `in`, `gte`, and `lte` preserve integer boundary semantics
- invalid or dynamically resolved level thresholds are rejected
- base class removal is rejected while a base or instance-local route uses it
- arbitrary character-state keys and typed scalars compare exactly
- missing state does not impersonate false, zero, or a string
- origin-zone routing uses the pre-move room
- unconditional final routes and fail-safe behavior
- malformed, dynamic, or query-backed conditions are rejected at apply time
- manifest export preserves route order and portable references
- duplicate death tokens apply one penalty and movement
- instance-local deaths remain active in the run
- delegated deaths use the exact recorded base runtime
- invalid return linkage uses the local transport fail-safe
- penalties and corpses remain in the origin instance
- all death modes and local or delegated destinations commit current health,
  energy, and stamina as 1
- an idempotent retry does not reset resources that regenerated after the
  original committed death
- warm resolver and death query-regression tests
- shared death locks coexist and exclusive publication waits for them

## WR1 Authored-World Conversion

WR2 launches with an empty database. There is no player/runtime-state
migration.

The optional WR1 authored-world exporter may preserve one fixed death room when
it is semantically clear. It must not infer ordered WR2 routes from legacy
spatial modes, faction procession rooms, player marks, historical deaths, or
runtime assignments.

Converted instance templates emit:

```yaml
death_routing_source: local
```

Builders may author new ordered core-faction, archetype, level,
character-state, and zone rules after import.
