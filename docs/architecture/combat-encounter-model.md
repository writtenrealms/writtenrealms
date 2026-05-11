# Encounter-Scoped Combat in WR2

This document proposes a high-level combat direction for WR2 that fits the
existing `Command -> Action -> Event` architecture and keeps room for later
implementation details.

The intent is not to lock in exact formulas, timings, or schemas. The intent is
to define the shape combat should have so WR2 can:

- ship a fun proof of concept
- scale better than WR1's global combat timing model
- support both live multiplayer and slower or fully async play styles

## Why This Direction

WR1 combat was fun, but it depended heavily on precise wall-clock timing:

- global combat ticks
- off-round instant abilities
- cast timers with sub-tick urgency
- responsiveness that degraded quickly under load

That model is a poor fit for WR2's queue-based runtime. WR2 already wants:

- explicit commands
- explicit actions
- deterministic execution
- row-level locking around concrete aggregates
- events emitted after state transitions

Combat should follow the same model instead of becoming a special real-time
exception.

## Core Idea

Combat in WR2 should be modeled as an **encounter-scoped state machine**.

At a high level:

1. A fight creates or joins a `combat encounter`.
2. That encounter owns combat state for only its participants.
3. Participants submit combat intents between resolution steps.
4. The encounter advances in discrete logical iterations.
5. Each iteration resolves all due work, persists state, and emits events.

The important distinction is:

- combat logic runs on **logical ticks / steps**
- wall-clock scheduling of those ticks is a **policy**

Those are not the same thing.

## Goals

- Keep combat compatible with WR2's `Command -> Action -> Event` direction.
- Avoid a global world-wide combat sweep as the primary runtime model.
- Make active combat consume work only where combat is actually happening.
- Support deterministic resolution and easier automated testing.
- Make combat work in multiplayer worlds, private instances, and single-player
  or async worlds.
- Preserve a sense of tension, anticipation, and tactical planning even if
  WR2 is less twitchy than WR1.

## Non-Goals

- Reproduce WR1's exact responsiveness model.
- Optimize for sub-second reflex execution as a core design goal.
- Require every world to run combat at the same wall-clock speed.
- Force fully async combat everywhere.

## How It Should Feel To Players

Combat should feel:

- deliberate rather than twitchy
- readable rather than chaotic
- responsive in acknowledgement, even when resolution is discrete
- tactically expressive through queued choices, target changes, interrupts,
  timing windows, and encounter context

Players should be able to:

- issue a combat action at any time
- see immediately that the action was accepted, changed, rejected, or queued
- understand when the next combat resolution is expected
- make meaningful choices between combat iterations

In a fast live world, combat should feel like a steady pulse.

In a slower or async world, combat should still feel coherent because the same
logical combat rules apply even when wall-clock pacing changes.

## Mental Model

The following concepts should exist, even if later implementations choose
different names.

### Encounter

A combat encounter is the runtime scope for one fight.

It should own or reference:

- participants
- combat state
- current logical tick or step
- pending intents
- due effects
- pacing policy
- whether the encounter is active, paused, or finished

An encounter is not global world state. It is a bounded runtime unit.

### Combat Intent

A combat intent is what a participant wants to do next.

Examples:

- basic attack
- cast spell
- defend
- flee
- switch target
- use item

Intents are submitted between combat steps and become eligible for resolution on
the appropriate future step.

Flee is a delayed combat intent in WR2. Submitting `flee` chooses a valid random
adjacent room that the player has enough stamina to enter, spends that
destination room's normal movement cost, and stores a pending flee intent. On
the next combat step, the player spends the round looking for an opening and
takes no primary action. On the following combat step, flee resolves at the top
of the step before effect ticks, attacks, or other damage can occur; the
encounter finishes and the player moves to the chosen room.

### Combat Resolution Step

A combat resolution step is one iteration of encounter processing.

During a step, the engine may resolve:

- queued player actions that are due
- mob AI choices that are due
- auto-attacks
- cast completions
- cooldown changes
- damage-over-time or heal-over-time effects
- deaths, interrupts, fleeing, and encounter end conditions

The outcome of a step should be explicit state mutation plus emitted events.

### Pacing Policy

A pacing policy controls when an encounter advances in wall-clock time.

Examples:

- live multiplayer cadence, such as every 1 or 2 seconds
- slower sandbox cadence
- player-triggered cadence for solo worlds
- paused encounter awaiting input or an external event

The pacing policy affects scheduling, not the meaning of combat rules.

## Logical Time vs Wall-Clock Time

This is the most important design boundary.

Combat durations should be expressed primarily in **logical combat ticks**, not
hard-coded wall-clock seconds.

Examples:

- a fast strike might resolve on the next tick
- a heavy spell might take 2 ticks
- a poison effect might last 5 ticks
- a stun might suppress one future action window

Then wall-clock scheduling can vary:

- live world: ticks resolve automatically on a short cadence
- lower-pressure world: the same ticks resolve more slowly
- fully async world: ticks resolve only when advanced

This keeps combat design stable while allowing operational flexibility.

## Why Encounter Scope Matters

WR2 should avoid treating combat as one global loop for the entire world.

Instead:

- only active encounters schedule combat work
- inactive rooms consume no combat work
- different encounters in the same world can progress independently
- encounter-heavy content can be isolated into instances

This is a better fit for WR2 scaling than a world-wide combat heartbeat that
must continuously inspect everyone who might be fighting.

## Relationship To WR2 Architecture

Combat should remain a normal WR2 runtime flow.

At a high level:

1. Player or system submits a combat command.
2. Planning resolves that command into one or more combat actions.
3. The encounter stores or updates intent state.
4. A future encounter resolution action advances the fight.
5. Combat events are emitted and published like other WR2 events.

This means combat should fit naturally alongside the existing architecture:

- commands represent intent
- actions perform deterministic work
- events describe results

Combat should not require a second hidden runtime model that bypasses the WR2
handler and event pipeline.

## Suggested Runtime Shape

This document does not prescribe exact models, but the architecture should make
room for concepts like:

## World-Level Pacing Configuration

Encounter pacing should live in world config, not in a separate combat-only
manifest kind.

The existing WR2 manifest flow already puts runtime world rules like
`allow_combat`, `death_mode`, and `death_room` inside `kind: world` `spec`.
Combat pacing should follow that same pattern.

Recommended field:

- `combat_resolution_interval`

Recommended semantics:

- `> 0`: auto-advance active encounters every N seconds
- `0`: resolve combat immediately
- `-1`: do not auto-advance encounters; advance them only through explicit
  actions or a later scheduler

This keeps authored pacing policy in one place and lets future encounter
implementations interpret the policy consistently across live and async worlds.

Current status: the placeholder `kill <mob>` flow now honors this field for WR2
combat pacing:

- `0`: immediate full auto-resolve
- `> 0`: scheduled round-by-round resolution
- `-1`: manual round-by-round resolution driven by explicit `kill <mob>`
  commands

Queued abilities, richer action selection, and more general encounter
orchestration are still future work.

- submit combat intent
- replace or cancel queued combat intent
- resolve encounter step
- pause or resume encounter
- finish encounter

In practice, that likely means:

- encounter-scoped state rows or runtime records
- scheduled resolution work keyed by encounter
- explicit event emission for combat outcomes

The engine should lock only the encounter and relevant combat aggregates needed
for that step, not the whole world.

## Scheduling Modes

The same combat model should support multiple play styles.

### Live Multiplayer

- Encounter advances automatically on a short cadence.
- Players queue actions between ticks.
- Immediate acknowledgement keeps the system feeling responsive.

### Slower Sandbox Multiplayer

- Same combat rules, but with a slower wall-clock cadence.
- Better fit for lower-intensity social or builder-facing environments.

### Single-Player or Private Async

- Encounter may advance only when the player acts or explicitly advances time.
- Useful for private worlds, asynchronous story play, or pause-friendly modes.

### Paused or Gated Encounter

- Encounter state exists but does not auto-advance.
- Useful for waiting on player choices, scripted moments, or world-specific
  pacing rules.

## Benefits

- Better alignment with WR2's queue-based runtime.
- Easier to scale than a global combat sweep.
- Easier to shard naturally by encounter, room, world, or instance.
- Easier to test because outcomes are step-based and deterministic.
- Easier to support multiple world styles without rewriting combat logic.
- Easier to reason about when debugging or replaying combat behavior.

## Tradeoffs

- Combat will feel less twitchy than WR1.
- If cadence is too slow, the game may feel mushy.
- The client needs strong feedback around queued actions and upcoming
  resolution.
- Encounter bookkeeping becomes a first-class runtime concern.

These are acceptable tradeoffs if the goal is scalable, legible, and flexible
combat rather than twitch precision.

## Guidance For Future Implementation

Future agents should preserve these principles even if they choose different
concrete models:

- keep combat scoped to active encounters rather than global world scans
- separate logical combat timing from wall-clock scheduling
- make intent submission immediate and explicit
- keep actual state mutation inside deterministic encounter resolution
- emit canonical combat events through the normal WR2 event flow
- allow worlds or instances to choose pacing policy without rewriting combat
  semantics

## Open Questions

The following are intentionally left open for later design work:

- what exact cadence should be the default for live worlds
- whether encounters are scoped by room, party, instance, or some hybrid
- how initiative and tie-breaking are resolved within a step
- how interruptible casts should behave
- how much intent replacement is allowed between ticks
- how mob AI chooses and updates intents
- when and how solo or async worlds should auto-advance combat
- what minimum client feedback is required for queued combat to feel good

## Bottom Line

WR2 combat should not try to win by being the most precise real-time system.

It should aim to be:

- tactically rich
- operationally scalable
- deterministic
- instance-friendly
- adaptable to both live and async play

Encounter-scoped combat with discrete logical resolution steps is the most
promising direction for that goal.
