# Player Say to Sidecar Mob Reaction Flow

This document describes the full WR Core + sidecar workflow for this case:

- A player says something in a room.
- The sidecar decides a configured mob should react.
- WR Core executes the mob command returned by the sidecar.

## Scope and Preconditions

- The player is connected to gameplay WebSocket: `/ws/game/cmd`.
- WR Core sidecar forwarding is configured:
  - `WR_AI_EVENT_FORWARD_URL` points to the sidecar event endpoint (for example `/v1/events`).
  - `WR_AI_EVENT_TYPES` includes `cmd.say.success`.
- WR Core AI ingress is configured:
  - `WR_CORE_AI_INGRESS_TOKEN` is set.
- Sidecar is configured with a matching mob rule:
  - `event_type: cmd.say.success`
  - `match: <text fragment>` (or empty match for unconditional on that event type)
  - `mob_key` corresponds to a live mob in WR Core.

## End-to-End Workflow

1. Player sends command text.
   - Frontend sends WebSocket message `{"type":"cmd.text","text":"say archive"}`.

2. Game WebSocket receives and queues command task.
   - `fastapi_app/game_ws.py` queues `spawns.tasks.handle_game_command` with:
     - `command_type="text"`
     - `player_id` / `player_key`
     - `payload={"text":"say archive"}`

3. Celery task dispatches command handler.
   - `backend/spawns/tasks.py:handle_game_command` calls `dispatch_command(...)`.
   - Actor is the player.

4. Text command resolves to `say`.
   - `backend/spawns/handlers/text.py` parses command and resolves `SayHandler`.
   - `backend/spawns/handlers/communication.py:SayHandler` calls `SayAction`.

5. `SayAction` emits canonical game events.
   - Player receives `cmd.say.success` with text like `You say 'archive'`.
   - Other players in room receive `notification.cmd.say.success`.

6. WR Core publishes events.
   - `backend/spawns/events.py:publish_events` publishes each event to Redis via `publish_to_player`.
   - FastAPI game pubsub forwards those messages to connected WebSocket clients.

7. The same publish path also evaluates side effects.
   - Trigger subscriptions execute (`dispatch_trigger_subscriptions_for_event`).
   - AI sidecar forwarding check executes (`maybe_enqueue_ai_sidecar_event_forwarding`).

8. Sidecar forwarding gate decides whether to enqueue.
   - `backend/spawns/ai_sidecar.py` requires all of:
     - forwarding URL configured,
     - event type allowed by `WR_AI_EVENT_TYPES`,
     - actor is player-originated (`player.<id>`).
   - If true, it enqueues `spawns.tasks.forward_event_to_ai_sidecar`.

9. Forward task builds and sends event envelope.
   - `backend/spawns/tasks.py:forward_event_to_ai_sidecar` resolves player/world/room and posts JSON to sidecar.
   - Envelope contains:
     - `event_id`, `event_type`, `world_key`, `room_key`, `timestamp`
     - `actor` (player key/name/kind)
     - `payload` (original event data, including spoken text)

10. Sidecar matches rules and produces intent(s).
    - Sidecar receives `POST /v1/events`.
    - Orchestrator filters configured mobs by:
      - matching `event_type`,
      - matching `payload.text` against rule `match` logic.
    - For each match, sidecar creates intent envelope (for example `intent_type="say"` with generated text).

11. Sidecar posts intent back to WR Core.
    - Sidecar calls WR Core ingress endpoint:
      - `POST /api/v1/internal/ai/intents/`
      - `Authorization: Bearer <WR_CORE_AI_INGRESS_TOKEN>`

12. WR Core validates and converts intent into command text.
    - `backend/spawns/views.py:AIIntentIngress` checks bearer token.
    - `backend/spawns/serializers.py:AIIntentIngressSerializer` validates:
      - `intent_type` in `say|emote`,
      - `mob_key` format and mob existence,
      - `world_key` format and world/mob consistency,
      - non-empty `text`.
    - Ingress builds command:
      - `say <text>` or `emote <text>`.

13. WR Core invokes the mob command.
    - Ingress calls:
      - `dispatch_command(command_type="text", actor_type="mob", actor_id=<mob_id>, payload={"text": command_text, "__ai_intent_source": True})`
    - This is the same text-command pipeline, now with a mob actor.

14. Mob speech/emote events are published to players.
    - Mob `SayAction` (or `EmoteAction`) publishes room notification events.
    - Player in room receives `notification.cmd.say.success` (or emote) and sees the mob reply in console.

## Why This Does Not Recursively Loop

- Sidecar forwarding is restricted to player-originated events (`player.<id>` actor key).
- Mob-originated events (`mob.<id>`) are ignored by `maybe_enqueue_ai_sidecar_event_forwarding`.
- That prevents mob replies from re-triggering sidecar forwarding.

## Ordering and Timing Notes

- This is asynchronous (`WebSocket -> Celery -> HTTP sidecar -> HTTP ingress -> command dispatch`).
- A player may see:
  - their own `You say '...'` immediately,
  - mob response slightly later,
  - occasional interleaving with subsequent commands.
- This is expected behavior for the sidecar path.

## Quick Validation Checklist

1. WR Core env includes:
   - `WR_AI_EVENT_FORWARD_URL`
   - `WR_AI_EVENT_TYPES=cmd.say.success,...`
   - `WR_CORE_AI_INGRESS_TOKEN`
2. Sidecar config contains matching `mob_key` and `trigger_rules`.
3. Sidecar uses the same ingress token as WR Core.
4. Player and target mob are in same room/world context.
5. In logs, you should observe:
   - `spawns.tasks.forward_event_to_ai_sidecar` received/succeeded,
   - `POST /api/v1/internal/ai/intents/` returning `202`.
