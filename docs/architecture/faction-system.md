# WR2 Faction System

## Purpose

This document defines the WR2 direction for factions, reputation, character
creation faction policy, mob definition faction assignment, and faction-linked
language behavior.

The immediate goals are:

- replace builder-facing "minor faction" terminology with "reputation faction"
- define the WR2 core/reputation faction model
- make faction definitions portable through YAML manifests
- make mob definitions able to declare factions directly
- keep language behavior separate from faction identity while allowing core
  factions to seed language knowledge

Reference docs:

- `.codex/skills/wr-transition/wr2-architecture.md`
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [deterministic-death-routing.md](deterministic-death-routing.md)
- [mob-definition-builder-guide.md](../guides/builders/mob-definition-builder-guide.md)
- [instance-builder-guide.md](../guides/builders/instance-builder-guide.md)
- [condition-builder-guide.md](../guides/builders/condition-builder-guide.md)

## Core Recommendation

Expose faction kind as a typed field in manifests and builder-facing docs:

```yaml
spec:
  type: core
```

Supported values for the initial WR2 faction model:

- `core`
- `reputation`

Do not use an `is_core` boolean in WR2 manifests or new builder-facing faction
configuration. A boolean makes the false branch an undefined bucket. WR2 should
name both supported categories directly.

Faction definitions own faction identity and metadata. World config owns player
creation policy. Mob definitions own their authored faction assignments.

## Vocabulary

### Core Faction

A core faction is a major character or mob identity.

Use core factions for:

- race
- nation
- origin side
- major order
- permanent campaign side
- identity that affects starting room, visibility, language, diplomacy, or PvP

A player or mob should have at most one core faction.

Examples:

- `human`
- `orc`
- `lifeless`
- `empire`
- `rebellion`

### Reputation Faction

A reputation faction is a group that tracks mutable standing.

Use reputation factions for:

- local organizations
- towns
- guilds as NPC institutions
- enemy forces that may become friendly or hostile
- quest reward/penalty standing
- mobs whose faction standing should influence aggro or quests

A player or mob may have many reputation faction assignments.

Examples:

- `blackfin`
- `town_watch`
- `ashwick`
- `rangers_guild`

Use "reputation faction" in builder-facing UI and docs. Do not use "minor
faction" for new WR2 surfaces.

### Cohort

A cohort is a runtime or authored group marker for a particular spawn set,
encounter, instance objective, or temporary enemy group.

Use cohorts when the group does not need standing, faction ranks, player
reputation, language, or cross-system diplomacy.

Examples:

- all mobs spawned by a Blackfin instance entry
- three lieutenants in a timed clear
- a boss wave

Do not create reputation factions only to mark one instance clear group.
Spawn-plan traits, spawn entry slugs, or dedicated cohort metadata are a better
fit.

### Clan Or Guild

Player organizations should remain a separate system.

A player guild can have an associated reputation faction if NPCs need to track
standing toward it, but membership in a player organization is not the same as a
core faction or reputation faction.

### Language

Language is a communication capability. It is not a faction type.

Core factions may grant default languages, but language knowledge should be
modeled under communication configuration and character state, not by adding
more faction categories.

## Non-Goals

Do not add extra faction types until there is a concrete runtime need.

Likely non-types:

- temporary instance teams
- spawn-plan cohorts
- quest-only labels
- player clans
- languages
- combat parties
- trigger tags

These should stay as their own systems or as tags/traits where appropriate.

## Faction Definition Manifest

Factions should be portable authored content.

Recommended shape:

```yaml
kind: faction
metadata:
  world: world.1
  code: human
  name: Human
spec:
  type: core
  description: Humans are adaptable and numerous.
  playable: true
  starting_room: room@0,0,0
  default_languages:
    - common
```

Reputation faction example:

```yaml
kind: faction
metadata:
  world: world.1
  code: blackfin
  name: Blackfin Raiders
spec:
  type: reputation
  description: A pirate fleet operating from the outer shoals.
  ranks:
    - standing: -100
      name: Hated
    - standing: 0
      name: Neutral
    - standing: 100
      name: Trusted
```

### Metadata

`metadata.code` is the stable author-facing faction identifier.

Rules:

- code must be unique within a world
- code should be lowercase snake case
- code changes should be blocked while live worlds depend on it
- references should use the code, not a database id, when possible

`metadata.name` is the display name.

### Spec Fields

Common fields:

- `type`: `core` or `reputation`
- `description`: player or builder-facing description

Core faction fields:

- `playable`: whether players may be assigned this core faction at character
  creation
- `starting_room`: optional faction-specific starting room
- `default_languages`: optional language codes granted by this core faction

Reputation faction fields:

- `ranks`: ordered standing thresholds and display names

Do not put the default player core faction on the faction document. The default
is world policy, not a faction property.

## World Config Manifest

World config should own character creation policy.

Recommended shape:

```yaml
kind: world
spec:
  player_creation:
    core_faction:
      mode: choose_required
      default: human
      options:
        - human
        - elf
        - orc
```

### Core Faction Modes

Supported `player_creation.core_faction.mode` values:

| Mode | Behavior |
| --- | --- |
| `none` | Do not assign player core factions. |
| `fixed_default` | Assign `default`; do not show a choice. |
| `choose_required` | Player must choose one allowed core faction. |
| `choose_optional` | Player may choose one allowed core faction or no core faction. |

### Default

`default` is the faction code assigned when:

- mode is `fixed_default`
- mode is `choose_required` and the UI needs an initial selected value
- mode is `choose_optional` and the UI wants a suggested value

Validation:

- `fixed_default` requires a valid `default`
- `choose_required` should require a valid `default` unless there is exactly
  one available option
- `choose_optional` may omit `default`
- `none` should ignore `default`

### Options

`options` is the list of core faction codes offered at character creation.

If `options` is omitted:

- use every `type: core` faction with `playable: true`

If `options` is present:

- every option must reference a `type: core` faction
- every option should reference a playable faction
- order should control the character creation UI order

### No Faction

If "no faction" means true absence, use `choose_optional` or `none` and store no
core assignment.

If "unaffiliated" has a starting room, language, diplomacy, or game meaning,
model it as a real core faction:

```yaml
kind: faction
metadata:
  code: unaffiliated
  name: Unaffiliated
spec:
  type: core
  playable: true
```

Do not use a magic empty string as a hidden faction.

## Communication And Languages

Language behavior belongs under world communication config.

Recommended shape:

```yaml
kind: world
spec:
  communication:
    languages:
      enabled: true
      default: common
      core_faction_defaults:
        human:
          - common
        elf:
          - elvish
          - common
        orc:
          - orcish
      say:
        understanding: shared_language
        unknown_speech: garble
      yell:
        understanding: shared_language
        unknown_speech: garble
```

`core_faction_defaults` seeds a new character's known languages from their core
faction. It should not be the only way to learn languages. Quests, items,
training, builder commands, and character state can grant languages
independently.

### Understanding Modes

Initial supported mode:

- `shared_language`: the listener understands the message if the speaker and
  listener share at least one language

Out-of-scope modes:

- `always`: everyone understands
- `never_cross_faction`: strict faction wall, regardless of language
- `proficiency_check`: partial comprehension based on proficiency data

Do not implement these modes until there is a specific design need.

### Unknown Speech

Initial supported values:

- `garble`: deliver the message but transform the spoken text
- `hide_text`: show that someone spoke, but not the contents
- `hide_message`: do not deliver the message

`garble` is the best default because it keeps social presence without
leaking exact text.

### Runtime Contract

Communication commands should evaluate language at publish time, not by
splitting the room into separate command executions.

The event can still be one canonical `say` or `yell` action result, with
recipient-specific rendered text depending on listener language knowledge.

## Mob Definition Factions

WR2 mob definitions should support factions directly in YAML.

Recommended shape:

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

Rules:

- `core` must reference a `type: core` faction
- a mob definition may have at most one core faction
- `reputation` values must reference `type: reputation` factions
- reputation values are integer standing values
- omitted reputation standing means no explicit assignment

Spawned mobs should receive concrete faction assignments from their definition
at spawn time. Do not store definition faction data only as JSON.

Reasons:

- aggro checks can read spawned mob assignments consistently
- death events can include the killed mob's factions
- quest conditions can inspect event faction data
- deleted or edited definitions do not make spawned runtime mobs lose their
  identity unexpectedly

When a stable mob definition is edited, spawned mobs linked to that definition
should sync faction assignments using the same policy as other definition-backed
fields:

- update definition-sourced faction assignments
- keep runtime-added faction assignments from other sources
- avoid duplicate core assignments

## Player Assignments

A player core faction is canonical character identity.

The target storage is nullable `Player.core_faction`, validated against the
authored base-world family resolved from the player's runtime or instance
context. `FactionAssignment` remains the canonical player storage only for
reputation standings.

Creation rules:

- use world `player_creation.core_faction` policy
- validate submitted faction codes server-side
- only allow `type: core` factions for core assignment
- only allow playable/options-listed factions during character creation
- assign no core faction only when mode allows it

Reputation assignments:

- are mutable standings
- default to `0` when no assignment exists
- should be created only when standing becomes non-zero or when display/rank
  history requires persistence

Read paths should not create default faction assignments as a side effect.
Default assignment should happen during creation, reset, or explicit admin
repair.

## Quest And Trigger Integration

Reputation changes should be typed WR2 effects.

Recommended quest effect:

```yaml
effects:
  - type: adjust_reputation
    faction: ashwick
    amount: 2
```

Rules:

- `faction` must reference a `type: reputation` faction
- amount may be positive or negative
- the effect creates or updates the player's reputation assignment
- V1 does not expose a gameplay `set_core_faction` effect. A future explicit
  core-faction repair/change action must follow the validation and aggregate
  locking contract in
  [deterministic-death-routing.md](deterministic-death-routing.md); it must not
  reuse `adjust_reputation`.

Conditions that check faction state must use the WR2 condition framework rather
than adding a new predicate language.

Recommended condition field vocabulary:

```yaml
conditions:
  all:
    - eq: [player.core_faction, human]
    - gte: [player.reputation.ashwick, 50]
```

Event payloads should expose faction data where useful:

- mob death events should include killed mob core faction and reputation
  assignments
- player events may include core faction when relevant
- quest event predicates should read event payloads, not query unrelated state
  when possible

## Aggro And Diplomacy

WR2 faction aggro should be policy-driven. The baseline policy is simple:
different core factions are hostile by default, and same-core actors are not
hostile by default.

Worlds that need richer diplomacy should express it as relationship policy, not
as additional faction types.

Baseline rules:

- passive mobs do not aggro from faction rules
- all/players aggression ignores faction rules and attacks eligible players
- normal/friendly aggression uses faction comparison
- same core faction is non-hostile by default
- different core faction is hostile by default
- reputation faction standing can create hostility when the mob is aligned with
  a reputation faction and the player has negative standing with it

Relationship policy can be represented as data when a world needs exceptions:

```yaml
kind: faction
metadata:
  code: town_watch
spec:
  type: reputation
  relationships:
    blackfin: hostile
    rangers_guild: friendly
```

## Death Routing

The target contract is defined in
[deterministic-death-routing.md](deterministic-death-routing.md).

Core faction may be used alone or alongside class, character-state, and
origin-zone predicates through the shared condition DSL. Reputation faction
and rank do not participate. Portable faction codes resolve to canonical
faction ids when the policy is applied, and the runtime consumes only a
precompiled ordered predicate list.

New behavior belongs to explicit world/instance policy rather than an implicit
scan of faction assignments or faction rooms. The existing faction
`death_room`, legacy `death_route` enum, and `Procession` structures are not the
target runtime contract.

## Builder UI Language

Use these labels:

- "Core Factions"
- "Reputation Factions"
- "Reputation Standing"
- "Faction Ranks"

Avoid:

- "Minor Factions"
- "Race" as the generic system label
- "Clan" unless referring to player organizations

Character creation should say "Faction" only when the world is using core
factions as player-facing identity. If a world uses core factions as races or
nations, builder labels may allow a world-specific display label, but the
underlying manifest should still use `core_faction`.

## Storage Guidance

Canonical faction data should remain relational:

- faction definitions
- faction ranks
- nullable `Player.core_faction` for stable player identity
- player `FactionAssignment` rows for reputation standings
- mob definition assignments
- spawned mob assignments

Runtime payloads may denormalize faction data for efficient client updates, but
the relational data must be sufficient to rebuild the payloads.

Do not make mob definition faction membership a JSON-only field. JSON is useful
for manifest parsing and payloads, but the canonical assignments should be
queryable and enforceable.

## Implementation Status

Implemented WR2 surfaces:

- faction storage, serializers, and manifests use `type: core | reputation`
- `kind: faction` manifest apply/export/delete
- world player creation policy under `player_creation.core_faction`
- builder Config -> Factions list/detail YAML workflow
- mob definition faction assignment support
- mob definition factions copied/synced to spawned mobs
- WR2 quest effects for reputation standing
- player authored identity uses nullable `Player.core_faction`
- stop writing player core identity as a core `FactionAssignment`; retain
  player assignments for reputation only

Still future work:

- communication language behavior after faction manifests are stable

## Advent Import Adapter

Advent export-to-manifest adapters may translate Advent fields into clean WR2
documents. That mapping belongs only at the import/export boundary.
It should not shape WR2 storage, serializers, APIs, or runtime behavior.

| Advent field | WR2 manifest output |
| --- | --- |
| `is_core: true` | `type: core` |
| `is_core: false` | `type: reputation` |
| `is_selectable` | `playable` on the faction, plus world creation options |
| `is_default` | `player_creation.core_faction.default` in world config |
| `can_select_faction` | `player_creation.core_faction.mode` |

## Testing Targets

Backend tests should cover:

- faction manifest create/update/delete
- uniqueness of faction code per world
- world config player creation policy modes
- player creation validation for faction selection
- no-faction player creation when allowed
- mob definition faction manifests
- definition-backed mob spawn faction assignment
- definition edit syncing spawned mob faction assignments
- aggro with core factions and reputation factions
- `adjust_reputation` quest effects
- language filtering for `say` and `yell` when enabled

Place new tests under `backend/tests/`.

Advent adapter tests should cover the translation table above without requiring
WR2 to accept Advent field names directly.
