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
- Builder command primitives are `/echo` and `/cmd`.
- Trigger YAML manifest ingestion is in place for create/update/delete.
- Room builder UI now exposes **Triggers** and a room-tailored new-trigger template.
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
- `/echo` and `/state` support room, zone, and world actors.
- `/setclass` supports a room actor with an explicit player target, preserving
  trigger patterns such as `/cmd room -- /setclass {{ actor_key }} tidecaller`.
- Direct scheduled room dispatch should include `world_id` or
  `runtime_world_id` in the payload when the command needs live-instance
  context. `/cmd room` carries this from the originating character
  automatically.

Current trigger command kind is `command`.

Still future work:

- The explicit `issuer`/`subject` context shape described below is not yet
  implemented.
- Handler declarations still use `supported_actor_types`; they have not yet
  moved to `allowed_issuer_types` and `required_subject_types`.
- There is not yet a dedicated `ScriptCommandRunner`.
- Ambient command recursion limits, rate limits, and structured diagnostics are
  not complete.

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

## Legacy Room Check Replacement Requirement

WR1-style `RoomCheck` and `RoomCommandCheck` are legacy authored concepts and
should not remain a permanent parallel system in WR2.

Detailed design direction for the replacement lives in
[pre-action-policy-hooks.md](/Users/teebes/code/writtenrealms/docs/architecture/pre-action-policy-hooks.md).

This ambient issuer plan must account for replacing them with the trigger and
command-policy model, not merely coexisting with them.

Why this matters:
- Room checks currently represent pre-action veto logic, especially for
  movement and command gating.
- The current WR2 command trigger path is not sufficient on its own because it
  only runs as a fallback for otherwise unresolved text commands.
- To retire room checks cleanly, WR2 needs first-class pre-action policy hooks,
  not only script execution after a command has already been resolved.

Required runtime hooks:
- `before_command`: runs after command parsing and resolution, but before the
  resolved handler executes.
- `before_move_exit`: runs before leaving the current room.
- `before_move_enter`: runs before entering the destination room.
- `after_command` or equivalent event hooks: remain useful for side effects,
  but they are not a substitute for veto-capable checks.

Required behavior:
- Hooks must be able to veto execution with authored feedback text.
- Hooks must support room, zone, world, and ambient issuer context.
- Hooks must support the same practical gating predicates room checks were used
  for, such as inventory, equipment, room occupancy, faction standing, health,
  and quest state.
- Hooks must define how legacy `track_state`-style behavior maps into WR2,
  whether by durable trigger state, quest facts, world facts, or explicit
  policy state tables.

Migration direction:
- Do not keep investing in `RoomCheck` and `RoomCommandCheck` as first-class
  builder-facing end-state models.
- Introduce trigger or policy authoring that can represent equivalent pre-action
  rules.
- Add a migration path from legacy room checks into the new authored model.
- Remove legacy builder UI and runtime reliance only after the new policy path
  is proven by tests.

Open TODOs for this plan:
- Extend the movement policy hook contract described in
  `pre-action-policy-hooks.md` as new pre-action hooks are added.
- Update command planning or handler dispatch so recognized commands consult
  pre-action hooks before normal execution.
- Define issuer and subject semantics for `before_move_exit` and
  `before_move_enter`.
- Decide whether these hooks live inside `Trigger` with additional kinds or
  phases, or in a sibling policy model that shares the same runtime pipeline.
- Audit current condition coverage and close gaps needed for room-check
  migration, especially quest-related predicates.
- Decide how legacy `failure_msg`, `hint_msg`, and tracked pass or fail state
  map into WR2 behavior.
- Define builder export, manifest, and migration tooling for converting legacy
  room checks into the new authored format.

## Safety Requirements

Ambient scripting can create loops quickly. Add guardrails early:
- Max command depth (example: 8)
- Max chained commands per invocation
- Cooldown/rate limits per issuer key
- Loop detection via short-lived dedupe key
- Clear error surfacing with issuer metadata

## Implementation Plan

### Phase 1: Model and compatibility

Status: partially implemented through the existing actor compatibility model.

1. Add `issuer` and `subject` fields to command context and registry resolution.
2. Keep current actor-based fields as compatibility aliases.
3. Support resolving refs for `room`, `zone`, and `world` in dispatch.
4. Keep current player/mob behavior unchanged.
5. Carry runtime `world` context for ambient room commands so live-instance
   operations can target the correct spawn world.

Exit criteria:
- Existing WR2 tests pass without behavior regressions.

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

Exit criteria:
- A room issuer can produce visible room/zone/world outputs through standard publish paths.

### Phase 4: Script runner integration

1. Add `ScriptCommandRunner` that executes command lines under ambient issuer context.
2. Integrate with trigger and quest script entry points.
3. Route command execution exclusively through runner for scripted sources.

Exit criteria:
- At least one room trigger path executes commands via runner in WR2.
- Traceable issuer metadata appears in logs/errors.

### Phase 5: Pre-action policy hooks and room-check replacement

1. Add veto-capable pre-action hooks for resolved commands and movement.
2. Ensure hooks execute before domain handlers mutate state.
3. Support authored failure text and policy outcomes in standard publish paths.
4. Prove one migrated legacy room-check flow end to end.

Exit criteria:
- A migrated room-check scenario can block command or movement execution
  without relying on legacy room-check runtime code.
- Builder-authored pre-action rules work for at least room and zone scope.
- Movement gating and command gating are both covered by tests.

### Phase 6: Safety and hardening

1. Add recursion/depth/rate protections.
2. Add structured telemetry for ambient command execution.
3. Add failure policy (continue vs stop-on-error) per script context.

Exit criteria:
- Looping scripts are contained and observable.
- Failures produce actionable diagnostics.

### Phase 7: Cleanup

1. Remove temporary actor-only compatibility fields once migrated.
2. Update docs and developer references.
3. Remove or fully deprecate legacy room-check models and UI once replacement
   coverage is complete.
4. Expand command coverage as needed.

Exit criteria:
- No core path depends on legacy actor-only API.
- No core gameplay path depends on legacy room-check models.

## Testing Strategy

Required test layers:
- Unit tests for context resolution and capability checks
- Handler tests for issuer/subject enforcement
- Integration tests for room/zone/world script execution
- Integration tests for pre-action command and movement veto hooks
- Regression tests for player/mob command behavior
- Migration regressions for legacy room-check replacement cases
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

This delivers immediate value for WR1-style content migration while validating
ambient actor dispatch before the broader issuer/subject refactor.

Recommended next vertical slice:

1. Introduce explicit command diagnostics for nested script command failures.
2. Start replacing `supported_actor_types` with issuer/subject capability
   declarations once the second ambient primitive is proven.
