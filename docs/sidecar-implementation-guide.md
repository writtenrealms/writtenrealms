# WR Core Sidecar Implementation Guide

This guide explains the WR Core-side integration contract for external AI sidecars.

Audience:

- Engineers building a sidecar service that reacts to WR Core events and returns bounded mob intents.
- Engineers debugging WR Core <-> sidecar behavior.

## Goals and Boundaries

WR Core remains authoritative for world state. A sidecar is an external decision service.

Rules:

- WR Core owns state mutation and command execution.
- Sidecars never write WR Core databases directly.
- Sidecars send only bounded intent payloads to WR Core ingress.
- WR Core validates all inbound sidecar intents before executing anything.

## WR Core Integration Surfaces

WR Core exposes two integration surfaces:

1. Outbound event forwarding to sidecar.
2. Inbound intent ingress from sidecar.

### 1) Outbound Event Forwarding

Entry point in WR Core publish path:

- `backend/spawns/events.py` calls `maybe_enqueue_ai_sidecar_event_forwarding(...)`.

Forwarding gate logic:

- `backend/spawns/ai_sidecar.py`
- Event is forwarded only when all conditions pass:
  - `WR_AI_EVENT_FORWARD_URL` is configured.
  - Event type is in `WR_AI_EVENT_TYPES`.
  - Actor resolves to a player key (`player.<id>`).

Forwarding task:

- `backend/spawns/tasks.py:forward_event_to_ai_sidecar`
- Uses Celery; intentionally non-blocking and fail-open.
- HTTP errors are swallowed to protect gameplay flow.

Environment variables:

- `WR_AI_EVENT_FORWARD_URL` (example: `http://host.docker.internal:8071/v1/events`)
- `WR_AI_EVENT_FORWARD_TOKEN` (optional bearer token for sidecar endpoint)
- `WR_AI_EVENT_TYPES` (comma-separated event types, e.g. `cmd.say.success,cmd.move.success`)

### 2) Inbound Intent Ingress

Ingress endpoint:

- `POST /api/v1/internal/ai/intents/`
- Route defined in `backend/config/urls.py`
- Handler: `backend/spawns/views.py:AIIntentIngress`

Authentication:

- Bearer token required.
- Must equal `WR_CORE_AI_INGRESS_TOKEN`.

Validation:

- Serializer: `backend/spawns/serializers.py:AIIntentIngressSerializer`
- Requires:
  - `intent_id` (string)
  - `world_key` in format `world.<id>`
  - `mob_key` in format `mob.<id>` and existing mob
  - `intent_type` in `say|emote`
  - non-empty `text`
  - `source_event_id` (string)
- `room_key` optional.
- `world_key` must match mob world context.

Execution:

- Ingress maps intent to command text:
  - `say <text>` or `emote <text>`
- Dispatches through standard command system:
  - `dispatch_command(command_type="text", actor_type="mob", actor_id=<id>, payload={"text": ...})`

This means sidecar intents follow the same WR Core command/action/event paths as native gameplay commands.

## Payload Contracts

### WR Core -> Sidecar Event Envelope

Generated in `forward_event_to_ai_sidecar`:

```json
{
  "event_id": "evt-<uuid>",
  "event_type": "cmd.say.success",
  "world_key": "world.137",
  "room_key": "room.206990",
  "timestamp": "2026-02-22T03:24:59.123456+00:00",
  "actor": {
    "key": "player.128",
    "name": "Teebes",
    "kind": "player"
  },
  "payload": {
    "actor": { "key": "player.128", "name": "Teebes" },
    "text": "archive"
  }
}
```

Notes:

- `payload` is the original event `data` from WR Core.
- For speech events, most sidecars should match against `payload.text`.

### Sidecar -> WR Core Intent Envelope

Expected by `AIIntentIngressSerializer`:

```json
{
  "intent_id": "intent-123",
  "world_key": "world.137",
  "room_key": "room.206990",
  "mob_key": "mob.34242860",
  "intent_type": "say",
  "text": "Welcome back, seeker. The archive remembers your question.",
  "source_event_id": "evt-abc",
  "metadata": {
    "provider": "openai",
    "dry_run": true
  }
}
```

## End-to-End Runtime Sequence

1. Player command enters WR Core (`cmd.text` -> `say ...`).
2. WR Core executes command and emits `cmd.say.success`.
3. `publish_events(...)` publishes to players.
4. Same publish path checks sidecar forwarding conditions.
5. If eligible, Celery task forwards normalized event to sidecar.
6. Sidecar evaluates rules and generates zero or more intents.
7. Sidecar POSTs intents to WR Core AI ingress with bearer token.
8. WR Core validates intent and dispatches mob text command.
9. Mob command emits normal events (`notification.cmd.say.success`, etc.) back to players.

## Sidecar Implementation Checklist

A compatible sidecar should implement:

1. Event ingress endpoint.
   - Accept WR Core event envelope JSON.
   - Validate `event_type`, actor, world/room keys.
2. Matching/orchestration layer.
   - Determine which mob(s) should react for an event.
   - Support deterministic matching before any LLM call.
3. Policy layer.
   - Enforce allowlisted intent types (`say`, `emote`).
   - Enforce output limits and safety checks.
   - Add per-mob cooldown/rate limits.
4. WR Core client.
   - POST intents to `/api/v1/internal/ai/intents/`.
   - Use ingress bearer token.
   - Handle retries/idempotency as needed.
5. Auditing/logging.
   - Log event_id, chosen mob_key, intent_id, and dispatch result.

## Failure and Safety Behavior

Design expectations for sidecars:

- Fail closed on invalid model output (drop/rewrite disallowed output).
- Fail open from WR Core perspective (WR gameplay continues if sidecar is unavailable).
- Preserve idempotency semantics with stable `intent_id` generation.
- Keep prompt/model artifacts separate from WR Core deterministic engine.

WR Core behavior today:

- Event forwarding failures do not break command execution.
- Invalid/unauthorized ingress requests return standard HTTP errors.
- Ingress executes only validated `say|emote` intents.

## Local Smoke Test

1. Configure WR Core env and restart backend + worker.
2. Run sidecar endpoint locally (example `http://localhost:8071/v1/events`).
3. Ensure player and test mob are in same room.
4. Say text that should match sidecar rule (example `say archive`).
5. Verify logs:
   - WR Core worker receives `spawns.tasks.forward_event_to_ai_sidecar`.
   - Sidecar receives `/v1/events` and produces intent.
   - WR Core logs `POST /api/v1/internal/ai/intents/` with `202`.
6. Verify in game console that mob speech appears.

## Related Docs

- `docs/player-say-sidecar-mob-reaction-flow.md`
- `docs/player-command-flow.md`
- `docs/trigger-event-subscriptions.md`
