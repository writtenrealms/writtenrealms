# World Upkeep Implementation Plan

Status: implemented locally, October 13, 2026. This document preserves the
original design and acceptance checklist. See the [builder contract](../guides/builders/world-upkeep.md)
for accepted YAML and [operations and validation](../dev/world-upkeep.md) for
worker controls, concrete processing limits, measurements, and rollout steps.
No production deployment or runtime provenance backfill is part of this change.

October 14 follow-up: item population accounting now uses availability at the
configured destination, as described in the [spawn-plan guide](../guides/builders/spawn-plan-builder-guide.md#items-available-at-the-destination).
This supersedes the original instruction below to preserve item duplicate
accounting unchanged. Spawn provenance and mob population rules remain intact.

World upkeep keeps running worlds usable without requiring periodic reboots
to remove accumulated objects. Implement corpse decay and abandoned-item
cleanup in one item lifecycle domain, alongside independently governed spawn
plans and door resets. Keep these services in the existing Django/Celery
backend, with shared scheduling conventions and separate processing budgets.

The first delivery targets base multiplayer worlds. Instance support must use
the existing gameplay clock before cleanup is enabled there. This work does
not include a WR1 database migration, runtime-data bridge, or production
backfill.

## Decisions and defaults

Every implemented lifetime is configurable. The player-corpse default is
explicitly **ten minutes after the corpse becomes empty**, not ten minutes
after death. Expiration of a player corpse containing unrecovered equipment
remains an open recovery-policy decision and is excluded from initial timed
cleanup.

| Category | Initial default | Timer starts |
| --- | --- | --- |
| NPC corpse with unclaimed loot | 10 minutes | Death and placement of the completed corpse |
| Empty NPC corpse | 60 seconds | First becoming empty, including empty at death |
| Empty player corpse | 10 minutes | First becoming empty, including empty at death |
| Player corpse with unrecovered death inventory | No automatic expiration initially | Reserved for a later recovery policy |
| Ordinary abandoned item or portable container | 1 hour | Placement outside character possession or protected storage |
| Authored item still occupying its intended placement | Retained by its placement policy | No ordinary ground timer |
| Authored fixture or explicit persistent storage | Retained according to its authored policy | No ordinary ground timer for the fixture |
| Carried or equipped item | Exempt from ground cleanup | No ground timer |

The one-hour ground lifetime is the proposed starting default from the policy
discussion. It can be tuned without changing the architecture. Default NPC
timings retain the previous proposal; the configurable durations and empty
player-corpse duration are confirmed requirements.

## Corpse behavior

Arm a corpse only after the death transaction has placed it in the room and
finished transferring equipment and rolling loot. NPC corpses are currently
pre-created while their mobs are alive, so `Item.created_ts` cannot represent
the time of death. Player corpses likewise must not be treated as empty in the
middle of the death penalty transaction.

Store an explicit corpse category and owner identity where applicable. Never
infer player ownership or NPC status from an item name, keyword, or current
container. Builder-authored items with type `corpse` need explicit lifecycle
classification; their type alone does not prove a runtime death occurred.

For an NPC corpse, the effective deadline is the earlier of the death
deadline and the first-empty deadline. For example, a corpse emptied nine
minutes and thirty seconds after death has thirty seconds left under the
defaults, not another full minute. A player corpse has only the empty deadline
in the first delivery.

An armed deadline never moves later because someone examines, partially
loots, moves, or refills the corpse. Emptiness means no surviving contents,
including non-pickable contents; it is not equivalent to `loot` finding
nothing pickable. Refilled corpses retain their deadline and the added
contents expire with them. In particular, an already emptied player corpse
does not become protected recovery storage when someone puts another item
inside it. Examination and container feedback must expose that deadline.

Expiration removes the corpse and its remaining contents. It does not spill
them onto the ground. Loot moved into a player's possession before cleanup
claims the corpse survives. Cleanup never rerolls loot, repeats death
rewards, counts as another kill, or initiates a respawn.

Corpse policy owns its contents while they remain inside the corpse. Generic
ground cleanup must not independently expire those contents or bypass the
nonempty player-corpse exemption. Removing contents into character possession
cancels ground retention; placing recovered loot directly on the floor arms
the applicable ground policy.

Show approximate remaining time when examining a managed corpse. Emit a
short room notification when it disappears, with correct room-state updates.
Do not introduce periodic warning messages or visual decomposition stages in
the first delivery. Aggregate simultaneous cleanup output where needed.

## Ground items and containers

Ground cleanup is an abandonment policy, not a rule to delete every old item
whose immediate container is a room.

| Transition or placement | Required behavior |
| --- | --- |
| Player drops an ordinary item | Start a new abandonment episode and snapshot the applicable policy. |
| Player recovers an item or bag | Cancel ground expiration for the recovered item and its recovered contents. |
| Player drops a recovered item again | Start a fresh episode using current policy. |
| Item moves between rooms or abandoned containers | Preserve an existing deadline; movement alone cannot extend it. |
| Item is put into an expiring container | It cannot outlive that container; an earlier existing deadline still applies. |
| Item is removed from a bag onto the floor | Preserve an existing abandonment deadline, including a containing bag's earlier deadline. |
| Item is placed in explicitly protected storage | Apply the storage contract to the item and its contents. |
| Item is carried by a mob or player | Do not apply generic ground expiration; death or later abandonment supplies the next policy. |
| Transient script or builder command loads an item directly into a room | Start ordinary ground retention unless the caller supplies a validated authored placement/protection context. |

Track whether a spawn-plan output still occupies its original authored
placement. Once acquired or displaced, that protection ends. Keep
`spawn_placement` for provenance and existing population accounting; merely
having that foreign key must not grant permanent cleanup immunity. Do not
change spawn-plan duplicate prevention as a side effect of this feature.

An authored permanent chest and permanent storage are distinct. Retaining the
chest does not retain arbitrary items deposited inside it forever. Proposed
authoring should distinguish retaining the placed object from preserving its
contents. Scope storage privileges to explicitly authored placements, rather
than giving every portable copy of an item definition the same exemption.
Newly deposited items in an ordinary retained fixture receive an abandonment
deadline; items authored as part of that fixture's initial placement remain
placement-managed until acquired or displaced.

The initial implementation must cover dropped bags and contents deposited in
retained fixtures. It must not create an unlimited-storage loophole by
processing only direct room inventory. For nested containers, resolve the
containing lifecycle boundary in bounded depth-batched queries and preserve
the earliest applicable deadline. Share this resolution with item transfers;
avoid a recursive query for every item on every sweep.

Existing quest-item transfer restrictions remain in force. Do not infer
exemptions from rarity, sale value, item names, or item type alone. Any later
conditional protection must use `backend/core/condition_dsl.py` rather than a
new condition language. Keep genuinely persistent authored objects and known
storage safe while their existing contracts are audited.

## Configuration and portability

Add one typed `spec.upkeep` block to the world manifest. Keep the existing
zone `respawn`, spawn-plan `respawn`, and zone `door_reset` contracts in place;
the functional umbrella does not require renaming those established fields.

World shape (now accepted by the manifest importer):

```yaml
kind: world
spec:
  upkeep:
    corpses:
      npc:
        unclaimed: {mode: fixed, seconds: 600}
        empty: {mode: fixed, seconds: 60}
      player:
        unclaimed: {mode: none}
        empty: {mode: fixed, seconds: 600}
    ground_items:
      abandoned: {mode: fixed, seconds: 3600}
```

`fixed` requires a positive bounded integer number of seconds; `none`
explicitly disables that timer and forbids `seconds`. Reject zero, negative
numbers, booleans, unknown keys, and incompatible shapes. The supported upper bound is 31536000 seconds (one year). A disabled unclaimed NPC
timer can coexist with enabled empty-corpse cleanup, and vice versa.
Choosing `none` permits accumulation for that category; operational previews
must make this visible without imposing an undocumented fallback lifetime.

Initially `player.unclaimed` accepts only `none`. Reject a finite setting
with an explicit explanation until recovery semantics have been decided and
implemented; do not accept a value that the engine silently ignores. All
other lifetimes above are independently configurable, including empty player
corpses.

World policy owns corpse defaults. Allow zone overrides for abandoned ground
items using `inherit_world`, `fixed`, or `none`; reserve per-room and per-mob
duration overrides for demonstrated authoring needs. Explicit storage
protection remains a separate placement property. Distinguish inheritance
from disabling cleanup rather than overloading one null value with both.

Instance templates should inherit base upkeep settings unless they provide
explicit overrides. Resolve and snapshot the effective policy at instance
creation, consistent with a run retaining its authored configuration. Base
world policy changes affect new deaths and abandonment episodes. Existing
episodes retain their snapshot, including their empty-corpse duration, so
shortening a setting cannot unexpectedly delete old objects. Moving into a
different zone during the same abandoned episode does not restart or extend
the deadline.

Partial manifest updates preserve omitted fields. Full exports include the
canonical settings and explicit inheritance where applicable. Runtime owner
IDs, deadlines, policy versions, deletion progress, and claims never appear
in authored manifests. Family imports must resolve policy ownership without
depending on source database IDs.

Replace the inert `has_corpse_decay` concept with this policy as the sole
authority. Audit its serializers and client payloads before removing it; do
not make the old boolean a second switch with conflicting semantics.
`never_reload` continues to govern its established scheduling behavior and
must not disable the new item cleanup service.

## Service boundaries

| Service | Responsibility | Proposed implementation location |
| --- | --- | --- |
| Upkeep policy | Validation, inheritance, defaults, effective-policy snapshots | `backend/worlds/upkeep_policy.py` |
| Item lifecycle | Arm or cancel timers, maintain containment rules, safely expire items | `backend/spawns/item_lifecycle.py` |
| Population | Existing placement reconciliation and respawn eligibility | `backend/spawns/spawn_plans.py` |
| Door upkeep | Initialize and process zone door-reset schedules | Extract scheduling from `backend/spawns/loading.py` into `backend/spawns/door_upkeep.py` |
| Scheduling adapters | Wake due work, supply budgets and clock context, report progress | Existing `backend/spawns/tasks.py`, `backend/worlds/tasks.py`, and `backend/config/celery.py` |

Use shared small utilities only where the actual policies agree: batch
budgets, deadline validation, clock access, retry conventions, and metrics.
Do not introduce a generic arbitrary-action job table, another message broker,
or independently deployed services for this work.

Expose explicit entry points such as `process_due_items(world_id, now,
budget)` and `process_due_door_resets(world, now, budget)`. Keep initial
population and deliberate world/instance reset coordinated by existing
lifecycle services. Separate item cleanup tasks have their own cadence and
do not wait for spawn-plan completion or a global spawn-runner lock.

Extract door scheduling without changing behavior. Preserve policy-version
reconciliation, runtime-world ownership, deterministic doorway deduplication,
revision increments, outbox publication, and the authored Zone -> runtime
schedule -> DoorState lock order. The first extraction can retain the current
wrapper and cadence. Enabling an independent door task is a subsequent
change that must replace the old periodic call, not run alongside it.
Preserve the current `never_reload` gate for door scheduling during that
refactor unless a separate policy change is explicitly chosen.

No domain triggers the others implicitly:

- Corpse expiration does not cause another death or an immediate mob respawn.
- Ground expiration removes eligible output; spawn plans decide later whether
  and when to replace a missing placement using their ordinary rules.
- Door resets do not delete items or reconcile populations.
- `/repop` preserves existing behavior, including its explicit door option.

## Runtime state and transitions

Prefer lifecycle metadata on runtime `Item` rows, with indexed scalar
deadlines. Final names should match repository conventions. The minimum
information is:

- Corpse category and optional originating player identity, independent of
  the owner's continued existence.
- Placement protection versus acquired/displaced status.
- The start of the current lifecycle episode and its first-empty time.
- The resolved duration policy for that episode and the effective deadline.
- A lifecycle revision for stale work and a distinct deleting state if
  deletion must span bounded batches.

Keep immutable real-world audit timestamps separate from gameplay timestamps
that move when an instance resumes. Record a deadline only when applicable;
pre-created living-mob corpses and protected items have no expiration deadline.
Use a partial index over due-capable rows, including runtime world and
deadline/id ordering for world-scoped work. Verify both global due discovery
and world-scoped queries with PostgreSQL query plans.

Do not reuse `is_pending_deletion` or `pending_deletion_ts` as an expiry timer.
They already participate in extraction, visibility, and lifecycle cleanup.
If the retiring state uses an existing visibility mechanism, document that
interaction and prove that its other consumers cannot resurrect or remove
items prematurely.

Centralize lifecycle updates at item transfer and death completion boundaries.
Audit ordinary get/drop/put/give/equip, combat rewards, player death penalties,
builder `/load` and grant paths, scripted mutations, instance transfers,
world cleanup, and definition synchronization. Cover bulk `.update()` paths;
model save hooks alone cannot observe them. Loading an authored placement
must pass its provenance explicitly rather than briefly arming a generic
room item before attaching its placement.

When the final item leaves a corpse, update its empty timer in the same
transaction. When an item or bag enters character possession, cancel the
applicable ground deadlines in that transaction. Preserve runtime-world
isolation even when multiple instances share the same authored room IDs.

## Safe expiration and publication

Looting, putting items into containers, moving containers, and expiration
must agree on lock order. Reuse and extend the existing source-container and
item locking discipline. Lock and revalidate the relevant containment
boundary as well as the selected item; locking only the corpse does not
protect against every current transfer path.

For each due candidate:

1. Enter the appropriate instance clock boundary and a short transaction.
2. Lock the item and required containing boundary in the documented order.
3. Recheck its runtime, lifecycle revision, deadline, containment, protection,
   and availability using current database state.
4. Claim retirement and enqueue the room removal event transactionally.
5. Delete a bounded amount of eligible data, retaining durable progress if
   the container is too large to finish in one batch.

Before claiming a large container, transfers must be able to recognize a
retiring ancestor and refuse new access without enumerating every descendant.
Keep the root until descendants are removed, preventing orphaned items.
Cancellation or pause of the worker must leave durable, resumable progress.
Budget actual contained rows and database work, not just the number of
top-level corpses: one huge bag cannot bypass the batch limit. Never call an
unbounded ORM cascade and describe it as bounded cleanup.

Use skip-locked claims where they fit the established lock ordering. A
candidate already picked up, emptied, deleted, or moved to protected storage
becomes a safe no-op or receives its correctly revised deadline. Workers must
not acquire player or combat locks after item locks and create an inverse
order with the gameplay paths.

Use the existing `enqueue_game_events` and outbox path for publication.
Removal must commit with its durable event; network delivery occurs after
commit. Include enough stable item identity for clients to remove precisely
the affected objects, recheck visibility, and refresh on reconnect. Retries
must not produce duplicate gameplay removal or reward events. Keep narration
bounded per affected room and avoid serializing contents just to discard them.

## Scheduling and clocks

Propose a 30-second cleanup wake interval for ordinary worlds, separate from
gameplay duration settings. Actual removal may occur shortly after a deadline;
never expire early. A persisted deadline is authoritative, so a worker or
broker restart cannot restart the lifetime or lose cleanup work. Use recurring
due polling rather than one long-lived broker countdown per item.

Bound due discovery, work per runtime, total rows removed, elapsed time, and
events per task. Use fair rotation between runtimes and skip busy claims;
do not repeatedly let the oldest backlog in one world consume every batch.
The scheduler design must demonstrate this under load before enabling broad
cleanup. Avoid an all-room walk, per-player scans, and a transaction or lock
covering every world. Expire obsolete wake messages so backlog does not
accumulate redundant polling tasks.

Base multiplayer deadlines continue while players are offline or rooms are
empty. Instances use `gameplay_now`, the existing clock guard, SQL exclusion
of paused worlds before applying batch limits, and the manual-advance path.
Register lifecycle deadlines and gameplay anchors with
`instance_clock_transitions._rebase_timers`; otherwise resume would count
paused wall time against item lifetimes. Process due upkeep during an advance
with the existing atomic turn and event budgets. Physical removal of an
already retired item is housekeeping and can finish independently once it
cannot affect gameplay.

Keep source-instance corpses in that instance when a player's death route
returns them to the base world. A recovered item's transfer to another
runtime cancels its former ground deadline atomically. No paused-world query
may fill an ordinary world's candidate batch.

World stop/reset and instance teardown remain explicit lifecycle operations.
They can currently remove room items before a decay deadline, so the initial
timer is not a guarantee of persistence across those operations. Document
this limitation. Decide shutdown retention, instance destruction, disconnects,
and re-entry together before promising recovery guarantees for nonempty
player corpses.

## Implementation sequence

### Phase 1 Policy and schema

- Implement the typed world policy, ground-item zone override, and explicit
  placement/storage distinctions. Define validation limits, patch semantics,
  full export behavior, and instance inheritance.
- Add runtime lifecycle metadata and partial indexes. Keep new workers
  disabled until all required mutation paths maintain these fields.
- Replace the inert corpse flag contract and update its consumers together.
  Record optional WR1 authored-world converter changes in
  `yaml-manifest-system.md`; do not infer decay lifetimes from an old boolean.
- Add schema, manifest round-trip, inheritance, and policy-snapshot tests.

### Phase 2 Lifecycle integration

- Add shared helpers for completed deaths, first-empty transitions,
  acquisition, abandonment, containment changes, and protected placement.
- Integrate the helpers into NPC and player death paths, transfers, spawn
  creation, builder/script paths, and runtime entry/exit flows.
- Preserve existing spawn-placement accounting and quest restrictions.
- Test deadlines and containment before enabling any deletion worker.

### Phase 3 Expiration and concurrency

- Implement world-scoped due processing, retirement/deletion budgets, durable
  events, and recovery after interruption.
- Align get/put/drop and nested-container locking with cleanup.
- Add the separately budgeted Celery polling task and fair runtime selection.
- Add examination feedback and client room updates using existing styles and
  event handling. Keep full contents out of expiration notifications.

### Phase 4 Instance and door integration

- Integrate paused-instance exclusion, manual advance, timer rebasing, and
  rollback. Enable instance cleanup only after that coverage passes.
- Extract door schedule processing into its own service while retaining
  current wrapper behavior, then replace the periodic coupling when tests
  establish independent scheduling parity.
- Keep initial population, explicit reset/repop, and spawn-plan policy behavior
  intact. No large spawn-engine rewrite is required for corpse cleanup.

### Phase 5 Operational validation and documentation

- Add a read-only, runtime-scoped preview that reports eligible objects,
  protection reasons, overdue age, and estimated descendant work in pages.
- Report work processed, skipped claims, failures, oldest overdue deadline,
  task duration, and budget exhaustion by job type. Prefer logs or bounded
  diagnostics for world detail rather than unbounded metric labels.
- Provide independent operational worker-disable controls. Stopping the worker
  preserves deadlines and rows; re-enabling resumes due work in bounded
  batches. Configuration edits do not retroactively rewrite live deadlines.
- Exercise representative local workloads and preview the candidates before
  enabling deletion in an authorized environment. Document limits and observed
  query/latency results rather than claiming a player capacity from unit tests.
- Update builder/player guides with implemented settings, retention rules,
  storage behavior, countdown feedback, and stop/reset limitations.

Existing WR2 runtime objects may lack reliable death or abandonment provenance.
Do not infer it from names or item creation dates and immediately expire them.
For first activation, document whether the operator will start a fresh runtime
or later authorize a separate, previewable bounded initialization of known
WR2 objects with a fresh grace period. Automatic classification and deletion
of existing ambiguous items is outside this implementation plan.

## Verification and acceptance

All new backend tests belong under `backend/tests/` and run in Docker with
`config.settings.testing`. Add focused modules such as
`test_item_lifecycle.py`, `test_item_lifecycle_concurrency.py`, and
`test_upkeep_policy.py`; extend existing suites where they own the behavior.

| Area | Required evidence |
| --- | --- |
| Defaults and configuration | Independent durations; ten minutes after emptying a player corpse; explicit disabling; invalid values; partial updates; full export/import; zone inheritance; immutable episode snapshots. |
| Death handling | Mob alive longer than its corpse TTL; loot attached before arming; empty-at-death; player penalties and routed deaths; no duplicate reward; no timer for unrecovered player equipment. |
| Empty transitions | Last-item removal; several simultaneous looters; non-pickable contents; partial loot; refill without extension; earlier NPC death cap. |
| Ground policy | Drop/get/drop; scripts loading room items; authored placement acquisition; retained fixtures; explicit storage; nested bags; room transfers; protected quest behavior. |
| Concurrency | Expire versus get, put, drop, storage transfer, world stop, and duplicate workers; lock-order checks; claimed parent with moving child; no item duplication or deletion after recovery. |
| Large containers | Bounded descendant queries and deletion; durable progress after crashes; no access through a retiring ancestor; root removed last; eventual completion without starving other worlds. |
| Runtime isolation and time | Identical authored room in two runtimes; empty rooms; offline players; pause before deadline; manual advance across deadline; resume rebase; turn rollback; independent instance locks. |
| Events and UI | One committed removal, outbox retries, room payload correctness, reconnect state, bounded narration, exact item identity, countdown semantics. |
| Population and doors | No immediate respawn on expiration; no duplicates; `never_reload` does not suppress item cleanup; door policy changes, `/repop --doors`, revisions, and scheduling behavior remain correct. |
| Scale and fairness | Compare small and large retained populations with the same due set; inspect indexed plans; profile hundreds of simultaneous looters, many worlds, one overloaded world, and deep/wide containers. |

Measure query count, rows inspected/deleted, lock waits, transaction time,
event volume, and overdue lag. Establish numeric processing budgets from
those results. Release requires that increasing retained but not-due items
does not introduce a full population scan or per-player cleanup work, and
that a hot world does not prevent other worlds meeting the intended polling
latency under the tested load.

Run focused tests first, followed by canonical `make test` for the completed
runtime change. Run frontend tests and actual desktop/mobile checks for
changed item presentation, including a screenshot in the implementation PR.
Run `make docs-build` after updating published guides. This plan itself changes
no gameplay and does not constitute runtime validation.

## Files and contracts to update

Current integration points:

- [Item and corpse models](../../backend/spawns/models.py),
  [death actions](../../backend/spawns/actions/combat.py), and
  [combat rewards](../../backend/spawns/combat_rewards.py).
- [Item actions](../../backend/spawns/actions/items.py),
  [item definition spawning](../../backend/builders/item_definitions.py),
  [spawn plans](../../backend/spawns/spawn_plans.py), and scripted/bulk transfer
  callers discovered during the mutation audit.
- [World and zone models](../../backend/worlds/models.py),
  [manifests](../../backend/builders/manifests.py), and builder serializers.
- [Current spawn and door coordinator](../../backend/spawns/loading.py),
  [world tasks](../../backend/worlds/tasks.py),
  [door actions](../../backend/spawns/actions/doors.py), and Celery schedules.
- [Instance clock](../../backend/spawns/instance_clock.py),
  [clock transitions](../../backend/spawns/instance_clock_transitions.py),
  [manual advance](../../backend/spawns/instance_time.py), and
  [event publication](../../backend/spawns/events.py).

Update the [world settings inventory](world-settings-portability.md),
[spawn-plan architecture](spawn-plan-system.md),
[instance clock contract](instance-time-control.md), and
[manifest contract and WR1 conversion notes](yaml-manifest-system.md) as their
implemented behavior changes. Publish the settings and behavior in the
[world config](../guides/builders/world-config-builder-guide.md),
[spawn plan](../guides/builders/spawn-plan-builder-guide.md),
[item](../guides/builders/item-definition-builder-guide.md),
[instance](../guides/builders/instance-builder-guide.md), and
[player combat](../guides/players/combat.md) guides. Add ground-item guidance
to the player guide navigation when that behavior ships. Reuse the frontend
style guide for presentation; update it only for genuinely new shared styles.

## Remaining policy decisions

The first delivery can proceed with the defaults and exemptions above. Before
adding finite retention for player corpses containing original equipment,
decide the recovery duration, outcome for unclaimed possessions, shutdown and
instance-teardown guarantees, and player-facing warnings together. The
confirmed empty-corpse lifetime does not answer those questions.

During the schema phase, finalize the maximum configurable duration and exact
placement/storage YAML shape. Keep them explicit and portable, with fixture
retention and contents preservation represented independently. These choices
must be settled before enabling ground cleanup for the affected placements.
