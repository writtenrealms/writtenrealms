# Phalanx Setup

This local guide configures Phalanx (`world.19`) as a WR1-like WR2 world with
Phalanx-specific classes and resource naming:

- `constitution`
- `strength`
- `dexterity`
- `intelligence`
- `willpower`
- `energy` labeled as `Ichor`
- `ability_power` labeled as `Spell Power`
- class profiles for Hoplite, Warlord, Tidecaller, Assassin, and Mystic
- all new characters start as Hoplites; class selection is disabled
- Hoplites have no Ichor, so the game UI does not show the Ichor resource
- builders can use `/setclass` to move a player into another class later
- dodge, crit, armor, and resilience authored as percentage points
- three starter combat abilities:
  - `bash`: stun a hostile target for 2 rounds
  - `burn`: apply a 3-round damage-over-time effect that ticks for 2x basic
    ability damage each round
  - `slice`: deal 1.5x weapon damage

In this setup, `dodge: 1` means 1% dodge, `crit: 1` means 1% crit, `armor: 1`
means 1% physical mitigation, and `resilience: 1` means 1% ability mitigation.
Those percentages do not change based on opponent level.

## Step 1: Open Phalanx

Open **World > Edit World** for Phalanx.

The examples below are designed to be pasted into that page.

## Step 2: Add Phalanx Stats And Classes

Paste this world manifest and apply it:

```yaml
kind: world
metadata:
  world: world.19
spec:
  ability_progression:
    max_known: 8

  default_gender: male

  stats:
    attributes:
      - key: constitution
        label: Constitution
      - key: strength
        label: Strength
      - key: dexterity
        label: Dexterity
      - key: intelligence
        label: Intelligence
      - key: willpower
        label: Willpower

    labels:
      resources:
        health: Health
        energy: Ichor
        stamina: Stamina
      stats:
        attack_power: Attack Power
        ability_power: Spell Power
        armor: Armor
        crit: Crit
        dodge: Dodge
        resilience: Resilience
        health_regen: Health Regen
        energy_regen: Ichor Regen
        stamina_regen: Stamina Regen
      classes:
        hoplite: Hoplite
        warlord: Warlord
        tidecaller: Tidecaller
        assassin: Assassin
        mystic: Mystic

    stat_display_order:
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
      attribute_weights:
        constitution: 3
        strength: 2
        dexterity: 2
        intelligence: 2
        willpower: 1
      stat_rules: []

    class_profiles:
      hoplite:
        label: Hoplite
        main_attribute: constitution
        attribute_weights:
          constitution: 4
          strength: 3
          dexterity: 3
          intelligence: 0
          willpower: 0
        stat_rules: []

      warlord:
        label: Warlord
        main_attribute: strength
        attribute_weights:
          constitution: 3
          strength: 4
          dexterity: 1
          intelligence: 1
          willpower: 1
        stat_rules:
          - source: strength
            target: crit
            multiplier: 1

      tidecaller:
        label: Tidecaller
        main_attribute: intelligence
        attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 1
          intelligence: 4
          willpower: 1
        stat_rules: []

      assassin:
        label: Assassin
        main_attribute: dexterity
        attribute_weights:
          constitution: 3
          strength: 1
          dexterity: 4
          intelligence: 1
          willpower: 1
        stat_rules:
          - source: dexterity
            target: attack_power
            multiplier: 1

      mystic:
        label: Mystic
        main_attribute: willpower
        attribute_weights:
          constitution: 2
          strength: 1
          dexterity: 1
          intelligence: 2
          willpower: 4
        stat_rules: []

    class_selection:
      enabled: false
      default: hoplite

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
        - source: willpower
          target: resilience
          multiplier: 2
          mode: total
        - source: willpower
          target: energy_regen
          multiplier: 1
          mode: total

      two_handed_multipliers:
        attack_power: 1.5
        ability_power: 1.5

      mob_boost:
        slot_factor: 10.25
        elite_multiplier: 1.2
        armor_multiplier_by_profile:
          hoplite: 3
          warlord: 3
          default: 2

  combat:
    level_scale:
      type: exponential
      base: 5.5
      growth: 1.1
    ratings:
      dodge:
        stat: dodge
        type: percentage_points
        base: 0
        cap: 0.75
      crit:
        stat: crit
        type: percentage_points
        base: 0
        cap: 1.0
      armor:
        stat: armor
        type: percentage_points
        base: 0
        cap: 0.75
      resilience:
        stat: resilience
        type: percentage_points
        base: 0
        cap: 0.75
```

Notes:

- `energy` is the WR2 engine resource. The label makes players see `Ichor`.
- `ability_power` is the WR2 engine stat. The label makes players see
  `Spell Power`.
- Character creation still lets players select gender, but the default is
  `Male`.
- `class_selection.enabled: false` locks character creation to the configured
  `default` class, so new Phalanx characters start as Hoplites.
- Hoplites have `intelligence: 0` and `willpower: 0`, so their computed
  `energy_max` is `0`. The UI hides Ichor when `energy_max` is `0`.
- Warlords, Tidecallers, Assassins, and Mystics have Ichor because they gain
  intelligence and willpower.
- Willpower gives `+2 resilience` and `+1 Ichor Regen` per point.
- Warlord, Tidecaller, and Mystic are the renamed Warrior, Mage, and Cleric
  profiles. Mystic uses the requested Cleric-derived weights with
  `willpower: 4`.
- The four rating configs use `percentage_points`, so dodge, crit, armor, and
  resilience do not scale against opponent level.

## Step 3: Add A Starter Weapon

This weapon gives `slice` something to scale from and demonstrates item
definition attributes.

Paste and apply:

```yaml
kind: itemdefinition
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
  attributes:
    strength: 2
    dexterity: 1
```

## Step 4: Add A Sparring Mob

Add a simple mob definition so the abilities have a target:

```yaml
kind: mobdefinition
metadata:
  slug: sparring-opponent
  name: a sparring opponent
spec:
  level: 1
  keywords: opponent sparring
  description: A sparring opponent watches for an opening.
  room_description: A sparring opponent waits here.
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
/load mob sparring-opponent
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
- `burn` costs Ichor, so a starting Hoplite cannot cast it until their class is
  changed with `/setclass`.
- `burn` uses `basic_ability`, so each tick scales from `Spell Power` through
  the basic ability profile. The `multiplier: 2` makes each tick 2x that
  profile's normal output.
- `slice` overrides the physical profile so it uses weapon damage only:
  `weapon_damage * 1.5`. It intentionally sets `power_scale`,
  `unarmed_power_scale`, and `mob_unarmed_level_scale` to `0` so it is not also
  scaling from attack power.

## Step 6: Create A Character

Create a new character in Phalanx.

The class selector is disabled. The new character starts as a Hoplite.

After entering the game, use the stats command:

```text
stats
```

The Hoplite stats should show no Ichor resource. To test a class with Ichor,
use the builder command:

```text
/setclass tidecaller
```

Then run:

```text
stats
```

You should see `Ichor` instead of `Energy`, and `Spell Power` instead of
`Ability Power`.

Builders can also target a player in the current room:

```text
/setclass <player> mystic
```

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
those attributes should flow through the world formulas into stats such as
attack power, crit, dodge, and health. In this setup, the added dodge and crit
values are percentage points.

## Step 8: Learn The Abilities

Use the player ability commands:

```text
learn bash
learn burn
learn slice
```

The default known ability cap is 8, so all three demo abilities fit.

## Step 9: Test Combat

Load or place the sparring opponent:

```text
/load mob sparring-opponent
```

Then try:

```text
bash <mob>
slice <mob>
```

To test the Ichor-cost ability, first move into an Ichor-using class:

```text
/setclass tidecaller
burn <mob>
```

Expected behavior:

- `bash` should suppress the target's primary action while the stun is active.
- `burn` should apply a 3-round DOT. Each tick uses the basic ability profile
  and a `2x` multiplier, so Tidecallers and Mystics should generally get better
  burn ticks from higher intelligence, willpower, and Spell Power.
- `slice` should depend on the equipped weapon's `weapon_damage`. With the
  training sword, the pre-mitigation base should be `8 * 1.5 = 12` before
  variance, crit, mitigation, and minimum damage.
- Dodge and crit chances should be read directly as percentage points from
  their final stat values.

## What To Tune First

If the world feels too lethal or too slow, start with these values:

- `weapon_damage` on starter weapons
- `basic_physical.power_scale` and `basic_ability.power_scale` in
  `spec.combat.profiles`
- class `attribute_weights`
- DOT `multiplier`
- stun `duration.rounds`
- percentage-point caps under `spec.combat.ratings`
- `combat.level_scale` if high-level mobs are too weak or too strong

Keep the stat inputs and combat outputs separate. Builders should tune
`strength`, `dexterity`, `constitution`, `intelligence`, and `willpower`
through `spec.stats`, and tune hit resolution through `spec.combat`.
