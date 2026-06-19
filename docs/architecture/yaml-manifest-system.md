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
- `mobdefinition`
- `spawnplan`
- `ability`
- `abilities`
- `quest`
- `questarc`

The legacy `itemtemplate` and `mobtemplate` kinds are still supported during the
transition for old content and legacy-only builder surfaces.

Builder-facing trigger authoring guidance lives in:

- [docs/guides/trigger-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/trigger-builder-guide.md)
- [docs/guides/builder-command-reference.md](/Users/teebes/code/writtenrealms/docs/guides/builder-command-reference.md)
- [docs/guides/combat-formula-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/combat-formula-builder-guide.md)
- [docs/guides/leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md)
- [docs/guides/spawn-plan-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/spawn-plan-builder-guide.md)
- [docs/guides/mob-trait-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-trait-builder-guide.md)

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

### 3. Item Definition Details Screen

In **World > Items**, the item definition detail screen can expose the current
item definition as YAML.

- It includes **Copy YAML** for the selected item definition.
- New authored items should use `kind: itemdefinition`.
- Recommended workflow: copy the YAML, edit it, then ingest it in
  **World > Edit World**.

The legacy **Item Templates** screens still expose `kind: itemtemplate` YAML for
old content and legacy-only surfaces.

### 4. Zone Loads Screen

In zone navigation, **Loads** lists manifest-backed spawn plans for that zone.

- The list is backed by `SpawnPlan`, not legacy `Loader` rows.
- Each spawn plan opens a YAML edit screen at
  `/build/worlds/<world_id>/zones/<zone_id>/loaders/<spawn_plan_id>`.
- The **Add** action opens a new spawn-plan YAML template for the current zone.
- Zone API responses expose both `relative_id` and `manifest_ref`; manifests
  should use the `manifest_ref` value, such as `zone@1`.
- Path API responses also expose `relative_id` and `manifest_ref`; spawn-plan
  path targets should use `path@<relative_id>`, not path names.

### 5. World Edit Screen

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
  - `kind: mobdefinition`
  - `kind: spawnplan`
  - `kind: ability`
  - `kind: abilities`
  - `kind: quest`
  - `kind: questarc`
  - `kind: trigger`
  - `kind: itemtemplate` for legacy item-template content
  - `kind: mobtemplate` for legacy mob-template content
  - `kind` is case-insensitive (`trigger`, `Trigger`, `TRIGGER` all work).
- Trigger manifests now support both:
  - **create** (no `metadata.id` / `metadata.key`)
  - **update** (include `metadata.id` or `metadata.key`)
  - **delete** (`operation: delete` with `metadata.id` or `metadata.key`)

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

Spawn plan authoring details, including fixed room spawns, weighted source
pools, guided dungeon density, spawn-plan trait/affix configuration, and
respawn behavior, live in:

- [docs/guides/spawn-plan-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/spawn-plan-builder-guide.md)

Mob trait architecture, including the rename from the earlier draft term
`affixes` to `traits`, lives in:

- [docs/architecture/mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md)

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
    type: mobtemplate
    key: mobtemplate.22
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

## World Config Manifest Shape

World config edits are update-only manifests (no create/delete mode). The config screen and the full world export emit the same single world document shape:

```yaml
kind: world
spec:
  name: Edeus
  short_description: Core setting text
  description: Long world description
  motd: Questions? Join Discord.
  is_public: true
  starting_gold: 0
  starting_level: 1
  max_level: 5
  leveling_curve:
    - 0
    - 30
    - 100
    - 400
    - 1000
  combat_resolution_interval: 0
  starting_room: room@0,0,0
  death_room: room@10,0,0
  death_mode: lose_gold
  death_route: nearest_in_zone
  pvp_mode: zone
  can_select_faction: true
  auto_equip: true
  is_narrative: false
  players_can_set_title: true
  allow_pvp: true
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

`starting_level`, `max_level`, and `leveling_curve` control player progression.
`leveling_curve` is a cumulative XP threshold list where the first entry is
level 1 and must be `0`; for example, the second entry is the XP required to
reach level 2. `max_level` cannot be higher than the number of curve entries.
The example above defines five reachable levels; a 20-level world needs 20
entries.
See [leveling-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/leveling-builder-guide.md).

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

- `kind` must resolve to `trigger`, `world`, `currency`, `zone`, `room`, `path`, `itemdefinition`, `itembundle`, `merchantprofile`, `mobdefinition`, `spawnplan`, `ability`, `abilities`, `quest`, or `questarc`. The legacy `itemtemplate` and `mobtemplate` kinds are also accepted during the transition.
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
  - mob reaction events such as `say` use `scope: world` and a `mobtemplate`
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

Permission checks are applied when editing via manifest:

- rank 3+ builders can edit all trigger scopes
- rank 1-2 builders can edit room/zone targets only when assigned
- rank 1-2 builders cannot edit world-scoped triggers
- rank 1-2 builders cannot edit world config manifests (`world`)

## Implementation Notes

- Manifest helpers live in `backend/builders/manifests.py`.
- World config read/export endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/config/`
- Trigger list + YAML serialization endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<room_pk>/triggers/`
- Manifest apply endpoint:
  - `POST /api/v1/builder/worlds/<world_pk>/manifests/apply/`
  - trigger returns `operation: created`, `operation: updated`, or `operation: deleted`
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
