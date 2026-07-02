# WR2 Instance System

This document describes the target architecture for instances in WR2.

Instances are a core game structure: private or semi-private copies of authored
content that can have their own layout, local spawn behavior, lifecycle,
optional goal, timer, and cleanup policy while still belonging to a base world.

The current codebase already has the beginning of this model through
`World.instance_of`, instance template worlds, `World.instance_for()`, spawned
worlds, `InstanceAssignment`, and room `transfer_to` links. This document
defines the target shape those pieces should move toward.

## Related Docs

- `.codex/skills/wr-transition/wr2-architecture.md`
- [spawn-plan-loader-transition.md](/Users/teebes/code/writtenrealms/docs/architecture/spawn-plan-loader-transition.md)
- [scoped-state-system.md](/Users/teebes/code/writtenrealms/docs/architecture/scoped-state-system.md)
- [quest-system-endstate.md](/Users/teebes/code/writtenrealms/docs/architecture/quest-system-endstate.md)
- [condition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/condition-builder-guide.md)
- [instance-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/instance-builder-guide.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)

## Goals

- Make instances first-class runtime aggregates in WR2.
- Preserve the WR1 value proposition: builders can make good, usable private
  dungeon-style content without writing code.
- Let instance templates define their own zones, rooms, paths, spawn plans,
  entry room, death room, exit behavior, goals, timers, and cleanup policy.
- Let instance templates inherit shared base-world definitions such as items,
  mobs, currencies, socials, abilities, item bundles, leveling curve, stat
  system, equipment rules, and combat formulas.
- Support a small allowlist of instance config overrides, especially death
  behavior, without turning instances into hidden forks of the base world.
- Support optional goals:
  - no goal, free enter/exit
  - kill one or more specific mobs
  - kill all mobs in a completion cohort
  - satisfy arbitrary objective conditions
  - expire or fail after a time limit
- Support clear-time records and builder/player-visible leaderboards.
- Support inactive cleanup without deleting an instance immediately when the
  last player leaves.
- Fit WR2's `Command -> Action -> Event` direction and existing condition DSL.

## Non-Goals

- Do not invent a second condition language for instance goals.
- Do not use quests as the only way to express instance completion. Quest
  objective machinery is a useful pattern, but instances need shared runtime
  lifecycle even when no player quest is active.
- Do not duplicate every base-world definition into every instance template.
- Do not make arbitrary deep config overrides the default. Instance overrides
  should be explicit, visible, and limited.
- Do not destroy a run just because everyone left. Cleanup is lifecycle policy,
  not a side effect of `leave`.
- Do not make spawn plans reroll completion-relevant population every loader
  pass.

## Core Mental Model

There are three different objects that should not be collapsed into one noun:

| Concept | Current shape | Target responsibility |
| --- | --- | --- |
| Base world | Root `World` | Owns shared definitions, global systems, persistent player records, and builder permissions. |
| Instance template | `World` with `instance_of` | Owns authored instance layout, local config, local spawn plans, local triggers, goal policy, timer policy, and cleanup policy. |
| Instance run | Spawned `World` plus future runtime row | Owns one active or historical playthrough: participants, status, timer, progress, spawned mobs/items, and cleanup state. |

The base world answers: "What larger game does this belong to?"

The instance template answers: "What is this dungeon/scenario?"

The instance run answers: "What happened this time this player or group entered
it?"

## Ownership And Inheritance

Instance templates should inherit shared gameplay libraries from the base world
and own local layout/runtime-facing content.

### Inherited From Base World

These should be resolved from the base world by default and should not be
duplicated onto the instance template:

| Area | Examples | Notes |
| --- | --- | --- |
| Item definitions | `ItemDefinition` | Instance spawn plans can reference base-world item definitions. Spawned item rows still belong to the instance run's spawned world. |
| Mob definitions | `MobDefinition` | Instance spawn plans can reference base-world mobs. Spawned mob rows still belong to the instance run's spawned world. |
| Item bundles | Weighted reward/drop/load bundles | Bundles stay global so rewards mean the same thing inside and outside the instance. |
| Currencies | Gold, medals, custom currencies | Currency definitions are base-owned. Player balances remain player/global state, not instance definitions. |
| Socials | Shared emote commands | Social vocabulary should not fork per instance. |
| Abilities | Ability definitions and ability progression | Instances may restrict use later through policies, but definitions are inherited. |
| Leveling | Starting/max level, leveling curve | Instances should not have hidden XP curves unless a later explicit feature needs it. |
| Stats and equipment | `stat_system`, `equipment_system` | These are part of the player's world identity and combat balance. |
| Combat formulas | `combat_system`, attack routines, damage formulas | Inherit by default. Prefer encounter/spawn tuning over formula forks. |
| Merchant profiles | Shared shop definitions | Instance-local shop placement can reference inherited profiles. |

This matches the direction already visible in the frontend: many base-world
resource pages are hidden or redirected when editing an instance world.

### Owned By Instance Template

These should be authored on the instance template:

| Area | Examples | Notes |
| --- | --- | --- |
| Layout | Zones, rooms, exits, paths, room flags/details | The instance is its own physical space. |
| Entry config | Entry room, death room, fallback exit room | Room references must point at instance-template rooms. |
| Spawn mechanics | Spawn plans, spawn entries, respawn policy, guided randomization | Plans reference inherited mob/item definitions but run locally. |
| Room and zone state | Local mutable state | State is per run, not shared with the base world. |
| Local triggers | Room/zone/world triggers that refer to instance layout | Definition-attached inherited triggers need an explicit policy, described below. |
| Instance goal | Optional completion policy and objectives | Owned by the template and evaluated by each run. |
| Timer policy | Time limit, clear-time tracking, start moment | Owned by the template. |
| Cleanup policy | Inactive TTL, completed grace period, cleanup behavior | Owned by the template. |
| Presentation | Instance name, short description, optional banner/art | Useful for entry prompts and completion screens. |

### Trigger Inheritance

Triggers need a more careful split than item or mob definitions.

Recommended policy:

- Room-scoped and zone-scoped triggers are local to the world whose rooms/zones
  they reference.
- Instance templates may author their own room/zone triggers.
- Definition-attached mob/item triggers may be inherited with the definition if
  their target is a base-world definition rather than a base-world room.
- Base-world global triggers should not automatically run inside every instance
  unless they are explicitly marked as inheritable.

That keeps base-world systems from leaking into private content while still
allowing shared mob reactions or item behavior to work in instances.

## Effective Config

Instances need their own configuration without becoming full independent
worlds.

The target should be an explicit config resolver:

```python
effective_instance_config = resolve_instance_config(
    base_config=base_world.config,
    template_config=instance_template.config,
    overrides=instance_template.instance_config_overrides,
)
```

Current storage can continue using `WorldConfig` during the transition, but the
runtime should stop treating the instance template config as an entirely
separate root-world config. Instead, the meaning should be:

- base config supplies inherited systems
- instance config supplies required local room references
- instance overrides supply the allowed local differences

### Required Instance-Local Config

Every playable instance template should define:

| Field | Meaning | Rule |
| --- | --- | --- |
| `entry_room` / `starting_room` | Room participants enter when the run starts | Must be a room in the instance template. |
| `death_room` | Room participants go to when they die inside the run | Must be a room in the instance template. |
| `exit_room` / `exits_to` | Default base-world return room when no entry room is remembered | Must be a room in the base world or be omitted to use participant `transfer_from`. |

The important rule is that `death_room` inside an instance is not the base-world
death room. Death inside a private dungeon should resolve inside that dungeon
unless the instance explicitly closes or ejects the player.

### Config Override Allowlist

Instance overrides should be explicit and visible in the builder UI.

Good initial override candidates:

| Field | Why it makes sense |
| --- | --- |
| `death_mode` | A base world may use gold loss while an instance destroys equipment or drops inventory. |
| `death_gold_penalty` | Needed if the instance uses gold-loss death. |
| `death_route` | Needed if faction or route-specific death behavior should differ. |
| `allow_combat` | Useful for puzzle/social instances or safe staging areas. |
| `allow_pvp` / `pvp_mode` | Instances often need stricter PvP policy than the base world. |
| `combat_resolution_interval` | Encounter pacing may differ between open world and dungeon content. |
| `never_reload` or spawn reload policy | Completion-sensitive instances may need loader reconciliation disabled or constrained. |
| presentation fields | Instance lobby/entry art and text can differ from the base world. |
| cleanup policy | Run lifetime is inherently instance-specific. |

Fields that should stay inherited initially:

| Field | Reason |
| --- | --- |
| `leveling_curve` / `max_level` / `starting_level` | Hidden per-instance progression curves make player growth hard to reason about. |
| `stat_system` | Player stats should mean the same thing across the world. |
| `equipment_system` | Equipment slot and armor-class behavior should not fork silently. |
| `combat_system` formulas | Balance should come from mobs, traits, spawn plans, and encounter design before formula forks. |
| `ability_progression` | Learned/available abilities should remain base-world player progression. |
| currencies | Currency definitions must stay shared so rewards and penalties resolve consistently. |

If later we need formula overrides, they should be introduced as a deliberate
feature with strong builder UI warnings and manifest diff visibility.

## Runtime Aggregate

The current runtime uses spawned `World` rows as the effective instance run.
That is useful but not sufficient for goals, timers, leaderboards, and cleanup.

Target model:

```python
class InstanceRun(models.Model):
    base_world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    template_world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    spawned_world = models.OneToOneField("worlds.World", on_delete=models.CASCADE)

    ref = models.TextField(db_index=True)
    leader = models.ForeignKey("spawns.Player", null=True, on_delete=models.SET_NULL)
    status = models.TextField()

    created_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    last_active_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    failed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)
    cleanup_after = models.DateTimeField(null=True)

    goal_spec = models.JSONField(default=dict)
    progress = models.JSONField(default=dict)
    outcome = models.JSONField(default=dict)
    seed = models.TextField(blank=True)
```

```python
class InstanceParticipant(models.Model):
    run = models.ForeignKey(InstanceRun, on_delete=models.CASCADE)
    player = models.ForeignKey("spawns.Player", on_delete=models.CASCADE)
    role = models.TextField(default="member")
    transfer_from = models.ForeignKey("worlds.Room", null=True, on_delete=models.SET_NULL)
    joined_at = models.DateTimeField()
    exited_at = models.DateTimeField(null=True)
```

The spawned world remains the owner of concrete runtime objects:

- player location while inside the run
- spawned mobs
- spawned items
- spawn plan runs and placements
- room/zone/world scoped runtime state for that playthrough

`InstanceRun` owns lifecycle and history.

### Status Values

Recommended run statuses:

| Status | Meaning |
| --- | --- |
| `created` | Run row exists but no participant has entered yet. |
| `active` | Participants may enter and the instance can progress. |
| `resolving` | Completion/failure has fired and finalization is in progress. |
| `completed` | Goal completed successfully. |
| `failed` | Goal failed by rules other than timer expiry. |
| `expired` | Timer expired. |
| `abandoned` | No active participants returned before inactive TTL. |
| `closed` | No more entry or gameplay; waiting for cleanup or already cleaned. |
| `cleaned` | Spawned runtime content has been removed, but the run record remains for history. |

This status model lets cleanup be separate from completion.

### Locking

`InstanceRun` should be the first aggregate locked for mutating instance actions.
This follows the WR2 lock ordering direction:

1. Instance run
2. Rooms
3. Characters
4. Mobs
5. Items

Entry, leave, goal progression, timer expiry, completion, and cleanup all mutate
instance lifecycle and should lock the run row.

## Commands, Actions, And Events

Instance lifecycle should move into WR2's command/action/event flow.

Implemented player commands:

- `enter`
- `enter <instance_ref>`
- `leave`
- `instance`

Future authorized system/builder/admin commands:

- `reset instance` for authorized builders/admins
- `close instance` for authorized systems/builders/admins

Recommended actions:

- `EnterInstanceAction(player_id, template_world_id, entry_room_id, transfer_from_room_id, ref=None)`
- `LeaveInstanceAction(player_id, run_id)`
- `EvaluateInstanceGoalAction(run_id, cause_event_id=None)`
- `CompleteInstanceAction(run_id, resolution, cause_event_id=None)`
- `ExpireInstanceAction(run_id)`
- `CleanupInstanceAction(run_id)`

Recommended events:

- `instance.entered`
- `instance.left`
- `instance.goal.progressed`
- `instance.completed`
- `instance.failed`
- `instance.expired`
- `instance.closed`
- `instance.cleaned`

The current player command implementation calls the runtime instance service and
then emits a fresh `cmd.state.sync.success` payload so the client redraws in the
new runtime world. Future lifecycle work should add first-class instance events
for observability, goals, timers, and cleanup.

### Entry Rules

Entry should:

1. resolve the base world, instance template, and target entry room
2. find or create an active `InstanceRun`
3. create or update the participant row
4. move the player into the run's spawned world and entry room
5. recursively migrate carried/equipped item rows to the spawned world
6. emit `instance.entered`
7. emit normal room look/enter events

The item migration must be recursive. Containers inside containers should not
be left in the base spawned world.

### Leave Rules

Leave should:

1. resolve the participant's run
2. choose return room:
   - participant `transfer_from`
   - explicit instance exit destination
   - base-world starting room fallback
3. move the player to the base spawned world
4. recursively migrate carried/equipped item rows to the base spawned world
5. set participant `exited_at`
6. update run `last_active_at`
7. emit `instance.left`
8. leave cleanup to the cleanup scheduler

Leaving should not delete the run.

## Instance Goals

An instance goal is optional.

If no goal exists, the instance is an explorable private space. Players can
enter, leave, and re-enter until cleanup policy closes the inactive run.

If a goal exists, it controls when the instance resolves.

### Goal Shape

Recommended authored shape:

```yaml
goal:
  mode: objective
  starts_on: first_entry
  complete_when:
    objective_complete: kill_boss
  fail_when: {}
  objectives:
    - id: kill_boss
      event: mob.died
      count: 1
      where:
        eq:
          - event.target.spawn_entry
          - boss
  timer:
    mode: none
  rewards: {}
```

This is not a final schema. The important points are:

- `complete_when` and `fail_when` use the existing condition DSL.
- Objectives are event trackers, similar to quest objectives.
- Event filters use the existing condition DSL over `event.*`.
- Progress is stored on the run, not on a player quest instance.

### Goal Modes

Builder UI can present common goal modes even if the manifest stores them in a
normalized objective/condition shape.

| Builder mode | Runtime meaning |
| --- | --- |
| No goal | No automatic completion. |
| Kill boss | Track death of one target placement/entry/tag. |
| Kill any of these key mobs | Track death of one or more target placements/entries/tags, optionally with `any` semantics. |
| Kill all key mobs | Track all target placements/entries/tags. |
| Clear all mobs | Track a generated completion cohort and complete when no eligible members remain. |
| Objective condition | Evaluate arbitrary condition DSL over instance state, objectives, and event data. |

Internally, these should all reduce to objective progress plus condition
evaluation.

### Condition Context

Do not create a separate instance predicate language.

The condition DSL should be extended by adding paths to the evaluation context.
Useful paths:

| Path | Meaning |
| --- | --- |
| `instance.status` | Current run status. |
| `instance.template_id` | Instance template world id. |
| `instance.elapsed_seconds` | Seconds since `started_at`. |
| `instance.remaining_seconds` | Seconds until `expires_at`, if any. |
| `instance.participant_count` | Active participants currently in the run. |
| `instance.objectives.<id>.count` | Current objective count. |
| `instance.objectives.<id>.complete` | Whether an objective is complete. |
| `state.world.<key>` | Instance-local world state because the spawned world is the run world. |
| `state.instance.<key>` | Optional future alias for instance-run state if added. |
| `event.*` | Event payload for the event that caused evaluation. |

`state.world.*` is already a good fit for run-local state because each run has
its own spawned world. A future `state.instance.*` alias may make builder
authoring clearer, but it should map to the same underlying runtime ownership
or to an explicit `InstanceRunState` row.

### Event Subscription

Instances need an event subscriber like quests.

On relevant events, the subscriber should:

1. determine whether the event occurred inside an active instance run
2. load and lock the run
3. update objective progress or scoped state
4. evaluate `complete_when` and `fail_when`
5. enqueue completion/failure actions if a resolution fired

Relevant initial events:

- mob death
- player death
- item pickup
- item delivery/drop
- room enter
- state changed
- trigger/custom objective event

Combat currently emits quest-oriented mob kill events. The target architecture
should converge on a general mob death event that quests, instances, triggers,
and analytics can all consume.

## Mob Kill Goals

Mob kill goals are important enough to deserve explicit rules.

### Spawn Identity

To support "kill this boss" or "choose one of these key mobs," death events
must identify more than the mob definition.

Death event payloads should include:

| Field | Why |
| --- | --- |
| `target.id` | Concrete spawned mob id. |
| `target.definition_id` / `target.definition_slug` | General mob type. |
| `target.spawn_plan_id` / `target.spawn_plan_slug` | Which plan produced it. |
| `target.spawn_entry_id` / `target.spawn_entry_slug` | Which authored entry produced it. |
| `target.spawn_placement_id` | Which generated placement produced it. |
| `target.completion_tags` | Builder-friendly tags such as `boss`, `key`, `optional`. |

Without spawn entry or placement identity, two identical mobs from different
rooms are indistinguishable to goal logic.

### Kill Specific Boss

Best representation:

```yaml
objectives:
  - id: kill_overseer
    event: mob.died
    count: 1
    where:
      eq:
        - event.target.spawn_entry_slug
        - overseer
complete_when:
  objective_complete: kill_overseer
```

The builder UI can expose this as "Boss to kill: Overseer."

### Kill One Of Several Key Mobs

Best representation:

```yaml
objectives:
  - id: choose_a_key_mob
    event: mob.died
    count: 1
    where:
      in:
        - event.target.spawn_entry_slug
        - [warden, oracle, beast]
complete_when:
  objective_complete: choose_a_key_mob
```

This supports "making a choice" by completing the instance when the first key
mob dies. Outcome data should record which key was killed:

```json
{
  "resolution": "complete",
  "choice": {
    "type": "mob_killed",
    "spawn_entry_slug": "oracle"
  }
}
```

That outcome can later drive rewards, world state, quest arcs, or narrative
recaps.

### Kill All Key Mobs

Best representation:

```yaml
objectives:
  - id: kill_key_mobs
    event: mob.died
    count: 3
    unique_by: event.target.spawn_entry_slug
    where:
      in:
        - event.target.spawn_entry_slug
        - [warden, oracle, beast]
complete_when:
  objective_complete: kill_key_mobs
```

The runtime should track unique keys so repeated deaths of respawned mobs do not
double count unless the goal explicitly allows it.

### Clear All Mobs

"Kill all mobs" must define which mobs count.

Recommended rule:

- At run start, after spawn-plan generation and initial reconciliation, build a
  completion cohort.
- The cohort contains spawned mobs or spawn placements marked as eligible.
- Default eligibility can be "hostile mobs spawned by completion-counting spawn
  plans."
- Infinite spawners, ambient mobs, summoned mobs, and replacement respawns do
  not count unless explicitly included.
- Completion happens when every cohort member is dead or otherwise removed by a
  completion-counting outcome.

This avoids ambiguity from respawn reconciliation. A loader pass should not
make "all mobs" impossible by recreating the population after the group clears
it.

Spawn plan additions:

```yaml
completion:
  counts_for_clear: true
  tags: [trash]
respawn_policy:
  mode: none
```

For clear-style instances, the builder UI should warn if a completion-counting
spawn plan has respawns enabled.

## Timers And Clear Records

Timers need two separate concepts:

- a gameplay time limit
- a clear-time record

An instance can have either, both, or neither.

### Timer Policy

Recommended shape:

```yaml
timer:
  starts_on: first_entry
  time_limit_seconds: 1800
  on_expire: fail
  show_to_players: true
  record_clear_time: true
```

`starts_on` options:

| Value | Meaning |
| --- | --- |
| `run_created` | Timer starts when the run is created. |
| `first_entry` | Timer starts when the first participant enters. |
| `goal_started` | Timer starts when an explicit start condition fires. |

`on_expire` options:

| Value | Meaning |
| --- | --- |
| `fail` | Mark run expired/failed and close according to policy. |
| `eject` | Move participants out and close. |
| `soft_fail` | Mark expired but let participants continue without leaderboard credit. |
| `none` | Timer is informational only. |

### Clear Time

Clear time should be computed from:

```text
clear_time = completed_at - started_at
```

Do not derive clear time from client clocks.

The run outcome should store:

- `started_at`
- `completed_at`
- `clear_time_ms`
- `resolution`
- participant ids and names at completion time
- optional choice/outcome details

### Leaderboards

Recommended persistent record:

```python
class InstanceClearRecord(models.Model):
    template_world = models.ForeignKey("worlds.World", on_delete=models.CASCADE)
    run = models.OneToOneField(InstanceRun, on_delete=models.CASCADE)
    clear_time_ms = models.BigIntegerField()
    participant_ids = models.JSONField(default=list)
    participant_names = models.JSONField(default=list)
    completed_at = models.DateTimeField()
    outcome = models.JSONField(default=dict)
```

Builder/player surfaces should be able to show:

- fastest clears for an instance template
- the player's or group's best clear
- recent clears
- clear details: participants, duration, date, completion condition

For fairness, leaderboard eligibility should be explicit:

```yaml
leaderboard:
  enabled: true
  scope: party
  max_participants: 5
  require_goal_completion: true
  invalidate_on_builder_command: true
```

If a builder command, admin intervention, or debug action mutates a run, the run
should become ineligible for public clear records unless the action is marked
safe.

## Cleanup

Cleanup should be policy-driven and status-aware.

Recommended template policy:

```yaml
cleanup:
  inactive_ttl_seconds: 1800
  completed_grace_seconds: 300
  failed_grace_seconds: 300
  expired_grace_seconds: 60
  preserve_run_record: true
```

Rules:

- Active runs with participants are never cleaned.
- Active runs with no participants are kept until `inactive_ttl_seconds`.
- Completed runs are kept for at least `completed_grace_seconds`.
- Failed/expired runs are kept according to their own grace periods.
- Cleanup deletes or resets spawned runtime content, not historical run records.
- Cleanup must happen after clear records and outcome summaries are written.
- A run can be closed to new entry before it is cleaned.

This gives players room to disconnect/reconnect and gives completed groups time
to loot or read completion output if the instance rules allow it.

### Timed Completion Cleanup

Some timed challenge instances may want aggressive cleanup after completion.
That should be explicit:

```yaml
cleanup:
  close_on_completion: true
  completed_grace_seconds: 30
```

Even then, completion should transition through `completed` and `closed`; it
should not delete the spawned world inline during the death event or command
that completed the run.

## Builder Frontend Implications

The builder UI should make the inheritance model obvious.

### Base World

Base-world navigation should include:

- Instances list
- Create instance
- Instance templates with status summary
- Active/recent runs per template
- Fastest clears per template, if leaderboards are enabled

Room config in the base world should keep the "instance link" behavior:

- choose which instance template a room enters
- choose default target entry room if needed
- show whether the target template is valid

### Instance Template

When editing an instance template, the UI should show:

- a clear "Instance of <base world>" header
- inherited resources section with read-only links back to the base world:
  - items
  - mobs
  - item bundles
  - currencies
  - socials
  - abilities
  - combat formulas
  - leveling config
- local editable sections:
  - config
  - goal
  - zones
  - rooms
  - paths
  - spawn plans
  - local triggers
  - presentation
  - cleanup

The current pattern of hiding base-owned resource editors on instance worlds is
correct. It should be expanded into a more explicit inherited-resource UI
instead of leaving builders to infer why sections are missing.

### Instance Config Screen

The instance config screen should separate:

| Section | Behavior |
| --- | --- |
| Required rooms | Editable instance entry/death rooms. |
| Inherited config | Read-only values from base world. |
| Overrides | Editable allowlisted overrides. |
| Goal | Optional goal summary and edit entry point. |
| Timer | Time limit and clear-time settings. |
| Cleanup | Inactive and post-resolution cleanup policy. |

For inherited fields, show source and value. Example:

```text
Combat formulas: inherited from Edeus
Death mode: override - destroy equipment
Leveling curve: inherited from Edeus
```

### Goal Builder

The goal builder should support a simple guided mode before exposing raw YAML.

Initial guided choices:

- No goal
- Kill a specific mob
- Kill one of several key mobs
- Kill all selected key mobs
- Clear all completion-counting mobs
- Custom condition

The guided UI should write the same manifest-backed goal spec used by advanced
YAML editing.

## Player Frontend Implications

While inside an instance, the game UI should be able to show:

- instance name
- optional objective summary
- optional timer
- completion or failure message
- clear time after success
- leave instance action
- reconnect/re-entry state if the player returns before cleanup

On completion, the server should emit a structured event that includes:

- resolution
- clear time if recorded
- participants
- completed objective
- chosen key mob or branch, if relevant
- rewards or next-step hints, if any

The frontend should not infer completion from hardcoded room ids or transfer
links. Completion is an instance lifecycle event.

## Manifest Direction

Instance authoring should fit the WR2 manifest workflow.

There are two viable manifest approaches:

1. Continue representing instance templates as `kind: world` documents with
   `metadata.instance_of`.
2. Add an explicit `kind: instance` document for instance template metadata,
   config, goals, timers, and cleanup.

The clearer long-term path is `kind: instance`.

Example:

```yaml
apiVersion: v1alpha1
kind: instance
metadata:
  world: world.1
  slug: sunken_hold
  name: Sunken Hold
spec:
  entry_room: room@0,0,0
  death_room: room@0,1,0
  exit_room: room.42

  overrides:
    death_mode: destroy_eq
    allow_pvp: false
    combat_resolution_interval: 3

  goal:
    mode: objective
    starts_on: first_entry
    objectives:
      - id: kill_captain
        event: mob.died
        count: 1
        where:
          eq:
            - event.target.spawn_entry_slug
            - drowned_captain
    complete_when:
      objective_complete: kill_captain

  timer:
    starts_on: first_entry
    time_limit_seconds: 1800
    on_expire: fail
    show_to_players: true
    record_clear_time: true

  cleanup:
    inactive_ttl_seconds: 1800
    completed_grace_seconds: 300
    failed_grace_seconds: 300
```

Zones, rooms, paths, triggers, and spawn plans would still use their existing
manifest kinds, applied to the instance template world context.

Short term, `kind: world` can remain the storage/editing primitive. The builder
can still expose instance metadata through `kind: instance` later without
changing the underlying fact that an instance template has rooms/zones/plans.

## Spawn Plan Integration

Instance population should use spawn plans.

The instance template owns spawn plans. Spawn plans reference inherited
base-world definitions and write runtime output into the run's spawned world.

For completion-sensitive instances:

- spawn plan runs should be tied to the `InstanceRun`
- placements should include completion tags/eligibility
- respawn policy should be explicit
- clear cohorts should be generated once per run
- reconciliation should not reroll completion targets

Recommended spawn placement runtime additions:

| Field | Purpose |
| --- | --- |
| `instance_run_id` | Direct link for completion and cleanup queries. |
| `completion_tags` | Tags such as `boss`, `key`, `trash`, `optional`. |
| `counts_for_clear` | Whether this placement belongs to clear-all goals. |
| `unique_completion_key` | Stable key for unique kill tracking. |

This makes "kill all mobs" and "kill key mob" goals deterministic even with
guided random population.

## Death Behavior

Death inside an instance should use the instance effective config.

Important rules:

- Death room must be instance-local.
- Death mode may be overridden by the instance.
- Death penalties still act on the player's real inventory/equipment/currency.
- If items are destroyed or dropped, they belong to the instance run's spawned
  world at that moment.
- If a death causes failure, the failure should be an instance event, not a
  special case inside combat damage resolution.

Example:

- Base world: `death_mode: lose_gold`
- Instance override: `death_mode: destroy_eq`
- Player dies in instance:
  - equipment destruction uses the override
  - player is moved to the instance death room
  - instance goal may optionally fail on `player.died`
  - leaving later returns the player to the base world

## Rewards And Outcomes

The instance system should record outcomes even before it owns a full reward
pipeline.

Outcome data should be structured:

```json
{
  "resolution": "complete",
  "clear_time_ms": 1240000,
  "completed_objective": "kill_captain",
  "choice": {
    "type": "mob_killed",
    "spawn_entry_slug": "drowned_captain"
  }
}
```

Rewards may later include:

- currency grants
- item bundle rolls
- quest or arc progression
- world/character state changes
- achievements
- unlocks

Reward effects should use existing systems where possible. Instance completion
should emit an event that quests, achievements, or future world-event systems
can consume.

## Compatibility With Current Plumbing

Current code already provides useful pieces:

- instance template worlds through `World.instance_of`
- spawned worlds through `World.create_spawn_world()`
- instance selection through `World.instance_for()`
- participant-ish records through `InstanceAssignment`
- base-room to instance-room links through `Room.transfer_to`
- instance admin views for spawned worlds

Gaps to close:

- add first-class `InstanceRun` lifecycle state
- move entry/leave to WR2 actions/events
- recursively migrate carried/equipped items on enter and leave
- stop deleting runs as an inline side effect of leaving
- add instance goal/timer policy
- add event subscriptions for instance objectives
- include spawn origin data in mob death events
- make cleanup status-aware and policy-driven
- make builder UI show inherited resources and local overrides explicitly

## Implementation Sequence

### Phase 1: Stabilize Existing Runtime Movement

- Centralize recursive item world migration.
- Use the same migration path for enter and leave.
- Add WR2 tests for nested inventory/equipment transfer.
- Preserve existing `World.instance_for()` behavior while wrapping it in a
  service boundary.

### Phase 2: Add InstanceRun

- Add `InstanceRun` and `InstanceParticipant`.
- Create a run whenever an instance spawned world is created.
- Backfill/adapt current `InstanceAssignment` into participants.
- Keep spawned `World` as the runtime object owner.
- Update admin views to show run status, participants, started/completed times,
  and cleanup ETA.

### Phase 3: Move Entry/Leave To Actions

- Add `EnterInstanceAction` and `LeaveInstanceAction`.
- Emit `instance.entered` and `instance.left`.
- Keep existing endpoints as compatibility wrappers.
- Remove frontend dependence on old hardcoded instance success messages over
  time.

### Phase 4: Add Config Resolver And Builder UI

- Define inherited fields and override allowlist.
- Validate instance entry/death rooms are local to the instance template.
- Show inherited values and overrides separately in the builder.
- Keep base-owned resource editors read-only or linked from instance contexts.

### Phase 5: Add Goal Runtime

- Add goal spec fields to the instance template.
- Add objective progress storage to `InstanceRun`.
- Add instance event subscriber.
- Add completion/failure evaluation using the condition DSL.
- Emit `instance.goal.progressed`, `instance.completed`, and `instance.failed`.

### Phase 6: Add Timers And Leaderboards

- Add `expires_at`, scheduled expiry action, and timer display payload.
- Add `InstanceClearRecord`.
- Add fastest/recent clear endpoints.
- Add player-visible completion summary.

### Phase 7: Add Cleanup Scheduler

- Add status-aware cleanup task.
- Close inactive runs after TTL.
- Preserve run records and clear records.
- Delete spawned runtime content only after grace periods.

## Test Plan

New automated tests should live under `backend/wr2_tests/`.

Recommended coverage:

- entering an instance creates or reuses an active run
- entering recursively moves inventory, equipment, and nested container contents
  to the spawned instance world
- leaving recursively moves carried/equipped items back to the base spawned world
- leaving does not delete the run
- inactive cleanup closes and cleans a run after TTL
- completed cleanup waits for grace period and preserves clear record
- instance death uses instance death room and death mode override
- base definitions are visible from an instance template while local rooms/zones
  remain instance-owned
- instance config rejects base-world rooms for instance death room
- boss death completes a boss-kill goal
- killing one of several key mobs records the chosen outcome
- clear-all goal completes after the initial completion cohort dies
- respawn reconciliation does not make a completed clear cohort incomplete
- timer expiry marks a run expired or failed according to policy
- clear time is computed from server timestamps
- builder command/admin mutation invalidates leaderboard eligibility when
  configured

## Open Questions

- Should `state.instance.*` be a real new scope or a builder-facing alias for
  `state.world.*` inside spawned instance worlds?
- Should instance templates be exported as `kind: instance` only, or should
  `kind: world` remain the canonical manifest with an instance flag?
- Which base-world global triggers should be inheritable, and how should
  builders mark that intent?
- Should re-entry after completion be allowed during grace periods, or should
  completed runs close to new entry immediately by default?
- How should public leaderboards handle party composition changes after the
  timer starts?
- Should instance rewards be authored directly on the instance goal or emitted
  as events consumed by quests/achievements/reward services?

## Guiding Rule

An instance should feel like its own place while remaining part of the same
game.

That means:

- local layout
- local death room
- local spawn behavior
- local goal and timer
- local cleanup lifecycle

But also:

- inherited definitions
- inherited player progression
- inherited combat math
- inherited currencies and abilities
- server-owned outcome records

This split is the core of the architecture.
