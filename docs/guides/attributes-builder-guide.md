# Attributes Builder Guide

WR2 separates attributes into two layers:

1. Built-in game stats: fixed numbers WR2 already knows how to use.
2. Input attributes: builder-authored numbers that feed those built-in stats.

A new world starts as a blank slate. It does not get `strength`, `dexterity`,
`intelligence`, `constitution`, classes, or stat formulas unless a builder adds
them.

## Built-In Game Stats

Built-in game stats are the stat keys that already have gameplay behavior.
Combat, resources, equipment, effects, and UI can rely on these keys.

Resources:

- `health_max`
- `energy_max`
- `stamina_max`
- `health_regen`
- `energy_regen`
- `stamina_regen`

Combat stats:

- `attack_power`
- `ability_power`
- `armor`
- `crit`
- `dodge`
- `resilience`

Item and mob definitions can also set direct built-in stats such as
`weapon_damage`, `attack_power`, `ability_power`, or `armor`. Those are not
input attributes; they are already final stats WR2 understands.

Use `ability_power` in new world stats and combat config. If you want players
to see a more specific name, label it in world YAML:

```yaml
spec:
  stats:
    labels:
      derived:
        ability_power: Spell Power
```

## Input Attributes

Input attributes are the names a builder chooses for the world. They can be
classic fantasy stats such as:

- `strength`
- `dexterity`
- `intelligence`
- `constitution`

They can also be setting-specific stats such as `grit`, `focus`, `faith`, or
`cybernetics`.

Input attributes have no behavior by themselves. `strength` does not affect
combat until the world's formulas map `strength` into built-in game stats.

## Defining Attributes

Define input attributes in the world manifest under `spec.stats`:

```yaml
kind: world
metadata:
  slug: ashlands
spec:
  stats:
    input_attributes:
      - key: strength
        label: Strength
      - key: dexterity
        label: Dexterity
      - key: intelligence
        label: Intelligence
      - key: constitution
        label: Constitution
```

This only declares the attributes. It does not make them affect combat yet.

## Labeling Built-In Stats

Builders can rename built-in stats for player-facing text without changing the
YAML keys used by formulas and combat:

```yaml
spec:
  stats:
    labels:
      resources:
        energy: Mana
      derived:
        ability_power: Spell Power
        attack_power: Attack Power
        armor: Armor
```

The key stays `ability_power`; players can see `Spell Power`.

## Mapping Inputs To Built-In Stats

Use formulas to turn input attributes into built-in stats:

```yaml
spec:
  stats:
    input_attributes:
      - key: strength
        label: Strength
      - key: dexterity
        label: Dexterity
      - key: intelligence
        label: Intelligence
      - key: constitution
        label: Constitution

    labels:
      resources:
        energy: Mana
      derived:
        ability_power: Spell Power

    formulas:
      base_resources:
        health:
          flat: 30
        energy:
          source: intelligence
          multiplier: 2
        stamina:
          flat: 50
      global_rules:
        - source: constitution
          target: health_max
          multiplier: 2
          mode: total
        - source: strength
          target: attack_power
          multiplier: 1
          mode: total
        - source: intelligence
          target: ability_power
          multiplier: 2
          mode: total
        - source: dexterity
          target: dodge
          multiplier: 1
          mode: total
        - source: dexterity
          target: crit
          multiplier: 1
          mode: total
```

Formula targets must be built-in game stats. If a formula points at a stat WR2
does not support, the World Editor should reject the YAML instead of creating a
stat that does nothing.

## Direct Stats Versus Input Attributes

Definitions can provide stats in two ways.

Direct built-in stats are already meaningful:

```yaml
kind: mobdefinition
metadata:
  slug: guard
  name: a town guard
spec:
  level: 3
  health_max: 45
  attack_power: 8
  armor: 4
```

Input attributes are interpreted through the world's formulas:

```yaml
kind: mobdefinition
metadata:
  slug: guard
  name: a town guard
spec:
  level: 3
  input_attributes:
    strength: 5
    constitution: 4
```

Both are valid. Direct stats are simple and explicit. Input attributes are
better when builders want mobs, players, items, and effects to share the same
world-specific stat language.

## Items

Item definitions can contribute input attributes:

```yaml
kind: itemdefinition
metadata:
  slug: iron-sword
  name: an iron sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 12
  input_attributes:
    strength: 3
    dexterity: 1
```

The item does not need to know what `strength` means. The world formulas decide
whether `strength` becomes attack power, armor, health, or something else.

## Mobs And Players

Mobs and players can carry `input_attributes`. Class profiles can also create
level-based input attributes for players:

```yaml
spec:
  stats:
    class_profiles:
      warrior:
        label: Warrior
        main_attribute: strength
        base_attribute_weights:
          strength: 4
          constitution: 3
          dexterity: 1
          intelligence: 1
```

Classes are optional. If `class_profiles` is empty or omitted, the world has no
classes.

When WR2 calculates a character's final stats, it uses this shape:

```text
level/class input attributes
+ character input attributes
+ equipped item input attributes
+ active effect input attributes
= total input attributes

total input attributes + formulas + direct built-in stats
= final stats
```

Combat then uses final stats such as `attack_power`,
`ability_power`, `armor`, `crit`, `dodge`, and `resilience`.

## Formula Modes

Most rules should use `mode: total`.

Available modes:

- `total`: use the full current value of the input attribute.
- `base_only`: use only the level/class baseline value.
- `bonus_from_total_minus_base`: use only bonuses above the baseline.

`bonus_from_total_minus_base` is useful when a world wants an input to set a
baseline resource but only wants gear or effects to add extra value.

## Guardrail

Builders can define arbitrary input attributes, but they should map into the
built-in game stats. Do not invent formula targets unless WR2 has gameplay
behavior for them.
