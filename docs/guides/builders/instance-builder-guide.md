# Instance Builder Guide

This guide explains how WR2 builders should think about creating, connecting,
and administering instances.

For the deeper architecture, see
[instance-system.md](/Users/teebes/code/writtenrealms/docs/architecture/instance-system.md).

## Mental Model

An instance is a private or semi-private playthrough of an authored instance
template.

There are three layers:

| Layer | Builder meaning |
| --- | --- |
| Base world | Owns shared definitions and player progression. |
| Instance template | Owns the instance layout, local config, spawn plans, and future goal/timer policy. |
| Instance run | One live or historical playthrough created when a player or group enters. |

Builders edit the base world and instance templates. Players create instance
runs by entering the instance during play.

## Implementation Status

The foundation exists now:

- entering an instance creates an `InstanceRun`
- each player in that run gets an `InstanceParticipant`
- participation records the exact base runtime used for return; exit never
  rediscovers or guesses a runtime shard
- group members can join the same run by instance reference
- leaving marks the participant exited instead of deleting the run
- carried and equipped item ownership moves into and out of the spawned
  instance world with bounded, depth-batched traversal
- players can use `enter`, `enter <instance_ref>`, `leave`, and `instance`
  from the game command input
- builders and trusted room/mob scripts can use `/exitinstance` to return a
  player to one explicitly chosen authored room in the recorded base runtime
- builder/admin payloads expose run state and participant counts
- world, zone, and room state is isolated per active run
- `/reset` reseeds only the current run from the template's authored defaults
- `pvp_mode: match` templates support private 1v1 duels created through
  challenge and acceptance

Goal, timer, clear-time, and leaderboard evaluation are the next layer. The
manifest examples in this guide describe the target authoring shape. They are
included so builders can design instance content now and so implementation work
has concrete acceptance examples.

Only the ordinary world, room, zone, path, mob definition, item definition, and
spawn plan manifests are currently applied by **World > Edit**. `kind:
instance` goal manifests are not ingested yet.

## What Instances Inherit

Instances should use WR Core definitions from the base world:

- item definitions
- mob definitions
- item bundles
- currencies
- socials
- abilities
- leveling configuration
- stat and equipment systems
- combat formulas
- combat availability and combat pacing
- duel-result announcement policy
- merchant profiles

Do not create separate item or mob libraries inside an instance. Spawn plans in
the instance should reference the base world's `ItemDefinition`,
`MobDefinition`, and `ItemBundle` content. In YAML, that means an instance
template spawn plan can use refs such as `mobdefinition.sparabara` even though
`sparabara` is authored on the base world. The entry target still belongs to the
instance template, so use the instance's own `zone@`, `room@`, and `path@`
references for placement.

## What Instances Own

An instance template owns its own playable space and run policy:

- zones
- rooms
- paths
- room flags and details
- authored world, zone, and room `initial_state`
- local room/zone/world triggers
- spawn plans
- entry room
- death room
- exit behavior
- future goal, timer, leaderboard, and cleanup policy

The death room for an instance must be inside the instance template. By
default, a player who dies inside an instance follows the template's local
death routing and remains in that run. A template may explicitly delegate
destination selection to its base world; that death atomically exits the
participant to the exact base runtime recorded at entry.

## State And Instance Boundaries

An instance template authors defaults, while each instance run owns mutable
state. Put seed values under `spec.initial_state` in the template's world,
zone, and room manifests:

```yaml
kind: world
spec:
  initial_state:
    alarm_raised: false
---
kind: zone
metadata:
  ref: zone@1
  name: Prison Camp
spec:
  initial_state:
    prisoners_freed: 0
---
kind: room
metadata:
  ref: room@12
  name: Command Tent
spec:
  coordinates:
    x: 4
    y: 2
    z: 0
  zone: zone@1
  initial_state:
    map_taken: false
```

When a run starts, WR2 copies those values into that run's runtime world. A
second run receives a different copy. Commands, conditions, and templates such
as `state.world.alarm_raised` always resolve against the exact current run.

The base world's live state is not an implicit parent state for an instance.
Likewise, editing template defaults does not overwrite an active run. Use an
explicit future shared-state feature if gameplay ever needs cross-run mutable
state; do not rely on hidden base-world fallback.

Player character state follows the player into and out of instances. Mob
character state belongs to the spawned mob. A mob definition may seed every
copy, and a spawn entry may add or override `initial_state` for one placement.
That state is removed when the mob is removed.

## Creating An Instance Template

From the base world:

1. Open **World > Config > Instances**.
2. Create a new instance.
3. Open the created instance template.
4. Build its zones, rooms, paths, and spawn plans.
5. Configure its starting room and death room.
6. Link a base-world room to the instance template from the base room's config.
7. If the instance has a goal, draft the instance goal manifest alongside the
   spawn plans so the completion cohort is clear.

When editing an instance template, open **World > Config** and use the base
world config link to jump directly back to the base world's config screen.

The instance template editor should feel like editing a small world, but its
resource libraries come from the base world.

Instance templates do not own a separate ruleset. WR2 always resolves core
systems through the base world while the player is inside a spawned instance.
That includes stat formulas, combat formulas, combat availability, combat
resolution timing, equipment rules, leveling, max level, starting level, and
ability progression. Instance config manifests and direct config API updates
reject those fields for instance worlds, even if a builder manually navigates to
the underlying editor URL.

The instance template owns instance-local content and policy instead: starting
room, death room, zones, rooms, paths, spawn plans, goals, timer settings,
cleanup policy, presentation fields, and supported local rules such as death
behavior. A base world can use a currency-loss death penalty while an instance
sends dead players to an instance-local death room and uses a different
supported death mode, but every currency reference still resolves against the
base-world catalog.

Instance world config manifests only accept local fields: identity text,
visibility, starting/death rooms, initial state, death mode, deterministic
death routing and its source, death currency and penalty, PvP policy, builder
credit, and background art.
Player-creation and global policy fields such as default currency, starting
balances, title rules, naming rules, globals, class selection, starting
equipment, leveling, stats, equipment, combat, and abilities belong to the
base world.

Learned player abilities also resolve through the base world while the player is
inside a spawned instance. Ability definitions cannot be authored on instance
templates; define them on the base world and use requirements or conditions when
an ability should only matter in a particular instance.

## Stable Template Identity And Family Export

Every direct authored instance template has an immutable `instance_slug`.
This is the template's portable identity inside its base-world family, not its
database id and not the runtime Instance ID shown to players.

For example, a template named **Hades** may have the stable scope
`instance.hades`. Renaming the template later changes its display name but not
that scope. A second sibling with the same generated name receives a suffixed
slug. Slugs are unique only inside their base family; they do not need to be
globally unique across WR2. Treat the exported slug as read-only. Changing it
in a hand-edited import selects or creates a different template; it is not how
you rename the existing template.

When a base world has one or more authored instance templates, exporting the
base produces a world-family bundle. Its first document declares the stable
scopes:

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
  links: []
```

Every following content document identifies its scope:

```yaml
---
kind: room
metadata:
  world_ref: world@base
  ref: room@12
  name: Hades Gate
spec:
  coordinates: {x: 4, y: 2, z: 0}
---
kind: room
metadata:
  world_ref: instance.hades
  ref: room@12
  name: Palace of Hades
spec:
  coordinates: {x: 0, y: 0, z: 0}
```

These two `room@12` refs do not collide. A stable room ref is local to its
`world_ref`; the bundle scope supplies the missing half of the address.
Ordinary refs inside each document—zones, paths, room exits, spawn targets,
Trigger targets, and structured conditions—remain local to that scope. A
literal `/transfer room@N` resolves later in the command issuer's current
authored runtime world; it is also not a cross-world address.

Supported relationships that actually cross the base/instance boundary are
stored centrally in `worldbundle.spec.links`:

- `room.enters_instance`: base room to instance template
- `room.transfer_to`: base room to a specific instance room
- `room.exits_to`: instance room back to a base room
- `world_config.exits_to`: instance config back to a base room

Each link endpoint names both its world scope and, where needed, its stable
room ref. These fields are intentionally absent from the individual scoped
room and world-config documents. Do not put a cross-world database id or an
assumed globally unique `room@N` in a Trigger or script; add a typed bundle
relation when the supported instance-link behavior is intended.

For the full header and link examples, see
[yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md#world-family-bundles).

## Moving A Family From Development To Production

Use the base world as the unit of portability:

1. In development, export the authored base world, not an individual linked
   instance template.
2. Keep the complete YAML stream together. The first document is the
   `worldbundle` header, followed by the base and template content documents.
3. On the destination installation, create or select the multiplayer authored
   base world that should receive the family. WR2 rejects a family bundle
   applied to a single-player target instead of risking an unsafe conversion
   of existing runtime state.
4. As a rank 3+ builder, apply the complete stream through
   **World > Edit World**.
5. Verify the base and each instance template, then re-export the destination
   family if you want a canonical comparison.

The destination's selected base world becomes `world@base`. WR2 matches an
existing direct template by `instance_slug` or creates a missing one, so no
development database world id has to match production. The declaration name
is descriptive; the slug is identity.

The complete bundle is atomic. WR2 reserves stable rooms independently within
each scope, applies every scoped document, then resolves the central links in
one batched pass. A failure anywhere rolls back content changes, newly created
templates, and link replacement. `links_mode: replace` means the declared
scopes' managed cross-world link fields become exactly the header's link list;
an empty list clears them. Existing instance templates omitted from a
hand-authored bundle are not deleted, although canonical export includes every
direct template.

Only non-archived authored templates are portable content. Spawned instance worlds,
runtime `instance_ref` values, runs, participants, live room/world state,
players, and inventory are never exported. A base with no templates keeps the
ordinary single-world format. An instance template cannot be exported safely
by itself; export its base family instead.

## Configuring Death Routing

Instance templates support two destination-policy sources:

```yaml
kind: world
spec:
  starting_room: room@1
  death_room: room@2
  death_routing_source: local
  death_routing:
    routes:
      - when:
          eq: [zone.id, zone@3]
        destination: room@2
      - when:
          gte: [player.level, 20]
        destination: room@4
      - when:
          eq: [player.archetype, warlord]
        destination: room@3
```

`local` is the default. The template's compiled policy and `death_room`
determine the destination, and the player stays inside the current run. Route
conditions may inspect the player's faction, class, level, or character state,
plus the room zone where the player died. The first matching route wins.
These stable refs are scoped to the instance template and continue to identify
the same authored rooms if their map coordinates change.

Level thresholds use inclusive `gte` and `lte` comparisons. For example,
`gte: [player.level, 20]` means level 20 or higher.

To route through the base world instead:

```yaml
kind: world
spec:
  death_routing_source: base_world
  death_room: room@2
```

`base_world` selects the base world's complete policy, including its fail-safe
room; it does not merge base and local routes. The local policy remains stored
and can be re-enabled later by switching the source back to `local`.
Core-faction, class, level, and character-state conditions in the base policy
apply to delegated deaths. Base-world zone references do not match
instance-template zones; use local routing when an instance's own zones must
select its death destination.

Only destination selection delegates. The instance's `death_mode`, currency
loss, equipment destruction, drops, and corpse creation still apply in the
origin instance. After the penalty, surviving carried and equipped items move
with the player to the recorded return runtime. A missing or invalid return
record uses the instance's local `death_room` and does not guess another base
runtime.

## Connecting A Base Room To An Instance

Use the base-world room config to choose the instance room players enter.

The base room is the ordinary return point remembered for the player. The
participant also records the exact spawned base runtime containing that room.
When the player leaves, WR2 uses that protected runtime relation rather than
searching for whichever base runtime happens to exist. Delegated death uses
the recorded runtime with the base death-routing destination instead of the
entrance room.

For group play, the leader's created run has a shared instance reference.
Members can join that same run by entering through the shared reference instead
of creating their own separate run.

## Building Interaction-Specific Exits

Use `/exitinstance` when different interactions inside one instance should
lead to different rooms in the base world. Its destination has an explicit
base-world scope:

```text
/exitinstance <player-target> world@base/room@<relative_id>
```

For example, these two command Triggers can offer a right-hand exit to Athens
and a left-hand exit to Sparta from the same instance room:

```yaml
kind: trigger
metadata:
  name: Exit Right To Athens
spec:
  scope: room
  kind: command
  target: room@9
  match: go right
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: command
          subject: trigger_room
          command: /exitinstance {{ actor_key }} world@base/room@42
---
kind: trigger
metadata:
  name: Exit Left To Sparta
spec:
  scope: room
  kind: command
  target: room@9
  match: go left
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: command
          subject: trigger_room
          command: /exitinstance {{ actor_key }} world@base/room@87
```

Here `room@9` belongs to the instance template, while base `room@42` and
`room@87` are resolved from the target player's active run. WR2 then places
the player in that authored room inside the exact base runtime recorded when
the participant entered. It does not route the player to another base shard.

The qualified destination is intentionally specific to `/exitinstance`.
`/transfer` continues to resolve destinations within one live runtime world
and cannot cross an instance boundary. Normal `leave` continues to use the
remembered entrance rather than one of these interaction-specific routes.

In typed `steps`, `/exitinstance` may target only `trigger_actor` and must be
the only action in the final step. Put any narration, payment, reward, or state
change in an earlier step. A direct builder or trusted room/mob script can use
the same command outside typed steps. Active duel contestants cannot be exited
this way; the duel must finish or be surrendered through its normal lifecycle.
A successful command records a `forced` participant exit, finishes ordinary
combat, cancels pending door work, and moves carried/equipped items and
character effects to the exact recorded return runtime.

## Building A Duel Arena

A duel arena uses the normal instance layout and entry link, but its run is
created only after a player accepts a challenge. This lets the base world
disable open-world PvP while enabling PvP in a clean, private runtime boundary.

Configure the pieces as follows:

1. Set the base world's `pvp_mode` to `disabled`.
2. Create an instance template belonging to that base world.
3. Set the instance template's local `pvp_mode` to `match`.
4. Build the arena rooms and paths, and choose an instance starting room.
5. Set a base-world room's `transfer_to` link to that starting room.
6. Optionally set the base world's `announce_duel_results` to `true`.

The linked base-world room is the arena entrance. Both contestants must be
standing in that same room when the challenge is issued and must remain there
until it is accepted. They must be out of combat and have no live hostile
character effects at acceptance time. Accepting creates a fresh private
`InstanceRun` for exactly those two contestants. Non-hostile character effects
follow their target into and out of the runtime; hostile effects created by
anything other than the opposing contestant cannot affect a duelist. Normal
`enter` and `enter <instance_ref>` calls cannot be used to create, join, or
rejoin a match arena outside that accepted duel.

`announce_duel_results` is base-world policy, is inherited while players are in
instances, and defaults to `false`. It cannot be overridden on an instance
template. When enabled, a completed duel announces this text to online players
in the base world and all of its active instances:

```text
<winner> has defeated <loser> in a duel.
```

Inside an active match, `kill <player>` and supported hostile abilities can
target only the opposing contestant. Charge-style abilities with
`move_actor: true` can move the caster through one arena exit and open combat
against that contestant in the adjacent room. In the current 1v1 workflow,
effect components targeting `room.allies` are narrowed to the caster so the
opponent is never treated as an ally. Broad `room.players` and
`room.hostiles` selectors remain unsupported for PvP. Participant rows already
store a role and team number as extension points; teams will still require
team-level result/opponent changes, and spectators will require an admission
workflow.

For the contestant-facing workflow, see
[duels.md](../players/duels.md).

### Match And Encounter Lifecycles

The duel match lasts for the entire arena contest. A combat encounter lasts
only while the contestants are actively fighting in one room. A multi-room
arena can therefore produce several combat encounters within one match.

In particular, `flee` remains the normal two-step combat escape and is ordinary
arena gameplay. A successful flee moves the player, closes only the current
encounter, and **never** counts as a forfeit. Contestants can pursue through the
arena and re-engage. The duel resolves only when a contestant is defeated or
uses `duel surrender`.

Arena scripts follow the same split between encounter movement and match
resolution. A room or mob `/transfer` that separates the contestants closes
the current encounter and clears/refunds its pending combat actions, but keeps
the duel active. A room or mob `/kill` treats its target as the loser, awards
the opposing contestant the win, and resolves the duel instead of sending the
target through the ordinary death-room pipeline.

Resolution atomically records the winner and loser, completes the instance run,
and disables further combat in that run. The engine restores both contestants'
resources, but it leaves them in the completed arena so they can see the
outcome and leave normally. Both must `leave` and create a new challenge, which
produces a new private run, before they can fight again.

If every contestant disconnects and the arena reaches normal idle cleanup, the
runtime is abandoned rather than scored: encounters close, resources are
restored, contestants return to the base world, and no duel record changes.

The result also updates persistent character state:

| State key | Increment |
| --- | --- |
| `state.character.duels_fought` | Once for each contestant |
| `state.character.duels_won` | Once for the winner |
| `state.character.duels_lost` | Once for the loser |

Builders can read these keys through the existing condition DSL and state
template syntax. Do not add separate duel-only predicates.

### Isolation And Scale

Each accepted duel owns a fresh spawned runtime world. Runtime room targeting,
combat, communication, triggers, and scoped room/zone state are constrained by
both the spawned world and authored room, so parallel copies of the same arena
cannot see or affect one another.

The runtime uses indexed match, participant, run, and encounter rows rather
than scanning every player or room. Only active encounters schedule combat
work, and result finalization is idempotent so worker retries cannot increment
records twice. Enabling world-wide result announcements adds one bounded fanout
per completed duel across the base world's online population; leave the
default off unless that broadcast is a deliberate part of the world design.

## Player Commands

Once a base-world room is linked to an instance entry room, players use normal
game commands from the linked base room.

| Command | Use |
| --- | --- |
| `enter` | Start or re-enter the player's active run for that instance. |
| `enter <instance_ref>` | Join an existing active run for the same instance template. |
| `leave` | Leave the current instance and return to the remembered base-world room. |
| `instance` | Show the linked entrance, or show the current run's Instance ID while inside. |
| `/exitinstance <player> world@base/room@N` | Builder/trusted-script: exit a player to one explicitly chosen authored room in their recorded base runtime. |
| `/repop [--doors]` | Builder-only: refill missing spawn-plan placements in the current instance zone, optionally resetting its runtime doorways, without rebuilding the run. |
| `/reset` | Builder-only: reset the current instance run to its initial spawned state. |
| `duel <player>` | Challenge a player at the same match-arena entrance. |
| `duel accept [player]` | Accept a pending challenge and enter a fresh private match run. |
| `duel decline [player]` | Decline a pending challenge. |
| `duel cancel` | Cancel a challenge you issued. |
| `duel` / `duel status` | Show the current challenge or active match. |
| `duel surrender` | Concede an active duel; unlike `flee`, this ends the match. |

At a linked base-world room, the room look output includes an **Enter** action.
Selecting it issues the normal `enter` command.

When the first player enters, WR2 prints the run's Instance ID in the room
output. The leader can share that ID with group members. A group member should
stand at the same linked entrance and type `enter <instance_ref>` to join the
leader's run.

If a player types `enter` in a room with no instance link, WR2 reports that
there is no instance entrance there. If a player types `leave` outside an
instance, WR2 reports that they are not in an instance.

Builders inside an instance can type `/reset` to rebuild that active run in
place. The Instance ID and active participants are kept, player inventory and
equipment are preserved, active participants are moved to the instance starting
room, world/zone/room state is reseeded from the template's `initial_state`,
and the instance reruns its initial spawn plans. Player character state is
preserved. The reset affects only that run; other active runs of the same
template keep their state.

Use `/repop` for the narrower testing operation: it reconciles only the active
spawn plans in the builder's current zone, bypassing their normal wait times
and `respawn.mode: none` while retaining live placement output and scoped
runtime state. Doors remain unchanged unless `--doors` is supplied; that option
resets the zone's runtime doorway overrides to authored defaults without
consuming the normal zone reset timer. Both forms affect only the current
instance run.

## Group Play

Instances support collaborative runs.

When a leader enters an instance, WR2 creates an `InstanceRun` and records the
leader as an `InstanceParticipant`. When another player joins by the same
instance reference, they are recorded as a member participant on the same run.

If a player previously had a solo run and then joins a group's run, their old
solo participation is marked exited. The old run is not destroyed immediately;
cleanup policy handles inactive runs later.

Current inactive cleanup is hardcoded rather than builder-configurable. When the
last active player leaves a spawned instance, WR2 records activity on the
`InstanceRun` and the world monitor keeps the empty instance alive for about five
minutes, plus up to one monitor tick, before stopping and deleting the spawned
runtime world.

## Inventory And Equipment

When a player enters an instance, WR2 moves the world ownership of carried and
equipped items into the spawned instance world.

This includes nested contents, such as:

- a bag in inventory
- a pouch inside the bag
- an item inside the pouch
- equipped weapons or armor

When the player leaves, the same carried/equipped item tree moves back to the
base spawned world.

This keeps item ownership aligned with the player's current runtime world while
preserving normal inventory and equipment containers.

## Administering Instance Runs

The builder admin instance view exposes spawned instance world details.

The backend now records first-class run state:

- run id
- instance reference
- status
- leader
- started time
- last active time
- participant count
- active participant count
- initial member ids

Use this information to distinguish:

- the authored instance template
- the spawned runtime world
- the active or historical run record
- each player's participant record

Leaving an instance marks the participant exited. It does not immediately delete
the run.

## Current Runtime Statuses

Instance runs currently use these status names:

| Status | Meaning |
| --- | --- |
| `created` | Run row exists but gameplay has not started. |
| `active` | Participants may enter and the instance can progress. |
| `resolving` | Completion or failure is being finalized. |
| `completed` | Goal completed successfully. |
| `failed` | Goal failed. |
| `expired` | Timer expired. |
| `abandoned` | Inactive run was abandoned. |
| `closed` | Run no longer accepts entry. |
| `cleaned` | Runtime contents have been cleaned. |

The initial implementation creates active runs and participant history. Goal,
timer, leaderboard, and cleanup automation should build on this state rather
than inventing another run model.

## Goals And Timers

Instance goals are the target authoring model. A goal belongs to the instance
template and is evaluated separately for each `InstanceRun`.

Goals should be authored in a future `kind: instance` manifest. The runtime
should store the normalized goal spec on the run, track objective progress, and
evaluate completion through the shared WR2 condition DSL.

Planned goal styles:

- no goal
- kill a specific boss
- kill one of several key mobs
- kill all selected key mobs
- clear all completion-counting mobs
- satisfy a custom condition

Goal conditions should use the shared WR2 condition DSL. Do not create a new
predicate format for instances.

Timer support should record server-side start and completion timestamps so WR2
can produce clear times and fastest-clear records.

### Goal Manifest Shape

Target shape:

```yaml
kind: instance
metadata:
  world: world.1
  slug: blackfin-hideout
  name: Blackfin Hideout
spec:
  entry_room: room@1
  death_room: room@2

  goal:
    starts_on: first_entry
    objectives:
      - id: clear_blackfin
        type: clear_initial_cohort
        cohort: blackfin_forces
    complete_when:
      objective_complete: clear_blackfin

  timer:
    starts_on: first_entry
    record_clear_time: true
    show_to_players: true

  leaderboard:
    enabled: true
    metric: clear_time_ms
    scope: party
```

`starts_on: first_entry` means the timer starts when the first participant
enters the run. Clear time should be computed on the server as
`completed_at - started_at`.

`record_clear_time: true` means a successful completion should write the run's
clear time into the run outcome and into a future clear-record table used by
leaderboards.

### Objective Event Shape

For kill goals, objectives listen to `mob.died`-style events.

Target shape:

```yaml
goal:
  starts_on: first_entry
  objectives:
    - id: kill_captain
      event: mob.died
      count: 1
      unique_by: event.target.spawn_placement_id
      where:
        eq:
          - event.target.spawn_entry_slug
          - blackfin-captain
  complete_when:
    objective_complete: kill_captain
```

For "kill one of these bosses," use one objective that matches several entry
slugs:

```yaml
goal:
  starts_on: first_entry
  objectives:
    - id: kill_one_lieutenant
      event: mob.died
      count: 1
      unique_by: event.target.spawn_placement_id
      where:
        in:
          - event.target.spawn_entry_slug
          - [dock-lieutenant, forge-lieutenant, shrine-lieutenant]
  complete_when:
    objective_complete: kill_one_lieutenant
```

The run outcome should record which target satisfied the objective so later
quest, reward, and narrative systems can react to the choice.

## Example: Timed Faction Clear

Suppose you want an instance where the player or group clears every Blackfin mob
and WR2 records how long the clear took.

### Choose Core Or Reputation Faction

Use a core faction when the faction is a character identity or major alignment,
such as a player race, nation, order, or side.

Use a reputation faction when the faction is a local organization, reputation
group, dungeon enemy set, or story group whose standing can change.

For a one-instance hostile force, start with a reputation faction such as
`blackfin`.

If the group is only a temporary encounter marker and does not need standing,
use spawn entry slugs or traits instead of creating a faction.

### Author The Mobs

Create the base-world mob definitions normally:

```yaml
kind: mobdefinition
metadata:
  slug: blackfin-cutthroat
  name: a Blackfin cutthroat
spec:
  type: humanoid
  keywords: blackfin cutthroat pirate
  level: 5
  health_max: 45
  attack_power: 7
  weapon_damage: 6
  factions:
    reputation:
      blackfin: 100
```

```yaml
kind: mobdefinition
metadata:
  slug: blackfin-captain
  name: the Blackfin captain
spec:
  type: humanoid
  keywords: blackfin captain pirate
  level: 8
  health_max: 120
  attack_power: 14
  weapon_damage: 10
```

### Spawn The Completion Cohort

On the instance template, author the population with `respawn.mode: none` for
the completion-counting mobs. This avoids a clear condition that refills itself
while the run is in progress.

This spawn plan is valid current YAML. The `traits.guaranteed` values are stored
on generated placements and spawned mobs as metadata; the goal runtime can use
them later as cohort markers.

```yaml
kind: spawnplan
metadata:
  slug: blackfin-hideout-population
  name: Blackfin Hideout Population
spec:
  zone: zone@1
  randomization:
    seed_scope: instance
  respawn:
    mode: none
  entries:
    - slug: blackfin-raiders
      source: mobdefinition.blackfin-cutthroat
      target: zone@1
      count:
        min: 8
        max: 12
      traits:
        guaranteed:
          - key: instance_clear_required
          - key: faction_blackfin

    - slug: blackfin-captain
      source: mobdefinition.blackfin-captain
      target: room@12
      count: 1
      traits:
        guaranteed:
          - key: instance_clear_required
          - key: faction_blackfin
          - key: boss
```

When these mobs are spawned, their runtime metadata includes spawn-plan origin
data and trait keys. Future mob death events should expose enough of that data
for instance objectives to evaluate against it.

### Target Goal Manifest

Once `kind: instance` goal ingestion exists, the timed clear should look like
this:

```yaml
kind: instance
metadata:
  world: world.1
  slug: blackfin-hideout
  name: Blackfin Hideout
spec:
  entry_room: room@1
  death_room: room@2

  goal:
    starts_on: first_entry
    cohorts:
      - id: blackfin_forces
        source: initial_spawned_mobs
        spawn_plan: blackfin-hideout-population
        include_traits:
          - instance_clear_required
          - faction_blackfin
    objectives:
      - id: clear_blackfin_forces
        type: clear_cohort
        cohort: blackfin_forces
    complete_when:
      objective_complete: clear_blackfin_forces

  timer:
    starts_on: first_entry
    record_clear_time: true
    show_to_players: true

  leaderboard:
    enabled: true
    metric: clear_time_ms
    scope: party
```

The exact cohort syntax may still change during implementation. The important
builder-facing contract is stable:

- define the initial population that counts
- disable respawn for completion-counting mobs
- mark those mobs with stable identifiers
- start the timer on first entry
- complete when every initial cohort member is dead
- record `clear_time_ms` for leaderboard use

### Future Faction-Based Version

Once mob definitions expose faction assignment directly and death events include
faction data, the same goal should be expressible without trait markers:

```yaml
goal:
  starts_on: first_entry
  cohorts:
    - id: blackfin_forces
      source: initial_spawned_mobs
      where:
        eq:
          - mob.faction.code
          - blackfin
  objectives:
    - id: clear_blackfin_forces
      type: clear_cohort
      cohort: blackfin_forces
  complete_when:
    objective_complete: clear_blackfin_forces
```

That is the cleaner long-term version. The spawn-trait version is a practical
bridge for builders who want to design and test the instance layout now.

## Spawn Plans

Instance population should be authored with spawn plans.

Use spawn plans on the instance template to place base-world mob and item
definitions into instance rooms.

When a player enters an instance, WR2 starts the spawned instance world and runs
its initial spawn plans before placing the player inside. After that,
the normal spawn-plan scheduler can process the instance while its spawned world is
running.

An active instance keeps the spawn-plan placement snapshot created at run
start. Editing the template while players are inside does not add, remove, or
reroll that run's completion population. Reconciliation for the changed plan is
paused in that active instance, and the updated plan is used by the next new
instance run. This differs from ordinary running worlds, which hot-reconcile
builder edits on the next scheduler pass.

The source side inherits from the base world:

```yaml
source: mobdefinition.sparabara
```

The target side is local to the instance template:

```yaml
target: path@1
```

For future clear-all goals:

- use `respawn.mode: none` for completion-counting entries
- keep non-counting ambient spawns in separate entries or separate plans
- use stable entry slugs
- add guaranteed traits such as `instance_clear_required` and `faction_blackfin`
  as temporary cohort markers
- avoid source pools that mix completion and non-completion mobs in one entry

If you need ambient respawning mobs, put them in a separate spawn plan without
the clear-required trait. They can make the instance feel alive without blocking
the clear objective.

## Testing Expectations

New instance runtime behavior should be covered under `backend/tests/`.

Important cases:

- `enter` from a linked base room creates or re-enters a run
- `enter <instance_ref>` joins an existing run
- `leave` returns the player to the remembered base-world room
- `instance` reports the linked entrance or current run reference
- leader entry creates one run and one leader participant
- group member entry by reference joins the same run
- joining a group run exits the player's previous solo participation
- enter moves inventory and equipment into the instance recursively
- leave moves inventory and equipment back recursively
- leave records participant exit without deleting the run
- future goal and timer behavior updates the same `InstanceRun`

## Builder Rule Of Thumb

Build the base world as the shared game. Build each instance template as its
own place inside that game.

Use inherited definitions for what things are. Use the instance template for
where those things are, how they load, and what a run is trying to accomplish.
