# WR2 Combat Buffs And Active Effects

## Purpose

Buffs and debuffs are a core part of the WR2 combat direction. They should be
first-class runtime effects, not one-off Python branches or hidden text fields.

This document describes the target architecture for effects that:

- regenerate health, energy, or stamina
- modify stats or authored attributes
- prevent or alter actions
- grant special states such as invisibility
- trigger follow-up behavior, such as restoring energy when an attack lands

This is an architecture target. The current runtime covers the first playable
encounter-scoped effect primitives: stun, damage-over-time, heal-over-time,
resource changes, damage absorption, and `after_damage` procs. It also supports
character-scoped outgoing damage modifiers for refreshable room-wide buffs.

Related documents:

- [combat-abilities-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-abilities-model.md)
- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [combat-resolution-formulas.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-resolution-formulas.md)
- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [scoped-state-system.md](/Users/teebes/code/writtenrealms/docs/architecture/scoped-state-system.md)

## Terminology

Use **active effect** as the runtime term.

Use **buff** for a beneficial active effect and **debuff** for a harmful active
effect. Buff and debuff are player-facing and builder-facing categories; the
runtime should process them through the same active effect system.

Examples:

- a stun is an active effect
- a poison dot is an active effect
- a regeneration buff is an active effect
- an armor debuff is an active effect
- invisibility is an active effect
- an "energized strikes" buff that restores energy after attacks land is an
  active effect with an attack hook

## Design Goals

- Make buffs data-driven and inspectable.
- Keep effect behavior bounded to validated engine primitives.
- Support both encounter-scoped and character-scoped effects.
- Let effects participate in stat snapshots, resource updates, combat hooks,
  and event payloads.
- Keep encounter round cost proportional to participants and active effects in
  the encounter.
- Avoid a global effect heartbeat that scans inactive actors.
- Use the existing WR2 condition DSL for conditional logic.
- Emit enough events for the client and builder tools to explain what happened.

## Non-Goals

- Do not allow arbitrary Python, JavaScript, Lua, or free-form scripts inside
  effect definitions.
- Do not make builders define new engine hook phases at runtime.
- Do not make every possible combat stat dynamic.
- Do not use scoped state as the main store for active combat effects.
- Do not rely on wall-clock timers for ordinary combat-round effects.

Scoped state remains useful for quest memory, room state, counters, and similar
mutable local state. Active effects need stronger lifecycle, stacking,
expiration, stat, and event semantics than generic scoped state should carry.

## Core Model

The runtime should distinguish between:

- **effect definition**: authored data that describes what an effect does
- **active effect instance**: runtime data applied to a target

An ability, item, trigger, quest reward, mob AI action, or system event may
create an active effect instance. Once created, encounter resolution should not
parse YAML. It should consume normalized runtime records.

Conceptual active effect fields:

```text
ActiveEffect
  id
  world_id
  origin_encounter_id    # provenance, not clock ownership
  label
  category              # buff, debuff, neutral
  source_ref            # player, mob, item, room, system
  target_ref            # player, mob, participant, room, encounter
  scope                 # encounter, character, room, world
  duration              # rounds, expires_at, until_event, permanent
  remaining_rounds
  started_round
  expires_at
  stack_key
  stack_count
  stacking_policy
  primitives            # normalized effect primitives
  conditions            # WR2 condition DSL, if any
  flags                 # dispellable, visible, hidden, harmful, etc.
  next_tick_at
  last_tick_token
  source_snapshot
  version
```

WR2 stores active effect instances as explicit `ActiveEffect` rows. Both
encounter- and character-scoped effects use the same canonical table. The
source and target are typed player/mob references, while `source_snapshot`
preserves frozen combat stats and attribution metadata if the live source later
disappears. Exactly one target is required.

Migration `spawns.0138_active_effects` converts the former player and encounter
JSON stores and then removes those columns. It is intentionally irreversible
because the old stores cannot represent all character-scoped rows. Deploy it
with backend and worker processes quiesced so no legacy process can write the
JSON columns between backfill and removal.

## Scope And Lifetime

Effects can have different scopes:

- `encounter`: exists only for one fight
- `character`: follows a player or mob outside a single encounter
- `room`: affects anyone in a room
- `world`: global runtime effect for a world or instance

Encounter scope remains appropriate for fight-only mechanics such as stun,
threat, and opponent-specific barriers. Character scope is used for effects
that follow players or mobs, including DOTs, HOTs, long buffs, food buffs,
curses, and resting effects.

Durations should be explicit:

- `rounds`: decremented by encounter rounds
- `expires_at`: wall-clock expiration for out-of-combat or long-lived effects
- `until_event`: expires when a known event occurs, such as dealing damage
- `permanent`: remains until dispelled, removed, or the owner resets

Combat effects should prefer `rounds`. Wall-clock duration is appropriate only
when the effect must survive outside an active encounter.

Round advancement is owned by the target actor, not the origin encounter. An
active encounter advances effects on its participants at round start. When a
target has no active encounter, an indexed, bounded actor-effect pulse advances
due effects. This gives each target one clock and prevents re-engagement or
multiple origin encounters from double-ticking the same effect.

## Effect Primitives

An active effect is made of one or more validated primitives. Each primitive has
known behavior and known hook phases.

Recommended primitive families:

- `resource_change`: add or subtract health, energy, or stamina
- `stat_modifier`: modify canonical combat stats
- `attribute_modifier`: modify authored attributes before stat derivation
- `action_rule`: prevent, require, replace, or alter an action
- `combat_modifier`: alter a combat profile input or formula stage
- `damage_absorb`: absorb incoming damage up to a budget
- `status_flag`: grant a known state such as invisibility or silence
- `proc`: run a bounded primitive when a combat hook occurs
- `dispel`: remove active effects matching validated criteria

Effects should not carry arbitrary executable behavior. If a builder needs a
new kind of behavior, the engine should add a primitive with a documented
schema and tests.

## Resource Regeneration Buffs

Resource regeneration buffs should use `resource_change`.

Supported resources should use canonical engine names:

- `health`
- `energy`
- `stamina`

Resource changes should clamp to legal values:

- health cannot exceed effective `health_max`
- energy cannot exceed effective `energy_max`
- stamina cannot exceed effective `stamina_max`
- resources cannot go below zero unless a future explicit primitive allows it

Conceptual example:

```yaml
components:
  - type: effect
    effect: regeneration
    category: buff
    target: ally
    duration:
      rounds: 4
    tick:
      phase: round_start
      every_rounds: 1
      primitives:
        - type: resource_change
          resource: health
          amount: 8
          calc: fixed
          target: effect.target
```

The same shape should cover energy or stamina:

```yaml
primitives:
  - type: resource_change
    resource: energy
    amount: 5
    calc: fixed
    target: effect.target
```

Useful calculations:

- `fixed`: add or subtract a fixed amount
- `percent_max`: percentage of the target's current effective max resource
- `percent_base`: percentage of the target's base resource before modifiers

This mirrors the current ability cost vocabulary and keeps resource math
consistent.

## Damage Absorption Barriers

Damage absorption buffs should use `damage_absorb`.

The primitive represents a finite pool of prevented incoming damage. The active
effect expires when either:

- `remaining_rounds` reaches zero
- every `damage_absorb` primitive on the effect has spent its remaining pool

Builder shape:

```yaml
components:
  - type: effect
    effect: ward
    category: buff
    target: self
    duration:
      rounds: 3
    primitives:
      - type: damage_absorb
        amount: 25
        calc: fixed
        damage_types: [physical, ability]
```

Supported calculations:

- `fixed`: the absorb pool is exactly `amount`
- `percent_max`: the absorb pool is `amount` percent of the target's current
  max health when the effect is applied

The pool can also include additive scaling terms. Scaling terms are evaluated
from the effect source's combat stats when the effect is applied:

```yaml
primitives:
  - type: damage_absorb
    amount: 0
    calc: fixed
    scaling:
      - source: ability_power
        multiplier: 0.5
```

Multiple scaling terms are summed:

```yaml
primitives:
  - type: damage_absorb
    amount: 0
    calc: fixed
    scaling:
      - source: ability_power
        multiplier: 0.1
      - source: health_max
        multiplier: 0.3
```

`damage_types` is optional. If omitted, the barrier absorbs all incoming damage
types. If present, it filters against the combat result's `damage_type`, such as
`physical` or `ability`.

Absorption happens after the normal combat formula resolves damage and before
health is reduced. Combat attack events should preserve the formula result's
pre-absorption fields, set `damage_absorbed` to the prevented amount, and report
only the unabsorbed remainder as `damage_taken`.

## Stat And Attribute Buffs

Stat buffs should modify the fixed canonical stat contract described in
`stats-formulas-and-classes.md`.

Examples:

- `attack_power +10`
- `ability_power +15`
- `armor +20`
- `crit +5`
- `dodge +3`
- `energy_max +25`

Attribute buffs should modify authored attributes, such as a world's `might` or
`focus`, before those attributes feed stat formulas.

The runtime boundary is:

- builders may author attribute or stat modifiers
- combat resolution consumes effective stats
- the combat formula pipeline does not discover new semantics from the effect

Recommended modifier shape:

```yaml
primitives:
  - type: stat_modifier
    stat: attack_power
    op: add
    amount: 12
```

```yaml
primitives:
  - type: attribute_modifier
    attribute: might
    op: add
    amount: 2
```

Supported operations should be limited:

- `add`: flat additive change
- `multiply`: multiplicative change
- `set_min`: enforce a minimum
- `set_max`: enforce a maximum

Modifier ordering must be deterministic. Recommended order:

1. base stats from level, class, authored attributes, and progression
2. equipment modifiers
3. persistent character effects
4. encounter-scoped active effects
5. clamps and caps

Within a layer, flat additions should apply before multipliers. This gives
builders predictable results and makes debug output easier to explain.

## Stat Snapshots

WR2 combat should not recompute full stat formulas for every attack.

Effects that change stats or attributes should update or overlay a stat
snapshot at clear boundaries:

- when a persistent character effect is applied, refreshed, dispelled, or
  expired
- when an encounter-scoped modifier starts or ends
- once per encounter step when the resolver builds participant snapshots

Acceptable implementation paths:

- persistent effects update the character's stored effective stat snapshot
- encounter-scoped effects are loaded with the encounter and overlaid onto a
  participant snapshot for the current step

Either path is acceptable if round resolution reads a bounded set of
participant and effect records and does not scan broad world data.

## Special Status Effects

Some buffs are not numeric. They grant a known status that other systems can
read.

Examples:

- `invisible`
- `hidden`
- `silenced`
- `rooted`
- `shielded`
- `haste`
- `taunted`

These should use `status_flag` or a more specific primitive when one exists.

Invisibility is important enough to document explicitly. It should not be just
a text label on an effect. It affects:

- room enter and exit notifications
- who can target the actor
- whether mobs can choose the actor as a target
- whether player state payloads expose visibility
- which combat events are broadcast to other players
- trigger and quest event context when visibility matters

Conceptual example:

```yaml
components:
  - type: effect
    effect: invisibility
    category: buff
    target: self
    duration:
      rounds: 3
    primitives:
      - type: status_flag
        flag: invisible
    expire:
      on:
        - outgoing_damage
```

The exact event names can change, but the expiration events must come from an
allowlist. Builders should not write arbitrary event predicates.

## Combat Hooks And Procs

Buffs often need to react to combat events. For example:

- restore energy after the buffed actor lands an attack
- gain armor after taking damage
- expire after blocking one hit
- apply a bleed when the actor crits
- remove invisibility when the actor deals damage

These should be modeled as `proc` primitives that subscribe to known hook
phases. Useful hook phases include:

- `round_start`
- `before_primary_action`
- `before_attack_roll`
- `after_attack_roll`
- `before_damage`
- `after_damage`
- `after_primary_action`
- `round_end`
- `on_expire`

The hook payload should expose structured fields such as actor, target,
ability, profile, outcome, damage, healing, round id, and active effect id.

Conditional logic inside a proc must use the WR2 condition DSL from
`backend/core/condition_dsl.py`. Do not invent a second predicate format for
effects.

Conceptual example for "each landed attack restores energy":

```yaml
components:
  - type: effect
    effect: energized-strikes
    category: buff
    target: ally
    duration:
      rounds: 4
    primitives:
      - type: proc
        phase: after_damage
        conditions:
          all:
            - eq: [event.actor, "{effect.target}"]
            - gte: [event.damage_taken, 1]
        actions:
          - type: resource_change
            resource: energy
            amount: 5
            calc: fixed
            target: effect.target
```

This is not a final manifest schema. It shows the important architecture:

- the buff is an active effect on the target
- attack processing emits or enters a known hook phase
- the proc checks structured event context with the condition DSL
- the action is a bounded resource change primitive
- energy is clamped to `energy_max`

## Action Rules

Action-altering effects should be explicit primitives, not hidden special cases.

Examples:

- stun prevents the primary action
- silence prevents abilities tagged as verbal, magical, or configured types
- root prevents flee or movement
- haste grants a configured bonus action or changes cooldown behavior
- taunt restricts target selection

Recommended shape:

```yaml
primitives:
  - type: action_rule
    phase: before_primary_action
    rule: prevent
    actions:
      - primary
    reason: stunned
```

This lets the encounter pipeline ask active effects for action rules at known
phases without hard-coding every effect name into the round resolver.

## Stacking And Refresh

Every active effect needs a stacking policy. Buff systems become impossible to
balance if stacking is implicit.

Recommended default:

- same `stack_key`, same source, and same target refreshes the existing effect
- different `stack_key` creates a separate effect
- stack count defaults to 1
- maximum stack count defaults to 1

Supported policies:

- `refresh`: reset duration, keep one stack
- `replace`: replace the old effect instance
- `stack_count`: add a stack up to `max_stacks`
- `stack_duration`: extend duration up to `max_rounds` or `expires_at`
- `independent`: allow separate instances, but require an explicit cap

Magnitude conflicts should be deterministic. For example, an armor buff from
the same source should not randomly choose which amount wins. A policy should
say whether to keep newest, keep strongest, refresh strongest, or stack.

## Dispel, Purge, And Cleanse

Removal effects should target active effect metadata, not arbitrary text.

Useful matching fields:

- category: buff, debuff, neutral
- tags: poison, magic, physical, curse, stealth
- source type
- target type
- dispellable flag
- stack key
- effect key

Conceptual example:

```yaml
primitives:
  - type: dispel
    target: enemy
    match:
      category: buff
      tags: [magic]
    count: 1
```

Conditions for dispel eligibility should use the condition DSL if they need
logic beyond structured metadata matching.

## Targeting

Effect targeting should distinguish the ability target from the effect target.

Examples:

- damage enemy, buff self
- heal ally, apply a short defensive buff to that ally
- attack enemy, apply a debuff to that enemy on hit
- shout in a room, apply a room-wide debuff

Useful effect target references:

- `actor`
- `ability.target`
- `effect.source`
- `effect.target`
- `current_target`
- `encounter.enemies`
- `encounter.allies`
- `room.players`
- `room.mobs`

The first implementation can support a smaller set. The important boundary is
that targets are structured references, not text interpolation.

## Event Output

Buffs need lifecycle and proc events. Suggested event types:

- `effect.applied`
- `effect.refreshed`
- `effect.stack_changed`
- `effect.tick`
- `effect.proc`
- `effect.expired`
- `effect.dispelled`
- `effect.removed`

Useful event data:

```yaml
data:
  effect:
    id: 123
    key: energized-strikes
    label: Energized Strikes
    category: buff
    stack_count: 1
    remaining_rounds: 3
  source:
    key: player.1
    name: Mira
  target:
    key: player.2
    name: Rowan
  primitive:
    type: resource_change
    resource: energy
    amount: 5
  round_id: encounter:44:7
```

The client should not have to parse combat text to update buff icons, durations,
resource bars, or visibility state.

## Relationship To Abilities

Abilities should apply active effects through components.

Simple direct output remains a direct component:

- `damage`
- `healing`
- immediate `resource_change`, once implemented

Ongoing or reactive behavior should be an active effect:

- dot
- hot
- regeneration
- stat buff
- shield
- invisibility
- energy-on-attack proc

This keeps ability resolution and effect lifecycle separate:

1. ability resolves
2. ability applies an active effect
3. later encounter phases process the active effect
4. effect lifecycle events tell clients what changed

## Relationship To Combat Formulas

Effects should plug into the existing combat formula pipeline.

They may:

- modify actor or target effective stats before a formula resolves
- modify selected profile fields through validated overrides
- absorb damage after mitigation
- react to formula output through a hook

They should not:

- replace the formula pipeline with custom code
- reorder dodge, crit, mitigation, and variance
- introduce unvalidated world-specific meanings for canonical stats

If a buff needs to modify a formula stage, that stage should be a named
primitive such as `combat_modifier`, and the profile debug output should show
the modifier.

## Persistence And Cleanup

Effect cleanup must be reliable.

Effects should expire when:

- remaining rounds reaches zero
- `expires_at` passes
- an `until_event` expiration fires
- a dispel or cleanse removes them
- the owning encounter ends, for encounter-scoped effects
- the owning actor is deleted or leaves the relevant runtime context

Encounter-scoped effects should not leak into character state after combat.
Character-scoped effects should survive encounter boundaries when their lifetime
requires it.

Harmful character-scoped periodic effects also maintain a combat tag for their
live source and target. Engagement controls legal attacks; the combat tag only
controls combat-exit policies such as regeneration and resting. Offline actors
and actors in stopped worlds pause their effect clocks and do not hold combat
tags. Damage attribution belongs to the active effect rather than encounter
membership, so a lethal remote tick resolves death, corpse placement, rewards,
and conditional loot against the original source in the effect transaction and
emits the normal `quest.mob.killed` event for quest credit after commit.

Detached effect events use `GameEventOutbox`: the pulse writes its event rows in
the same transaction as damage, death, and rewards, then the heartbeat drains
them with at-least-once delivery. Quest and trigger subscribers persist an
event/subscriber receipt before mutating database state, so a publish retry
cannot grant the same quest progress twice. Websocket, Redis, and Celery side
effects remain at-least-once and must tolerate duplicate delivery.

## Performance Requirements

Encounter resolution should load active effects in bounded scope:

```text
participants in the encounter
+ active effects on those participants or the encounter
```

It should not scan:

```text
all players in the world
+ all active effects in the world
+ all effect definitions in builder data
```

Implementation requirements:

- normalize authored effect definitions before combat
- avoid parsing YAML during encounter resolution
- load effect primitives with participant state
- process hook phases with already-loaded effect records
- batch resource, stat, stack, and expiration writes where practical
- emit events after state mutation
- include query-count tests for effect-heavy rounds

## Recommended Implementation Phases

### Phase 1: Unified Active Effect Shape

- normalize the current stun, dot, and hot behavior into one active effect
  record shape
- add lifecycle events for apply, tick, and expire
- keep effect processing encounter-scoped
- preserve bounded round resolution

### Phase 2: Resource And Stat Buffs

- add `resource_change` for health, energy, and stamina
- add `damage_absorb` for finite shield and barrier effects
- add `stat_modifier` for canonical stats
- define snapshot recompute or overlay behavior
- add stacking policies
- add builder validation for resource and stat names

### Phase 3: Special Status Effects

- add `status_flag`
- implement invisibility as a real visibility primitive
- add silence, root, or other high-value flags as needed
- ensure state sync and combat events expose active effect state

### Phase 4: Hooks And Procs

- define hook payloads for attack, damage, healing, and action phases
- add `proc` primitives
- require condition DSL for proc conditions
- implement effects such as energy-on-attack, thorns, and expire-on-damage
- add tests for hook ordering and resource clamping

### Phase 5: Dispel And Richer Interactions

- add `dispel` primitives
- add metadata tags and dispellable flags
- support multi-source stacking where explicitly configured
- add builder and debug tooling for active effect inspection

## Open Questions

- Should reusable effect definitions become their own manifest kind, or should
  they remain embedded in abilities and items until duplication hurts?
- Which character-scoped effects need wall-clock expiration in the first pass?
- How should party, group, and PvP targeting affect buff target references?
- Which effect metadata should be visible to other players by default?
- Should very long-lived effects use a separate persistence table immediately?

These questions should not block the core direction: WR2 needs a validated
active effect system with resource, stat, status, and hook primitives.
