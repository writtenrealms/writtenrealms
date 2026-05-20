# Combat Formula Builder Guide

## Overview

WR2 combat is configured from your world YAML manifest.

There are two related systems:

- `stats` decides what numbers a player or mob has, such as attack power,
  ability power, armor, dodge, crit, and resilience.
- `combat` decides how those numbers become damage, dodge, crits, and
  mitigation.

A newly created world may not show a `combat` block in **World > Config > Copy
YAML**. That means the builder has not authored any combat overrides yet. The
runtime still uses the default combat model below, and `spec.combat` only needs
to be added when the builder wants to tune that model.

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

Auto-attacks use `basic_physical` by default. Ability components can use
`basic_physical`, `basic_ability`, `basic_heal`, or a custom combat profile
defined by the world. See
[ability-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/ability-builder-guide.md)
for the ability authoring shape.

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

The runtime normalizes missing combat config against this default shape. Copy
YAML omits the block until the world has authored combat config; paste only the
fields you want to change under `spec.combat`.

```yaml
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

You do not need to paste the full block every time. You can paste only the
fields you want to change.

## How A Hit Becomes Damage

Combat uses the actor's effective stats at the moment the hit resolves. For a
player, `attack_power`, `ability_power`, armor, crit, dodge, and resilience come
from the stat system, including equipped item and augment bonuses. The equipped
weapon's `weapon_damage` is read separately from the weapon slot. Use the
`stats` command before testing combat if you want to confirm the exact effective
numbers the engine is using.

On a completely blank new world, there are no authored attributes or combat
power formulas. The default world config still gives players baseline stamina
and stamina regeneration so they can move, but a new unarmed player usually has
`attack_power: 0`. With the default physical profile, that means the player
needs either a weapon with `weapon_damage` or a stat formula that produces
`attack_power` before their basic attack deals damage. Mobs without weapons use
the default level-based fallback described below, so they can still hit even
without authored stats.

For the default physical attack profile, the base damage starts from weapon
damage plus attack power:

```text
power = actor.attack_power
weapon = actor.equipped_weapon.weapon_damage

if weapon > 0:
  base = weapon * weapon_damage_scale + power * power_scale
else:
  base = power * unarmed_power_scale
```

Mobs without weapons use a level-based fallback instead of the player unarmed
formula:

```text
base = level_scale(actor.level) * mob_unarmed_level_scale
     + power * power_scale
```

### Level Scale

`level_scale` is the combat system's way to make ratings and unarmed mob damage
grow with level. Combat clamps levels below `1` up to `1` before applying the
scale.

This is separate from `leveling_curve`, which controls how much XP a player
needs to reach each level.

Combat uses `level_scale` in two places:

- unarmed mob fallback damage:
  `level_scale(actor.level) * mob_unarmed_level_scale`
- rating math for dodge, crit, armor, and resilience:
  `level_scale(opponent.level) * constant`

#### Exponential

`exponential` is the default. It keeps growing past the player level cap, which
lets builders cap players at one level while still creating higher-level
monsters or challenge content.

```yaml
combat:
  level_scale:
    type: exponential
    base: 5.5
    growth: 1.1
```

Formula:

```text
level_scale = base * growth^level
```

With the default `base: 5.5` and `growth: 1.1`:

| Level | `level_scale` |
| ---: | ---: |
| 1 | 6.05 |
| 5 | 8.86 |
| 10 | 14.27 |
| 15 | 22.97 |
| 20 | 37.00 |
| 30 | 95.97 |
| 40 | 248.93 |
| 60 | 1674.65 |

#### Linear

`linear` is easier to reason about and grows at the same amount every level.

```yaml
combat:
  level_scale:
    type: linear
    base: 5.5
    per_level: 1.25
```

Formula:

```text
level_scale = base + per_level * level
```

With the default linear values:

| Level | `level_scale` |
| ---: | ---: |
| 1 | 6.75 |
| 5 | 11.75 |
| 10 | 18.00 |
| 15 | 24.25 |
| 20 | 30.50 |
| 60 | 80.50 |

#### Flat

`flat` ignores level. Use it for worlds where ratings should mean the same
thing at every level.

```yaml
combat:
  level_scale:
    type: flat
    value: 1.0
```

Formula:

```text
level_scale = value
```

#### ILF

`ilf` is the WR1 legacy scale. It preserves the original level 1-20 feel:
levels 1-15 grow quickly, then levels 16-20 taper so that high-level content is
not locked exclusively to capped characters. Values above level 20 currently use
the same scale as level 20.

```yaml
combat:
  level_scale:
    type: ilf
```

Formula:

```text
if level < 17:
  level_scale = 5.5 * 1.1^level
else:
  level_scale = 5.5 * 1.1^16
  if level >= 17: level_scale *= 1.08
  if level >= 18: level_scale *= 1.06
  if level >= 19: level_scale *= 1.04
  if level >= 20: level_scale *= 1.02
```

Approximate values:

| Level | `level_scale` |
| ---: | ---: |
| 1 | 6.05 |
| 5 | 8.86 |
| 10 | 14.27 |
| 15 | 22.97 |
| 16 | 25.27 |
| 17 | 27.29 |
| 18 | 28.93 |
| 19 | 30.09 |
| 20 | 30.69 |

The default ability profile does not use weapon damage:

```text
base = actor.ability_power * power_scale
```

This is the formula an ability damage component will use when it points at
`basic_ability`. It is not currently exposed as a standalone player command.

After base damage is found, the combat profile resolves the hit in this order:

```text
output = base * multiplier

if can_dodge and random() < target_dodge_chance:
  damage_dealt = 0
  damage_taken = 0
  stop

if variance is enabled:
  output *= random number from (1 - variance.percent / 100)
            to (1 + variance.percent / 100)

if can_crit and random() < actor_crit_chance:
  output *= crit_multiplier

damage_dealt = ceil(output)
mitigated = damage_dealt

for each enabled mitigation, such as armor or resilience:
  mitigated *= 1 - target_mitigation_percent

damage_taken = ceil(mitigated)

if damage_dealt > 0:
  damage_taken = max(minimum, damage_taken)
```

`damage_dealt` is the pre-mitigation number. `damage_taken` is the final HP loss
after armor, resilience, and minimum damage.

### Dodge, Crit, Armor, And Resilience Ratings

Dodge, crit, armor, and resilience use rating configs. Each config has the same
shape, though the default values differ by rating:

```yaml
base: 0
constant: 60
cap: 0.75
```

The `base` is the starting chance or mitigation. The `cap` is the maximum. The
`constant` controls how strong each rating point is. Lower constants make rating
points stronger. Higher constants make them weaker.

The rating is scaled against the opponent's level:

```text
opponent_scale = level_scale(opponent.level)
```

For `linear_rating`, used by default crit:

```text
percent = rating / (opponent_scale * constant) + base
percent = clamp(percent, 0, cap)
```

For `mitigation_curve`, used by default dodge, armor, and resilience:

```text
percent = (rating + opponent_scale * constant * base)
        / (rating + opponent_scale * constant)
percent = clamp(percent, 0, cap)
```

For a default physical attack, only armor mitigates damage. Resilience is
ignored unless `mitigation.resilience` is set to `true`. For a default ability
attack, resilience mitigates damage and armor is ignored.

When more than one mitigation is enabled, they stack multiplicatively. For
example, 20% armor mitigation and 10% resilience mitigation leave:

```text
damage * 0.8 * 0.9 = damage * 0.72
```

### Worked Physical Attack Example

Assume:

- the attacker is level 1
- the attacker has `attack_power: 80`
- the attacker has `crit: 30`
- the attacker has a weapon with `weapon_damage: 12`
- the target is level 1
- the target has `armor: 60`
- variance and dodge are disabled for easier math
- the profile uses default `basic_physical` values

Base damage:

```text
base = 12 * 1.0 + 80 * 0.0625
base = 17
```

At level 1, the default level scale is about `6.05`. The attacker's crit chance
against a level 1 target is:

```text
crit_chance = 30 / (6.05 * 120) + 0.02
crit_chance = 0.061, or about 6.1%
```

The target's armor mitigation against a level 1 attacker is:

```text
armor_mitigation = 60 / (60 + 6.05 * 60)
armor_mitigation = 0.142, or about 14.2%
```

On a normal hit:

```text
damage_dealt = ceil(17)
damage_taken = ceil(17 * (1 - 0.142))
damage_taken = 15
```

On a crit:

```text
damage_dealt = ceil(17 * 1.5)
damage_dealt = 26
damage_taken = ceil(26 * (1 - 0.142))
damage_taken = 23
```

If default variance is left on, the `17` output is first randomly adjusted by
`-12.5%` to `+12.5%`, then crit is applied, then mitigation is applied.

For rough balancing, ignoring rounding and minimum damage:

```text
average_damage_before_mitigation =
  base * multiplier * (1 - dodge_chance)
  * (1 + crit_chance * (crit_multiplier - 1))
```

Variance is centered around `1.0`, so it changes the spread of hits more than the
average hit size.

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

## XP And Leveling

Mob rewards use `exp_worth`. When combat grants XP, the player is checked
against the world leveling config (`starting_level`, `leveling_curve`, and
`max_level`). See
[leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md)
for the YAML shape and `/setlevel` testing command.

## Designing Armor And Resilience

Use armor when you want protection from weapons, claws, fists, bullets, blades,
or other physical attacks.

Use resilience when you want protection from magic, psionics, tech abilities,
mental strain, elemental force, or other non-weapon ability attacks.

If your world is not fantasy, rename the labels in `spec.stats.labels.stats`.
For example:

```yaml
kind: world
spec:
  stats:
    labels:
      stats:
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
      stats:
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

- Use `ability_power` in combat configs. Worlds can label it as `Spell Power`
  if that fits the setting.
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
