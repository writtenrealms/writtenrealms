# Instance Combat Pause Control

Single-player templates may grant owners a combat-pause preference. Eligible
instances use ordinary WR2 scheduling until the owner opts in and enters combat.
The entire instance then waits before the first round and between every round.
This is a pause boundary around the existing systems, with an explicit gameplay
clock while paused; there is no separate automatic simulation cadence.

## Authoring and ownership

`instance_single_player` and `instance_time_control` default to false. Time
control requires a single-player instance template; base worlds and duel match
templates cannot enable it. Config, manifest, and instance-editor APIs enforce
the same policy. Creation snapshots permissions on `InstanceRun`; its immutable
owner is the first entrant's character. Entry by reference, group entry, and
re-entry check that owner under the run lock. Missing owners fail closed.
Other characters on the same account cannot enter the copy either.

## Normal scheduling and pause boundaries

`pause_in_combat` is the player's per-run checkbox, initially false.
`time_paused` is the current clock authority. Permission alone changes neither
command execution nor inherited combat intervals. Exploration stays live even
with the preference enabled. Combat admission pauses before its first round;
ending the owner's combat resumes time while retaining the preference.
`pause`, `resume`, and `time toggle` change the same checkbox as Settings.

Pause-capable gameplay jobs and commands lock their own `InstanceRun` before
mutating gameplay rows. Combat keeps its existing shared instance locks for
ordinary runs and uses an exclusive lock for eligible runs. Pause/resume and
an already-running job therefore have a definite order. Jobs recheck the pause
state after acquiring the lock. Independent instances never share a clock lock.
Ordinary scheduled Triggers retain their cross-world `SKIP LOCKED` selection.

While live, existing encounter ETAs, heartbeat recovery, Trigger polling, spawn
plans, roaming, and door/merchant schedules run with their normal inheritance
and rules. The heartbeat batches ordinary worlds together, then queues an independent
pulse for each live eligible world under its own run lock. Paused worlds are excluded in SQL
before batch limits. No simulation job is scheduled for a waiting instance.

While paused, `simulation_time` supplies gameplay time in an explicit context;
Django's wall clock is never globally patched. An advance adds two gameplay
seconds and resolves each active encounter once. Timers between step boundaries
become due at the next boundary. Combat rounds preserve their inherited normal
interval for use after resume; that interval does not change the manual quantum.

Resuming rebases outstanding gameplay timestamps by the difference between wall
time and the paused simulation clock. Effects, Trigger deadlines/gates, delayed
scripts, doors, restocks, spawn reconciliation, quest offers, combat activity
windows, and encounter deadlines preserve their remaining durations. Schedule
generations invalidate old encounter/instance jobs, and a post-commit task
re-arms normal encounter, door, and deferred-work scheduling. No missed time is
replayed. The cumulative clock offset also keeps client countdowns continuous.

Networking, audit timestamps, outbox delivery, and instance retention remain on
wall time. Paused owners are exempt from idle logout; ordinary connection rules
apply while live. Disconnecting does not release an active combat pause.

## Intent and advancement

Only while paused do gameplay commands prepare one action on the run. Another
preparation replaces it; `cancelturn` clears it. Inspection, help, personal
hotkeys, aliases, and communication remain immediate, with gameplay reactions
deferred. Quest acceptance, choices, and abandonment also prepare actions when
requested through REST. Outside combat, all these commands work normally.

`advance` commits the prepared action. With none, combat uses ordinary basic
attacks/casting. Invalid actions roll back the entire turn and report an error.
Disabling pause submits an existing preparation through normal command rules;
it does not consume a synthetic turn. Client requests include run, tick,
generation, and pending revision, checked under the lock. Stale or duplicate
advances cannot consume another turn. Text advances retain idempotency receipts
until run deletion. Advance stays disabled until prior input is acknowledged.

Each advance holds one run lock and database transaction:

1. Drain deferred reactions and execute the prepared command.
2. Move to the next gameplay-time boundary.
3. Drain due Triggers and prepared doors, including immediate consequences.
4. Reconcile due spawning and restocks, then their reactions.
5. Reconcile combat admission and resolve each active encounter once.
6. Apply recovery/roaming and finish resulting reactions and timers.
7. Resume normal scheduling if the owner's combat ended, then persist output
   and the final snapshot in the outbox.

Combat-owned effects do not get a second heartbeat decrement when combat ends
during that turn. Character effects and quest clocks preserve remaining time
when crossing runtime boundaries. Trigger gate claims are transactional rows
in eligible instances, including while live, so pauses and failed turns preserve
them. Delayed legacy script segments are likewise durable in both modes.

Captured event subscriptions finish before the turn commits. Outbox publication
marks those reactions complete so retries cannot mutate the waiting instance.
External reactions arriving during a pause are persisted before delivery.
Leaving or resetting an instance can bypass a problematic reaction backlog;
reset clears pending gameplay while preserving monotonic turn identities.

## Bounds, recovery, and performance

Manual advances are atomic and bounded: 512 active mobs, 2048 captured events,
and 2048 due Trigger/door steps. Exceeding a bound fails visibly and rolls back
instead of publishing a partial world. External deferred interactions are
capped at 256, with equivalent observations coalesced. The work queue's partial
index excludes retained advance receipts and Trigger gates from due-work scans.
Recovery uses bounded indexed slices, deduplicates live work submission, and
releases the claim on broker failure. It also resumes orphaned pauses whose
owner is no longer fighting. Generation checks fence obsolete resume jobs.

NPC recovery uses bounded bulk writes. A regression profile of a paused combat
advance with 1, 16, and 64 recovering offscreen NPCs measures 97 queries in each
case, with roughly 80–130 ms locally. This demonstrates bounded query growth,
not a concurrent throughput guarantee: combat, scripts, rooms, and database
load add work. Real PostgreSQL concurrency tests verify duplicate advance
serialization, pause/resume fencing, and independence of separate runs.

Live eligible worlds incur scoped heartbeat work: a local sample of 16 pulses
measured 409 queries and 848 ms. Doing that serially would delay the shared
heartbeat at larger populations, so production heartbeat delivery fans out by
instance. Each instance has at most one claimed pulse, an expiry tied to the
normal heartbeat interval, and generation/claim checks under its run lock.
Backpressure coalesces pulses instead of replaying a backlog. Broker failure
releases the claim; a lost task's claim expires. Ordinary worlds retain their
existing batch. The dispatcher streams IDs in batches of 200 and never locks
instance gameplay itself. Separate workers can therefore process different
instances concurrently. This removes global head-of-line blocking; it does not
remove per-instance SQL cost or substitute for production load testing.

The schema changes are WR2 migrations only and add no WR1 runtime migration path.
