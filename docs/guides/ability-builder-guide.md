# Ability Builder Guide

## Status

This guide describes the WR2 ability authoring model. Ability manifests are
wired into the runtime for player commands, encounter-round queueing, direct
damage, healing, cast times, stun, damage-over-time, heal-over-time, and
out-of-combat self utility. Mob definitions can also reference active ability
definitions from combat loadouts. Encounter-scoped effects support resource
change ticks, damage absorption barriers, and `after_damage` procs for bounded
buff behavior such as energy return on landed attacks. Character-scoped effects
support outgoing damage modifiers for room-wide buffs that can survive into
combat after out-of-combat use.

Ability `requirements` use the shared WR2 condition DSL. For condition
operators and paths, read
[condition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/condition-builder-guide.md).

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

In combat, an ability is queued as the actor's primary action for the next
encounter round. It replaces the auto-attack for that round.

If an ability has `cast_time.rounds`, the queued ability consumes that many
encounter rounds charging before its components resolve. For `rounds: 1`, the
sequence is: prepare the ability, spend the next encounter round charging, then
resolve the damage or healing on the following encounter round.

If no ability is queued, the actor uses the normal auto-attack.

If a queued ability becomes invalid before the round resolves, the actor falls
back to auto-attacking if an auto-attack is legal.

Out of combat, an allowed utility ability, such as a self-heal, uses the same
schema but may resolve immediately through the command/action/event pipeline.

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
  action_type: primary
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
damage or healing components, stun/dot/hot effects, state updates, and costs.
Cost resources use the world's configured player-facing labels, while damage
wording comes from the combat profile damage type.

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
  action_type: primary
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
  action_type: primary
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

Players may replace a queued ability before the first encounter round starts.
Once the ability is actively charging, it cannot be replaced by another ability.

Out-of-combat utility abilities currently resolve immediately; cast times are
combat-round behavior.

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
  action_type: primary
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
  action_type: primary
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
  action_type: primary
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
    - type: effect
      effect: stun
      duration:
        rounds: 1
      apply: on_hit
```

Stun prevents the target's primary action while it is active.

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
  action_type: primary
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

DOT ticks should resolve during encounter rounds, not as separate wall-clock
timers.

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
  action_type: primary
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
  action_type: primary
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
  action_type: primary
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

## Damage Absorption Barriers

Use a `damage_absorb` primitive when an effect should prevent incoming damage
until either its duration expires or its absorb pool is depleted.

```yaml
kind: ability
metadata:
  slug: ward
  name: Ward
spec:
  command:
    verbs: [ward]
  action_type: primary
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
  action_type: primary
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

## Ability Trainers

Mob definitions can act as trainers by listing the abilities they teach. If at
least one trainer in the world teaches an ability, players can only learn or
unlearn that ability while an eligible trainer mob is present in their current
room. Abilities with no trainer remain learnable and unlearnable through the
existing commands.

```yaml
kind: mobdefinition
metadata:
  slug: arms-trainer
  name: an arms trainer
spec:
  type: humanoid
  keywords: trainer arms
  trainer:
    availability: alive_and_present
    abilities:
      - power-strike
      - shield-slam
```

Use `availability: present` when the spawned trainer only needs to be in the
room. Use `availability: alive_and_present` when a pending-deletion or defeated
trainer should not teach.

Use `availability` for class and level gates. Use `requirements` for the
shared condition DSL:

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

## Queueing Behavior

Players can substitute their queued primary ability until the round resolves.

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

- [combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md)

Recommended first-pass order:

1. Make basic auto-attacks feel reasonable.
2. Add one damage ability that replaces an auto-attack.
3. Give it a round cooldown.
4. Add one healing ability.
5. Add one stun or dot/hot ability.
6. Test the same fight with several queued substitutions.

Avoid making every ability a high-multiplier damage ability. If an ability has
stun, dot, hot, or unusual targeting, lower its direct damage first and tune up
only after the combat log feels clear.

## Performance Notes

Ability authoring should stay declarative. Do not expect custom script code to
run every round.

At runtime, WR2 uses normalized definitions, known ability sets, cooldown
state, and active effect state that can be loaded in bounded queries. Builder
configuration should describe behavior through known primitives, not through
free-form per-round logic.
