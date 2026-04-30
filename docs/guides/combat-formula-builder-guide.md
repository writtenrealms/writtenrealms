# Combat Formula Builder Guide

## Overview

WR2 combat is configured from your world YAML manifest.

There are two related systems:

- `stats` decides what numbers a player or mob has, such as attack power,
  ability power, armor, dodge, crit, and resilience.
- `combat` decides how those numbers become damage, dodge, crits, and
  mitigation.

Most builders should only need to tune a few combat values:

- `weapon_damage` on weapon item templates
- `power_scale` for how much attack power or ability power matters
- `can_dodge` and `can_crit`
- `crit_multiplier`
- `mitigation.armor`
- `mitigation.resilience`
- `variance.percent`
- `minimum`

The default combat model is:

- physical attacks use weapon damage plus attack power
- unarmed players use a reduced attack power fallback
- mobs without weapons get a level-based fallback
- armor mitigates physical damage
- resilience mitigates ability or magical damage
- dodge and crit use level-scaled ratings

## Where To Edit

Open the world config YAML from the builder UI, then edit:

```yaml
kind: world
spec:
  combat:
    ...
```

Weapons are edited on item template manifests:

```yaml
kind: itemtemplate
metadata:
  slug: iron-sword
  name: an iron sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_type: sword
  weapon_damage: 12
```

`weapon_damage` is the direct weapon hit value. It is separate from
`attack_power`, so you can make worlds where gear matters heavily, worlds where
attributes matter heavily, or something in between.

## Full Default Combat Shape

The normalized world config will include a full combat block. This is the
important part:

```yaml
combat:
  version: 1
  default_attack_profile: basic_physical
  default_ability_profile: basic_ability
  default_healing_profile: basic_heal
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

You do not need to paste the full block every time. You can paste only the
fields you want to change.

## Common Edits

### Make Combat Less Random

Use this if you want predictable output while testing:

```yaml
kind: world
spec:
  combat:
    variance:
      enabled: false
      percent: 0
    profiles:
      basic_physical:
        can_dodge: false
        can_crit: false
      basic_ability:
        can_crit: false
```

### Make Weapons Matter More

Increase weapon damage scaling and lower attack power scaling:

```yaml
kind: world
spec:
  combat:
    profiles:
      basic_physical:
        weapon_damage_scale: 1.25
        power_scale: 0.03
```

Then give weapons clear `weapon_damage` values:

```yaml
kind: itemtemplate
metadata:
  slug: frontier-rifle
  name: a frontier rifle
spec:
  type: equippable
  equipment_type: weapon_2h
  weapon_type: rifle
  weapon_damage: 28
  attack_power: 0
```

### Make Attributes Matter More

Lower weapon scaling and increase attack power scaling:

```yaml
kind: world
spec:
  combat:
    profiles:
      basic_physical:
        weapon_damage_scale: 0.7
        power_scale: 0.12
```

This makes the same sword better in the hands of a high-attack-power character.

### Make WR1-Style Physical Mitigation

WR2 defaults to armor for physical damage and resilience for ability damage.
If you want physical hits to be reduced by both armor and resilience:

```yaml
kind: world
spec:
  combat:
    profiles:
      basic_physical:
        mitigation:
          armor: true
          resilience: true
```

### Make Combat More Tactical And Less Twitchy

Pair formula tuning with paced encounters:

```yaml
kind: world
spec:
  combat_resolution_interval: 2
  combat:
    variance:
      enabled: false
      percent: 0
    profiles:
      basic_physical:
        can_dodge: true
        can_crit: true
        crit_multiplier: 1.5
```

Players queue or repeat commands between rounds, and each combat round resolves
on the interval.

### Make Fully Async Combat

Use `combat_resolution_interval: -1`:

```yaml
kind: world
spec:
  combat_resolution_interval: -1
```

In this mode, combat does not auto-advance. A player command can advance the
encounter one round at a time. This is useful for slower, async, or
single-player-like worlds.

### Resolve Combat Immediately

Use `combat_resolution_interval: 0`:

```yaml
kind: world
spec:
  combat_resolution_interval: 0
```

The engine will auto-resolve rounds as quickly as it can until someone dies or
combat stalls. This can feel more like auto-chess or fast simulation.

## Designing Weapon Damage

A practical starting point:

- weak level 1 weapon: `weapon_damage: 5`
- decent level 1 weapon: `weapon_damage: 8`
- strong level 1 two-handed weapon: `weapon_damage: 12`
- level 10 one-handed weapon: `weapon_damage: 18-25`
- level 10 two-handed weapon: `weapon_damage: 28-38`

These are not hard rules. They depend on your health pools, attack power,
armor, crit, and encounter pacing.

For a simple world, start by making a basic mob die in about 4-8 successful
hits from an appropriately equipped player.

## Designing Armor And Resilience

Use armor when you want protection from weapons, claws, fists, bullets, blades,
or other physical attacks.

Use resilience when you want protection from magic, psionics, tech abilities,
mental strain, elemental force, or other non-weapon ability attacks.

If your world is not fantasy, rename the labels in `spec.stats.labels.derived`.
For example:

```yaml
kind: world
spec:
  stats:
    labels:
      derived:
        ability_power: Technique
        resilience: Focus
```

The engine names stay stable, but players see world-appropriate labels.

## Designing Crit And Dodge

Dodge uses a mitigation curve. It has a small default base chance:

```yaml
dodge:
  base: 0.02
  constant: 60
  cap: 0.75
```

Crit uses a linear rating:

```yaml
crit:
  base: 0.02
  constant: 120
  cap: 1.0
```

Lower `constant` makes each rating point stronger. Higher `constant` makes the
rating weaker. Lower `cap` prevents extremes.

For early tuning, change profile flags first:

```yaml
profiles:
  basic_physical:
    can_dodge: false
    can_crit: true
```

Only tune rating constants after you know the basic hit counts feel right.

## Example: Low-Magic Frontier World

This world labels ability power as technique, keeps physical combat weapon
heavy, and makes resilience protect against non-weapon techniques.

```yaml
kind: world
spec:
  combat_resolution_interval: 1.5
  stats:
    labels:
      resources:
        energy: Grit
      derived:
        ability_power: Technique
        resilience: Nerve
  combat:
    variance:
      enabled: true
      percent: 8
    profiles:
      basic_physical:
        weapon_damage_scale: 1.15
        power_scale: 0.04
        mitigation:
          armor: true
          resilience: false
      basic_ability:
        power_stat: ability_power
        power_scale: 0.08
        mitigation:
          armor: false
          resilience: true
```

Example weapon:

```yaml
kind: itemtemplate
metadata:
  slug: rusted-revolver
  name: a rusted revolver
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_type: pistol
  weapon_damage: 10
  hit_msg_first: shoot
  hit_msg_third: shoots
```

## Example: WR1-Like World

This keeps the WR1-style weapon plus AP coefficient and opts physical damage
into both armor and resilience.

```yaml
kind: world
spec:
  combat:
    variance:
      enabled: true
      percent: 12.5
    profiles:
      basic_physical:
        power_scale: 0.0625
        weapon_damage_scale: 1.0
        unarmed_power_scale: 0.0417
        mob_unarmed_level_scale: 0.5
        can_dodge: true
        can_crit: true
        crit_multiplier: 1.5
        mitigation:
          armor: true
          resilience: true
      basic_ability:
        power_stat: ability_power
        power_scale: 0.1
        can_crit: true
        mitigation:
          armor: false
          resilience: true
```

You can still label `ability_power` as Spell Power in `spec.stats` if your
world wants WR1 fantasy vocabulary.

## Example: Fast Auto-Resolve Encounters

This is useful for auto-chess-like or simulation-heavy worlds.

```yaml
kind: world
spec:
  combat_resolution_interval: 0
  combat:
    variance:
      enabled: false
      percent: 0
    profiles:
      basic_physical:
        can_dodge: true
        can_crit: true
        minimum: 1
      basic_ability:
        can_crit: true
        minimum: 1
```

The engine resolves repeated combat rounds immediately until the encounter
ends. The formulas are the same; only pacing changes.

## Tuning Order

Use this order when balancing:

1. Set health pools.
2. Set weapon damage and mob attack power.
3. Disable variance, dodge, and crit while checking basic hit counts.
4. Tune `power_scale` and `weapon_damage_scale`.
5. Re-enable armor and resilience.
6. Re-enable crit and dodge.
7. Re-enable variance.
8. Set the encounter interval that matches your desired feel.

Do not tune every knob at once. If a rat dies too slowly, first decide whether
the problem is health, weapon damage, attack power scaling, or mitigation.

## Common Mistakes

- Do not use `spell_power` in new combat configs unless you are working around
  old data. Use `ability_power`.
- Do not set `constant` to zero in ratings.
- Do not make armor and resilience both apply everywhere unless that is a
  deliberate WR1-like choice.
- Do not use huge `weapon_damage` values and huge `attack_power` scaling at the
  same time unless you also raise health pools.
- Do not judge combat feel with variance, dodge, and crit enabled until basic
  hit counts are reasonable.

## How To Verify In Game

Create or edit:

- one weapon with known `weapon_damage`
- one weak mob with low health and low armor
- one armored mob with the same health and higher armor
- one resilient mob with the same health and higher resilience

Then test:

- physical attacks should perform worse against armor
- physical attacks should ignore resilience unless you opted it in
- ability attacks should perform worse against resilience
- ability attacks should ignore armor by default
- combat messages should show hits, dodges, crits, death, and rewards correctly

Use the `stats` command to verify the actor's effective stats before testing
combat outcomes.
