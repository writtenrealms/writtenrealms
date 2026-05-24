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
  exp_worth: 1
```

Common direct mob fields include `level`, `exp_worth`, `gold`, `health_max`,
`health_regen`, `energy_max`, `energy_regen`, `stamina_max`, `stamina_regen`,
`regen_rate`, `attack_power`, `ability_power`, `armor`, `crit`, `dodge`,
`resilience`, `fights_back`, and `is_invisible`.

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

World export includes mob definitions as `kind: mobdefinition` documents, so a
definition authored in one world can be copied into another world through
**World > Edit**.

## Transition Notes

`MobDefinition` is a transition name while WR2 still has the older
`MobTemplate` model. The long-term direction is to remove the old template path
and let the clean definition model become the normal authored mob concept.

For now, legacy mob-template-only surfaces such as reactions, quest NPC
bindings, equipment profiles, merchant setup, and template inventory still live
under `/mobtemplates`. Use mob definitions for new plain, fixed-stat, and
randomized mobs; use the legacy URL-only template editor only when you need one
of those older surfaces.
