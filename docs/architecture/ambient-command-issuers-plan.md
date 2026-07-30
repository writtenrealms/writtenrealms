# Ambient Command Issuers in WR2 (Room/Zone/World)

This document tracks the architecture and implementation plan for supporting non-character command issuers in WR2.

The goal is to support commands initiated by:
- Rooms
- Zones
- Worlds
- System jobs

without forcing those issuers to behave like physical entities (players/mobs).

## Current Status Snapshot

This plan remains directional, but several pieces are now implemented:

- Trigger is the authored automation concept in WR2.
- Builder communication primitives include `/echo`, `/send`, `/sendexcept`,
  and `/cmd`.
- Trigger YAML manifest ingestion is in place for create/update/delete.
- Room builder UI now exposes **Triggers** and a room-tailored new-trigger template.
- Room builder UI exposes **Edit** in the former **Checks** slot. It loads the
  selected room's canonical `kind: room` YAML on demand and applies changes
  through the shared manifest endpoint.
- Room-scoped `before_move_exit` and `before_move_enter` policy triggers now
  provide veto-capable movement gates. WR2 no longer has `RoomCheck`,
  `RoomCommandCheck`, or `RoomCommandCheckState` models or APIs.
- The command dispatcher can resolve `room`, `zone`, and `world` actor types
  through the existing actor compatibility path.
- `CommandContext` carries optional `room`, `zone`, and `world` references in
  addition to `player` and `mob`.
- `TextCommandHandler` can route ambient actors, while dynamic ability commands
  and fallback command triggers remain character-only.
- `/cmd room`, `/cmd zone`, and `/cmd world` now dispatch nested commands as the
  corresponding ambient actor instead of only tagging an `issuer_scope`.
- `/load` supports player, mob, and room actors. Player and mob actors load
  items into their own inventory; room actors load items onto the room floor.
  Mob and room usage is script-source gated.
- `/grantitem <target> <item>` supports builder player, mob, and room issuers.
  It resolves a player or mob target in the issuer's current room and loads the
  item into that target's inventory. Mob and room usage is script-source gated.
- `/transfer <target> <room>` supports direct builder players plus script-gated
  mob and room issuers. Player targets stay inside one live runtime world, mob
  targets are local to the issuer room, and portable scripts can use
  `room@x,y,z` destinations.
- `/echo`, `/send`, `/sendexcept`, and `/state` support room, zone, and world
  actors. `/send` privately addresses one connected player;
  `/sendexcept` addresses every other connected player in that target's
  current runtime-isolated room.
- `/setclass` supports a room actor with an explicit player target, preserving
  trigger patterns such as `/cmd room -- /setclass {{ actor_key }} tidecaller`.
- `/set` supports direct builder players plus script-gated room issuers. Room
  issuers resolve one player or mob in the issuer room and live runtime world,
  lock only that target row, and mutate the runtime character rather than its
  authored definition.
- `/open`, `/close`, `/lock`, and `/unlock` support direct builder players and
  script-source-gated room/mob issuers. They require an explicit live runtime
  world, mutate the same canonical doorway state used by players and movement,
  and intentionally reject zone/world issuers. A direct builder's
  `/cmd room -- ...` does not delegate builder authority; the room form works
  only when trusted Trigger provenance sets `script_source`.
- Direct scheduled room dispatch should include `world_id` or
  `runtime_world_id` in the payload when the command needs live-instance
  context. `/cmd room` carries this from the originating character
  automatically.
- `dispatch_command` and `CommandContext` now carry explicit issuer and
  optional subject identity while retaining the current actor compatibility
  fields. A room-issued Trigger-step command can therefore execute with a
  player or mob subject without pretending that subject initiated the intent.
- A dedicated `ScriptCommandRunner` now powers typed Trigger `command` actions.
  It captures audited handler output inside the step transaction so publication
  occurs through the durable outbox after commit. Most approved commands are
  event-only; `/transfer` is also audited for transactional Trigger-step use
  with the Trigger actor as its only target; any supported step subject may
  issue it. Its room change and events roll back with a failed step. Durable
  events carry internal Trigger/run/issuer/subject provenance that is stripped
  from player payloads. Forced speech and socials are excluded from Trigger and
  quest subscriptions because they are not voluntary player input. When a
  transfer actually moves a player, the committed structural lifecycle event
  runs destination mob-definition `enter` reactions and room-scoped
  `event: enter` triggers after commit; a moved player still in that
  destination after its reactions additionally runs hostile-mob aggro.
  Reaction and aggro output is captured and durably enqueued outside the
  original step locks. Only the final current player arrival in one event batch
  runs this work; a later location change invalidates an earlier pending
  arrival, and delivery rechecks in-game state, runtime, room, and location
  sequence. Transferred mobs retain their compatibility mob-reaction path.
- Trigger command actions support the fixed Trigger room, the Trigger actor
  (including a player), or one bounded exact-one room-local mob selector.
  Single-command, nested-dispatch, alias/history, and fallback-trigger guards
  are enforced at this boundary.
- The runner reuses the already resolved subject, issuer, and runtime world
  rather than refetching those identities once per command action.
- Typed steps require item/mob mutations as an initial prefix. After that,
  `command`, `echo`, `send`, `send_except`, `debit_currency`, and
  `grant_currency` retain authored narrative-output order. The starting wallet
  must cover gross debits without same-step grant subsidy, final net balances
  must remain within the safe-integer limit, and all currency actions settle
  through one signed mutation with balance rows written last. Native send
  actions target the connected player Trigger actor; `send_except` follows that
  actor's current room. A nonzero mutation's authoritative wallet state event
  follows those action events; an exact net-zero mutation changes no revision
  and emits no wallet-state event. Approved commands do not branch on or mutate
  the wallet; `/transfer` may serialize a pre-mutation wallet snapshot as part
  of its full player state, so the final wallet event, when present,
  deliberately supersedes it.

Current trigger command kind is `command`.

Still future work:

- Handler declarations still use `supported_actor_types`; they have not yet
  moved generally to `allowed_issuer_types` and `required_subject_types`.
  Trigger steps currently add narrower audited modes for event-only and
  transactional handlers.
- The dedicated runner is integrated with typed Trigger steps, but legacy
  `spec.script`, quests, and other scheduled script sources have not yet moved
  to it.
- Ambient command rate limits and cross-source recursion/deduplication limits
  are not complete. Typed command steps already reject chains, history,
  aliases, nested `/cmd`, and fallback Trigger recursion. Marked speech/social
  output does not enter Trigger or quest subscriber cascades. Transfer
  lifecycle and location-refresh events are the explicit post-commit
  exceptions. Transfer arrival reactions inherit the script depth, are capped
  at eight layers, collapse same-batch intermediate arrivals, and validate the
  current runtime, room, and location sequence before reacting or scanning for
  aggro. Player transfers use the shared room-entry lifecycle; same-room
  transfers do not emit it.
- A generic `before_command` policy hook for vetoing already resolved command
  handlers is not implemented.

## Why This Is Needed

WR1 depended heavily on room-driven scripting. WR2 now supports players and mobs issuing commands and has an initial compatibility slice for room/zone/world actors, but the full issuer/subject model is still missing.

Players and mobs are similar because both are embodied actors with room presence and physical constraints. Rooms/zones/worlds are different:
- They can initiate behavior
- They can affect many entities
- They do not have inventory, movement, or physical position in the same sense

Treating room/zone/world as fake mobs or players leads to awkward handler logic and brittle assumptions.

## Proposed Mental Model

Split command context into two roles:
- `issuer`: who initiated intent
- `subject`: who/what is physically executing, if applicable

Examples:
- Player types `north`
  - issuer: player
  - subject: player
- Room trigger runs `say Beware`
  - issuer: room
  - subject: room (optional) or none
- Room trigger runs `force guard say Halt`
  - issuer: room
  - subject: mob (resolved by `force`)
- World scheduler runs reset command
  - issuer: world
  - subject: none

## Command Categories

Commands should declare execution requirements instead of hard-coding actor classes.

Category A: Embodied commands
- Require physical subject (`player` or `mob`)
- Examples: move, get, drop, put, inventory, combat actions

Category B: Ambient commands
- Require ambient issuer context (`room`/`zone`/`world`/`system`)
- Subject optional
- Examples: echo, write.zone, write.game, spawn/despawn, world/zone flags

Category C: Bridge commands
- Ambient or embodied issuer can invoke
- Resolve a target subject and dispatch onward
- Example: `/cmd`

## Proposed Context Shape

Add explicit references for issuer and subject.

```python
@dataclass
class EntityRef:
    type: str   # "player" | "mob" | "room" | "zone" | "world" | "system"
    id: int | None
    key: str

@dataclass
class CommandContext:
    issuer: EntityRef
    subject: EntityRef | None
    payload: dict
    connection_id: str | None = None
```

Compatibility aliases can exist during migration:
- `ctx.player`
- `ctx.mob`
- `ctx.actor` (temporary alias for current behavior)

## Handler Contract

Evolve handler declarations from `supported_actor_types` into capability declarations.

```python
class CommandHandler:
    # old: supported_actor_types = ("player",)
    allowed_issuer_types: tuple[str, ...] = ("player",)
    required_subject_types: tuple[str, ...] = ()
```

Rules:
- If `required_subject_types` is empty, subject is optional.
- If non-empty, subject must exist and match one of those types.
- `TextCommandHandler` continues to parse and route text, then enforces issuer/subject requirements before invoking a domain handler.

## Dispatch API Direction

Current dispatch takes `actor_type`/`actor_id`. Move to:

```python
dispatch_command(
    command_type: str,
    payload: dict,
    issuer_type: str,
    issuer_id: int | None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    connection_id: str | None = None,
)
```

Keep existing `player_id` and `actor_type` compatibility paths during migration.

## Event and Output Conventions

Ambient issuers should not always emit `cmd.*` actor-style responses.

Guideline:
- Use `cmd.*` when there is a command result for a specific issuer/subject flow.
- Use `notification.*`, `write.*`, or domain events for room/zone/world broadcast effects.

This aligns with existing frontend console handling for `write.zone`, `write.game`, and notification styles.

## Script Runner Boundary

Introduce a dedicated script command runner used by:
- Triggers (room/zone/world scoped)
- Quest entrance/completion scripts
- Zone/world scheduled logic

Responsibilities:
- Parse script lines
- Build dispatch context (`issuer`, optional `subject`)
- Enforce safety limits
- Emit structured errors and diagnostics

This prevents room/quest logic from directly crafting ad hoc command calls.

## WR1 Room Check Retirement And Export Boundary

WR1 `RoomCheck` and `RoomCommandCheck` were pre-action veto concepts. WR2 does
not store either model, and it has no `RoomCommandCheckState`. The old builder
UI, REST endpoints, runtime payloads, and state cleanup paths are removed.

Movement replacement is implemented through room-scoped policy triggers:

- `before_move_exit` runs against the origin room before movement mutates
  state.
- `before_move_enter` runs against the destination room before movement
  mutates state.
- a false policy condition vetoes movement and returns authored
  `failure_message` text.
- ordinary movement, direction-based Charge, and flee-route selection use the
  policy path.

Detailed movement behavior lives in
[pre-action-policy-hooks.md](/Users/teebes/code/writtenrealms/docs/architecture/pre-action-policy-hooks.md).

Command gating remains a distinct future capability. An ordinary WR2
`kind: command` trigger is not a replacement for `RoomCommandCheck`: command
triggers handle authored matched commands, while command checks intercepted
already resolved handlers. A semantics-preserving replacement would require a
`before_command` policy hook after parsing/resolution and before mutation.

WR1 conversion is an exporter concern, not a WR2 database migration:

- supported `RoomCheck` predicates become `kind: policy` trigger documents
- unsupported predicates produce explicit exporter diagnostics
- `RoomCommandCheck` rows are reported until `before_command` exists
- WR2 imports only canonical room and trigger manifests into a fresh world

The field-level mappings and unsupported cases are tracked in
[yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md).
Do not reintroduce a compatibility model, predicate vocabulary, or state table
to make export easier.

Remaining TODOs for this plan:

- Add `before_command` only when there is a concrete WR2 command-veto use case,
  and define issuer/subject semantics at the same time.
- If `before_command` is added, make recognized command planning consult the
  policy before the domain handler mutates state.
- Extend the structured condition DSL for any inventory, equipment, or
  health-percentage predicate accepted by the WR1 exporter.
- Define explicit WR2 behavior for any desired WR1 `hint_msg`, `cmd_issued`, or
  tracked-state use case; do not imply these fields already map.
- Implement WR1 exporter fixtures that prove supported movement conversion and
  unsupported-row reporting.

## Safety Requirements

Ambient scripting can create loops quickly. Add guardrails early:
- Max command depth (example: 8)
- Max chained commands per invocation
- Cooldown/rate limits per issuer key
- Loop detection via short-lived dedupe key
- Clear error surfacing with issuer metadata

## Implementation Plan

### Phase 1: Model and compatibility

Status: implemented for dispatcher/context resolution with compatibility
aliases retained.

1. Add `issuer` and `subject` fields to command context and registry resolution.
2. Keep current actor-based fields as compatibility aliases.
3. Support resolving refs for `room`, `zone`, and `world` in dispatch.
4. Keep current player/mob behavior unchanged.
5. Carry runtime `world` context for ambient room commands so live-instance
   operations can target the correct spawn world.

Exit criteria:
- Existing backend tests pass without behavior regressions.

### Phase 2: Capability-based handlers

1. Add `allowed_issuer_types` and `required_subject_types` to handler base.
2. Implement enforcement in registry and text routing.
3. Migrate existing handlers from actor type checks to capabilities.

Exit criteria:
- Player and mob commands still behave as before.
- `/cmd` remains functional with new enforcement model.

### Phase 3: Ambient command primitives

Status: partially implemented for scoped builder primitives.

1. Add minimal ambient-safe commands (example: `echo`, `write.zone`, `write.game`).
2. Add room/zone/world tests for ambient dispatch and publish behavior.
3. Define payload schemas for ambient commands.
4. Support `/load` as a first item-spawn primitive: player and mob actors load
   items into inventory, while room actors load items onto the room floor.
5. Support `/grantitem` as a targeted item-spawn primitive for room-triggered
   rewards and starter-equipment scripts.
6. Support `/transfer` as a runtime-isolated forced-movement primitive for room
   and mob scripts, without treating ambient issuers as physical movers.
7. Support `/set` as a room-script primitive with room-local, runtime-isolated
   character targeting and target-row locking.
8. Support `/send` and `/sendexcept` as targeted, runtime-isolated
   communication primitives for the same direct-builder and script-gated
   ambient actor contexts.

Exit criteria:
- A room issuer can produce visible room/zone/world outputs through standard publish paths.

### Phase 4: Script runner integration

Status: implemented for typed Trigger command actions; other script entry
points remain pending.

1. Add `ScriptCommandRunner` that executes command lines under ambient issuer
   context. Implemented for one bounded command.
2. Integrate with typed Trigger steps. Implemented for audited event-only
   commands and transactional `/transfer` restricted to the Trigger actor as
   its target.
3. Integrate with legacy Trigger scripts and quest script entry points.
4. Route command execution exclusively through the runner for scripted
   sources.

Exit criteria:
- At least one room trigger path executes commands via runner in WR2. Met.
- Traceable issuer metadata appears in command context and step errors. Met for
  typed Trigger steps.

### Phase 5: Pre-action policy hooks and room-check replacement

Status: movement complete; generic command veto remains future work.

1. Movement uses veto-capable `before_move_exit` and `before_move_enter`
   policy triggers before state mutation.
2. Movement failures use authored feedback through standard publish paths.
3. Legacy room-check models, APIs, payloads, and builder UI are removed.
4. If a concrete command-veto use case is adopted, add `before_command`
   without reviving `RoomCommandCheck`.
5. Prove WR1 room-check conversion in exporter fixtures rather than a WR2 data
   migration.

Exit criteria:

- Builder-authored room movement rules work without legacy runtime code. Met.
- WR2 contains no room-check storage or builder surface. Met.
- Supported WR1 movement checks export to policy manifests with regression
  coverage. Pending in the WR1 exporter.
- Command gating is covered if and when `before_command` joins the WR2 scope.

### Phase 6: Safety and hardening

1. Add recursion/depth/rate protections. The eight-layer scripted-command depth
   guard is implemented; broader rate protections remain pending.
2. Add structured telemetry for ambient command execution.
3. Add failure policy (continue vs stop-on-error) per script context.

Exit criteria:
- Looping scripts are contained and observable.
- Failures produce actionable diagnostics.

### Phase 7: Cleanup

1. Remove temporary actor-only compatibility fields once migrated.
2. Update docs and developer references.
3. Keep legacy room-check models, UI, APIs, and runtime payloads absent from
   WR2. This room-check cleanup is complete.
4. Expand command coverage as needed.

Exit criteria:
- No core path depends on legacy actor-only API.
- No core gameplay path depends on legacy room-check models. Met.

## Testing Strategy

Required test layers:
- Unit tests for context resolution and capability checks
- Handler tests for issuer/subject enforcement
- Integration tests for room/zone/world script execution
- Integration tests for movement veto hooks, plus command-veto coverage if
  `before_command` is implemented
- Regression tests for player/mob command behavior
- WR1 exporter fixtures for supported room-check conversion and unsupported
  command-check diagnostics
- Loop safety tests (depth and dedupe guards)

## Implemented First Vertical Slice

The first compatibility slice is:

1. A script-safe command dispatch can target a room actor directly.
2. `/cmd room -- <command>` resolves the current room and dispatches the nested
   command as `actor_type=room`.
3. Runtime world context is carried into the nested command payload.
4. `/load item <slug>` under a room actor loads the item onto the room floor;
   under a mob actor it loads the item into that mob's inventory.
5. Tests cover direct room actor `/load`, `/cmd room -- /load`, and
   pledge-style `/cmd room -- /setclass <player> <class>`.
6. `/grantitem <target> <slug>` under a room or mob actor loads the item into
   the target character inventory and sends player targets an inventory-updating
   notification.
7. `/transfer <target> <room@x,y,z>` under a room or mob actor relocates a
   character within the current runtime world, emits the legacy-compatible
   exit/enter flow plus a transfer-state snapshot for player targets, runs
   runtime-isolated destination mob reactions, and terminates active combat
   with bulk cleanup queries.
8. `/set <target> <field> <value>` under a room actor changes one supported
   runtime player or mob field after local target resolution and a target-only
   row lock; player targets receive a state-updating notification.

This delivers immediate value for WR1-style content migration while validating
ambient actor dispatch before the broader issuer/subject refactor.

Recommended next vertical slice:

1. Introduce explicit command diagnostics for nested script command failures.
2. Start replacing `supported_actor_types` with issuer/subject capability
   declarations once the second ambient primitive is proven.
