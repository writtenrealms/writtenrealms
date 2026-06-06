# WR2 Mob Suggestions and Power Budgeting

## Purpose

Builders need a fast way to create reasonably balanced mobs without maintaining
external spreadsheets. A mob's level should provide an authoring baseline: if a
builder says "this is a level 6 beast", the builder UI should be able to produce
editable `kind: mobdefinition` YAML with plausible direct combat stats.

This document describes a WR2 balance-assistance layer for:

- prefilled mob definition YAML from a small Add Mob form
- explainable suggested stats based on the current world's combat/stat settings
- reusable power analysis that can later classify item strength

This is an authoring tool. It should help builders make content faster, but it
should not turn level into a hidden combat rule that overrides authored stats at
runtime.

## Related Documents

- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [combat-resolution-formulas.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-resolution-formulas.md)
- [guided-random-item-definitions.md](/Users/teebes/code/writtenrealms/docs/architecture/guided-random-item-definitions.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [mob-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-definition-builder-guide.md)
- `.codex/skills/wr-transition/wr2-architecture.md`

## Current Baseline

WR2 combat already has the important runtime pieces:

- `WorldConfig.combat_system` defines combat profiles, rating curves, and
  `level_scale`.
- `WorldConfig.stat_system` defines world-authored attributes and formulas that
  derive canonical stats.
- `MobDefinition` is the clean WR2 mob authoring path.
- `kind: mobdefinition` manifests can create and update mob definitions.
- Mobs persist direct canonical stats such as `health_max`, `attack_power`,
  `ability_power`, `armor`, `crit`, `dodge`, and `resilience`.

The missing piece is builder assistance.

The old `core.utils.mobs.suggest_stats` helper and `MobTemplateSerializer`
creation path can suggest legacy mob-template stats, but that path is not the
right long-term WR2 surface. It is tied to `MobTemplate`, old defaults, and old
assumptions. The new feature should target `MobDefinition` and the YAML
manifest flow.

The current frontend `Mobs > Add` path simply opens **World > Edit** with a
static `newMobDefinitionYaml` template. That is useful as a placeholder, but it
does not reflect the mob's intended level, type, or world balance settings.

## Problem

Without a balancing helper, builders must:

- maintain their own level-to-stat tables outside Written Realms
- manually translate those tables into every mob definition
- reverse-engineer how combat profiles and rating curves affect outcomes
- guess whether a mob's stats are appropriate for its level
- repeat that same work later for item stat budgets

This slows world creation and makes balancing inconsistent. It also makes WR2's
flexible stat/combat systems harder to use because the engine exposes the knobs
but does not yet provide guardrails.

## Design Goals

- Make common mob creation fast from **Mobs > Add**.
- Generate editable YAML, not hidden persisted data.
- Prefer canonical direct mob stats over generated attributes.
- Use the world's existing combat/stat configuration where practical.
- Keep suggestions deterministic and explainable.
- Reuse the same balance analysis concepts for future item power classification.
- Keep the first version small enough to implement without inventing a full
  procedural encounter system.

## Non-Goals

- No automatic runtime stat scaling every time combat starts.
- No hidden level-difference bonus or penalty in combat.
- No requirement that all worlds define attributes or classes.
- No arbitrary builder-authored formula language for the suggestion engine.
- No attempt to perfectly solve encounter balance across every possible ability,
  item, party, or build.
- No immediate replacement for all legacy `MobTemplate` behavior.

## Recommendation

Create a WR2 balance-assistance service that can:

1. Suggest a `mobdefinition` manifest from a small set of builder inputs.
2. Analyze a proposed mob or item and return an approximate power band.

The first user-facing feature should be mob suggestions. The item-power work
should share the same service concepts, but it can land later.

The suggestion service should produce canonical direct stats in
`spec`, for example:

```yaml
kind: mobdefinition
metadata:
  slug: cave-wolf
  name: a cave wolf
spec:
  type: beast
  description: ''
  room_description: A cave wolf is here.
  keywords: cave wolf
  level: 4
  exp_worth: 18
  gold: 0
  health_max: 46
  health_regen: 0
  energy_max: 0
  energy_regen: 0
  stamina_max: 0
  stamina_regen: 0
  regen_rate: 4
  attack_power: 8
  ability_power: 0
  armor: 2
  dodge: 2
  crit: 1
  resilience: 0
  fights_back: true
  is_invisible: false
  attributes: {}
  randomization:
    attributes: []
```

Builders remain free to change any number before applying the manifest.

## Builder UX

### Add Mob Flow

The **Mobs > Add** screen should become a small form instead of a direct link to
static YAML.

Initial fields:

- mob name
- mob slug
- mob type
- mob level

Optional later fields:

- role: standard, skirmisher, brute, caster, defender
- rank: weak, standard, elite, boss
- reward profile: none, poor, normal, rich
- starting behavior: fights back, passive, merchant, invisible

On submit, the frontend calls the backend suggestion endpoint. The backend
returns YAML plus a compact analysis payload. The frontend then opens
**World > Edit** with the returned YAML prefilled.

This keeps the existing manifest review/apply workflow intact:

1. Builder enters the small creation form.
2. Backend suggests a manifest.
3. Builder reviews and edits YAML in **World > Edit**.
4. Existing manifest apply creates the `MobDefinition`.

The Add form should not create a mob definition directly in the first version.
The review step is important because the output is advisory.

### Preview Data

The endpoint should return more than YAML. It should also return enough data for
the UI to show a short preview later:

```json
{
  "summary": {
    "level": 4,
    "type": "beast",
    "role": "standard",
    "estimated_power_level": 4,
    "confidence": "medium"
  },
  "suggested_stats": {
    "health_max": 46,
    "attack_power": 8,
    "armor": 2,
    "crit": 1,
    "dodge": 2,
    "resilience": 0
  },
  "combat_preview": {
    "basic_attack_damage": 4,
    "same_level_armor_mitigation": 0.005,
    "same_level_dodge_chance": 0.025,
    "same_level_crit_chance": 0.021
  },
  "diagnostics": [
    "Generated direct stats only; world attributes were not required.",
    "Using default beast type modifiers."
  ]
}
```

The first UI pass may ignore most of this payload. The backend should still
return it so the design can evolve without changing the service contract
immediately.

## Backend Shape

### Suggestion Endpoint

Add a non-persistent builder endpoint. Exact URL shape can follow existing
builder conventions, but a balance namespace keeps the feature from looking
like a CRUD operation:

```text
POST /builder/worlds/<world_id>/balance/mob-suggestions/
```

Request:

```json
{
  "name": "a cave wolf",
  "slug": "cave-wolf",
  "type": "beast",
  "level": 4
}
```

Response:

```json
{
  "manifest": {
    "kind": "mobdefinition",
    "metadata": {
      "slug": "cave-wolf",
      "name": "a cave wolf"
    },
    "spec": {
      "type": "beast",
      "level": 4,
      "health_max": 46,
      "attack_power": 8
    }
  },
  "yaml": "kind: mobdefinition\nmetadata:\n  slug: cave-wolf\n...",
  "summary": {},
  "suggested_stats": {},
  "combat_preview": {},
  "diagnostics": []
}
```

The endpoint should:

- require normal world builder permissions
- validate level against the world's leveling config
- validate mob type against supported mob types
- normalize slug the same way mob-definition manifests do
- never create or update a database row
- reuse manifest serialization helpers where possible so returned YAML matches
  the apply path

### Service Module

Put the core logic in a plain service module, not the view:

```text
backend/builders/balance/
  __init__.py
  mob_suggestions.py
  power_analysis.py
```

The suggested public functions:

```python
suggest_mob_definition_manifest(world, *, name, slug, mob_type, level, options=None)
analyze_combatant_power(world, *, stats, level=None, combat_profile=None)
analyze_item_power(world, *, item_stats, equipment_type=None, level=None)
```

Only the first function needs to exist in the initial implementation. The other
two are the intended extension points and should guide the first design.

## Stat Suggestion Model

### Canonical Stats First

The generated mob should primarily use direct canonical stats:

- `health_max`
- `health_regen`
- `energy_max`
- `energy_regen`
- `stamina_max`
- `stamina_regen`
- `attack_power`
- `ability_power`
- `armor`
- `crit`
- `dodge`
- `resilience`

The generated manifest should include `attributes: {}` by default.

This keeps suggested mobs:

- easy to inspect
- independent of attribute naming changes
- stable when a world's attribute formulas evolve
- compatible with worlds that have no authored attributes

World-authored attributes still matter. They should be used for analysis and
diagnostics, and builders can add them manually. The suggestion engine should
not default to emitting attributes unless we add an explicit option such as
`stat_source: attributes`.

### Level As Authoring Input

The mob's level should drive suggested values at creation time. After the
builder applies the manifest, the persisted direct stats are the source of
truth.

Changing a mob definition from level 4 to level 8 later should not silently
rewrite its stats unless the builder explicitly asks to regenerate or rebudget
the mob.

This preserves the current WR2 runtime model:

- level is part of combat snapshots
- level affects rating math and unarmed mob fallback damage
- direct stats are still what actually make a mob strong or weak

### Starting Heuristic

The first version can use a deterministic heuristic built around
`combat.level_scale`.

Inputs:

- normalized level
- normalized mob type
- world combat system
- world stat system
- default role: `standard`
- default rank: `normal`

Outputs:

- target survivability budget
- target throughput budget
- rating budget
- reward estimate

The simplest model:

```text
scale = combat_level_scale(level)

health_budget = scale * health_multiplier
damage_budget = scale * damage_multiplier
rating_budget = scale * rating_multiplier
```

Then apply type and rank modifiers:

```text
beast:
  health: 1.10
  attack_power: 1.10
  armor: 0.75
  resilience: 0.50
  dodge: 1.10

humanoid:
  health: 1.00
  attack_power: 1.00
  armor: 1.00
  resilience: 1.00
  dodge: 1.00

undead:
  health: 1.15
  attack_power: 0.95
  armor: 1.05
  resilience: 1.10
  dodge: 0.75
```

These values are examples, not final constants. The first implementation should
keep them conservative and easy to change.

### Combat-Aware Calibration

The service should not merely multiply stats by level. It should use the same
combat formula concepts that runtime combat uses.

At minimum, analysis should calculate:

- expected basic attack base damage
- expected mitigation from armor and resilience against a same-level opponent
- dodge chance against a same-level opponent
- crit chance against a same-level opponent

Later, the service can use deterministic simulations to solve for targets such
as:

- average rounds for a same-level baseline player to defeat the mob
- average rounds for the mob to defeat a same-level baseline player
- expected damage per round with and without variance

Simulation should be optional. The first version can stay analytic and cheap.

### Baseline Player Problem

Different worlds may have different classes, formulas, and starting gear. There
may not be one true same-level player.

The first version should use a neutral baseline:

- derive player stats from the world's default or first class profile if one
  exists
- include no gear unless the world later defines balance baseline gear
- fall back to combat-system defaults when no stat system exists

The endpoint should include diagnostics when confidence is lower:

- no stat formulas found
- no class profiles found
- no health formula found
- no attack-power formula found
- generated from default balance coefficients only

This avoids pretending the suggestion is exact.

## Rewards

Suggested `exp_worth` should come from a separate reward heuristic, not from
combat damage math directly.

Initial behavior:

- use existing WR1-style `MOB_EXP` values when the level is in range
- fall back to a simple level-scaled value when out of range or when world
  leveling config differs

Future behavior should let worlds configure reward curves in manifests.

Gold should default by mob type:

- humanoids may receive a small level-scaled gold suggestion
- beasts, undead, constructs, and similar types default to `0`
- merchants and special NPCs should be explicit, not inferred

## Optional World Balance Configuration

The first implementation can use code defaults. Once the feature proves useful,
worlds should be able to tune the balance helper in the world manifest.

Possible future manifest shape:

```yaml
kind: world
spec:
  balance:
    version: 1
    baseline:
      standard_mob_rounds_to_kill: 4
      standard_mob_rounds_to_kill_player: 10
    mob_types:
      beast:
        health: 1.1
        attack_power: 1.1
        armor: 0.75
        dodge: 1.1
      humanoid:
        health: 1.0
        attack_power: 1.0
        armor: 1.0
        dodge: 1.0
    ranks:
      weak:
        health: 0.65
        attack_power: 0.75
        exp_worth: 0.6
      elite:
        health: 2.5
        attack_power: 1.35
        exp_worth: 2.5
```

This config should remain advisory. It should affect suggestions and power
analysis, not runtime combat resolution.

## Item Power Direction

The same balance service should eventually classify items.

Builder questions to answer:

- Is this weapon roughly appropriate for level 3, 8, or 15?
- Is this armor piece over-budget for its intended level?
- How much stronger is this item than a normal item of the same level?
- Which stat is causing the budget spike?

An item-power analyzer should return:

```json
{
  "estimated_power_level": 7,
  "budget_score": 123.4,
  "offense_score": 86.0,
  "defense_score": 28.0,
  "utility_score": 9.4,
  "drivers": [
    {"stat": "weapon_damage", "score": 61.0},
    {"stat": "crit", "score": 14.0},
    {"stat": "attack_power", "score": 11.0}
  ],
  "warnings": [
    "weapon_damage is high for a one-handed weapon at level 4"
  ]
}
```

This is the WR2 version of WR1's useful item-budget idea. The exact weights do
not need to match WR1. The important design principle is the same: give builders
feedback about relative power before content reaches players.

### Item Budget Inputs

Item analysis should consider:

- equipment type and slot
- weapon damage
- attack power
- ability power
- armor
- crit
- dodge
- resilience
- health/resource bonuses
- world combat rating curves
- item randomization ranges

For guided-random item definitions, analysis should return at least:

- minimum power
- average/expected power
- maximum power

This lets builders see when a random item range can produce outliers.

## Power Analysis Model

Power analysis should be directional, not absolute truth.

Use separate categories:

- offense: weapon damage, attack power, ability power, crit
- defense: health, armor, resilience, dodge
- sustain: regen and resource bonuses
- utility: future effects, cooldown changes, special flags

Convert each category into a normalized score, then compare that score to a
level budget curve. The inverse of that curve gives an estimated power level.

This gives useful builder feedback even when exact combat simulation is
impossible.

## Implementation Phases

### Phase 1: Backend Mob Suggestion Service

- Add a plain service that suggests direct mob stats.
- Add a non-persistent builder endpoint.
- Return manifest object, YAML, stats, preview, and diagnostics.
- Add WR2 tests under `backend/wr2_tests/`.

### Phase 2: Mobs Add Flow

- Replace the current static Add behavior with the small Add Mob form.
- Call the suggestion endpoint.
- Route to **World > Edit** with returned YAML.
- Keep existing manifest apply as the creation step.

### Phase 3: Preview and Regeneration

- Show compact preview data before opening YAML or beside the YAML editor.
- Add a "Regenerate stats from level" action for existing mob definitions.
- Require explicit confirmation before replacing existing stat fields.

### Phase 4: Item Power Analysis

- Add an item analyzer using the same balance service.
- Surface estimated power level in item definition editing/listing.
- Include min/expected/max analysis for guided-random item definitions.

### Phase 5: World-Tunable Balance Config

- Add optional `spec.balance` to world manifests.
- Let builders tune type/rank multipliers and target encounter pacing.
- Keep defaults available for worlds that do not care.

## Testing Strategy

Backend tests should cover:

- suggestion endpoint permission checks
- level validation against world leveling config
- type validation
- deterministic output for the same input
- direct canonical stats are emitted by default
- generated YAML can be applied through the existing manifest endpoint
- diagnostics are returned when world stat config is sparse
- type modifiers affect output in expected directions

Power-analysis tests should cover:

- weapon damage increases offensive score
- armor/resilience/health increase defensive score
- same item stats produce stable estimated power levels
- guided-random ranges return min/expected/max power

Frontend tests, where practical, should cover:

- Add Mob form validates required fields
- successful suggestion opens **World > Edit** with returned YAML
- failed suggestion shows the backend validation error

## Open Questions

- Should the first Add form include `role` or keep it to name, slug, type, and
  level only?
- Should suggested mobs default to `energy_max: 0` and `stamina_max: 0`, or keep
  current static YAML's small resource values?
- Should `exp_worth` initially use old `MOB_EXP`, world leveling config, or a
  new reward curve?
- Should balance config live on `WorldConfig` immediately, or wait until after
  hard-coded defaults prove useful?
- How much preview information should be visible in the first UI slice?

## Key Constraint

This feature should not change combat semantics.

The runtime should continue to resolve combat from persisted stats, equipment,
combat profiles, and rating curves. Level can inform authoring suggestions and
rating math, but a mob should not become stronger merely because a hidden system
recomputed its stats during combat.
