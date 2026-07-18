# Mob Definition Builder Guide

Mob definitions are the WR2 mob authoring path. Builders create them with YAML
in **World > Edit**. The **World > Mobs** screen lists mob definitions and can
copy or prefill the YAML for a definition.

Use `kind: mobdefinition` for one authored mob. WR2 no longer exposes the legacy
`MobTemplate` editor or manifest kind.

## Add Mob Suggestions

Use **World > Mobs > Add** to open the Add Mob modal. The form accepts name,
slug, type, level, and optional target percentages for `crit`, `resilience`,
`armor`, and `dodge`.

When a percentage is supplied, the suggestion endpoint converts it into the
direct rating needed at the entered level using the world's combat rating
curves. Empty percentage fields keep the normal suggested rating defaults. The
modal shows those defaults as same-level percentages for the selected level and
type. The generated YAML still opens in **World > Edit** for review before
applying. New suggestions explicitly use `aggression: normal`, so mobs follow
their faction relationships by default instead of remaining passive.

Standard humanoid suggestions target `8%` armor, `7%` dodge, `5%` crit, and
`3%` resilience before converting those targets into level-appropriate ratings.
Other mob types apply their type modifiers to those targets; for example,
beasts receive more natural armor and dodge but less crit and resilience.

## Plain Mobs

A mob definition with no `randomization` creates stable mobs. Mobs never stack in
the UI, but stable definition-backed mobs are still useful because existing
spawned copies can stay aligned with definition edits.

```yaml
kind: mobdefinition
metadata:
  slug: village-rat
  name: a village rat
spec:
  type: beast
  description: A lean gray rat with quick black eyes.
  room_description: A village rat searches for crumbs.
  keywords: rat village
  level: 1
  health_max: 8
  attack_power: 1
  weapon_damage: 2
  exp_worth: 1
```

Common direct mob fields include `level`, `exp_worth`, `health_max`,
`health_regen`, `energy_max`, `energy_regen`, `stamina_max`, `stamina_regen`,
`regen_rate`, `attack_power`, `weapon_damage`, `ability_power`, `armor`, `crit`, `dodge`,
`resilience`, `aggression`, `target_priority`, `fights_back`, `roam_chance`, and
`is_invisible`.

## Currency Rewards

Use a code-to-amount mapping under `rewards.currencies`; mobs do not have a
special Gold field:

```yaml
kind: mobdefinition
metadata:
  slug: village-raider
  name: a village raider
spec:
  exp_worth: 8
  rewards:
    currencies:
      obol: 3
```

Each code must belong to the base world's currency catalog and each reward must
be a positive whole number no greater than `9,007,199,254,740,991`. A kill
credits all configured currency rewards as one wallet batch. Applying
`rewards.currencies` replaces the complete currency-reward mapping for that mob
definition.

See [currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md)
for defining currencies and choosing a default.

For mobs, `weapon_damage` is an internal combat stat. It represents the mob's
weapon, claws, bite, slam, or other natural attack without requiring a spawned
weapon item. Runtime mob damage ignores equipped weapon items and uses the mob's
own `weapon_damage` plus `attack_power` scaling. If `weapon_damage` is `0`, the
combat profile's unarmed mob fallback is used instead.

## Basic Attack Messages

Use `hit_msg_first` and `hit_msg_third` to customize a mob's basic-attack verb
or verb phrase:

```yaml
kind: mobdefinition
metadata:
  slug: village-dog
  name: a village dog
spec:
  type: beast
  weapon_damage: 2
  hit_msg_first: bite
  hit_msg_third: bites
```

`hit_msg_first` is the first-person form that follows `You` and
`hit_msg_third` is the third-person form that follows an actor's name. WR2 does
not conjugate one form into the other, so author both forms. With the example
above, a player being attacked reads `A village dog bites you` followed by the
normal damage suffix.

Messages can be multiword phrases. For example, `lash at` and `lashes at`
produce messages beginning `You lash at a target` and `A tentacle lashes at a
target`. Author only the phrase: do not include the actor, target, damage
amount, or punctuation.

On a new definition, an omitted or blank field uses its corresponding `hit` or
`hits` default. When updating an existing definition, omission preserves the
current value; set the field to `""` or `null` to restore the default. These
messages customize successful basic attacks only. Ability damage, periodic
effects, and other combat output continue to use their ability or effect labels.

Use `aggression: passive`, `normal`, `players`, `all`, or `friendly`. The
alias `aggressive` is accepted for `all`.

Use `target_priority` when several hostile mobs can engage the same player from
one room and one mob should be the automatic faceoff target. The default is `0`.
Higher values are targeted first; ties keep the normal room mob order. Negative
values sit behind default-priority mobs. This is useful for encounters such as a
`target_priority: 1` shieldbearer protecting a default-priority fighter and a
`target_priority: -1` archer in the same room.

Use `roam_chance` to override the world's default ambient roam chance for that
mob. A value of `0` means "use the world default". Mobs only roam when the
spawn plan gives them a zone or path target; mobs loaded into a fixed
room stay static. If a roaming mob enters a room with an eligible player, its
normal `aggression` rules are checked just as they are when the player enters
the mob's room.

## Factions

Use `spec.factions` when a mob definition belongs to a core faction or carries
standing with reputation factions.

```yaml
kind: mobdefinition
metadata:
  slug: blackfin-raider
  name: a Blackfin raider
spec:
  type: humanoid
  aggression: normal
  factions:
    core: orc
    reputation:
      blackfin: 100
      town_watch: -50
```

`core` must reference a core faction. `reputation` keys must reference
reputation factions and values are integer standings. Spawned mobs receive
concrete assignments copied from the definition.

For faction documents and player creation policy, see
[faction-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/faction-builder-guide.md).

## Traits

Use `traits` when a mob should spawn with structured modifiers or behavior
metadata. Numeric modifiers apply immediately to spawned mobs.

```yaml
kind: mobdefinition
metadata:
  slug: crypt-brute
  name: a crypt brute
spec:
  type: humanoid
  health_max: 80
  attack_power: 12
  traits:
    - key: colossal
      modifiers:
        health_max_multiplier: 2
    - key: enraged
      modifiers:
        attack_power_multiplier: 1.5
```

For weighted spawn-plan traits and compatibility notes for the older draft name
`affixes`, see
[mob-trait-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-trait-builder-guide.md).

## Loot

Use `loot` when every spawned copy of a mob definition should be able to drop
the same items. Loot rolls when the mob dies, and successful drops are placed
inside the corpse.

Each `loot.entries` row is an independent roll. Use multiple entries when the
mob should drop one item from one group and one item from another group.

```yaml
kind: mobdefinition
metadata:
  slug: road-bandit
  name: a road bandit
spec:
  type: humanoid
  health_max: 42
  loot:
    entries:
      - slug: weapon-drop
        probability: 100
        source_pool:
          - ref: itemdefinition.rusty-sword
            weight: 3
          - ref: itemdefinition.chipped-axe
            weight: 2
          - ref: itemdefinition.hunting-knife
            weight: 2
          - ref: itemdefinition.war-club
            weight: 1
          - ref: itemdefinition.short-spear
            weight: 1

      - slug: charm-drop
        probability: 100
        source_pool:
          - ref: itemdefinition.bone-charm
            weight: 3
          - ref: itemdefinition.river-stone
            weight: 1
```

The example above drops one item from the weapon pool and one item from the
charm pool. `probability` is a percent chance for that entry to roll at all.
`weight` only chooses between options inside a pool.

A loot entry can also point at a fixed item definition or an item bundle:

```yaml
loot:
  entries:
    - slug: rat-tail
      source: itemdefinition.rat-tail
      probability: 75
      quantity:
        min: 1
        max: 2
    - slug: scrap-drop
      source: itembundle.cave-rat-scraps
      probability: 25
```

Use `conditions` for state-aware drops. Conditions use the shared WR2 condition
system.

```yaml
loot:
  entries:
    - slug: tide-pearl
      source: itemdefinition.tide-pearl
      probability: 20
      conditions:
        eq:
          - state.world.tide
          - high
```

## Fixed Attribute Mobs

Use `attributes` for world-defined attributes such as `strength`,
`dexterity`, `intelligence`, or `constitution`.

```yaml
kind: mobdefinition
metadata:
  slug: road-bandit
  name: a road bandit
spec:
  type: humanoid
  description: A wary thief in patched leather armor.
  room_description: A road bandit watches for easy prey.
  keywords: road bandit thief
  level: 5
  health_max: 42
  attack_power: 7
  armor: 3
  attributes:
    strength: 2
    dexterity: 1
```

If the world does not define an attribute, that key contributes nothing to
combat. This is intentional: mob YAML should not make the world fail to boot
because an attribute was renamed or removed.

Direct stats and attributes compound. If the world maps `strength` to
`attack_power`, then a mob with both `attack_power: 7` and `strength: 2` spawns
with the direct attack power plus the attack power produced by `strength`.

## Power Analysis

The mob definition details screen has a **POWER** action. It opens an advisory
analysis modal for the current mob definition.

The analysis uses the world's combat formulas, rating curves, level, type,
fixed direct stats, and fixed attributes. It reports category scores, the
strongest stat drivers, basic attack output, mitigation, effective health, and
a same-type reference score for the mob's level.

Power analysis does not change mob stats or runtime combat. It is a builder aid
for comparing definitions before applying further YAML edits. Randomized
attribute ranges are not included in the first pass; the modal analyzes the
fixed values on the definition.

## Random Attribute Mobs

Add `randomization.attributes` when each spawned copy should roll different
attribute values.

```yaml
kind: mobdefinition
metadata:
  slug: raider
  name: a raider
spec:
  type: humanoid
  description: A scarred raider carrying stolen gear.
  room_description: A raider prowls here.
  keywords: raider bandit
  level: 7
  health_max: 55
  attack_power: 9
  attributes:
    strength: 1
    constitution: 1
  randomization:
    attributes:
      - key: strength
        min: 1
        max: 4
        mode: favor_high
      - key: constitution
        min: 0
        max: 3
        mode: uniform
```

The fixed `attributes` value is added to the roll. In the example above,
`strength` is always at least `2`: fixed `1` plus a random `1-4`.

Supported randomization modes:

- `uniform`: every value in the range is equally likely.
- `favor_low`: lower values are more likely.
- `favor_high`: higher values are more likely.

Use `curve` to make `favor_low` or `favor_high` stronger. `curve: 1.0` is the
default. Higher values make the favored side more likely.

```yaml
randomization:
  attributes:
    - key: intelligence
      min: 1
      max: 10
      mode: favor_high
      curve: 1.5
```

## Definition Edits

Stable definition-backed mobs are meant to behave like authored copies of the
definition. When a stable mob definition is edited, existing spawned mobs from
that definition are resynced to the new definition values.

Randomized mobs keep their rolled attributes. They still receive current
authored properties such as name, descriptions, level, and combat stats when the
definition changes.

## Loading Mobs

Spawn plans load mob definitions directly through `kind: spawnplan` manifests.
The reference key shape is `mobdefinition.<slug>` or `mobdefinition.<id>`, and
mob definitions can also be resolved by slug in the builder load path.

For direct builder testing, use `/load mob`:

```text
/load mob road-bandit
```

When a builder player runs that command directly, the mob is spawned in the
builder's current room. The selector can be a mob definition id or mob
definition slug.

Room and mob scripts can also use `/load mob` through `/cmd`:

```yaml
script: /cmd room -- /load mob road-bandit
```

This spawns the mob in the room issuing the command. If a mob issues
`/load mob ...` from an internal script context, the new mob is spawned in the
issuer mob's current room. Room and mob issuers cannot use `/load` from normal
player-facing command input; it is script-source gated.

World export includes mob definitions as `kind: mobdefinition` documents, so a
definition authored in one world can be copied into another world through
**World > Edit**.

## Ability Trainers

A mob definition can teach abilities by adding a `trainer` block. Trainer
abilities are stored by ability slug so exported worlds remain portable.

```yaml
kind: mobdefinition
metadata:
  slug: arms-trainer
  name: an arms trainer
spec:
  type: humanoid
  keywords: trainer arms
  trainer:
    availability: present
    abilities:
      - power-strike
      - shield-slam
```

Once any trainer in the world offers an ability, `learn <ability>` and
`unlearn <ability>` require an eligible spawned trainer in the player's current
room. Use `availability: alive_and_present` when a pending-deletion or defeated
trainer should not teach.

## Combat Ability Loadouts

Mobs can use authored abilities in combat by referencing existing ability
slugs from `combat.abilities`. Define the ability as `kind: ability` first, then
attach it to one or more mob definitions. Do not inline full ability bodies
inside the mob definition; reusable ability definitions are the canonical
builder-facing shape.

```yaml
kind: ability
metadata:
  slug: shadow-bolt
  name: Shadow Bolt
spec:
  command:
    verbs: [shadowbolt]
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 2
  components:
    - type: damage
      profile: basic_ability
      overrides:
        multiplier: 1.4
      text:
        label: Shadow Bolt
---
kind: mobdefinition
metadata:
  slug: cave-shaman
  name: a cave shaman
spec:
  type: humanoid
  health_max: 80
  ability_power: 12
  combat:
    attackable: true
    abilities:
      - ability: shadow-bolt
        weight: 3
        chance: 25
```

Each loadout entry supports:

- `ability`: required ability slug, id, or `ability.<id>` key.
- `chance`: optional integer from `0` to `100`, defaulting to `100`. Each
  otherwise-eligible ability must pass this percentage roll before selection.
- `weight`: optional positive integer. Higher weights make the ability more
  likely when multiple entries pass their chance rolls.
- `when`: optional WR2 condition DSL gate. Use this for health thresholds,
  phases, room state, or world state.

Cooldown, resource cost, and `when` eligibility are checked before `chance`.
Chance is rolled independently for each remaining entry, then `weight` chooses
among the entries that passed. If none pass, the mob uses its normal basic
attack. A failed chance roll does not start the ability cooldown, so the mob can
try again on its next turn.

Example self-heal that only becomes eligible when the mob is wounded:

```yaml
combat:
  abilities:
    - ability: mend-self
      weight: 1
      when:
        lte:
          - actor.health_percent
          - 40
```

Mob abilities use the same round-based cast time and cooldown fields as player
abilities. If no loadout entry is eligible, the mob falls back to its normal
basic attack. Current mob loadout execution supports damage, healing, and active
effects such as stun, DOT, HOT, resource ticks, and barriers. DOTs, HOTs, and
other `scope: character` effects can target either players or mobs and survive
fleeing; `scope: encounter` effects end with the fight. A mob's lingering DOT
can therefore defeat a player after the player escapes, using the mob as the
recorded damage source. Character state components and room-wide player buff
components are still ignored for mob actors.

## Transition Notes

Mob reactions, quest NPC bindings, and spawn plans target `MobDefinition`. WR2
no longer has `MobTemplate`, legacy loader rules, or mob-template-only builder
surfaces.
