# LLM integration

Core has a shared, disabled-by-default inference service and an opt-in mob
conversation pilot. Phase 1A supplies provider configuration, budgets, accounting,
and worker isolation. Phase 1B connects selected humanoids to public `talk` / `say`
conversations. Authored quests take priority; greetings, refusals, and eligibility
checks are deterministic and free. AI help, moderation, world management, and
the broader builder knowledge editor remain future work.

## One provider connection

Choose **OpenAI** or **Anthropic** for the installation. Both capability profiles
use that connection. `fast` is required; `deliberate` is optional. There is no
model escalation, provider fallback, tool access, or automatic repair request.

Create a dedicated provider project/workspace and API key in the provider's
console. Give development and production separate budgets/credentials. Enter the
key in your deployment's secret store or private, untracked `.env`:

```dotenv
WR_LLM_API_KEY=your-provider-key
WR_LLM_OPENAI_PROJECT=
```

`WR_LLM_OPENAI_PROJECT` is optional for OpenAI and unused for Anthropic. Never
put keys in JSON configuration, YAML world manifests, source control, screenshots,
or command arguments. The database stores configuration and accounting, not
credentials. Restrict access to the environment file and database backups.
The retired `GPT_SECRET_KEY`/`GPT_PROJECT_ID` wrapper and old AI sidecar example
variables are not supported integration paths.

Pass the same connection key to the API, ordinary Celery worker (admission and
outbox checks), and dedicated inference worker. Set **`WR_LLM_WORKER=1` only on
the inference worker**. The default Compose configuration already does this.
Changing `.env` requires recreating affected containers; a restart does not
reload Compose environment values.

## Configure models and prices

Run migrations before starting workers from this revision:

```bash
docker compose exec backend python manage.py migrate
```

Create a JSON configuration file accessible inside the backend container, such
as `/tmp/llm-config.json`. Copy a local file in with `docker compose cp` if needed.
Replace the placeholders below with a pinned model ID and current USD prices per
million tokens. Placeholder prices deliberately fail validation.

```json
{
  "provider": "openai",
  "profiles": {
    "fast": {
      "model": "REPLACE_WITH_PINNED_MODEL_ID",
      "price_revision": "2026-10-26-operator-reviewed",
      "rates": {
        "input": "REPLACE_WITH_PRICE",
        "output": "REPLACE_WITH_PRICE",
        "cached_input": "REPLACE_WITH_PRICE",
        "cache_write": "REPLACE_WITH_PRICE"
      },
      "max_input_tokens": 8192,
      "max_output_tokens": 256
    }
  }
}
```

For Anthropic set `provider` to `anthropic` and use its model and prices. Add a
`deliberate` object with the same fields only when needed. Choose models that
support native JSON-schema output: OpenAI Responses `text.format`, or Anthropic
Messages `output_config.format`. Smoke-test the exact model and review its
conversation quality before opening player-facing behavior.

Use the official [OpenAI prices](https://developers.openai.com/api/docs/pricing)
or [Anthropic prices](https://platform.claude.com/docs/en/about-claude/pricing).
All four price fields are required. When a provider has no separate cache-write
charge, use its ordinary input rate. If different cache-write lifetimes have
different prices, set `cache_write` to their **highest applicable rate**; this
conservatively accounts for every returned write token. The service does not
request paid tools, priority processing, batch processing, or extended thinking.
Provider-internal reasoning is still billable output and shares the output cap.

The byte-based input bound includes JSON escaping and 4,096 tokens of framing
headroom. Inputs are limited to 32 text messages and a fixed `{text}` output
schema. The reservation prices the **entire input cap**, using the highest
input/cache rate, plus the entire output cap. This over-reserves small prompts
until actual usage arrives. Do not choose an unverified model/tokenizer or rate:
the ledger depends on accurate tariffs and these bounded text API contracts.
An observed cost above its reservation records the discrepancy and disables the
integration. Review and reconfigure the tariff/bounds before enabling it again.

```bash
docker compose exec backend python manage.py llm configure /tmp/llm-config.json
docker compose exec backend python manage.py llm status
```

`configure` always disables spending and advances a configuration generation.
It does not reset usage. Changes to provider, model, prices, or limits invalidate
queued requests. In-flight requests keep their old price snapshot and may still
be charged; their results are suppressed. Never edit these database rows directly.

## Budget and period defaults

Accounting uses integer micro-USD: **1,000,000 units = $1**. Periods are UTC
calendar days and months; server/player timezones do not change them.

| Limit | Default |
| --- | ---: |
| Installation monthly | $90 |
| Installation daily | $3 |
| Conversations monthly / daily | $75 / $2.50 |
| Preparation and evaluation monthly / daily | $15 / $0.50 |
| Each base world monthly / daily | $90 / $3, also constrained by shared caps |
| Each linked account owner | 30 requests per UTC day, across all purposes/worlds |
| Outstanding work per owner | 1 |
| Outstanding work per installation | 16 |
| Simultaneous provider dispatches | 4 |

The $90 operating ceiling leaves $10 below a $100 monthly inference ceiling.
There is no automatic use of the headroom. Daily limits are hard pacing caps;
unused daily capacity does not accumulate. Preparation and smoke/evaluation
share one allocation. All base-world limits also include its instance templates
and runtime copies. An owner uses `User.link_id` when present, otherwise the
user ID, so linked accounts share the same allowance. The conversation pilot
adds the per-NPC patience and quotas described below.

Optional `limits` overrides belong alongside `provider` and `profiles`:

```json
{
  "monthly_usd": "90",
  "daily_usd": "3",
  "conversation_monthly_usd": "75",
  "conversation_daily_usd": "2.50",
  "preparation_monthly_usd": "15",
  "preparation_daily_usd": "0.50",
  "world_monthly_usd": "90",
  "world_daily_usd": "3",
  "owner_daily_requests": 30,
  "pending_requests": 16,
  "concurrent_requests": 4
}
```

World caps cannot exceed installation caps; purpose allocations cannot sum to
more than installation caps. Limits can be reduced to zero for spending scopes.
Only an operator can increase budgets; imported worlds cannot do so. If several
installations use one provider billing account, allocate their combined ceilings
within the total budget. Provider spending controls are an additional backstop.
Core cannot account for unrelated calls made outside this service, taxes, or
other invoice charges.

The admission transaction checks **spent + reserved + new maximum cost** in all
seven applicable buckets. Concurrent callers cannot spend the same remaining
balance. Cancellation before dispatch refunds the reservation and request count.
Once dispatched, failures still consume the owner's request allowance.

## Start and check the worker

Ordinary game workers consume `celery`; the optional worker consumes only
`inference`. The dedicated worker uses four processes and prefetch one. Keep
those queues separate in any custom deployment, and run one scheduler.

For the local Compose stack, recreate the API and regular worker to load secret
changes, then start the optional worker. Include your usual mount override when
running with bind-mounted source.

```bash
docker compose up -d backend celery-worker celery-beat
docker compose --profile ai up -d llm-worker
docker compose exec backend python manage.py llm enable
```

Admission performs **no network I/O**: it writes a durable database request.
The ordinary worker's bounded outbox dispatcher runs every two seconds and
publishes only request IDs. Provider work happens in the dedicated worker after
another availability/generation check. The outbox recovers lost publication
without retrying an ambiguous provider call. There can be roughly two seconds
of queue delay before model latency; requests expire after 90 seconds.

The provider call has bounded timeouts, a 256 KiB response limit, a 35-second
Celery soft limit and 40-second hard limit. A persisted dispatch lease expires
after 60 seconds. Missing workers, broker downtime, duplicate delivery, and
expired queued work do not cause unbounded provider fan-out.

## Enable a world conversation pilot

Apply the migrations and run the API, regular worker, inference worker, and
scheduler from the same revision. The regular worker checks at most 32 ready or
expired conversations each second; it publishes replies through the existing
game event outbox. Network inference stays on the dedicated worker. Restart
workers after updating Python code, even with source bind mounts.

Configure a reviewed policy for the **authored base world**. It also applies to
runtime copies using those definitions. The checked-in Phalanx policy selects
`athens-potter`, `athens-armorer`, and `orientation-watchman`:

```bash
docker compose exec backend python manage.py mob_conversations configure --world-id 12 --input-file spawns/conversations/pilots/phalanx.json --enable
docker compose exec backend python manage.py mob_conversations status --world-id 12
```

World 12 is this project's Phalanx ID; use the matching authored ID in another
installation. The command checks that each slug exists and is a talkable
humanoid. See the [mob builder guide](../builders/mob-definition-builder-guide.md#optional-conversation-pilot)
for the small JSON contract. Omit `--enable` to save a disabled policy. Every
configuration update changes its revision, invalidating older conversations.
Pilot configuration does not enable the shared provider or make a model call.
Both the world policy and the shared integration must be enabled and funded.

To disable only this world's pilot:

```bash
docker compose exec backend python manage.py mob_conversations disable --world-id 12
```

Players start with `talk <mob>` (or bare `talk` for an unambiguous target), then
use `say`. `talk stop` releases their focus. Mobs outside the allowlist retain
their existing behavior. The watchman's applicable authored quest interaction
always wins over conversation, including a silent matching tracker. A matching
authored SAY reaction also takes ownership before the model path.

| Gameplay limit | Pilot default |
| --- | --- |
| Active targets per player / active speakers per runtime mob | 1 / 1 |
| Outstanding inference or undelivered reply per focus | 1 |
| Turns per focus / attention window | 6 / 6 |
| Patience recovery | 30 minutes since the last admitted turn |
| Owner + mob-definition daily allowance | 12 admitted turns per UTC day |
| Idle expiry / maximum focus lifetime | 90 seconds / 5 minutes |
| Repeated refusal or pending-reply notices | At most once per 15 seconds per relevant allowance/focus |
| Player input / generated speech | 280 characters each |
| Transcript context | At most 6 recent messages, shortened further to fit the provider input cap |

These are server constants for this pilot. Account-owner quotas span characters,
linked users, respawns, and runtime copies of the same definition. Gameplay
allowances count **admitted attempts**, including cancelled or failed attempts;
the shared financial ledger separately refunds requests cancelled before
dispatch. Closing/reopening a conversation does not reset patience or allowances.
Refusal does not change faction reputation or consume an authored quest attempt.

Idle and maximum-lifetime expiry send one in-world notice to the listening player
if the participants are still eligible and together. The existing bounded poll
handles this notice; an input or queued dispatch that encounters expiry first
also closes and notifies once. Timeout notices do not call the provider.

Extra SAY messages while a reply is pending remain public but are not queued for
later answers. No provider work occurs for greetings, refusals, bystanders,
forced/scripted player speech, or idle mobs. Each admitted turn is checked again
before dispatch and before publication. Movement, combat, death, logout, world
restart, policy changes, or global disablement suppress stale answers. Already
dispatched calls can still incur a charge. Generated replies cannot invoke game
commands or trigger quest/Trigger reactions. Invalid text is discarded without
a repair call.

The packet contains only reviewed shared/per-mob knowledge, bounded current mob
and room descriptions, and recent conversation. No global history lookup or
automatic lore ingestion occurs. The default prompts require ignorance of
unfamiliar subjects and treat the listener as a stranger. Review the chosen
model against modern names, historical dates, false identity claims, requests
for possessions, and attempts to override its role; mocked tests cannot establish
actual model quality. Use the budgeted evaluation path below before broadening
the pilot.

## Budgeted smoke and evaluation checks

Commands are **dry runs unless `--execute` is present**. Dry runs validate the
input/configuration and show the maximum reservation; they do not reserve funds,
prove credentials work, or guarantee that admission will succeed.

Use existing user/world IDs. Both smoke checks and evaluations charge the
preparation/evaluation allocation and the same global/world/owner limits.

```bash
docker compose exec backend python manage.py llm smoke --world-id 12 --user-id 1
docker compose exec backend python manage.py llm smoke --world-id 12 --user-id 1 --execute
docker compose exec backend python manage.py llm request REQUEST_UUID --result
```

The smoke request asks for one short greeting. `--execute` returns a request UUID
without waiting for a model. Inspect it after the worker finishes. Use
`--request-id UUID` to reuse the same identity after an uncertain CLI response;
a matching repeat does not create another reservation or dispatch. Reusing an
identity with different input is rejected. Idempotency receipts last 90 days;
do not replay old events beyond that retention window.

For a conversation evaluation, create a JSON input file containing only:

```json
{
  "instructions": "You are a potter in a small Greek village. You know your craft and immediate surroundings. Admit when you do not know something. Reply briefly in the required JSON format.",
  "messages": [{"role": "user", "content": "Who rules the far side of the world?"}]
}
```

```bash
docker compose exec backend python manage.py llm evaluate --world-id 12 --user-id 1 --input-file /tmp/potter-case.json
docker compose exec backend python manage.py llm evaluate --world-id 12 --user-id 1 --input-file /tmp/potter-case.json --execute
```

Evaluate one case at a time, inspecting quality, usage, and latency before the
next case. `--profile deliberate` is available only if configured. These are
manual, budgeted checks, not unattended quality graders. Routine automated tests
mock HTTP and disable network access through testing settings.

## Failure, shutdown, and retention

`llm status` reports configuration, control availability, the failure circuit,
pending count, and the current UTC day/month installation buckets. Admission also
checks the selected purpose, world, owner, and remaining budget. `llm request UUID` reports safe status,
usage, and cost fields; `--result` explicitly includes generated text. Neither
command prints credentials or original prompts.

| Request state | Meaning |
| --- | --- |
| `queued` | Reserved, no provider authorization yet |
| `dispatching` | Authorization persisted; the provider might have received it |
| `succeeded` | Valid structured text; a later consumer must still recheck game state |
| `invalid` | Known usage charged, but unusable/truncated output or a cost discrepancy |
| `uncertain` | Billing unknown; the full maximum remains charged |
| `cancelled` | Never dispatched; reservation and request allowance refunded |
| `discarded` | Known usage charged, output suppressed after expiry/configuration change |

Timeouts, HTTP errors, unknown usage, and crashed workers retain the full maximum
charge and open a five-minute circuit. Errors do not trigger retries, escalation,
or a second model call to repair JSON. A late response with trustworthy usage can
reconcile an uncertain charge once, but never publish its stale text. No command
blindly refunds uncertain billing. Check the provider's records when investigating;
conservatively charged amounts remain charged without better evidence.

```bash
docker compose exec backend python manage.py llm disable
docker compose --profile ai stop llm-worker
```

Disablement takes effect under the same lock as dispatch authorization. Queued
requests are cancelled by the dispatcher; already authorized calls can still be
billed. Invalidated output stays suppressed, even if re-enabled quickly. To remove
access, disable first, let outstanding accounting settle, remove/revoke the
provider key, and recreate services to remove it from their environment.
Stopping a worker or deleting a key is **not** evidence that an in-flight call
was free. Keep the ordinary worker and scheduler running to recover its ledger.

Completed input is erased immediately. Generated text is cleared after three days
by hourly bounded cleanup. Request metadata/usage/idempotency receipts last 90
days; installation, world, and purpose spending buckets survive receipt cleanup.
Old per-owner daily buckets are removed once their receipts are gone. Queued input is retained
until dispatch/cancellation, so an installation with stopped workers/scheduler
must resume cleanup to expire it. Backups have their own retention policy.

Conversation transcripts are cleared when focus closes. Inactive focus and
allowance rows expire after three days. Conversation input receipts expire after
ten minutes, longer than a focus can live. A minute task drains at most 5,000
expired input receipts and 500 rows of each other kind per run; cleanup can lag
if workers stop or an exceptional backlog exceeds that drain rate. Public mob
speech also uses the normal three-day communication archive.

## Shared consumer contract

The internal entry point is `inference.service.submit`, with a stable UUID,
server-resolved user/world, purpose, profile, instructions, and text messages.
Supported purposes are `mob_conversation`, `mob_preparation`, and `mob_evaluation`.
It returns either a request ID or a safe denial reason. There is no public HTTP
endpoint and no manifest field that enables spending.

Consumers own eligibility and knowledge assembly before submission. They must
consume `result_for` inside their publication transaction, recheck game state,
and write their own idempotent publication receipt. This keeps disablement fenced
through publication. Consumers must not publish by reading `result` directly,
call adapters directly, or interpret model text as an authorized game action.
Use `service.cancel` when consumer eligibility ends: it refunds queued work or
suppresses a dispatched result while retaining the charge.
The implemented consumer is `spawns.conversations.service`. Mob-purpose dispatch
requires a valid bound focus; an unbound `mob_conversation` request is cancelled
before calling a provider. Operator smoke/evaluation commands use their separate
purpose and allocation. The free greeting preflight is advisory; atomic
reservation at each SAY remains authoritative.
