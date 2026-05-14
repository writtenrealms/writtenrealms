# Input Attribute Builder Guide

WR2 worlds start as a blank slate. They do not get `strength`, `dexterity`,
`intelligence`, or `constitution` unless a builder explicitly defines those
attributes.

Input attributes are builder-authored numbers such as:

- `brawn`
- `grace`
- `willpower`
- `faith`
- `tech`

Combat code does not consume those inputs directly. The world stat config maps
them into canonical derived stats such as `attack_power`, `armor`,
`energy_max`, `crit`, `dodge`, and `resilience`.

## World Stats

Define input attributes and formula rules in the world manifest:

```yaml
kind: world
metadata:
  slug: iron-frontier
spec:
  stats:
    input_attributes:
      - key: brawn
        label: Brawn
      - key: grace
        label: Grace
      - key: willpower
        label: Willpower

    labels:
      resources:
        energy: Resolve
      derived:
        attack_power: Force
        armor: Toughness
        ability_power: Technique

    class_profiles:
      bruiser:
        label: Bruiser
        main_attribute: brawn
        base_attribute_weights:
          brawn: 4
          grace: 1
          willpower: 2
      duelist:
        label: Duelist
        main_attribute: grace
        base_attribute_weights:
          brawn: 2
          grace: 4
          willpower: 1

    formulas:
      base_resources:
        health:
          flat: 30
        stamina:
          flat: 50
        energy:
          source: willpower
          multiplier: 3
      global_rules:
        - source: brawn
          target: attack_power
          multiplier: 1
        - source: brawn
          target: armor
          multiplier: 0.5
        - source: grace
          target: dodge
          multiplier: 1
        - source: grace
          target: crit
          multiplier: 0.75
        - source: willpower
          target: resilience
          multiplier: 1
```

## Items

Items contribute input attributes with an `input_attributes` map:

```yaml
kind: itemtemplate
metadata:
  slug: iron-sword
  name: an iron sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 12
  input_attributes:
    brawn: 3
    grace: 1
```

The item does not need to know what `brawn` means. The world formulas decide
how `brawn` affects derived stats.

## Mobs And Players

Mobs and players can also carry an `input_attributes` map. Class profiles are
usually the simplest way to give players level-based inputs, while mob
definitions can use explicit input attributes for special cases.

Runtime stat calculation uses:

```text
level/class input attributes
+ character input attributes
+ equipped item input attributes
+ active effect input attributes
= total input attributes

total input attributes + formulas
= canonical derived stats
```

## Guardrail

Builders can define arbitrary inputs, but formula targets must be canonical
derived stats. Add a new derived stat only when the engine has a concrete
behavior for it.
