# Player World Entry Flow

This document describes the current WR2 flow for getting a player from lobby to an active in-world game session.

## Preconditions

- The user is authenticated and has a valid access token.
- The user has a character (`Player`) in the target root world.
- FastAPI WebSocket endpoints are reachable:
  - Forge: `/ws/forge/`
  - Game: `/ws/game/cmd`

## 1) Lobby: choose or create a character

Before rendering `/`, `/home`, or `/lobby`, the client loads
`GET /api/v1/lobby/config/` once per page lifetime. A non-null `main_world_id`
(default `1`) redirects to `/worlds/<id>`, preserving query parameters and the
fragment. A null value restores the multi-world pages and the authenticated
directory flow. Site Control manages this platform setting independently of
world permissions and manifest content. No directory queries run during
single-world entry.

1. World lobby page loads world + user characters:
   - `GET /api/v1/lobby/worlds/<world_id>/`
   - `GET /api/v1/lobby/worlds/<world_id>/chars/`
2. If needed, user creates a character:
   - `POST /api/v1/lobby/worlds/<world_id>/chars/`
   - MPW: reuse existing multiplayer spawn world if present; otherwise create one.
   - SPW: create a new spawn world.
   - Evaluate the base world's `player_creation.instance_routes` in order using
     the new character's faction, archetype, gender, and level. For the first
     matching route, atomically reserve a fresh owner-only run in the selected
     single-player template and move the character and starting items there.
     No match keeps the normal base-world starting room.
     Record the base runtime and normal (including faction-specific) starting
     room as the return destination. The character remains visible in the base
     world lobby. Population loading waits for the normal asynchronous login.
3. If the user deletes an existing character from the world lobby, the client opens a confirmation dialog and only enables deletion after the user types the character name; matching is case-insensitive. The confirmed `DELETE /api/v1/lobby/worlds/<world_id>/chars/<player_id>/` request marks the player pending deletion when the player is not in-game and has no live instances.
4. User clicks `PLAY AS`, which dispatches `game/request_enter_world` with `player_id` and `world_id`.

## 2) Forge job: queue world entry

5. Frontend sends Forge WebSocket message:
   - `{ "type": "job", "job": "enter_world", "player_id": <id>, "world_id": <id> }`
6. FastAPI Forge handler queues Celery task `spawns.tasks.enter_world` with:
   - `player_id`, `world_id`, `client_id`, `ip`
7. Forge stores a Redis mapping `forge:connected_player:<player_id> -> <client_id>` for return messages.

## 3) Backend: execute enter-world

8. Celery task `spawns.tasks.enter_world`:
   - Loads the player.
   - Resolves the actual target spawn world from `player.world`.
   - Calls `WorldGate(player, world).enter(ip=...)`.
9. `WorldGate.enter` does:
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
   - For a pending initial instance, start its goal under the run lock in the
     login transaction and mark its first room-entry event ready for state sync.
   - Marks player `in_game = true`, updates connection/action timestamps.
   - Updates root world `last_entered_ts`.
   - Creates `PlayerEvent(login)`.
10. Task publishes Forge `job_complete`:
   - Success payload: `world`, `player_config`, `player_id`, `ws_uri`, `motd`
   - Error payload: `error`

## 4) Frontend handoff to gameplay socket

11. Frontend Forge module receives `job_complete`:
   - `status=error`: show error notification, stay in lobby.
   - `status=success`: dispatch `game/enter_ready_world`.
12. `enter_ready_world` stores pregame state (`world`, `player_config`, `player_id`, `ws_uri`) and opens game WebSocket.
13. On game WS open, client sends:
   - `{ "type": "system.connect", "data": { "player_key": "player.<id>" }, "token": "<access>" }`
14. Game WS authenticates, replies `system.connect.success`, then queues initial state sync.
15. After publishing the first state sync for an initial instance, durably
    enqueue its opening room-entry event once. The game connection is now ready
    to receive the introduction text.
16. On `cmd.state.sync.success`, frontend loads map/room/player/world state and routes to `/game`.

## Room key contract (WR2)

To keep map + room + actor payloads consistent across spawned worlds, WR2 treats
room keys as world-local identifiers:

- Public room key format: `room.<relative_id>`
- Authored manifest reference: `room@<relative_id>`
- Internal database identity: `Room.id` (never used as either public identity)

The public dotted key and manifest `@` reference carry the same immutable
world-relative id. The syntax differs because runtime entity keys and authored
manifest references are separate contracts.

The public key is not command or manifest input. Builder displays and copy
actions expose `room@<relative_id>`; interactive room-destination commands may
accept the bare relative id as shorthand. This avoids confusing a gameplay
`room.<relative_id>` key with legacy database-local dotted keys at import and
diagnostic boundaries.

For state payloads, these fields must always agree:

- `data.actor.room.key`
- `data.room.key`
- `data.map[*].key`
- Any room exits in `data.room` / `data.map[*]` (e.g., `north`, `east`, etc.)

Notes:

- `relative_id` is unique per world and remains stable for world-local references.
- `relative_id` is required and positive; a missing value is a data-integrity
  error rather than a reason to expose the database primary key.
- Frontend uses `data.room.key` as the primary active-room key and falls back to
  other known map keys only for resilience.

## Failure behavior

- Any enter preflight/startup failure is returned as Forge `job_complete` with `status=error`.
- In that case, user remains in lobby and sees the returned message.
- If startup fails after the spawn world enters `starting`, the backend recovers
  that spawn to `stopped` and cleans transient runtime state so the builder can
  fix the content issue and retry.
- The builder Admin world screen exposes a recovery action for transient spawn
  lifecycle states such as `starting`, `stopping`, `restarting`, or `queued`,
  plus the legacy `stored` state produced by stuck-world monitoring.
- If game WebSocket auth/connect fails, transition to `/game` does not complete.

## Related variants

- `POST /api/v1/game/enter/` exists as a legacy direct endpoint; lobby entry uses the Forge job flow above.
- `POST /api/v1/game/play/` (temporary intro user flow) still ends by calling `request_enter_world` and then follows this same entry path.
