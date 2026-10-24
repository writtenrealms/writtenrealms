# World Config Builder Guide

World config controls the global rules and presentation for a WR2 world. Open
**World > Config** to edit the current canonical `kind: world` YAML directly.
Use **Copy YAML** to copy the editor contents and **Save YAML** to apply changes
to the selected world. **World > Edit** remains available for general and
multi-document manifest workflows.

## World information

Open **More information** below the description in your world's lobby to visit
`/worlds/<world_id>/about`. Players see a formatted page; the author, staff, and
builders with editing access can choose **Edit**. An empty page opens the editor
automatically for these builders.

Site administrators can register a stable short URL in Django admin under
**Worlds → World URLs**: select the world and enter a unique lowercase slug
such as `phalanx` in **URL**. Leave **Is private** unchecked for an ordinary
alias that follows the world's existing access rules. This enables both
`/worlds/phalanx` and `/worlds/phalanx/about`; numeric URLs remain valid. These
site-specific aliases are configured separately from World Config and world
manifests, and do not change when the world is renamed.

Write plain Markdown, use **Preview** to check it, then **Save** to publish it on
the world's information page. **Cancel** discards the draft. Failed saves keep
your draft available for retry, and leaving with unsaved changes asks first.

Set `built_by` in World Config to display a muted **Built by** credit below the
About heading. Leave it blank to omit the credit.

Headings, paragraphs, emphasis, lists, links, images, quotes, tables, and code
blocks are supported. Code blocks preserve indentation and line breaks, and
wrap long lines to fit the page. Raw HTML is shown as text. Content is limited to
50,000 characters. Keep the lobby description brief; use this page for longer
setting notes, world rules, and guidance for new players.

The same Markdown is available as `spec.information` in World Config YAML and
travels with world exports/imports. Omitting it preserves existing content;
setting it to an empty string clears the page. Instance templates can have their
own information. Public pages are readable by visitors; private pages follow
the world's lobby access rules.

```yaml
kind: world
spec:
  information: |
    ## Welcome

    Explore ancient Greece and choose your path.

    - Meet the locals.
    - Find your first quest.
```

## Editing world configuration

Loading or saving reloads the YAML in the [standard field order](#yaml-field-order),
with common controls first and related settings together. Reordering top-level
keys in the editor does not change their meaning; the next reload restores the
standard order. Ordered lists, such as `death_routing.routes`, keep their authored
order and retain their existing priority rules.

You can edit one field in the full YAML and save the whole document. Unchanged
currency defaults and starting balances do not count as currency edits, so they
do not block an unrelated change such as `starting_room` while the world is
running. Changing `default_currency` or `starting_balances` still requires the
affected worlds to be stopped; the editor displays a validation error if that
requirement is not met.

Instance templates can also set `instance_goal` to record timed population
clears, such as killing every initially spawned Persian mob. See
[timed population clears](instance-builder-guide.md#timed-population-clears)
for the YAML, faction conditions, and timestamp semantics.

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
  is_public: true
  starting_room: room@1
  is_narrative: false
  pvp_mode: free_for_all
  combat_resolution_interval: 0
  default_roam_chance: 10
  death_room: room@1
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
  starting_level: 1
  max_level: 20
  auto_equip: true
  non_ascii_names: false
  starting_equipment:
    - item_definition: itemdefinition.training_spear
      count: 1
      equip: false
    - item_definition: itemdefinition.lockpick
      count: 2
      archetype: assassin
  leveling_curve: [0, 30, 100]
  default_currency: crowns
  starting_balances:
    crowns: 0
  globals_enabled: true
  players_can_set_title: true
  announce_duel_results: false
  decay_glory: false
  short_description: A frontier realm.
  description: A long world description.
  motd: Questions? Join Discord.
  initial_state:
    weather: clear
    invasion_active: false
```

## YAML Field Order

The Config editor and world/family exports use the same top-level order. Each
group contains only fields supported by the selected base world or instance
template; inherited base-world settings stay out of instance YAML.

| Order | Group | Settings |
| --- | --- | --- |
| 1 | Core controls | Name, public visibility, starting room, narrative/PvP mode, combat timing, roaming, and fleeing. |
| 2 | Instance run | Single-player access, time control, and completion goal. |
| 3 | Death | Fallback room, loss mode and currency penalty, routing source, then routing rules. `death_routing_source` sits immediately before `death_routing`. |
| 4 | Characters | Starting/max level, equipment behavior, gender/naming defaults, starting equipment, creation policy, XP curve, and abilities. Compact defaults precede the longer policy maps. |
| 5 | Economy | Default currency, starting balances, and clan registration currency/cost. |
| 6 | Community | Global commands, player titles, leaderboards, duel announcements, and glory decay. |
| 7 | Presentation | Listing descriptions, message of the day, entry message/mode, backgrounds, and builder credit. |
| 8 | Runtime maintenance | Reload policy, upkeep, cross-faction login cooldown, and initial world state. |
| 9 | Detailed systems | The large `stats`, `combat`, and `equipment` maps. |

Long descriptions and detailed system maps appear below everyday controls so
they do not push entry, combat, instance, and death settings out of view.

## Lobby Leaderboards

Configure an ordered list of panels on the **base world's** Config YAML.
For example, Phalanx can show Persian Outpost times followed by duel rankings:

```yaml
kind: world
spec:
  leaderboards:
    - type: instance_clear_time
      instance: a-persian-outpost
      entry_name: "{{ name }} the {{ class }}"
    - type: dueling
      minimum_matches: 3
```

`instance` is the template's stable instance slug, not its name, runtime ID,
or database ID. It must belong to this base world and have completion criteria.
Panels keep their list order. Add more instance panels by repeating the first
entry with another slug. No separate leaderboard editor is required.

| Type | Settings | Ranking |
| --- | --- | --- |
| `instance_clear_time` | Required `instance`; optional `timing: current`, `time_control`, or `continuous`, and `entry_name` | Fastest eligible clear first. Solo templates show one personal best per character; group templates show party runs. |
| `dueling` | `minimum_matches`, default `3` | Highest win percentage, then most wins. Only completed wins/losses count; exactly three matches qualifies at the default threshold. |
| `glory_experience` | None required | Highest glory, then experience, then newest character, matching the original ranking. |

Every panel accepts an optional `title` (1–80 characters) and `limit` (1–50,
default 10). A world can have up to eight panels. For example:

```yaml
leaderboards:
  - type: instance_clear_time
    instance: a-persian-outpost
    title: Fastest Outpost Clears
    limit: 5
    timing: time_control
  - type: glory_experience
```

New worlds default to one glory/experience panel. Omitting `leaderboards` from
a partial update preserves the existing setting; `leaderboards: []` hides all
panels. Configuration is included in world and family exports. Unknown types,
settings, slugs, and invalid thresholds produce save errors. Influence is not
available yet and will become another panel type when that rating exists.

### Completion entry names

Instance panels accept an optional `entry_name` format of 1–160 characters.
The default is `"{{ name }}"`. Available substitutions are:

| Value | Meaning |
| --- | --- |
| `{{ name }}` | Character name saved at completion. |
| `{{ class }}` | Class display name saved at completion, using the world's class label. |

For example, `"{{ name }} the {{ class }}"` displays **Onyx the Hoplite**.
Later class changes, character renames, and class-label edits do not change
that result. The format can be edited later and will use the same saved facts.
For party clears, it formats each participant before joining their names.
The class is omitted from the secondary detail line when it appears in the name;
any faction shown there also comes from the completion snapshot.

Only the two substitutions and literal plain text are supported: no HTML
formatting, filters, expressions, or conditionals. Unknown substitutions and
malformed braces produce a save error. If a completion has no saved class
(including older records and classless characters), a format requesting class
falls back to the saved character name alone. Current class is never used to
guess historical class. This setting is only available for instance panels.

Personal bests still show one result per character. If Onyx later clears faster
as a Tidehunter, that result replaces the Hoplite clear on the board; a slower
clear does not. Both completions remain in history. Class-filtered rankings are
not currently supported.

### Ranking rules

Instance `timing: current` follows the template's current time-control setting.
Timing and solo/group modes are separate categories. Times measure wall-clock
duration, including pauses and absences. Exact ties use a stable character ID
(solo) or the earliest completion (party). When a character has multiple clears
with the same personal-best time, the earliest completion supplies the identity;
equal completion timestamps use the earliest record ID.

Ranked instance attempts require non-builder participants when the timer starts
and when the completion is recorded. Builder `/reset` attempts stay in completion history but are not
ranked. Historical clears recorded before ranking eligibility was captured also
remain in history without being ranked. These checks are not a certification
against every possible form of builder assistance. Deleted and builder
characters are excluded from individual boards. Duel thresholds count lifetime
completed matches within this base world, across its arenas.

Empty panels remain visible with a short explanation. Results are shared and
cached for 30 seconds; a concurrent refresh may briefly show the previous
snapshot. Configuration changes take effect immediately. A leaderboard loading
failure does not block entering the world or choosing a character.

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
not portable across installations. Bare numeric values are ambiguous and are
rejected rather than interpreted as database room ids. This applies to
`starting_room`, `death_room`, and every `death_routing.routes[].destination`.

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
| `entry_message` | text | empty | Optional full-screen introduction shown when entering this world. Empty or whitespace-only text disables it. |
| `entry_message_mode` | `all`, `reveal`, `replace` | `all` | Show all paragraphs immediately, reveal one more every three seconds, or replace the current paragraph every three seconds. |
| `entry_message_conditions` | condition | `{}` | Existing condition DSL controlling whether an instance's introduction is still relevant when returning from the lobby or reloading. |

### Full-Screen Entry Messages

Set these fields in **World > Config > World YAML** for either a base world or
an instance template, then save. Each template owns its own message; instances
do not inherit the base world's introduction. Newly created instances start
with no message. World and world-family exports preserve both settings.

```yaml
kind: world
spec:
  entry_message: |
    You step into the cave. The air is cool and still.

    Somewhere ahead, a faint light flickers.

    Your journey begins here.
  entry_message_mode: reveal
```

Separate paragraphs with blank lines. Text is displayed literally, without HTML
or Markdown formatting. `all` displays the complete message immediately.
`reveal` shows the first paragraph immediately and adds another every three
seconds, keeping earlier paragraphs visible. `replace` uses the same timing but
shows only the current paragraph. The final text stays visible until dismissed;
a single paragraph appears immediately in every mode.

The presentation matches the Alpha cave introduction: an opaque black screen,
centered text, and a click/tap-anywhere prompt. Players can dismiss it at any
time, including during a timed sequence. Enter, Space, and Escape also dismiss
it. Long messages can be scrolled.

For instances, the modal appears on a character's first entry into that run.
Returning to an existing run skips the modal. A lobby return or page reload
prints the instance ID, applicable introduction text, then the room description,
with input immediately available. Blank lines between introduction paragraphs
are preserved in the console. This console replay only appears in the
instance's starting room and when `entry_message_conditions` passes. It displays
text without rerunning entry triggers or their gameplay effects. A real move
back into an instance still runs its normal arrival triggers.

Use the existing condition DSL to stop replaying an outdated introduction:

```yaml
entry_message_conditions:
  all:
    - not:
        quest_active: athens-intro
    - not:
        quest_completed: athens-intro
```

An empty mapping permits the console replay whenever the player is still in the
starting room; `false` disables it. Omitting this field preserves its value.
These conditions affect the return narration, not the first-entry modal.
Ordinary state refreshes and brief socket reconnects replay neither introduction.
Base-world modals retain their existing behavior on game entry and returning
from an instance. The presentation overlay does not pause the world.

To disable a message, save `entry_message: ''`. Omitting the field in a partial
manifest preserves the existing message.

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
| `player_creation` | mapping | `{}` | Per-account character limit, core faction choices, and initial instance routes. |
| `ability_progression` | mapping | `max_known: 8` | Known ability cap and starting abilities. |
| `can_select_gender` | boolean | `true` | If false, new characters use `default_gender`. |
| `default_gender` | choice | `male` | `male`, `female`, or `non_binary`. Also used by mobs without an explicit `gender` override, including in instances. |
| `non_ascii_names` | boolean | `false` | Allows non-ASCII character names. |
| `name_exclusions` | text | empty | Exact character names to reserve in multiplayer worlds; matching ignores case. Separate entries with whitespace, preferably one name per line. |

World Config YAML and full-world exports always include `can_select_gender`
and `default_gender`, even when they use the defaults. Importing into a new
world or reimporting a later revision preserves both settings. Partial edits
leave either setting unchanged when omitted. In family exports, these
character-creation settings appear only on the base world's document.

Use `name_exclusions` to reserve established characters, deities, or other
identities in your setting. The field is a YAML text block, not a YAML list:

```yaml
spec:
  name_exclusions: |
    Heracles
    Herakles
    Hercules
    Leonidas
    Orpheus
    Zeus
```

Each entry matches the whole character name after the normal name processing;
`Zeus` also blocks `ZEUS`, but does not block `Zeuxis`. Enter spelling variants
explicitly. Keep explanations and YAML comments outside the `|` block: inside
it they become literal text, and each whitespace-separated word becomes another
reserved name. Alphabetize and deduplicate longer lists for easier review.
This setting validates submitted names; applying it does not rename or delete
existing characters. A partial manifest containing `name_exclusions` replaces
the entire reserved-name field, so include any existing reservations you want
to retain. Omitting the field preserves it; an empty string clears it.

To cap ordinary characters per account, set this on the base world's World YAML:

```yaml
spec:
  player_creation:
    max_characters_per_account: 1
```

The limit must be a positive integer. Change it to `2` to allow two characters,
or explicitly set it to `null` to restore unlimited creation (the default).
Omitting it from a partial update preserves the current limit. Other creation
policy fields, including introduction routes, are preserved by this partial edit.

Only non-deleted, non-Builder characters count. Characters inside introduction
or other instance runs still consume a slot in their base world. Ordinary
characters owned by staff or builders count normally. Lowering the limit never
removes existing characters; it blocks new creation until the account is below
the cap. Deleting a character frees its slot. If the cap has been reached, the
lobby shows an explanation after the user selects **New character**. The server
also enforces the limit for simultaneous requests.

For progression details, see
[leveling-builder-guide.md](leveling-builder-guide.md).

For faction selection policy, see
[faction-builder-guide.md](faction-builder-guide.md).

For ability progression, see
[ability-builder-guide.md](ability-builder-guide.md).

`ability_progression.max_known` is the player's world-wide known-ability cap.
It remains independent of any Trainer Profile `learning.max_known` quota; a
learn operation must satisfy both. Trainer Profile limits count only known
abilities in that profile's catalog, while the world cap counts the entire
known set.

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

### World Upkeep

Use `spec.upkeep` to configure NPC corpse lifetimes, empty player-corpse
lifetimes, and abandoned-item cleanup. Empty player corpses default to ten
minutes **after first becoming empty**; unrecovered player equipment has no
automatic expiry. Ground items default to one hour. See [World Upkeep](world-upkeep.md)
for the complete typed YAML, instance inheritance, and zone/storage policies.

### Combat, Roaming, And Runtime Rules

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `combat_resolution_interval` | number | `0` | `0` resolves combat immediately; `> 0` auto-advances rounds on that cadence; `-1` requires explicit advancement. |
| `default_roam_chance` | integer 0-100 | `10` | Final fallback percent per heartbeat for mobs with zone/path roaming targets. |
| `never_reload` | boolean | `false` | Skips scheduled spawn-plan reconciliation; set independently for a base world or instance template. |
| `flee_to_unknown_rooms` | boolean | `true` | Base-world rule allowing flight into rooms the player has not explored; instances inherit it. |
| `cross_race_cooldown` | integer 0-2147483647 | `0` | Minutes before a non-builder may log in as a different core faction in the same multiplayer runtime; `0` disables the delay. Local to each base world or instance template. |
| `is_narrative` | boolean | `false` | Narrative worlds disable combat through the manifest apply path. |
| `auto_equip` | boolean | `true` | New equipment is equipped automatically when possible. |
| `players_can_set_title` | boolean | `true` | Allows players to manage their title. |
| `globals_enabled` | boolean | `true` | Enables global command surfaces that depend on world globals. |
| `decay_glory` | boolean | `false` | Enables glory decay behavior where supported. |

`allow_combat` is a stored config field, but it is not directly authored through
`kind: world` manifests. Use `is_narrative: true` for the current manifest path
when a world should be non-combat.

World Config YAML and full-world/family exports always include the applicable
reload, flee, and cross-faction cooldown rules, including default values.
Reimporting preserves them; a partial edit leaves omitted rules unchanged.
`never_reload` affects scheduled reconciliation, including replenishment after
gameplay, rather than preventing the initial population load.

Roaming resolves the first applicable value in this order: a positive
mob-definition `roam_chance`, a non-null spawn-plan `default_roam_chance`, a
non-null roaming-target-zone `default_roam_chance`, then this base-world
default. The mob-definition field retains its legacy `0 = inherit` behavior.
At the plan level, omission or `null` means inherit. At the zone level, a
stored `null` means inherit. At either level, `0` explicitly disables ambient
roaming. Because zone applies are partial updates, omitting the field on an
existing zone preserves its current value; use an explicit `null` to clear that
zone override.

For cohort patrols, the leader's resolved roam chance is rolled once on each
heartbeat. If any live cohort member is in active combat, the cohort does not
roam on that heartbeat; otherwise, present followers move with the leader when
the destination is valid for their roaming target.

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

Every completed player death sets current health and stamina to 1. Energy becomes
1 only if the player still has a positive energy maximum after effects are cleared
and the equipment penalty is applied; otherwise it stays at 0. This applies
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

Base-world Config YAML and full exports always include all three system maps,
with their effective defaults filled in. If you reset a custom system locally,
export again and reimport: the destination receives those defaults instead of
keeping its previous custom configuration. This also preserves equipment
settings that only customize armor suggestions. Instance templates inherit
these systems from the base world and omit the maps from their own documents.

For a partial edit, omitting a system keeps its current value. Supplying
`stats: {}`, `combat: {}`, or `equipment: {}` resets that system to its defaults;
`null` has the same effect. Resetting stats removes custom class profiles and
makes the world classless, subject to the existing reference validation. Invalid
stored systems produce an export error identifying the invalid configuration;
they are never silently excluded from the export.

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
| `death_route` | Removed WR1 faction/distance selector; not accepted in Core manifests or Config API updates. | Remove it from older YAML. Configure `death_routing` with `death_room` as the fallback. |
| `starting_eq` | Removed legacy starter-equipment relation. | Configure `starting_equipment` with item-definition references. |
| `exits_to` | Cross-world instance transfer field; not accepted inside `kind: world`. | Configure it through the family bundle's `world_config.exits_to` link. |
| `can_create_chars` | Destination character-creation closure switch; import leaves it unchanged. | Manage access on the destination; author starting choices through `player_creation`. |
| `autoflee` | Retained compatibility value with no active gameplay consumer. | Not part of world manifests. |
| `has_corpse_decay` | Removed. | Use `spec.upkeep` instead. |
| `death_routing_generation`, `death_routing_source_generation` | Internal destination cache versions. | Import rebuilds routing state; do not copy version numbers. |

## Instance Templates

Instance template worlds only accept local config fields in `kind: world`
manifests:

- identity text: `name`, `short_description`, `description`, `motd`,
  `is_public`
- local rooms: `starting_room`, `death_room`
- local gameplay rules: `never_reload`, `cross_race_cooldown`, and `upkeep` overrides
- local death/PvP/presentation fields: `death_mode`, `death_routing`,
  `death_routing_source`, `death_currency`, `death_currency_penalty`,
  `pvp_mode`, `built_by`, `small_background`, `large_background`,
  `entry_message`, `entry_message_mode`

`death_routing_source` is instance-only. `local` is the default and uses the
instance template's complete routing policy. `base_world` uses the base
world's complete policy and atomically returns the player to the exact base
runtime from which they entered. The instance's local `death_room` remains
required as a transport-integrity fallback, and the instance still owns its
death penalty.

Core systems such as `stats`, `combat`, `equipment`, `ability_progression`,
`leveling_curve`, `starting_level`, `max_level`, `combat_resolution_interval`,
`default_roam_chance`, `flee_to_unknown_rooms`, and `announce_duel_results` are
inherited from the base world.

`clan_registration_cost` and `clan_registration_currency` also belong to the
base world. Registration and renaming inside an instance use the base world's
current fee, including after a family export/import. See
[Death And System Costs](currency-builder-guide.md#death-and-system-costs).

An instance template still owns its zones and spawn plans. Those manifests may
set local `default_roam_chance` values that take precedence over the inherited
world fallback, while a positive shared mob-definition `roam_chance` remains
the highest-precedence value.

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

## Initial Character Instances

Configure `player_creation.instance_routes` in the **base world's** World YAML
to start new characters in private introductions. Each destination must be a
single-player instance template in this base world. Use its stable instance
slug, as shown in world-family exports:

```yaml
kind: world
spec:
  player_creation:
    instance_routes:
      - when:
          eq: [player.core_faction, elves]
        instance: elven-introduction
      - when:
          eq: [player.core_faction, orcs]
        instance: orc-introduction
      - when:
          always: true
        instance: common-introduction
```

The first matching route wins. A final `when: {always: true}` supplies a default;
a single such route sends everyone to one introduction. Without a match, the
character starts in their normal base-world room, including any faction-specific
starting room. Routes run only when a character is created.

`when` uses the shared condition DSL. Conditions can read `player.core_faction`
(the faction code, or `null`), `player.archetype`, `player.gender`, and
`player.level` after the base world's character creation choices and starting
level have been applied. Use `eq`, `ne`, and `in` for comparisons, `gte` and `lte`
for level comparisons, and `all`, `any`, and `not` to combine conditions.
`always` accepts a boolean; a bare boolean condition is also supported. Each
condition mapping has one operator. Values must be literals; live room, mob,
quest, and Trigger state are not available during character creation.

There may be at most 32 routes. Each `in` list accepts 1–64 values, and the
shared condition DSL's size and nesting limits apply. Faction codes must refer
to playable core factions in the base world. Every destination is validated at
save time, including routes that follow a default rule: it must be unarchived,
have a starting room in its template, and have `instance_single_player: true`.
Referenced templates cannot be changed to shared access until their routes are
removed. Instance templates cannot author their own routes.

Set `instance_routes: []` to disable routing. Omitting `instance_routes` in a
partial `player_creation` update preserves existing routes; editing routes
preserves the existing `core_faction` policy. Setting `player_creation: null`
clears both policies. World-family import resolves routes after importing all
templates, and world-family export preserves the slug references.

Each routed character gets a fresh private run. Population and any clear-goal
timer start on first login, and entry-room RP waits for the game connection.
Changing routes affects future characters only. See the
[initial-instance guide](instance-builder-guide.md#start-new-characters-in-a-private-introduction)
for entry triggers, return rooms, and cleanup behavior.
