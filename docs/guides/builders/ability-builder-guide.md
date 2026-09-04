# Ability Builder Guide

## Status

This guide describes the WR2 ability authoring model. Ability manifests are
wired into the runtime for player commands, encounter-round queueing, direct
damage, healing, cast times, interrupts, stun, damage-over-time, heal-over-time,
and out-of-combat self-targeted abilities. Mob definitions can also reference
active ability definitions from combat loadouts. Encounter-scoped effects
support resource change ticks, damage absorption barriers, and `after_damage`
procs for bounded buff behavior such as energy return on landed attacks.
Effects can also carry a validated `action_rule`; the implemented `flee` rule
supports roots that stop a character from escaping. Character-scoped effects
support outgoing damage and stat modifiers for buffs that can survive into
combat after out-of-combat use.

Ability `requirements` use the shared WR2 condition DSL. For condition
operators and paths, read
[condition-builder-guide.md](condition-builder-guide.md).

## Mental Model

An ability is an authored command that resolves one or more components:

- damage
- healing
- stun
- damage-over-time
- heal-over-time
- resource-changing ticks
- damage absorption barriers
- resource-changing damage procs
- flee-preventing roots
- interrupts of committed casts

In combat, an ability is queued for the next encounter round. By default, it
consumes the actor's primary action while casting and when it resolves,
replacing the auto-attack in both phases. Builders can configure those phases
independently for supplemental abilities.

If an ability has `cast_time.rounds`, the queued ability spends that many
encounter rounds charging before its components resolve. For `rounds: 1`, the
sequence is: prepare the ability, spend the next encounter round charging, then
resolve the damage or healing on the following encounter round. Charging rounds
consume the primary action when `consumes_primary_action_while_casting` is true.

A hostile ability with `cast_time.rounds: 0` has no windup, but it is not
normally a reaction or a free action. It is still queued and resolves only when
its actor's initiative-bound turn arrives. The narrow exception is a ready
hostile interrupt aimed at a committed cast or channel, described under
[Interrupts](#interrupts).

If no ability is queued, the actor uses the normal auto-attack.

If a queued ability becomes invalid before the round resolves, the actor falls
back to auto-attacking if an auto-attack is legal.

Out of combat, an allowed self-targeted ability, such as a self-heal, uses the
same schema but may resolve immediately through the command/action/event pipeline.

## Known Ability Limit

Abilities are known until unlearned.

By default, a player can know 8 abilities. A world can configure a different
limit:

```yaml
kind: world
spec:
  ability_progression:
    max_known: 12
```

To remove the known ability limit:

```yaml
kind: world
spec:
  ability_progression:
    max_known: uncapped
```

## Starting Abilities

Use `ability_progression.starting_abilities` when newly initialized characters
should begin with known abilities. A plain slug grants the ability to every
new character, which works for classless worlds:

```yaml
kind: world
spec:
  ability_progression:
    max_known: 8
    starting_abilities:
      - first-aid
```

Use a condition entry when only some characters should start with an ability.
Conditions use the same WR2 condition DSL as triggers and ability requirements:

```yaml
kind: world
spec:
  ability_progression:
    max_known: 8
    starting_abilities:
      - ability: bash
        conditions:
          eq: [actor.archetype, hoplite]
```

Starting abilities are granted from active ability definitions in the world,
deduped, assigned hotkeys in order, and capped by `max_known`.

Future progression may support feat-style choice slots where one slot chooses
one of several abilities. Do not build worlds around that yet; the first pass is
a simple known ability cap.

Players use the runtime commands below to manage the current known set:

```text
learn power-strike
unlearn power-strike
```

The command validator checks the materialized known list directly, so encounter
rounds do not scan class rules, quest rules, or manifests.

## Ability Manifest Shape

Use one `kind: ability` manifest per ability:

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
  consumes_primary_action_on_resolve: true
  consumes_primary_action_while_casting: true
  target:
    type: hostile
    default: current_target
  cost:
    resource: stamina
    amount: 10
    calc: fixed
  cast_time:
    rounds: 0
  cooldown:
    rounds: 3
  help:
    text: 1 round cast, 3 round cooldown, inflicts 1.6x physical damage on the target.
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.6
      text:
        label: Power Strike
```

Bundled `kind: abilities` manifests are also supported for import convenience,
but one ability per manifest should be the default authoring style.

## Actor Audience

`spec.availability.actors` controls which kinds of actors may acquire and use
an ability. It must be a non-empty list containing `player`, `mob`, or both.
Omitting it preserves the backward-compatible default of both audiences, and
canonical export includes the normalized list. Availability accepts only
`actors`, `classes`, and `min_level`, so misspelled gates are rejected:

```yaml
availability:
  actors: [player, mob]
```

Mark an NPC combat technique as mob-only when it should remain active for mob
loadouts but never enter player progression:

```yaml
kind: ability
metadata:
  slug: mob-burning-curse
  name: Burning Curse
spec:
  availability:
    actors: [mob]
  target:
    type: hostile
    default: current_target
  components:
    - type: damage
      profile: basic_ability
```

`actors: [mob]` excludes the ability from player training, starting grants,
help discovery, hotkeys, and player command execution while leaving it
available to mob combat loadouts. Use `actors: [player]` for a technique that
mobs must not select. Actor audience is independent of `classes` and
`min_level`; those fields continue to narrow player access when `player` is in
the audience. Do not use `is_active: false` for this purpose, because an
inactive ability is unavailable to both players and mobs.

## Ability Help

Players can type `help <ability>` for abilities they already know or can learn
right now. The lookup accepts the ability slug, an authored command verb, the
exact ability name, or an unambiguous slug/name prefix.
The displayed line is prefixed with the resolved ability name, such as
`Trident - 1 round cast, inflicts 1.25x ability damage on the target.`

Builders may author a concise player-facing help line with `spec.help.text`:

```yaml
help:
  text: 1 round cast, 6 round cooldown, stuns the target for 2 rounds if it lands.
```

When `spec.help.text` is absent, the runtime generates a plain text line from
the ability definition. Generated help includes cast rounds, cooldown rounds,
damage or healing components, interrupts, stun/dot/hot effects,
flee-preventing action rules, state updates, and costs. Cost resources use the
world's configured player-facing labels, while damage wording comes from the
combat profile damage type.

## Targeting And Openers

Hostile abilities normally target mobs in the current room. Runtime target
fields can opt an ability into opener behavior:

- `range: current_room` is the default.
- `range: adjacent_room` requires one direction in the command.
- `range: current_or_adjacent_room` allows the command to omit direction for a
  current-room target, or include one direction for an adjacent-room target.
- `move_actor: true` moves the actor before combat when a direction is supplied.
- `opener_priority: true` gives the actor first-action priority for the first
  encounter round only.

Charge-style abilities should also set `allow_out_of_combat: true` because they
start combat from outside an existing encounter:

```yaml
kind: ability
metadata:
  slug: charge
  name: Charge
spec:
  command:
    verbs: [charge]
  target:
    type: hostile
    default: current_target
    allow_out_of_combat: true
    range: current_or_adjacent_room
    move_actor: true
    opener_priority: true
  cooldown:
    rounds: 10
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.5
```

In a `pvp_mode: match` instance, the same Charge-style contract supports the
opposing contestant as the hostile target. A directed opener moves the caster
through one arena exit and starts the encounter in the destination room;
`opener_priority` still affects only the first round. The runtime validates
that the target is the opposing contestant in the active 1v1 match.

## Attack Routine Targets

Attack-routine modifiers can add strikes during a character's normal attack.
Use `strike.target: room.secondary_hostile` when a temporary effect should make
the caster's attacks also hit another active hostile mob in the same room. The
base attack still resolves against the main faceoff target, and the extra strike
only happens when a secondary active hostile exists:

```yaml
kind: ability
metadata:
  slug: cleave
  name: Cleave
spec:
  command:
    verbs: [cleave]
  consumes_primary_action_on_resolve: false
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 6
  components:
    - type: effect
      effect: cleave
      category: buff
      target: self
      stack_key: cleave
      stacking: refresh
      duration:
        rounds: 1
      primitives:
        - type: combat_modifier
          phase: attack_routine
          attack_routine:
            extra_mainhand_strikes: 1
            strike:
              source: cleave
              target: room.secondary_hostile
              weapon_slot: weapon
              damage_multiplier: 1
              label: Cleave
```

Attack-routine duration counts the round in which the effect is applied. A
1-round Cleave applies to the current round's attack when the ability does not
consume the primary action; a 2-round Cleave applies to the current round and
the next combat round.

## Costs

`spec.cost.resource` supports `health`, `energy`, and `stamina`.

`spec.cost.calc` supports:

- `fixed`: spend `amount` directly.
- `percent_max`: spend `amount` percent of the actor's current maximum pool
  after stat modifiers.
- `percent_base`: spend `amount` percent of the actor's base pool before
  equipment and other maximum-pool modifiers. For energy, `amount: 5` costs 5
  energy when the actor's base energy pool is 100.

## Cast Times

Use `spec.cast_time.rounds` when an ability should charge across encounter
rounds before its components resolve:

```yaml
kind: ability
metadata:
  slug: charged-bolt
  name: Charged Bolt
spec:
  command:
    verbs: [bolt]
  target:
    type: hostile
    default: current_target
  cost:
    resource: energy
    amount: 15
    calc: fixed
  cast_time:
    rounds: 1
  cooldown:
    rounds: 3
  components:
    - type: damage
      profile: basic_ability
      overrides:
        multiplier: 2
```

With `rounds: 1`, the player sees the prepare acknowledgement immediately, the
next encounter round is spent charging, and the ability resolves on the
following encounter round. The charging round consumes the player's primary
action, so they do not auto-attack during that round.

As soon as the player prepares a hotkeyed ability, its assigned Combat hotkey
uses the primary-color background. The highlight persists through the queued
and charging states and clears when the ability resolves, is replaced, or is
canceled.

Players may replace a queued ability before the first encounter round starts.
Once the ability is actively charging, it cannot be replaced by another ability.

Out-of-combat self-targeted abilities currently resolve immediately; cast times
are combat-round behavior.

## Interrupts

Use an `interrupt` component to cancel a hostile target's committed cast. This
example deals one-quarter physical damage and interrupts only when that damage
lands. Its availability is omitted because class access is world-specific:

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

The normalized `interrupt` component supports only these fields:

- `target` must be `ability.target`.
- `apply` accepts `on_resolve` or `on_hit`.
- `text` supplies the player-facing component label.

An ability containing an interrupt component must use
`spec.target.type: hostile`.

Components resolve in authored order. An `apply: on_hit` interrupt must come
after an output component that recorded a landed outcome for the same ability.
If no earlier output landed, the interrupt does nothing. Use `on_resolve` when
the interrupt should not depend on an earlier hit.

The interruptible committed statuses are `casting` and `channeling`. A merely
`queued` intent is still replaceable by its owner and is immune to interrupts.
The runtime currently produces `casting`; it recognizes `channeling` so the
component has a stable contract when channels are implemented, but builders
cannot author or execute channel abilities yet.

Interrupting clears the victim's committed ability before it resolves. The
victim pays none of that ability's cost, starts none of its cooldown, and falls
back to a basic attack on its turn when one is legal.

A ready hostile ability containing an interrupt component receives narrow
primary-action priority when it is already pending as the step's primary order
is derived and its target has a committed `casting` or `channeling` intent. The
resolver places it immediately before that target for the current step,
regardless of stored initiative, without changing the stored order. If multiple
qualifying interrupts have the same insertion point, their relative order
remains the stored encounter order.

This response priority does not guarantee cancellation. An `on_hit` interrupt
must still land; if its preceding output misses or is dodged, the target's cast
continues on its turn. A `queued` target is not committed, so it receives no
special interrupt ordering and cannot be canceled. Zero-windup abilities that
do not contain an interrupt component remain initiative-bound.

Player and duel commands queued between rounds meet the pending-intent
requirement. NPC ability selection currently happens when the NPC's turn begins,
so a zero-windup interrupt first selected at that point cannot retroactively
reorder the step. An NPC interrupt already pending from an earlier windup can
still receive response priority when it becomes ready.

## Primary Action Consumption

Abilities configure primary-action consumption separately for casting rounds
and the resolution round. Both fields default to `true`:

```yaml
consumes_primary_action_on_resolve: true
consumes_primary_action_while_casting: true
```

This preserves the standard "ability instead of auto-attack" combat rhythm in
both phases. The four combinations behave as follows:

| While casting | On resolve | Behavior |
| --- | --- | --- |
| `true` | `true` | No regular attack during either phase. |
| `true` | `false` | Charge without attacking, then resolve alongside the regular attack. |
| `false` | `true` | Attack while charging, then replace the resolution-round attack. |
| `false` | `false` | Regular attack during both phases. |

Use `consumes_primary_action_on_resolve: false` for supplemental abilities such
as light DOTs, marks, minor debuffs, or quick setup effects that should apply
while the actor still takes their normal attack in the resolution round:

```yaml
kind: ability
metadata:
  slug: minor-bleed
  name: Minor Bleed
spec:
  command:
    verbs: [minorbleed]
  consumes_primary_action_on_resolve: false
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 3
  components:
    - type: effect
      effect: dot
      duration:
        rounds: 2
      tick:
        every_rounds: 1
        component:
          type: damage
          profile: basic_physical
          overrides:
            multiplier: 0.25
```

Phase-specific consumption does not change requirements, costs, cooldowns,
target validation, or cast-time delays. To let an ability resolve alongside a
regular attack while still occupying its charging rounds, set only
`consumes_primary_action_on_resolve: false` and leave
`consumes_primary_action_while_casting` at its default `true`.

## Cooldowns

Use `spec.cooldown.rounds` for round-based cooldowns. By default, cooldown
starts when the ability resolves:

```yaml
cooldown:
  rounds: 3
```

Use `trigger: on_hit` when cooldown should start only if a damage or healing
component lands. This is useful for dodgeable control abilities where a miss
should not consume the cooldown:

```yaml
cooldown:
  rounds: 6
  trigger: on_hit
```

## Damage Abilities

A physical weapon technique should usually use `basic_physical`:

```yaml
kind: ability
metadata:
  slug: heavy-slash
  name: Heavy Slash
spec:
  command:
    verbs: [slash]
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 2
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.35
```

An ability-power attack should use `basic_ability`:

```yaml
kind: ability
metadata:
  slug: mind-spike
  name: Mind Spike
spec:
  command:
    verbs: [spike]
  target:
    type: hostile
    default: current_target
  cost:
    resource: energy
    amount: 12
    calc: fixed
  cooldown:
    rounds: 2
  components:
    - type: damage
      profile: basic_ability
      overrides:
        multiplier: 1.25
```

`basic_ability` means "use ability power and ability mitigation rules." It does
not imply a fantasy spell. Worlds can label `ability_power` however they want.

## State Components And Combo Points

Abilities can write scoped state as part of their component list. This is useful
for combo points, charges, stance counters, room-state toggles, and similar
small runtime values.

Use `type: state` with one of these operations:

- `op: increment` adds `amount` and can clamp with `min` or `max`.
- `op: set` writes `value`.
- `op: clear` removes the key.

Supported scopes are `character`, `room`, `zone`, and `world`. For combo points,
use `character` so each player tracks their own points.

Builder example:

```yaml
kind: ability
metadata:
  slug: quick-jab
  name: Quick Jab
spec:
  command:
    verbs: [jab]
  target:
    type: hostile
    default: current_target
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1
    - type: state
      scope: character
      key: combo_points
      op: increment
      amount: 1
      max: 5
      apply: on_hit
```

`apply: on_hit` means the state component only runs if an earlier damage or
healing component in the same ability actually landed. Use `apply: on_resolve`
when the state change should happen whenever the ability resolves.

A spender can require points, scale its damage from the current state value,
and then clear the points:

```yaml
kind: ability
metadata:
  slug: finisher
  name: Finisher
spec:
  command:
    verbs: [finish]
  target:
    type: hostile
    default: current_target
  requirements:
    gte:
      - state.character.combo_points
      - 1
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1
      scaling:
        from: state.character.combo_points
        multiplier_per_point: 0.5
        max_points: 5
    - type: state
      scope: character
      key: combo_points
      op: clear
```

Components resolve in authored order. Put the damage component before the clear
component when the damage needs to read the points being spent.

## Healing Abilities

Healing uses the same ability shape:

```yaml
kind: ability
metadata:
  slug: mend
  name: Mend
spec:
  command:
    verbs: [mend]
  target:
    type: ally
    default: self
  cost:
    resource: energy
    amount: 15
    calc: fixed
  cooldown:
    rounds: 2
  components:
    - type: healing
      profile: basic_heal
      overrides:
        multiplier: 1.2
```

Because out-of-combat abilities share the same schema, this kind of self-heal
can also be used outside combat when `target.allow_out_of_combat` is true. Self
and ally targets default to allowing out-of-combat use.

## Stun

Use a stun component after a damage component when the stun should apply only if
the hit lands:

```yaml
kind: ability
metadata:
  slug: shield-slam
  name: Shield Slam
spec:
  command:
    verbs: [slam]
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
    - type: effect
      effect: stun
      duration:
        rounds: 1
      apply: on_hit
```

Stun prevents the target's primary action while it is active.

## Root

Use **root** for the mechanic and **Rooted** for the visible status. Ability and
effect labels may still use setting-specific words such as snare, entangle, or
web. A trap is usually the source that applies an effect, rather than the name
of this action restriction.

A root is an effect with an explicit `action_rule`; the runtime does not infer
behavior from the `effect` slug. This example is the Phalanx mob ability
`Leg Irons`:

```yaml
kind: ability
metadata:
  slug: mob-leg-irons
  name: Leg Irons
spec:
  command:
    verbs: [graspingroots]
  consumes_primary_action_on_resolve: false
  consumes_primary_action_while_casting: true
  target:
    type: hostile
    default: current_target
  cast_time:
    rounds: 1
  cooldown:
    rounds: 7
  components:
    - type: effect
      effect: root
      category: debuff
      target: ability.target
      duration:
        rounds: 4
      apply: on_resolve
      primitives:
        - type: action_rule
          phase: before_action
          rule: prevent
          actions: [flee]
          reason: rooted
      text:
        label: Rooted
```

The supported contract is exactly `phase: before_action`, `rule: prevent`, and
an `actions` list containing `flee`. `reason: rooted` supplies the stable,
machine-readable reason, while `text.label: Rooted` names the effect in the
player-facing failure. Keep the primitive even when the effect is named `webbed`
or `entangled`; arbitrary effect slugs are descriptive, not mechanical switches.

Root prevents the `flee` combat action. It does not independently block ordinary
direction movement, so the normal round-zero and combat movement rules still
apply. When a rooted character enters `flee`, the command fails before the game
chooses a route, reserves stamina, or stores a pending flee. Because fleeing has
a preparation window, the same action rule is checked again at completion. If a
root lands during that window, the pending flee is cleared, its reserved stamina
is refunded, and the character remains in combat. The blocked completion consumes
the character's primary action while the rest of that combat round still resolves.

## Effect Scope

Active effects declare whether their lifetime belongs to one fight or follows
their target:

```yaml
scope: character  # or encounter
```

`character` effects follow either a player or mob across encounter boundaries.
`encounter` effects are removed when their owning fight ends. When `scope` is
omitted, DOTs, HOTs, ticking effects, room-wide player effects, and stat or
combat modifiers default to `character`. A self-targeted `damage_absorb` barrier
also defaults to `character` when its ability allows out-of-combat use. Other
effects such as stun, root, and fight-specific barriers default to `encounter`.
An explicit `scope` always takes precedence over these defaults.

Use `character` for poison, bleeding, curses, regeneration, and other effects
that should survive fleeing. Use `encounter` for effects whose meaning depends
on the current opponents or fight state.

## Damage-Over-Time

Use `dot` for periodic damage:

```yaml
kind: ability
metadata:
  slug: bleeding-cut
  name: Bleeding Cut
spec:
  command:
    verbs: [bleed]
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 3
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 0.8
    - type: effect
      effect: dot
      duration:
        rounds: 3
      tick:
        every_rounds: 1
        component:
          type: damage
          profile: basic_physical
          overrides:
            multiplier: 0.35
      apply: on_hit
```

DOTs are character-scoped by default. While the target is engaged, they advance
at the start of the target's combat rounds. After the target leaves combat, the
same effect advances through the world's bounded effect pulse; fleeing never
removes it. A lethal tick retains its original source, so a player receives
normal kill, experience, currency rewards, loot-condition, and quest credit even when the
player is no longer in the mob's room.

DOT application consumes the primary action by default; set
`consumes_primary_action_on_resolve: false` when the DOT is meant to be
supplemental damage alongside the caster's normal attack. Application messages
use the effect label, such as `You apply Bleed on a guard.` for the caster, `A
guard applies Bleed on you.` for the target, and `Mira applies Bleed on a
guard.` for observers. Tick damage is presented as passive harm from the effect,
such as `A guard suffers 12 damage from your Bleed.`, rather than as a fresh
direct hit.

## Heal-Over-Time

Use `hot` for periodic healing:

```yaml
kind: ability
metadata:
  slug: renewal
  name: Renewal
spec:
  command:
    verbs: [renew]
  target:
    type: ally
    default: self
  cost:
    resource: energy
    amount: 10
    calc: fixed
  cooldown:
    rounds: 3
  components:
    - type: effect
      effect: hot
      duration:
        rounds: 4
      tick:
        every_rounds: 1
        component:
          type: healing
          profile: basic_heal
          overrides:
            multiplier: 0.4
      apply: on_resolve
```

HOT ticks should use normal combat events so players can see what happened.

## Resource Regeneration Effects

Use a ticking effect with a `resource_change` primitive when an effect should
restore health, energy, or stamina across later encounter rounds:

```yaml
kind: ability
metadata:
  slug: focus-renewal
  name: Focus Renewal
spec:
  command:
    verbs: [renewfocus]
  target:
    type: self
    default: self
  components:
    - type: effect
      effect: focus-renewal
      category: buff
      target: self
      duration:
        rounds: 3
      tick:
        every_rounds: 1
        primitives:
          - type: resource_change
            resource: energy
            amount: 5
            calc: fixed
            target: effect.target
```

`resource` supports `health`, `energy`, and `stamina`. `calc` supports the same
`fixed`, `percent_max`, and `percent_base` vocabulary used by ability costs.
Resource changes clamp to the target's current maximum pool.

## Damage Output Buffs

Use a character-scoped `combat_modifier` primitive when an effect should change
future outgoing damage. `phase: outgoing_damage` multiplies damage components
and basic attacks, but does not change healing.

Room-wide friendly buffs can target `room.allies`. In the current WR2 combat
runtime, room allies are in-game players in the caster's room, including the
caster.

The 1v1 duel runtime deliberately narrows `room.allies` effect components to
the caster, both between encounters and during an active encounter. This keeps
the opposing contestant from receiving a friendly buff while leaving the
authoring shape ready for future team-aware ally selection. Broad
`room.players` and `room.hostiles` selectors remain unsupported for PvP
abilities.

Use `stack_key` with `stacking: refresh` when the buff should not stack. A later
application with the same stack key replaces the previous active effect and
resets its duration:

```yaml
kind: ability
metadata:
  slug: shout
  name: Shout
spec:
  command:
    verbs: [shout]
  target:
    type: self
    default: self
    allow_out_of_combat: true
  cooldown:
    rounds: 12
  components:
    - type: effect
      effect: shout
      category: buff
      target: room.allies
      stack_key: shout-damage-output
      stacking: refresh
      duration:
        rounds: 4
      primitives:
        - type: combat_modifier
          phase: outgoing_damage
          multiplier: 1.2
```

## Stat Buffs

Use a character-scoped `stat_modifier` primitive when an effect should change a
canonical stat such as `armor`, `attack_power`, `ability_power`, `crit`,
`dodge`, `resilience`, or a maximum resource stat for later combat and stat
checks.

Flat modifiers use `op: add` and `amount`:

```yaml
primitives:
  - type: stat_modifier
    stat: armor
    op: add
    amount: 20
```

Multiplicative modifiers use `op: multiply` and `multiplier`:

```yaml
kind: ability
metadata:
  slug: shield-wall
  name: Shield Wall
spec:
  command:
    verbs: [shieldwall]
  target:
    type: self
    default: self
  cooldown:
    rounds: 12
  components:
    - type: effect
      effect: shield-wall
      category: buff
      target: self
      stack_key: shield-wall-armor
      stacking: refresh
      duration:
        rounds: 3
      primitives:
        - type: stat_modifier
          stat: armor
          op: multiply
          multiplier: 3
```

For a self or friendly room buff, set the effect `target` to `self` or
`room.allies`. Flat additions apply before multipliers, regardless of primitive
order. A refreshed effect with the same `stack_key` replaces the previous active
effect and resets its duration.

## Damage Absorption Barriers

Use a `damage_absorb` primitive when an effect should prevent incoming damage
until either its duration expires or its absorb pool is depleted.

Self and ally abilities allow out-of-combat use by default. A self-targeted
barrier on one of those abilities therefore defaults to `scope: character`, so
it can be applied before a fight and follows its target until its rounds or pool
run out. For a barrier that exists only within its current fight, set
`allow_out_of_combat: false` on the ability target and `scope: encounter` on the
effect.

```yaml
kind: ability
metadata:
  slug: ward
  name: Ward
spec:
  command:
    verbs: [ward]
  target:
    type: self
    default: self
  cooldown:
    rounds: 3
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

`calc` supports:

- `fixed`: absorb exactly `amount` damage.
- `percent_max`: absorb `amount` percent of the target's current max health.

`damage_types` is optional. Omit it to absorb all incoming damage types, or use
`[physical]`, `[ability]`, or `[physical, ability]` to narrow the shield.
Absorbed damage is reported on combat attack events as `damage_absorbed`, and
the remaining health damage is reported as `damage_taken`.

Barrier pools can also scale from the effect source's combat stats. This shield
absorbs `0.5 * ability_power`:

```yaml
primitives:
  - type: damage_absorb
    amount: 0
    calc: fixed
    scaling:
      - source: ability_power
        multiplier: 0.5
```

Scaling terms are additive. This shield absorbs
`0.1 * ability_power + 0.3 * health_max` from the source's current stats:

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

You can combine a flat base with scaling. For example, `amount: 25` plus
`ability_power * 0.5` creates a pool of `25 + 0.5 * ability_power`.

## Damage Proc Buffs

Use a `proc` primitive when an active effect should react to a known combat hook.
The first supported proc phase is `after_damage`.

Example: restore energy when the buffed actor lands a physical auto-attack.

```yaml
kind: ability
metadata:
  slug: energized-strikes
  name: Energized Strikes
spec:
  command:
    verbs: [energize]
  target:
    type: self
    default: self
  components:
    - type: effect
      effect: energized-strikes
      category: buff
      target: self
      duration:
        rounds: 10
      primitives:
        - type: proc
          phase: after_damage
          conditions:
            all:
              - eq: [event.actor, "{effect.target}"]
              - eq: [event.attack, attack]
              - eq: [event.damage_type, physical]
              - gte: [event.damage_taken, 1]
          actions:
            - type: resource_change
              resource: energy
              amount: 5
              calc: fixed
              target: effect.target
```

Proc conditions use the shared WR2 condition DSL. When comparing one event path
to another path, wrap the right-hand reference in braces, as in
`"{effect.target}"`.

## Class Access

Abilities are custom first. Classes can grant or restrict access, but the
ability still resolves through the same generic runtime.

```yaml
kind: ability
metadata:
  slug: power-strike
  name: Power Strike
spec:
  availability:
    classes:
      - warrior
    min_level: 2
  target:
    type: hostile
    default: current_target
  components:
    - type: damage
      profile: basic_physical
```

Worlds without classes can grant abilities through trainers, quests, starting
loadout, items, or builder tools instead.

On the builder Abilities screen, use the **Class** filter to review the
abilities explicitly attributed to one authored class. The filtered list only
includes abilities that name the selected class in `availability.classes`;
unrestricted abilities whose class list is empty remain visible under **All**.
The builder stores the selected filters, page, sort order, and search text in the
list URL, so opening an ability and returning with the browser Back button
restores the same list context.

## Ability Trainers

Trainer Profiles are reusable catalogs of abilities that players can learn and
unlearn. Define the abilities first, then create the profile:

World Config links to **Trainer Profiles**. The list opens each profile's
details and canonical Trainer Profile YAML editor. The details summarize its
selection limit and show authored eligibility conditions. In Room Config, use
the **ABILITY TRAINING** service card and its **Trainer Profile** selector;
**OPEN PROFILE** opens the selected YAML, **CLEAR** removes the selection, and
**SAVE** applies the room attachment. Attaching or detaching a Trainer Profile
requires builder rank 3 because the first or last attachment changes where that
profile's abilities can be learned throughout the definition world. Other room
editors can inspect the selected profile but the training control is read-only.

```yaml
kind: trainerprofile
metadata:
  slug: arms-training
  name: Arms Training
spec:
  notes: Techniques taught in the city drill hall.
  abilities:
    - power-strike
    - shield-slam
    - brace
  learning:
    conditions:
      in:
        - actor.archetype
        - [warlord, tidecaller]
    max_known: 2
```

A Trainer Profile can contain at most 100 ability entries. This keeps
provider discovery and the Learn/Unlearn lists bounded under concurrent play.

`spec.learning` is an optional, profile-scoped learning policy. Its
`conditions` field uses the [shared condition DSL](condition-builder-guide.md)
and decides who may learn through that profile. `max_known` is either a
positive integer or `uncapped`. In the example, an eligible Warlord or
Tidecaller can choose any two of the three listed techniques; the policy does
not reserve two fixed entries.

The profile counts the player's currently known abilities that occur anywhere
in its complete `abilities` list. Starting abilities, quest or item grants,
builder grants, inactive abilities, and entries whose current requirements no
longer pass all count. This prevents another acquisition path from bypassing
the quota. Unlearning a counted ability frees a slot immediately. Reducing a
profile limit never removes known abilities; a player already over the new
limit simply cannot learn another through that profile until below it.

The profile limit and the world's `ability_progression.max_known` are
independent and both must have capacity. Use `max_known: uncapped` when the
profile needs only a condition. A profile with no `learning` policy retains
the legacy unrestricted behavior; existing profiles migrate with this empty
policy. On a partial update, omitting `learning` preserves the current policy,
while `learning: {}` clears it back to unrestricted. A non-empty `learning`
mapping requires `max_known`; omitting `conditions` makes the policy apply to
every player, while an explicit `conditions: false` deliberately denies
learning to everyone.

Manifest validation rejects malformed policies and condition operators. The
runtime also fails closed for malformed policy data written outside the
manifest path, reporting the profile as denied instead of accidentally
bypassing its gate.

The policy governs learning only. A local provider that contains the ability
still permits unlearning even when the player's class has changed, its
condition now fails, or its quota is full. This ensures a player can free a
slot instead of becoming trapped by a progression change.

The Trainer Profile is the quota boundary. Reusing one profile on several
rooms or mobs shares one allowance; visiting another provider does not reset
it. Distinct profiles have independent allowances even if their catalogs
overlap, and a known overlapping ability counts in every profile containing
it. Reuse the same profile whenever several providers must share one quota.
When multiple local profiles offer the requested ability, provider order is
room first and then mob id, skipping profiles whose condition fails or whose
quota is full until the first eligible provider with capacity is found.

A draft Trainer Profile has no gameplay effect until it is attached to a room
or mob definition. Once an attached profile offers an ability anywhere in the
definition world, players can only learn or unlearn that ability through an
eligible local provider. Abilities absent from every attached Trainer Profile
remain learnable and unlearnable through the ordinary commands anywhere their
other requirements are met.

Attach a profile directly to a room when training should always be available
there without a mob or Spawn Plan:

```yaml
kind: room
metadata:
  ref: room@42
  name: City Drill Hall
spec:
  trainer:
    profile: trainerprofile.arms-training
```

The room automatically exposes **Learn** and **Unlearn** actions. Bare `learn`
lists abilities the player can learn there; bare `unlearn` lists known
abilities the player can unlearn there. Both lists are numbered, so players may
use `learn <number>` or `unlearn <number>` as well as an ability name or
authored command. A direct room provider is always available while the player
is in that room.

Attach the same kind of profile to a mob definition only when the spawned NPC's
presence should control training:

```yaml
kind: mobdefinition
metadata:
  slug: arms-trainer
  name: an arms trainer
spec:
  type: humanoid
  keywords: trainer arms
  trainer:
    profile: trainerprofile.arms-training
    availability: alive_and_present
```

Use `availability: present` when the spawned trainer only needs to be in the
room. Use `availability: alive_and_present` when a pending-deletion or defeated
trainer should not teach. Room attachments do not accept `availability`.

For an ordinary training location, attach the profile to either the room or the
mob, not both. When both teach the same ability, the room is considered first;
if its policy denies learning or has no remaining slot, provider selection
continues through mobs in stable id order. Unlearning continues to use the
first local provider containing the ability because learning policy does not
restrict removal.

Omitting `trainer` from a partial room or mob manifest preserves the existing
attachment. Set `trainer: null` or `trainer: {}` to clear it. Older mob
manifests with inline `trainer.abilities` remain accepted as import input, but
canonical exports use a reusable Trainer Profile and a profile reference.

Use each ability's `spec.availability` for actor, class, and level gates. Use its
`requirements` for the shared condition DSL:

```yaml
requirements:
  all:
    - eq:
        - actor.equipment.offhand.equipment_type
        - shield
    - eq:
        - state.character.oath_sworn
        - true
```

A profile policy is an additional gate; it does not override an ability's own
availability or requirements. For example, a cross-class profile must list the
allowed classes in `learning.conditions` *and* each offered ability must permit
those classes in `spec.availability`.

Each room and each mob definition accepts one Trainer Profile. To put a
six-ability native curriculum and a separate two-choice cross-training
curriculum at one location, attach one profile to the room and the other to a
spawned mob, or use two separate provider locations. Do not merge the catalogs
unless they are also intended to share one profile quota.

## Queueing Behavior

Players can substitute their queued ability until the round resolves.

Example:

```text
strike goblin
```

The player sees:

```text
You prepare Power Strike.
```

Then:

```text
mend self
```

The player sees:

```text
You switch to Mend.
```

When the round resolves, only `Mend` is used. The replaced ability is not paid
for and does not go on cooldown.

## Builder Tuning Advice

Start with simple direct damage and healing.

Use the combat formula guide to tune the profiles that abilities call:

- [combat-formula-builder-guide.md](combat-formula-builder-guide.md)

Recommended first-pass order:

1. Make basic auto-attacks feel reasonable.
2. Add one damage ability that replaces an auto-attack.
3. Give it a round cooldown.
4. Add one healing ability.
5. Add one stun, root, or dot/hot ability.
6. Test the same fight with several queued substitutions.

Avoid making every ability a high-multiplier damage ability. If an ability has
stun, root, dot, hot, or unusual targeting, lower its direct damage first and
tune up only after the combat log feels clear.

## Performance Notes

Ability authoring should stay declarative. Do not expect custom script code to
run every round.

At runtime, WR2 uses normalized definitions, known ability sets, cooldown
state, and active effect state that can be loaded in bounded queries. Action
rules are evaluated only for the acting character's active effects; they do not
scan every effect in a world. Builder configuration should describe behavior
through known primitives, not through free-form per-round logic.
