# World Config Builder Guide

World config controls the global rules and presentation for a WR2 world. Edit it
with a `kind: world` manifest in **World > Edit**. The current config can be
copied from **World > Config**.

World config manifests are update-only. Applying a `kind: world` document
updates the selected world; it does not create or delete worlds.

The **World > Config** page also links to related WR2 authoring and operational
screens. The former **Random Item Profiles** and **Transformations** screens
have been removed. Use item-definition `spec.randomization` for bounded rolls,
`kind: itembundle` for weighted item choices, and canonical mob definitions or
spawn-plan traits for representable spawn variations. There is no
`randomitemprofile` or `transformation` manifest kind.

```yaml
kind: world
spec:
  name: Edeus
  short_description: A frontier realm.
  description: A long world description.
  motd: Questions? Join Discord.
  is_public: true
  initial_state:
    weather: clear
    invasion_active: false
  starting_room: room@0,0,0
  death_room: room@0,0,0
  default_currency: crowns
  starting_balances:
    crowns: 0
  starting_equipment:
    - item_definition: itemdefinition.training_spear
      count: 1
      equip: false
    - item_definition: itemdefinition.lockpick
      count: 2
      archetype: assassin
  starting_level: 1
  max_level: 20
  leveling_curve: [0, 30, 100]
  combat_resolution_interval: 0
  default_roam_chance: 10
  death_mode: lose_none
  death_route: top_faction
  death_currency: crowns
  death_currency_penalty: 0.2
  pvp_mode: free_for_all
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
| `initial_state` | mapping | `{}` | Seed state copied into each new runtime world; it is not live runtime state. |

`exits_to` exists on `WorldConfig` for instance transfer behavior, but it is not
currently authored through `kind: world` manifests.

`initial_state` contains authored defaults for `state.world.*`. Applying a
manifest changes the seed for future runtime worlds; it does not overwrite a
currently running world's state. On an instance template, these defaults seed
each new run and each builder reset. They are local to the template and are not
merged with the base world's runtime state.

### Character Creation And Progression

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `default_currency` | currency code | initial currency | Default used for new authoring that omits a currency. |
| `starting_balances` | code-to-integer mapping | `{}` | Exact starting wallet policy; omitted currencies start at zero. |
| `starting_equipment` | list | `[]` | Item definitions granted to new players, with optional `count`, `archetype`, and `equip`. |
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

Starting equipment entries use WR2 item definitions, not legacy item templates.
Use `itemdefinition.<slug>` or a bare item definition slug. `count` defaults to
`1`. If `archetype` is present, the item is only granted to players whose
selected archetype/class id exactly matches that value. `equip` defaults to
`true` for equippable items. Set `equip: false` to grant an item into the
character's carried inventory without equipping it.

Starting balances are inherited from the base world and applied on character
initialization and explicit reset. They are not granted again on reconnect or
instance entry. Amounts must be whole numbers from `0` through
`9,007,199,254,740,991`. For definitions, prices, rewards, and deletion rules,
see [currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md).

### Class-Specific Starting Loadout

Class-specific gear uses `starting_equipment[*].archetype`. Class-specific
abilities use conditions in `ability_progression.starting_abilities`:

```yaml
kind: world
spec:
  starting_equipment:
    - item_definition: itemdefinition.hoplite-spear
      archetype: hoplite
      equip: false
    - item_definition: itemdefinition.hoplite-sword
      archetype: hoplite
    - item_definition: itemdefinition.hoplite-shield
      archetype: hoplite
  ability_progression:
    max_known: 6
    starting_abilities:
      - ability: bash
        conditions:
          eq: [actor.archetype, hoplite]
      - ability: guard
        conditions:
          eq: [actor.archetype, hoplite]
```

In this example, a new Hoplite carries the spear as an alternate weapon, starts
with the sword and shield equipped, and knows both abilities. Other classes
receive none of these class-specific entries.

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
| `death_mode` | choice | `lose_none` | `lose_all`, `lose_none`, `lose_eq`, `destroy_eq`, `destroy_all`, `lose_currency`, or `lose_inv`. |
| `death_route` | choice | `top_faction` | `top_faction`, `near_room`, `far_room`, or `nearest_in_zone`. |
| `death_currency` | currency code/null | initial currency | Balance charged by `lose_currency`. |
| `death_currency_penalty` | number 0-1 | `0.2` | Fraction of equipped-item value denominated in `death_currency`, capped by that balance, charged on a non-PvP `lose_currency` death. |
| `pvp_mode` | choice | `free_for_all` | Sole authored PvP policy: `free_for_all`, `disabled`, or `zone`. |

`pvp_mode` is the only PvP policy authored in current WR2 world manifests. The
manifest importer still accepts the legacy authored-content alias `allow_pvp`
and normalizes `false` to `disabled` or `true` to `free_for_all`. This is not a
WR1 database migration path. If both fields are present, `allow_pvp` must agree
with whether `pvp_mode` is disabled. New and exported manifests should use only
`pvp_mode`.

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
  `death_currency`, `death_currency_penalty`, `pvp_mode`, `built_by`,
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
