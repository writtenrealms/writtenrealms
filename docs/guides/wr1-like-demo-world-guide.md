# WR1-Like Demo World Guide

This guide builds a small WR2 demo world with WR1-style fantasy stats:

- `strength`
- `constitution`
- `dexterity`
- `intelligence`
- `energy` labeled as `Mana`
- `ability_power` labeled as `Spell Power`
- WR1-style Warrior, Mage, Assassin, and Cleric class profiles
- exponential WR2 combat level scaling, not WR1 `ilf`
- three starter combat abilities:
  - `bash`: stun a hostile target for 2 rounds
  - `burn`: apply a 3-round damage-over-time effect that ticks for 2x basic
    ability damage each round
  - `slice`: deal 1.5x weapon damage

The result is WR1-flavored, but it uses the WR2 input attribute and ability
systems. Strength, dexterity, constitution, and intelligence are not built into
WR2; this guide adds them explicitly.

## Step 1: Create A Blank World

Create a new world normally in the builder.

Open **World > Edit World**. The examples below are designed to be pasted into
that page.

## Step 2: Add WR1-Like Stats

Paste this world manifest and apply it:

```yaml
kind: world
spec:
  ability_progression:
    max_known: 8

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
        mage: Mage
        assassin: Assassin
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
      derived_rules: []

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

      mage:
        label: Mage
        main_attribute: intelligence
        base_attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 1
          intelligence: 4
        derived_rules: []

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

      cleric:
        label: Cleric
        main_attribute: intelligence
        base_attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 1
          intelligence: 4
        derived_rules: []

    formulas:
      base_resources:
        energy:
          source: intelligence
          multiplier: 2
        stamina:
          flat: 100
        health: {}

      global_rules:
        - source: constitution
          target: health_max
          multiplier: 2
          mode: total
        - source: constitution
          target: resilience
          multiplier: 1
          mode: total
        - source: strength
          target: attack_power
          multiplier: 1
          mode: total
        - source: strength
          target: health_max
          multiplier: 1
          mode: total
        - source: intelligence
          target: ability_power
          multiplier: 2
          mode: total
        - source: intelligence
          target: energy_max
          multiplier: 1
          mode: bonus_from_total_minus_base
        - source: dexterity
          target: dodge
          multiplier: 1
          mode: total
        - source: dexterity
          target: crit
          multiplier: 1
          mode: total

      two_handed_multipliers:
        attack_power: 1.5
        ability_power: 1.5

      mob_boost:
        slot_factor: 10.25
        elite_multiplier: 1.2
        armor_multiplier_by_profile:
          warrior: 3
          default: 2

  combat:
    level_scale:
      type: exponential
      base: 5.5
      growth: 1.1
```

Notes:

- `energy` is the WR2 engine resource. The label makes players see `Mana`.
- `ability_power` is the WR2 engine stat. The label makes players see
  `Spell Power`.
- This is intentionally class-based, just like WR1. Class selection is implied
  by `stats.class_profiles`; no separate classless flag is needed.
- The combat block only states the default exponential level scale explicitly.
  Do not use `type: ilf` if you want the open-ended WR2 scaling behavior.

## Step 3: Add A Starter Weapon

This weapon gives `slice` something to scale from and demonstrates item input
attributes.

Paste and apply:

```yaml
kind: itemtemplate
metadata:
  slug: training-sword
  name: a training sword
spec:
  level: 1
  type: equippable
  equipment_type: weapon_1h
  weapon_grip: one_hand
  weapon_type: sword
  weapon_damage: 8
  is_pickable: true
  cost: 0
  currency: gold
  keywords: sword training
  hit_msg_first: slash
  hit_msg_third: slashes
  input_attributes:
    strength: 2
    dexterity: 1
```

## Step 4: Add A Sparring Mob

Add a simple mob template so the abilities have a target:

```yaml
kind: mobtemplate
metadata:
  slug: sparring-goblin
  name: a sparring goblin
spec:
  level: 1
  keywords: goblin sparring
  description: A wiry goblin watches for an opening.
  room_description: A sparring goblin waits here.
  default_stats: false
  fights_back: true
  use_abilities: false
  health_max: 80
  attack_power: 6
  armor: 0
  resilience: 0
  dodge: 0
  crit: 0
  hit_msg_first: jab
  hit_msg_third: jabs
```

As a builder, you can later place it with:

```text
/load mob sparring-goblin
```

## Step 5: Add The Demo Abilities

Paste this ability bundle and apply it:

```yaml
kind: abilities
spec:
  abilities:
    - slug: bash
      name: Bash
      command:
        verbs:
          - bash
      action_type: primary
      target:
        type: hostile
        default: current_target
      availability:
        classes: []
        min_level: 1
      cooldown:
        rounds: 4
      components:
        - type: effect
          effect: stun
          duration:
            rounds: 2
          apply: on_resolve
          text:
            label: Bash

    - slug: burn
      name: Burn
      command:
        verbs:
          - burn
      action_type: primary
      target:
        type: hostile
        default: current_target
      availability:
        classes: []
        min_level: 1
      cost:
        resource: energy
        amount: 10
        calc: fixed
      cooldown:
        rounds: 3
      components:
        - type: effect
          effect: dot
          duration:
            rounds: 3
          tick:
            every_rounds: 1
            component:
              type: damage
              profile: basic_ability
              overrides:
                multiplier: 2
              text:
                label: Burn
          apply: on_resolve
          text:
            label: Burn

    - slug: slice
      name: Slice
      command:
        verbs:
          - slice
      action_type: primary
      target:
        type: hostile
        default: current_target
      availability:
        classes: []
        min_level: 1
      cooldown:
        rounds: 1
      components:
        - type: damage
          profile: basic_physical
          overrides:
            weapon_damage_scale: 1.5
            power_scale: 0
            unarmed_power_scale: 0
            mob_unarmed_level_scale: 0
            multiplier: 1
          text:
            label: Slice
```

Details:

- `bash` is effect-only. It stuns the target for 2 encounter rounds.
- `burn` is effect-only. It applies a DOT that ticks once per round for 3
  rounds.
- `burn` uses `basic_ability`, so each tick scales from `Spell Power` through
  the basic ability profile. The `multiplier: 2` makes each tick 2x that
  profile's normal output.
- `slice` overrides the physical profile so it uses weapon damage only:
  `weapon_damage * 1.5`. It intentionally sets `power_scale`,
  `unarmed_power_scale`, and `mob_unarmed_level_scale` to `0` so it is not also
  scaling from attack power.

## Step 6: Create A Character

Create a new character in the world.

Pick one of the four class profiles:

- Warrior
- Mage
- Assassin
- Cleric

After entering the game, use the stats command to confirm the labels:

```text
stats
```

You should see `Mana` instead of `Energy`, and `Spell Power` instead of
`Ability Power`.

## Step 7: Load And Equip The Sword

As a builder, load the sword:

```text
/load item training-sword
```

Then equip it with the normal game command:

```text
equip sword
```

Run `stats` again. The sword should contribute `strength` and `dexterity`, and
those inputs should flow through the world formulas into derived stats such as
attack power, crit, dodge, and health.

## Step 8: Learn The Abilities

Use the player ability commands:

```text
learn bash
learn burn
learn slice
```

The default known ability cap is 8, so all three demo abilities fit.

## Step 9: Test Combat

Load or place the sparring goblin:

```text
/load mob sparring-goblin
```

Then try:

```text
bash <mob>
burn <mob>
slice <mob>
```

Expected behavior:

- `bash` should suppress the target's primary action while the stun is active.
- `burn` should apply a 3-round DOT. Each tick uses the basic ability profile
  and a `2x` multiplier, so mages and clerics should generally get better burn
  ticks from higher intelligence and Spell Power.
- `slice` should depend on the equipped weapon's `weapon_damage`. With the
  training sword, the pre-mitigation base should be `8 * 1.5 = 12` before
  variance, crit, mitigation, and minimum damage.

## What To Tune First

If the world feels too lethal or too slow, start with these values:

- `weapon_damage` on starter weapons
- `basic_physical.power_scale` and `basic_ability.power_scale` in
  `spec.combat.profiles`
- class `base_attribute_weights`
- DOT `multiplier`
- stun `duration.rounds`
- `combat.level_scale` if high-level mobs are too weak or too strong

Keep the stat inputs and combat outputs separate. Builders should tune
`strength`, `dexterity`, `constitution`, and `intelligence` through
`spec.stats`, and tune hit resolution through `spec.combat`.
