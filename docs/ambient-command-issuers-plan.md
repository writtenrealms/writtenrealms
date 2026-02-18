# Ambient Command Issuers in WR2 (Room/Zone/World)

This document proposes an architecture and implementation plan for supporting non-character command issuers in WR2.

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

Current trigger command kind is `command`.

## Why This Is Needed

WR1 depended heavily on room-driven scripting. WR2 now supports players and mobs issuing commands, but ambient sources (room/zone/world) are still missing.

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

## Safety Requirements

Ambient scripting can create loops quickly. Add guardrails early:
- Max command depth (example: 8)
- Max chained commands per invocation
- Cooldown/rate limits per issuer key
- Loop detection via short-lived dedupe key
- Clear error surfacing with issuer metadata

## Implementation Plan

### Phase 1: Model and compatibility

1. Add `issuer` and `subject` fields to command context and registry resolution.
2. Keep current actor-based fields as compatibility aliases.
3. Support resolving refs for `room`, `zone`, and `world` in dispatch.
4. Keep current player/mob behavior unchanged.

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

1. Add minimal ambient-safe commands (example: `echo`, `write.zone`, `write.game`).
2. Add room/zone/world tests for ambient dispatch and publish behavior.
3. Define payload schemas for ambient commands.

Exit criteria:
- A room issuer can produce visible room/zone/world outputs through standard publish paths.

### Phase 4: Script runner integration

1. Add `ScriptCommandRunner` that executes command lines under ambient issuer context.
2. Integrate with trigger and quest script entry points.
3. Route command execution exclusively through runner for scripted sources.

Exit criteria:
- At least one room trigger path executes commands via runner in WR2.
- Traceable issuer metadata appears in logs/errors.

### Phase 5: Safety and hardening

1. Add recursion/depth/rate protections.
2. Add structured telemetry for ambient command execution.
3. Add failure policy (continue vs stop-on-error) per script context.

Exit criteria:
- Looping scripts are contained and observable.
- Failures produce actionable diagnostics.

### Phase 6: Cleanup

1. Remove temporary actor-only compatibility fields once migrated.
2. Update docs and developer references.
3. Expand command coverage as needed.

Exit criteria:
- No core path depends on legacy actor-only API.

## Testing Strategy

Required test layers:
- Unit tests for context resolution and capability checks
- Handler tests for issuer/subject enforcement
- Integration tests for room/zone/world script execution
- Regression tests for player/mob command behavior
- Loop safety tests (depth and dedupe guards)

## Recommended First Vertical Slice

Implement one end-to-end room issuer path first:
1. Room trigger invokes script runner
2. Runner dispatches with `issuer=room`
3. Script executes `force <mob> say ...` and `force <mob> emote ...`
4. Players in room receive notifications

This delivers immediate value for WR1-style content migration while validating architecture choices before broader rollout.
