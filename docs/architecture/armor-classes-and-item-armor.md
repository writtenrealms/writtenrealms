# Armor Classes And Item Armor

Status: initial platform slice implemented.

This document describes the WR2 direction for world-authored armor classes,
class armor proficiencies, and item armor value suggestions. It is intended to
guide implementation before changing runtime equip behavior or item definition
schemas.

## Problem

The current equipment model still carries WR1 assumptions:

- armor classes are effectively hard-coded as `light` and `heavy`
- equip restrictions still rely on legacy archetype names such as `warrior`
- item definitions can author many combat stats, but item `armor` is not yet a
  first-class authored item-definition property
- builders do not have an item creation helper comparable to the mob-definition
  suggestion flow

This does not fit WR2 worlds where classes, attributes, and combat formulas are
manifest-authored. A world such as Phalanx should be able to define armor
categories like linen, wicker, bronze, or unarmored without teaching the engine
new constants. A class should then declare which of those categories it can use.

## Goals

- Let each world optionally define its own armor class slugs and labels.
- Let item definitions optionally declare an `armor_class` from that world.
- Let class profiles declare armor class proficiencies.
- Make the equip command enforce authored proficiencies.
- Add direct item `armor` as an authored stat, so equipment contributes to the
  existing armor mitigation pipeline.
- Add an item suggestion flow that can produce `kind: itemdefinition` YAML from
  builder inputs such as level, equipment type, and armor class.
- Keep suggestions advisory. Builders can edit the generated YAML before
  applying it.
- Preserve manifest portability where possible by using slugs instead of
  database ids.

## Non-Goals

- Do not make armor classes define combat mitigation directly.
- Do not make armor class imply armor at runtime.
- Do not make armor proficiencies a condition DSL replacement.
- Do not add arbitrary item formulas in this change.
- Do not require class-based worlds. Classless worlds should continue to work.

## Conceptual Model

There are two separate concepts:

- `armor_class`: a wearable category, such as `linen`, `wicker`, or `bronze`.
- `armor`: a numeric rating that contributes to the character's computed armor
  stat and then flows through the world's combat formula.

Armor class answers "may this character wear this item?" Armor rating answers
"how much mitigation does this item provide?"

This distinction matters because two bronze items may have different armor
ratings, and a low-level bronze cuirass should not have the same mitigation as a
high-level bronze cuirass. The class controls proficiency; the item controls
actual rating.

## World Manifest Shape

Add a world-level equipment block:

```yaml
kind: world
spec:
  equipment:
    armor_classes:
      - key: unarmored
        label: Unarmored
        description: Clothing, robes, or gear with no armor training requirement.
        armor_multiplier: 0.0
      - key: linen
        label: Linen
        description: Quilted linen, padded cloth, and linothorax-style armor.
        armor_multiplier: 1.0
      - key: wicker
        label: Wicker
        description: Wicker shields and light woven defensive gear.
        armor_multiplier: 0.85
      - key: bronze
        label: Bronze
        description: Bronze helmets, greaves, plates, and heavy shields.
        armor_multiplier: 1.35
    default_armor_class: linen
    armor_suggestions:
      full_set_scale: 0.35
      slot_weights:
        head: 0.15
        body: 0.30
        arms: 0.10
        hands: 0.10
        waist: 0.10
        legs: 0.15
        feet: 0.10
        shield: 0.35
```

`armor_classes` is optional. If it is absent or empty, the world has no authored
armor class restrictions. Items may still carry an `armor_class` string for
display or legacy content. Current compatibility behavior preserves the legacy
`heavy` armor gate for worlds that have not opted into authored armor classes.

`armor_multiplier` is suggestion-only. It affects the item suggestion service's
default armor rating. It is not read by combat at runtime and does not multiply
a builder-authored item `armor` value.

`default_armor_class` is suggestion-only. It gives the item creation flow a
default selected armor class for armor slots. An item that omits `armor_class`
should be considered unrestricted rather than silently assigned the default.

`armor_suggestions` is also advisory. It can be omitted, in which case the
backend uses conservative defaults.

## Class Profile Shape

Extend stat class profiles with optional armor proficiencies:

```yaml
kind: world
spec:
  stats:
    class_profiles:
      mystic:
        label: Mystic
        main_attribute: willpower
        armor_proficiencies: [unarmored, linen, wicker]
        attribute_weights:
          constitution: 2
          strength: 1
          dexterity: 1
          intelligence: 2
          willpower: 4
        stat_rules: []
      warlord:
        label: Warlord
        main_attribute: strength
        armor_proficiencies: [unarmored, linen, wicker, bronze]
        attribute_weights:
          constitution: 3
          strength: 4
          dexterity: 1
          intelligence: 1
          willpower: 1
```

Rules:

- If the world has no authored `equipment.armor_classes`, ignore
  `armor_proficiencies`.
- If a class profile omits `armor_proficiencies`, inherit from
  `default_profile`.
- If neither the class profile nor `default_profile` declares
  `armor_proficiencies`, treat the class as unrestricted for compatibility.
- An explicit empty list means the class is proficient with no authored armor
  classes.
- Classless worlds are unrestricted unless a later design introduces a
  world-level default player proficiency policy.

The profile normalizer should validate that every listed proficiency references
a declared `equipment.armor_classes[*].key`.

## Item Definition Shape

Add `armor` as a normal item-definition property and relax `armor_class` to a
world-authored slug:

```yaml
kind: itemdefinition
metadata:
  slug: salt-stained-linothorax
  name: a salt-stained linothorax
spec:
  type: equippable
  level: 20
  equipment_type: body
  armor_class: linen
  armor: 4
  resilience: 2
  attributes:
    constitution: 2
```

Rules:

- `armor` is an integer rating and defaults to `0`. It is the final runtime
  rating; if a builder changes generated YAML from `armor: 14` to `armor: 8`,
  the item grants 8 armor even if its armor class is heavy.
- `armor_class` is optional.
- If `armor_class` is present and the world defines armor classes, it must match
  a declared armor class key.
- If `armor_class` is absent or empty, the item has no armor class proficiency
  requirement.
- Proficiency checks apply only to armor equipment slots and shields:
  `head`, `body`, `arms`, `hands`, `waist`, `legs`, `feet`, and `shield`.
- Weapons and accessories ignore `armor_class` for equip permission unless a
  future design intentionally expands proficiency checks.

## Runtime Equip Policy

The equip command should use one shared backend policy function, not UI-only
rules. The policy should answer:

```text
can_equip(player, item) -> allowed | denied(reason, code)
```

The armor-class portion should:

1. Resolve the world equipment system from the player's world.
2. If the world has no authored armor classes, use the legacy WR1-compatible
   heavy-armor gate.
3. Return allowed if the item has no `armor_class`.
4. Return allowed if the item is not armor or shield equipment.
5. Resolve the player's class profile from `player.archetype`.
6. Return allowed if the class is unrestricted by the rules above.
7. Return allowed only when `item.armor_class` is in the class proficiency list.
8. Return a clear failure message otherwise.

Suggested failure message:

```text
You are not proficient with bronze armor.
```

The frontend should display the same policy result where possible, but the
backend equip command is authoritative.

## Armor Suggestion Model

Add an item suggestion service parallel to the existing mob-definition
suggestion service. The first version should produce deterministic
`kind: itemdefinition` YAML, not spawn items directly.

Initial endpoint inputs:

- `name`
- `slug`
- `level`
- `equipment_type`
- `armor_class`
- optional `weapon_type`
- optional intended class/profile key

For armor and shield equipment, the service suggests `armor`. For weapons, it
can also suggest `weapon_damage`, but armor is the priority of this proposal.

Suggested armor formula:

```text
level_scale = combat.level_scale(level)
full_set_armor = ceil(level_scale * armor_suggestions.full_set_scale)
slot_weight = armor_suggestions.slot_weights[equipment_type]
class_multiplier = armor_classes[armor_class].armor_multiplier
armor = ceil(full_set_armor * slot_weight * class_multiplier)
```

With the default `full_set_scale: 0.35`, the suggestion baseline intentionally
tracks the same general order of magnitude as the standard humanoid mob armor
suggestion. A full same-level armor set should be useful but not automatically
stronger than enemy defenses. A shield can add extra defense because it consumes
the offhand slot.

The returned payload should include:

```json
{
  "manifest": {},
  "yaml": "kind: itemdefinition\n...",
  "summary": {
    "level": 20,
    "equipment_type": "body",
    "armor_class": "linen",
    "suggested_armor": 4,
    "estimated_power_level": 20,
    "confidence": "medium"
  },
  "diagnostics": [
    "Generated direct item armor; builders may edit before applying."
  ]
}
```

The service must not hide derived defaults. If it suggests armor, the YAML
should include `armor: <value>`. That keeps the manifest explicit and stable
even if future world config changes alter suggestion rules.

## Builder UI

Add an item-definition suggestion flow similar to the mob-definition add flow:

1. Builder opens **World > Items > Add**.
2. UI shows a small form for name, slug, level, equipment type, and optional
   armor class.
3. Armor class options come from `world.equipment.armor_classes`.
4. Submitting calls the item suggestion endpoint.
5. The returned YAML is loaded into the existing **World > Edit World**
   manifest textarea for review and edits.
6. Applying the manifest creates the item definition through the existing
   manifest endpoint.

This keeps raw YAML as the canonical authoring step while giving builders a
useful default.

Item detail and lookup surfaces should show:

- armor class label when available
- direct armor rating
- stale/unknown armor class warnings for builders

## Data Model Changes

Recommended backend storage:

- Add `WorldConfig.equipment_system = JSONField(default=dict)`.
- Add normalization helpers in a new `core.equipment_system` module.
- Export/import it as `spec.equipment` in world manifests.
- Add `armor = models.IntegerField(default=0)` to `ItemMixin`.
- Relax `ItemMixin.armor_class` from hard-coded choices to an optional text
  field.
- Let `ItemDefinition` base properties include `armor` automatically through
  `item_definition_property_fields()`.
- Keep existing `equipment_type` constants for slot semantics. This proposal is
  about armor classes, not arbitrary equipment slots.

Existing spawned items and templates should migrate with `armor: 0`. Existing
`armor_class` values such as `light` and `heavy` can remain as strings.

## Compatibility And Migration

Implementation should avoid a sudden behavior change for legacy worlds:

1. Add the data fields and manifest schema first.
2. Keep current legacy behavior for worlds without authored
   `equipment.armor_classes`.
3. Once a world defines `equipment.armor_classes`, use the authored policy
   instead of the hard-coded heavy-armor check.
4. Convert worlds that need legacy behavior by explicitly declaring:

```yaml
equipment:
  armor_classes:
    - key: light
      label: Light
      armor_multiplier: 1.0
    - key: heavy
      label: Heavy
      armor_multiplier: 1.35
```

and class profile proficiencies that match their intended rules.

The old frontend-only heavy armor warnings should be replaced by backend policy
data. Until that is available, the frontend should avoid hard-coding
`player.archetype !== 'warrior'`.

## Implementation Path

### Phase 1: Equipment Config

- Add `core.equipment_system` defaults, normalization, and label helpers.
- Add `WorldConfig.equipment_system`.
- Add `spec.equipment` world manifest parse/export support.
- Add tests for world manifest round-trip and invalid armor class definitions.

### Phase 2: Item Armor Schema

- Add direct `armor` to `ItemMixin` and migrations for builder and spawned
  items.
- Relax `armor_class` choices to arbitrary optional strings.
- Include `armor` in item definition manifest parsing and serialization.
- Validate item-definition `armor_class` against authored world armor classes
  when present.
- Add item definition manifest tests for valid armor, missing armor, and stale
  armor class errors.

### Phase 3: Class Proficiencies

- Extend stat profile normalization with `armor_proficiencies`.
- Validate proficiency keys against the equipment system.
- Export proficiencies in world config YAML.
- Add tests for inheritance from `default_profile`, explicit empty lists, and
  unrestricted compatibility behavior.

### Phase 4: Equip Enforcement

- Replace the hard-coded heavy armor gate in `spawns/actions/items.py` with a
  shared equipment policy helper.
- Keep existing slot resolution rules for one-handed, two-handed, shield, and
  dual-wield behavior.
- Add runtime tests under `backend/wr2_tests/` for allowed and denied armor
  class equips.
- Update frontend item inspection to display policy results from backend data
  instead of hard-coded class checks.

### Phase 5: Item Suggestions

- Add `builders.balance.item_suggestions`.
- Add a builder endpoint that returns item-definition manifest YAML, summary,
  and diagnostics.
- Add the **Items > Add** form that preloads generated YAML into **Edit World**.
- Cover builder endpoint behavior with builder app tests.
- Add documentation examples to the item-definition builder guide after the
  endpoint shape settles.

### Phase 6: Power Analysis

- Extend the future item-power analyzer described in
  [mob-suggestions-and-power-budgeting.md][mob-power] so armor contributes to
  defensive score.
- Return warnings when armor is high for the item's level, slot, or armor class.
- Include min/expected/max defensive score for randomized item definitions once
  random direct stats are supported.

## Test Plan

- World manifest tests for `spec.equipment`.
- Item definition manifest tests for `armor` and arbitrary `armor_class`.
- Stat system tests for profile `armor_proficiencies`.
- Runtime equip command tests for class proficiency enforcement.
- State payload tests to ensure item armor and armor class labels reach the UI.
- Builder item suggestion endpoint tests.
- Frontend type check after adding the item suggestion form.

Project placement should follow current conventions:

- Builder endpoint and permission tests live with builder app tests.
- Runtime equip behavior tests live in `backend/wr2_tests/`.

## Open Questions

- Should `armor_proficiencies` eventually move out of `stats.class_profiles`
  into a dedicated `classes` manifest section?
- Should worlds be able to define weapon proficiencies with the same pattern?
- Should armor class labels be exposed on the lightweight world payload used by
  the game client, or only on item payloads?
- Should existing player marks such as heavy armor proficiency become authored
  effects later, or should this proposal ignore them entirely?
- Should item suggestions also choose default resilience, dodge, or health for
  armor pieces, or only direct `armor`?

[mob-power]: /Users/teebes/code/writtenrealms/docs/architecture/mob-suggestions-and-power-budgeting.md
