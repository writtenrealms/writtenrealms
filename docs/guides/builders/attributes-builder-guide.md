# Attributes Builder Guide

WR2 separates character numbers into two layers:

1. Stats: fixed WR2 numbers that already have gameplay behavior.
2. Attributes: builder-authored numbers that feed those stats.

A new world starts as a blank slate. It does not get `strength`, `dexterity`,
`intelligence`, `constitution`, classes, or stat formulas unless a builder adds
them.

## Stats

Stats are the fixed keys WR2 already knows how to use. Combat, resources,
equipment, effects, and UI can rely on these keys.

Resources:

- `health_max`
- `energy_max`
- `stamina_max`
- `health_regen`
- `energy_regen`
- `stamina_regen`

Combat stats:

- `weapon_damage` (the equipped main-hand weapon's damage in player stat output)
- `attack_power`
- `ability_power`
- `armor`
- `crit`
- `dodge`
- `resilience`

Item and mob definitions can also set direct stats such as
`weapon_damage`, `attack_power`, `ability_power`, or `armor`. Those are not
attributes; they are already final stats WR2 understands.

Use `ability_power` in new world stats and combat config. If you want players
to see a more specific name, label it in world YAML:

```yaml
spec:
  stats:
    labels:
      stats:
        weapon_damage: Weapon Damage
        ability_power: Spell Power
```

## Attributes

Attributes are the names a builder chooses for the world. They can be
classic fantasy stats such as:

- `strength`
- `dexterity`
- `intelligence`
- `constitution`

They can also be setting-specific attributes such as `grit`, `focus`, or
`cybernetics`.

Attributes have no behavior by themselves. `strength` does not affect
combat until the world's formulas map `strength` into stats.

## Defining Attributes

Define attributes in the world manifest under `spec.stats`:

```yaml
kind: world
metadata:
  slug: ashlands
spec:
  stats:
    attributes:
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

## Labeling Stats

Builders can rename stats for player-facing text without changing the YAML
keys used by formulas and combat:

```yaml
spec:
  stats:
    labels:
      resources:
        energy: Mana
      stats:
        ability_power: Spell Power
        attack_power: Attack Power
        armor: Armor
```

`resources` labels the resource families shown as bars or vitals. `stats`
labels the fixed WR2 stat keys shown as stat rows. Both are presentation labels;
neither creates new stats or attributes.

The key stays `ability_power`; players can see `Spell Power`. The `stats`
command also shows the equipped main-hand `weapon_damage` before Attack Power,
or `0` when the player is unarmed.

## Mapping Attributes To Stats

Use formulas to turn attributes into stats:

```yaml
spec:
  stats:
    attributes:
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
      stats:
        ability_power: Spell Power

    formulas:
      base_stats:
        stamina_regen: 2
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

Formula targets must be stats. If a formula points at a stat WR2 does not
support, the World Editor should reject the YAML instead of creating a stat
that does nothing.

## Direct Stats Versus Attributes

Definitions can provide stats in two ways.

Direct stats are already meaningful:

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

Attributes are interpreted through the world's formulas:

```yaml
kind: mobdefinition
metadata:
  slug: guard
  name: a town guard
spec:
  level: 3
  attributes:
    strength: 5
    constitution: 4
```

Both are valid. Direct stats are simple and explicit. Attributes are
better when builders want mobs, players, items, and effects to share the same
world-specific stat language.

When a mob definition provides both direct stats and attributes, they add
together on the spawned mob. For example, if `strength` maps to `attack_power`
at `1:1`, then `attack_power: 3` plus `attributes.strength: 5` produces an
effective `attack_power` of `8`.

## Items

Item definitions can contribute attributes:

```yaml
kind: itemdefinition
metadata:
  slug: iron-sword
  name: an iron sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 12
  attributes:
    strength: 3
    dexterity: 1
```

The item does not need to know what `strength` means. The world formulas decide
whether `strength` becomes attack power, armor, health, or something else.

## Mobs And Players

Mobs and players can carry `attributes`. Class profiles can also create
level-based attributes for players:

```yaml
spec:
  stats:
    class_profiles:
      warrior:
        label: Warrior
        main_attribute: strength
        armor_proficiencies: [light, heavy]
        attribute_weights:
          strength: 4
          constitution: 3
          dexterity: 1
          intelligence: 1
```

Classes are optional. If `class_profiles` is empty or omitted, the world has no
classes.

`armor_proficiencies` is optional class-profile data. When the world defines
`spec.equipment.armor_classes`, the equip command uses each character's class
profile to decide whether armor and shield items with `armor_class` can be
equipped. If a class omits `armor_proficiencies`, it inherits from
`default_profile`. If neither the class nor `default_profile` declares
proficiencies, the class is unrestricted for compatibility. An explicit empty
list means the class is proficient with no authored armor classes.

When WR2 calculates a character's final stats, it uses this shape:

```text
level/class attributes
+ character attributes
+ equipped item attributes
+ active effect attributes
= total attributes

total attributes + formulas + direct stats
= final stats
```

Combat then uses final stats such as `attack_power`,
`ability_power`, `armor`, `crit`, `dodge`, and `resilience`.

## Formula Modes

Most rules should use `mode: total`.

Available modes:

- `total`: use the full current value of the attribute.
- `base_only`: use only the level/class baseline value.
- `bonus_from_total_minus_base`: use only bonuses above the baseline.

`bonus_from_total_minus_base` is useful when a world wants an attribute to set
a baseline resource but only wants gear or effects to add extra value.

## Guardrail

Builders can define arbitrary attributes, but they should map into stats. Do
not invent formula targets unless WR2 has gameplay behavior for them.
