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
- [spawn-plan-system.md](/Users/teebes/code/writtenrealms/docs/architecture/spawn-plan-system.md)
- [scoped-state-system.md](/Users/teebes/code/writtenrealms/docs/architecture/scoped-state-system.md)
- [quest-system-endstate.md](/Users/teebes/code/writtenrealms/docs/architecture/quest-system-endstate.md)
- [condition-builder-guide.md](../guides/builders/condition-builder-guide.md)
- [instance-builder-guide.md](../guides/builders/instance-builder-guide.md)
- [duels.md](../guides/players/duels.md)
- [currency-system.md](/Users/teebes/code/writtenrealms/docs/architecture/currency-system.md)
- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [deterministic-death-routing.md](deterministic-death-routing.md)

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
- Keep death routing inside the instance by default while allowing an explicit
  instance setting to route and return a dead player through the base world.
- Support optional goals:
  - no goal, free enter/exit
  - kill one or more specific mobs
  - kill all mobs in a completion cohort
  - satisfy arbitrary objective conditions
  - expire or fail after a time limit
- Support clear-time records and builder/player-visible leaderboards.
- Support inactive cleanup without deleting an instance immediately when the
  last player leaves.
- Support private match instances whose durable result is separate from any
  one room-level combat encounter.
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
- Do not make spawn plans reroll completion-relevant population during
  reconciliation.

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
| Currency economy | Currency definitions, default currency, starting balances, and player wallets | Definitions and creation policy are base-owned. Code-keyed player balances remain player/global state rather than instance definitions. |
| Socials | Shared emote commands | Social vocabulary should not fork per instance. |
| Abilities | Ability definitions and ability progression | Instances may restrict use later through policies, but definitions are inherited. |
| Leveling | Starting/max level, leveling curve | Instances should not have hidden XP curves unless a later explicit feature needs it. |
| Stats and equipment | `stat_system`, `equipment_system` | These are part of the player's world identity and combat balance. |
| Combat formulas | `combat_system`, attack routines, damage formulas | Inherit by default. Prefer encounter/spawn tuning over formula forks. |
| Merchant profiles | Shared shop definitions | Instance-local shop placement can reference inherited profiles. |

This matches the direction already visible in the frontend: many base-world
resource pages are hidden or redirected when editing an instance world.

The base world also owns `clan_registration_currency` and
`clan_registration_cost`; entering an instance does not create a second clan
economy. Instance templates do not clone currencies, convert balances, or open
instance-local wallets. They may override only the allowlisted death currency
policy described below, and every such currency code still resolves against
the base-world catalog.

### Owned By Instance Template

These should be authored on the instance template:

| Area | Examples | Notes |
| --- | --- | --- |
| Layout | Zones, rooms, exits, paths, room flags/details | The instance is its own physical space. |
| Entry config | Entry room, death room, fallback exit room | Room references must point at instance-template rooms. |
| Spawn mechanics | Spawn plans, spawn entries, respawn policy, guided randomization | Plans reference inherited mob/item definitions but run locally. |
| World, room, and zone initial state | `spec.initial_state` seeds | Each run gets an independent copy; live state is never shared with the base world or another run. |
| Local triggers | Room/zone/world triggers that refer to instance layout | Definition-attached inherited triggers need an explicit policy, described below. |
| Instance goal | Optional completion policy and objectives | Owned by the template and evaluated by each run. |
| Timer policy | Time limit, clear-time tracking, start moment | Owned by the template. |
| Cleanup policy | Inactive TTL, completed grace period, cleanup behavior | Owned by the template. |
| Presentation | Instance name, short description, optional banner/art | Useful for entry prompts and completion screens. |
| Match PvP policy | `pvp_mode: match` | Allows PvP only for admitted opposing contestants while their durable match is active. |

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
| `death_room` | Local death and transport fail-safe room | Must be a room in the instance template, even during base delegation. |
| `death_routing` | Optional local death policy | Uses base class/faction/level identity, local zones, or character state and targets only local rooms. |
| `death_routing_source` | Complete destination-policy owner | `local` (default) or `base_world`; instance-only and never inherited. |
| `exit_room` / `exits_to` | Default base-world return room when no entry room is remembered | Must be a room in the base world or be omitted to use participant `transfer_from`. |

The instance's `death_room` and `death_routing` remain wholly local and are
never inherited or merged through the override mechanism. Source `local`
selects both. Source `base_world` selects the linked base world's complete
policy and fail-safe, then atomically exits the Player to the recorded base
runtime. The local `death_room` remains required. Local `death_routing` remains
optional, but any present policy or disabled tombstone stays canonical,
validated, editable, and exportable so switching back to `local` is
non-destructive.

Only destination selection delegates. The instance effective config continues
to own death mode, currency/equipment penalties, corpse/drop behavior, and
instance goal reactions.

### Config Override Allowlist

Instance overrides should be explicit and visible in the builder UI.

Good initial override candidates:

| Field | Why it makes sense |
| --- | --- |
| `death_mode` | A base world may use currency loss while an instance destroys equipment or drops inventory. |
| `death_currency` | Selects the concrete base-catalog currency used when `death_mode` is `lose_currency`. |
| `death_currency_penalty` | Sets the balance fraction lost when `death_mode` is `lose_currency`. |
| `pvp_mode` | Instances often need stricter PvP policy than the base world. |
| `never_reload` or spawn reload policy | Completion-sensitive instances may need spawn-plan reconciliation disabled or constrained. |
| presentation fields | Instance lobby/entry art and text can differ from the base world. |
| cleanup policy | Run lifetime is inherently instance-specific. |

Fields that should stay inherited initially:

| Field | Reason |
| --- | --- |
| `leveling_curve` / `max_level` / `starting_level` | Hidden per-instance progression curves make player growth hard to reason about. |
| `default_currency` / `starting_balances` | Character creation economy policy belongs to the base world; balances are keyed by concrete currency code. |
| starting equipment | Character creation equipment should come from the base world. |
| character creation and naming policy | Gender, faction, title, class, and name restrictions are base-world player policy. |
| `stat_system` | Player stats should mean the same thing across the world. |
| `equipment_system` | Equipment slot and armor-class behavior should not fork silently. |
| `combat_system` formulas | Balance should come from mobs, traits, spawn plans, and encounter design before formula forks. |
| `allow_combat` | Combat availability is part of the base ruleset, not an instance-local toggle. |
| `combat_resolution_interval` | Encounter pacing is part of the base combat system and should stay consistent. |
| `ability_progression` | Learned/available abilities should remain base-world player progression. |
| global channels and glory decay | Shared social/economy-style policy should not fork inside an instance. |
| `announce_duel_results` | The optional announcement is base-world policy and fans out across that world and its instances. |
| currency definitions and clan registration currency/cost | Currency definitions and global clan policy must stay shared so rewards, registration, and penalties resolve consistently. |

If later we need any of these overrides, they should be introduced as deliberate
features with strong builder UI warnings, manifest diff visibility, and runtime
tests proving the inheritance boundary is still explicit.

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
    return_runtime_world = models.ForeignKey(
        "worlds.World",
        null=True,
        on_delete=models.RESTRICT,
    )
    transfer_from = models.ForeignKey("worlds.Room", null=True, on_delete=models.SET_NULL)
    joined_at = models.DateTimeField()
    exited_at = models.DateTimeField(null=True)
    exit_reason = models.TextField(null=True)
```

The spawned world remains the owner of concrete runtime objects:

- player location while inside the run
- spawned mobs
- spawned items
- spawn plan runs and placements
- room/zone/world scoped runtime state for that playthrough

`InstanceRun` owns lifecycle and history.

The authored instance template owns only state seeds. At runtime, zone and room
state is keyed by both the spawned world and the authored zone/room. That
composite ownership prevents parallel runs, which reuse the same template
layout rows, from observing or overwriting each other's state.

Player character state remains player-owned and follows the player through
entry and leave. Mob character state belongs to a spawned mob and is deleted
with that mob.

`transfer_from` identifies an authored base-world room; it does not identify
which spawned base runtime the Player occupied. Entry must record that exact
runtime in `return_runtime_world` and require
`return_runtime_world.context_id == run.base_world_id`. Normal leave and
death-driven delegation use the relation directly and never choose the first
spawned base world at runtime.

A database check requires `return_runtime_world IS NOT NULL` whenever
`exited_at IS NULL`. Historical cleanup may clear it only after exit and after
the durable exit receipt has captured the runtime id. This keeps active
returns deterministic without making participant history pin a base runtime
forever.

A partial unique constraint permits at most one active participant per Player.
The return runtime is immutable while that participation is active. A trigger
or equivalently protected domain service enforces that its context and the
instance template match `run.base_world`.

Because absence cannot be row-locked, every participant create/reactivate,
entry/leave, and instance death locks the Player row as its participation
reservation. It serializes a missing-row fallback against concurrent admission
without adding a separate advisory-lock query or contending across players.

While the participant is active, this relation is a return lease. Entry and
base-runtime teardown serialize through the same per-runtime lifecycle lock;
teardown rejects active leases. A death can therefore return under its
participant lock without adding one shared base-runtime lock to every
delegated death.

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

`InstanceRun` is the first aggregate locked for actions that mutate run
lifecycle. The extended WR2 order is:

1. Base-runtime lifecycle lock, only when creating a return lease or tearing
   down that runtime
2. Instance run
3. Match/combat aggregates in stable id order
4. Player rows, which also serve as participation reservations, in stable id
   order
5. Instance participants in stable id order
6. Rooms
7. Mobs
8. Items

Entry, leave, goal progression, timer expiry, completion, and cleanup all mutate
instance lifecycle and should lock the run row.

Deterministic death routing is the bounded exception for scalability. Every
instance death first takes shared transaction advisory locks for the local and
base routing configs in sorted config-id order. Shared locks coexist, so deaths
do not block one another; builder publication takes the exclusive form. The
death then locks Player and the affected participant. A delegated death may
mark that participant exited without locking or synchronously updating
`InstanceRun`, provided it makes no run-level transition. Its transactional
outbox consumer upserts one unique run-dirty generation, coalescing many exits.
The lifecycle worker batches exits, locks Run once, updates activity with
`GREATEST`, recounts active participants, and applies guarded transitions.
Cleanup locks Run and then participant rows, includes exit timestamps in
effective-last-activity calculation, and revalidates before teardown. This
avoids moving a same-run lock convoy from death workers to lifecycle workers
without permitting premature cleanup.

If death also resolves a duel, fails a goal, or otherwise changes run status,
the outer action owns the full Run-first lock set and composes both mutations in
one transaction.

### Durable Duel Matches And Spatial Combat Encounters

An instanced duel has two lifecycles that must remain separate:

| Runtime object | Scope | Ends when |
| --- | --- | --- |
| `DuelMatch` | The invitation, accepted contest, and durable result | A contestant is defeated or surrenders |
| `CombatEncounter` | One active room-level engagement inside that match | The engagement ends, including when either contestant flees |

`DuelMatch` records the base world, arena template, entrance, fresh
`InstanceRun`, challenger, challenged player, status, winner, loser, and
structured outcome. `DuelParticipant` records role, team, and result. These are
canonical history rows. Roles and teams are extension points, but the current
winner/loser, opponent-selection, and command services remain explicitly 1v1
and must become team-aware before teams or spectators are exposed.

`CombatEncounter` is transient combat state. A duel encounter links back to its
match and owns `CombatParticipant` rows for actor-local intent, flee state, and
team identity. One match may produce many sequential encounters as contestants
move through a multi-room arena.

This distinction gives `flee` its normal combat meaning. It remains a two-step
escape, moves the player through an eligible exit, and finishes only the
current encounter. It never writes a match result. The contestants can pursue
one another and create another encounter when they re-engage.

Accepting a challenge creates a new private run even if the same pair fought
before. Only opposing contestants in that active match may target one another
with `kill` or hostile abilities. Bare `enter` cannot create a match run, and
reference-based entry admits only contestants from its active accepted match.
Acceptance rejects contestants with active combat or live hostile character
effects. Character-scoped non-hostile effects are rehomed with their target
when crossing runtime boundaries, and effect resolution is scoped to the
target's current spawned world.

Defeat or explicit surrender finalizes the match exactly once:

1. lock the instance run, match, and contestant rows in stable order
2. record winner, loser, resolution, and completion time
3. mark the linked run completed and close all match combat encounters
4. increment `state.character.duels_fought` for both contestants
5. increment `state.character.duels_won` for the winner and
   `state.character.duels_lost` for the loser
6. restore contestant resources and publish the outcome through the
   transactional event outbox

Once complete, match policy blocks all further combat in that runtime world.
Contestants remain present until they use normal instance leave behavior. A
rematch requires a new challenge and a new run.

The explicit exception is a match template configured with
`death_routing_source: base_world`: a lethal defeat composes match completion
with the loser's atomic death delegation and participant exit under the
Run-first lock order. Surrender without death still uses normal match/leave
behavior.

If all contestants are offline when the runtime reaches idle cleanup, the
match is cancelled as abandoned and the run becomes `abandoned`. Cleanup
restores resources and returns the players without incrementing any duel
record; disconnecting is not silently scored as surrender.

The base-world-only `announce_duel_results` flag defaults to `false`. When it
is true, resolution sends one result event to the online population of the base
world and its active instances:

```text
<winner> has defeated <loser> in a duel.
```

The completion transaction and active-status guard make result recording and
counter increments idempotent under worker retries. Match and participant
lookups use indexed status/player/world keys; combat scheduling remains scoped
to active encounters rather than a world-wide PvP tick.

## Commands, Actions, And Events

Instance lifecycle should move into WR2's command/action/event flow.

Implemented player commands:

- `enter`
- `enter <instance_ref>`
- `leave`
- `instance`
- `duel <player>`
- `duel accept [player]`
- `duel decline [player]`
- `duel cancel`
- `duel` / `duel status`
- `duel surrender`

Implemented authorized builder commands:

- `/reset` while inside an instance

Future authorized system/builder/admin commands:

- `close instance` for authorized systems/builders/admins

Recommended actions:

- `EnterInstanceAction(player_id, template_world_id, entry_room_id, transfer_from_room_id, ref=None)`
- `LeaveInstanceAction(player_id, run_id, action_token)`
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

Implemented duel events include:

- `cmd.duel.challenge.success`
- `notification.duel.challenged`
- `notification.duel.started`
- `notification.duel.declined`
- `notification.duel.cancelled`
- `notification.duel.completed`
- `notification.duel.announcement` when base-world announcements are enabled

The current player command implementation calls the runtime instance service and
then emits a fresh `cmd.state.sync.success` payload so the client redraws in the
new runtime world. Future lifecycle work should add first-class instance events
for observability, goals, timers, and cleanup.

### Entry Rules

Player authored identity is stable across runtime movement. Admission derives
the authored family from the current runtime context and requires it to match
`run.base_world`. Entry and leave mutate `Player.world` and `Player.room` only,
preserving `Player.core_faction`.

Entry should:

1. resolve the base world, instance template, and target entry room
2. validate the Player's current authored base-world family
3. under the per-runtime lifecycle lock, validate that the current Player
   world is a running spawned runtime whose context is that base world and
   establish its return lease
4. find or create an active `InstanceRun`
5. create or update the participant row, recording the current runtime as
   `return_runtime_world` before movement
6. move the player into the run's spawned world and entry room
7. move the complete carried/equipped ownership closure to the spawned world
8. seed world/zone/room state if this is a newly created runtime world
9. emit `instance.entered`
10. emit normal room look/enter events

The transfer is recursively complete in semantics: containers inside
containers cannot be left in the base runtime. Its database implementation
must still be set-based rather than one query per container.

For a `pvp_mode: match` template, generic entry must not create a run. Duel
acceptance validates that both contestants are still at the same linked
base-world entrance, creates a fresh run, admits the two contestant
participants, and moves both players as one transaction. Knowing an instance
reference is not sufficient admission to a private match.

### Leave Rules

Leave should:

1. resolve the participant's run
2. choose return room:
   - participant `transfer_from`
   - explicit instance exit destination
   - base-world starting room fallback
3. validate and use the participant's exact `return_runtime_world`
4. move the player to that runtime and chosen authored room
5. move the complete carried/equipped ownership closure to that runtime
6. set participant `exited_at` and `exit_reason`
7. update run `last_active_at`
8. emit `instance.left`
9. leave cleanup to the cleanup scheduler

Leaving should not delete the run.

The target transfer primitive must move the complete carried/equipped ownership
closure with a set-based database update or equivalent carrier-derived model.
The current recursive per-container query pattern is not suitable for
performance-sensitive death delegation.

Every exit has a durable `InstanceExitReceipt` keyed by
`(participant_id, action_token)`. It records exit reason, origin and destination
runtime/room ids, return-runtime id, Player location sequence, and event id.
Normal leave uses a server-namespaced action token; delegated death derives its
exit token from the death token and inserts both receipts in one transaction.
A retry returns the receipt before relying on current Player location. The
participant's historical return-runtime relation may be cleared only after this
receipt exists.

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
| `state.world.<key>` | Instance-run-local world state because the spawned world is the run world. |
| `state.zone.<key>` | Instance-run-local state for the actor's current zone. |
| `state.room.<key>` | Instance-run-local state for the actor's current room. |
| `state.character.<key>` | Player- or mob-owned state for the current character. |
| `event.*` | Event payload for the event that caused evaluation. |

`state.world.*` is the run-local world scope because each run has its own
spawned world. WR2 does not expose an implicit `state.instance` alias or a
fallback into base-world state. A future cross-run or template-wide mutable
scope would need an explicit name and access policy rather than changing these
ownership rules.

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

This avoids ambiguity from respawn reconciliation. Spawn-plan reconciliation
should not make "all mobs" impossible by recreating the population after the
group clears it.

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

Current implementation: active spawned instance worlds with no in-game players
use the `InstanceRun.last_active_at` timestamp as their idle reference and are
stopped/deleted after roughly five minutes of inactivity, plus monitor cadence.
This is intentionally hardcoded until the fuller policy below is implemented.

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

An in-place builder reset is separate from lifecycle cleanup. It keeps the run
and participants, preserves player inventory/equipment/character state,
reseeds only that runtime world's world/zone/room state from the template's
`initial_state`, and rematerializes mobs from spawn plans. Other active runs
are unchanged.

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
| Death routing | Explicit local/base source, dormant local policy, and transport fail-safe status. |
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

The canonical target document is `apiVersion: v1alpha1`, `kind: instance`.
It owns instance template metadata, config, goals, timers, and cleanup. During
the transition, `kind: world` plus `metadata.instance_of` remains accepted
compatibility input and may remain the internal editing/storage primitive, but
the canonical instance exporter emits `kind: instance`.

Changing the linked base (`metadata.world` canonically or
`metadata.instance_of` in compatibility input) is an explicit relink action,
not an ordinary field update. It is blocked while runs are active and must
atomically validate or rebuild every base-catalog reference and family-owned
policy, including deterministic-death class and core-faction selectors, and
increment both the source-selection and any changed local-plan generation. See
[deterministic-death-routing.md](deterministic-death-routing.md) for that
policy's relink contract.

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
  # Omit or use local for the default in-instance behavior.
  death_routing_source: base_world
  exit_room: room.42

  overrides:
    death_mode: destroy_eq
    pvp_mode: disabled

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

Short term, the builder may translate between the canonical instance document
and the existing world-backed editing primitive without changing the underlying
fact that an instance template has rooms/zones/plans.

The current `kind: world`, `kind: zone`, and `kind: room` documents expose
`spec.initial_state` on the instance template. These are seeds for new runs and
builder resets, not live template-wide values:

```yaml
kind: world
spec:
  initial_state:
    alarm_raised: false
---
kind: room
metadata:
  ref: room@4,2,0
  name: Prison Cell
spec:
  zone: zone@1
  initial_state:
    cell_door_open: false
```

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

Death inside an instance always uses the instance effective penalty config. Its
destination policy is selected by `death_routing_source`.

Important rules:

- `local` is the default. It selects the instance `death_routing` and local
  `death_room`, and the participant stays in the run.
- `base_world` selects the direct base world's entire compiled routing policy
  and base `death_room`; policies and fail-safes are never merged.
- The local `death_room` remains required as a transport-integrity fail-safe.
  A dormant instance policy remains local, validated, and round-trippable.
- Death mode may be overridden by the instance.
- Death penalties still act on the player's real inventory, equipment, or
  code-keyed wallet balances.
- Penalty and corpse/drop creation happen while the Player still belongs to the
  instance. Destroyed/dropped items and corpses stay in that runtime.
- Delegation then moves the Player, surviving carried/equipped asset closure,
  and any surviving character effects to the exact
  `InstanceParticipant.return_runtime_world`; it does not use normal
  `transfer_from`/`exit_room` destination selection.
- Player world/room, participant exit, causal sequences, death event,
  `instance.left(reason=death_delegated)`, and the final state-sync request
  commit atomically. There is no intermediate local death room.
- Delegation exits only the affected participant and does not lock/update the
  shared Run row unless the outer death action also changes run lifecycle.
- An invalid return-runtime context uses only the local `death_room`, keeps the
  participant active, and records the transport fallback. A transient transfer
  failure rolls back and retries.
- If a death causes failure, the failure should be an instance event, not a
  special case inside combat damage resolution.

Local example:

- Base world: `death_mode: lose_currency`, `death_currency: crowns`,
  `death_currency_penalty: 0.2`
- Instance override: `death_mode: destroy_eq`
- Instance source: `local`
- Player dies in instance:
  - equipment destruction uses the override
  - player is moved to the instance death room
  - instance goal may optionally fail on `player.died`
  - leaving later returns the player to the base world

Delegated example:

- The same instance sets `death_routing_source: base_world`.
- A player whose gameplay has set `state.character.afterlife_path` to `ember`
  dies:
  - equipment destruction still uses the instance override
  - the base compiled policy maps `ember` and core faction to a base room
  - the player and surviving carried assets cross directly to the recorded base
    runtime and selected room
  - the corpse/drop remains in the instance
  - the participant is exited with `death_delegated`
  - the instance goal may still consume `player.died` using immutable origin-run
    metadata

See
[deterministic-death-routing.md](deterministic-death-routing.md)
for the manifest, source-generation, cache, lock, failure, and receipt
contracts.

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

- currency grants with an explicit base-catalog currency code
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
- record the exact base return runtime on every participant
- replace per-container migration with a set-based carried/equipped ownership
  closure transfer
- add local/default and delegated/base death routing source behavior
- stop deleting runs as an inline side effect of leaving
- add instance goal/timer policy
- add event subscriptions for instance objectives
- include spawn origin data in mob death events
- make cleanup status-aware and policy-driven
- make builder UI show inherited resources and local overrides explicitly

## Implementation Sequence

### Phase 1: Stabilize Existing Runtime Movement

- Centralize a bounded, set-based carried/equipped ownership-closure transfer.
- Use the same migration path for enter and leave.
- Add backend tests for nested inventory/equipment transfer.
- Preserve existing `World.instance_for()` behavior while wrapping it in a
  service boundary.

### Phase 2: Add InstanceRun

- Add `InstanceRun` and `InstanceParticipant`, including protected
  `return_runtime_world` and an exit reason.
- Create a run whenever an instance spawned world is created.
- Replace current `InstanceAssignment` runtime use with participants. WR2
  launches on a clean database; this does not require a WR1 production backfill
  or compatibility layer.
- Keep spawned `World` as the runtime object owner.
- Update admin views to show run status, participants, started/completed times,
  and cleanup ETA.

### Phase 3: Move Entry/Leave To Actions

- Add `EnterInstanceAction` and `LeaveInstanceAction`.
- Emit `instance.entered` and `instance.left`.
- Record/validate the exact return runtime during entry and use it during leave.
- Add server-namespaced leave tokens and durable instance-exit receipts before
  allowing historical return relations to clear.
- Keep existing endpoints as compatibility wrappers.
- Remove frontend dependence on old hardcoded instance success messages over
  time.

### Phase 4: Add Config Resolver And Builder UI

- Define inherited fields and override allowlist.
- Validate instance entry/death rooms are local to the instance template.
- Add instance-only `death_routing_source`, default it to `local`, and expose
  dormant local policy plus transport fail-safe state.
- Show inherited values and overrides separately in the builder.
- Keep base-owned resource editors read-only or linked from instance contexts.

### Phase 5: Add Delegated Death Exit

- Integrate selected base compiled plans with the canonical death coordinator.
- Apply instance penalties before one atomic Player/asset transfer.
- Mark the participant exited and enqueue `player.died`,
  `instance.left(reason=death_delegated)`, and final state sync together.
- Add participant-sharded concurrency, idempotency, transport-fallback, and
  maximum-inventory performance coverage.

### Phase 6: Add Goal Runtime

- Add goal spec fields to the instance template.
- Add objective progress storage to `InstanceRun`.
- Add instance event subscriber.
- Add completion/failure evaluation using the condition DSL.
- Emit `instance.goal.progressed`, `instance.completed`, and `instance.failed`.

### Phase 7: Add Timers And Leaderboards

- Add `expires_at`, scheduled expiry action, and timer display payload.
- Add `InstanceClearRecord`.
- Add fastest/recent clear endpoints.
- Add player-visible completion summary.

### Phase 8: Add Cleanup Scheduler

- Add status-aware cleanup task.
- Close inactive runs after TTL.
- Preserve run records and clear records.
- Delete spawned runtime content only after grace periods.

## Test Plan

New automated tests should live under `backend/tests/`.

Recommended coverage:

- entering an instance creates or reuses an active run
- entering moves inventory, equipment, and nested container contents to the
  spawned instance world
- entry records the exact validated base return runtime
- one Player cannot have two active participants and an active return runtime
  cannot be changed or cleared
- leaving moves the carried/equipped ownership closure to the recorded base
  runtime
- retrying leave after return-relation cleanup returns the durable exit receipt
  without moving or emitting twice
- leaving does not delete the run
- inactive cleanup closes and cleans a run after TTL
- completed cleanup waits for grace period and preserves clear record
- instance death defaults to local routing and uses the death mode override
- delegated death uses the base policy/base fail-safe while retaining the
  instance penalty
- delegated death atomically exits the participant and moves surviving nested
  assets to the recorded base runtime while corpse/drops remain in the instance
- invalid return-runtime context uses the local transport fail-safe and keeps
  the participant active
- concurrent delegated deaths in one run do not contend on the shared Run row
- delegated transfer query shape is set-based and maximum-inventory latency is
  measured
- carried ownership cycles and limits are rejected on mutation; corrupted
  closures never transfer partially
- simultaneous participant exits coalesce into one dirty-run lifecycle batch
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
- two parallel runs of one template have isolated world, zone, and room state
- resetting one run reseeds that run without changing another run
- player character state survives instance entry, leave, and reset
- mob character state is deleted and reseeded with the replacement mob

## Open Questions

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
- local death room and local-by-default death routing
- explicit base-world death delegation when the builder chooses it
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
