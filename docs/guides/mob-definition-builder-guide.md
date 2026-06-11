# Mob Definition Builder Guide

Mob definitions are the WR2 mob authoring path. Builders create them with YAML
in **World > Edit**. The **World > Mobs** screen lists mob definitions and can
copy or prefill the YAML for a definition.

Use `kind: mobdefinition` for one authored mob. The legacy `MobTemplate` editor
still exists at `/mobtemplates` for old content and specialized legacy surfaces,
but new mob authoring should start here.

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

Common direct mob fields include `level`, `exp_worth`, `gold`, `health_max`,
`health_regen`, `energy_max`, `energy_regen`, `stamina_max`, `stamina_regen`,
`regen_rate`, `attack_power`, `weapon_damage`, `ability_power`, `armor`, `crit`, `dodge`,
`resilience`, `aggression`, `fights_back`, and `is_invisible`.

For mobs, `weapon_damage` is an internal combat stat. It represents the mob's
weapon, claws, bite, slam, or other natural attack without requiring a spawned
weapon item. Runtime mob damage ignores equipped weapon items and uses the mob's
own `weapon_damage` plus `attack_power` scaling. If `weapon_damage` is `0`, the
combat profile's unarmed mob fallback is used instead.

Use `aggression: passive`, `normal`, `players`, `all`, or `friendly`. The
alias `aggressive` is accepted for `all`.

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

The loader rule UI can load mob definitions directly. The reference key shape is
`mob_definition.<id>`, and mob definitions can also be resolved by slug in the
builder load path.

For direct builder testing, use `/load mob`:

```text
/load mob road-bandit
```

When a builder player runs that command directly, the mob is spawned in the
builder's current room. The selector can be a mob template id, a mob template
slug, a mob definition id, or a mob definition slug.

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

## Transition Notes

`MobDefinition` is a transition name while WR2 still has the older
`MobTemplate` model. The long-term direction is to remove the old template path
and let the clean definition model become the normal authored mob concept.

For now, legacy mob-template-only surfaces such as reactions, quest NPC
bindings, equipment profiles, merchant setup, and template inventory still live
under `/mobtemplates`. Use mob definitions for new plain, fixed-stat, and
randomized mobs; use the legacy URL-only template editor only when you need one
of those older surfaces.
