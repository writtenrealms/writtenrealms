# WR2 Stats, Formulas, and Classes

## Purpose

This document describes the intended WR2 direction for character attributes,
stats, formulas, and class-like progression.

The goal is to keep the system flexible enough for different world genres and
builder preferences without turning the runtime contract into a fully dynamic
"anything can mean anything" engine.

This is the WR2 direction implemented by the world `stats` configuration:
worlds start with no authored attributes, and builders opt into only the
attributes their world needs.

Reference docs:

- `.codex/skills/wr-transition/wr2-architecture.md`
- `docs/architecture/combat-encounter-model.md`
- `docs/architecture/yaml-manifest-system.md`

## Current Problem

WR1 hard-coded several assumptions into the engine:

- four attributes:
  - constitution
  - strength
  - dexterity
  - intelligence
- several stats such as:
  - attack power
  - spell power
  - armor
  - crit
  - resilience
  - health
  - mana
  - stamina
- archetype-specific formulas for how attributes map into stats

That worked for WR1, but it creates problems for WR2:

- the vocabulary is fantasy-specific in places
- the engine contract is tightly coupled to one game style
- classes and formulas are harder to evolve cleanly
- builders who want different themes or stat models are forced to work around
  names they may not want
- making everything fully dynamic would solve the naming problem in the wrong
  way and create a much harder runtime to reason about

WR2 should not simply freeze WR1's assumptions in place, but it also should not
replace them with an infinitely malleable stat soup.

## Design Goals

- Keep the runtime combat contract stable and predictable.
- Let worlds define their own authored attributes.
- Let worlds choose class-based or classless progression.
- Remove fantasy-specific naming from canonical engine internals.
- Allow world-specific player-facing labels without changing engine semantics.
- Keep formula evaluation deterministic, inspectable, and cheap.
- Avoid arbitrary code execution in builder-authored formula definitions.
- Make combat resolution depend on a persisted stat snapshot, not on
  repeated full recomputation during every command.

## Non-Goals

- No fully user-programmable combat engine.
- No arbitrary Python or script execution inside formulas.
- No requirement that every world define custom attributes or custom classes.
- No attempt to make every stat name in the engine builder-defined.
- No need to add every imaginable combat stat up front.

## Recommendation

WR2 should separate the system into three layers:

1. Attributes
2. Stats
3. Player-facing labels and presentation

This is the important line:

- inputs may be flexible
- presentation may be flexible
- engine combat semantics should stay fixed

That separation gives builders room to create different worlds without forcing
the runtime to dynamically discover what "mana" or "attack power" means in a
particular world every time combat runs.

## Core Mental Model

### Layer 1: Attributes

These are the builder-authored attributes that players, mobs, items, buffs, and
progression systems can modify directly.

Examples:

- strength
- dexterity
- intelligence
- constitution

Different worlds may choose different sets.

These are not the stats combat code should directly consume during attack
resolution. Attributes feed formulas.

### Layer 2: Stats

Stats are the fixed internal numbers the engine actually uses during combat,
resource management, regen, and encounter resolution.

They are the stable contract between:

- formulas
- equipment and effects
- combat actions
- AI decisions
- UI payload assembly
- tests and balancing tools

Stats should have stable internal identifiers and stable semantics.

### Layer 3: Player-Facing Labels

These are presentation choices.

A world should be able to display:

- `energy` as `Mana`
- `energy` as `Energy`
- `energy` as `Focus`
- `ability_power` as `Spell Power`
- `ability_power` as `Tech Power`
- `ability_power` as `Ability Power`

without changing the underlying engine stat identifiers.

This should be handled as world-configured labels and UI text, not by making
combat formulas or runtime storage dynamically rename stats.

## Stat Contract

WR2 should move away from fantasy-specific internal names where reasonable.

Recommended canonical internal combat stats:

- `health_max`
- `energy_max`
- `stamina_max`
- `health_regen`
- `energy_regen`
- `stamina_regen`
- `attack_power`
- `ability_power`
- `armor`
- `crit`
- `dodge`
- `resilience`

This list is intentionally modest. It covers the current direction without
trying to pre-solve every future combat design problem.

### Why `energy` Instead of `mana`

`mana` is too genre-specific to serve as the preferred engine term.

`energy` is more neutral:

- fantasy worlds can label it as mana
- sci-fi worlds can keep energy as-is
- realistic or low-magic worlds can label it as focus, resolve, stamina, or
  another world-specific term

The engine should use `energy` internally even if some worlds present it as
`Mana`.

### Why `ability_power` Instead of `spell_power`

`spell_power` encodes a magic assumption that does not belong in the engine
contract.

`ability_power` is broad enough to cover:

- magic
- psionics
- tech abilities
- martial techniques
- support abilities

The engine should use `ability_power` internally even if some worlds present it
as `Spell Power`.

## Healing Power

WR2 should not add `healing_power` as a separate canonical stat initially.

For the initial design:

- healing should scale from `ability_power`
- each healing ability can apply its own coefficients

This keeps the engine smaller and easier to understand.

A separate `healing_power` stat becomes justified only when there is a concrete
design reason to distinguish:

- offensive caster scaling
- support or healer scaling
- itemization for healing vs damage

That may become worthwhile later. It should not be part of the base contract
until there is a real balancing need.

## Attribute Direction

Worlds define their own authored attributes. A newly created world has no
fixed `strength`, `dexterity`, `constitution`, or `intelligence` attributes
unless a builder explicitly adds those keys.

Examples:

- a classic fantasy world may define `might`, `grit`, `finesse`, and `lore`
- a sci-fi world may define `power`, `precision`, `systems`, and `will`
- a classless survival world may define only `grit`, `awareness`, and
  `craft`

These authored attributes should be the layer builders think in when creating:

- races or ancestries
- classes or archetypes
- equipment bonuses
- buffs and debuffs
- progression rewards

The important limit is that these attributes should still feed into the
same canonical combat stat contract.

## Formulas

### Recommendation

Formulas should be builder-authored, but only within a constrained declarative
system.

WR2 should not allow arbitrary code for stat formulas.

Instead, formulas should be expressed through a bounded rules or expression
layer that is:

- deterministic
- schema-validated
- inspectable
- testable
- cheap to evaluate

### What Formulas Should Do

Formulas should map from authored attributes into stats.

Examples:

- `strength` contributes to `attack_power`
- `intelligence` contributes to `energy_max`
- `constitution` contributes to `health_max`
- `dexterity` contributes to `crit`
- class profile modifiers change coefficients or grant bonuses

Formulas may also incorporate:

- equipment contributions
- effect contributions
- level or progression contributions
- class or archetype modifiers

### What Formulas Should Not Do

Formulas should not redefine combat semantics.

For example, builders should not be able to redefine:

- how dodge is rolled
- how armor mitigation curves work
- how crit is resolved at attack time
- how encounter step ordering works

Those are engine behaviors, not world-authored formulas.

This boundary matters because it keeps combat behavior coherent across the
runtime and prevents the engine from becoming impossible to reason about.

## Classes and Archetypes

Classes should be optional authored content, not a hard dependency of the
engine.

WR2 should support both:

- class-based worlds
- classless worlds

### What a Class Should Own

A class or archetype should be able to define or influence:

- starting attribute distributions
- growth curves
- ability access
- equipment proficiencies
- formula modifiers
- passive bonuses

### What a Class Should Not Require

The engine should not assume:

- every world has classes
- every character must pick one class
- formulas are impossible without class context

Worlds without classes should simply omit that layer and still resolve cleanly
through the same formula pipeline.

## Runtime Model

### Stat Snapshot

WR2 combat should not recompute full stat calculation from scratch on every
single command or combat step.

Instead, the engine should compute and persist a stat snapshot whenever
relevant inputs change.

Typical recompute triggers:

- equipment changed
- level changed
- class changed
- buff or debuff changed
- authored formula profile changed
- character attribute values changed

Combat and regen systems should read from that persisted stat snapshot.

This is the correct tradeoff:

- computation moves to the moments when inputs change
- encounter resolution reads stable stat values cheaply
- debugging is easier because the effective current stats are inspectable

### Runtime Boundary

At runtime, the combat engine should care about:

- current resource values
- canonical max and regen values
- stats
- temporary combat effects
- encounter state

It should not care about:

- the user-facing label for a stat
- whether `ability_power` came from intelligence, gear, or another source
- whether the world is class-based or classless

That is formula-layer and presentation-layer work.

## Naming and Presentation Strategy

WR2 should treat naming as a presentation concern wherever possible.

That means:

- canonical internal stat ids remain fixed
- worlds may define labels, descriptions, and UI copy for those ids
- commands and UI should render the world's labels when presenting stats

This is a much better tradeoff than making core runtime identifiers builder-
defined.

### Example

Internally:

- `energy_max`
- `energy_regen`
- `ability_power`

Fantasy world presentation:

- `Mana`
- `Mana Regen`
- `Spell Power`

Sci-fi world presentation:

- `Energy`
- `Energy Recharge`
- `Tech Power`

Low-magic presentation:

- `Focus`
- `Focus Recovery`
- `Ability Power`

The engine remains stable while the world presentation changes.

## Combat Relationship

Combat should consume stats, not authored attributes directly.

Examples:

- weapon attacks use `attack_power`
- ability attacks or heals use `ability_power`
- survivability reads `health_max`, `armor`, `dodge`, and `resilience`
- resource spend reads current and max `energy` or `stamina` values

This keeps combat logic decoupled from world-specific stat taxonomies.

That is especially important in WR2's encounter-scoped combat model, where
combat resolution should operate on a simple, explicit runtime contract.

## Why Not Fully Dynamic Engine Stats

It is tempting to let builders define every input and every stat
freely, but that would create major problems:

- combat actions would need per-world semantic lookup
- tools and UI would lose a stable schema
- formula debugging would become much harder
- items and effects would be harder to validate
- tests would be less reusable
- balancing support would be worse

WR2 should be flexible at the edges, not unstructured at the center.

The center should stay small, stable, and explicit.

## Suggested Authoring Direction

This document does not define final manifest kinds, but the authoring model
should make room for separate authored concepts such as:

- attribute definitions
- formula profiles
- class templates
- stat label configuration

At a high level, worlds should be able to author:

- which attributes exist
- which classes exist, if any
- how attributes map into stats
- how stats are labeled to players

What worlds should not author is the core meaning of stats
themselves.

## Practical Starting Point

The recommended first practical WR2 contract is:

- keep stats fixed
- use `energy` consistently for the third resource
- use `ability_power` consistently for non-weapon combat power
- let worlds define labels for those canonical stats
- let worlds define authored attributes
- let classes be optional
- let formulas map authored attributes into stats
- have healing use `ability_power` until there is a strong reason to split it

This is enough flexibility to support different world styles without paying the
cost of a fully dynamic combat engine.

## Future Extensions

If later worlds genuinely need more differentiation, WR2 can add more canonical
stats deliberately.

Possible future additions might include:

- `healing_power`
- `accuracy`
- `block`
- `threat`
- secondary resistance categories

Those should be introduced only when they solve a real gameplay problem across
worlds, not because the engine wants to feel theoretically complete.

## Summary

The WR2 stats system should be:

- flexible in authored attributes
- fixed in engine semantics
- configurable in player-facing labels
- optional in class structure
- declarative in formulas
- snapshot-based at runtime

That gives WR2 the right balance:

- enough freedom for builders
- enough structure for the engine
- enough clarity for players
- enough stability to scale and evolve
