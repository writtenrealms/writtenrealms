# WR2 Typed Scheduled Trigger-Step Execution

This note describes the runtime path for a command or event trigger with a
non-empty `spec.steps` list. Typed steps are the durable alternative to
free-form multi-line `spec.script` pacing.

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
5. Validate the typed steps, resolve all item-definition refs against the
   authored world, and snapshot their ids and slugs.
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
- status and terminal error information

Deleting or editing the authored trigger does not rewrite an in-flight run.
Deleting an item definition needed by a future step causes that step to cancel
cleanly rather than resolving a newly created definition with the same slug.
Player movement or logout does not redirect room actions or echoes.

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
- `echo` selects players currently in the original runtime world and room.

Events are written to `GameEventOutbox` before commit and published afterward.
A process failure before commit leaves the run due and changes nothing. A
failure after commit cannot lose the event because the outbox remains durable.
Room-item additions and removals use a room-scoped delta. Inventory additions
or removals use a private delta sent only to the triggering player; when that
player also needs the room delta, the two changes share one private payload.
This keeps fanout bounded to at most two events per step without disclosing an
inventory grant to other occupants. Viewer-specific custom item actions are
refreshed the next time the client receives a full room view, such as after
`look`; a scheduled item delta does not force a look or recompute those actions.

After success, the cursor advances and `next_run_ts` is calculated as
`started_ts + cumulative_offset`. Worker lateness therefore does not stretch
later intervals. If another step is already overdue, the bounded worker loop
may claim it again immediately.

## Failure And Completion

Expected semantic failures—missing actor inventory, missing room items, a
harvested/moved bound item, a missing actor for a grant, or a deleted
definition—roll back the current step. With
`on_step_error: cancel`, the run records the error and becomes `cancelled`.
Earlier committed steps remain intact; later steps do not run.

After the last successful step, the run becomes `completed`. Completed and
cancelled rows are retained briefly for diagnosis and pruned after seven days.
The scheduler permits no more than one active run for a trigger, runtime world,
room, and trigger actor; terminal runs do not prevent a later start.

## Related Docs

- [Trigger builder guide](../guides/builders/trigger-builder-guide.md)
- [Condition builder guide](../guides/builders/condition-builder-guide.md)
- [Multi-line script execution](trigger-multiline-script-execution.md)
- [YAML manifest system](../architecture/yaml-manifest-system.md)
