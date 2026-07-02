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
- group members can join the same run by instance reference
- leaving marks the participant exited instead of deleting the run
- carried and equipped item ownership moves into and out of the spawned
  instance world recursively
- players can use `enter`, `enter <instance_ref>`, `leave`, and `instance`
  from the game command input
- builder/admin payloads expose run state and participant counts

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
- local room/zone/world triggers
- spawn plans
- entry room
- death room
- exit behavior
- future goal, timer, leaderboard, and cleanup policy

The death room for an instance should be inside the instance template. A player
who dies inside an instance should not be sent to the base world's death room
unless the instance explicitly closes or ejects them.

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

The instance template editor should feel like editing a small world, but its
resource libraries come from the base world.

## Connecting A Base Room To An Instance

Use the base-world room config to choose the instance room players enter.

The base room is the return point remembered for the player. When the player
leaves the instance, WR2 returns them to that remembered room unless a more
specific exit policy overrides it.

For group play, the leader's created run has a shared instance reference.
Members can join that same run by entering through the shared reference instead
of creating their own separate run.

## Player Commands

Once a base-world room is linked to an instance entry room, players use normal
game commands from the linked base room.

| Command | Use |
| --- | --- |
| `enter` | Start or re-enter the player's active run for that instance. |
| `enter <instance_ref>` | Join an existing active run for the same instance template. |
| `leave` | Leave the current instance and return to the remembered base-world room. |
| `instance` | Show the linked entrance, or show the current run's Instance ID while inside. |

When the first player enters, WR2 prints the run's Instance ID in the room
output. The leader can share that ID with group members. A group member should
stand at the same linked entrance and type `enter <instance_ref>` to join the
leader's run.

If a player types `enter` in a room with no instance link, WR2 reports that
there is no instance entrance there. If a player types `leave` outside an
instance, WR2 reports that they are not in an instance.

## Group Play

Instances support collaborative runs.

When a leader enters an instance, WR2 creates an `InstanceRun` and records the
leader as an `InstanceParticipant`. When another player joins by the same
instance reference, they are recorded as a member participant on the same run.

If a player previously had a solo run and then joins a group's run, their old
solo participation is marked exited. The old run is not destroyed immediately;
cleanup policy handles inactive runs later.

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
  entry_room: room@0,0,0
  death_room: room@0,1,0

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

### Choose Core Or Minor Faction

Use a core faction when the faction is a character identity or major alignment,
such as a player race, nation, order, or side.

Use a minor faction when the faction is a local organization, reputation group,
dungeon enemy set, or temporary story group.

For a one-instance hostile force, start with a minor faction such as
`blackfin`.

Current WR2 mob definitions do not yet expose faction assignment in their
manifest. Until that lands, mark the relevant spawn entries with guaranteed
spawn traits so the future goal runtime has a stable cohort marker.

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
      target:
        zone: zone@1
      count:
        min: 8
        max: 12
      traits:
        guaranteed:
          - key: instance_clear_required
          - key: faction_blackfin

    - slug: blackfin-captain
      source: mobdefinition.blackfin-captain
      target:
        room: room@4,2,0
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
  entry_room: room@0,0,0
  death_room: room@0,1,0

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
its initial loaders and spawn plans before placing the player inside. After that,
the normal loader scheduler can process the instance while its spawned world is
running.

The source side inherits from the base world:

```yaml
source: mobdefinition.sparabara
```

The target side is local to the instance template:

```yaml
target:
  path: path@1
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

New instance runtime behavior should be covered under `backend/wr2_tests/`.

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
