# Player World Entry Flow

This document describes the current WR2 flow for getting a player from lobby to an active in-world game session.

## Preconditions

- The user is authenticated and has a valid access token.
- The user has a character (`Player`) in the target root world.
- FastAPI WebSocket endpoints are reachable:
  - Forge: `/ws/forge/`
  - Game: `/ws/game/cmd`

## 1) Lobby: choose or create a character

1. World lobby page loads world + user characters:
   - `GET /api/v1/lobby/worlds/<world_id>/`
   - `GET /api/v1/lobby/worlds/<world_id>/chars/`
2. If needed, user creates a character:
   - `POST /api/v1/lobby/worlds/<world_id>/chars/`
   - MPW: reuse existing multiplayer spawn world if present; otherwise create one.
   - SPW: create a new spawn world.
3. User clicks `PLAY AS`, which dispatches `game/request_enter_world` with `player_id` and `world_id`.

## 2) Forge job: queue world entry

4. Frontend sends Forge WebSocket message:
   - `{ "type": "job", "job": "enter_world", "player_id": <id>, "world_id": <id> }`
5. FastAPI Forge handler queues Celery task `spawns.tasks.enter_world` with:
   - `player_id`, `world_id`, `client_id`, `ip`
6. Forge stores a Redis mapping `forge:connected_player:<player_id> -> <client_id>` for return messages.

## 3) Backend: execute enter-world

7. Celery task `spawns.tasks.enter_world`:
   - Loads the player.
   - Resolves the actual target spawn world from `player.world`.
   - Calls `WorldGate(player, world).enter(ip=...)`.
8. `WorldGate.enter` does:
   - Auto-start world if lifecycle is `new` or `stopped` via `WorldSmith.start` (`starting` -> `running`).
   - Preflight checks (fail fast with error):
     - Site maintenance mode
     - Invalid/banned user or player
     - Disabled world (`no_start`)
     - World maintenance mode (non-builders)
     - MPW multichar restriction
     - Cross-race cooldown
     - World must be `running`
     - IP ban
     - Character currently being saved
   - Marks player `in_game = true`, updates connection/action timestamps.
   - Updates root world `last_entered_ts`.
   - Creates `PlayerEvent(login)`.
9. Task publishes Forge `job_complete`:
   - Success payload: `world`, `player_config`, `player_id`, `ws_uri`, `motd`
   - Error payload: `error`

## 4) Frontend handoff to gameplay socket

10. Frontend Forge module receives `job_complete`:
   - `status=error`: show error notification, stay in lobby.
   - `status=success`: dispatch `game/enter_ready_world`.
11. `enter_ready_world` stores pregame state (`world`, `player_config`, `player_id`, `ws_uri`) and opens game WebSocket.
12. On game WS open, client sends:
   - `{ "type": "system.connect", "data": { "player_key": "player.<id>" }, "token": "<access>" }`
13. Game WS authenticates, replies `system.connect.success`, then queues initial state sync.
14. On `cmd.state.sync.success`, frontend loads map/room/player/world state and routes to `/game`.

## Room key contract (WR2)

To keep map + room + actor payloads consistent across spawned worlds, WR2 treats
room keys as world-local identifiers:

- Public room key format: `room.<relative_id>`
- Internal DB identity: `room.id` (never used as a client-facing room key)

For state payloads, these fields must always agree:

- `data.actor.room.key`
- `data.room.key`
- `data.map[*].key`
- Any room exits in `data.room` / `data.map[*]` (e.g., `north`, `east`, etc.)

Notes:

- `relative_id` is unique per world and remains stable for world-local references.
- If `relative_id` is unexpectedly missing, backend falls back to `room.<id>` as a
  safety net, but this should be treated as a data integrity issue.
- Frontend uses `data.room.key` as the primary active-room key and falls back to
  other known map keys only for resilience.

## Failure behavior

- Any enter preflight/startup failure is returned as Forge `job_complete` with `status=error`.
- In that case, user remains in lobby and sees the returned message.
- If game WebSocket auth/connect fails, transition to `/game` does not complete.

## Related variants

- `POST /api/v1/game/enter/` exists as a legacy direct endpoint; lobby entry uses the Forge job flow above.
- `POST /api/v1/game/play/` (temporary intro user flow) still ends by calling `request_enter_world` and then follows this same entry path.
