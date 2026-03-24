# Quest Runtime Playground

## Purpose

This document explains how to exercise the Phase 2 quest runtime in game.

The key distinction is:

- `docs/quest-manifest-playground.md` is about authoring quest templates
- this document is about making those authored templates playable through the
  Phase 2 runtime

The smallest working interaction is intentionally tiny:

1. author one questlet manifest
2. apply it
3. create or reuse a dev player
4. issue one in-game command that discovers or starts the quest
5. inspect it with `quest recap`
6. choose one option and watch it resolve

That is the minimal proof that the runtime slice is real.

## What Phase 2 Supports Right Now

Implemented in this pass:

- player-scoped quests only
- runtime content types:
  - `questlet`
  - `quest`
- discovery sources:
  - `auto_start`
  - `room_prompt`
  - `npc_dialogue`
- runtime step kinds:
  - `storylet`
  - `objective`
  - `resolution`
- objective progress modes:
  - `boolean`
  - `count`
  - `unique_count`
- player interaction commands:
  - `give <item> <mob>`
  - `talk <mob>`
  - `kill <mob>`
- canonical quest progression events:
  - `quest.item.delivered`
  - `quest.mob.killed`
- typed reward effects:
  - `grant_gold`
  - `grant_xp`
- constrained completion-time mob commands:
  - `say`
  - `yell`
  - `emote`
  - `/echo`
  - `/zecho`
  - `/wecho`
- quest journal entries and recap output
- in-game `quest` command
- runtime endpoints for:
  - opportunities
  - active quests
  - resolved quests
  - recap
  - accept
  - choose
  - abandon
- backend runtime playground script:
  - `backend/scripts/quest_runtime_playground.py`

Important current limits:

- no dedicated frontend quest UI yet
- no state-sync-triggered quest refresh yet
- the runtime `completed` list currently lives at `resolved/` to avoid
  clobbering the legacy completed endpoint before cutover
- `npc_dialogue` currently means "the matching mob template is present in the
  room", not a full conversational UI
- `kill` is currently a minimal quest-enabling defeat command, not a finished
  combat system

## Prerequisites

Run from the repo root:

```bash
cd /Users/teebes/code/writtenrealms
```

Bring up the backend-side stack with bind mounts:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose up -d db redis redis-celery rabbitmq backend
```

Find a root world id:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py shell -c "from worlds.models import World; print(World.objects.filter(context__isnull=True).values_list('id', flat=True).first())"
```

The examples below use:

- `<world_id>` for the authored root world
- `<player_id>` for the spawned runtime player

## Helper Scripts

Phase 2 uses two scripts together:

- authoring:
  - `backend/scripts/quest_manifest_playground.py`
- runtime:
  - `backend/scripts/quest_runtime_playground.py`

Useful runtime commands:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py --help
```

Create or reuse a dev player:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py ensure-player \
    --world <world_id> \
    --email quest-playground@example.com \
    --name Questplay
```

That prints the player id you will use for the rest of the walkthrough.

List players if you want to confirm:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py players
```

Dispatch an in-game command as that player:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
```

Show current quest opportunities:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py opportunities --player <player_id>
```

Show current quest recap:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py recap --player <player_id>
```

If you are using the actual game frontend instead of the playground script, the
same text commands work there too:

- `look`
- `quest recap`
- `quest opportunities`
- `quest accept <slug>`
- `quest choose <slug-or-id> <choice_id>`
- `quest abandon <slug-or-id>`

## Minimal Case

This is the smallest possible in-game interaction with the new quest runtime.

### Step 1: Apply the Minimal Questlet

Create `/tmp/tiny_hello.yml` inside the backend container:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/tiny_hello.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: tiny_hello
  name: Tiny Hello
spec:
  type: questlet
  scope: player
  status: active
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
      lead: Read the note and move on.
      stakes: ''
      text:
        body: A minimal authored quest beat.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The note tells you nothing useful, but the system works.
      lead: ''
      stakes: ''
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

### Step 2: Create or Reuse a Dev Player

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py ensure-player \
    --world <world_id> \
    --email quest-playground@example.com \
    --name Questplay
```

Copy the printed player id into `<player_id>`.

### Step 3: Trigger the Quest In Game

Run `look` as that player:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
```

What should happen:

- you see the normal `cmd.look.success`
- you also see `quest.instance.started`
- the quest starts automatically because its discovery source is `auto_start`

### Step 4: Inspect the Quest

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py recap --player <player_id>
```

You should see:

- title: `Tiny Hello`
- the current recap and lead
- one visible choice:
  - `continue`

This proves the runtime can:

- discover a quest
- create a `QuestInstance`
- enter a `storylet` step
- write journal data
- render a recap

### Step 5: Resolve It

Choose the only option:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "quest choose tiny_hello continue"
```

Then confirm it resolved:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py resolved --player <player_id>
```

That is the minimal Phase 2 runtime loop.

## Fuller Example

This example adds two important runtime features:

- `room_prompt` discovery
- an `objective` step that advances off real `look` events using
  `unique_count`

### Step 1: Make Sure You Have Three Rooms In A Line

This snippet reuses the first room in the world and creates two rooms east of
it if they do not already exist:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py shell -c "
from worlds.models import World
world = World.objects.get(pk=<world_id>)
room_one = world.rooms.order_by('id').first()
room_two = room_one.east or room_one.create_at('east')
room_three = room_two.east or room_two.create_at('east')
print(room_one.id, room_two.id, room_three.id)
"
```

Treat the printed ids as:

- `<room_one_id>`
- `<room_two_id>`
- `<room_three_id>`

Re-run `ensure-player` so the player starts back in the first room:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py ensure-player \
    --world <world_id> \
    --email quest-playground@example.com \
    --name Questplay
```

### Step 2: Apply the Quest

Create `/tmp/shrine_survey.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/shrine_survey.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: shrine_survey
  name: Shrine Survey
spec:
  type: quest
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: room_prompt
        room: room.<room_one_id>
    visible_if: {}
    accept_if: {}
    salience: 20
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: A weathered placard asks you to survey the shrines ahead.
      lead: Decide whether to take the survey.
      stakes: The route will stay dangerous if nobody checks it.
      choices:
        - id: begin
          text: Take the survey.
          goto: survey
    - id: survey
      kind: objective
      recap: You accepted the survey route.
      lead: Look around at both shrines to confirm they are intact.
      stakes: If either shrine has collapsed, travelers need warning.
      objectives:
        - id: inspect_shrines
          text: Inspect both shrines.
          tracker:
            event: cmd.look.success
            where:
              all:
                - eq: [event.target_type, room]
                - in: [event.target.id, [<room_two_id>, <room_three_id>]]
          progress:
            mode: unique_count
            target: 2
            distinct_by: event.target.id
      transitions:
        - when:
            objective_complete: inspect_shrines
          goto: resolved
    - id: resolved
      kind: resolution
      recap: You surveyed both shrines and the route is safe enough to report.
      lead: ''
      stakes: ''
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
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/shrine_survey.yml
```

### Step 3: Discover It

Run `look` in the first room:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
```

You should see a `quest.opportunity.available` message.

You can confirm it explicitly:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "quest opportunities"
```

### Step 4: Accept And Begin It

Accept the opportunity:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "quest accept shrine_survey"
```

Move from the offer step into the objective step:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "quest choose shrine_survey begin"
```

Check the recap:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py recap --player <player_id>
```

You should now see an objective with progress `0/2`.

### Step 5: Progress It Off Real Game Events

Move east and look:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "east"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
```

Move east again and look again:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "east"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
```

What should happen:

- the first shrine look advances objective progress to `1/2`
- the second shrine look advances progress to `2/2`
- the transition fires automatically
- the quest resolves

Confirm the resolved quest list:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py resolved --player <player_id>
```

## Item Turn-In Example

This is the first common MMO quest loop now supported:

- bring `x` copies of item `y` to a mob
- grant typed rewards on completion
- let the turn-in mob execute a completion command

### Step 1: Seed The Mob And Items Into The Player's Current Room

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py shell -c "
from builders.models import ItemTemplate, MobTemplate
from spawns.models import Player
player = Player.objects.select_related('room', 'world', 'world__context').get(pk=<player_id>)
author_world = player.world.context or player.world
spawn_world = player.world
quartermaster_template, _ = MobTemplate.objects.get_or_create(
    world=author_world,
    name='Quartermaster',
    defaults={'keywords': 'quartermaster'},
)
if not spawn_world.mobs.filter(room=player.room, template=quartermaster_template).exists():
    quartermaster_template.spawn(player.room, spawn_world)
pelt_template, _ = ItemTemplate.objects.get_or_create(world=author_world, name='Wolf Pelt')
herb_template, _ = ItemTemplate.objects.get_or_create(world=author_world, name='Moonleaf')
pelt_template.spawn(player, spawn_world)
pelt_template.spawn(player, spawn_world)
herb_template.spawn(player, spawn_world)
print(quartermaster_template.id, pelt_template.id, herb_template.id)
"
```

Treat the printed ids as:

- `<quartermaster_template_id>`
- `<pelt_template_id>`
- `<herb_template_id>`

### Step 2: Apply The Quest

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/quartermaster_supplies.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: quartermaster_supplies
  name: Quartermaster Supplies
spec:
  type: quest
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 10
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: turn_in
      kind: objective
      recap: The quartermaster needs pelts and moonleaf.
      lead: Bring 2 wolf pelts and 1 moonleaf to the quartermaster.
      stakes: The camp cannot restock without those supplies.
      objectives:
        - id: deliver_pelts
          text: Deliver 2 wolf pelts.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.template_id, <quartermaster_template_id>]
                - eq: [event.item.template_id, <pelt_template_id>]
          progress:
            mode: count
            target: 2
        - id: deliver_herb
          text: Deliver 1 moonleaf.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.template_id, <quartermaster_template_id>]
                - eq: [event.item.template_id, <herb_template_id>]
          progress:
            mode: count
            target: 1
      transitions:
        - when:
            all:
              - objective_complete: deliver_pelts
              - objective_complete: deliver_herb
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The quartermaster signs off on the delivery.
      lead: ''
      stakes: ''
  rewards:
    complete:
      - type: grant_gold
        amount: 10
      - type: grant_xp
        amount: 50
      - type: mob_command
        command: /echo room Delivery accepted.
    compromised: []
    failed_forward: []
    expired: []
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/quartermaster_supplies.yml
```

### Step 3: Start It And Turn It In

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "give all.pelt quartermaster"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "give moonleaf quartermaster"
```

What should happen:

- the quest objective progresses off `quest.item.delivered`
- the quest resolves on the final hand-in
- the player receives `10` gold and `50` experience
- the quartermaster executes `/echo room Delivery accepted.`

## Kill Then Report Example

This is the second common loop now supported:

- kill `x` mobs
- return to a specific mob
- complete by talking to that mob

### Step 1: Seed The Captain And Rats

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py shell -c "
from builders.models import MobTemplate
from spawns.models import Player
player = Player.objects.select_related('room', 'world', 'world__context').get(pk=<player_id>)
author_world = player.world.context or player.world
spawn_world = player.world
captain_template, _ = MobTemplate.objects.get_or_create(
    world=author_world,
    name='Captain Merrow',
    defaults={'keywords': 'captain merrow captain'},
)
rat_template, _ = MobTemplate.objects.get_or_create(
    world=author_world,
    name='Tunnel Rat',
    defaults={'keywords': 'rat tunnel rat'},
)
if not spawn_world.mobs.filter(room=player.room, template=captain_template).exists():
    captain_template.spawn(player.room, spawn_world)
rat_template.spawn(player.room, spawn_world)
rat_template.spawn(player.room, spawn_world)
print(captain_template.id, rat_template.id)
"
```

Treat the printed ids as:

- `<captain_template_id>`
- `<rat_template_id>`

### Step 2: Apply The Quest

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend sh -lc "cat > /tmp/rat_cull.yml <<'EOF'
kind: quest
metadata:
  world: world.<world_id>
  slug: rat_cull
  name: Rat Cull
spec:
  type: quest
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 10
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: hunt
      kind: objective
      recap: Captain Merrow wants the tunnel rats culled.
      lead: Kill 2 tunnel rats.
      stakes: They are chewing through the camp stores.
      objectives:
        - id: kill_rats
          text: Kill 2 tunnel rats.
          tracker:
            event: quest.mob.killed
            where:
              eq: [event.target.template_id, <rat_template_id>]
          progress:
            mode: count
            target: 2
      transitions:
        - when:
            objective_complete: kill_rats
          goto: report
    - id: report
      kind: objective
      recap: The rats are down. Report back to Captain Merrow.
      lead: Talk to Captain Merrow.
      stakes: The camp is waiting on your report.
      objectives:
        - id: report_back
          text: Talk to Captain Merrow.
          tracker:
            event: cmd.talk.success
            where:
              eq: [event.target.template_id, <captain_template_id>]
          progress:
            mode: boolean
            target: 1
      transitions:
        - when:
            objective_complete: report_back
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Captain Merrow confirms the camp is safe for now.
      lead: ''
      stakes: ''
  rewards:
    complete:
      - type: grant_gold
        amount: 8
      - type: mob_command
        command: say Good work.
    compromised: []
    failed_forward: []
    expired: []
EOF"
```

Apply it:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_manifest_playground.py apply --world <world_id> --file /tmp/rat_cull.yml
```

### Step 3: Start It, Kill, Then Report

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "look"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "kill rat"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "kill rat"
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python scripts/quest_runtime_playground.py cmd --player <player_id> "talk captain"
```

What should happen:

- each `kill rat` emits `quest.mob.killed`
- after the second kill, the quest advances to the report-back step
- `talk captain` satisfies the return objective
- Captain Merrow executes `say Good work.` on completion

## Runtime API Surface

If you want to hit the HTTP endpoints directly instead of using the runtime
playground script, the Phase 2 runtime currently exposes:

- `GET /api/v1/game/quests/opportunities/`
- `POST /api/v1/game/quests/opportunities/<slug>/accept/`
- `GET /api/v1/game/quests/active/`
- `GET /api/v1/game/quests/resolved/`
- `GET /api/v1/game/quests/instances/<instance_id>/recap/`
- `POST /api/v1/game/quests/instances/<instance_id>/choose/`
- `POST /api/v1/game/quests/instances/<instance_id>/abandon/`

These require normal game auth plus `X-Player-Id`.

## Testing

Focused Phase 2 runtime tests:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose exec backend \
  python manage.py test \
    wr2_tests.test_quest_runtime \
    wr2_tests.test_quest_manifests \
    --settings=config.settings.testing
```

Broader regression run used for this pass:

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

## What The Minimal Case Proves

The minimal `tiny_hello` flow is valuable because it isolates the essence of
the system:

- authored quest template
- runtime discovery
- quest instance creation
- journal/recap output
- storylet choice handling
- resolution

If that path works, the architecture is sound. The fuller example then proves
that the same runtime can also handle a normal quest with event-driven
objective progress.
