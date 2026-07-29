# WR2 Typed Scheduled Trigger-Step Execution

This note describes the runtime path for a command or event trigger with a
non-empty `spec.steps` list. Typed steps are the durable alternative to
free-form multi-line `spec.script` pacing and can include audited command
execution with explicit room, actor, or mob subjects.

## Start Transaction

When a trigger matches:

1. Resolve the actor's live runtime world and the fixed trigger room.
2. Acquire a PostgreSQL transaction advisory lock scoped to
   `(runtime_world_id, room_id)`. Parallel instances of the same authored room
   use different locks.
3. Lock and refresh the trigger actor, then verify that the actor is still in
   that runtime world and room.
4. Evaluate `spec.conditions`. This closes the race between checking for an
   empty plot and spawning the first crop.
5. Validate the typed steps, resolve all item/mob-definition refs (including
   command mob subjects) against the authored world and all debit currency
   codes against the base economy world, then snapshot their stable ids and
   portable slugs/codes.
6. Convert relative `after_seconds` values to cumulative offsets from the run's
   fixed `started_ts`.
7. Reject a duplicate active run for the same trigger, runtime world, room, and
   trigger actor, or a start beyond the per-actor limit of 16 active sequences
   in that runtime world. Different actors may run sequences concurrently.
8. Create `ScheduledTriggerRun`, execute step zero, advance the cursor, and
   enqueue its game events in one transaction.

If step zero fails, the run, consumed items, spawned items, bindings, and events
all roll back.

## Persisted Context

The run is the source of truth for delayed work. It stores:

- the original runtime world and authored room
- actor type, id, and key
- the normalized step snapshot
- the next step cursor and absolute due timestamp
- exact bindings such as `crop -> item.123`
- concrete currency ids alongside portable authored codes
- concrete mob-definition ids for exact-one command subjects
- status and terminal error information

Deleting or editing the authored trigger does not rewrite an in-flight run.
Deleting an item definition needed by a future step causes that step to cancel
cleanly rather than resolving a newly created definition with the same slug.
Player movement or logout does not redirect fixed room actions, echoes, or mob
selectors. A `command` with `subject: trigger_actor` is intentionally different:
it follows that actor's current room in the same runtime world, and a missing
or logged-out player subject fails the step.

## Due-Step Worker

Celery beat invokes `spawns.tasks.run_scheduled_trigger_steps` at the game
heartbeat interval. Each invocation performs a bounded number of due-step
claims/executions. For each claim it opens a transaction and selects the oldest
due active run with:

- the `(status, next_run_ts)` index
- `select_for_update(skip_locked=True)`
- `next_run_ts <= worker_start_time`

There is no global worker lock and no scan per player, room, or authored
trigger. Multiple workers can process different runs concurrently. A Celery
countdown/ETA is not used as durable state.

## Step Transaction

Every action in one step shares a transaction:

- `consume_item` locks exact live items in the original actor's inventory.
- `consume_room_item` locks exact-definition live items in the original runtime
  room and removes the requested count.
- `grant_item` locks and revalidates the original actor, then spawns the
  requested exact-definition items into that actor's inventory.
- `spawn_room_item` spawns into the original runtime world and room and can bind
  the exact new item id.
- `replace_room_item` locks that bound id, verifies its exact world and room,
  spawns the replacement, removes the old item, and updates the binding.
- `debit_currency` requires a player actor. All debit actions in the step are
  aggregated into one wallet mutation. The step locks the Player, prelocks any
  existing Mob and Item candidates in aggregate order, and preflights the
  complete currency batch. The batch is rejected before commands execute if
  any balance is insufficient. Ordered balance rows are written last.
- `command` resolves `trigger_room`, the current `trigger_actor`, or exactly one
  live mob from a portable definition in the original runtime room. The
  Trigger room is the issuer and an embodied player/mob is the subject.
  Only explicitly audited handlers are accepted. Most are event-only;
  transactional `/transfer` is the initial mutating exception.
- `echo` selects players currently in the original runtime world and room.

The authoring contract requires item and mob mutations to form one initial
prefix. After that prefix, `command`, `echo`, and `debit_currency` may
interleave. Their narrative events are appended in authored order, so a mob
emote can visibly precede a debit and a transfer can follow it. The
authoritative aggregate wallet event is appended after those action events.
Step-safe commands do not branch on or mutate the wallet. `/transfer` may still
serialize the pre-debit wallet in its full player snapshot; the final wallet
event deliberately supersedes that snapshot. This permits aggregate
affordability preflight and balance writes last without changing command
decisions or durable wallet state. Command mob subjects join the same bounded
exact-one Mob prelock used by `set_mob`.
Non-debit mixed steps use the same Mob-then-Item prelock phase, so concurrent
runs cannot invert the aggregate order. Immediate and delayed Mob-triggered
steps lock the actor and all bounded target candidates together in ascending
Mob id order before Item rows.
Immediate Mob starts pin only that Mob set while conditions, active-run limits,
and the cache-backed gate are checked; they select and lock Item candidates
only after those checks pass. Item candidates are capped by the summed authored
consume counts, locked by stable id, and reused during execution; bounded slack
for same-step bound replacements prevents a replacement from exhausting a
later consume set. A later external drop is not pulled into the already-running
step. An unfiltered `set_mob` needs at most two stable candidates to prove
ambiguity. A filtered `set_mob` may evaluate at most 256 candidates; a larger
candidate population fails the step instead of taking an unbounded set of row
locks.

The dedicated `ScriptCommandRunner` renders one command against the original
Trigger actor and dispatches it with an explicit room issuer and the chosen
room, player, or mob execution subject. Newlines, `;`/`&&` chains, history
references, nested `/cmd`, aliases, and fallback Trigger matching are disabled.
Approved handlers publish output only as `GameEvent` objects. The audited
`/transfer` handler may also change the Trigger actor's room inside the
transaction. Any supported subject may issue it, but no other transfer target
is step-safe. The
canonical forms are `subject: trigger_room` with
`/transfer {{ actor_key }} room@x,y,z` and `subject: trigger_actor` with
`/transfer self room@x,y,z`; an exact-one selected mob may instead use the
explicit `{{ actor_key }}` target. `self` or `me` is accepted only when the
resolved subject is the Trigger actor; a selected mob can qualify when it is
itself the Mob Trigger actor. Relative `here` or direction destinations use
the subject's room, so they use the original Trigger room for `trigger_room`,
the actor's current room for `trigger_actor`, and the selected mob's room for a
mob subject. Portable content should use an absolute `room@x,y,z`. A context-local
capture intercepts events and direct command-result messages without touching
WebSockets or subscriptions inside the transaction. A transfer that would move
a player in active PvP fails with `target_busy`; a successful move finishes
ordinary active encounters. A handler error becomes a structured step error,
and a later failure rolls back both the transfer row change and its captured
events. Durable command events carry internal
Trigger/run/issuer/subject provenance, which is stripped from the WebSocket
payload. Player-visible speech and socials are delivered normally, but Trigger
and quest subscribers skip them because forced output is not voluntary player
input. A maximum depth marker remains as a secondary safeguard for internal
forwarding.

Events are written to `GameEventOutbox` before commit and published afterward.
A process failure before commit leaves the run due and changes nothing. A
failure after commit cannot lose the event because the outbox remains durable.
When an audited transfer actually moves a player or mob, its lifecycle event
drives destination mob `entering` reactions after commit, outside the step's
locks. A moved player also runs hostile-mob aggro if its entering reactions
leave it in that destination. Reaction and aggro output is captured in one
transaction and enqueued as another durable outbox batch. Before doing that
work, the subscriber verifies that the actor is still in the stated runtime and
destination. Only the final arrival for an actor in one event batch runs this
work, and a later player transfer invalidates an earlier pending player
arrival. Reaction output inherits the scripted-command depth and remains
bounded by the eight-layer limit. A same-room transfer emits no arrival
lifecycle event and runs neither work item. This is an explicit
transfer-lifecycle continuation, not recursive handling of forced speech or
socials.
Room-item additions and removals use a room-scoped delta. Inventory additions
or removals use a private delta sent only to the triggering player; when that
player also needs the room delta, the two changes share one private payload.
This keeps item-delta fanout bounded to at most two events per step without
disclosing an inventory grant to other occupants. Viewer-specific custom item
actions are refreshed the next time the client receives a full room view, such
as after `look`; a scheduled item delta does not force a look or recompute those
actions.

A successful currency debit also queues the private
`currency.balances_changed` event and visible perspective-specific text. The
actor receives `You part with 10 obols.`; current in-game occupants of the
actor's room other than the actor receive `Joe parts with 10 obols.` using the
authored currency singular/plural and actor name. A delayed debit therefore
follows the actor's current location instead of notifying stale occupants of
the original Trigger room. Within one step, its witness snapshot is taken at
the debit action's authored position, so a preceding transfer targets the
destination and a following transfer leaves it in the origin. Invisible or
logged-out actors produce no witness event. Witness payloads contain the
charged amount but no private balance,
revision, or before/after values. These events are constructed only after the
wallet batch succeeds. Debit perspective text occupies the action's authored
position; the single authoritative `currency.balances_changed` state event
follows all authored action events so a pre-debit transfer snapshot cannot
become the client's final wallet state. With at most 16 actions per step, wallet
work remains one bounded mutation and debit text fanout remains bounded.

After success, the cursor advances and `next_run_ts` is calculated as
`started_ts + cumulative_offset`. Worker lateness therefore does not stretch
later intervals. If another step is already overdue, the bounded worker loop
may claim it again immediately.

## Failure And Completion

Expected semantic failures—missing actor inventory, missing room items, a
harvested/moved bound item, a missing actor for a grant, a non-player debit
actor, insufficient funds, a missing/ambiguous command subject, an unsafe or
rejected command, an invalid transfer target or destination, an active player
PvP target, or a deleted definition/currency—roll back the current step. A
transfer that ran earlier in the authored action list is rolled back with the
rest of that step. With `on_step_error: cancel`, the run records the error and
becomes `cancelled`. Earlier committed steps remain intact; later steps do not
run.

After the last successful step, the run becomes `completed`. Completed and
cancelled rows are retained briefly for diagnosis and pruned after seven days.
The scheduler permits no more than one active run for a trigger, runtime world,
room, and trigger actor; terminal runs do not prevent a later start.

## Related Docs

- [Trigger builder guide](../guides/builders/trigger-builder-guide.md)
- [Condition builder guide](../guides/builders/condition-builder-guide.md)
- [Multi-line script execution](trigger-multiline-script-execution.md)
- [YAML manifest system](../architecture/yaml-manifest-system.md)
