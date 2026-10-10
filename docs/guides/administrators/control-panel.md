# Control Panel

Sign in with an active staff account and select **Control Panel** in the header (**Control** on small screens), or open `/staff/panel` on your installation. Staff status is required for both the page and every administrative API. A world builder is not automatically a platform administrator.

If staff status changes while you are signed in, open `/staff` directly. The page checks your current account permissions with the server before allowing access. Reloading any page also refreshes the account information used by the header.

For a new installation, create the first administrator with:

```sh
docker compose exec backend python manage.py createsuperuser
```

Use that account's email with the normal sign-in flow. Staff accounts can always pass the platform access policy, including the deployment email restriction, so administrators can reopen a closed installation. Normal account activation and authentication still apply.

## Access and invitations

The access settings control the entire installation:

| Mode | Who can use Core |
| --- | --- |
| Open | Any otherwise eligible account |
| Invite only | Staff and accounts whose email appears in the whitelist |
| Closed | Staff only |

The whitelist uses exact email addresses, ignoring case and surrounding whitespace. Add an address before inviting someone to sign in. Adding an address grants eligibility; it does not send an email or create an account. Removing it withdraws invite-only access. The whitelist is retained when you switch modes.

Access changes apply to sign-in, authenticated API calls, and gameplay command processing. Cached decisions can take up to five seconds to expire. Existing WebSocket connections may receive passive updates until they disconnect; changing access mode is not a server shutdown or a forced disconnect.

If your deployment sets `WR_ACCESS_RESTRICTED` / `WR_ALLOWED_EMAILS`, that restriction remains an additional limit and is shown in the panel. Opening the site or adding a database whitelist entry cannot override it for non-staff accounts. Update the deployment environment to remove that restriction when you are ready to manage admission entirely through the panel.

**Open building** controls who can create worlds. Turning it off preserves existing builder assignments and staff access. The **main world** setting controls where the homepage and lobby lead; leaving it blank restores the multi-world entry page. Selecting a main world does not publish it or grant permission to enter a private world.

**Maintenance mode** blocks new non-staff world entry and world starts while allowing existing players to continue. Use **Closed** when you also need to block non-staff API requests and game commands.

## Overview and analytics

The overview shows account and character totals, signups over the last 24 hours, unique accounts entering worlds, running runtime worlds, and characters currently marked in game. Temporary guest accounts are excluded from account and character metrics; characters pending deletion are excluded from character totals.

The 14-day chart groups signups and distinct accounts entering worlds by **UTC day**. World entries come from recorded login events. They do not measure time spent playing or every account active during a long session. The current day's bar is partial.

Online status comes from Core's game records. A disconnected character can remain listed until connection cleanup. A running world can have no players. Use the world and character links for the existing world administration and character tools.

Snapshots are cached for 15 seconds. Lists show at most 50 running worlds and 100 online characters; totals cover the full population. Refreshing does not discard unsaved access edits.

## Queues and jobs

Queue monitoring loads separately from the overview. An unavailable broker or a worker that did not respond is reported explicitly; it is not reported as an empty queue. RabbitMQ's passive queue check supplies ready messages and consumer counts. The unacknowledged count is unavailable from that check. Worker task lists show running, reserved, and scheduled work without task arguments or results.

The panel also shows the configured periodic schedule and durable database work, including delayed trigger runs, prepared actions, and the event outbox. These are different views of work; do not add their counts to broker queue depth. The schedule describes configuration and does not prove that Celery Beat is running. Worker inspection can be partial; unavailable counts stay unknown.

Trigger and action failure counts cover cancelled work with a failure code in the last 24 hours, which can include expected cancellations. Due counts exclude paused instances. Outbox pending counts event rows, while its due count measures eligible batch heads. Completed-task history and broker-wide unacknowledged totals require separate monitoring.

Manual recovery actions are restricted to the listed existing recovery jobs. Confirm an action to submit one pass with a batch limit of 100. Each action has a 30-second cooldown across staff users. A returned task ID means publication succeeded, not that the job has finished. If publication cannot be confirmed, the panel says so and records that uncertainty in the audit history. Review subsequent queue and durable-work snapshots to check progress. Recovery processes pending work; it does not replay cancelled or completed work, execute arbitrary task names, or discard queued work.

## Audit history

The audit list records access setting changes, whitelist edits, and manual job submissions with the staff actor and timestamp. Changes made outside these panel APIs are not a complete platform audit trail. Review it when investigating unexpected access changes or manual recovery activity.

## Performance

Analytics use grouped database queries and bounded lists, with a query count independent of the number of worlds and characters displayed. Monitoring runs only when administrators request it and is cached between requests. Broker calls have short timeouts; they are separate from gameplay and the main dashboard. Access decisions use bounded short-lived caches rather than fetching the whitelist for every game command.
