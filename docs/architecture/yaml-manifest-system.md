# WR2 YAML Manifest Editing

## Goals

WR2 world editing is moving toward an authored-manifest workflow inspired by Kubernetes:

- builder UI pages show current state
- canonical edit format is YAML
- import/export is straightforward because authored entities can round-trip through manifests

Implemented manifest kinds currently include the current WR2 authoring path:

- `worldbundle` (the first-document wrapper for a base world and its authored
  instance templates)
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
- `trainerprofile`
- `faction`
- `mobdefinition`
- `spawnplan`
- `ability`
- `abilities`
- `social`
- `quest`
- `questarc`

## Optional WR1 Authored-World Conversion Notes

WR1-to-WR2 export scripts should target the current WR2 manifest contracts, not
the temporary compatibility models. As WR2 legacy concepts are removed, update
this section in the same change so the WR1 exporter can be kept in sync.
Exporter invocation, default scope, and optional logic conversion are
documented in [WR1 World Manifest Export](../dev/wr1-world-manifest-export.md).

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
  fields or effects as aliases. A zero-valued mob currency reward is accepted
  as an explicit absence and normalized away; canonical exports omit it.
- Normalize only the known WR1 item-currency enum value `medal` to the built-in
  code `medals`; do not rename an unrelated authored custom code by guesswork.
- Apply the death-destination conversion boundary documented in
  [WR1 World Manifest Export](../dev/wr1-world-manifest-export.md).
- Convert representable legacy currency conditions to the existing structured
  condition path `actor.balances.<code>`. Flag ambiguous predicates for builder
  review instead of inventing a second currency condition language.
- Convert each authored WR1 social to `kind: social`, normalize its command to
  lowercase, and map the WR1 five-message positional data into named fields in
  this exact order: index `0` to `spec.targetless.self`, index `1` to
  `spec.targetless.others`, index `2` to `spec.targeted.self`, index `3` to
  `spec.targeted.target`, and index `4` to `spec.targeted.others`. Preserve an
  authored priority when available. Rewrite legacy Jinja variables
  `actor_marks` and `target_marks` to the canonical WR2 variables `actor_state`
  and `target_state`; do not emit the legacy names because WR2 imports reject
  them. Apply WR2 template and complete-group
  validation and flag invalid definitions for builder review rather than
  silently changing their meaning. This converts authored definitions only;
  never export users, players, mute lists, command history, emitted social
  events, or any other runtime data.

- WR1 world PvP settings export only as `kind: world`
  `spec.pvp_mode`; do not emit `spec.allow_pvp`. When the source has a valid
  `pvp_mode`, that value wins. Otherwise, map `allow_pvp: true` to
  `pvp_mode: free_for_all` and `allow_pvp: false` to `pvp_mode: disabled`.
  Never infer `pvp_mode: match` from the legacy boolean; match-gated arena PvP
  has no WR1 equivalent and must be authored explicitly in WR2.
  Exporters should audit and flag conflicts where both legacy fields are
  present but disagree (`allow_pvp: false` with `free_for_all`, `zone`, or
  `match`, or `allow_pvp: true` with `disabled`) rather than preserving both
  fields. WR1 conversion should omit `spec.announce_duel_results`; there is no
  legacy equivalent, and the WR2 default is `false`.
- Trigger mob reactions target `kind: trigger` with a canonical scalar
  `spec.target` such as `mobdefinition.<slug>`, not `mobtemplate` or a database
  id.
- An authored WR1 room action that is known to run for every player arrival
  should export as a room-scoped `kind: event` trigger with `event: enter`.
  Use `after_move_enter` only when the WR1 source is provably limited to
  directional movement, and `after_death_room_enter` only for a provably
  death-specific action. Do not translate a mob's legacy `enter` reaction into
  a room trigger: it remains world-scoped with a `mobdefinition` target. If the
  WR1 row does not establish which of those semantics it had, flag it for
  author review instead of guessing.
- Quest NPC dialogue sources use `mob_definition` / `mob_definition_id`, not
  `mob_template`.
- Quest room pickups and item grant/spawn effects use `item_definition` /
  `item_definition_id`, not `item_template`. Authored room-pickup description
  overrides use `room_description`, never `ground_description`.
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
- WR1 `ItemTemplate.ground_description` exports as `kind: itemdefinition`
  `spec.room_description`. Do not emit `spec.ground_description`; it is not
  part of the canonical WR2 contract. Preserve explicitly authored blank values
  as `""` when the conversion needs to clear an existing description.
- WR1 item point and combat fields use current WR2 names: `mana_max` and
  `mana_regen` become `energy_max` and `energy_regen`, `spell_power` becomes
  `ability_power`, and food whose legacy `food_type` is `mana` becomes
  `food_type: energy`. Move fixed `strength`, `constitution`, `dexterity`, and
  `intelligence` values under `spec.attributes`; do not emit those legacy flat
  keys. WR1 computed item `damage` and `armor` were runtime properties rather
  than stored template columns, so the converter materializes their legacy
  level, slot, quality, and armor-class formulas into WR2 `weapon_damage` and
  `armor`. Otherwise converted weapons and armor would import successfully but
  have zero combat value.
- WR1 `ItemTemplate.skill_modifier` has no direct current item-definition
  mapping. Omit non-empty values and report them for builder review rather than
  inventing an attribute or ability effect.
- WR1 `ItemTemplate.hit_msg_first` and `ItemTemplate.hit_msg_third` export to
  `kind: itemdefinition` fields `spec.hit_msg_first` and
  `spec.hit_msg_third`. Preserve non-empty multiword phrases as authored. Emit
  blank legacy values as `""` so applying over an existing WR2 definition
  clears an old customization; omission defaults only when creating a definition.
- WR1 `MobTemplate` rows export as `kind: mobdefinition`; WR2 no longer has a
  `MobTemplate` model, manifest kind, API endpoint, or runtime mob FK.
- WR1 mob `mana_max` and `mana_regen` become `energy_max` and `energy_regen`,
  `spell_power` becomes `ability_power`, and positive legacy Gold rewards become
  `spec.rewards.currencies.gold`. Split the whitespace-delimited WR1 `traits`
  field into a WR2 trait list. Legacy random-drop generation, elite/editor
  flags, carried or equipped template inventory, merchant behavior, crafting,
  and upgrading require explicit current WR2 definitions or profiles; omit and
  report them instead of leaving rejected legacy keys in a mob-definition
  document.
- Convert WR1 mob skill teaching only when every legacy skill can be resolved
  deliberately to an exported WR2 ability slug. Emit a `kind: trainerprofile`
  catalog and attach it to the converted mob with `spec.trainer.profile`;
  otherwise omit the unsupported entries and report them for author review.
  Do not infer a profile `spec.learning` quota from WR1 teaching data; omit the
  policy to retain unrestricted legacy teaching unless the converter has an
  explicit, deterministic authored source for both its condition and limit.
  Direct room attachment is an additional WR2 authoring option and is not
  inferred from a WR1 trainer mob or its loader destination.
- WR1 `MobTemplate.hit_msg_first` and `MobTemplate.hit_msg_third` export to
  `kind: mobdefinition` fields `spec.hit_msg_first` and
  `spec.hit_msg_third`. Preserve non-empty multiword phrases as authored. Emit
  blank legacy values as `""` so applying over an existing WR2 definition
  clears an old customization; omission defaults only when creating a definition.
- WR1 `Loader` / `Rule` rows export as `kind: spawnplan` entries. WR2 no longer
  imports or stores loader/rule rows, and runtime item/mob rows no longer keep
  `rule_id` or source-template FKs.
- Each converted WR1 rule must emit exactly one scalar spawn target:
  `room@<relative_id>`, `zone@<relative_id>`, `path@<relative_id>`, or the
  plan-local `entry.<slug>`. Resolve WR1 numeric and coordinate destinations in
  the source world before export; never emit a WR1 database id or one of the
  legacy target mapping aliases as canonical WR2 YAML.
- Each converted WR1 Trigger must likewise emit exactly one canonical scalar
  `spec.target`: `room@<relative_id>`, `zone@<relative_id>`, `world`,
  `mobdefinition.<slug>`, or `itemdefinition.<slug>`. Resolve legacy database
  ids before export and never emit the legacy `{type, ref}`, `{type, key}`, or
  `{type, id}` target mappings as canonical WR2 YAML.
- Map WR1 authored default world facts to `kind: world`
  `spec.initial_state` and authored zone defaults to `kind: zone`
  `spec.initial_state` only when the converter can distinguish authored seed
  data from live runtime mutations. Ambiguous values must be reported for
  builder review, not silently exported as defaults. Never export live player
  marks, current facts/zone data, runtime room state, or runtime mob state.
- A WR1 loader/rule may emit `spec.entries[].initial_state` only when it contains
  an explicit authored constant for a mob placement with equivalent mutable
  state semantics. Never infer mob initial state by inspecting a live WR1 mob,
  and never attach initial state to item or mixed-source entries.
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
- Builder read-only spawn-plan inspection APIs are
  `/zones/<zone_pk>/spawn-plans/` and
  `/rooms/<database_room_pk>/spawn-plans/`; do not export/import or call the
  removed `/loads/` endpoints.
- WR1 door keys export as WR2 `itemdefinition.<slug>` refs. The WR2 manifest
  surface remains one `spec.doors[]` entry on each originating room; exporters
  must not emit an internal `Doorway` id, a doorway ref, or a separate manifest
  kind.
- Before emitting doors, group WR1 rows whose endpoints and opposite directions
  form a reciprocal pair. A pair becomes one canonical WR2 doorway with two
  faces. Preserve `direction`, `name`, and destination separately on each face,
  while requiring the pair to agree on `key`, `destroy_key`, and
  `default_state`. Emit those shared values identically in both room documents.
- If reciprocal WR1 faces disagree on any shared setting, report the pair for
  builder review and do not choose one face as the winner or import them as two
  unrelated doorways. A single WR1 face, or a face whose reverse exit is not
  authored, remains a one-faced doorway; the exporter must not invent a reverse
  face.
- Export only authored default door state. Never inspect or export a live WR1
  door's current open, closed, or locked state, a pending close, a key held by a
  player, or any other runtime state.
- WR1 room or loader-authored room inventory exports as `kind: spawnplan`
  entries using `target: room@<relative_id>` and `itemdefinition` /
  `itembundle` source refs.
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
  merchantprofile` plus the mob definition's `spec.merchant.profile`
  attachment; WR2 no longer has `MerchantInventory`, mob `merchant_profit`, or
  the `/game/system/update_merchants/` endpoint. Direct room attachment is an
  additional WR2 authoring option and is not inferred during WR1 conversion.
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
- WR1 room conversion must assign every authored room one deterministic,
  positive world-relative id and preserve it throughout the converted
  manifest stream. Emit canonical `room@<relative_id>` references; never use a
  WR1 database id as that relative id by accident.
- Write converted YAML as literal UTF-8. JSON-style UTF-16 surrogate-pair
  escapes for astral characters are not valid authored YAML scalar content and
  can survive parsing as code units that PostgreSQL rejects. Exporter YAML
  quoting must therefore disable ASCII-only escaping and round-trip non-BMP
  names and descriptions before a manifest is handed to WR2.
- When one WR1 authored export contains a base world plus authored instance
  templates, emit one `kind: worldbundle` stream. Give each direct template a
  deterministic lowercase `instance_slug` that is unique within that base
  family, and use `instance.<instance_slug>` as its bundle scope. Do not use a
  WR1 world database id as a bundle ref.
- Put `metadata.world_ref` on every converted content document so a local ref
  such as `room@1` is resolved in the correct converted world. The same
  relative room id may legitimately occur in the base world and an instance
  template.
- Convert supported authored base/instance relationships into the bundle
  header's `spec.links`: `room.transfer_to`, `room.enters_instance`,
  `room.exits_to`, and `world_config.exits_to`. These links remain the right
  representation for static entry and default-exit relationships. When one
  specific interaction inside a direct instance instead chooses one of several
  base-world destinations, a deterministic legacy player-exit script may map
  to the command
  `/exitinstance {{ actor_key }} world@base/room@<relative_id>`. Resolve the
  legacy destination against the authored base world and emit its stable
  relative id; never copy a WR1 or WR2 database id into the command. An
  immediate room script uses `/cmd room --`.
  A typed conversion must target `trigger_actor`, and `/exitinstance` must be
  the only action in the final step. Flag dynamic destinations, non-player
  targets, nested-instance transitions, or scripts whose meaning is not an
  instance exit for builder review. Do not embed any other cross-world address
  in room, world-config, Trigger, or script documents.
- Convert only direct authored instance templates. Never include spawned
  runtime worlds, `instance_ref` values, instance runs or participants,
  players, or mutable runtime world/room state. This remains an optional
  authored-content conversion into a fresh WR2 database, not an in-place
  WR1-to-WR2 migration.
- WR1 `RoomCheck` rows export as room-scoped `kind: trigger` documents with
  `spec.kind: policy`. Use the checked room's stable `room@<relative_id>` ref
  as the full-world export target (`spec.target: room@<relative_id>`). Map
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
  - `in_inv <item_template_id>` -> `not: {item_present: {location:
    actor_inventory, item: itemdefinition.<slug>}}`.
  - `not_in_inv <item_template_id>` -> `item_present: {location:
    actor_inventory, item: itemdefinition.<slug>}`.
  Resolve every numeric WR1 id against the source export and emit portable
  slugs/refs. Never copy a numeric definition, quest, or room id into portable
  WR2 YAML.
- Flag WR1 room-check `equipped`, `not_equipped`, and `health_below` rows until
  the structured WR2 condition DSL has equivalent equipment membership and
  health-percentage predicates. `in_inv` and `not_in_inv` now map through
  `item_present` as shown above. Also flag
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
- Deterministic WR1 room-action growth chains may export as typed trigger
  `spec.steps`. Resolve every numeric item-template id against the source world
  and emit `itemdefinition.<slug>`. Convert `/take it:<seed> {{ actor }}` to
  `consume_item`, the first room `/load item <stage>` to `spawn_room_item` with
  an explicit binding such as `crop`, each exact purge/load stage pair to
  `replace_room_item` against that binding, and room text to `echo`.
- WR1 `/delay N` offsets are measured from trigger invocation; WR2
  `after_seconds` is relative to the preceding step. Group commands with the
  same WR1 offset, sort the groups, and subtract successive offsets. Thus WR1
  offsets `0, 20, 40, 60` become WR2 offsets `0, 20, 20, 20`.
  Preserve a positive first WR1 offset as the first WR2 `after_seconds`;
  invocation-time conditions and scheduling still happen immediately.
- Convert WR1 action conditions `item_in_inv <id>` and `item_in_room <id>` to
  `item_present` with `location: actor_inventory` and `location: room`,
  respectively. Preserve negation and counts. Flag arbitrary or dynamic
  delayed commands, ambiguous purge selectors, nested delays, and unsupported
  action types for builder review. A deterministic delayed `say`, `emote`,
  social, or room echo may instead export as one typed `command`
  action when its subject maps unambiguously to `trigger_actor`,
  `trigger_room`, or exactly one portable room-local mob definition. Emit one
  action per command; do not put WR1 command chains or `/cmd` wrappers inside
  `command.command`.
- A deterministic WR1 `/send {{ actor }} <text>` maps to native
  `send` with `actor: trigger_actor`. A deterministic
  `/sendexcept {{ actor }} <text>` maps to native `send_except` with the same
  actor ref. Preserve the authored action order and actor-template text.
  Literal or arbitrary player selectors do not map to typed actions and must be
  flagged for builder review; WR2 does not migrate runtime users.
- A deterministic WR1 harvest item action attached to the mature stage may
  export as a separate room-scoped command trigger. Preserve its authored
  command match, set `display_action_in_room: true`, and add an `item_present`
  room condition for the mature definition so the action is listed only while
  a harvest is available. Convert an exact mature-stage purge to
  `consume_room_item`, the load-and-give of a fixed harvested definition to
  `grant_item` for `trigger_actor`, and room text to `echo`, all in one
  `after_seconds: 0` step. This makes the availability check, removal, grant,
  and notification atomic. Resolve both numeric WR1 item-template ids to
  portable `itemdefinition.<slug>` refs. Do not emit a forced `look`: scheduled
  room-item deltas do not recompute viewer-specific actions, and the maturity
  message can direct the player to refresh the room normally.
- WR1 room-action `transfer {{ actor }} <numeric_room_id>` scripts should
  export as
  `/cmd room -- /transfer {{ actor_key }} room@<relative_id>`. Resolve the
  legacy room id to the converted room's assigned relative id; never copy a
  WR1 or WR2 database id into portable trigger YAML. Normalize slashless
  `transfer` to `/transfer`. For mob-authored scripts, keep the mob as issuer
  and use the same portable destination. When the surrounding WR1 action maps to typed
  `spec.steps`, the canonical forms are `subject: trigger_room` with
  `/transfer {{ actor_key }} room@<relative_id>` and
  `subject: trigger_actor` with
  `/transfer self room@<relative_id>`. Any supported step subject may issue
  the command provided the target is the Trigger actor; for example, an exact-one selected
  mob can use `/transfer {{ actor_key }} room@<relative_id>`. WR1's optional trailing
  transfer command does not map directly: export it as an explicit command
  before `/transfer`. For an immediate `spec.script`, use same-line `&&`
  segments and repeat the ambient wrapper for every segment, for example
  `/cmd room -- /send ... && /cmd room -- /transfer ...`. In `spec.steps`, emit
  a native `send` action followed by the `/transfer` `command` action in
  authored narrative order after any initial item/mob mutation prefix.
  Separate script lines are heartbeat-paced and are not equivalent. WR2 still
  emits the standard disappearance notification, whereas WR1 suppressed that
  text when a trailing command was present, so exporters should flag those
  scripts for author review. WR1 could also transfer a local floor item even
  though its help advertised only players and mobs; WR2 `/transfer` deliberately
  accepts character targets only, so exporters must flag item-target scripts
  instead of silently rewriting them.

Builder-facing authoring guidance lives in:

- [docs/guides/builders/currency-builder-guide.md](../guides/builders/currency-builder-guide.md)
- [docs/guides/builders/world-config-builder-guide.md](../guides/builders/world-config-builder-guide.md)
- [docs/guides/builders/trigger-builder-guide.md](../guides/builders/trigger-builder-guide.md)
- [docs/guides/builders/social-builder-guide.md](../guides/builders/social-builder-guide.md)
- [docs/guides/builders/builder-command-reference.md](../guides/builders/builder-command-reference.md)
- [docs/guides/builders/combat-formula-builder-guide.md](../guides/builders/combat-formula-builder-guide.md)
- [docs/guides/builders/leveling-builder-guide.md](../guides/builders/leveling-builder-guide.md)
- [docs/guides/builders/spawn-plan-builder-guide.md](../guides/builders/spawn-plan-builder-guide.md)
- [docs/guides/builders/mob-trait-builder-guide.md](../guides/builders/mob-trait-builder-guide.md)
- [docs/guides/builders/room-builder-guide.md](../guides/builders/room-builder-guide.md)

## Current Flows

### 1. World Config Screen

In **World > Config**, configuration is manifest-oriented:

- the page loads the current canonical **World YAML** into the shared manifest editor
- builders can edit, copy, and save the YAML directly
- saves require exactly one `kind: world` document and reload the canonical YAML
- **World > Edit World** remains the general editor for other kinds and batches

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
  YAML. Use the apply YAML to edit fields such as `metadata.name` and
  `spec.initial_state`, then paste it into **World > Edit World**.
- Path API responses also expose `relative_id` and `manifest_ref`; spawn-plan
  path targets should use `path@<relative_id>`, not path names.

### 6. World Edit Screen

The world-level **Edit World** view uses the same YAML editor layout as the
entity detail screens. It tells builders that they can paste one or more YAML
manifests and that each document is applied in order. The UI links to the
builder-facing [YAML Manifest Guide](../guides/builders/yaml-manifests.md) for
the supported-kind catalog and examples rather than duplicating them inline.

- Submitting YAML currently supports one or more YAML documents in sequence.
- Dependency order matters for hand-authored streams. Apply `ability`
  documents before a `trainerprofile`, then apply rooms and mob definitions
  that reference that profile. Canonical world export emits them in that order
  so its output can be imported directly.
- A `kind: worldbundle` is accepted only as the first document of a complete
  base-family stream. It is a scope/link wrapper around the supported content
  kinds below, not a standalone per-world content document.
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
  - `kind: trainerprofile`
  - `kind: faction`
  - `kind: mobdefinition`
  - `kind: spawnplan`
  - `kind: ability`
  - `kind: abilities`
  - `kind: social`
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

### 8. Socials Screen

**World > Socials** reads the base world's social catalog. Rank 3+ base-world
builders can create, edit, and delete definitions there; instance views inherit
the catalog read-only. Canonical `kind: social` YAML can also be applied through
**World > Edit World**, and world export emits the same portable contract.

Zone manifests exported by the system include `metadata.ref` in the portable
form `zone@<relative_id>`. Room and path manifests use `room@<relative_id>` and
`path@<relative_id>`, respectively. Spawn plans and exported room/path
manifests use those stable refs instead of names, coordinates, or database ids,
so moving a room does not break authored references and prod/dev database ids
do not need to match.

Quest authoring details, including field-by-field manifest docs and current
runtime behavior notes, live in:

- [docs/guides/builders/quest-builder-guide.md](../guides/builders/quest-builder-guide.md)
- [docs/guides/builders/quest-reference.md](../guides/builders/quest-reference.md)

Item definition authoring details, including stackable plain items, fixed stat
items, randomized stat items, and item bundles, live in:

- [docs/guides/builders/item-definition-builder-guide.md](../guides/builders/item-definition-builder-guide.md)

Merchant authoring details, including fixed stock, item-bundle stock, buyback,
finite funds, and killable versus non-killable shopkeepers, live in:

- [docs/guides/builders/merchant-builder-guide.md](../guides/builders/merchant-builder-guide.md)

Ability and Trainer Profile authoring, including direct room providers and
optional presence-controlled trainer mobs, lives in:

- [docs/guides/builders/ability-builder-guide.md](../guides/builders/ability-builder-guide.md)

Mob definition authoring details, including plain mobs, fixed stat mobs, and
randomized stat mobs, live in:

- [docs/guides/builders/mob-definition-builder-guide.md](../guides/builders/mob-definition-builder-guide.md)

Currency definitions, defaults, starting balances, prices, rewards, policies,
and conditions are documented in:

- [docs/guides/builders/currency-builder-guide.md](../guides/builders/currency-builder-guide.md)

Spawn plan authoring details, including fixed room spawns, weighted source
pools, guided dungeon density, spawn-plan trait/affix configuration, and
respawn behavior, live in:

- [docs/guides/builders/spawn-plan-builder-guide.md](../guides/builders/spawn-plan-builder-guide.md)

Instance architecture, including inherited base-world content, instance-local
layout/config overrides, goals, timers, leaderboards, and cleanup policy, lives
in:

- [docs/architecture/instance-system.md](/Users/teebes/code/writtenrealms/docs/architecture/instance-system.md)
- [docs/guides/builders/instance-builder-guide.md](../guides/builders/instance-builder-guide.md)

Mob trait architecture, including the rename from the earlier draft term
`affixes` to `traits`, lives in:

- [docs/architecture/mob-traits.md](/Users/teebes/code/writtenrealms/docs/architecture/mob-traits.md)

Attack routine and dual-wielding architecture, including proposed manifest
ownership for extra attacks and offhand weapon permissions, lives in:

- [docs/architecture/attack-routines-and-dual-wielding.md](/Users/teebes/code/writtenrealms/docs/architecture/attack-routines-and-dual-wielding.md)

Builder-facing attack routine and dual-wielding authoring guidance lives in:

- [docs/guides/builders/attack-routine-builder-guide.md](../guides/builders/attack-routine-builder-guide.md)

Builder-facing mob trait authoring guidance lives in:

- [docs/guides/builders/mob-trait-builder-guide.md](../guides/builders/mob-trait-builder-guide.md)

## Initial State Manifest Shape

Authored base worlds and instance templates expose seed state under
`spec.initial_state` on world, zone, and room documents:

```yaml
kind: world
spec:
  initial_state:
    weather: clear
---
kind: zone
metadata:
  ref: zone@1
  name: Harbor District
spec:
  initial_state:
    fog_level: 2
---
kind: room
metadata:
  ref: room@12
  name: Prison Cell
spec:
  coordinates:
    x: 4
    y: 2
    z: 0
  zone: zone@1
  initial_state:
    cell_door_open: false
```

`initial_state` must be a mapping. It is copied into a newly spawned runtime
world and reseeded during an instance reset. Applying an authored manifest does
not overwrite a running world's current state.

An instance template owns its defaults independently. Its world, zone, and room
defaults do not merge with live base-world state, and parallel runs receive
separate runtime copies.

Mob definitions may seed every newly spawned copy:

```yaml
kind: mobdefinition
metadata:
  slug: greek-captive-commander
  name: a Greek commander
spec:
  initial_state:
    captive: true
```

Mob entries in `kind: spawnplan` may add or override values for one placement:

```yaml
kind: spawnplan
metadata:
  slug: camp-spawns
  name: Camp Spawns
spec:
  zone: zone@3
  entries:
    - slug: greek-commander
      source: mobdefinition.greek-captive-commander
      target: room@12
      count: 1
      initial_state:
        captive: true
```

Every possible source for such an entry must be a mob definition. Each newly
materialized mob receives a copy. Definition values are merged first and entry
values override matching keys. Editing either manifest never overwrites the
current state of a surviving mob.

## Trigger Manifest Shapes

Trigger manifests use one typed scalar `spec.target`. Canonical values are:

- `room@<relative_id>` for an authored room
- `zone@<relative_id>` for an authored zone
- `world` for the selected world
- `mobdefinition.<slug>` for a mob definition
- `itemdefinition.<slug>` for an item definition

The prefix or reserved `world` literal identifies the target type, so
canonical YAML does not repeat it in `type`, `ref`, `key`, or `name` fields.
Imports continue to accept the legacy `{type, ref}`, `{type, key}`, and
`{type, id}` mappings, including a display-only `name`, but export always
normalizes them to a scalar. Database-local keys and ids are compatibility
inputs only and must never appear in portable full-world output. When an
update identifies an existing Trigger and omits `spec.target`, the existing
target is preserved; create manifests still require a target except for the
existing world-command default.

### Create Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target: room@10
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
  target: mobdefinition.archive-greeter
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
  target: room@10
  event: before_move_enter
  conditions:
    eq:
      - actor.archetype
      - warlord
  failure_message: Only warlords may enter.
  order: 0
  is_active: true
```

### Create Universal Player-Arrival Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Spear Trap
spec:
  scope: room
  kind: event
  target: room@10
  event: enter
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

Room-scoped `event: enter` is the canonical “player enters this room” contract.
It runs for ordinary movement, adjacent-room charge, flee, `/transfer`, death,
`/jump`, a connected character reset that changes room or runtime world,
instance entry, instance leave, and instance reset. Scope and target
disambiguate it from the pre-existing mob reaction with the same event text:

- `scope: room`, `target: room@<relative_id>`, `event: enter` runs the room
  behavior for a player arrival.
- `scope: world`, `target: mobdefinition.<slug>`, `event: enter` runs matching
  destination mobs' reactions.

The source-specific room events remain valid compatibility hooks.
`after_move_exit` and `after_move_enter` run only for the `move` source
(ordinary movement and adjacent-room charge), while
`after_death_room_enter` runs only for `death`. A source event runs the
universal hook as well, so identical effects should not be authored in both
unless two executions are intended.

Arrival condition context includes `event.source`, `event.origin_room`,
`event.destination_room`, and `event.direction`. Source values are `move`,
`flee`, `transfer`, `death`, `jump`, `character_reset`, `instance_enter`,
`instance_leave`, and `instance_reset`. Direction is populated for ordinary
movement, adjacent-room charge, flee, and directional `/jump`; it is empty for
non-directional sources. The destination supplies `room.*` and
`state.room.*`.

An ordinary relocation that does not change the player's location epoch emits
no arrival. Transfers and jumps to the current room therefore emit none, and
`character_reset` is emitted only when a connected player's room or runtime
world changes. Death and instance reset deliberately advance the location epoch
and emit an arrival even when the authored room id is unchanged; instance entry
or leave also counts across a runtime-world boundary. The subscriber validates
the current in-game state, runtime world, destination room, and location epoch,
so
stale or intermediate arrivals do not run remotely. Script-derived arrivals
inherit the eight-layer command-depth bound. Login, reconnect, and offline
location repair do not emit an arrival.

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
  target: room@10
  match: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
```

### Multi-line `script`

`spec.script` accepts YAML block strings (multi-line).

Runtime behavior details are documented in:

- `docs/flows/trigger-multiline-script-execution.md`
- `docs/architecture/trigger-event-subscriptions.md`
- `docs/architecture/trigger-matching-dsl.md`

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

### Typed Scheduled `steps`

Use `spec.steps` when a trigger needs builder-controlled timing, exact
transactional item/currency operations, or an audited command executed by the
Trigger environment. `script` and `steps` cannot both be non-empty.

```yaml
kind: trigger
metadata:
  world: world.1
  name: Plant Barley
spec:
  scope: room
  kind: command
  target: room@10
  match: plant seeds
  script: ""
  conditions:
    item_present:
      location: actor_inventory
      item: itemdefinition.barley-seeds
  steps:
    - after_seconds: 0
      actions:
        - type: consume_item
          actor: trigger_actor
          item: itemdefinition.barley-seeds
          count: 1
        - type: spawn_room_item
          room: trigger_room
          item: itemdefinition.barley-seedlings
          bind: crop
    - after_seconds: 20
      actions:
        - type: replace_room_item
          target: crop
          with: itemdefinition.barley-growing-plants
        - type: echo
          room: trigger_room
          text: A murmur of growth fills the air.
  on_step_error: cancel
```

For an atomic player charge, use an explicit currency code:

```yaml
conditions:
  gte:
    - actor.balances.obol
    - 10
steps:
  - after_seconds: 0
    actions:
      - type: debit_currency
        actor: trigger_actor
        currency: obol
        amount: 10
```

For an atomic player award, use the corresponding Trigger action:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: grant_currency
        actor: trigger_actor
        currency: obol
        amount: 10
```

This Trigger-step shape is distinct from a quest `grant_currency` effect:
Trigger actions identify exactly `actor: trigger_actor`; quest effects remain
inside their quest reward/effect context.

An audited command can execute as the Trigger room, the Trigger actor
(including a player), or one exact room-local mob:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: command
        subject:
          type: mob
          room: trigger_room
          mob: mobdefinition.charon
        command: emote grunts satisfactorily.
      - type: debit_currency
        actor: trigger_actor
        currency: obol
        amount: 10
      - type: command
        subject:
          type: mob
          room: trigger_room
          mob: mobdefinition.charon
        command: say Get on board.
      - type: command
        subject: trigger_room
        command: /transfer {{ actor_key }} room@42
  - after_seconds: 10
    actions:
      - type: command
        subject: trigger_actor
        command: say I accept the ferryman's price.
```

Perspective-specific narration uses native actions rather than an audited
command wrapper:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: send
        actor: trigger_actor
        text: You pull the lever.
      - type: send_except
        actor: trigger_actor
        text: "{{ actor }} pulls the lever."
```

Every step may have `after_seconds: 0` or a positive delay, relative to the
prior step. Each offset and their cumulative total are capped at one year. A
zero-delay first step executes atomically with the invocation-time condition
check; a later zero-delay step begins promptly after the preceding transaction
commits while retaining its own transaction boundary. A delayed first step
commits only the eligible run and its due time at invocation; it does not
reserve or apply the first step's authored resources early. `command`,
`debit_currency`, `grant_currency`,
`consume_item`, `consume_room_item`,
`grant_item`, `spawn_room_item`, `replace_room_item`, `set_mob`, `echo`, `send`,
and `send_except` are the action whitelist. Item and mob-definition refs are
resolved within the authored world and stored portably. Both currency actions
require exactly `actor: trigger_actor`, a positive safe integer amount, and a
currency code resolved against the base-world catalog; omission never means the
current default. Only a player Trigger actor may use them. Bindings must be
created before use and identify exact runtime items, not keywords or definition
matches. `set_mob` resolves exactly one live candidate in the current runtime
room, may filter it through the query-free Boolean/comparison subset of shared
`where` conditions, and can atomically update its supported runtime fields and
character state.

`send` and `send_except` require exactly `actor: trigger_actor` plus `text`.
They require the Trigger actor to be a connected player. `send` addresses only
that player. `send_except` resolves the player's current room at its exact
authored action position and addresses all other connected players in the same
runtime world and room. Their text renders actor name, key, and pronoun
substitutions such as `{{ actor }}` without exposing scoped-state
substitutions, then is revalidated as non-empty and no longer than 4,000
characters.

`command.subject` is `trigger_room`, `trigger_actor`, or a mapping with
`type: mob`, `room: trigger_room`, a portable `mobdefinition.<slug>`, and an
optional query-free `where`. One action executes one command; command chains,
history references, and nested `/cmd` dispatch are rejected. The dedicated
runner accepts only explicitly audited handlers. Current coverage is room-local
`say`, `emote`, `talk`, authored socials, room-subject, room-scoped `/echo`,
transactional `/open`, `/close`, and `/lock`, player-subject transactional
`follow` and `unfollow`, transactional `/transfer`, and transactional
`/exitinstance`.

`follow <character>` and `unfollow [character]` require the player
`trigger_actor` as their subject. They execute directly as that player; a
Trigger must not wrap them in `/force` or `/cmd`. The relationship affects
directional locomotion through an adjacent exit. Every follower's move retains
its normal combat, stamina, policy, exit, and door checks. A chain is capped at
16 following links, and relationship creation fails if it would exceed that
propagation depth.
A direction-based `/transfer` of a mob through an adjacent exit also emits a
directional follow edge. Player transfers, mob transfers using `here` or a
room reference, instance transitions, death routing, resets, and other
non-directional relocations do not pull followers. Following is independent
of group or party membership and shares no combat participation, rewards, or
loot.

The transfer target must be the Trigger actor, but any supported step subject
may issue the command. The canonical forms are
`subject: trigger_room` with
`/transfer {{ actor_key }} room@<relative_id>` and
`subject: trigger_actor` with `/transfer self room@<relative_id>`; an exact-one
selected mob may also use the explicit `{{ actor_key }}` target. `self` or `me`
is valid only when the resolved subject is the Trigger actor; a selected mob
can qualify when it is itself the Mob Trigger actor. Relative `here` and
direction destinations are resolved from the subject's room: the fixed Trigger
room, the Trigger actor's current room, or the selected mob's room respectively.
Portable manifests should use `room@<relative_id>`, not a coordinate or
database room id, so the destination survives room moves and cross-instance
imports. A typed transfer that would
move a player in active PvP fails with `target_busy`; a successful move finishes
ordinary active encounters. The Trigger room is retained as the recorded issuer
while the chosen room, player, or mob is recorded as the step subject. Durable
command events carry internal, unforgeable
Trigger/run/issuer/subject provenance, which is stripped from the player
payload. Forced speech and socials remain visible but do not count as voluntary
input for quest or Trigger subscriptions. When a transfer actually moves a
player, the committed structural lifecycle event starts destination
mob-definition `enter` reactions and then the room-scoped `event: enter`
triggers after commit. A moved player still in that destination after its
reactions additionally starts hostile-mob aggro. A transferred mob preserves
its existing mob-reaction path but does not run the player-only room hook.

`/exitinstance` is the only audited command in this contract that may cross a
runtime-world boundary. Its exact syntax is
`/exitinstance <player-target> world@base/room@<relative_id>`. A
supported step subject may issue it, but the target must render to the original
player `trigger_actor`. It must be the sole action in the final step; no later
step is valid. The qualified room token resolves against the target's active
run's direct base world, and the player returns to the exact base runtime saved
on their participant record. The command rejects an active duel contestant
and never broadens `/transfer` beyond its existing same-runtime boundary. A
successful exit finishes ordinary non-duel combat, cancels pending door work,
moves carried/equipped items and character effects, records a `forced`
participant exit, updates run activity, and emits `instance.left` plus one
room-enter lifecycle event with source `instance_leave`. The exited player
also receives a full destination state sync.

The derived reaction and aggro output is captured into one bounded durable
outbox batch. Only the player's final current arrival in one event batch runs
this work. A later location change invalidates an earlier pending arrival, and
delivery rechecks the player's in-game state, runtime world, room, and location
sequence. Reaction execution preserves the bounded eight-layer depth. No
lifecycle arrival work runs for a same-room transfer. `trigger_actor` is the
only player subject in this initial contract; typed steps do not select
arbitrary bystander players.

Item and mob mutations must form an initial action prefix. After that prefix,
`debit_currency`, `grant_currency`, `command`, `echo`, `send`, and
`send_except` may interleave, and their narrative events retain authored order.
The sole exception is `/exitinstance`: its command action must stand alone in
the final step, so all other effects and narration belong in earlier steps.
The runtime requires the starting wallet to cover the gross total of all
same-currency debits; same-step grants never subsidize those charges. It also
validates that the final net balance stays within the safe-integer limit, then
captures approved commands and transactional transfer state and writes balance
rows last. All grants and debits settle through one signed wallet mutation.
Approved commands do not branch on or mutate the wallet. `/transfer` may
nevertheless serialize a pre-mutation wallet as part of its full player
snapshot, so a nonzero mutation's authoritative
`currency.balances_changed` event is appended after all authored action events.
An exact net-zero batch retains its grant/debit narratives but changes no
wallet revision and emits no wallet-state event. Any later action failure rolls
back the balance, transfer state, and captured events together.
Each successful grant tells the actor `You receive <money>.` and visible
players in the actor's current room `<Actor> receives <money>.`; debit wording
uses `part with`/`parts with`. Invisible or logged-out actors produce no room
witness message, and wallet details remain private.
Query-backed presence and quest operators belong in the trigger's outer
conditions. Policy triggers cannot define steps.

Trigger start rechecks conditions under a runtime-world/room-scoped transaction
mutex and persists a `ScheduledTriggerRun` snapshot with cumulative due
offsets, original runtime world/room context, exact item bindings, stable
currency ids, and the actor identity. When the first offset is zero, its
actions commit in that same transaction. When it is positive, no authored
action executes until the due worker claims it. For a command that starts one
or more matching step Triggers, every run retains the validated request
identity, but only the first successful run retains the initiating connection
and owns its correlated lifecycle. After commit, that owner's private
`cmd.trigger.accepted` control event updates only that connection. A
final successful step emits `cmd.trigger.completed` after its authored events.
A rejected start emits a correlated `cmd.trigger.rejected` response instead.
If the owning run later fails, a textless, connection-pinned
`cmd.trigger.cancelled` updates the original input. Every failed
command-origin player run also sends one unpinned, player-only
`notification.trigger.cancelled` with safe generic prose, so the player still
learns what happened after reconnecting. Event/subscription runs have no
request identity and remain silent on cancellation.
These lifecycle events are not room narrative and cannot start Trigger or
quest subscriptions; detailed failure diagnostics remain only on the run. A
separate bounded Celery beat worker claims due rows through the
`(status, next_run_ts)` index with `select_for_update(skip_locked=True)`. Celery
ETA jobs are not the source of truth. Each step, wallet mutation, and outbox
events share a transaction; `on_step_error: cancel` rolls back the failed step
and cancels the remainder. All grants and debits in one step use one signed
wallet batch and at most one revision/state event. Insufficient starting funds
or an excessive final balance produce no currency change or success text. Only
one run of the same trigger may be active for a given runtime world, room, and
trigger actor; different actors may run concurrently. A runtime actor is also
limited to 16 active typed sequences in one runtime world.
See `docs/flows/trigger-scheduled-step-execution.md` for the runtime flow.

### Delete Trigger

```yaml
kind: trigger
operation: delete
metadata:
  world: world.1
  id: 42
```

## Social Manifest Shape

Socials use their lowercase command as a portable identity. Canonical detail
exports also include `metadata.id` and `metadata.key`, but imports can create or
update by command alone.

```yaml
kind: social
metadata:
  world: world.1
  command: wave
spec:
  priority: 10
  targetless:
    self: You wave.
    others: "{{ Actor }} waves."
  targeted:
    self: "You wave at {{ target }}."
    target: "{{ Actor }} waves at you."
    others: "{{ Actor }} waves at {{ target }}."
```

`metadata.command` must match `[a-z][a-z0-9_-]{0,63}` and is unique ignoring
case within the base world. Applying the same command updates that definition.
If `metadata.id` or `metadata.key` is included, it must identify the same
command. Renaming is a create of the new command followed by deletion of the
old command.

A targetless group is either empty or contains both `self` and `others`. A
targeted group is either empty or contains `self`, `target`, and `others`. At
least one complete group is required. Update manifests are partial: omitting a
group or a field preserves it, while `null` or `{}` clears an entire group.

Messages are sandboxed Jinja templates. Targetless messages can use the actor
name, title, character state, and pronoun variables. Targeted messages can also
use the equivalent target variables. Templates are validated and compiled at
authoring time, and both source templates and rendered messages are limited to 2,000
characters. Bounded interpolation and conditionals are supported; loops,
calls, filters, imports, assignments, arithmetic, explicit concatenation, and
collection literals are rejected.
`spec.priority` is an integer from `0` through `1,000,000`; exact
commands always win, while prefix collisions use higher priority and then
stable lexical/id ordering.

A base world can define at most 512 socials. Imports beyond that bound are
rejected, and runtime catalog construction remains bounded even if unsupported
direct database writes bypass normal authoring validation.

Delete by command:

```yaml
kind: social
operation: delete
metadata:
  world: world.1
  command: wave
```

Social definitions can be authored only on the base world by rank 3+ builders.
Instances inherit the base-world catalog and cannot fork it. See
[social-builder-guide.md](../guides/builders/social-builder-guide.md)
for message variables, player resolution, mob reactions, and scaling behavior.

## Stable Room References

Rooms separate immutable authored identity from mutable map position:

- `room@<relative_id>` is the canonical, world-scoped manifest identity.
- `spec.coordinates` is the room's current `x`, `y`, `z` position.
- Moving a room changes only `spec.coordinates`; every semantic reference keeps
  the same `room@<relative_id>`.
- Relative ids are positive, immutable, and never reused after deletion.
- Database keys such as `room.187` and legacy coordinate refs such as
  `room@10,4,0` are accepted only as import compatibility forms. Import
  resolves them in the selected authored world and canonical export emits the
  stable relative ref. A database key is not portable to another installation.

The identity boundary is deliberate:

- builders use `room@<relative_id>` when a room reference is stored, copied,
  displayed, or placed in generic authored data
- an interactive command argument whose grammar already requires a room may
  accept the bare relative id as shorthand, so `/jump 42` means `room@42`
- a bare number in a manifest, Trigger, quest, condition, or other persisted
  mixed-value field is not canonical room syntax; use the typed ref
- database primary keys remain the relational identity used by foreign keys,
  locks, actions, and internal events after a room ref is resolved at ingress

No builder-facing resolver may try a number as a database id and then as a
relative id, or the reverse. Both namespaces can contain the same positive
number and identify different rooms. Interactive bare shorthand is therefore
always world-scoped relative identity, while legacy database and coordinate
forms are confined to import normalization and explicit staff diagnostics.

The stable-room rollout first assigns room relative ids, then runs a one-time,
idempotent authored-data canonicalization migration. It rewrites resolvable
legacy database and coordinate aliases inside semantic JSON and command-text
fields for spawn plans, Triggers and legacy room actions, quests, abilities,
crafting conditions, death-routing conditions, and mob/item command or
condition data. Updates are world-scoped and batched; an alias that does not
resolve in its authored world is left unchanged for manual review.
Runtime/player state, quest progress, authored state seeds, and ordinary prose
are intentionally not rewritten.
The reverse migration is a no-op because the original database or coordinate
spelling cannot be reconstructed safely from a stable room ref.

Known semantic room-reference fields—including world/faction rooms, exits,
doors, paths, spawn targets, quest data, Trigger targets and conditions, and
supported command destinations—use the same resolver. Syntactically recognized
literal aliases in semantic scripts and command text are canonicalized when
they resolve; computed or dynamic strings cannot be rewritten reliably and
must resolve at runtime.

Gameplay payloads use `room.<relative_id>` as a world-local public map key.
Some relational serializers and legacy import data also contain dotted
database-local keys. The identical punctuation does not make either form an
authored reference: builder displays, copy actions, commands stored in YAML,
and conditions use the explicit `room@<relative_id>` manifest ref.

`world@base/room@<relative_id>` is a deliberately narrow qualified command
token, not a general manifest foreign key. It is accepted only as the
destination of `/exitinstance`. At runtime, WR2 resolves it against the target
player's active instance run's direct base world and returns that player to the
exact base runtime recorded on the participant. Every ordinary
`room@<relative_id>` remains scoped to its containing authored world, and
`/transfer` remains confined to one live runtime world. Only a positive
relative id is valid in the qualified token; scoped database and coordinate
aliases are rejected. Case-insensitive input normalizes to lowercase canonical
output.

### Builder Room URLs And Operational Identity

The builder-facing canonical room route is
`/build/worlds/<world_pk>/rooms/<relative_id>`. The resource path supplies the
room type, so the final segment is the bare positive relative id; for example,
room `room@42` in database world 23 appears at
`/build/worlds/23/rooms/42`. Builder navigation and breadcrumbs must generate
this route from `Room.relative_id`, not `Room.id`, so the URL remains stable
when rooms move and the visible/hovered value is the one builders use in
authored content.

Manifest references remain explicitly typed as `room@<relative_id>`. Unlike a
room URL, a manifest value can appear in a generic `ref`, command, condition,
or mixed resource field without surrounding type context. `room.<database_pk>`
therefore remains a legacy, installation-local import alias and is not
repurposed as portable syntax.

The troubleshooting route
`/build/worlds/<world_pk>/rooms/db/<database_pk>` resolves the database row
within the selected world and immediately replaces the browser location with
the canonical relative-id route. The ordinary room screen gives the portable
`room@<relative_id>` identity primary emphasis. Database ids remain available
to staff through explicit diagnostics, relational API payloads, logs, and
admin tools; ordinary builder labels, selectors, searches, notifications, and
copy actions do not present them as authored identity.

This is an intentional pre-launch breaking cutover. A bare
`/rooms/<number>` segment always means a relative id, while
`/rooms/db/<number>` always means a database id. The router and API must not
fall back from one namespace to the other: the same integer can validly name
different rooms, so a heuristic fallback could silently open or mutate the
wrong resource. Former `/rooms/<database_pk>` development bookmarks are not
retained as ambiguous aliases.

### Interactive Command Room Selectors

Direct room-destination commands follow the same no-fallback rule while
retaining concise input:

- `room@42` is the canonical selector and the form to persist in Trigger or
  quest command text
- bare `42` is interactive shorthand for world-relative `room@42`
- directions and command-specific values such as `here` keep their existing
  meanings
- `world@base/room@42` remains restricted to `/exitinstance`

Database-key and coordinate spellings are legacy import aliases, not examples
for new builder commands. Command parsing resolves a selector once, within the
issuer's authored world, and passes the resulting database id into the strict
Action/runtime layer. It never changes the internal identity used by row locks,
foreign keys, or structural events.

### Builder Zone URLs And Operational Identity

Zones use the same builder-facing identity contract. The canonical zone route
is `/build/worlds/<world_pk>/zones/<relative_id>`, so `zone@5` in database
world 23 appears at `/build/worlds/23/zones/5`. Every route nested beneath the
zone keeps that relative-id segment. Builder navigation must therefore be
generated from `Zone.relative_id`, while API calls resolve the zone once and
continue using `Zone.id` for relational database operations.

The troubleshooting route
`/build/worlds/<world_pk>/zones/db/<database_pk>` resolves the database row
inside the selected world and immediately replaces the browser location with
the canonical relative-id route. The ordinary zone screen presents
`zone@<relative_id>` as the authored identity and keeps the database id in
collapsed technical details.

A bare `/zones/<number>` segment always means a relative id, while
`/zones/db/<number>` always means a database id. There is no fallback between
the namespaces and no compatibility alias for the former database-id route.

### Relational Spawn-Entry Targets

Every `spec.entries[]` row has exactly one canonical scalar target:

```yaml
target: room@22
target: zone@1
target: path@4
target: entry.patrol-leader
```

The scalar prefix is the target discriminator, so manifests do not repeat a
`type`, `ref`, or display name. `entry.<slug>` is local to the containing spawn
plan and identifies one other `spec.entries[].slug`; it is not a world-wide
resource ref. The referenced entry must have a lower authored order, and it
must also be active whenever the dependent entry is active. Cohort follower
validation continues to require a compatible leader entry.

Canonical database storage uses nullable `target_room`, `target_zone`,
`target_path`, and `target_entry` foreign keys with an exact-one constraint.
Those relations—not a copied JSON locator—are the database source of truth.
Room, zone, and path deletion is restricted while referenced. Service
validation enforces that location targets belong to the plan's authored world
and that `target_entry` belongs to the same spawn plan. Runtime target loading
must use the indexed relations with bounded eager loading rather than parsing
JSON or issuing one lookup per placement.

Export derives one scalar from the populated foreign key. Room, zone, and path
relative ids are portable across database instances; a room move updates its
coordinates without changing either the room FK or its immutable
`room@<relative_id>` export identity. Import resolves the scalar in the selected
authored world and stores the destination database's local FK. It must know the
complete entry slug set before resolving and validating `entry.<slug>`; the
referenced entry must still have a lower authored order.

Legacy mappings keyed by `room`, `room_ref`, `zone`, `path`, `entry`, or
`parent_entry` remain import aliases. Import normalizes any valid alias to one
relation, strips display-only target metadata, and rejects mappings containing
multiple target kinds instead of choosing one by key precedence. Canonical
export never emits those mappings. The data migration resolves existing
`SpawnEntry.target` JSON locators before removing the duplicated JSON field;
the two representations must not remain jointly authoritative. Polymorphic and
weighted spawn sources remain a separate concern.

A multi-document import first reserves all declared room identities and
coordinates, then applies relationships and behavior. This two-phase process
lets zones, exits, doors, paths, and Triggers reference rooms declared later in
the stream, handles circular room links, and keeps the complete apply atomic.
Only a `kind: room` document may establish a new stable room identity; an
unknown reference does not silently create an untitled room.
Creating a stable room requires `spec.coordinates`; an update may omit it to
preserve the room's current position, while canonical export always includes
it. Importing an explicit new relative id advances the world's persistent room
id high-water mark, so a deleted or skipped lower id cannot later be reused.
On a pristine newly created world, the existing `room@1` starting-room row is
the only placeholder identity eligible for direct adoption as `room@1`.

## World-Family Bundles

A stable room ref solves movement and database portability only within one
authored world. It is deliberately world-scoped: the base world and every
instance template may each have a different `room@1`. A world-family bundle
adds a stable scope to those local refs without making room ids global.
The relational models still store normal database foreign keys; export maps
them to stable scope/room identities, and import resolves those identities
back to destination-local foreign keys.

Exporting an authored base world with direct instance templates produces one
flat multi-document stream. The first document is the family header:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: worldbundle
metadata:
  name: Phalanx
spec:
  worlds:
    - ref: world@base
      role: base
      name: Phalanx
    - ref: instance.hades
      role: instance
      slug: hades
      name: Hades
      parent: world@base
  links_mode: replace
  links:
    - relation: room.enters_instance
      source:
        world: world@base
        room: room@42
      target:
        world: instance.hades
    - relation: room.transfer_to
      source:
        world: world@base
        room: room@43
      target:
        world: instance.hades
        room: room@1
    - relation: room.exits_to
      source:
        world: instance.hades
        room: room@9
      target:
        world: world@base
        room: room@42
    - relation: world_config.exits_to
      source:
        world: instance.hades
      target:
        world: world@base
        room: room@42
```

The remaining documents use the ordinary per-world contracts and add only
`metadata.world_ref`:

```yaml
---
apiVersion: writtenrealms.com/v1alpha3
kind: world
metadata:
  world_ref: world@base
spec:
  name: Phalanx
---
apiVersion: writtenrealms.com/v1alpha3
kind: room
metadata:
  world_ref: world@base
  ref: room@42
  name: Hades Gate
spec:
  coordinates: {x: 8, y: 3, z: 0}
---
apiVersion: writtenrealms.com/v1alpha3
kind: world
metadata:
  world_ref: instance.hades
spec:
  name: Hades
---
apiVersion: writtenrealms.com/v1alpha3
kind: room
metadata:
  world_ref: instance.hades
  ref: room@1
  name: The Far Bank
spec:
  coordinates: {x: 0, y: 0, z: 0}
```

`metadata.world_ref` is stream-routing metadata. The bundle importer removes
it before passing a document to the ordinary per-world manifest loader.
Consequently, manifest foreign keys inside a document—rooms, zones, paths,
spawn targets, Trigger targets, and structured conditions—resolve within that
document's authored scope. Literal room refs executed later by commands such
as `/transfer` retain their normal runtime rule: they resolve inside the
issuer's current authored runtime world. Neither form is a cross-world
address. Static supported base/instance relationships use the central link
table. The one interaction-specific command exception is
`/exitinstance <player> world@base/room@<relative_id>`: it validates the
destination in that target's active run's direct base world and returns the
player to the exact recorded base runtime. The qualified token is not accepted
by `/transfer` or ordinary manifest fields.

### Stable Instance Scope

`World.instance_slug` is the authored instance template's portable identity
within one base-world family:

- only a direct authored instance template has an `instance_slug`
- the slug is lowercase, at most 120 characters, and unique among that base
  world's direct templates
- creation derives it from the template name and adds a deterministic suffix
  when needed, unless an explicit valid slug is supplied
- the slug, parent relation, and authored/runtime role are immutable after
  creation
- renaming an instance template does not change its bundle ref
- a spawned runtime instance uses `instance_ref`, not `instance_slug`, and is
  never part of an authored bundle

The canonical scope is `instance.<instance_slug>`. Import maps `world@base` to
the selected target base world, matches an existing direct template by
`instance_slug`, and creates a missing template with that slug. Database world
ids may therefore differ between development and production. Declaration
`name` is descriptive and supplies the initial name for a newly created
template; it is not identity.

Bundles represent exactly one base world and its non-archived direct authored
instance templates. Nested instance families and spawned runtime worlds are
rejected.
An existing template not declared by a hand-authored bundle is not deleted.
Canonical base-world export declares every non-archived direct authored
template, so a normal export/import round trip covers the active family.

### Central Cross-World Links

Per-world manifests keep `room@<relative_id>` local. Supported cross-world
foreign keys are therefore removed from the scoped room/world documents and
serialized once in `worldbundle.spec.links`.

| Relation | Source | Target |
| --- | --- | --- |
| `room.enters_instance` | Base `world` + base `room` | Direct instance `world` |
| `room.transfer_to` | Base `world` + base `room` | Direct instance `world` + instance `room` |
| `room.exits_to` | Direct instance `world` + instance `room` | Base `world` + base `room` |
| `world_config.exits_to` | Direct instance `world` | Base `world` + base `room` |

Room endpoints must use canonical `room@<relative_id>` refs; database and
coordinate aliases are not accepted in the bundle link table. Every endpoint
room must also have a `kind: room` document in that endpoint's declared scope,
so a bundle cannot accidentally depend on a row that happens to exist only in
the destination database. Export rejects links outside the declared family or
in the wrong direction instead of silently producing a non-portable file. A
base world's
`WorldConfig.exits_to` is likewise invalid because that relation is defined
only from an instance back to its base.

Each relation may occur at most once for a given source world/source room
pair. Interaction-specific branching is not another link relation: a trusted
instance Trigger may use
`/exitinstance <player> world@base/room@<relative_id>` so separate interactions
can choose separate base rooms. That qualified destination is validated against
the target's active run's direct base world at execution time. Future
cross-world concepts should otherwise add an explicit, validated bundle
relation rather than inventing a globally resolved room ref or hiding a
database id in a script.

`links_mode: replace` is the only supported mode. On import, WR2 clears the
managed cross-world link fields in the declared scopes and recreates them from
the header. An empty `links` list therefore means “no managed family links,”
not “preserve whatever is in the destination database.” This replacement
happens inside the same transaction as content import. The importer defaults
an omitted mode to `replace` and omitted links to an empty list for compatible
hand-authored input; canonical export always emits both fields explicitly.

### Validation And Atomic Import

A bundle must be applied to an authored base world by a rank 3+ builder. The
stream is validated and applied as one unit:

1. The first document must be the one `worldbundle` header.
2. The header must declare exactly one `world@base`; every other scope must be
   a direct `instance.<slug>` child with `parent: world@base`.
3. A bundle that declares instances must target a multiplayer authored base
   world. Import does not silently convert a single-player world that may
   already have runtime state.
4. Every content document must name one declared
   `metadata.world_ref`, and every declared scope must contain exactly one
   `kind: world` document.
5. WR2 locks and resolves the base family, then matches or creates instance
   templates by their stable slug. A declared slug that belongs to an archived
   template is rejected; import never mutates or silently reactivates archived
   content.
6. Room identities and coordinates are reserved independently in each scope
   before ordinary documents are applied, so forward and circular references
   work within that world.
7. Cross-world links are resolved in a batched pass only after all scoped
   content exists.

Any parse, permission, identity, room, content, or link error rolls back the
whole transaction, including newly created templates and link replacement.
The header is not a partial-update mechanism for family links. Individual
content documents retain their normal apply/delete semantics, but undeclared
instance templates are not pruned implicitly.

A newly created target's untouched `Starting Room` may be removed when its
identity is absent from a complete incoming stream. The stream must explicitly
declare a `spec.starting_room` that is also defined by one of its room
documents. The Lobby's Create World workflow creates an offline Builder player
and an editor `LastViewedRoom` bookmark at the scaffold room; those two
navigation records are atomically rehomed to the declared incoming starting
room before the scaffold is removed. The importer still proves that the room
is disposable first: authored fields, state, links, inventory, triggers,
assignments, an active or non-builder player, a foreign bookmark, and every
other dependent record make it ineligible for cleanup. Existing authored rooms
are preserved, never heuristically deleted.

The canonical format has explicit safety bounds: at most 50 authored worlds
including the base, 10,000 total documents including the header, and 20,000
cross-world links. Every declared scope must contain content; nested
`worldbundle` documents and undeclared `world_ref` values are rejected.

### Export Behavior And Scale

- Export a family from its base world. A base with at least one non-archived
  authored instance template always exports a bundle, even when
  `spec.links` is empty.
- A base with no authored templates retains the ordinary single-world export
  stream.
- A standalone instance template is never exported independently. Export its
  base-world family so inherited catalogs, its stable scope, and both sides of
  every relationship remain available.
- Header declarations, content scopes, and links are emitted in deterministic
  order for useful version-control diffs.

Export/import is an administrative authoring path, not per-player runtime work.
Stable room lookup uses the indexed world/relative-id identity, instance scope
lookup uses the unique base/slug identity, cross-world room endpoints are
resolved in batches, and link writes use bulk updates rather than one query per
link. The explicit family/document/link limits bound the family-wide work
admitted by this path. No bundle scan, serialization, or link fan-out is added
to movement, combat, ticks, or other concurrent gameplay paths.

## Ability Manifest Shape

Ability `spec.availability` supports an explicit actor audience alongside its
player class and level gates:

```yaml
availability:
  actors: [player, mob]
  classes: [hoplite]
  min_level: 1
```

`actors` must be a non-empty list containing `player`, `mob`, or both. It
defaults to both when omitted, and canonical export includes the normalized
list. `availability` rejects keys other than `actors`, `classes`, and
`min_level`, preventing a misspelling such as `actor` from silently opening the
ability to both audiences. Use `actors: [mob]` for an active NPC-only ability:
mobs may still select it from their combat loadouts, while player learning,
starting grants, help discovery, hotkeys, and command execution exclude it.
This is an explicit contract, not a `mob-` slug convention. Actor audience
combines with class, level, requirements, and trainer policy rather than
replacing them.

Ability manifests store ordered, normalized combat components. An interrupt is
its own component rather than a flag on damage. For example, this Hoplite Kick
deals one-quarter physical damage and interrupts only when that output lands:

```yaml
kind: ability
metadata:
  slug: kick
  name: Kick
spec:
  command:
    verbs: [kick]
  target:
    type: hostile
    default: current_target
  availability:
    actors: [player, mob]
    classes: [hoplite]
    min_level: 1
  cast_time:
    rounds: 0
  cooldown:
    rounds: 12
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 0.25
      text:
        label: Kick
    - type: interrupt
      target: ability.target
      apply: on_hit
      text:
        label: Kick
```

After normalization, an `interrupt` entry has only `type`, `target`, `apply`,
and `text`. `target` currently accepts only `ability.target`. `apply` accepts
`on_resolve` or `on_hit`, and `text` is the ordinary normalized component-text
mapping. An ability containing the component must set `spec.target.type` to
`hostile`. Components resolve in authored order, so `on_hit` fires only when
an earlier output component in the same resolution recorded a landed outcome.

The runtime interrupts only committed intent states: implemented `casting` and
reserved future `channeling`. A replaceable `queued` intent is immune. Clearing
a committed intent leaves its resource cost unpaid and its cooldown unstarted;
when that actor's initiative turn arrives, it falls back to a legal basic attack
instead of selecting another special ability during the same turn.

Interrupt resolution reads and mutates the target's pending intent from the
participant state already locked for the encounter step. It does not scan the
target's encounters, ability catalog, or world documents per component. A
hostile ability with `cast_time.rounds: 0`, including Kick, has zero windup but
is still queued and resolves on its actor's stored initiative turn. Channel
authoring and execution remain future work even though `channeling` is already
a recognized committed status. In player duels, hostile cast narration exposes
the casting contestant and ability to the opponent so an interruptible cast is
visible before it resolves.

## Trainer Profile Manifest Shape

A Trainer Profile is an ordered, reusable ability catalog. Its optional
`learning` policy can restrict who may learn from the profile and how many of
its entries that player may currently know:

```yaml
kind: trainerprofile
metadata:
  slug: hoplite-cross-training
  name: Hoplite Cross-Training
spec:
  notes: Choose any two Hoplite techniques.
  abilities:
    - ability.bash
    - ability.charge
    - ability.guard
  learning:
    conditions:
      in:
        - actor.archetype
        - [warlord, tidecaller, mystic, moonstalker]
    max_known: 2
```

`learning.conditions` uses the existing condition DSL and must be a
structured, query-free condition. `learning.max_known` is required when the
`learning` mapping is non-empty and accepts a positive integer or `uncapped`.
Omitting `conditions` applies the limit to every player; `conditions: false`
denies everyone. On create, omitting `learning` gives the profile its legacy
unrestricted behavior. On update, omitting it preserves the stored policy;
`learning: {}` clears the policy. Malformed policies are rejected before
apply.

The limit counts the intersection of the player's complete known-ability set
with the profile's complete `abilities` list, independent of how each ability
was acquired or whether it is currently active or eligible. It does not
replace the world-wide known-ability cap or an ability's availability and
requirements. Reusing the same profile on multiple providers shares one quota;
distinct profiles are independent quota boundaries even when their catalogs
overlap. Policy conditions and caps gate learning only, so a local provider can
still unlearn a profile member and free a slot.

Canonical export includes the normalized `learning` mapping when a policy is
present and emits `learning: {}` for an unrestricted profile. Trainer Profile
manifests must still precede room and mob documents that reference them in
hand-authored streams.

## Room Manifest Shape

The selected room's **Rooms > Edit** screen exposes this complete shape:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: room
metadata:
  ref: room@42
  name: North Gate
spec:
  coordinates:
    x: 10
    y: 4
    z: 0
  zone: zone@2
  description: An ironbound gate closes the northern road.
  note: Builder-only note.
  type: road
  color: "#8a8175"
  is_landmark: true
  initial_state:
    gate_alarm_raised: false
  exits:
    north: room@43
    east: null
    south: room@44
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
      to_room: room@43
      key: itemdefinition.north-gate-key
      destroy_key: false
      default_state: locked
```

Room manifests currently support `operation: apply` only. Preserve the stable
`metadata.ref` when editing an existing room. Change `spec.coordinates` to move
that room; its exits, spawn targets, Triggers, and other semantic references
continue to identify it.
Including `flags`, `details`, or `doors` replaces that complete collection for
the room. The canonical YAML shown after a save includes every exit direction,
so copy/edit/save round trips do not depend on hidden form state.

Rooms can expose always-available authored services without a spawned mob:

```yaml
spec:
  merchant:
    profile: merchantprofile.market-stalls
  crafting:
    profile: craftingprofile.village-forge
  trainer:
    profile: trainerprofile.village-training
```

Merchant, crafting, and trainer profile manifests are emitted before room
manifests in a world export so these typed references resolve in one import
stream. Omitting `spec.merchant`, `spec.crafting`, or `spec.trainer` preserves
the current attachment; setting the section, its `profile`, or an empty mapping
to null/empty clears it. For an instance template, profile references resolve
against its effective base definition world, matching the profiles inherited
by its builder UI.

A room accepts one Trainer Profile. If one location needs separate native and
cross-training quota boundaries, attach one profile to the room and the other
to a mob definition present there, or use separate rooms. Reusing a profile in
several rooms does not multiply its learning allowance.

The external room-manifest shape is intentionally unchanged even though WR2
stores a canonical logical doorway internally. Each `spec.doors[]` entry is one
directional face:

- the containing room is the origin
- `direction`, `to_room`, and `name` belong to that face
- `key`, `destroy_key`, and `default_state` belong to the logical doorway

Two entries are reciprocal faces when they connect the same two rooms in
opposite directions and the rooms have the corresponding reverse exits. They
share one runtime state and one set of key/default settings. Canonical export
repeats those shared fields on both room documents so every room manifest stays
self-contained; it does not expose a `doorway` field or require a new manifest
kind. A face without a reciprocal entry remains a valid one-faced doorway.

An apply stream that supplies both reciprocal faces must give them identical
`key`, `destroy_key`, and `default_state` values. Conflicting values reject the
apply atomically instead of letting document order choose a winner. Faces whose
endpoints/directions are not truly reciprocal remain independent one-faced
doorways. Applying only one face may intentionally change the logical doorway's
shared settings; a later canonical export then shows those values on every
reciprocal face.

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

`rewards.currencies` is an exact replacement mapping. Positive entries become
mob rewards. Explicit zero entries and omitted codes both mean no reward in
that currency, and canonical export omits them. Negative amounts remain
invalid. Omit the entire `rewards` patch when an update should preserve the
existing reward mapping.

See [currency-builder-guide.md](../guides/builders/currency-builder-guide.md)
for currency deletion, item, merchant, quest, death-policy, and condition
examples.

## World Config Manifest Shape

World config edits are update-only manifests (no create/delete mode). The
config screen and per-world export emit the same world document shape. Inside
a family bundle, the document additionally carries `metadata.world_ref`;
cross-world `WorldConfig.exits_to` remains in the bundle header rather than
this `spec`:

For a full field reference, see
[world-config-builder-guide.md](../guides/builders/world-config-builder-guide.md).

```yaml
kind: world
spec:
  name: Edeus
  short_description: Core setting text
  description: Long world description
  motd: Questions? Join Discord.
  is_public: true
  initial_state:
    weather: clear
    invasion_active: false
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
  starting_room: room@1
  death_room: room@18
  death_mode: lose_currency
  death_currency: crowns
  death_currency_penalty: 0.2
  death_route: nearest_in_zone
  pvp_mode: zone
  announce_duel_results: false
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

Queued abilities and non-basic combat actions use this encounter scheduler; see
[combat-abilities-model.md](combat-abilities-model.md) for their round and
initiative contract.

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
See [leveling-builder-guide.md](../guides/builders/leveling-builder-guide.md).

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
- [attributes-builder-guide.md](../guides/builders/attributes-builder-guide.md)
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
- [combat-formula-builder-guide.md](../guides/builders/combat-formula-builder-guide.md)

Ability manifests do not have an `action_type` field. Targeting, out-of-combat
availability, component behavior, and primary-action consumption are expressed
by `target`, `components`, `consumes_primary_action_on_resolve`, and
`consumes_primary_action_while_casting`. The optional WR1 authored-world
conversion utility must omit any legacy ability action classification and emit
those canonical fields directly. Supported legacy interrupt behavior must
normalize to an ordered `type: interrupt` component with `target`, `apply`, and
`text`; it must not become a damage flag or introduce a legacy timing field.

## `apiVersion`

- `apiVersion` is optional for manifests.
- If provided, accepted values are:
  - `v1alpha1`
  - `writtenrealms.com/v1alpha1` (legacy-compatible)
  - `v1alpha2`
  - `writtenrealms.com/v1alpha2` (stable-room compatibility contract)
  - `v1alpha3`
  - `writtenrealms.com/v1alpha3` (canonical scalar Trigger-target contract)
- Canonical full-world exports emit `writtenrealms.com/v1alpha3` on every
  document. Canonical room, spawn-plan, Trigger, and world-config YAML uses the
  same version. Older and hand-authored manifests may omit the field; their
  legacy target mappings and room-reference forms are normalized during
  import.

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

- `key` is still useful as a typed local reference format in relational API
  payloads (`zone.3`, `trigger.42`), but it is not canonical authored identity.
  Room keys require particular care: gameplay emits `room.<relative_id>` while
  legacy relational payloads can still contain `room.<database_id>`. Builders
  must copy `manifest_ref`, never infer a room ref from a dotted key.
- `id` is simpler for update targeting.
- For WR2 manifests, treat `id` as the primary update identifier and `key` as an interoperability/reference-friendly alias.
- Room manifests are the deliberate exception: `metadata.ref:
  room@<relative_id>` is their portable update identity, while coordinates
  remain mutable spec data.

For entity types that still use database identity, neither raw `id` nor
`trigger.<id>` is portable alone; add a stable authored identifier (for
example `metadata.slug` or `metadata.uid`) and map it at import time. Rooms and
instance-template scopes now follow that pattern through
`room@<relative_id>` and `instance.<instance_slug>`.

## Is `kind: trigger` redundant with `key: trigger.42`?

Partially, yes. They validate different things:

- `kind` selects the manifest parser/contract and is case-insensitive.
- `key` (or `id`) identifies one concrete instance.

Keeping both is still useful because:

- `kind` allows generic ingestion dispatch before touching IDs.
- `key` keeps typed references consistent with other entity refs.

If we eventually move to `metadata.id` only for updates, `kind` remains required.

## Validation Rules (Current)

- `kind` must resolve to `trigger`, `world`, `currency`, `zone`, `room`, `path`, `itemdefinition`, `itembundle`, `merchantprofile`, `faction`, `mobdefinition`, `spawnplan`, `ability`, `abilities`, `social`, `quest`, or `questarc`.
- For trigger update: `metadata.id` or `metadata.key` must reference an existing
  trigger in the selected world.
- For trigger create: omit both `metadata.id` and `metadata.key`.
- For trigger delete: set `operation: delete` and include `metadata.id` or
  `metadata.key`.
- `metadata.world` (if present) must match the selected world.
  - `metadata.world` accepts either integer id (`1`) or key form (`world.1`).
- `spec.scope`, `spec.kind`, booleans, and integers are validated.
- `spec.match` matcher syntax is validated using the Trigger Matching DSL.
- For create:
  - `spec.scope` is required.
  - `spec.target` is required for room/zone scope.
- For update, omitting `spec.target` preserves the existing target. Supplying
  it resolves and replaces the target.
- Canonical `spec.target` is a scalar whose prefix or reserved `world` literal
  determines its type. Legacy target mappings are import-only compatibility
  aliases; conflicting `type`, `ref`, `key`, or `id` locators are rejected.
- For `spec.kind: event`:
  - `spec.event` is required.
  - mob reaction events such as `say` and the mob-definition form of `enter`
    use `scope: world` and a `mobdefinition` target.
  - `event: social` also requires `spec.match`; its literals compare exactly
    with the resolved social command and run only for a player actor directly
    targeting the mob.
  - room events use `scope: room` and a `room` target. `event: enter` is the
    recommended universal player-arrival hook; `after_move_enter`,
    `after_move_exit`, and `after_death_room_enter` are source-specific
    compatibility hooks.
  - the shared text `event: enter` is resolved from scope and target:
    room/room means player-arrival room behavior, while
    world/mobdefinition means a mob reaction.
- For `spec.kind: policy`:
  - `spec.event` is required.
  - `spec.event` must be `before_move_enter` or `before_move_exit`.
  - v1 policy triggers use `scope: room` and a `room` target.
- `spec.script` and non-empty `spec.steps` are mutually exclusive.
- Every `spec.steps[*].after_seconds` may be `0` or a positive integer. A later
  zero offset is an immediate post-commit continuation with its own step
  transaction, not part of the preceding step's atomic action batch. Every
  step has a bounded, non-empty `actions` list.
- Typed step actions reject unknown fields and types. Context refs are limited
  to `trigger_actor` and `trigger_room`; item refs must resolve to an
  `itemdefinition` in the selected world.
- `debit_currency` and `grant_currency` each require exactly
  `actor: trigger_actor`, an explicit base-world currency code, and a positive
  integer `amount` no greater than `9,007,199,254,740,991`. Only player Trigger
  actors may use them. Gross debits must be affordable from the starting
  wallet, same-step grants never subsidize them, and every final net balance
  must remain within the same limit.
- `command` requires exactly one `command` string and a `subject` of
  `trigger_room`, `trigger_actor`, or an exact-one room-local mob selector.
  Newlines, `;`/`&&` chains, history references, and nested `/cmd` are rejected;
  the resolved handler must explicitly support Trigger-step execution.
  Transactional `follow <character>` and `unfollow [character]` require
  exactly the player `trigger_actor` as subject. They establish or clear only
  adjacent directional locomotion; normal movement blockers still apply, and
  non-directional relocation never pulls a follower. Following chains are
  capped at 16 links, and a longer relationship request fails. A direction-based
  `/transfer` of a mob through an adjacent exit is directional and can pull
  that mob's followers; player transfers and other mob transfer forms cannot.
  They do not establish a group or share combat, rewards, or loot.
  Any supported subject may issue transactional `/transfer`, but only the
  Trigger actor may be its target. Relative destinations use the subject's room;
  authored content should use a stable `room@<relative_id>` destination. Moving a
  player in active PvP fails with `target_busy`.
  Transactional `/exitinstance` instead requires the player Trigger actor as
  its target and an exact `world@base/room@<relative_id>` destination. Any
  supported step subject may issue it; `self` or `me` is valid only when that
  subject is the player Trigger actor. Its command action must be the sole
  action of the final step. The destination must resolve in the target's active
  run's direct base world, the participant must have a valid recorded base
  runtime, and active duel contestants are rejected. Scoped database and
  coordinate destination aliases are not accepted.
- `send` and `send_except` require exactly `actor: trigger_actor` and `text`.
  The actor must still be a connected player when the action executes;
  `send_except` additionally requires a current room. Actor substitutions are
  rendered before the non-empty, 4,000-character limit is rechecked.
- Item/mob mutations must be an initial action prefix. After that prefix,
  `debit_currency`, `grant_currency`, `command`, `echo`, `send`, and
  `send_except` may interleave in authored narrative order. A nonzero aggregate
  wallet change emits at most one state event after those action events. Exact
  net-zero grant/debit batches retain their narratives but emit no wallet
  revision or state event. No item/mob mutation may follow one of those actions.
- `spawn_room_item.bind` names are unique and must precede any matching
  `replace_room_item.target`. The current `spec.on_step_error` value is
  `cancel`.
- Policy triggers cannot define `spec.steps`.
- For command triggers, `spec.target` must match the scope type (`room`,
  `zone`, or `world`) or a supported definition-target form and must resolve in
  the selected world.
- For event triggers, the scalar target type must match the event family and
  resolve in the selected world.
- structured `conditions` are validated through the shared WR2 condition DSL in
  `backend/core/condition_dsl.py`; legacy trigger text conditions still pass
  through `backend/core/conditions.py`.
- For world config manifests:
  - only `operation: apply` is supported
  - `spec` fields are validated against the world schema
  - `initial_state`, when present, must be a mapping and replaces the authored
    seed without mutating an existing runtime world
  - room references (`starting_room`, `death_room`) must resolve to rooms in the selected world
  - `default_currency`, `starting_balances`, `death_currency`, and
    `clan_registration_currency` resolve against the base-world catalog
  - currency amounts reject booleans, fractions, negatives, and values above
    `9,007,199,254,740,991`
  - `pvp_mode` is the canonical PvP field; legacy `allow_pvp` is accepted only
    as an import alias and must not conflict when both fields are present;
    legacy `allow_pvp: true` normalizes to `free_for_all`, never `match`
  - `announce_duel_results` is a base-world boolean, defaults to `false`, and
    cannot be overridden by an instance template
  - `death_routing.routes` is an ordered first-match list; each route has one
    shared-DSL `when` condition and one local room `destination`
  - death-route conditions are limited to the bounded query-free subset over
    `player.core_faction`, `player.archetype`, `player.level`,
    `state.character.*`, and `zone.id`; level supports inclusive `gte` / `lte`
    thresholds, and query-backed condition operators are rejected
  - death-route faction codes, class keys, zone refs, and destination room refs
    resolve during manifest apply, and canonical export preserves route order
  - `death_routing_source` is instance-only; `local` is the default and
    `base_world` selects the direct base world's complete policy without
    merging local routes
- Zone and room `initial_state`, when present, must be mappings. The zone
  importer accepts legacy `spec.state` / `spec.zone_data` only as aliases for
  authored initial state; they never address a live runtime row. New manifests
  must emit `spec.initial_state`. Runtime world/zone/room state is not accepted
  as manifest input.
- Room door entries remain directional faces in `spec.doors`. A face's
  destination must match that room's exit, directions cannot repeat within one
  room, and a door cannot connect a room to itself or cross authored worlds.
  Reciprocal faces share one canonical doorway and must agree on `key`,
  `destroy_key`, and `default_state` when supplied together; inconsistent pairs
  reject the complete apply rather than being resolved by document order.
- Spawn-plan entry `initial_state`, when present, must be a mapping and every
  possible source for that entry must be a mob definition.
- Mob-definition `initial_state`, when present, must be a mapping. It seeds new
  mobs and is not reapplied during definition resync.
- Currency codes match `[a-z][a-z0-9_-]{0,63}`, are unique ignoring case per
  base world, and cannot be changed after creation. Instance worlds inherit
  currencies and cannot author their own definitions/default/starting balances.
- Social commands match `[a-z][a-z0-9_-]{0,63}`, are unique ignoring case per
  base world, and have priority from `0` through `1,000,000`. Targetless and
  targeted messages must form complete groups, at least one group is required,
  and message templates use only the supported sandboxed actor/target variables.
  Instance worlds inherit socials and cannot author their own definitions.
- `spec.cost` without `spec.currency` resolves the default on item creation and
  stores that concrete relation. `spec.currency` without `spec.cost` is invalid.
- Mob rewards use `spec.rewards.currencies.<code>`. Their replacement mapping
  accepts zero as an explicit absence, stores only positive rows, and rejects
  negative amounts. Merchant profiles use `spec.settlement_currency`, and
  quest rewards use `type: grant_currency` with explicit `currency` and
  `amount`.

Permission checks are applied when editing via manifest:

- rank 3+ builders can edit all trigger scopes
- rank 1-2 builders can edit room/zone targets only when assigned
- rank 1-2 builders cannot edit world-scoped triggers
- rank 1-2 builders cannot edit world config manifests (`world`)
- only rank 3+ builders can apply or delete social manifests, and only on the
  base world
- only rank 3+ builders can import a `worldbundle`, and the selected target
  must be an authored base world

## Implementation Notes

- Manifest helpers live in `backend/builders/manifests.py`.
- Full world or world-family export endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/export/`
  - a base with direct authored templates returns one `worldbundle` stream;
    a world without templates returns the ordinary content stream
- World config read/export endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/config/`
- Selected-room manifest endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<database_room_pk>/manifest/`
  - Django route name: `builder-room-manifest`
  - this is loaded on demand by **Rooms > Edit**; ordinary room/map payloads
    do not carry YAML
- Portable room lookup used to resolve a canonical builder URL:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/by-relative-id/<relative_id>/`
  - Django route name: `builder-room-relative-detail`
  - lookup is always scoped to the selected authored world; its response
    includes both `id` for internal API operations and `relative_id` plus
    `manifest_ref` for navigation and authoring
- Portable zone lookup used to resolve a canonical builder URL:
  - `GET /api/v1/builder/worlds/<world_pk>/zones/by-relative-id/<relative_id>/`
  - Django route name: `builder-zone-relative-detail`
  - lookup is always scoped to the selected authored world; its response
    includes both `id` for internal API operations and `relative_id` plus
    `manifest_ref` for navigation and authoring
- Trigger list + YAML serialization endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<database_room_pk>/triggers/`
- Manifest apply endpoint:
  - `POST /api/v1/builder/worlds/<world_pk>/manifests/apply/`
  - trigger returns `operation: created`, `operation: updated`, or `operation: deleted`
  - social returns `operation: created`, `operation: updated`, or `operation: deleted`
  - zone returns `operation: created`, `operation: updated`, or `operation: deleted`
  - world config returns `operation: updated`

## How To Edit World Config

1. Open **World > Config**.
2. Edit the desired `spec` fields in **World YAML**.
3. Select **Save YAML**.
4. Confirm the success message and review the reloaded canonical YAML.

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
