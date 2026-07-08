# Mob Trait Builder Guide

Mob traits are structured modifiers attached to mobs. Some game systems call
these affixes. In WR2, use `traits`.

Traits can come from two places:

- A mob definition, when every copy of that mob should have the trait.
- A spawn plan entry, when the trait should be guaranteed or randomly rolled for
  generated placements.

Current runtime support applies numeric modifiers immediately. Behavior-style
traits such as `exploder`, `tracker`, or `linker` can be authored and preserved
as structured metadata, but they need dedicated runtime handlers before they
change gameplay by themselves.

## Intrinsic Traits

Put `traits` on a `mobdefinition` when the mob should always spawn with them.

```yaml
kind: mobdefinition
metadata:
  slug: crypt-brute
  name: a crypt brute
spec:
  type: humanoid
  level: 8
  health_max: 80
  attack_power: 12
  armor: 4
  traits:
    - key: colossal
      modifiers:
        health_max_multiplier: 2
    - key: enraged
      modifiers:
        attack_power_multiplier: 1.5
```

Every spawned `crypt-brute` gets both trait instances. The modifiers apply to
the spawned mob's current stats, so `health_max_multiplier` also raises current
health when the mob is created.

## Rolled Spawn-Plan Traits

Put `traits` on a spawn-plan entry when the spawn plan should choose traits for each
generated placement.

```yaml
kind: spawnplan
metadata:
  slug: crypt-patrols
  name: Crypt Patrols
spec:
  zone: zone@3
  respawn:
    mode: fixed
    seconds: 60
  entries:
    - slug: hallway-guards
      source_pool:
        - ref: mobdefinition.skeleton-guard
          weight: 70
        - ref: mobdefinition.crypt-scout
          weight: 30
      target:
        zone: zone@3
      count:
        min: 4
        max: 7
      traits:
        guaranteed:
          - key: armored
            modifiers:
              armor_multiplier: 1.5
        chance: 35
        pool:
          - key: enraged
            weight: 40
            modifiers:
              attack_power_multiplier: 1.5
          - key: resilient
            weight: 30
            modifiers:
              resilience_multiplier: 1.5
```

`guaranteed` traits are always applied to each generated placement. `pool`
traits are weighted choices. `chance` is the percent chance to select one trait
from the pool for that placement.

Spawn-plan trait rolls are deterministic for an active spawn-plan run. Ordinary
respawn reconciliation refills the same generated placements and does not
reroll traits until the run resets.

## Modifier Syntax

Use direct modifier keys to add to a stat:

```yaml
traits:
  - key: armored
    modifiers:
      armor: 2
```

Use `_multiplier` keys to multiply a stat:

```yaml
traits:
  - key: colossal
    modifiers:
      health_max_multiplier: 2
```

Common mob modifier fields:

- `health_max`
- `attack_power`
- `weapon_damage`
- `armor`
- `resilience`
- `ability_power`
- `crit`
- `dodge`
- `energy_max`
- `stamina_max`
- `health_regen`
- `energy_regen`
- `stamina_regen`
- `level`
- `gold`
- `exp_worth`

Unsupported modifier keys are preserved in trait metadata but do not change
runtime stats.

## Behavior Trait Metadata

Behavior traits can carry params even before a runtime handler exists:

```yaml
traits:
  - key: exploder
    visibility: hidden_until_death
    params:
      delay_rounds:
        min: 1
        max: 2
      damage:
        calc: percent_max_health
        amount: 35
```

This creates a structured trait instance on spawned mobs. Until an `exploder`
handler is implemented, the data is available for diagnostics and future
runtime behavior but does not schedule an explosion.

## Visibility

Traits may include a `visibility` value:

- `visible`
- `hidden`
- `hidden_until_triggered`
- `hidden_until_death`
- `builder_only`

Visibility is stored on the trait instance. Player-facing display rules can use
it when trait presentation is added to the client.

## Compatibility

The old draft spawn-plan field name `affixes` is accepted as an import alias
while transition content is migrated:

```yaml
affixes:
  guaranteed: [armored]
```

New content should use:

```yaml
traits:
  guaranteed: [armored]
```

Do not define both `traits` and `affixes` on the same spawn entry. Exported
spawn plans use `traits`.

## Practical Patterns

Use intrinsic traits for identity:

- a named boss that is always `colossal`
- a trap construct that is always `exploder`
- a guardian that is always `resilient`

Use spawn-plan traits for variety:

- random patrol mobs sometimes become `enraged`
- elite guards always get `armored`
- a dungeon reset rolls a different mix of `armored`, `resilient`, and
  `enraged` mobs

Use simple numeric modifiers first. They are deterministic, visible in spawned
mob trait snapshots, and covered by the current runtime.
