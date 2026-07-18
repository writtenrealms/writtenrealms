# WR2 YAML Manifest Editing

## Goals

WR2 world editing is moving toward an authored-manifest workflow inspired by Kubernetes:

- builder UI pages show current state
- canonical edit format is YAML
- import/export is straightforward because authored entities can round-trip through manifests

Implemented manifest kinds currently include the current WR2 authoring path:

- `trigger`
- `world`
- `currency`
- `zone`
- `room`
- `path`
- `itemdefinition`
- `itembundle`
- `merchantprofile`
- `craftmaterial`
- `craftingrecipe`
- `craftingprofile`
- `faction`
- `mobdefinition`
- `spawnplan`
- `ability`
- `abilities`
- `quest`
- `questarc`

## Optional WR1 Authored-World Conversion Notes

WR1-to-WR2 export scripts should target the current WR2 manifest contracts, not
the temporary compatibility models. As WR2 legacy concepts are removed, update
this section in the same change so the WR1 exporter can be kept in sync.

WR2 itself starts with a clean, empty database. This exporter is an optional
authored-world conversion tool only: it does not migrate accounts, players,
balances, inventories, quest progress, runtime mobs/items, or any other live
state into WR2.

Current required mappings:

- Emit each WR1 authored currency as `kind: currency` with its portable code,
  then select exactly one `spec.default_currency` in `kind: world`. Because
  Gold was WR1's fixed effective default, a converted WR1 world emits a `gold`
  definition and selects it. Emit `medals` only if authored content references
  the built-in Medals concept; never inspect or export player balances to infer
  currency definitions.
- Map WR1 `starting_gold` to `kind: world`
  `spec.starting_balances.gold`. Map item values to adjacent `cost` and
  `currency`, mob Gold to `spec.rewards.currencies.gold`, merchant
  `funds.currency` to `spec.settlement_currency`, Gold-loss death configuration
  to `death_mode: lose_currency` plus `death_currency` and
  `death_currency_penalty`, and quest `grant_gold` to `grant_currency` with an
  explicit `currency: gold`. Canonical WR2 imports do not accept the old Gold
  fields or effects as aliases.
- Normalize only the known WR1 item-currency enum value `medal` to the built-in
  code `medals`; do not rename an unrelated authored custom code by guesswork.
- Convert representable legacy currency conditions to the existing structured
  condition path `actor.balances.<code>`. Flag ambiguous predicates for builder
  review instead of inventing a second currency condition language.

- WR1 world PvP settings export only as `kind: world`
  `spec.pvp_mode`; do not emit `spec.allow_pvp`. When the source has a valid
  `pvp_mode`, that value wins. Otherwise, map `allow_pvp: true` to
  `pvp_mode: free_for_all` and `allow_pvp: false` to `pvp_mode: disabled`.
  Exporters should audit and flag conflicts where both legacy fields are
  present but disagree (`allow_pvp: false` with `free_for_all` or `zone`, or
  `allow_pvp: true` with `disabled`) rather than preserving both fields.
- Trigger mob reactions target `kind: trigger` with `target.type:
  mobdefinition`, not `mobtemplate`.
- Quest NPC dialogue sources use `mob_definition` / `mob_definition_id`, not
  `mob_template`.
- Quest room pickups and item grant/spawn effects use `item_definition` /
  `item_definition_id`, not `item_template`.
- WR1 `Quest`, `Objective`, and `Reward` rows should export into `kind: quest`
  manifests when they can be represented by the WR2 graph/effect model; WR2 no
  longer has legacy quest CRUD models, serializers, views, or frontend screens.
- WR1 `PlayerQuest` / `PlayerEnquire` runtime rows do not export. WR2 quest
  runtime state is `QuestInstance` / `QuestObjectiveState` / `QuestJournalEntry`
  and room NPC markers use `quest_indicator.available` / `quest_indicator.ready`.
- WR1 `StartingEq` rows export into the `kind: world`
  `spec.starting_equipment` list using WR2 `itemdefinition.<slug>` refs,
  `count`, and optional `archetype`. The optional `equip` field defaults to
  `true`; `equip: false` grants the item into carried inventory without
  equipping it. WR1 starter equipment should retain its existing auto-equip
  behavior by omitting `equip` or emitting `equip: true`. WR2 no longer has a
  `StartingEq` model.
- WR1 `ItemTemplate` rows export as `kind: itemdefinition`; WR2 no longer has an
  `ItemTemplate` model, manifest kind, API endpoint, or runtime item FK.
- WR1 `ItemTemplate.hit_msg_first` and `ItemTemplate.hit_msg_third` export to
  `kind: itemdefinition` fields `spec.hit_msg_first` and
  `spec.hit_msg_third`. Preserve non-empty multiword phrases as authored. Emit
  blank legacy values as `""` so applying over an existing WR2 definition
  clears an old customization; omission defaults only when creating a definition.
- WR1 `MobTemplate` rows export as `kind: mobdefinition`; WR2 no longer has a
  `MobTemplate` model, manifest kind, API endpoint, or runtime mob FK.
- WR1 `MobTemplate.hit_msg_first` and `MobTemplate.hit_msg_third` export to
  `kind: mobdefinition` fields `spec.hit_msg_first` and
  `spec.hit_msg_third`. Preserve non-empty multiword phrases as authored. Emit
  blank legacy values as `""` so applying over an existing WR2 definition
  clears an old customization; omission defaults only when creating a definition.
- WR1 `Loader` / `Rule` rows export as `kind: spawnplan` entries. WR2 no longer
  imports or stores loader/rule rows, and runtime item/mob rows no longer keep
  `rule_id` or source-template FKs.
- WR1 `TransformationTemplate` rows and transformation `Rule` chains do not
  export as a WR2 model or manifest kind. They only overlaid serialized mob
  fields and did not mutate canonical runtime state. Exporters must report every
  use for builder review instead of translating it automatically. If a builder
  intentionally wants canonical WR2 behavior, they may replace a fixed field
  variation with a dedicated `kind: mobdefinition` variant or a supported
  numeric spawn variation with
  `spec.entries[].traits.guaranteed[].modifiers`. Direct modifiers add and
  `_multiplier` modifiers multiply, and both mutate persisted runtime state.
  Report and omit arbitrary strings, unsupported attributes, and every
  unreviewed or non-equivalent transformation; do not recreate transformation
  templates, nested rule targets, or arbitrary attribute mutation.
- WR1 loader reset configuration does not export. WR2 spawn-plan manifests have
  no `spec.reset` key: world/instance lifecycle services perform initial
  population, while `spec.respawn` controls replacement of missing placements
  in a running world.
- WR1 `Zone.is_warzone` does not export. WR2 zones no longer have an
  `is_warzone` model field or zone manifest key.
- Runtime spawn reconciliation is now named spawn-plan processing in WR2:
  Celery schedules `worlds.tasks.run_world_spawn_plans`, the world timestamp is
  `last_spawn_plan_run_ts`, and the removed system endpoint
  `/game/system/run_loaders/` has no WR2 replacement.
- Builder read-only spawn-plan inspection APIs are `/zones/<id>/spawn-plans/`
  and `/rooms/<id>/spawn-plans/`; do not export/import or call the removed
  `/loads/` endpoints.
- WR1 door keys export as WR2 `itemdefinition.<slug>` refs; `Door.key` now points
  to `ItemDefinition`.
- WR1 room or loader-authored room inventory exports as `kind: spawnplan`
  entries targeting WR2 room refs and `itemdefinition` / `itembundle` refs.
- WR1 `RandomItemProfile` rows do not export as a WR2 model or manifest kind.
  Rewrite each authored reference into explicit `kind: itemdefinition`
  documents, using `spec.randomization` only for supported authored attribute
  ranges, and a `kind: itembundle` when discrete weighted choice is intended.
  Giver-relative levels (`level: 0`), broad procedural equipment restrictions,
  and imbued/enchanted chance generation have no semantics-preserving automatic
  mapping; exporters must flag those references for author review. Never
  restore a compatibility table or runtime adapter, and never export runtime
  `Item.profile` provenance.
- WR1 procedural drop-generation requests and generated runtime items do not
  export. WR2 has no `/game/system/generate/drops/` endpoint; authored random
  loot must resolve through item definitions, item bundles, mob loot, merchant
  profiles, or spawn plans before import.
- WR1 item template inventory rows do not export as `itemtemplate`
  `spec.inventory`; WR2 no longer has `ItemTemplateInventory`. Nested/container
  contents should target WR2 item definition manifests once a definition-backed
  container inventory contract exists.
- WR1 mob template inventory rows do not export as `mobtemplate`
  `spec.inventory`; WR2 no longer has `MobTemplateInventory`. Carried, equipped,
  loot, and merchant-stock semantics should be split into WR2 mob definition,
  loot/item bundle, equipment, and merchant profile manifests rather than
  recreated as template inventory.
- WR1 merchant mob-template settings and inventory export into `kind:
  merchantprofile` plus `MobDefinition.merchant_profile`; WR2 no longer has
  `MerchantInventory`, mob `merchant_profit`, or the
  `/game/system/update_merchants/` endpoint.
- WR1 crafter/upgrader mob-template flags and item `upgrade_count` do not
  export. WR2 crafting uses `craftmaterial`, `craftingrecipe`, and
  `craftingprofile` manifests, item-definition `spec.salvage`, and an optional
  room or mob-definition `spec.crafting` attachment. Exporters must translate
  intentional legacy recipes into those contracts rather than restoring WR1
  flags, system endpoints, workshop flags, or upgrade counters. The replacement
  is documented in [crafting-system.md](crafting-system.md).
- Quest tracker conditions compare `event.target.definition_id` and
  `event.item.definition_id` to `mobdefinition` / `itemdefinition` refs.
- The shared condition DSL resolves typed refs such as
  `mobdefinition.guard_captain` and `itemdefinition.saloon_keg` for
  `.definition_id` paths.
- WR1 `mob_in_room <numeric_definition_id>` conditions should export as the
  structured WR2 condition `mob_present: mobdefinition.<slug>`. When a policy
  should block movement while that mob exists, wrap the condition in `not` so
  the policy passes only while the mob is absent.
- WR2 has no `RoomCheck`, `RoomCommandCheck`, or `RoomCommandCheckState` model,
  API, runtime payload, or builder screen. These WR1 rows are exporter input
  only; do not recreate them as a WR2 manifest kind or compatibility table.
- WR1 `RoomCheck` rows export as room-scoped `kind: trigger` documents with
  `spec.kind: policy`. Use the checked room's portable `room@x,y,z` ref as the
  full-world export target (`spec.target: {type: room, ref: room@x,y,z}`). Map
  `prevent: enter` to the `before_move_enter` event, `prevent: exit` to the
  `before_move_exit` event, and `prevent: all` to two policy documents, one for
  each event. Copy a non-empty direction to `spec.match`, `failure_msg` to
  `spec.failure_message`, and preserve deterministic source order in
  `spec.order`.
- A policy condition describes when movement is **allowed**, while a WR1 room
  check describes the case that prevents movement. Exporters must therefore
  invert the WR1 blocking predicate. Current direct mappings are:
  - `mob_is_present <mob_id>` -> `not: {mob_present:
    mobdefinition.<slug>}`.
  - `faction_below <faction> <standing>` -> `gte:
    [actor.factions.<faction_code>, <standing>]` when a missing assignment and
    the source threshold retain the same result; flag edge cases instead of
    changing WR1's missing-standing behavior.
  - `quest_incomplete <quest_id>` -> `quest_completed: <quest_slug>`.
  - `quest_complete <quest_id>` -> `not: {quest_completed: <quest_slug>}`.
  Resolve every numeric WR1 id against the source export and emit portable
  slugs/refs. Never copy a numeric definition, quest, or room id into portable
  WR2 YAML.
- Flag WR1 room-check `in_inv`, `not_in_inv`, `equipped`, `not_equipped`, and
  `health_below` rows until the structured WR2 condition DSL has equivalent
  inventory/equipment membership and health-percentage predicates. Also flag
  `argument2` exemptions and any legacy free-form `conditions` expression that
  cannot be translated with identical polarity. Do not fall back to a new
  room-check vocabulary or silently drop part of a predicate.
- WR1 `RoomCommandCheck` has no semantics-preserving automatic mapping yet.
  Its allow/disallow lists veto already recognized commands, whereas a WR2
  `kind: command` trigger handles an authored matched command and does not wrap
  every resolved command handler. Exporters must report and omit these rows
  until WR2 has a `before_command` policy hook. Do not copy
  `allow_commands`/`disallow_commands` into `spec.match`. `check_type:
  cmd_issued`, `track_state`, and `hint_msg` likewise require explicit redesign
  and must be reported as unsupported. If the WR1 content was actually meant
  to introduce a custom room verb rather than veto a core command, an author
  may replace it with a separate room-scoped `kind: command` trigger and an
  explicit script; the check row alone does not contain enough behavior to
  generate that trigger safely.
- Room-check conversion belongs in the WR1 manifest exporter, not in a WR2
  database migration. WR2 imports the resulting trigger documents into a fresh
  world and never stores the legacy rows.
- WR1 room-action `transfer {{ actor }} <numeric_room_id>` scripts should
  export as `/cmd room -- /transfer {{ actor_key }} room@x,y,z`. Resolve the
  legacy room id to the imported room's coordinates; never copy a WR1 or WR2
  database id into portable trigger YAML. Normalize slashless `transfer` to
  `/transfer`. For mob-authored scripts, keep the mob as issuer and use the same
  portable destination. WR1's optional trailing transfer command does not map
  directly: export it as an explicit command before `/transfer`. For immediate
  timing, use same-line `&&` segments and repeat the ambient wrapper for every
  segment, for example `/cmd room -- /send ... && /cmd room -- /transfer ...`.
  Separate script lines are heartbeat-paced and are not equivalent. WR2 still
  emits the standard disappearance notification, whereas WR1 suppressed that
  text when a trailing command was present, so exporters should flag those
  scripts for author review. WR1 could also transfer a local floor item even
  though its help advertised only players and mobs; WR2 `/transfer` deliberately
  accepts character targets only, so exporters must flag item-target scripts
  instead of silently rewriting them.

Builder-facing authoring guidance lives in:

- [docs/guides/currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md)
- [docs/guides/world-config-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/world-config-builder-guide.md)
- [docs/guides/trigger-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/trigger-builder-guide.md)
- [docs/guides/builder-command-reference.md](/Users/teebes/code/writtenrealms/docs/guides/builder-command-reference.md)
- [docs/guides/combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md)
- [docs/guides/leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md)
- [docs/guides/spawn-plan-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/spawn-plan-builder-guide.md)
- [docs/guides/mob-trait-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-trait-builder-guide.md)
- [docs/guides/room-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/room-builder-guide.md)

## Current Flows

### 1. World Config Screen

In **World > Config**, configuration is read-oriented:

- all configurable world values are shown read-only
- the page can reveal the current **World Config YAML**
- the page supports **Copy Config YAML**
- edits happen through **World > Edit World** by applying one or more YAML manifests

### 2. Room Triggers Screen

In room navigation, **Triggers** now replaces **Actions**.

- It lists room-scoped triggers for the selected room.
- It includes a room-tailored **New Room Trigger Template** YAML block.
- Each trigger displays its YAML definition.
- Each trigger includes **Copy YAML** and **Copy Delete YAML** actions.
- Recommended workflow: copy template YAML, tweak it, ingest in **Edit World**.

### 3. Room Edit Screen

The former room **Checks** navigation slot is now **Edit**. In **Rooms > Edit**,
the selected room's current `kind: room` manifest is loaded into the shared YAML
editor used by other manifest-authored definitions. YAML and its related
collections are fetched only when this screen opens, keeping ordinary map and
room-selection payloads lean.

- **Save YAML** applies the document through the world manifest endpoint.
- A successful save reloads the selected room and its canonical YAML.
- The room manifest edits room identity/display fields, zone, description,
  notes, type, color, landmark state, exits, flags, details, and doors.
- Triggers remain separate `kind: trigger` documents under
  **Rooms > Triggers**; they are not nested inside the room manifest.
- Room checks are not exposed because they are not part of WR2.

### 4. Item Definition Details Screen

In **World > Items**, the item definition detail screen can expose the current
item definition as YAML.

- It includes **Copy YAML** for the selected item definition.
- New authored items should use `kind: itemdefinition`.
- Recommended workflow: copy the YAML, edit it, then ingest it in
  **World > Edit World**.

### 5. Spawn Plan Screens

Spawn plans are authored through `kind: spawnplan` YAML in **World > Edit World**.
Room **Spawn Plans** is a read-only view of spawn plans targeting that room.

- The list is backed by `SpawnPlan`, not legacy `Loader` rows.
- Zone API responses expose both `relative_id` and `manifest_ref`; manifests
  should use the `manifest_ref` value, such as `zone@1`.
- Zone detail screens expose copy actions for the zone apply YAML and delete
  YAML. Use the apply YAML to edit fields such as `metadata.name`, then paste
  it into **World > Edit World**.
- Path API responses also expose `relative_id` and `manifest_ref`; spawn-plan
  path targets should use `path@<relative_id>`, not path names.

### 6. World Edit Screen

A new world-level **Edit World** view accepts a YAML manifest textarea.

- Submitting YAML currently supports one or more YAML documents in sequence.
- Supported kinds:
  - `kind: world`
  - `kind: currency`
  - `kind: zone`
  - `kind: room`
  - `kind: path`
  - `kind: itemdefinition`
  - `kind: itembundle`
  - `kind: merchantprofile`
  - `kind: craftmaterial`
  - `kind: craftingrecipe`
  - `kind: craftingprofile`
  - `kind: faction`
  - `kind: mobdefinition`
  - `kind: spawnplan`
  - `kind: ability`
  - `kind: abilities`
  - `kind: quest`
  - `kind: questarc`
  - `kind: trigger`
  - `kind` is case-insensitive (`trigger`, `Trigger`, `TRIGGER` all work).
- Trigger manifests now support both:
  - **create** (no `metadata.id` / `metadata.key`)
  - **update** (include `metadata.id` or `metadata.key`)
  - **delete** (`operation: delete` with `metadata.id` or `metadata.key`)
- Zone manifests support **apply** for create/update and **delete**
  (`operation: delete` with `metadata.ref`). Zone manifests no longer include
  legacy `spec.is_warzone`; use `spec.pvp_zone` only for authored PvP zone
  behavior.

### 7. Currency Screen

**World > Currencies** reads the base world's inherited catalog. Root-world
builders can create currencies, edit display fields and starting amounts,
select the single default, inspect deletion blockers, and copy canonical apply
or delete YAML. Instance currency views are inherited/read-only. The same
builder services enforce identity, lifecycle, default, starting-balance, and
deletion rules for REST and manifests.

Zone manifests exported by the system include `metadata.ref` in the portable
form `zone@<relative_id>`. Path manifests use `metadata.ref` in the portable
form `path@<relative_id>`. Spawn plans and exported room/path manifests use
those portable refs instead of names or database ids, so duplicate names do not
make imports ambiguous and prod/dev database ids do not need to match.

Quest authoring details, including field-by-field manifest docs and current
runtime behavior notes, live in:

- [docs/guides/quest-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/quest-builder-guide.md)
- [docs/guides/quest-reference.md](/Users/teebes/code/writtenrealms/docs/guides/quest-reference.md)

Item definition authoring details, including stackable plain items, fixed stat
items, randomized stat items, and item bundles, live in:

- [docs/guides/item-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/item-definition-builder-guide.md)

Merchant authoring details, including fixed stock, item-bundle stock, buyback,
finite funds, and killable versus non-killable shopkeepers, live in:

- [docs/guides/merchant-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/merchant-builder-guide.md)

Mob definition authoring details, including plain mobs, fixed stat mobs, and
randomized stat mobs, live in:

- [docs/guides/mob-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-definition-builder-guide.md)

Currency definitions, defaults, starting balances, prices, rewards, policies,
and conditions are documented in:

- [docs/guides/currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md)

Spawn plan authoring details, including fixed room spawns, weighted source
pools, guided dungeon density, spawn-plan trait/affix configuration, and
respawn behavior, live in:

- [docs/guides/spawn-plan-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/spawn-plan-builder-guide.md)

Instance architecture, including inherited base-world content, instance-local
layout/config overrides, goals, timers, leaderboards, and cleanup policy, lives
in:

- [docs/architecture/instance-system.md](/Users/teebes/code/writtenrealms/docs/architecture/instance-system.md)
- [docs/guides/instance-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/instance-builder-guide.md)

Mob trait architecture, including the rename from the earlier draft term
`affixes` to `traits`, lives in:

- [docs/architecture/mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md)

Attack routine and dual-wielding architecture, including proposed manifest
ownership for extra attacks and offhand weapon permissions, lives in:

- [docs/architecture/attack-routines-and-dual-wielding.md](/Users/teebes/code/writtenrealms/docs/architecture/attack-routines-and-dual-wielding.md)

Builder-facing attack routine and dual-wielding authoring guidance lives in:

- [docs/guides/attack-routine-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/attack-routine-builder-guide.md)

Builder-facing mob trait authoring guidance lives in:

- [docs/guides/mob-trait-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-trait-builder-guide.md)

## Trigger Manifest Shapes

### Create Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.10
  match: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
  conditions: level 1
  show_details_on_failure: true
  failure_message: Not yet.
  display_action_in_room: true
  gate_delay: 5
  order: 7
  is_active: true
```

### Create Mob Event Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Greeter Reaction
spec:
  scope: world
  kind: event
  target:
    type: mobdefinition
    key: mobdefinition.22
  event: say
  match: hello and (traveler or friend)
  script: say Welcome to the archive.
  conditions: ""
  display_action_in_room: false
  gate_delay: 10
  order: 0
  is_active: true
```

### Create Room Movement Policy Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Warlord Gate
spec:
  scope: room
  kind: policy
  target:
    type: room
    key: room.10
  event: before_move_enter
  conditions:
    eq:
      - actor.archetype
      - warlord
  failure_message: Only warlords may enter.
  order: 0
  is_active: true
```

### Create Room Movement Event Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Spear Trap
spec:
  scope: room
  kind: event
  target:
    type: room
    key: room.10
  event: after_move_enter
  conditions:
    not:
      eq:
        - state.room.trap_sprung
        - true
  script: |
    /cmd room -- /echo -- Spears snap out from the walls.
    /cmd room -- /state set room trap_sprung true
  display_action_in_room: false
  gate_delay: 0
  order: 0
  is_active: true
```

### Update Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  id: 42
  key: trigger.42
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.10
  match: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
```

### Multi-line `script`

`spec.script` accepts YAML block strings (multi-line).

Runtime behavior details are documented in:

- `docs/trigger-multiline-script-execution.md`
- `docs/trigger-event-subscriptions.md`
- `docs/trigger-matching-dsl.md`

Execution behavior:

- first script line runs immediately
- each following line runs after a fixed delay from the previous line
- default delay is `2` seconds, configured via
  `backend/config/game_settings.py` (`GAME_HEARTBEAT_INTERVAL_SECONDS`)

```yaml
kind: trigger
metadata:
  world: world.1
  id: 42
spec:
  script: |
    /cmd room -- /echo -- The lever clicks.
    /cmd room -- /echo -- Dust falls from the ceiling.
    /cmd room -- /echo -- A hidden door slides open.
```

### Delete Trigger

```yaml
kind: trigger
operation: delete
metadata:
  world: world.1
  id: 42
```

## Room Manifest Shape

Rooms use coordinate refs as their portable identity. The selected room's
**Rooms > Edit** screen exposes this complete shape:

```yaml
kind: room
metadata:
  ref: room@10,4,0
  name: North Gate
spec:
  zone: zone@2
  description: An ironbound gate closes the northern road.
  note: Builder-only note.
  type: road
  color: "#8a8175"
  is_landmark: true
  exits:
    north: room@10,5,0
    east: null
    south: room@10,3,0
    west: null
    up: null
    down: null
  flags:
    - no_roam
  details:
    - keywords: gate ironbound
      description: Rivets run in black rows across the gate.
      is_hidden: false
  doors:
    - direction: north
      name: ironbound gate
      to_room: room@10,5,0
      key: itemdefinition.north-gate-key
      destroy_key: false
      default_state: locked
```

Room manifests currently support `operation: apply` only. Preserve
`metadata.ref` when editing an existing room: changing the coordinate ref means
"apply a room at these other coordinates," not "rename this room's id."
Including `flags`, `details`, or `doors` replaces that complete collection for
the room. The canonical YAML shown after a save includes every exit direction,
so copy/edit/save round trips do not depend on hidden form state.

Room triggers are separate documents. Use `kind: trigger` with a room target;
do not add a `checks`, `room_checks`, or `triggers` key to `kind: room`.

## Currency And Economy Manifest Shape

Currency definitions use immutable lowercase codes as their portable identity:

```yaml
kind: currency
metadata:
  code: obol
spec:
  name: Obol
  plural_name: Obols
  description: The common coin of Phalanx.
```

The first currency created for a defaultless world becomes its default. The
world document remains the authoritative place to select the default and set
starting balances:

```yaml
kind: world
spec:
  default_currency: obol
  starting_balances:
    obol: 12
```

`starting_balances` is an exact replacement mapping. Omitted currencies and
explicit zero entries both mean a zero starting balance; canonical export omits
zero rows. Amounts must be integers from `0` through
`9,007,199,254,740,991`. Changing the default does not convert balances or
retarget already-authored prices and rewards.

Money-bearing manifests persist the concrete code next to the amount:

```yaml
kind: itemdefinition
metadata:
  slug: bronze-knife
  name: a bronze knife
spec:
  type: equippable
  cost: 18
  currency: obol
---
kind: mobdefinition
metadata:
  slug: road-raider
  name: a road raider
spec:
  rewards:
    currencies:
      obol: 4
```

See [currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md)
for currency deletion, item, merchant, quest, death-policy, and condition
examples.

## World Config Manifest Shape

World config edits are update-only manifests (no create/delete mode). The config screen and the full world export emit the same single world document shape:

For a full field reference, see
[world-config-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/world-config-builder-guide.md).

```yaml
kind: world
spec:
  name: Edeus
  short_description: Core setting text
  description: Long world description
  motd: Questions? Join Discord.
  is_public: true
  default_currency: crowns
  starting_balances:
    crowns: 0
  starting_equipment:
    - item_definition: itemdefinition.training_spear
      count: 1
      archetype: hoplite
      equip: false
    - item_definition: itemdefinition.training_sword
      count: 1
      archetype: hoplite
    - item_definition: itemdefinition.training_shield
      count: 1
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
  starting_level: 1
  max_level: 5
  leveling_curve:
    - 0
    - 30
    - 100
    - 400
    - 1000
  combat_resolution_interval: 0
  default_roam_chance: 10
  starting_room: room@0,0,0
  death_room: room@10,0,0
  death_mode: lose_currency
  death_currency: crowns
  death_currency_penalty: 0.2
  death_route: nearest_in_zone
  pvp_mode: zone
  player_creation:
    core_faction:
      mode: choose_required
      default: human
      options:
        - human
        - elf
  auto_equip: true
  is_narrative: false
  players_can_set_title: true
  non_ascii_names: false
  globals_enabled: true
  decay_glory: false
  built_by: Team WR
  small_background: https://assets.example/card.png
  large_background: https://assets.example/banner.png
  name_exclusions: |
    admin
    moderator
```

`combat_resolution_interval` is the world-level encounter pacing knob, in
seconds:

- `> 0`: auto-advance combat encounters on that cadence
- `0`: resolve combat immediately
- `-1`: do not auto-advance combat encounters

Current status: this field is the authored WR2 contract for encounter pacing,
and the current placeholder `kill <mob>` combat flow now honors it:

- `0`: immediate full auto-resolve
- `> 0`: scheduled round-by-round resolution on that cadence
- `-1`: manual round-by-round resolution, advanced by explicit `kill <mob>`
  commands

Broader encounter scheduling, queued abilities, and non-basic combat actions are
still future work.

`default_roam_chance` is the percent chance that a mob with a zone or path
roaming target moves on each WR2 heartbeat. The default is `10`, matching the
old WR1 `ROAM_CHANCE`. Set it to `0` to disable default ambient roaming.
Mobs loaded into a fixed room have no roaming target and stay static unless a
future explicit behavior system moves them.

`starting_level`, `max_level`, and `leveling_curve` control player progression.
`leveling_curve` is a cumulative XP threshold list where the first entry is
level 1 and must be `0`; for example, the second entry is the XP required to
reach level 2. `max_level` cannot be higher than the number of curve entries.
The example above defines five reachable levels; a 20-level world needs 20
entries.
See [leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md).

`starting_equipment` grants item definitions during character initialization.
`count` defaults to `1`, `archetype` limits an entry to one class id, and `equip`
defaults to `true`. Set `equip: false` for alternate weapons or other equippable
items that should begin in carried inventory instead of occupying an equipment
slot. `ability_progression.starting_abilities` supports the same class-specific
outcome through shared WR2 conditions, as shown above.

World manifests now also support `spec.stats`, which holds the authored WR2
stat system for that world:

- attribute definitions
- resource and stat labels
- class or archetype profiles
- bounded formula rules

New worlds do not get authored attributes by default. Blank worlds do include
minimal stamina defaults so a new character can move and regenerate stamina.
Builders add only the attributes they want, then map those attributes into stats.
Class selection is implied by `spec.stats.class_profiles`: if no class profiles
are defined, the world has no classes.

For details and examples, see:

- [stats-formulas-and-classes.md](/Users/teebes/code/writtenrealms/docs/architecture/stats-formulas-and-classes.md)
- [attributes-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/attributes-builder-guide.md)
- [wr1-archetype-world-reference.md](/Users/teebes/code/writtenrealms/docs/dev/wr1-archetype-world-reference.md)

World manifests also support `spec.combat`, which holds the authored WR2
combat formula system:

- named attack/healing profiles
- level scaling for rating curves and unarmed mob fallback damage
- rating curves for dodge, crit, armor, and resilience
- weapon damage, attack power, and ability power scaling
- mitigation rules for physical and ability damage
- variance, crit multiplier, and minimum output rules

For details, see:

- [combat-resolution-formulas.md](/Users/teebes/code/writtenrealms/docs/architecture/combat-resolution-formulas.md)
- [combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md)

## `apiVersion`

- `apiVersion` is optional for manifests.
- If provided, accepted values are:
  - `v1alpha1`
  - `writtenrealms.com/v1alpha1` (legacy-compatible)
- For approachability, the exported YAML omits `apiVersion` by default.

## `metadata.id` vs `metadata.key`

### What they are

- `metadata.id`: numeric DB identifier (`42`)
- `metadata.key`: typed string key (`trigger.42`)

### How they are used today

- Both are accepted as trigger identity for updates.
- If both are present, they must refer to the same trigger.
- If neither is present, ingestion creates a new trigger.

### Is `key` WR1 cruft?

No, but its role should be narrow and explicit in WR2:

- `key` is still useful as a typed reference format across entities (`room.10`, `zone.3`, `trigger.42`) and is already widely used in builder/game payloads.
- `id` is simpler for update targeting.
- For WR2 manifests, treat `id` as the primary update identifier and `key` as an interoperability/reference-friendly alias.

Long term, if we want portable manifests across worlds/environments, neither raw `id` nor `trigger.<id>` is ideal alone; we should add stable authored identifiers (for example `metadata.slug` or `metadata.uid`) and map those at import time.

## Is `kind: trigger` redundant with `key: trigger.42`?

Partially, yes. They validate different things:

- `kind` selects the manifest parser/contract and is case-insensitive.
- `key` (or `id`) identifies one concrete instance.

Keeping both is still useful because:

- `kind` allows generic ingestion dispatch before touching IDs.
- `key` keeps typed references consistent with other entity refs.

If we eventually move to `metadata.id` only for updates, `kind` remains required.

## Validation Rules (Current)

- `kind` must resolve to `trigger`, `world`, `currency`, `zone`, `room`, `path`, `itemdefinition`, `itembundle`, `merchantprofile`, `faction`, `mobdefinition`, `spawnplan`, `ability`, `abilities`, `quest`, or `questarc`.
- For update: `metadata.id` or `metadata.key` must reference an existing trigger in the selected world.
- For create: omit both `metadata.id` and `metadata.key`.
- For delete: set `operation: delete` and include `metadata.id` or `metadata.key`.
- `metadata.world` (if present) must match the selected world.
  - `metadata.world` accepts either integer id (`1`) or key form (`world.1`).
- `spec.scope`, `spec.kind`, booleans, and integers are validated.
- `spec.match` matcher syntax is validated using the Trigger Matching DSL.
- For create:
  - `spec.scope` is required.
  - `spec.target` is required for room/zone scope.
- For `spec.kind: event`:
  - `spec.event` is required.
  - mob reaction events such as `say` use `scope: world` and a `mobdefinition`
    target.
  - room events such as `after_move_enter`, `after_move_exit`, and
    `after_death_room_enter` use `scope: room` and a `room` target.
- For `spec.kind: policy`:
  - `spec.event` is required.
  - `spec.event` must be `before_move_enter` or `before_move_exit`.
  - v1 policy triggers use `scope: room` and a `room` target.
- For command triggers, `spec.target` must match scope type (`room`, `zone`, `world`) and exist in world.
- For event triggers, `spec.target.type` must match the event family and exist in world.
- structured `conditions` are validated through the shared WR2 condition DSL in
  `backend/core/condition_dsl.py`; legacy trigger text conditions still pass
  through `backend/core/conditions.py`.
- For world config manifests:
  - only `operation: apply` is supported
  - `spec` fields are validated against the world schema
  - room references (`starting_room`, `death_room`) must resolve to rooms in the selected world
  - `default_currency`, `starting_balances`, `death_currency`, and
    `clan_registration_currency` resolve against the base-world catalog
  - currency amounts reject booleans, fractions, negatives, and values above
    `9,007,199,254,740,991`
  - `pvp_mode` is the canonical PvP field; legacy `allow_pvp` is accepted only
    as an import alias and must not conflict when both fields are present
- Currency codes match `[a-z][a-z0-9_-]{0,63}`, are unique ignoring case per
  base world, and cannot be changed after creation. Instance worlds inherit
  currencies and cannot author their own definitions/default/starting balances.
- `spec.cost` without `spec.currency` resolves the default on item creation and
  stores that concrete relation. `spec.currency` without `spec.cost` is invalid.
- Mob rewards use `spec.rewards.currencies.<code>`, merchant profiles use
  `spec.settlement_currency`, and quest rewards use `type: grant_currency` with
  explicit `currency` and `amount`.

Permission checks are applied when editing via manifest:

- rank 3+ builders can edit all trigger scopes
- rank 1-2 builders can edit room/zone targets only when assigned
- rank 1-2 builders cannot edit world-scoped triggers
- rank 1-2 builders cannot edit world config manifests (`world`)

## Implementation Notes

- Manifest helpers live in `backend/builders/manifests.py`.
- World config read/export endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/config/`
- Selected-room manifest endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<pk>/manifest/`
  - Django route name: `builder-room-manifest`
  - this is loaded on demand by **Rooms > Edit**; ordinary room/map payloads
    do not carry YAML
- Trigger list + YAML serialization endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<room_pk>/triggers/`
- Manifest apply endpoint:
  - `POST /api/v1/builder/worlds/<world_pk>/manifests/apply/`
  - trigger returns `operation: created`, `operation: updated`, or `operation: deleted`
  - zone returns `operation: created`, `operation: updated`, or `operation: deleted`
  - world config returns `operation: updated`

## How To Edit World Config

1. Open **World > Config**.
2. Click **Copy Config YAML** (or show YAML and copy manually).
3. Open **World > Edit World**.
4. Paste the YAML and edit desired `spec` fields.
5. Submit manifest.
6. Verify response indicates `kind: world` and `operation: updated`.

## How To Add A New Trigger (Builder Workflow)

1. Open room **Triggers** view.
2. Copy YAML from an existing trigger if you want a template.
3. In **Edit World**, paste YAML and remove `metadata.id`/`metadata.key`.
4. Update `metadata.name`, `spec.target`, `spec.match`, `spec.script`, etc.
5. Submit manifest.
6. Verify response indicates `operation: created`.
7. Refresh room Triggers view and confirm new trigger appears.

## How To Edit A Room

1. Open the room in the world editor.
2. Select **Rooms > Edit**.
3. Edit the loaded `kind: room` YAML while preserving `metadata.ref`.
4. Select **Save YAML**.
5. Confirm the success message and review the reloaded canonical YAML.
6. Use **Rooms > Triggers** for movement policies or room commands; those are
   separate `kind: trigger` documents.

## How To Edit An Existing Trigger

1. Open room **Triggers** view.
2. Copy YAML for the trigger.
3. Keep `metadata.id` (and optionally `metadata.key`) intact.
4. Modify only the fields you want to change in `spec` (partial updates are supported).
5. Submit manifest.
6. Verify response indicates `operation: updated`.

### Minimal Patch Example

```yaml
kind: trigger
metadata:
  world: 1
  id: 42
spec:
  match: new action
```

## How To Delete A Trigger

1. Open room **Triggers** view.
2. Use **Copy Delete YAML** on the trigger.
3. In **Edit World**, paste the delete manifest.
4. Submit manifest.
5. Verify response indicates `operation: deleted`.

## Guidelines For Extending To Other Entities

When adding YAML support for another entity (ItemDefinition, MobDefinition, Quest, etc.):

1. Add serializer/parser/apply helpers in `backend/builders/manifests.py` (or a sibling module per domain if it grows large).
2. Support both create and update semantics up front:
   - create when identity fields are omitted
   - update when identity fields are present
3. Keep one stable manifest contract per `kind` with:
   - `apiVersion`
   - `kind`
   - `metadata`
   - `spec`
4. Make UI pages read-oriented first (state visibility), then use World Edit for writes.
5. Enforce strict world/target validation to avoid cross-world edits.
6. Keep permission checks at apply time, based on entity scope.
7. Add round-trip tests:
   - list/export includes YAML
   - apply can create
   - apply updates expected entity
   - permission gate behavior
8. Prefer additive evolution (`apiVersion` bumps, new optional fields) over breaking format changes.
