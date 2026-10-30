# Founder’s Pass administration

Founder’s Passes are an optional, operator-managed entitlement for permanent
complimentary access to one authored root world. Core remains free by default;
this feature implements no checkout, subscription billing, or paid-world gate.
World builders cannot issue passes or enable site admission through manifests.

Both `SiteControl.founders_enabled` and `FounderProgram.enabled` default to false.
The program belongs to one root world; its policy and recipient data are outside
builder-editable world exports. Enabling enrollment never grants staff or builder
privileges. Existing non-founder invitations remain valid.

## Configure and grant

Replace the example world and administrator IDs with those from your installation:

```sh
docker compose exec backend python manage.py founders configure --world-id 1 --actor-id 1 --enable
docker compose exec backend python manage.py founders grant alice@example.com bob@example.com --world-id 1 --actor-id 1
docker compose exec backend python manage.py founders status --world-id 1
docker compose exec backend python manage.py founders status alice@example.com --world-id 1
docker compose exec backend python manage.py founders resend alice@example.com --world-id 1 --actor-id 1
```

The command runs with server-operator privileges. If `--actor-id` is supplied,
it must identify an active site administrator and is retained in the audit.
Use `--dry-run` to validate email syntax and preview without writes or delivery.
Grants are processed per address; a nonzero exit can mean partial success.
Rerun the same batch safely: duplicates retain their existing owner, claim state,
and invitation allowance, including passes transferred away from the original
address. A direct grant does not upgrade an existing gifted pass.

New addresses receive unconfirmed reserved User accounts with unusable passwords.
Granting access does not authenticate a user or accept terms on their behalf.
Both signup and email login recognize reserved accounts without overwriting
their profile from anonymous input.

## End beta

Set an explicit timezone-aware cutoff when the date is known:

```sh
docker compose exec backend python manage.py founders configure --world-id 1 --actor-id 1 --claim-deadline 2027-01-01T00:00:00Z
```

`--claim-deadline none` removes an unset/future cutoff; it does not undo claims
already completed. A minute task claims due passes in bounded batches, and
interactive actions enforce the cutoff even if the task is delayed. Unused +1
invitations do not expire. Direct passes issued after the cutoff are claimed
immediately.

`configure --disable` pauses this world’s new grants, transfers, and +1 issuance.
It does not revoke existing benefits, remove platform admission, or prevent
claiming an existing pass. A fully closed site still blocks non-staff sign-in;
account bans and gameplay moderation remain independent of pass ownership.

## Notifications and recovery

Grants and email-verification challenges commit a durable `AccountNotice` before
dispatching delivery to the normal Celery worker. Run one beat scheduler and a
worker. A minute recovery task picks up missed dispatches; delivery uses bounded
batches and up to five attempts. `status` reports `pending`, `sent`, `suppressed`
(outbound email disabled), `failed`, or `expired`. SES acceptance does not prove
inbox delivery. A crash after delivery but before recording success can cause a
duplicate email, never a duplicate pass.

Use `resend` to send the grant description to the **current owner**. Transfer and
email-change confirmations must be requested anew in the account UI, which
invalidates old challenges. Do not circulate tokens or include notification
bodies in logs. Completed/expired challenge records and notices are pruned in
bounded batches 30 days after expiry; sent, failed, suppressed, and expired
notice bodies are cleared. Grant and ownership audit events remain persistent.

Database backups include passes, reserved users, policies, and audit events.
When cloning production into development, delete pending transfer/email-change
challenges and the account-notice outbox, and disable outbound delivery. Retain
pass ownership and user IDs for troubleshooting.

## Performance and future billing

Admission shares Core’s bounded five-second cache: warmed game checks add no
queries. A cold invite-only miss may check pass ownership after the allowlist.
Pass management uses indexed account/program lookups and serializes mutations
within a program. It does not lock runtime worlds or add work to simulation,
movement, combat, or inference requests. Ordinary pass listing uses three
queries regardless of the number of passes.

`founders.services.has_founder_access(user_id, world_id)` exposes the permanent
world entitlement for future admission/billing integration. Enrollment flags do
not erase it. Future subscriptions and earned time can contribute additional
access reasons without changing founder ownership rules.
