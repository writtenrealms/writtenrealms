# WR2 Combat Ability Model

## Purpose

This document describes the WR2 active ability model and the implementation
direction for features that build on it.

The initial runtime is wired: player ability commands can be learned, queued,
substituted before scheduled resolution, resolved in encounter rounds, and used
for out-of-combat self-targeted abilities. Reusable room and mob Trainer
Profiles can apply conditional, profile-scoped choice limits. The current
implementation covers direct damage, healing, cast-time windups, stun,
damage-over-time, heal-over-time, interrupts of committed casts, and root
effects whose action rules prevent fleeing.
Multi-participant frays, class grant manifests, feat-style choice slots, and
richer effect primitives remain roadmap items.

The goal is to make abilities:

- turn-based and encounter-scoped
- data-driven rather than hard-coded per archetype
- compatible with the existing combat formula profiles
- usable by class-based and classless worlds
- understandable to builders before implementation details leak into the UI
- performant with Postgres as the source of truth

Related documents:

- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [combat-buffs-and-effects.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-buffs-and-effects.md)
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
- queued abilities that consume the primary action replace auto-attacks
- players may substitute a queued ability until the encounter round
  resolves
- invalid queued abilities fall back to auto-attacks when an auto-attack is
  legal
- out-of-combat abilities share the same schema as combat abilities
- known abilities are permanent until unlearned
- worlds have a configurable maximum number of known abilities, defaulting to 8,
  with an `uncapped` option
- Trainer Profiles can independently restrict who may learn from their catalog
  and how many abilities from that catalog a player may currently know
- normal contact rolls a stable encounter initiative order once and stores it on
  the encounter; charge, ambush, and prepared attacks can override the first
  primary action through opener priority
- the implemented persistent effect primitives include stun, damage-over-time,
  heal-over-time, and root-style action rules that prevent fleeing
- `interrupt` is a normalized ordered component that cancels committed
  `casting` state and recognizes future `channeling` state; it never cancels a
  replaceable `queued` intent

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

If a participant has a queued ability that consumes the primary action, that
ability is their primary action for the round. If they do not, the engine falls
back to their default auto-attack.

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
- interrupt resolution must read and mutate the target's pending intent from
  the actor state already locked for the encounter step; it must not scan the
  target's encounters, ability catalog, or world state per component
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

### Training Providers

Ability teaching is authored through reusable Trainer Profiles. A profile owns
an ordered list of existing ability definitions and may be attached to a room
or a mob definition:

- a room attachment is an always-available, fixed-location provider
- a mob attachment is available only while an eligible spawned copy is present
- `present` accepts any non-pending copy; `alive_and_present` also requires a
  living copy

An unattached profile is a draft and has no runtime effect. Once a profile is
attached anywhere in the definition world, its abilities require an eligible
provider in the player's current room for both learning and unlearning.
Abilities absent from every attached profile remain ungated by location.

Provider selection does not add provider-specific command grammar. Bare
`learn` and `unlearn` list and number eligible abilities at the current
location; `learn <number>` and `unlearn <number>` resolve against those current,
bounded catalogs, while named forms remain available. Learning checks providers
in room-first, then stable mob-id order, skipping a profile whose condition
fails or whose quota is full. The first eligible provider with capacity teaches
the ability. Unlearning remains available through any local provider containing
the ability, irrespective of its learning condition or quota.

Each Trainer Profile may own a learning policy:

```yaml
learning:
  conditions:
    in:
      - actor.archetype
      - [warlord, tidecaller, mystic, moonstalker]
  max_known: 2
```

`conditions` uses the existing query-free condition DSL. `max_known` is a
positive integer or `uncapped`. A missing policy preserves pre-policy behavior:
the profile does not add a class gate or quota. Policy evaluation is an
additional gate and never overrides the ability's own availability or
requirements. Manifest parsing rejects malformed policies; the defensive
runtime treats malformed directly persisted data as denied rather than
silently opening the catalog.

The current usage for a profile is the size of the intersection between the
player's materialized known-ability set and the profile's complete membership.
The count therefore includes starting, quest, item, and administrative grants,
as well as inactive or currently unavailable abilities. It is acquisition-path
independent. Unlearning one member frees one slot. Lowering a cap does not
delete progression; an over-limit player retains known abilities but cannot
learn another profile member until below the cap.

The profile id is the quota boundary. All room and mob attachments reusing a
profile share that allowance. Distinct profiles have independent quotas, even
when their catalogs overlap; a known ability in the overlap counts toward each
profile. Builders who need multiple providers to share one allowance must
reuse one profile. The world-wide known-ability cap remains independent, and a
learn operation must satisfy both limits.

The existing ability command events retain their `trainer` and `trainers`
fields, but each provider is a typed object with `type`, `id`, `key`, and
`name`. This lets clients distinguish room and mob providers without parsing
text or overloading database ids.

Trainer discovery must use indexed attachment and profile relations rather
than scanning every room or JSON definition for each command. Instance rooms
resolve Trainer Profiles from their base definition world, just as they resolve
the inherited ability catalog. Policy enforcement must preserve the bounded
provider and 100-entry curriculum limits, batch-load profile memberships and
the player's known-set intersection, and avoid a query per provider or ability.
The final quota check and insertion occur while holding the existing player
progression row lock so concurrent learn requests cannot claim the same last
slot. Popular shared profiles remain read-only and are never a contention lock.

### Profile Choices And Future Ability Slots

WR1 feats used a useful pattern where a tier or slot represented one choice
from several available options. A Trainer Profile quota now covers the simpler
case of choosing up to N currently known abilities from one trainer catalog.
It is not a permanent progression-slot assignment: unlearning frees capacity,
and separately authored profiles are separate quota boundaries.

A future tier or quest slot may still need a named boundary shared across
several different catalogs. Keep the progression model compatible with
something like:

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

There should be at most one pending ability intent per participant.

If the player queues another ability before the round resolves, the new
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
  target:
    type: hostile
    default: current_target
  availability:
    actors:
      - player
      - mob
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
      target:
        type: hostile
        default: current_target
      components:
        - type: damage
          profile: basic_physical
          overrides:
            multiplier: 1.6
```

### Actor Audience

An ability's normalized `availability.actors` is a non-empty subset of
`player` and `mob`. Omission normalizes to `[player, mob]` so existing manifests
retain their behavior, and canonical storage and export include the normalized
list. The audience gate is authoritative and independent of class, level,
requirements, provider, and known-ability checks.

`actors: [mob]` is the canonical mob-only declaration. The definition remains
active and selectable by mob combat loadouts, but player-facing acquisition,
discovery, hotkey, and execution paths exclude it. `actors: [player]` provides
the inverse boundary for player-only techniques. `is_active: false` remains a
global definition switch and therefore cannot represent mob-only content.

Player paths must apply the audience gate before offering an unattached ability
as globally learnable. Starting grants and other player acquisition paths must
also reject mob-only definitions; a stale known slug must not turn a mob-only
ability into a usable player command. Mob selection must require the `mob`
audience rather than relying on a slug prefix such as `mob-`.

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

Ability costs support `fixed`, `percent_max`, and `percent_base` calculations.
`percent_base` uses the base resource pool before equipment and other max-pool
modifiers; for energy, a 100 base pool and `amount: 5` means a 5 energy cost.

Healing should use the same shape:

```yaml
kind: ability
metadata:
  slug: mend
  name: Mend
spec:
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

An interrupt is a separate ordered component rather than a special damage
flag. For example, the Hoplite's Kick is a zero-windup, initiative-bound attack
that interrupts only after its damage lands:

```yaml
kind: ability
metadata:
  slug: kick
  name: Kick
spec:
  command:
    verbs: [kick]
  target:
    type: hostile
    default: current_target
  availability:
    classes: [hoplite]
    min_level: 1
  cast_time:
    rounds: 0
  cooldown:
    rounds: 12
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 0.25
      text:
        label: Kick
    - type: interrupt
      target: ability.target
      apply: on_hit
      text:
        label: Kick
```

The normalized interrupt shape admits `target`, `apply`, and `text` only.
`target` is currently restricted to `ability.target`; `apply` is
`on_resolve` or `on_hit`. Because components resolve in authored order,
`on_hit` requires a preceding output component to have landed during the same
ability resolution.

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
- a Trainer Profile can independently gate learning and cap the known-set
  intersection with its catalog
- every materialized known ability in that catalog counts, regardless of its
  acquisition path
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

An explicit interrupt follows the same forgiving fallback rule. It clears the
victim's committed ability before resolution, so that ability pays no cost and
starts no cooldown. On the victim's initiative-bound turn, the resolver falls
back to a basic attack when one is legal. The interrupting ability resolves its
own cost and cooldown normally.

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

Out-of-combat abilities may need wall-clock cooldowns later, but those should be
modeled separately from encounter-bound combat cooldowns.

Out-of-combat abilities should share the same ability schema. For example, a
self-heal can use the same `target`, `cost`, `cooldown`, and `components`
structure whether the actor is in combat or standing alone. The difference is
execution policy:

- in combat, an ability is queued and resolves during an encounter round
- out of combat, an allowed self-targeted ability may resolve immediately through the
  normal command/action/event pipeline

The schema should not fork just because an ability is usable outside combat.

## Windups And Channels

WR1 used wall-clock cast times and channel timings. WR2 should translate that
design space into logical rounds. Cast-time windups are implemented for primary
combat abilities with `cast_time.rounds`.

Pending intent status is part of the interruption boundary:

- `queued` is replaceable preparation and is immune to interrupts.
- `casting` is a committed windup and can be interrupted.
- `channeling` is reserved as an interruptible committed status for future
  channel execution.

A hostile ability with `cast_time.rounds: 0` has no windup, but it still enters
the pending primary-intent pipeline and resolves on the actor's stored
initiative turn. It is not an out-of-order reaction.

A windup occupies the primary action according to
`consumes_primary_action_while_casting`. Completing it resolves the ordered
component list. An interrupt clears the committed intent before resolution;
the victim's cost and cooldown remain unpaid and unstarted, and its turn falls
back to a legal basic attack. The resolver must also suppress immediate special
ability reselection for that actor during the same turn.

Channels remain future work. A channel should be represented as repeated
scheduled components across encounter rounds, with each tick emitting normal
combat events. Although execution and authoring are not implemented, reserving
the `channeling` status now keeps cancellation semantics stable: the same
explicit interrupt component will cancel either committed status.

## Effect Model

Effects should be declarative runtime records with known hooks.

The broader target model for buffs, debuffs, resource regeneration, stat
modifiers, special status effects, and combat procs is covered in
[combat-buffs-and-effects.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-buffs-and-effects.md).
This section keeps the ability-facing summary.

Useful hook phases:

- `on_apply`
- `round_start`
- `before_action`
- `before_primary_action`
- `before_damage`
- `after_damage`
- `after_primary_action`
- `round_end`
- `on_expire`

Effects should modify combat through explicit primitives, such as:

- prevent primary action
- prevent a named action such as `flee`
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

The playable effect set includes:

- `stun`: prevents the target's primary action for a number of rounds
- `dot`: applies periodic damage during encounter rounds
- `hot`: applies periodic healing during encounter rounds
- `root`: carries a `before_action`/`prevent` rule for the `flee` action

Those four are implemented. They exercise the main
runtime problems: action prevention, repeated effect ticks, expiration, and
clear combat messages.

Root behavior comes from the effect's validated `action_rule` primitive, not
from its slug. The flee path evaluates the rule before queueing and again when
the delayed escape completes. A root that lands during preparation cancels the
pending flee, refunds its reserved movement cost, and leaves the actor in the
encounter.

Other primitives, such as absorbs, buffs, debuffs, taunts, silences, dispels,
and expire-on-damage behavior, should be deferred until the first effect runtime
has proven stable.

## Encounter Round Pipeline

The ability-aware encounter round follows this shape:

```text
load encounter and lock participants
increment round number
resolve round_start effects

for each participant in persisted encounter order:
  action = pending_primary_intent or default_auto_attack
  if the participant's committed intent was interrupted earlier this step:
    action = default_auto_attack if legal
  if action is invalid:
    emit private failure
    action = default_auto_attack if legal
  resolve action components
  interrupt components may clear another locked participant's committed intent
  clear consumed pending intent
  stop if encounter ended

resolve round_end effects
schedule next encounter step if still active
emit events
```

Normal participant ordering should be rolled or derived when the encounter
forms, then stored on encounter participant runtime state. Round resolution
should use that persisted order, not reroll each round and not issue ordering
queries while resolving each action.

Opener mechanics such as charge, ambush, and prepared attacks should write an
explicit first-round priority list before the first round resolves. Charge can
open against a current-room target or move through one exit before opening
against an adjacent-room target. Opener priority overrides the actor's first
primary action only; the stored encounter order remains the default after that
opening action.

Fray join order is still useful as a deterministic tie-breaker and as metadata
for explaining how the encounter formed, but it should not be the sole ordering
rule for normal contact.

## Component Resolution

Ability components should resolve in authored order.

Current normalized component types:

- `damage`
- `healing`
- `effect`
- `state`
- `interrupt`

Additional future component types may include:

- `resource_change`
- `movement`
- `summon`
- `dispel`
- `threat`

The current resolver supports:

- damage through `resolve_attack`
- healing through the same combat formula result shape
- stun effects that prevent a primary action
- dot effects that tick damage during encounter rounds
- hot effects that tick healing during encounter rounds
- scoped state updates
- interrupts of the hostile `ability.target` when its pending intent is
  committed as `casting`, or is marked `channeling` for the future channel
  contract

An interrupt accepts only `target: ability.target`, `apply: on_resolve` or
`apply: on_hit`, and its normalized `text`. With `on_hit`, a preceding output
component must have landed. A queued but uncommitted target intent is not
eligible, and an ability containing the component must have a hostile target.
Resolution uses the target actor's already-locked pending state and does not
perform a cross-encounter or world scan.

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

The UI needs clear feedback at each action boundary:

- when an ability is queued
- when a committed cast is exposed to its hostile target
- when an ability resolves
- when an interrupt cancels a committed cast or future channel

Suggested event types:

- `combat.intent.queued`
- `combat.intent.replaced`
- `combat.intent.rejected`
- `notification.combat.ability`
- `notification.combat.ability_casting`
- `notification.combat.ability_interrupted`
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
In a duel, hostile cast narration is sent to the opposing contestant with the
casting actor and ability identity, not only to the caster. Interruption events
identify the interrupting ability, actor, target, interrupted ability, committed
status, and round so both sides receive coherent combat-log feedback.

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
- store persisted encounter initiative order and first-round opener priority
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
- support simple cast-time windups with `cast_time.rounds`
- support explicit interrupts of committed casts, with ordered `on_resolve` and
  `on_hit` application
- expose hostile duel cast narration to the opposing contestant
- make queued ability replace auto-attack
- fall back to auto-attack if the queued ability is invalid
- emit queue and resolution events
- keep round resolution bounded to preloaded encounter state

Limit Phase 1 to direct damage, direct healing, simple cast-time windups, and
explicit interrupts of committed windups. Skip channel execution, reactions,
bonus actions, and complex effects.

### Phase 2: First Effects

- persist the unified active effect runtime schema as canonical rows
- implement simple effect primitives
- support stun, dot, hot, and root action rules that prevent `flee`
- route target-owned effect advancement through encounter rounds while engaged
  and bounded actor pulses while detached
- retain effect source attribution for remote death and reward resolution
- distinguish spatial engagement from hostile-effect combat tagging
- emit effect lifecycle events
- add query-count tests for dot/hot-heavy rounds

### Phase 3: Access And Progression

- implement class grants and classless grants
- validate learned abilities
- expose cooldown and known ability state to the client
- add builder tests for class-restricted and classless abilities
- keep room for future feat-style choice slots

### Phase 4: Richer Combat

- channels
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
- exact client payloads for known abilities, queued abilities, and cooldowns
- exact query-count budgets for queueing and resolving abilities
- future feat-style choice slots where one progression slot selects one of
  several possible abilities

## Bottom Line

WR2 abilities should be custom, declarative combat actions that resolve inside
encounter rounds.

The default action economy should be:

```text
queued ability that consumes the primary action replaces auto-attack
no queued ability means auto-attack
status ticks and reactions are explicit extras
```

Classes can grant or restrict abilities, but classes should not own hard-coded
runtime behavior.

This keeps the good part of WR1 custom skills while moving combat toward WR2's
deterministic, queue-friendly, builder-authored architecture.
