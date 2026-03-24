# WR2 Quest System Roadmap

## Purpose

This document describes how to get from the current repository state to the
quest end state described in `docs/quest-system-endstate.md`.

The roadmap is intentionally replacement-oriented. We do not want to spend time
on WR1 data migration, legacy compatibility layers, or keeping the current
quest model alive longer than necessary.

Reference docs:

- `docs/quest-system-endstate.md`
- `docs/quest-manifest-playground.md`
- `docs/quest-runtime-playground.md`

## Current Status

Status as of 2026-03-23:

- Phase 1 backend authoring foundation is implemented.
- Phase 1 playground and usage docs are implemented.
- Phase 2 backend runtime slice is implemented.
- Phase 2 runtime walkthrough docs are implemented.
- There is no frontend builder UI yet.
- There is no dedicated frontend quest UI yet.

Implemented so far:

- new app:
  - `backend/quests/`
- new authored models:
  - `QuestTemplate`
  - `QuestArcTemplate`
- manifest validation and apply/delete support for:
  - `kind: quest`
  - `kind: questarc`
- builder world manifest integration in the existing
  `POST /builder/worlds/<world_pk>/manifests/apply/` flow
- read endpoints for Phase 1 authoring:
  - `GET /builder/worlds/<world_pk>/questtemplates/`
  - `GET /builder/worlds/<world_pk>/questtemplates/<id-or-slug>/`
  - `GET /builder/worlds/<world_pk>/questarcs/`
  - `GET /builder/worlds/<world_pk>/questarcs/<id-or-slug>/`
- backend playground script:
  - `backend/scripts/quest_manifest_playground.py`
- playground usage doc:
  - `docs/quest-manifest-playground.md`
- new runtime models:
  - `QuestInstance`
  - `QuestObjectiveState`
  - `QuestJournalEntry`
  - `QuestOfferState`
- runtime services:
  - discovery
  - progression
  - journal/recap
  - quest event subscriptions
- runtime command surface:
  - `quest recap`
  - `quest opportunities`
  - `quest accept <slug>`
  - `quest choose <slug-or-id> <choice_id>`
  - `quest abandon <slug-or-id>`
- runtime endpoints:
  - `GET /game/quests/opportunities/`
  - `POST /game/quests/opportunities/<slug>/accept/`
  - `GET /game/quests/active/`
  - `GET /game/quests/resolved/`
  - `GET /game/quests/instances/<instance_id>/recap/`
  - `POST /game/quests/instances/<instance_id>/choose/`
  - `POST /game/quests/instances/<instance_id>/abandon/`
- runtime playground script:
  - `backend/scripts/quest_runtime_playground.py`
- runtime walkthrough doc:
  - `docs/quest-runtime-playground.md`
- automated tests:
  - `backend/wr2_tests/test_quest_manifests.py`
  - `backend/wr2_tests/test_quest_runtime.py`

Deliberate Phase 1 deviation from the ideal end-state:

- the new read endpoints use `questtemplates/` and `questarcs/` instead of
  taking over legacy `/quests/` routes yet
- schema classes currently live in `backend/quests/manifests.py` instead of a
  separate `backend/quests/schemas.py`
- no builder frontend shipped yet

Deliberate Phase 2 deviation from the ideal end-state:

- runtime resolved quests currently live at `GET /game/quests/resolved/`
  instead of taking over the legacy completed route yet
- discovery refresh is currently driven by emitted gameplay events and quest
  endpoints, not by `state.sync`
- `npc_dialogue` discovery currently means "matching mob template is present in
  the room" rather than full dialogue-tree acceptance
- no dedicated frontend runtime quest UI shipped yet

Important architectural reality today:

- the smallest thing you can interact with is a `QuestTemplate` manifest
- there is no standalone persisted `Storylet` model yet
- storylets currently exist as step nodes inside a quest template graph

Immediate next work:

- Phase 1 cleanup:
  - optional frontend builder read UI
  - move schema classes into `backend/quests/schemas.py` if we still want the
    module split as follow-up cleanup
- Phase 2 polish:
  - frontend runtime quest UI
  - trigger quest discovery from `state.sync` / enter-world flow
  - decide when `/game/quests/resolved/` should become canonical
    `/game/quests/completed/`
- Phase 3 cutover:
  - retire legacy quest log surfaces
  - stop reading old quest runtime models

## Current Codebase Fit

The brainstorm is directionally right for this repository, with a few important
codebase-specific adjustments.

### What fits well

- Event-driven progression matches the WR2 architecture in
  `backend/spawns/events.py` and the command -> action -> event document in
  `.codex/skills/wr-transition/wr2-architecture.md`.
- Manifest-based authoring fits the current builder direction in
  `docs/yaml-manifest-system.md` and `backend/builders/manifests.py`.
- Reusing broad concepts like facts, marks, actions, and conditions is correct
  at the architecture level.
- Named step graphs, fail-forward, and separating discovery from the quest log
  all address real weaknesses in the current quest stack.

### What must change for this repo

- The current quest system is too legacy-shaped to extend:
  - `builders.Quest` is tied to `mob_template`, zone ownership, old script
    fields, and `fetch` / `deliver` quest types only.
  - `builders.Objective` and `builders.Reward` are enum-limited tables for old
    quest patterns.
  - `spawns.PlayerQuest` and `spawns.PlayerEnquire` only capture timestamped
    interaction state.
- The existing condition DSL in `backend/core/conditions.py` is not a good
  authoring target for new quest content:
  - it is string-based
  - `quest_complete` is explicitly not implemented
  - it is awkward for scoped, typed, event payload checks
- Trigger `script` fields are still imperative command text. For quests, we
  should move straight to typed quest predicates and typed quest effects instead
  of adding more authored command scripts.
- Ambient issuer support is still transitional in the current trigger path, so
  the new quest runtime should advance from canonical game events and typed
  quest effects, not by leaning on trigger-script execution.
- The current builder UI is zone-scoped for quests. The new system needs to be
  world-scoped because contracts, world events, and arc membership are not
  fundamentally zone-local.

## Hard Boundaries

These are non-negotiable.

- No WR1 quest migration work.
- No dual authoring format.
- No new features added to legacy `builders.Quest`.
- No new runtime behavior built on `PlayerQuest` / `PlayerEnquire`.
- No quest manifests that embed arbitrary code or command scripts.

## High-level Architecture

The new quest system should live in a dedicated app, for example:

```text
backend/quests/
  __init__.py
  apps.py
  models.py
  schemas.py
  manifests.py
  serializers.py
  views.py
  services/
    discovery.py
    engine.py
    effects.py
    journal.py
    predicates.py
    progress.py
    slots.py
```

The builder integration can remain in the broader builder flow, but quest
domain logic should live in `backend/quests/`, not inside `builders.models.py`.

## Proposed Django Models

### `QuestTemplate`

Canonical authored quest definition.

Current implementation:

- `world` FK
- `slug` unique per world
- `name`
- `quest_type`
  - `questlet`, `quest`, `contract`, `world_event`
- `scope`
  - `player`, `party`, `guild`, `world`
- `status`
  - `draft`, `active`, `archived`
- `arc` FK nullable to `QuestArcTemplate`
- `repeatability_mode`
- `repeatability_cooldown_seconds`
- `max_active`
- `discovery_policy` JSONField
- `slot_schema` JSONField
- `graph` JSONField
- `reward_policy` JSONField
- `manifest_version`
- timestamps

Notes:

- The graph stays in structured JSON because quests are naturally graph-shaped.
- We still keep indexable relational columns for world, slug, type, scope, and
  lifecycle.
- `source_manifest` / `manifest_hash` are still future work.

### `QuestArcTemplate`

Optional authored grouping for linked quests.

Suggested fields:

- `world` FK
- `slug` unique per world
- `name`
- `summary`
- `journal_policy` JSONField
- timestamps

### `QuestInstance`

Runtime state for an accepted or auto-started quest.

Current implementation:

- `template` FK
- `player` FK nullable
- `world` FK for world-scoped instances
- `status`
  - `active`, `resolved`
- `resolution`
  - `complete`, `abandoned`
- `current_step_id`
- `slot_bindings` JSONField
- `local_state` JSONField
- `visible_objective_ids` JSONField
- `resolved_at`
- `expires_at` nullable
- `last_journal_entry_at`

Notes:

- A quest instance is the authoritative runtime record.
- We do not rebuild runtime state out of `PlayerQuest` timestamps.
- Phase 2 is player-scoped only, so shared-scope fields are still future work.

### `QuestObjectiveState`

Runtime progress rows for active objectives.

Current implementation:

- `quest_instance` FK
- `objective_id`
- `text`
- `status`
  - `active`, `complete`, `failed`, `hidden`
- `progress_current`
- `progress_target`
- `distinct_values` JSONField nullable
- `last_matching_event_type`
- `last_matching_event_at`
- `deadline_at` nullable

This lets the engine update active objectives without re-walking the entire
graph for every event.

### `QuestJournalEntry`

Authored recap history for player memory.

Current implementation:

- `quest_instance` FK
- `step_id`
- `entry_type`
  - `step_entered`, `objective_updated`, `resolved`, `system`
- `recap`
- `lead`
- `stakes`
- `payload` JSONField nullable
- `created_at`

### `QuestOfferState`

Per-player discovery bookkeeping.

Current implementation:

- `player` FK
- `template` FK
- `is_visible`
- `last_seen_at`
- `last_accepted_at`
- `last_resolved_at`
- `cooldown_until`
- `snoozed_until`
- `dismiss_count`

This supports opportunity cooldowns and rumor resurfacing without pretending
that every seen storylet is an active quest.

## Schema Layer

Authoring schemas should live in `backend/quests/schemas.py` and use explicit
typed structures, for example:

- `QuestManifest`
- `QuestArcManifest`
- `QuestSpec`
- `QuestDiscoverySpec`
- `QuestSlotSpec`
- `QuestStepSpec`
- `QuestObjectiveSpec`
- `QuestChoiceSpec`
- `QuestTransitionSpec`
- `QuestPredicate`
- `QuestEffect`

Important rule:

- the authored quest schema is new and typed
- the runtime implementation may initially adapt some predicates to current
  facts and marks
- authors should not write new content in the old `conditions` DSL

Current implementation note:

- typed schema validation exists today
- it currently lives inside `backend/quests/manifests.py`
- splitting it into `backend/quests/schemas.py` is still worthwhile cleanup

## Endpoint Plan

### Builder-facing endpoints

End-state builder surfaces:

- `GET /builder/worlds/<world_pk>/quests/`
  - list quest templates
  - include manifest YAML and summary data
- `GET /builder/worlds/<world_pk>/quests/<quest_pk>/`
  - full quest payload
  - include manifest YAML
  - later include graph preview and validation output
- `GET /builder/worlds/<world_pk>/quest-arcs/`
  - arc list
- `GET /builder/worlds/<world_pk>/quest-arcs/<arc_pk>/`
  - arc detail
- `POST /builder/worlds/<world_pk>/manifests/apply/`
  - accept `kind: quest`
  - later accept `kind: questarc`

Current Phase 1 builder surfaces:

- `GET /builder/worlds/<world_pk>/questtemplates/`
- `GET /builder/worlds/<world_pk>/questtemplates/<id-or-slug>/`
- `GET /builder/worlds/<world_pk>/questarcs/`
- `GET /builder/worlds/<world_pk>/questarcs/<id-or-slug>/`
- `POST /builder/worlds/<world_pk>/manifests/apply/`
  - accepts `kind: quest`
  - accepts `kind: questarc`

Current endpoint behavior details:

- list endpoints support `?query=<text>`
- numeric `query` values filter by id
- non-numeric `query` values filter by `name` or `slug`
- detail endpoints accept either numeric id or slug even though the URL
  parameter name is still `<pk>`

Manifest create/update/delete behavior should match the trigger workflow:

- create when `metadata.id` / `metadata.slug` are omitted
- update when identity is present
- delete with `operation: delete`

### Runtime endpoints

The player runtime surface should be explicit and service-backed.

- `GET /api/v1/game/quests/opportunities/`
- `POST /api/v1/game/quests/opportunities/<slug>/accept/`
- `GET /api/v1/game/quests/active/`
- `GET /api/v1/game/quests/completed/`
- `GET /api/v1/game/quests/arcs/`
- `POST /api/v1/game/quests/instances/<instance_id>/abandon/`
- `GET /api/v1/game/quests/instances/<instance_id>/recap/`

We should also add a command handler for `quest recap` so the text-game flow is
not dependent on dedicated frontend pages.

## YAML Ingestion Integration

The current manifest system already provides a solid pattern.

Implementation direction:

1. add quest serializer/parser/apply helpers in `backend/quests/manifests.py`
2. have `backend/builders/manifests.py` delegate to quest manifest helpers, or
   move builder manifest routing into domain modules if the file gets too large
3. extend `WorldManifestApplyView` to route `kind: quest` and later
   `kind: questarc`
4. add round-trip tests similar to existing trigger and world-config manifest
   tests

Current status:

- done

Quest manifests should support:

- optional `apiVersion`
- case-insensitive `kind`
- strict world validation
- partial updates
- deletion
- human-readable YAML block strings

## Runtime Integration Into WR2 Event Flow

The quest engine should behave like another event subscriber, similar to the
current trigger subscription path.

Near-term integration point:

- `backend/spawns/events.py`

Add quest dispatch after event publication, using a dedicated subscriber entry
point such as:

- `backend/quests/subscriptions.py`

Flow:

1. action emits canonical `GameEvent`
2. event is published to clients
3. quest subscription layer receives the event
4. matching objective states update
5. transitions are evaluated
6. journal entries and resulting state mutations are written

Longer term, triggers and quests should probably share a generic subscriber
registry, but that is not required for the first quest slice.

## Builder UI Direction

The new UI should follow the read-oriented manifest pattern already used by
triggers and world config.

Recommended new screens:

- `World > Quests`
- `World > Quest Arcs`
- `World > Edit World` continues to be the write surface for YAML apply

Recommended UI behavior:

- show quest summaries and current manifest YAML
- offer copy/create/update/delete manifests
- later show graph preview and simulation output

Do not keep investing in the current:

- `frontend/src/views/builder/zone/QuestList.vue`
- `frontend/src/views/builder/zone/QuestDetails.vue`
- old quest objective/reward form components

Those screens mirror the legacy model too closely.

Current status:

- backend read endpoints exist
- frontend builder screens do not exist yet

## Tiered Implementation Plan

### Phase 0: Freeze Legacy Quest Work

Goal: stop adding more debt while the new system is built.

Tasks:

- treat legacy quest code as feature-frozen
- add this design documentation
- create a short internal checklist for "new quest work must go into WR2 quests"
- if needed, add a temporary setting or world-level flag for enabling the new
  quest runtime in development worlds only

Validation:

- no new builder or runtime work lands in `builders.Quest`

Status:

- done

### Phase 1: Authoring Foundation

Goal: make the new quest format real before runtime work starts.

Tasks:

- create `backend/quests/` app
- add migrations for:
  - `QuestTemplate`
  - `QuestArcTemplate`
- implement Pydantic schema validation
- implement manifest parsing, serialization, apply, and delete
- add builder list/detail endpoints
- add world manifest apply routing for `kind: quest`
- build a minimal builder UI that can list quests and expose YAML

Status:

- done on the backend
- frontend builder UI still pending

Completed:

- `backend/quests/` app exists
- migrations for `QuestTemplate` and `QuestArcTemplate` exist
- typed manifest validation exists
- create/update/delete manifest flow exists
- builder read endpoints exist
- world manifest apply routing exists for `quest` and `questarc`
- backend playground script exists
- backend playground documentation exists
- automated manifest tests exist and pass

Still open inside or adjacent to Phase 1:

- optional frontend builder read UI
- optional module cleanup to move schema classes out of `manifests.py`
- deciding when the temporary `questtemplates/` and `questarcs/` routes should
  become canonical `/quests/` and `/quest-arcs/` routes

Scope limits:

- no runtime quest progression yet
- no slot query execution yet
- no world events yet
- no in-game playable quest experience yet

Validation:

- quest manifests round-trip cleanly
- builder permission checks match world/zone rules where relevant
- invalid graphs and invalid references are rejected early

Recommended tests:

- `backend/wr2_tests/test_quest_manifests.py`
- builder permission tests in `backend/builders/tests.py`

Current verification:

- `wr2_tests.test_quest_manifests`
- `wr2_tests.test_trigger_manifests`
- `wr2_tests.test_world_config_manifests`
- manual playground verification:
  - `template`
  - `list`
  - `show`
  - `apply`

Verified command:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py test \
    wr2_tests.test_quest_manifests \
    wr2_tests.test_trigger_manifests \
    wr2_tests.test_world_config_manifests \
    --settings=config.settings.testing
```

### Phase 2: First Playable Vertical Slice

Goal: ship a small but real WR2 quest engine.

This is the first phase where authored quest manifests become actually
playable in game.

Minimum supported features:

- player-scoped quests only
- content types:
  - `questlet`
  - `quest`
- discovery sources:
  - `npc_dialogue`
  - `room_prompt`
  - `auto_start`
- step kinds:
  - `storylet`
  - `objective`
  - `resolution`
- objective progress modes:
  - `boolean`
  - `count`
  - `unique_count`
- resolutions:
  - `complete`
  - `abandoned`
- fixed slots only
- journal entries with recap/lead/stakes
- active/completed/opportunities endpoints
- `quest recap` command

Status:

- done on the backend
- frontend runtime quest UI still pending

Completed in this pass:

- runtime models exist:
  - `QuestInstance`
  - `QuestObjectiveState`
  - `QuestJournalEntry`
  - `QuestOfferState`
- discovery works for:
  - `auto_start`
  - `room_prompt`
  - `npc_dialogue`
- runtime step kinds work for:
  - `storylet`
  - `objective`
  - `resolution`
- objective progress modes work for:
  - `boolean`
  - `count`
  - `unique_count`
- in-game command path exists:
  - `quest recap`
  - `quest opportunities`
  - `quest accept`
  - `quest choose`
  - `quest abandon`
- runtime endpoints exist
- quest subscriptions are wired into `publish_events()`
- runtime playground script exists
- runtime walkthrough documentation exists
- focused runtime tests exist and pass

Still open inside or adjacent to Phase 2:

- dedicated frontend runtime quest UI
- discovery refresh on `state.sync` / enter-world
- canonical completed-route cutover
- broader builder/runtime integration polish around `npc_dialogue`

Implementation tasks:

- add migrations for:
  - `QuestInstance`
  - `QuestObjectiveState`
  - `QuestJournalEntry`
  - `QuestOfferState`
- implement discovery service
- implement accept/start service
- implement objective event subscription handler
- implement step transition evaluator
- implement journal service
- implement runtime endpoints
- expose opportunities in runtime UI

Validation:

- a simple authored questlet works end to end
- a normal multi-step quest can be accepted, progressed by events, and resolved
- journal output remains readable after reconnects

Recommended tests:

- `backend/wr2_tests/test_quest_discovery.py`
- `backend/wr2_tests/test_quest_engine.py`
- `backend/wr2_tests/test_quest_journal.py`
- `backend/wr2_tests/test_quest_event_subscriptions.py`

Current verification:

- `wr2_tests.test_quest_runtime`
- `wr2_tests.test_quest_manifests`
- `wr2_tests.test_information`
- `wr2_tests.test_movement`
- `wr2_tests.test_triggers`

Verified command:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py test \
    wr2_tests.test_quest_runtime \
    wr2_tests.test_quest_manifests \
    wr2_tests.test_information \
    wr2_tests.test_movement \
    wr2_tests.test_triggers \
    --settings=config.settings.testing
```

### Phase 3: Cutover and Delete Legacy Quest System

Goal: stop paying the old-system tax once the new vertical slice is proven.

Tasks:

- remove legacy builder quest CRUD usage from the UI
- replace old quest log endpoints with new quest services
- remove `quest_data: {enquire, complete}` as the canonical quest runtime
  signal
- stop referencing `mob_template.template_quests` for runtime surfacing
- delete legacy models and code paths:
  - `builders.Quest`
  - `builders.Objective`
  - `builders.Reward`
  - `spawns.PlayerQuest`
  - `spawns.PlayerEnquire`
- drop old migrations/tables through normal Django schema deletion migrations
- remove old zone quest routes and Vue components

Validation:

- all quest authoring uses the new manifests
- all runtime quest surfaces use the new instance model
- there is no dual-write or dual-read path

This phase is where we honor the "do not keep old code or models around"
constraint.

### Phase 4: Branching, Fail-forward, and Better State

Goal: move from a usable slice to a strong narrative system.

Add:

- `compromised`
- `failed_forward`
- `expired`
- branching steps
- choice-gated transitions
- quest timers
- hidden objectives
- optional objectives
- snooze/cooldown behavior on opportunities
- richer quest-local state
- typed predicates and effects beyond the phase-2 minimum

Implementation tasks:

- expand schema support
- add timer scheduler integration
- add richer transition ordering and priority
- add more effect executors
- add more predicate evaluators

Validation:

- authored fail-forward quests can resolve badly without dead-ending the player
- timed quests can expire cleanly
- journal entries stay coherent across branches

### Phase 5: Slots, Contracts, Arcs, and World Events

Goal: reach the full content model.

Add:

- query-based slots
- generator-based slots
- contracts
- quest arcs
- world events
- shared scopes:
  - `party`
  - `guild`
  - `world`
- discovery salience ranking

Implementation tasks:

- add slot resolver services
- add arc endpoints and progress summarization
- add repeatability and contract cooldown policy
- add world-event phase graphs
- add carefully scoped shared-state mutation rules

Validation:

- generated contracts use the same engine as authored quests
- arc summaries stay in sync with child quest progress
- world events do not accidentally leak private quest state

### Phase 6: Tooling and Hardening

Goal: make the system safe to scale.

Add:

- unreachable-step linting
- missing-transition linting
- event/objective linting
- slot resolution validation
- graph visualization
- canned event-trace simulation
- author-friendly error messages

Validation:

- designers can preview quest graphs without reading raw JSON
- invalid manifests fail with precise diagnostics
- test coverage exists for both authoring and runtime paths

## Facts, Marks, and Existing Systems

The brainstorm is correct to reuse existing WR2 primitives where possible, but
we need to do it carefully.

Use now:

- world facts from `worlds.World.facts`
- player marks from `spawns.Mark`
- canonical player/item/mob/room relations from current Django models
- event emission from `spawns.events.GameEvent`
- manifest apply pattern from `backend/builders/manifests.py`

Do not reuse as the new authored quest format:

- legacy `conditions` strings
- trigger `script` command text
- legacy quest-specific enums and tables

In other words: reuse storage and runtime seams where they are stable, but do
not author new content in legacy syntax.

## Suggested File-level Work Breakdown

This is a practical first cut for implementation ownership.

Backend:

- `backend/quests/models.py`
- `backend/quests/schemas.py`
- `backend/quests/manifests.py`
- `backend/quests/services/discovery.py`
- `backend/quests/services/engine.py`
- `backend/quests/services/progress.py`
- `backend/quests/services/journal.py`
- `backend/quests/services/effects.py`
- `backend/quests/views.py`

Builder integration:

- `backend/builders/views.py`
- `backend/builders/manifests.py`

Runtime integration:

- `backend/spawns/events.py`
- new `backend/quests/subscriptions.py`
- command handler for `quest recap`

Frontend:

- replace legacy zone quest screens with world-scoped quest views
- update runtime quest log screens to use opportunities / active / completed
  endpoints from the new service

Tests:

- builder manifest/permission tests in `backend/builders/tests.py`
- WR2 runtime tests in `backend/wr2_tests/`

## Recommended Next Deliverable

Phase 1 authoring exists now. The next milestone should be a strict playable
vertical slice:

1. author one `kind: quest` manifest
2. ingest it through the builder manifest flow
3. surface it as an opportunity from an NPC or room prompt
4. accept it
5. progress it from a canonical game event
6. write journal entries
7. resolve it as `complete`
8. render it in `quest recap`

That is enough to validate the architecture before adding contracts, world
events, and advanced slot resolution.
