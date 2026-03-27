# Quest Manifest Playground

## Purpose

This document explains how to use
`backend/scripts/quest_manifest_playground.py`, what it actually does in the
current implementation, and what you can and cannot test in Phase 1.

This is a Phase 1 authoring tool. It works with quest templates and quest arc
templates stored in Django. It does **not** yet drive playable in-game quest
runtime.

If your goal is "author a quest manifest, store it, inspect it, patch it, and
confirm the backend accepts it," this tool is ready now.

If your goal is "accept a quest in the game client and progress it through
actual gameplay," that starts in Phase 2, not Phase 1.

That means:

- you can create quest templates
- you can update and delete quest templates
- you can create quest arcs
- you can inspect the exact YAML that is stored
- you can validate manifests through the same parser used by the builder apply
  endpoint
- you cannot yet accept, progress, complete, or recap quests in the game client

Phase 2 is where runtime quest instances, opportunities, objectives, and event
driven progression get added.

For the Phase 2 runtime walkthrough, see `docs/quest-runtime-playground.md`.

## What Exists Today

The current Phase 1 implementation consists of:

- Django models in `backend/quests/models.py`
  - `QuestTemplate`
  - `QuestArcTemplate`
- manifest parsing and application in `backend/quests/manifests.py`
- builder/world manifest integration in `backend/builders/views.py`
- read endpoints in `backend/quests/views.py`
- tests in `backend/wr2_tests/test_quest_manifests.py`
- this script:
  - `backend/scripts/quest_manifest_playground.py`

The script is a thin CLI around the same manifest code used by the builder
world manifest apply endpoint. It is not a separate quest system.

The smallest thing you can meaningfully create with it today is a single
`QuestTemplate` that contains a tiny two-step graph:

- one `storylet` step
- one `resolution` step

There is no standalone persisted `Storylet` model yet. In the current
implementation, storylets live inside a quest template's `steps` graph.

## Current Data Model

The script works with two authored entity types.

### Quest Template

Stored in `QuestTemplate`.

Important fields:

- `slug`
- `name`
- `quest_type`
- `scope`
- `status`
- `arc`
- `discovery_policy`
- `slot_schema`
- `graph`
- `reward_policy`

### Quest Arc Template

Stored in `QuestArcTemplate`.

Important fields:

- `slug`
- `name`
- `summary`
- `journal_policy`

## Current API Surface

Phase 1 read endpoints exist, but they intentionally do not replace the legacy
quest routes yet.

Current endpoints:

- `GET /api/v1/builder/worlds/<world_pk>/questtemplates/`
- `GET /api/v1/builder/worlds/<world_pk>/questtemplates/<id-or-slug>/`
- `GET /api/v1/builder/worlds/<world_pk>/questarcs/`
- `GET /api/v1/builder/worlds/<world_pk>/questarcs/<id-or-slug>/`
- `POST /api/v1/builder/worlds/<world_pk>/manifests/apply/`
  - supports `kind: quest`
  - supports `kind: questarc`

The script is often easier to use than hitting those endpoints directly because
it avoids authentication and request-shaping overhead when you are just playing
with manifests locally.

## What You Can Test Today

You can test:

- manifest validation
- create/update/delete behavior
- partial manifest patch behavior
- quest to arc linkage
- YAML round-trip serialization
- builder read endpoints
- builder endpoint filtering by `query`
- id-or-slug lookup behavior on detail views

You cannot test:

- opportunities surfacing to players
- quest acceptance
- quest instances
- event-driven objective progression
- journal/recap output
- in-game quest log behavior

If your goal is "generate quests in game and play them," Phase 1 is not enough
yet. Phase 1 generates authored quest templates in the database, not live
runtime quest instances.

## The Smallest Unit You Can Play With Today

Architecturally, the smallest authored narrative beat is a `storylet` step.

Practically, the smallest thing you can store and inspect today is a minimal
`kind: quest` manifest with:

- no explicit `spec.type` line for a normal quest
- one `storylet` step
- one `resolution` step

That is the closest Phase 1 equivalent to "touch the API and get the essence
of the system."

Minimal example:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: tiny_hello
  name: Tiny Hello
spec:
  scope: player
  status: draft
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 1
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: You notice a strange scrap of paper.
      text:
        body: A minimal authored quest beat.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The note tells you nothing useful, but the system works.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Important caveat:

- `auto_start` is only stored right now
- it does not yet create runtime quest instances
- the quest is not playable in the game loop yet

## Prerequisites

Run from the repo root:

```bash
cd /Users/teebes/code/writtenrealms
```

Bring up the backend-side stack with bind mounts:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose up -d db redis redis-celery rabbitmq backend
```

Find a world id:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py shell -c "from worlds.models import World; print(World.objects.values_list('id', flat=True).first())"
```

The examples below use `<world_id>` as a placeholder.

## Command Reference

The script currently supports four commands.

### Print A Starter Manifest

Quest template starter:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py template --world <world_id> --kind quest
```

Quest arc starter:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py template --world <world_id> --kind arc
```

### List Stored Templates

List quests:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py list --world <world_id> --kind quest
```

List arcs:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py list --world <world_id> --kind arc
```

### Show Stored YAML

Show a quest by slug:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py show --world <world_id> --kind quest bitter_well
```

Show an arc by id:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py show --world <world_id> --kind arc 1
```

### Apply A Manifest From Disk

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/my_manifest.yml
```

The script uses the same validation/apply code as the builder manifest flow.

## Concrete Walkthrough

This section gives you a concrete sequence you can run today.

## Example 0: Create The Minimal Quest

Create `/tmp/tiny_hello.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/tiny_hello.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: tiny_hello
  name: Tiny Hello
spec:
  scope: player
  status: draft
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 1
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: You notice a strange scrap of paper.
      text:
        body: A minimal authored quest beat.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The note tells you nothing useful, but the system works.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/tiny_hello.yml
```

Show it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py show --world <world_id> --kind quest tiny_hello
```

This is the cleanest first interaction with the new system:

- authored YAML in
- validated graph stored
- canonical YAML out

## Example 1: Create A Quest Arc

Create a local file:

```yaml
kind: questarc
metadata:
  world: world.<world_id>
  slug: ashwick_arc
  name: Ashwick Arc
spec:
  summary: The village faces an outbreak.
  journal_policy: {}
```

Save it as `/tmp/ashwick_arc.yml` inside the backend container:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/ashwick_arc.yml <<'EOF'
kind: questarc
metadata:
  world: world.<world_id>
  slug: ashwick_arc
  name: Ashwick Arc
spec:
  summary: The village faces an outbreak.
  journal_policy: {}
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/ashwick_arc.yml
```

List arcs:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py list --world <world_id> --kind arc
```

## Example 2: Create A Quest Template

Create `/tmp/bitter_well.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/bitter_well.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: bitter_well
  name: The Bitter Well
spec:
  scope: player
  status: active
  arc: ashwick_arc
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_template: mobtemplate.12
    visible_if: {}
    accept_if: {}
    salience: 80
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: A healer asks for help.
      choices:
        - id: accept
          text: Help.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The quest is complete.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/bitter_well.yml
```

List quests:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py list --world <world_id> --kind quest
```

Show the stored YAML:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py show --world <world_id> --kind quest bitter_well
```

## Example 3: Partially Update A Quest

Create `/tmp/bitter_well_patch.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/bitter_well_patch.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: bitter_well
spec:
  discovery:
    salience: 95
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/bitter_well_patch.yml
```

Verify:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py show --world <world_id> --kind quest bitter_well
```

The stored quest should still have the same step graph, but `discovery.salience`
should now be `95`.

## Example 4: Delete A Quest

Create `/tmp/delete_bitter_well.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/delete_bitter_well.yml <<'EOF'
kind: quest
operation: delete
metadata:
  world: world.<world_id>
  slug: bitter_well
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/delete_bitter_well.yml
```

Verify:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py list --world <world_id> --kind quest
```

## Example 5: Inspect The Same Data Through Builder Endpoints

The playground script is convenient, but Phase 1 also exposes builder read
endpoints.

List quest templates:

- `GET /api/v1/builder/worlds/<world_id>/questtemplates/`

List quest arcs:

- `GET /api/v1/builder/worlds/<world_id>/questarcs/`

Details:

- `GET /api/v1/builder/worlds/<world_id>/questtemplates/<id-or-slug>/`
- `GET /api/v1/builder/worlds/<world_id>/questarcs/<id-or-slug>/`

Useful details:

- list endpoints accept `?query=<text>`
- if `query` is numeric, it filters by id
- otherwise it filters by `name` or `slug`
- detail endpoints accept either a numeric id or a slug even though the URL
  parameter is named `<pk>`

The detail payload includes:

- the structured manifest
- canonical YAML
- the delete manifest
- delete YAML

So even without a dedicated frontend UI yet, the builder API can already round
trip authored quest data.

## Testing The Authoring Layer

If you want to run the automated authoring tests:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py test \
    wr2_tests.test_quest_manifests \
    wr2_tests.test_trigger_manifests \
    wr2_tests.test_world_config_manifests \
    --settings=config.settings.testing
```

What this verifies:

- quest manifests can be created
- quest manifests can be partially updated
- quest manifests can be deleted
- quest arcs can be created and linked
- the shared builder manifest endpoint still works for existing trigger and
  world config manifests

## Implementation Details

The script does not call the HTTP endpoint. It imports the same manifest
helpers directly.

The path is:

1. read YAML from disk
2. parse the YAML into a Python dict
3. detect `kind`
4. validate the manifest
5. apply it to Django models
6. print back the canonical stored YAML

Create vs update behavior:

- if `metadata.id` or `metadata.slug` resolves to an existing quest or arc in
  the selected world, the manifest updates that row
- if no existing row resolves, the manifest creates a new row
- `operation: delete` requires an existing `metadata.id` or `metadata.slug`

Patch behavior:

- quest and arc manifests support partial updates
- the parser starts from the current stored manifest if the target already
  exists
- it deep-merges the incoming `spec` onto that base
- validation runs after the merge, not before

That is why a tiny patch like:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: bitter_well
spec:
  discovery:
    salience: 95
```

updates one nested field without blowing away the rest of the quest graph.

For `kind: quest` the main code path is:

- `builders.manifests.load_yaml_manifest`
- `builders.manifests.parse_manifest_kind`
- `quests.manifests.parse_quest_manifest`
- `quests.manifests.apply_quest_manifest`

For `kind: questarc` it is:

- `quests.manifests.parse_quest_arc_manifest`
- `quests.manifests.apply_quest_arc_manifest`

The script is therefore good for quick local iteration, while the builder
manifest endpoint remains the HTTP entry point for real editor integration.

Validation currently enforced by the schema layer includes:

- `spec.type` must be one of:
  - `quest`, `contract`, `world_event`
- `spec.scope` must be one of:
  - `player`, `party`, `guild`, `world`
- `spec.status` must be one of:
  - `draft`, `active`, `archived`
- at least one step is required
- step ids must be unique
- step kinds must be one of:
  - `storylet`, `objective`, `branch`, `timer`, `resolution`
- `choices[].goto` must reference an existing step id
- `transitions[].goto` must reference an existing step id
- referenced quest arcs must exist in the same world

## Current Limitations

These are the important ones.

- There is no runtime quest engine yet.
- `steps` are validated structurally, but not executed.
- `discovery.sources` are stored but not surfaced to players yet.
- there is no `QuestInstance`
- there is no objective event subscription
- there is no journal or `quest recap`
- there is no frontend builder page yet
- the current read endpoints use `questtemplates/` and `questarcs/` to avoid
  clobbering legacy `/quests/` routes before cutover

## If You Want To "Test Quests In Game"

Right now, the honest answer is: you cannot do that yet.

Phase 1 lets you test authored quest data.
Phase 2 is the first milestone where you will be able to:

- surface an opportunity
- accept it
- create a quest instance
- progress it off real game events
- resolve it

Until then, the right mental model is:

- Phase 1: "Can I author and store the quest graph correctly?"
- Phase 2: "Can a player actually experience it?"

That distinction matters because "quest" currently means authored template, not
live player experience.
