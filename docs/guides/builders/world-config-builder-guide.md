# World Config Builder Guide

World config controls the global rules and presentation for a WR2 world. Open
**World > Config** to edit the current canonical `kind: world` YAML directly.
Use **Copy YAML** to copy the editor contents and **Save YAML** to apply changes
to the selected world. **World > Edit** remains available for general and
multi-document manifest workflows.

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
  starting_room: room@1
  death_room: room@1
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
  death_currency: crowns
  death_currency_penalty: 0.2
  death_routing:
    routes:
      - when:
          gte: [player.level, 20]
        destination: room@9
      - when:
          eq: [player.archetype, warlord]
        destination: room@10
      - when:
          eq: [player.core_faction, orc]
        destination: room@11
      - when:
          eq: [state.character.divine_patron, poseidon]
        destination: room@12
  pvp_mode: free_for_all
  announce_duel_results: false
  auto_equip: true
  is_narrative: false
  players_can_set_title: true
  non_ascii_names: false
  globals_enabled: true
  decay_glory: false
```

## Room References

Use the stable room refs shown in exported YAML:

```yaml
starting_room: room@1
death_room: room@2
```

`room@<relative_id>` identifies the same authored room after it moves and after
world export/import. The import path also accepts legacy coordinate refs such
as `room@2,0,0` and database refs such as `room.123`; both are import-only
aliases and canonical YAML rewrites them to the stable form. Database refs are
not portable across installations.

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

`exits_to` exists on `WorldConfig` for instance transfer behavior, but it is
not a local `kind: world` field. A base/instance family export represents it
centrally in the `kind: worldbundle` header:

```yaml
spec:
  links:
    - relation: world_config.exits_to
      source:
        world: instance.hades
      target:
        world: world@base
        room: room@42
```

The source must be a direct authored instance template and the target must be
a stable room in its base world. The bundle importer applies this link only
after both scopes and their rooms exist.

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
[leveling-builder-guide.md](leveling-builder-guide.md).

For faction selection policy, see
[faction-builder-guide.md](faction-builder-guide.md).

For ability progression, see
[ability-builder-guide.md](ability-builder-guide.md).

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
see [currency-builder-guide.md](currency-builder-guide.md).

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
[combat-formula-builder-guide.md](combat-formula-builder-guide.md).

For spawn-plan roaming behavior, see
[spawn-plan-builder-guide.md](spawn-plan-builder-guide.md).

### Death And PvP

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `death_mode` | choice | `lose_none` | `lose_all`, `lose_none`, `lose_eq`, `destroy_eq`, `destroy_all`, `lose_currency`, or `lose_inv`. |
| `death_routing` | mapping/null | `null` | Optional ordered deterministic routing policy. |
| `death_currency` | currency code/null | initial currency | Balance charged by `lose_currency`. |
| `death_currency_penalty` | number 0-1 | `0.2` | Fraction of equipped-item value denominated in `death_currency`, capped by that balance, charged on a non-PvP `lose_currency` death. |
| `pvp_mode` | choice | `free_for_all` | Sole authored PvP policy: `free_for_all`, `disabled`, `zone`, or `match`. |
| `announce_duel_results` | boolean | `false` | Base-world policy that announces completed duel results when enabled. |

`death_room` is always the unconditional fail-safe. A routing policy adds
ordered conditional destinations without replacing that safety room. Routes
are evaluated from top to bottom against the character and the room where the
death occurred. Evaluation stops at the first match.

Every completed player death sets current health, energy, and stamina to 1,
regardless of `death_mode`, the matched route, or whether an instance delegates
to base-world routing. Normal regeneration resumes from those values.

Death routing uses the shared condition syntax, but the death compiler accepts
only a bounded, precompilable subset:

- `player.core_faction` for canonical core-faction identity
- `player.archetype` for the world's class/profile key
- `player.level` for exact levels or inclusive level bands
- any `state.character.*` path set by gameplay or triggers
- `zone.id` for the authored zone containing the room where the death occurred
- `eq`, `in`, `all`, `any`, and `not`
- `gte` and `lte` for `player.level`
- `always: true` only as the final route

Destinations, faction codes, class keys, and zone references are validated when
the manifest is applied. Query-backed conditions such as inventory, quests, or
mob presence are deliberately unavailable in the death hot path. A trigger can
translate those gameplay consequences into character state before a later
death. There is no reserved death-routing state key and no player command for
selecting a route; builders may use any valid `state.character.*` path and set
it through ordinary gameplay.

Level operands must be positive integers. `gte` and `lte` are inclusive:
`gte: [player.level, 20]` means level 20 or higher. Since levels are integers,
strictly above level 20 can be written as `gte: [player.level, 21]`, and
strictly below level 20 as `lte: [player.level, 19]`.

A class profile cannot be removed while a death route in the base world or one
of its instance templates still references that class key. Clear or update
those routes first.

Order is significant, and overlapping conditions are allowed. For example, a
zone-specific field hospital can override class and faction destinations by
appearing first:

```yaml
death_routing:
  routes:
    - when:
        eq: [zone.id, zone@7]
      destination: room@10

    - when:
        gte: [player.level, 20]
      destination: room@9

    - when:
        eq: [player.archetype, warlord]
      destination: room@11

    - when:
        in: [player.core_faction, [human, orc]]
      destination: room@12

    - when:
        eq: [state.character.divine_patron, poseidon]
      destination: room@13
```

Omitting `death_routing` from an update preserves the current policy. Setting
it to `null`, or supplying an empty route list, disables conditional routing.
If no route matches, the player goes to `death_room`.

`pvp_mode` is the only PvP policy authored in current WR2 world manifests. The
manifest importer still accepts the legacy authored-content alias `allow_pvp`
and normalizes `false` to `disabled` or `true` to `free_for_all`. This is not a
WR1 database migration path. If both fields are present, `allow_pvp` must agree
with whether `pvp_mode` is disabled. New and exported manifests should use only
`pvp_mode`.

`match` is the instance-arena policy. It does not permit unrestricted attacks:
the runtime must find an active match in the current instance and both the
attacker and target must be eligible participants. A completed match remains
closed to further PvP, so participants must leave and create a new match before
fighting again. Keep the base world's `pvp_mode` set to `disabled` when PvP
should only happen in linked arena instances.

`announce_duel_results` belongs to the base world and is inherited by its
instances. When enabled, a completed duel can publish
`<winner> has defeated <loser> in a duel.` to the base world's announcement
audience. It is omitted from instance-template manifests and cannot be
overridden per arena.

For death-related builder commands, see
[builder-command-reference.md](builder-command-reference.md).

### Authored Systems

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `stats` | mapping | minimal WR2 stamina defaults | Attributes, labels, class profiles, and stat formulas. |
| `combat` | mapping | default combat model | Combat profiles, rating curves, mitigation, variance, and crit rules. |
| `equipment` | mapping | default equipment model | Armor classes, armor proficiency, and equipment policy. |

For stats and attributes, see
[attributes-builder-guide.md](attributes-builder-guide.md).

For equipment and armor classes, see
[item-definition-builder-guide.md](item-definition-builder-guide.md).

For combat configuration, see
[combat-formula-builder-guide.md](combat-formula-builder-guide.md).

## Legacy Or Derived Fields

Some stored config fields are not meant to be authored directly in current world
manifests.

| Field | Status | Use Instead |
| --- | --- | --- |
| `can_select_faction` | Derived storage field. | Configure `player_creation.core_faction`. |
| `allow_combat` | Stored field not accepted in world manifests. | Use `is_narrative`. |
| `is_classless` | Legacy compatibility field accepted by import. | Configure `stats.class_profiles`; absence of class profiles means classless. |
| `death_route` | Legacy authored field retained for compatibility; deterministic routing does not interpret it. | Configure `death_routing`. |
| `starting_eq` | Stored many-to-many starter equipment. | Not currently authored through `kind: world`. |
| `exits_to` | Cross-world instance transfer field; not accepted inside `kind: world`. | Configure it through the family bundle's `world_config.exits_to` link. |

## Instance Templates

Instance template worlds only accept local config fields in `kind: world`
manifests:

- identity text: `name`, `short_description`, `description`, `motd`,
  `is_public`
- local rooms: `starting_room`, `death_room`
- local death/PvP/presentation fields: `death_mode`, `death_routing`,
  `death_routing_source`, `death_currency`, `death_currency_penalty`,
  `pvp_mode`, `built_by`, `small_background`, `large_background`

`death_routing_source` is instance-only. `local` is the default and uses the
instance template's complete routing policy. `base_world` uses the base
world's complete policy and atomically returns the player to the exact base
runtime from which they entered. The instance's local `death_room` remains
required as a transport-integrity fallback, and the instance still owns its
death penalty.

Core systems such as `stats`, `combat`, `equipment`, `ability_progression`,
`leveling_curve`, `starting_level`, `max_level`, `combat_resolution_interval`,
`default_roam_chance`, and `announce_duel_results` are inherited from the base
world.

In a family bundle, every instance `kind: world` document has
`metadata.world_ref: instance.<instance_slug>`. Its `starting_room`,
`death_room`, local death-routing destinations, and any other room refs resolve
only inside that instance template. The instance slug is the stable portable
scope; its database world id and spawned runtime `instance_ref` are not
manifest identity. Cross-world return behavior remains in the bundle header,
not in this document.

For instance authoring, see
[instance-builder-guide.md](instance-builder-guide.md).

## Validation Notes

- Unknown `spec` fields are rejected.
- Boolean fields must be YAML booleans or recognizable boolean strings.
- Integer fields must be integers.
- `starting_level` and `max_level` must be at least `1`.
- `default_roam_chance` must be between `0` and `100`.
- `combat_resolution_interval` can be `-1` or any value greater than or equal
  to `0`.
- `pvp_mode` must be `free_for_all`, `disabled`, `zone`, or `match`.
- `leveling_curve` must be long enough for `max_level`, and its first entry must
  be `0`.
