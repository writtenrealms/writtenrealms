# World Config Builder Guide

World config controls the global rules and presentation for a WR2 world. Edit it
with a `kind: world` manifest in **World > Edit**. The current config can be
copied from **World > Config**.

World config manifests are update-only. Applying a `kind: world` document
updates the selected world; it does not create or delete worlds.

```yaml
kind: world
spec:
  name: Edeus
  short_description: A frontier realm.
  description: A long world description.
  motd: Questions? Join Discord.
  is_public: true
  starting_room: room@0,0,0
  death_room: room@0,0,0
  starting_gold: 0
  starting_level: 1
  max_level: 20
  leveling_curve: [0, 30, 100]
  combat_resolution_interval: 0
  default_roam_chance: 10
  death_mode: lose_none
  death_route: top_faction
  death_gold_penalty: 0.2
  pvp_mode: free_for_all
  allow_pvp: true
  auto_equip: true
  is_narrative: false
  players_can_set_title: true
  non_ascii_names: false
  globals_enabled: true
  decay_glory: false
```

## Room References

Use exported room coordinate refs when editing normal exported YAML:

```yaml
starting_room: room@0,0,0
death_room: room@2,0,0
```

The import path also accepts database refs such as `room.123`, but those are not
portable across databases.

## Field Reference

### Identity And Listing

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | text | world name | Required if included; cannot be blank. |
| `short_description` | text | empty | Short listing or summary text. |
| `description` | text | empty | Longer world description. |
| `motd` | text | empty | Message shown on entry surfaces. |
| `is_public` | boolean | `false` | Controls public listing visibility. |
| `built_by` | text | empty | Builder or team credit. |
| `small_background` | URL/text | empty | Small frontend background image. |
| `large_background` | URL/text | empty | Large frontend background image. |

### Entry And Rooms

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `starting_room` | room ref | first room | Required by runtime world entry. |
| `death_room` | room ref | first room | Used by death handling when death routing resolves here. |

`exits_to` exists on `WorldConfig` for instance transfer behavior, but it is not
currently authored through `kind: world` manifests.

### Character Creation And Progression

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `starting_gold` | integer >= 0 | `0` | Gold granted to new players. |
| `starting_level` | integer >= 1 | `1` | Initial player level. |
| `max_level` | integer >= 1 | `20` | Maximum automatic level. |
| `leveling_curve` | list | WR2 default curve | Cumulative XP thresholds; first entry must be `0`. |
| `player_creation` | mapping | `{}` | Player creation policy, including core faction choices. |
| `ability_progression` | mapping | `max_known: 8` | Known ability cap and starting abilities. |
| `can_select_gender` | boolean | `true` | If false, new characters use `default_gender`. |
| `default_gender` | choice | `male` | `male`, `female`, or `non_binary`. |
| `non_ascii_names` | boolean | `false` | Allows non-ASCII character names. |
| `name_exclusions` | text | empty | Names or tokens to block during character creation. |

For progression details, see
[leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md).

For faction selection policy, see
[faction-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/faction-builder-guide.md).

For ability progression, see
[ability-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/ability-builder-guide.md).

### Combat, Roaming, And Runtime Rules

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `combat_resolution_interval` | number | `0` | `0` resolves combat immediately; `> 0` auto-advances rounds on that cadence; `-1` requires explicit advancement. |
| `default_roam_chance` | integer 0-100 | `10` | Percent chance per heartbeat that mobs with zone/path roaming targets move. |
| `is_narrative` | boolean | `false` | Narrative worlds disable combat through the manifest apply path. |
| `auto_equip` | boolean | `true` | New equipment is equipped automatically when possible. |
| `players_can_set_title` | boolean | `true` | Allows players to manage their title. |
| `globals_enabled` | boolean | `true` | Enables global command surfaces that depend on world globals. |
| `decay_glory` | boolean | `false` | Enables glory decay behavior where supported. |

`allow_combat` is a stored config field, but it is not directly authored through
`kind: world` manifests. Use `is_narrative: true` for the current manifest path
when a world should be non-combat.

For cohort patrols, `default_roam_chance` is rolled once for the cohort leader
on each heartbeat. If any live cohort member is in active combat, the cohort
does not roam on that heartbeat; otherwise, present followers move with the
leader when the destination is valid for their roaming target.

For combat formulas and encounter pacing, see
[combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md).

For spawn-plan roaming behavior, see
[spawn-plan-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/spawn-plan-builder-guide.md).

### Death And PvP

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `death_mode` | choice | `lose_none` | `lose_all`, `lose_none`, `lose_eq`, `destroy_eq`, `destroy_all`, `lose_gold`, or `lose_inv`. |
| `death_route` | choice | `top_faction` | `top_faction`, `near_room`, `far_room`, or `nearest_in_zone`. |
| `death_gold_penalty` | number | `0.2` | Fraction of gold lost for gold-loss death modes. |
| `pvp_mode` | choice | `free_for_all` | `free_for_all`, `disabled`, or `zone`. |
| `allow_pvp` | boolean | `true` | Global PvP permission switch. |

For death-related builder commands, see
[builder-command-reference.md](/Users/teebes/code/writtenrealms/docs/guides/builder-command-reference.md).

### Authored Systems

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `stats` | mapping | minimal WR2 stamina defaults | Attributes, labels, class profiles, and stat formulas. |
| `combat` | mapping | default combat model | Combat profiles, rating curves, mitigation, variance, and crit rules. |
| `equipment` | mapping | default equipment model | Armor classes, armor proficiency, and equipment policy. |

For stats and attributes, see
[attributes-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/attributes-builder-guide.md).

For equipment and armor classes, see
[item-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/item-definition-builder-guide.md).

For combat configuration, see
[combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md).

## Legacy Or Derived Fields

Some stored config fields are not meant to be authored directly in current world
manifests.

| Field | Status | Use Instead |
| --- | --- | --- |
| `can_select_faction` | Derived storage field. | Configure `player_creation.core_faction`. |
| `allow_combat` | Stored field not accepted in world manifests. | Use `is_narrative`. |
| `is_classless` | Legacy compatibility field accepted by import. | Configure `stats.class_profiles`; absence of class profiles means classless. |
| `starting_eq` | Stored many-to-many starter equipment. | Not currently authored through `kind: world`. |
| `exits_to` | Instance transfer field. | Configure instance entry/exit behavior through instance-specific workflows. |

## Instance Templates

Instance template worlds only accept local config fields in `kind: world`
manifests:

- identity text: `name`, `short_description`, `description`, `motd`,
  `is_public`
- local rooms: `starting_room`, `death_room`
- local death/PvP/presentation fields: `death_mode`, `death_route`,
  `death_gold_penalty`, `pvp_mode`, `allow_pvp`, `built_by`,
  `small_background`, `large_background`

Core systems such as `stats`, `combat`, `equipment`, `ability_progression`,
`leveling_curve`, `starting_level`, `max_level`, `combat_resolution_interval`,
and `default_roam_chance` are inherited from the base world.

For instance authoring, see
[instance-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/instance-builder-guide.md).

## Validation Notes

- Unknown `spec` fields are rejected.
- Boolean fields must be YAML booleans or recognizable boolean strings.
- Integer fields must be integers.
- `starting_level` and `max_level` must be at least `1`.
- `default_roam_chance` must be between `0` and `100`.
- `combat_resolution_interval` can be `-1` or any value greater than or equal
  to `0`.
- `leveling_curve` must be long enough for `max_level`, and its first entry must
  be `0`.
