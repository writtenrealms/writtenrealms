# WR1-Like Archetype World Reference

This document shows how to configure a WR2 world so it behaves roughly like a
WR1 world with the familiar archetype-based stat model:

- warrior
- assassin
- mage
- cleric

The goal is not perfect WR1 parity. The goal is a clean WR2 reference
implementation that preserves the same broad mental model while using the new
WR2 stat system.

## What Landed

WR2 now supports a world-authored stat system on the world manifest under:

```yaml
kind: world
spec:
  stats: ...
```

That stat system currently controls:

- input attribute definitions and labels
- resource labels
- derived stat labels
- class profile labels
- base attribute weights per class profile
- formula rules mapping inputs into derived combat stats

The runtime now uses the authored world stat system for:

- player stat computation
- state sync payloads
- player vitals and stat displays
- combat stat lookup
- player regen
- builder world manifest import/export
- mob template stat generation when using computed template stats

## Important Current Scope

Use this feature with these current boundaries in mind:

1. Canonical runtime names are `energy` and `ability_power`.
   Player-facing labels can still be `Mana` and `Spell Power`.

2. The database still has compatibility aliases for older names such as
   `mana` and `spell_power`, but builder-authored input attributes live in
   `input_attributes`, not fixed primary stat columns.

3. Class ids are non-empty slugs. A WR1-like world usually uses:
   - `warrior`
   - `assassin`
   - `mage`
   - `cleric`

4. Equipment, mob templates, players, and spawned items can carry
   `input_attributes` values for whichever keys the world defines.

## Recommended WR1-Compatible Approach

For a WR1-style world:

- define the four WR1-like inputs explicitly:
  - `constitution`
  - `strength`
  - `dexterity`
  - `intelligence`
- define class profiles; their presence is what makes the world class-based
- label `energy` as `Mana`
- label `ability_power` as `Spell Power`
- use WR1 archetype ids for class profiles
- use the WR1-style weights and derived rules shown below

## Reference World Manifest

This is the recommended WR1-like baseline:

```yaml
kind: world
spec:
  name: Example WR1 World
  short_description: ''
  description: ''
  motd: ''
  is_public: false
  starting_gold: 0
  combat_resolution_interval: 0
  starting_room: room@0,0,0
  death_room: room@0,0,0
  death_mode: lose_none
  death_route: top_faction
  pvp_mode: free_for_all
  can_select_faction: true
  auto_equip: true
  is_narrative: false
  players_can_set_title: true
  allow_pvp: true
  non_ascii_names: false
  globals_enabled: true
  decay_glory: false
  built_by: ''
  small_background: ''
  large_background: ''
  name_exclusions: ''

  stats:
    input_attributes:
      - key: constitution
        label: Constitution
      - key: strength
        label: Strength
      - key: dexterity
        label: Dexterity
      - key: intelligence
        label: Intelligence

    labels:
      resources:
        health: Health
        energy: Mana
        stamina: Stamina
      derived:
        attack_power: Attack Power
        ability_power: Spell Power
        armor: Armor
        crit: Crit
        dodge: Dodge
        resilience: Resilience
        health_regen: Health Regen
        energy_regen: Mana Regen
        stamina_regen: Stamina Regen
      classes:
        warrior: Warrior
        assassin: Assassin
        mage: Mage
        cleric: Cleric

    derived_display_order:
      - attack_power
      - ability_power
      - crit
      - armor
      - resilience
      - dodge
      - health_regen
      - energy_regen
      - stamina_regen

    default_profile:
      label: ""
      main_attribute: ""
      base_attribute_weights:
        constitution: 3
        strength: 2
        dexterity: 2
        intelligence: 2

    class_profiles:
      warrior:
        label: Warrior
        main_attribute: strength
        base_attribute_weights:
          constitution: 3
          strength: 4
          dexterity: 1
          intelligence: 1
        derived_rules:
          - source: strength
            target: crit
            multiplier: 1

      assassin:
        label: Assassin
        main_attribute: dexterity
        base_attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 4
          intelligence: 1
        derived_rules:
          - source: dexterity
            target: attack_power
            multiplier: 1

      mage:
        label: Mage
        main_attribute: intelligence
        base_attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 1
          intelligence: 4

      cleric:
        label: Cleric
        main_attribute: intelligence
        base_attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 1
          intelligence: 4

    formulas:
      base_resources:
        energy:
          source: intelligence
          multiplier: 2
        stamina:
          flat: 50

      global_rules:
        - source: constitution
          target: health_max
          multiplier: 2
        - source: constitution
          target: resilience
          multiplier: 1
        - source: strength
          target: attack_power
          multiplier: 1
        - source: strength
          target: health_max
          multiplier: 1
        - source: intelligence
          target: ability_power
          multiplier: 2
        - source: intelligence
          target: energy_max
          multiplier: 1
          mode: bonus_from_total_minus_base
        - source: dexterity
          target: dodge
          multiplier: 1
        - source: dexterity
          target: crit
          multiplier: 1

      two_handed_multipliers:
        attack_power: 1.5
        ability_power: 1.5

      mob_boost:
        slot_factor: 10.25
        elite_multiplier: 1.2
        armor_multiplier_by_profile:
          warrior: 3
          default: 2
```

## How This Maps To WR1

This reference configuration preserves the major WR1 patterns:

- warrior weights:
  - constitution `3`
  - strength `4`
  - dexterity `1`
  - intelligence `1`
- assassin weights:
  - constitution `3`
  - strength `1`
  - dexterity `4`
  - intelligence `1`
- mage and cleric weights:
  - constitution `3`
  - strength `1`
  - dexterity `1`
  - intelligence `4`
- default profile weights:
  - constitution `3`
  - strength `2`
  - dexterity `2`
  - intelligence `2`

Derived rules also mirror WR1:

- constitution:
  - `health_max += constitution * 2`
  - `resilience += constitution`
- strength:
  - `attack_power += strength`
  - `health_max += strength`
  - warrior also gets `crit += strength`
- intelligence:
  - `ability_power += intelligence * 2`
  - `energy_max += intelligence - base_intelligence`
- dexterity:
  - `dodge += dexterity`
  - `crit += dexterity`
  - assassin also gets `attack_power += dexterity`

`energy` is just the WR2 engine name for the old `mana` concept. In this WR1
reference setup, it is labeled back to `Mana`.

`ability_power` is just the WR2 engine name for the old `spell_power` concept.
In this WR1 reference setup, it is labeled back to `Spell Power`.

## How To Apply It

Use the normal world manifest flow:

1. Open the world in the builder.
2. Go to **World > Edit World**.
3. Paste the full `kind: world` manifest.
4. Apply it.
5. Reconnect a player or run `state.sync` to confirm the labels and stats are
   showing as expected.

## What To Verify In Game

After applying the manifest:

1. The vitals panel should show `Health`, `Mana`, and `Stamina`.
2. The stats panels should show `Spell Power`, not `Ability Power`.
3. Warriors should show more Strength-weighted stats.
4. Assassins should show more Dexterity-weighted stats.
5. Mages and clerics should show more Intelligence-weighted stats.
6. Combat, regen, and state sync should still function normally.

## If You Want A Less Fantasy-Specific World

You do not need to change the formulas first. Start by changing only labels.

Example:

```yaml
spec:
  stats:
    labels:
      resources:
        energy: Energy
      derived:
        ability_power: Tech Power
```

That gives you a less fantasy-coded presentation without changing the runtime
combat contract.

## Current Follow-Up Gaps

These are the main things not yet generalized:

- item and mob template stat columns are still legacy field-based
- custom class ids are not yet fully exposed across all builder/player flows
- arbitrary formula DSL expressions do not exist; the current system is a
  bounded rules model

That is intentional. The current implementation is meant to be understandable,
manifest-editable, and close enough to WR1 to use immediately as a foundation.
