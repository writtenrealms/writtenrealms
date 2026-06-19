# Attack Routine Builder Guide

Attack routines control how many weapon strikes an actor makes when their basic
combat turn resolves. Use them for extra attacks, dual wielding, and mob
multiattack behavior.

For architecture details, see
[attack-routines-and-dual-wielding.md](/Users/teebes/code/writtenrealms/docs/architecture/attack-routines-and-dual-wielding.md).

## Core Concepts

Extra mainhand attacks and dual wielding are separate.

- **Extra mainhand strike**: attacks again with the main weapon slot. This works
  with a one-handed weapon, a two-handed weapon, or a mainhand weapon plus
  shield.
- **Offhand strike**: attacks with a weapon in the offhand slot.
- **Dual wielding**: requires permission to equip an offhand weapon and a world
  combat rule that grants an offhand strike.

## Assassin-Only Dual Wielding

This world lets only assassins equip one-handed weapons in the offhand slot.
Anyone who legally dual wields gets one offhand strike each round.

```yaml
kind: world
spec:
  equipment:
    offhand_weapons:
      default_allowed: false
      allowed_grips: [one_hand]

  combat:
    attack_routine:
      base_mainhand_strikes: 1
      stacking:
        extra_mainhand_strikes: max
        max_primary_strikes: 2
      dual_wield:
        enabled: true
        grants_offhand_strike: true
        offhand_damage_multiplier: 0.5

  stats:
    class_profiles:
      warrior:
        label: Warrior

      assassin:
        label: Assassin
        features:
          equipment:
            can_equip_offhand_weapon: true
            allowed_offhand_weapon_grips: [one_hand]
```

## Class Extra Attack

This class attacks twice with its main weapon. It does not need to dual wield.

```yaml
kind: world
spec:
  combat:
    attack_routine:
      stacking:
        extra_mainhand_strikes: max
        max_primary_strikes: 2

  stats:
    class_profiles:
      fighter:
        label: Fighter
        features:
          combat:
            extra_mainhand_strikes: 1
```

## Temporary Extra Attack Ability

This ability gives the caster one extra mainhand attack for 6 rounds.

```yaml
kind: ability
metadata:
  slug: battle-trance
  name: Battle Trance
spec:
  command:
    verbs: [trance]
  action_type: utility
  target:
    type: self
    default: self
  cooldown:
    rounds: 10
  components:
    - type: effect
      effect: battle-trance
      category: buff
      target: self
      duration:
        rounds: 6
      stacking: refresh
      stack_key: battle-trance
      primitives:
        - type: combat_modifier
          phase: attack_routine
          attack_routine:
            extra_mainhand_strikes: 1
            strike:
              source: battle-trance
              weapon_slot: weapon
              damage_multiplier: 1
```

## Dual-Wielding Mob

Use a mob trait when the behavior belongs to every copy of a mob definition.
Mobs use authored `weapon_damage` for combat, so this does not require the mob
to carry actual weapon item records.

```yaml
kind: mobdefinition
metadata:
  slug: blade-dancer
  name: a blade dancer
spec:
  type: humanoid
  level: 6
  health_max: 90
  attack_power: 14
  weapon_damage: 12
  traits:
    - key: dual-wielder
      label: Dual Wielder
      params:
        attack_routine:
          extra_offhand_strikes: 1
          offhand_damage_multiplier: 0.5
```

Use a spawn-plan trait when only some spawned copies should gain the behavior:

```yaml
kind: spawnplan
metadata:
  slug: bandit-camp
spec:
  entries:
    - slug: elite-bandits
      source_pool:
        - ref: mobdefinition.bandit
      target:
        zone: zone@3
      count: 3
      traits:
        guaranteed:
          - key: dual-wielder
            params:
              attack_routine:
                extra_offhand_strikes: 1
                offhand_damage_multiplier: 0.5
```

## Stacking

`extra_mainhand_strikes: max` means overlapping class, trait, and effect grants
use the largest extra-strike value instead of adding every source together.

Raise `max_primary_strikes` if the world intentionally supports more than two
mainhand strikes.
