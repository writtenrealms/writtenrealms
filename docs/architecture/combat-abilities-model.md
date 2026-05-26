# WR2 Combat Ability Model

## Purpose

This document describes the WR2 active ability model and the implementation
direction for features that build on it.

The initial runtime is wired: player ability commands can be learned, queued,
substituted before scheduled resolution, resolved in encounter rounds, and used
for out-of-combat self utility. The current implementation covers direct
damage, healing, stun, damage-over-time, and heal-over-time. Multi-participant
frays, class grant manifests, feat-style choice slots, and richer effect
primitives remain roadmap items.

The goal is to make abilities:

- turn-based and encounter-scoped
- data-driven rather than hard-coded per archetype
- compatible with the existing combat formula profiles
- usable by class-based and classless worlds
- understandable to builders before implementation details leak into the UI
- performant with Postgres as the source of truth

Related documents:

- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [combat-resolution-formulas.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-resolution-formulas.md)
- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)

## Current Status

The combat formula layer already has concepts that are intended for abilities:

- `default_ability_profile`
- `basic_ability`
- `ability_power`
- ability-style mitigation through resilience
- healing profiles

Those profiles are formula primitives. Ability manifests use them by saying
"resolve this damage through `basic_ability`" or "resolve this heal through
`basic_heal`."

## Settled Direction

The current design decisions are:

- ability definitions should be first-class manifests, not embedded only inside
  the world manifest
- the runtime supports both one ability per manifest and bundled ability
  manifests; both normalize into the same internal definition shape
- queued primary abilities replace auto-attacks
- players may substitute a queued primary ability until the encounter round
  resolves
- invalid queued abilities fall back to auto-attacks when an auto-attack is
  legal
- out-of-combat abilities share the same schema as combat abilities
- known abilities are permanent until unlearned
- worlds have a configurable maximum number of known abilities, defaulting to 8,
  with an `uncapped` option
- multi-participant encounters resolve participants in fixed order by fray join
  order
- the first persistent effect primitives should include stun, damage-over-time,
  and heal-over-time

## WR1 Reference

WR1 had two active skill paths.

### Hard-Coded Archetype Skills

WR1 class skills lived mostly in Python:

- command classes in `advent/commands/combat.py`
- skill classes in `advent/skills/combat.py`
- attack classes in `advent/combat/attacks.py`
- effect classes in `advent/effects.py`

The skill class usually declared:

- code and display name
- archetype
- level
- cost and cost type
- cast duration
- cooldown
- attack class
- effect class
- custom validation

This allowed rich behavior, but the runtime became tightly coupled to WR1's
fixed archetypes and Python class hierarchy. Adding a truly new kind of skill
required code.

### Data-Driven Custom Skills

WR1 also had custom skills. These were stored as world data and interpreted at
runtime.

Useful fields included:

- `skill` / `code`
- `name`
- `intent`
- `cost`, `cost_type`, and `cost_calc`
- `damage`, `damage_type`, and `damage_calc`
- `cast_time`
- `cooldown`
- `effect`
- `effect_duration`
- `effect_damage`, `effect_damage_type`, and `effect_damage_calc`
- `consumes`
- `requires`
- `learn_conditions`

This was closer to the WR2 direction, but it still resolved through WR1's
real-time timing queue, WR1 attack classes, and WR1 effect classes.

## WR1 Lessons To Keep

WR2 should keep these ideas:

- abilities are world-authored data
- builders can define cost, targeting, damage, healing, effects, cooldowns, and
  requirements
- abilities can be learned, granted, or restricted
- ability output should use the same combat formulas as auto-attacks
- combat messages should identify the ability that caused the result

WR2 should not keep these constraints:

- fixed Python classes for warrior, mage, cleric, assassin, etc.
- wall-clock cast and channel timings as the core combat model
- separate attack subclasses for every authored skill
- hard-coded assumptions that all worlds have classes
- hard-coded assumptions that all ability power is "spell" power

## Core Action Economy

Each encounter participant gets one primary combat action per round.

If a participant has a queued primary ability, that ability is their primary
action for the round. If they do not, the engine falls back to their default
auto-attack.

```text
primary_action = queued_ability if present else basic_attack
resolve primary_action
```

This means queued abilities replace auto-attacks by default.

That should be the baseline because it keeps ability balance legible:

- an ability is a choice instead of free extra output
- heals and defensive actions have real opportunity cost
- combat logs remain readable
- active players get better decisions, not double turns
- builders can tune ability multipliers against the basic attack baseline

Extra combat lines can still exist, but they should be explicit:

- status ticks, such as damage-over-time or heal-over-time
- passive procs
- reactions
- bonus-action abilities, if that category is added later

Those are separate action types. They should not be the default behavior of
ordinary abilities.

## Performance Requirements

WR2 combat has to scale materially better than WR1. Ability resolution must not
recreate a global real-time bottleneck in a different form.

The guiding rule is:

```text
round resolution may read and lock only the active encounter's bounded runtime
state, and must not parse manifests or scan broad world/player data.
```

Important requirements:

- ability manifests are parsed and normalized when saved, imported, or reloaded,
  not during combat rounds
- command verbs should resolve through a per-world ability command index, not a
  scan of every ability definition
- actor ability access should be materialized into a compact known/granted set,
  so queue-time checks are set membership checks
- round resolution should preload participants, pending intents, cooldowns,
  active effects, and relevant stat snapshots in bounded queries
- effect ticks should be stored as encounter/participant runtime state and
  processed during encounter rounds, not scheduled as one database job per dot
  or hot tick
- validation primitives should use already-loaded participant state whenever
  possible
- `select_for_update` should lock only encounter participants and mutable rows
  involved in the current step
- writes should be batched where practical: health/resource updates, cooldown
  updates, active effect updates, and emitted events
- runtime code should avoid N+1 queries per participant, per ability component,
  or per active effect
- any future condition language used by ability requirements or mob AI should
  compile into known primitives, not execute arbitrary builder-authored code

The desired end state is that encounter round cost is roughly proportional to:

```text
participants in the encounter
+ pending intents
+ active effects on those participants
```

It should not be proportional to:

```text
players in the world
+ abilities in the world
+ rooms in the world
+ all cooldowns or effects owned by inactive actors
```

This should be backed by automated tests once implementation starts:

- query-count tests for queueing an ability
- query-count tests for resolving a typical round
- regression tests for dot/hot-heavy encounters
- load-oriented tests around many small independent encounters

## Runtime Concepts

The exact model names may change, but the engine should have these concepts.

### Ability Definition

An ability definition is authored world data. It describes what the ability is,
who can use it, what it costs, what target it needs, and what components it
resolves.

Definitions should be immutable during a single encounter resolution step. The
runtime can load and normalize them before execution.

### Ability Grant

An ability grant answers whether a participant can use a definition.

Grants may come from:

- class or archetype progression
- level
- quests
- trainers
- equipped items
- builder commands
- world defaults

The engine should not care where the grant came from once it is checking
runtime access.

Grant sources should be evaluated when progression changes, equipment changes,
or a learn/unlearn action occurs. Combat queueing and round resolution should
read the resulting materialized ability access, not re-evaluate the full grant
graph.

### Known Ability Set

A known ability set is the actor's materialized list of abilities that can be
queued or used, subject to contextual checks such as cooldowns, resources,
targeting, and current effects.

Known abilities are persistent until unlearned. The default world cap should be
8 known abilities. Worlds should be able to set a different integer cap or
declare known abilities `uncapped`.

The known set is a runtime/progression concern. It should not require scanning
all class definitions, quests, trainers, and item grants during each combat
round.

### Ability Choice Slot

WR1 feats used a useful pattern where a tier or slot represented one choice
from several available options. WR2 should keep room for that idea later.

Do not implement choice slots in the first pass, but leave the progression
model compatible with something like:

```yaml
ability_slots:
  - slot: tier_1
    choose_one:
      - power-strike
      - shield-slam
      - brace
```

That later feature should still materialize into the same known ability set.
The round resolver should not need to understand why a particular ability is
known.

### Combat Intent

A combat intent is a participant's pending choice for a future encounter round.

For active abilities, the intent should include:

- encounter id
- actor type and id
- ability key
- target reference, if any
- queued round number
- client command id or idempotency key
- snapshot of resolved command arguments

There should be at most one pending primary intent per participant.

If the player queues another primary ability before the round resolves, the new
intent replaces the previous one and emits a replacement acknowledgement.

### Active Effect

An active effect is runtime state applied to a participant, room, or encounter.

Examples:

- stun
- shield
- poison
- regeneration
- taunt
- silence
- temporary stat buff

Effects should be data-driven, versioned runtime records. They should not be
Python-only behavior hidden behind arbitrary classes.

## Authoring Model

Ability definitions should be authored outside the world manifest.

The preferred authoring shape is one ability per manifest:

Example:

```yaml
kind: ability
metadata:
  slug: power-strike
  name: Power Strike
spec:
  command:
    verbs:
      - strike
      - powerstrike
  action_type: primary
  target:
    type: hostile
    default: current_target
  availability:
    classes:
      - warrior
    min_level: 2
  cost:
    resource: stamina
    amount: 10
    calc: fixed
  cooldown:
    rounds: 3
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.6
      text:
        label: Power Strike
```

The runtime may also support bundled ability manifests for import convenience:

```yaml
kind: abilities
metadata:
  slug: warrior-starter-kit
spec:
  abilities:
    - slug: power-strike
      name: Power Strike
      action_type: primary
      target:
        type: hostile
        default: current_target
      components:
        - type: damage
          profile: basic_physical
          overrides:
            multiplier: 1.6
```

Both forms should normalize to the same internal ability definitions. The world
manifest should only hold small global ability policy, such as the known ability
cap:

```yaml
kind: world
spec:
  ability_progression:
    max_known: 8
```

For worlds without a known-ability cap:

```yaml
kind: world
spec:
  ability_progression:
    max_known: uncapped
```

Healing should use the same shape:

```yaml
kind: ability
metadata:
  slug: mend
  name: Mend
spec:
  action_type: primary
  target:
    type: ally
    default: self
  availability:
    classes:
      - cleric
    min_level: 3
  cost:
    resource: energy
    amount: 15
    calc: percent_max
  cooldown:
    rounds: 2
  components:
    - type: healing
      profile: basic_heal
      overrides:
        multiplier: 1.2
      text:
        label: Mend
```

An effect-bearing attack should be a list of components:

```yaml
kind: ability
metadata:
  slug: shield-slam
  name: Shield Slam
spec:
  action_type: primary
  target:
    type: hostile
    default: current_target
  requirements:
    eq:
      - actor.equipment.offhand.equipment_type
      - shield
  cooldown:
    rounds: 4
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.25
        can_dodge: false
      text:
        label: Shield Slam
    - type: effect
      effect: stun
      duration:
        rounds: 1
      apply: on_hit
```

## Relationship To Combat Profiles

Combat profiles remain the way damage and healing are resolved from stats.

An ability does not invent a parallel formula. It chooses a combat profile and
may provide narrow overrides.

Examples:

- a sword technique uses `basic_physical`
- a spell, psionic strike, tech attack, or ritual uses `basic_ability`
- a heal uses `basic_heal`
- a custom world can define `fire_spell`, `rifle_shot`, or `psychic_blast`
  profiles and have abilities point at them

This is why `basic_ability` exists as a default profile. Ability damage
components can call it directly.

## Class Attribution

WR2 abilities should be custom first.

Classes should influence ability access, not define separate hard-coded
execution paths.

Good:

```yaml
availability:
  classes:
    - warrior
  min_level: 5
```

Also good:

```yaml
kind: class
metadata:
  slug: warrior
spec:
  ability_grants:
    - ability: power-strike
      level: 2
    - ability: shield-slam
      level: 5
```

Both forms can normalize into the same grant data.

The runtime should only need to ask:

```text
does actor have access to ability X right now?
```

It should not need a warrior code path, mage code path, or class-specific
Python skill registry.

Worlds without classes should work by granting abilities through trainers,
quests, items, starting loadouts, or direct builder configuration.

## Ability Progression

The first progression model should be simple:

- abilities are known until unlearned
- a world can cap the number of known abilities
- the default cap is 8
- a world can set the cap to `uncapped`
- learning a new ability at the cap should fail until the actor unlearns one
- class, level, quest, trainer, and item rules may update the known set, but the
  command validator checks the known set directly

This is intentionally different from WR1's split among core skills, flex
skills, feats, and custom skills. WR2 should have one active ability concept.

The future choice-slot model should be layered on top:

- a slot can represent one selected ability from a set
- slots may come from class tiers, quests, or world progression
- changing a slot changes the known ability set
- the combat resolver still only sees the materialized known abilities

That keeps the performant runtime shape while preserving the WR1 feat-style
design space.

## Queueing An Ability

Using an ability command during combat should submit intent, not immediately
apply damage.

Flow:

1. Player enters `strike goblin`.
2. Command parser resolves `strike` to an ability definition.
3. Planner validates obvious requirements:
   - actor has access
   - actor is in the encounter or can start one
   - target exists and is legal
   - ability is not currently on cooldown
   - actor appears able to pay the cost
4. Planner stores or replaces the actor's pending primary intent.
5. Player receives an immediate acknowledgement.
6. The ability resolves during the next encounter round.

The acknowledgement should be explicit:

```text
You prepare Power Strike.
```

If the player replaces the queued action:

```text
You switch to Mend.
```

## Resolution-Time Validation

Abilities must be validated again when the encounter round resolves.

Between queue time and resolution time:

- the target may die
- the target may leave
- the actor may be stunned
- the actor may spend the resource elsewhere
- the actor may lose access through equipment changes or effects
- the encounter may end

If the ability is invalid at resolution time, the baseline behavior should be:

1. emit a private failure event explaining why the ability did not fire
2. fall back to the actor's default auto-attack if one is legal
3. do not charge the ability cost
4. do not start the ability cooldown

This keeps early WR2 combat forgiving and avoids turns disappearing because of
ordinary multiplayer timing races.

Worlds can later opt into stricter behavior, such as consuming the round on
interruption, but that should not be the first default.

## Cost And Cooldown Timing

For combat abilities, cost and cooldown should be tied to encounter resolution,
not command submission.

Recommended defaults:

- prevalidate cost when queueing for good feedback
- pay cost when the ability resolves
- start cooldown when the ability resolves
- do not pay cost or start cooldown if the ability falls back before resolving

Cooldowns should be expressed in logical combat rounds:

```yaml
cooldown:
  rounds: 3
```

This fits WR2's pacing model better than WR1's wall-clock cooldown seconds. The
same ability should behave consistently whether a world resolves combat every
0.75 seconds, every 2 seconds, or manually.

Out-of-combat utility abilities may need wall-clock cooldowns later, but those
should be modeled separately from encounter-bound combat cooldowns.

Out-of-combat abilities should share the same ability schema. For example, a
self-heal can use the same `target`, `cost`, `cooldown`, and `components`
structure whether the actor is in combat or standing alone. The difference is
execution policy:

- in combat, a primary ability is queued and resolves during an encounter round
- out of combat, a safe utility ability may resolve immediately through the
  normal command/action/event pipeline

The schema should not fork just because an ability is usable outside combat.

## Windups And Channels

WR1 used wall-clock cast times and channel timings. WR2 should translate that
design space into logical rounds.

Recommended future fields:

```yaml
windup:
  rounds: 1
channel:
  ticks: 3
  component_interval_rounds: 1
```

Initial implementation should probably skip windups and channels. Start with
abilities that resolve on the next encounter round.

When windups are added:

- a windup should occupy the actor's primary action
- the pending ability should become a committed casting intent
- interruption rules should be explicit effect rules, not hidden timing side
  effects
- completing the windup should resolve the ability component list

When channels are added:

- a channel should be represented as repeated scheduled components across
  encounter rounds
- each tick should emit normal combat events
- cancel and interrupt behavior should be explicit

## Effect Model

Effects should be declarative runtime records with known hooks.

Useful hook phases:

- `on_apply`
- `round_start`
- `before_primary_action`
- `before_damage`
- `after_damage`
- `after_primary_action`
- `round_end`
- `on_expire`

Effects should modify combat through explicit primitives, such as:

- prevent primary action
- prevent dodge
- modify a combat profile field
- add or subtract a stat modifier
- absorb damage
- apply periodic damage
- apply periodic healing
- force target selection
- expire on damage

Effects should not be arbitrary code in authored YAML. If a new behavior is
needed, the engine should add a new validated primitive that builders can use.

The first playable effect set should include:

- `stun`: prevents the target's primary action for a number of rounds
- `dot`: applies periodic damage during encounter rounds
- `hot`: applies periodic healing during encounter rounds

Those three are important enough to design early. They also exercise the main
runtime problems: action prevention, repeated effect ticks, expiration, and
clear combat messages.

Other primitives, such as absorbs, buffs, debuffs, taunts, silences, dispels,
and expire-on-damage behavior, should be deferred until the first effect runtime
has proven stable.

## Encounter Round Pipeline

The ability-aware encounter round follows this shape:

```text
load encounter and lock participants
increment round number
resolve round_start effects

for each participant in deterministic order:
  action = pending_primary_intent or default_auto_attack
  if action is invalid:
    emit private failure
    action = default_auto_attack if legal
  resolve action components
  clear consumed pending intent
  stop if encounter ended

resolve round_end effects
schedule next encounter step if still active
emit events
```

Participant ordering should be fixed by fray join order. This keeps the first
multi-participant model deterministic, fast, and easy to explain.

Join order should be stored on encounter participant runtime state. Round
resolution should sort already-loaded participants by that value, not issue
ordering queries while resolving each action.

## Component Resolution

Ability components should resolve in authored order.

Common component types:

- `damage`
- `healing`
- `effect`
- `resource_change`
- `movement`
- `summon`
- `dispel`
- `threat`

The first implementation should be narrower:

- damage through `resolve_attack`
- healing through the same combat formula result shape
- stun effects that prevent a primary action
- dot effects that tick damage during encounter rounds
- hot effects that tick healing during encounter rounds

Damage and healing components should include enough event data to explain the
result:

```yaml
data:
  ability: power-strike
  component: damage
  profile: basic_physical
  damage_base: 17
  damage_dealt: 26
  damage_taken: 23
  damage_mitigated: 3
  is_crit_hit: true
  outcome: hit
```

## Events And UI Feedback

The UI needs clear feedback at two moments:

- when an ability is queued
- when an ability resolves

Suggested event types:

- `combat.intent.queued`
- `combat.intent.replaced`
- `combat.intent.rejected`
- `notification.combat.ability`
- `notification.combat.attack`
- `notification.combat.healing`
- `effect.start`
- `effect.tick`
- `effect.expire`
- `ability.cooldown.start`
- `ability.cooldown.ready`

The exact names can change, but the event data should distinguish:

- ability key
- display label
- actor
- target
- round id
- component type
- formula profile
- costs paid
- cooldown started
- outcome

The client should be able to render a pending action without guessing from text.

## Mob Abilities

Mobs should use the same ability definitions as players.

Mob templates can define a combat loadout:

```yaml
combat_ai:
  abilities:
    - ability: power-strike
      weight: 3
    - ability: shield-slam
      weight: 1
      conditions:
        target_not_affected_by: stun
```

During encounter resolution, mob AI should choose or update a pending primary
intent before its action is selected. The chosen ability should still pass the
same validation, cost, cooldown, and component pipeline as player abilities.

## Persistence Direction

The current `CombatEncounter` model is intentionally minimal and scoped to the
first `kill <mob>` flow. Ability-aware combat will need richer runtime state.

Preferred direction:

- keep canonical ability definitions in builder/world data
- store learned/granted abilities in player progression data
- store pending combat intent in encounter participant runtime data
- store active effects in participant or encounter runtime data
- keep cooldowns as structured runtime data, not opaque text
- store encounter participant join order explicitly
- store normalized ability definitions and command aliases in indexed/cached
  runtime-friendly shape
- store actor known ability ids in a shape that supports fast membership checks

Conceptual structures:

```text
CombatEncounter
CombatParticipant
CombatIntent
CombatCooldown
ActiveEffect
AbilityDefinition
ActorKnownAbility
```

These do not all need to be separate tables on day one. The important boundary
is that encounter state should be inspectable, versioned, and safe to rebuild
where possible.

For performance, the resolver should prefer loading a compact set of rows for an
encounter over repeatedly consulting world manifests or player progression
rules. Postgres remains authoritative, but combat resolution should operate on
pre-normalized data.

## Implementation Phases

### Phase 1: Primary Ability Intent

- define normalized ability schema
- support `kind: ability` manifests
- optionally support bundled `kind: abilities` manifests
- add world-level `ability_progression.max_known`, defaulting to 8 and allowing
  `uncapped`
- implement known ability storage and fast membership checks
- implement ability command parsing
- store one pending primary intent per player encounter
- resolve damage abilities through existing `resolve_attack`
- resolve healing abilities through the combat formula result shape
- pay simple resource costs at resolution time
- start simple round-based cooldowns at resolution time
- make queued ability replace auto-attack
- fall back to auto-attack if the queued ability is invalid
- emit queue and resolution events
- keep round resolution bounded to preloaded encounter state

Limit Phase 1 to direct damage and direct healing. Skip windups, channels,
reactions, bonus actions, and complex effects.

### Phase 2: First Effects

- define active effect runtime schema
- implement simple effect primitives
- support stun, dot, and hot
- route effect ticks through encounter rounds
- emit effect lifecycle events
- add query-count tests for dot/hot-heavy rounds

### Phase 3: Access And Progression

- implement class grants and classless grants
- validate learned abilities
- expose cooldown and known ability state to the client
- add builder tests for class-restricted and classless abilities
- keep room for future feat-style choice slots

### Phase 4: Richer Combat

- windups
- channels
- interrupts
- mob ability AI
- group encounters
- PvP ability targeting
- reactions and explicit bonus actions
- absorbs, buffs, debuffs, taunts, silences, dispels, and expire-on-damage
  behavior
- combat previews and builder debugging tools

## Documentation Implication

The combat formula builder guide should separate:

- auto-attack formulas
- ability profiles and ability components

Builder guide language about `basic_ability` should clearly say that it is a
formula profile that ability components can reference, not a standalone command
players type.

## Remaining Design Work

The major baseline decisions are settled. Remaining design work should focus on
implementation details:

- exact database tables versus JSON runtime records for the first pass
- exact manifest validation errors and import/export behavior
- exact command syntax for learning, unlearning, and listing abilities
- exact client payloads for known abilities, queued abilities, and cooldowns
- exact query-count budgets for queueing and resolving abilities
- future feat-style choice slots where one progression slot selects one of
  several possible abilities

## Bottom Line

WR2 abilities should be custom, declarative combat actions that resolve inside
encounter rounds.

The default action economy should be:

```text
queued primary ability replaces auto-attack
no queued ability means auto-attack
status ticks and reactions are explicit extras
```

Classes can grant or restrict abilities, but classes should not own hard-coded
runtime behavior.

This keeps the good part of WR1 custom skills while moving combat toward WR2's
deterministic, queue-friendly, builder-authored architecture.
