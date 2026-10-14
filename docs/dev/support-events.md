# Support event history

```sh
docker compose exec -T backend python manage.py support_events --player 3
docker compose exec -T backend python manage.py support_events --world 12 --since 2026-10-14T00:00:00Z
docker compose exec -T backend python manage.py support_events --run 4 --limit 200
```

These read-only commands print JSON lines, newest first, with UTC timestamps.
They finish after at most `--limit` rows (100 by default, 1,000 maximum); they do
not follow a stream or change gameplay. Additional filters are `--user` and
`--kind`. `--world` means the base world and includes its instance family.
Run them through the deployment's existing administrative access; there is no
player-accessible history endpoint.

## Coverage

`system.SupportEvent` retains small historical snapshots for **30 days**:

| Event | Recorded meaning |
| --- | --- |
| `character.created`, `character.deleted` | Successful creation or requested soft deletion, with the original name |
| `session.login`, `session.logout` | World admission and logout; reasons distinguish idle timeout, socket closure, requested logout, and generic service logout |
| `instance.reserved` | Onboarding run assigned at character creation or recreated on Play after cleanup (`onboarding_return`) |
| `instance.entered`, `instance.left` | Instance admission and return, including destination IDs and explicit `leave_command`, cleanup, forced, or death exit reasons |
| `instance.cleanup` | Cleanup **started**, with its reason; this alone does not prove every cleanup step succeeded |
| `quest.started`, `quest.resolved` | Acceptance/automatic start and completion, failure, or abandonment, with quest slug and attempt ID |
| `onboarding.rewards_granted`, `onboarding.rewards_skipped` | First reward package or a repeat resolution whose rewards were already granted to this character |
| `player.died` | Committed death routing, destination, sequence and corpse ID; replaying a death receipt adds no duplicate |

Each row snapshots the character/user IDs, character name, runtime world ID/name,
base world, instance template/run, and room where applicable. IDs are scalar
snapshots, not foreign keys, so deleting a character or runtime does not erase or
rewrite its history. The timestamps use wall time even in paused instances.
For a cleanup event without a character, search by run or base world; a player's
`instance.left` event separately records their return and its reason.

This is an investigation timeline, not a transcript. It excludes chat, email,
IP addresses, credentials, raw commands, request payloads, inventory dumps,
ordinary room movement, combat rounds, and heartbeat ticks. Details are explicitly
allowlisted, scalar, and strings are capped at 160 characters. It does not log
every rejected action or replace task/error logs, economic records, or staff
administrative audit records. History starts when this migration and code are
deployed; it cannot reconstruct past missing events.

## Retention and cost

Celery beat runs `system.tasks.prune_support_events` hourly at minute 40. Each run
deletes up to twenty batches of 1,000 records older than 30 days. Each batch has
its own transaction, uses `SKIP LOCKED`, and resumes safely on the next run.
A backlog emits a warning. Keep the normal beat scheduler running; manual
pruning is also available through the same function in a Django shell. There is
no additional daemon or external logging service.

The read command excludes expired records even if pruning is delayed. Existing
backup retention applies independently: an older backup can still contain
records that have expired in the live database. Legacy `PlayerEvent` and staff
audit retention are unchanged.

Recording a snapshot costs one context lookup and one insert at a selected
lifecycle boundary, with no per-tick work, shared counter, queue fan-out, or
network logging dependency. Quest events can additionally load their template
if it is not cached. Player, base-world, run, and timestamp indexes support
bounded investigations and pruning. The writes participate in their caller's
transaction so rolled-back gameplay does not leave a success event. Cleanup's
start event intentionally remains useful if subsequent teardown fails.

Regression coverage includes idle logout through instance deletion, quest
history surviving deletion, rollback, death replay, privacy boundaries, query
cost, and capped retention batches. Adding coverage requires an explicit hook
and metadata allowlist; do not subscribe the archive to every game event.
