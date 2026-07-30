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
8. Create `ScheduledTriggerRun`. If the first `after_seconds` is zero, execute
   that step, advance the cursor, and enqueue its game events in the same
   transaction. If it is positive, leave the cursor at zero and set
   `next_run_ts` to that first due time without executing authored actions.

If an immediate first step fails, the run, consumed items, spawned items,
bindings, and events all roll back. Conditions always run at invocation, but a
delayed first step does not reserve items, currency, mobs, or command subjects;
the due-step transaction revalidates those resources.

## Persisted Context

The run is the source of truth for delayed work. It stores:

- the original runtime world and authored room
- actor type, id, and key
- the normalized step snapshot
- the next step cursor and absolute due timestamp
- exact bindings such as `crop -> item.123`
- concrete currency ids alongside portable authored codes
- concrete mob-definition ids for exact-one command subjects
- the originating command request identity, plus the initiating connection
  only for the lifecycle-owner run
- status and terminal error information

Deleting or editing the authored trigger does not rewrite an in-flight run.
Deleting an item definition needed by a future step causes that step to cancel
cleanly rather than resolving a newly created definition with the same slug.
Player movement or logout does not redirect fixed room actions, echoes, or mob
selectors. A `command` with `subject: trigger_actor`, `send`, and `send_except`
are intentionally different: they follow that actor in the same runtime world.
A missing or logged-out player fails either send action, and `send_except`
selects witnesses from the player's current room at its authored action
position.

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

When a committed step leaves its next step already due, the runtime also
enqueues one continuation addressed to that run id and expected step cursor.
The continuation executes at most that one step in a new transaction, then
queues the next already-due cursor only after commit. The expected cursor makes
duplicate or stale deliveries no-ops, and `select_for_update(skip_locked=True)`
keeps competing workers from executing the same step. A sequence is capped at
32 steps, so this one-at-a-time chain is bounded. The beat scan remains the
durable recovery path if an immediate continuation is lost or skipped during
contention.

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
- `send` emits one private actor-templated event to the connected player
  Trigger actor.
- `send_except` emits actor-templated text to every other connected player in
  that actor's current runtime world and room. Its recipient lookup uses the
  partial `(world, room, id) WHERE in_game` index and returns only player ids;
  it performs no per-recipient queries or character serialization.

The authoring contract requires item and mob mutations to form one initial
prefix. After that prefix, `command`, `echo`, `send`, `send_except`, and
`debit_currency` may interleave. Their narrative events are appended in
authored order, so a private second-person line can immediately precede its
third-person witness line. The
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

A positive first delay deliberately acquires none of that step's Mob, Item, or
wallet resource locks during start. This keeps the invocation transaction
bounded to context, condition, gate, and run creation work; the ordinary due
worker acquires the same ordered step locks when the action is actually due.

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
For a player command, the first successfully started matching run owns a
connection-pinned `cmd.trigger.accepted` control event. Its final step appends
`cmd.trigger.completed` after all authored events in the same outbox batch.
Failed starts return `cmd.trigger.rejected` with a completed command receipt:
the Trigger refusal is an authoritative domain outcome. A later controlled
owner failure emits a textless correlated `cmd.trigger.cancelled` with the
same completed receipt status. An unexpected internal step exception uses the
same safe lifecycle shape but explicitly marks the receipt failed; its stored
exception detail is never sent to the player. Every failed command-origin
player run also emits unpinned safe cancellation prose so reconnecting players
still see it. Request lifecycle events carry an internal marker that is
stripped before delivery and prevents Trigger or quest subscription dispatch.
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
later intervals. If another step is already overdue, a specific-run
continuation is queued after commit; a bounded recovery-worker pass may also
claim it immediately. An authored `after_seconds: 0` therefore preserves the
transaction boundary between steps without adding the heartbeat interval to
their pacing.

## Failure And Completion

Expected semantic failures—missing actor inventory, missing room items, a
harvested/moved bound item, a missing actor for a grant, a non-player debit
actor, insufficient funds, a missing/ambiguous command subject, an unsafe or
rejected command, an invalid transfer target or destination, an active player
PvP target, a non-player or disconnected send actor, a roomless `send_except`
actor, invalid rendered send text, or a deleted definition/currency—roll back
the current step. A
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
