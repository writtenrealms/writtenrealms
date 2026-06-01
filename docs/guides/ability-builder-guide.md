# Ability Builder Guide

## Status

This guide describes the WR2 ability authoring model. Ability manifests are
wired into the runtime for player commands, encounter-round queueing, direct
damage, healing, stun, damage-over-time, heal-over-time, and out-of-combat
self utility.

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

In combat, an ability is queued as the actor's primary action for the next
encounter round. It replaces the auto-attack for that round.

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

Bundled `kind: abilities` manifests are also supported for import convenience,
but one ability per manifest should be the default authoring style.

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
