# WR2 Attack Routines And Dual Wielding

This document describes the target architecture for supporting multiple attacks
per combat round and offhand weapon use in WR2.

This architecture is implemented for WR2's current basic combat path. Player
and mob primary combat turns now resolve an attack routine containing one or
more strikes. WR2 already had an `offhand` equipment slot; this work adds
world/class-controlled offhand weapon permission and offhand strike resolution.

Related documents:

- [combat-encounter-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-encounter-model.md)
- [combat-resolution-formulas.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-resolution-formulas.md)
- [combat-abilities-model.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-abilities-model.md)
- [combat-buffs-and-effects.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-buffs-and-effects.md)
- [mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md)
- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)

## Decision Summary

- Treat **attack routine** and **equipment permission** as separate systems.
- Do not make dual wielding the generic name for attacking twice.
- Resolve a combatant's primary attack into a list of strike specs.
- Let class features, permanent character bonuses, mob traits, and active
  effects contribute to the same attack-routine resolver.
- Keep offhand weapon permission explicit. An actor can attack twice with a
  two-handed weapon or with a shield without being a dual wielder.
- Let mobs opt into dual-wield-style behavior through mob traits or mob
  definition data, without pretending to be a player class.
- Use the existing WR2 condition DSL for conditional feature gates.

## Terminology

Use **attack routine** for the resolved set of strikes an actor makes when its
primary attack resolves.

Use **strike** for one concrete attack roll and damage resolution within an
attack routine.

Use **extra mainhand strike** for an additional strike that uses the primary
weapon slot. This works with a one-handed weapon, two-handed weapon, or
mainhand weapon plus shield.

Use **offhand strike** for a strike that uses the offhand weapon slot.

Use **dual wielding** only for an equipment and attack style where the actor has
a mainhand weapon and a legal offhand weapon.

## Design Goals

- Support one or more strikes during a combatant's primary attack.
- Support extra attacks from class features, permanent character bonuses, mob
  traits, and temporary ability effects.
- Support offhand weapon equipment as a capability distinct from strike count.
- Support attack routines for both players and mobs.
- Keep the combat loop deterministic and encounter-scoped.
- Keep builder-authored behavior data-driven and inspectable.
- Avoid hard-coded archetype names such as `assassin` in runtime combat logic.
- Preserve WR2's existing separation between combat formulas, stats, equipment,
  abilities, and mob traits.

## Non-Goals

- Do not recreate WR1's wall-clock attack timing model.
- Do not grant arbitrary scripting in class profiles, abilities, or mob traits.
- Do not make every offhand item an attack source. Shields and held items remain
  separate from offhand weapons.
- Do not require mobs to carry actual weapon items just to express a
  dual-wield-style attack routine.

## Core Model

The combat turn should ask:

```text
what strikes does this actor's primary attack routine contain right now?
```

It should not ask only:

```text
does this actor attack twice?
```

A resolved routine should be a compact runtime list:

```yaml
strikes:
  - source: base
    weapon_slot: weapon
    damage_multiplier: 1.0

  - source: extra_mainhand_attack
    weapon_slot: weapon
    damage_multiplier: 1.0

  - source: dual_wield_offhand
    weapon_slot: offhand
    damage_multiplier: 0.5
```

Each strike should carry enough information for combat resolution and event
text:

- `source`: why this strike exists, such as `base`, `dual_wield_offhand`, or an
  ability/effect key
- `weapon_slot`: `weapon`, `offhand`, or a future explicit unarmed/natural
  source
- optional attack profile or profile override
- optional damage, accuracy, or crit multiplier
- optional text/event label

The existing `resolve_attack` path should grow a weapon-slot-aware input. The
default must remain equivalent to today's mainhand/basic attack behavior.

## Attack Routine Resolution

The resolver should build a routine in a deterministic order:

1. Start with the world's default routine policy.
2. Add the actor's base mainhand strike.
3. Add class or permanent actor feature contributions.
4. Add mob trait contributions.
5. Add active effect contributions.
6. Add equipment-derived contributions, such as an offhand strike when the actor
   is legally dual wielding.
7. Apply stacking and cap rules.
8. Return an ordered list of strike specs.

The routine resolver should not mutate actor state. It should be a pure
calculation over the actor, encounter, world config, equipment, active effects,
and mob trait snapshot.

## Equipment Permission

Offhand weapon permission answers:

```text
may this actor equip this weapon in the offhand slot?
```

That is different from attack routine resolution. A character may be able to
attack twice without equipping an offhand weapon. A character may also be able
to equip an offhand weapon only because a class feature, permanent bonus, mob
trait, or temporary effect allows it.

Two-handed mainhand weapons should continue to conflict with offhand equipment
unless an explicit future feature says otherwise.

Shields should remain normal offhand equipment. A shield does not imply an
offhand weapon strike.

## Manifest Ownership

The target manifest ownership is:

| Addition | Manifest | Path |
| --- | --- | --- |
| Global attack-routine defaults and stacking policy | `kind: world` | `spec.combat.attack_routine` |
| Global offhand weapon policy | `kind: world` | `spec.equipment.offhand_weapons` |
| Class-specific combat features | `kind: world` | `spec.stats.class_profiles.<class>.features.combat` |
| Class-specific equipment permissions | `kind: world` | `spec.stats.class_profiles.<class>.features.equipment` |
| Temporary combat-routine changes | `kind: ability` | `spec.components[].primitives[]` |
| Intrinsic mob attack-routine behavior | `kind: mobdefinition` | `spec.traits` |
| Rolled or placement-specific mob attack-routine behavior | `kind: spawnplan` | `spec.entries[].traits` |
| Permanent per-character bonuses | future character feature/grant record | not currently a world manifest |

The class-profile location is a near-term fit because WR2 currently models
classes through `spec.stats.class_profiles`. If WR2 later adds a dedicated
`kind: class` manifest, the same feature data can move there while normalizing
to the same runtime grant model.

The YAML examples below are supported by the current WR2 manifest normalizers.

## World Example: Assassin-Only Dual Wielding

This world allows only the `assassin` class to equip offhand weapons. Anyone who
legally dual wields gets one mainhand strike and one offhand strike per primary
attack routine.

```yaml
kind: world
spec:
  equipment:
    offhand_weapons:
      default_allowed: false
      allowed_grips:
        - one_hand

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
        offhand_weapon_slot: offhand

  stats:
    class_profiles:
      warrior:
        label: Warrior

      assassin:
        label: Assassin
        features:
          equipment:
            can_equip_offhand_weapon: true
            allowed_offhand_weapon_grips:
              - one_hand
```

The combat routine produced for an assassin with two one-handed weapons is:

```yaml
strikes:
  - source: base
    weapon_slot: weapon
    damage_multiplier: 1.0
  - source: dual_wield_offhand
    weapon_slot: offhand
    damage_multiplier: 0.5
```

A warrior with a one-handed weapon and shield still gets only the base mainhand
strike unless another feature grants an extra mainhand strike.

## Ability Example: Extra Mainhand Attack For Six Rounds

This ability grants one extra mainhand strike for six rounds. The extra strike
uses the `weapon` slot, so it works with a one-handed weapon, two-handed weapon,
or a mainhand weapon plus shield.

```yaml
kind: ability
metadata:
  slug: battle-trance
  name: Battle Trance
spec:
  version: 1
  command:
    verbs:
      - trance
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
      text:
        label: Battle Trance
      primitives:
        - type: combat_modifier
          phase: attack_routine
          attack_routine:
            extra_mainhand_strikes: 1
            strike:
              source: battle-trance
              weapon_slot: weapon
              damage_multiplier: 1.0
```

The active effect system already supports refreshable character-scoped effects
and `combat_modifier` primitives for outgoing damage. The target change is to
add `attack_routine` as another supported combat modifier phase.

## Mob Example: Dual-Wielding Mob In A Restricted World

Player class restrictions should not block authored mob behavior. If a world
allows only assassins to dual wield, a particular mob can still dual wield by
declaring a mob trait.

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
      visibility: visible
      params:
        attack_routine:
          extra_offhand_strikes: 1
          offhand_damage_multiplier: 0.5
```

For mobs, this should initially be treated as an attack-routine trait, not as a
requirement to equip two item records. Current WR2 mob damage uses the mob's
authored `weapon_damage` stat instead of equipped item weapon damage.

The resolved routine for this mob is:

```yaml
strikes:
  - source: base
    weapon_slot: weapon
    damage_multiplier: 1.0
  - source: dual-wielder
    weapon_slot: offhand
    damage_multiplier: 0.5
```

If only some spawned copies should dual wield, put the same trait on a
`spawnplan` entry:

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
            label: Dual Wielder
            params:
              attack_routine:
                extra_offhand_strikes: 1
                offhand_damage_multiplier: 0.5
```

If builders also want the mob to visibly carry or drop two weapons, that should
be separate equipment or loot authoring. Combat should not depend on those item
records unless WR2 later extends mob combat to read slot-specific weapon item
damage.

## Stacking Policy

Stacking is explicit so overlapping sources do not create accidental
unbounded multiattack behavior.

Recommended defaults:

- `base_mainhand_strikes` defaults to `1`.
- `extra_mainhand_strikes` should use `max` stacking by default.
- `max_primary_strikes` should cap total mainhand strikes unless explicitly
  raised.
- Dual-wield offhand strikes should be separate from extra mainhand strikes.
- A temporary ability that grants an extra mainhand strike should refresh or
  replace itself by stack key, not create unbounded extra attacks.

This prevents class feature plus permanent bonus plus temporary buff plus trait
from accidentally producing many attacks unless a world intentionally allows it.

## Source Semantics

### Class Features

Class features should be declared in the world manifest near class profiles
until a dedicated class manifest exists:

```yaml
kind: world
spec:
  stats:
    class_profiles:
      fighter:
        label: Fighter
        features:
          combat:
            extra_mainhand_strikes: 1
```

### Permanent Character Bonuses

Permanent character bonuses need a future character feature or grant record.
They should normalize to the same runtime shape as class features:

```yaml
features:
  combat:
    extra_mainhand_strikes: 1
```

This should not be stored as an active effect, because it should not expire and
should not need effect-duration processing.

### Temporary Ability Effects

Temporary bonuses should use active effects with a combat-routine modifier.
They already have duration, target, stacking, and refresh semantics.

### Mob Traits

Mob traits should use `params.attack_routine` for behavior data:

```yaml
traits:
  - key: multiattack
    params:
      attack_routine:
        extra_mainhand_strikes: 1
```

Numeric stat changes should continue to use `modifiers`, as described in
[mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md).

## Formula Interaction

Combat formula resolution should remain profile-driven. The attack routine only
decides how many strikes happen and which weapon slot or virtual source each
strike uses.

Player strike weapon damage:

- `weapon_slot: weapon` reads the main weapon's `weapon_damage`.
- `weapon_slot: offhand` reads the offhand weapon's `weapon_damage`.

Mob strike weapon damage:

- the current implementation uses the mob's authored `weapon_damage` for both
  main and virtual offhand strikes
- an offhand multiplier can represent weaker offhand output
- later, equipped mob weapon items can become strike-specific damage sources if
  mob equipment becomes a first-class combat input

Two-handed weapons do not need special attack-routine behavior. An extra
mainhand strike with `weapon_slot: weapon` naturally uses the two-handed weapon
again.

## Runtime Implementation

Implemented runtime support includes:

- weapon-slot-aware attack resolution, defaulting to the mainhand path
- an attack-routine resolver that returns ordered strike specs
- player and mob primary turns executing all strikes in the resolved routine
- world combat config normalization for `spec.combat.attack_routine`
- world equipment config normalization for `spec.equipment.offhand_weapons`
- class profile normalization for `features.combat` and `features.equipment`
- active effect `combat_modifier` primitives with `phase: attack_routine`
- mob trait handling for `params.attack_routine`
- builder-facing docs in
  [attack-routine-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/attack-routine-builder-guide.md)

## Open Questions

- Should `max_primary_strikes` cap only mainhand strikes, or all strike specs
  including offhand strikes?
- Should offhand strikes have an accuracy penalty in addition to a damage
  multiplier?
- Should an ability that consumes the primary action replace the attack routine
  unless it explicitly says otherwise?
- Should mobs ever use equipped item weapon damage, or should mob weapon items
  remain visual/loot data while authored mob stats drive combat?
- Should class features remain inside `spec.stats.class_profiles`, or should
  WR2 introduce `kind: class` before feature-heavy classes are broadly used?
