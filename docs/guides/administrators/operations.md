# Operations and Backups

The commands below use the repository's local Compose service names. For your
production deployment, use its configured services and environment; do not run
the development Compose stack over a production database.

## Health and logs

```sh
docker compose ps
docker compose logs --tail=100 backend fastapi celery-worker celery-beat
docker compose exec celery-worker celery -A backend.config inspect ping --timeout=5
curl --fail http://localhost:8000/health/
curl --fail http://localhost:8001/health
```

For a hosted site, check `https://YOUR_HOST/health/` and keep gateway/internal
checks on the private network. A worker ping proves a worker responds, not that
its queue is keeping up. A gateway health response does not check its database,
broker, or sockets. Also verify sign-in, world entry, and a completed command.

Use [Control Panel](control-panel.md) for bounded activity snapshots, queue
monitoring, and recovery jobs. Treat unavailable metrics as unknown. A published
recovery task ID is not a completion confirmation; check that due work declines
and the affected gameplay resumes. Keep exactly one Beat scheduler running.

For live logs, use `docker compose logs -f --tail=100 SERVICE`; **Ctrl-C** stops
following without stopping the service. Avoid publishing raw logs containing
local login links or player data.

## Restart and maintenance

```sh
docker compose restart backend fastapi celery-worker celery-beat
```

A restart interrupts requests, sockets, and tasks. Use it for diagnosed service
problems, not as a substitute for reading errors. Recreating a service is needed
after Compose environment changes; `restart` alone retains the old environment.

**Maintenance mode** blocks new non-staff entries and world starts, but current
players continue. **Closed** also blocks non-staff APIs and game commands.
Neither setting stops background jobs or guarantees a write-free database.
For migrations or recovery that require quiescence, also stop incoming traffic
and all API, gateway, worker, scheduler, and other database writers.

## Backups and restore

Back up PostgreSQL, protected runtime configuration, deployment revision, and
any additional storage your installation uses. Authored-world YAML exports
are useful content snapshots, but do not contain accounts, inventories, or
quest progress. Database backups include invitations and the saved access mode.

For the local stack, take a portable custom-format dump into a private location
outside the checkout:

```sh
mkdir -p ../core-backups
chmod 700 ../core-backups
umask 077
backup_file="../core-backups/core-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$backup_file"
docker compose exec -T db pg_restore --list < "$backup_file" > /dev/null
git rev-parse HEAD > "$backup_file.revision"
```

Check each command succeeds; keep partial or failed dumps out of your usable
backup set. Listing an archive checks readability, not restorability. A running
database dump gives a consistent database snapshot, but does not capture
in-flight broker messages or every external side effect.

Schedule backups to meet your acceptable data-loss window, copy them off the
application host, restrict access, and define a retention policy. Record the
last successful backup and restore drill. Do not rely on Docker's volume path
as a portable backup, and do not delete volumes to repair an installation.

For a restore drill:

1. Create a separate, empty PostgreSQL database and isolated application stack.
   Keep its broker, channel Redis, cache, and signing secrets separate from the
   live installation. Disable outbound email and integrations.
2. Restore with `pg_restore --no-owner --no-acl --exit-on-error` into that empty
   database. Use a PostgreSQL client compatible with the dump and server.
3. Start with the recorded source revision and configuration. Check migrations,
   account/world counts, staff access, and authored content before upgrading it.
4. Confirm the restored access policy and test world entry and representative
   commands. Never connect a recovered worker to the live broker as a test.

Restoring over a live database is a separate recovery operation: stop all
writers, retain a recovery copy of the current data, and plan how post-backup
writes will be handled. A restore may discard those writes.

## Upgrades

1. Choose an explicit source revision and review its migrations and configuration
   changes. Build and test it in an isolated environment with a restored backup.
2. Record the running revision and take a verified backup. Prepare the previous
   images and a schema-compatible rollback or restore plan.
3. Restrict admission and stop writers as required. Apply migrations once with
   the new release image before starting its services.
4. Deploy matching API, gateway, worker, scheduler, and frontend revisions.
   Rebuild frontend assets when their public URLs or Google client ID change.
5. Check HTTPS, readiness, staff and player sign-in, both socket paths, world
   entry, and a completed command. Check periodic jobs and queue recovery before
   reopening admission.

Do not assume an older application image can use a newer database schema.
Rolling back code does not undo a migration. Restore only with an explicit
plan for the data written since the backup.

## Common failures

| Symptom | Likely area to inspect |
| --- | --- |
| Site renders but API requests fail | Frontend `VITE_API_BASE`, proxy routes, host validation, and API readiness. |
| Login links use the wrong host | `WR_SITE_BASE` in the sending process, then request a new link. |
| Forge connects but gameplay tries localhost | `WR_HOST` in the worker; see [Production hosting](production-hosting.md#runtime-configuration). |
| Commands or timers stop progressing | Worker/broker health, queue age, Beat, and the Control Panel's durable-work counts. |
| API returns 503 from `/health/` | Database connectivity and the configured Django cache. |
| Database TLS fails | Database hostname, CA mount, certificate validity, and network access. |
| Staff can sign in but a player cannot | Saved access mode, exact whitelist email, account eligibility, and world permissions. |
| CPU/DB load rises with player count | Query/lock behavior, worker concurrency, queue backpressure, and representative gameplay measurements. |
