# WR2 Combat Resolution Formulas

## Purpose

This document describes the WR2 combat formula layer: the system that turns
effective stats into hit, dodge, crit, mitigation, damage, healing, death, and
combat event data.

WR2 intentionally keeps this separate from stat derivation.

- `spec.stats` answers: what are this character's effective stats?
- `spec.combat` answers: how do those stats resolve into combat outcomes?

The goal is a system that feels enough like Written Realms combat to support
real games, while staying manifest-authored, inspectable, cheap to run, and
safe for builders to tune.

## Related Documents

- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [wr1-archetype-world-reference.md](/Users/teebes/code/writtenrealms/docs/dev/wr1-archetype-world-reference.md)

## Status

The first WR2 implementation is live in the runtime:

- `WorldConfig.combat_system` stores normalized combat configuration.
- `kind: world` manifests accept `spec.combat`.
- `KillAction` resolves attacks through the formula layer instead of using
  `attack_power` as direct damage.
- Combat events include formula details such as base damage, damage dealt,
  damage mitigated, dodge chance, crit chance, and mitigation percentages.
- Item templates and spawned items now have `weapon_damage`.
- Randomly generated weapons receive level-scaled `weapon_damage`.

This is still the first version. Abilities, effects, PvP, groups, absorbs,
resistances, and richer action overrides should build on this layer rather
than bypass it.

## WR1 Reference

WR1 had two formula layers:

- stat derivation: attributes, equipment, class, level, and buffs became combat
  stats
- attack resolution: combat stats became dodge, crit, mitigation, and final
  damage

WR2 keeps that separation.

For a basic WR1 physical attack, the useful mental model was:

```text
weapon_damage + attack_power / 16
```

WR1 spells generally used:

```text
ability_power * 0.1 * attack_multiplier
```

WR2 keeps the shape but uses `ability_power` as the combat stat.

WR1 also used level-scaled rating curves for dodge, crit, armor, and
resilience. WR2 keeps that idea because flat percentages scale poorly.

## Core Model

Combat formulas are authored as named profiles and rating curves.

Profiles describe attack shape:

- which stat provides power
- whether weapon damage participates
- whether the attack can be dodged
- whether it can crit
- which mitigation rules apply
- how much variance is allowed
- the minimum output

Ratings describe how numeric rating stats turn into percentages:

- dodge chance
- crit chance
- armor mitigation
- resilience mitigation

Builders tune those values in YAML. They do not author arbitrary Python,
JavaScript, Lua, or free-form expressions.

## World Manifest Shape

Combat configuration lives on the world manifest:

```yaml
kind: world
spec:
  combat_resolution_interval: 1.5
  combat:
    version: 1
    default_attack_profile: basic_physical
    default_ability_profile: basic_ability
    default_healing_profile: basic_heal
    level_scale:
      type: exponential
      base: 5.5
      growth: 1.1

    variance:
      enabled: true
      percent: 12.5

    ratings:
      dodge:
        stat: dodge
        type: mitigation_curve
        base: 0.02
        constant: 60
        cap: 0.75
      crit:
        stat: crit
        type: linear_rating
        base: 0.02
        constant: 120
        cap: 1.0
      armor:
        stat: armor
        type: mitigation_curve
        base: 0
        constant: 60
        cap: 0.75
      resilience:
        stat: resilience
        type: mitigation_curve
        base: 0
        constant: 120
        cap: 0.75

    profiles:
      basic_physical:
        kind: damage
        power_stat: attack_power
        power_scale: 0.0625
        use_weapon_damage: true
        weapon_damage_scale: 1.0
        unarmed_power_scale: 0.25
        mob_unarmed_level_scale: 0.5
        multiplier: 1.0
        damage_type: physical
        can_dodge: true
        can_crit: true
        crit_multiplier: 1.5
        mitigation:
          armor: true
          resilience: false
        variance: default
        minimum: 1

      basic_ability:
        kind: damage
        power_stat: ability_power
        power_scale: 0.1
        use_weapon_damage: false
        weapon_damage_scale: 0
        unarmed_power_scale: 0
        mob_unarmed_level_scale: 0
        multiplier: 1.0
        damage_type: ability
        can_dodge: false
        can_crit: true
        crit_multiplier: 1.5
        mitigation:
          armor: false
          resilience: true
        variance: default
        minimum: 1
```

Only include values you want to change. The manifest normalizer merges partial
profile edits into the engine defaults.

## Important Stat Semantics

`weapon_damage` is a first-class item stat. Basic weapon attacks can use it
directly, and builders can set it on item definitions. This fixes the WR1
awkwardness where weapon damage was effectively hidden inside level.

`attack_power` remains the physical throughput stat. It can add to weapon
damage or drive unarmed damage, depending on the attack profile.

`ability_power` is generic enough for magic, psionics, technology, tactics,
rituals, or other world-specific ability systems.

`armor` mitigates physical damage by default.

`resilience` mitigates ability or magical damage by default.

That armor/resilience split is simpler than WR1's physical-plus-magical
resilience behavior. A WR1-like world can still opt physical attacks into both
armor and resilience by setting:

```yaml
profiles:
  basic_physical:
    mitigation:
      armor: true
      resilience: true
```

## Resolution Pipeline

The runtime pipeline is fixed by the engine:

1. Load the profile.
2. Build actor and target combat snapshots.
3. Compute base output from weapon damage, power stat, and profile scales.
4. Apply profile multiplier.
5. Roll dodge if enabled.
6. Apply variance if enabled.
7. Roll crit if enabled.
8. Apply crit multiplier.
9. Round pre-mitigation output.
10. Apply enabled mitigation curves.
11. Enforce minimum output.
12. Persist health changes.
13. Emit combat events.
14. Resolve death, corpse presentation, graveyard routing, and rewards.

Builders can tune profile knobs, but they cannot reorder this pipeline. That is
intentional: predictable pipeline ordering keeps combat debuggable and cheap.

## Rating Curves

Rating curves use `level_scale(opponent.level)` so the same rating value is more
effective against lower-level opponents and less dominant against higher-level
opponents. The default scale is open-ended exponential:

```text
level_scale = 5.5 * 1.1^level
```

Worlds can also choose `linear`, `flat`, or legacy WR1 `ilf` scaling under
`spec.combat.level_scale`. See
[combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md)
for the builder-facing options.

`mitigation_curve` is used for armor, dodge, and resilience:

```text
value = (rating + opponent_scale * constant * base)
      / (rating + opponent_scale * constant)
```

The result is clamped between `0` and `cap`.

`linear_rating` is used for crit:

```text
value = rating / (opponent_scale * constant) + base
```

The result is also clamped between `0` and `cap`.

Worlds that prefer transparent percentage-point stats can use
`percentage_points` for any rating. This type ignores opponent level and treats
`1` stat point as one percentage point:

```text
value = rating / 100 + base
```

The result is clamped between `0` and `cap`, and `constant` is not used.

## Weapon Damage

Weapon damage is stored on item definitions and spawned items as
`weapon_damage`.

For physical profiles using weapon damage:

```text
base = weapon_damage * weapon_damage_scale
     + attack_power * power_scale
```

For players without a weapon:

```text
base = attack_power * unarmed_power_scale
```

For spawned mobs without a weapon:

```text
base = level_scale(actor.level) * mob_unarmed_level_scale
     + attack_power * power_scale
```

Mobs keep this level fallback so builders do not have to put a weapon item on
every rat, wolf, slime, or guard just to make it capable of dealing damage.

The default `unarmed_power_scale` is intentionally stronger than WR1's pure
unarmed fallback. WR2 has no mandatory starting weapon yet, so a new world
should remain testable before builders configure starting equipment.

## Event Data

`notification.combat.attack` keeps the existing event shape and adds formula
details:

```yaml
data:
  attack: attack
  label: Attack
  outcome: hit
  profile: basic_physical
  damage_type: physical
  damage_base: 14
  damage_dealt: 19
  damage_taken: 11
  damage_mitigated: 8
  damage_absorbed: 0
  healing_done: 0
  is_crit_hit: true
  is_heal: false
  dodge_chance: 0.08
  crit_chance: 0.14
  armor_mitigation: 0.28
  resilience_mitigation: 0
```

The UI does not need to display every field. The important point is that
builders and future debug tools can explain why a combat result happened.

## Validation

The manifest validator enforces a narrow contract:

- `spec.combat` must be a mapping.
- `version` must be `1`.
- unknown top-level combat fields are rejected.
- profile keys must be lowercase slug-style strings.
- profile fields must be known.
- `power_stat` must be a canonical combat stat.
- ratings must use supported rating types.
- mitigation entries must reference declared ratings.
- booleans must be real YAML booleans.
- caps must be between `0` and `1`.

The bias is toward clear errors rather than permissive interpretation.

## Design Boundaries

This layer does not implement the whole future combat system.

It deliberately does not yet solve:

- active abilities
- player-vs-player formulas
- group reward splitting
- absorptions and shields
- elemental resistances
- buffs and debuffs modifying formula stages
- seeded encounter RNG
- detailed combat preview tooling

Those systems should plug into this profile/rating pipeline instead of
creating independent one-off damage code.

## Bottom Line

WR2 combat now has a manifest-backed formula layer.

The default model is:

- weapons provide editable `weapon_damage`
- `attack_power` augments physical damage
- `ability_power` drives non-weapon abilities
- armor mitigates physical damage
- resilience mitigates ability or magical damage
- dodge, crit, variance, and mitigation are configurable per profile

This gives builders real knobs without giving them arbitrary formula scripting,
and it gives the runtime a stable shape that can scale beyond the initial
minimal kill command.
