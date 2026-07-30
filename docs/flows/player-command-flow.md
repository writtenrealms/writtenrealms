# Player Command to Console Response Flow

This document describes the WR2 flow from a player entering a command in the in-game input box to seeing the resulting response rendered in the console.

## Scope and Preconditions

- The player has already entered a world and is connected to the game WebSocket (`/ws/game/cmd`).
- The game UI is active (`/game`) and using the shared input component:
  - `frontend/src/components/game/Input.vue`
- Command handling described here is the text-command path (`cmd.text`), which is what the input box uses.

## 1) Input capture and local command shaping

1. Player submits the input form in `frontend/src/components/game/Input.vue`.
2. `onSubmit` builds the outgoing command text:
   - Uses current input text when present.
   - Reuses `last_sent` if submit is empty (except for communication commands such as `chat`, `tell`, `say`, etc.).
3. Keyboard shortcuts can also dispatch commands directly from the same component:
   - Arrow keys map to movement (`north`, `south`, `east`, `west`).
   - Shift+Up/Down maps to `up` / `down`.
4. Input dispatches `store.dispatch("game/cmd", <command>)`.

## 2) Frontend Vuex command action (`game/cmd`)

5. `frontend/src/store/modules/game.ts` action `cmd` receives the command.
6. It performs client-side preprocessing:
   - Focus substitutions (`kill` or some two-token commands may auto-fill focused target).
   - Semicolon splitting (`"look;inv"` becomes sequential silent sub-commands).
   - `quit` special-case sends `system.disconnect`.
7. It creates a secure client UUID for the command and commits a local echo:
   - `{ type: "cmd.text", text: <cmd>, request_id: <uuid>, echo: true }`
   - The echo begins in the `sending` state. This is still only a local
     browser state, not proof of server receipt.
8. It sends that same request id with the command via `sendWSMessage`.
   Missing or non-open sockets become `delivery unconfirmed`; mutating
   commands are never retried automatically.

## 3) WebSocket transport to backend

9. `sendWSMessage` appends the auth token and transmits JSON on the open gameplay socket.
10. FastAPI endpoint `fastapi_app/main.py` routes `/ws/game/cmd` to `fastapi_app/game_ws.py:handle_game_websocket`.
11. For `msg_type == "cmd.text"`, the gateway validates the request UUID and
    queues Celery task `spawns.tasks.handle_game_command` with:
   - `command_type="text"`
   - `player_id` / `player_key`
   - `payload={"text": <cmd>, "_request_id": <uuid>}`
   - `connection_id` (used to prevent stale-connection delivery)
12. After `send_task` returns, the gateway sends the same connection a private
    `cmd.request.queued` control frame. The frontend changes the existing echo
    to `received`; it does not add another transcript line.
13. If command publication raises, the gateway reports
    `command_delivery_unconfirmed`. Broker acceptance can be ambiguous across
    a connection failure, so this state is deliberately not called failed and
    the client does not retry.

## 4) Celery task and handler dispatch

14. `backend/spawns/tasks.py:handle_game_command` resolves player identity and calls `dispatch_command(...)`.
    Task-level player/handler lookup failures return a sanitized, correlated
    command response with `data.receipt_status == "failed"`. Otherwise
    unhandled exceptions return the same safe response and are then re-raised,
    so the task remains failed for server monitoring. Exception details stay
    in server logs, and commands are not retried automatically because a
    handler may already have mutated state.
15. `backend/spawns/handlers/registry.py:dispatch_command`:
   - Loads `Player`.
   - Resolves handler for `command_type`.
   - Builds `CommandContext`.
   - Invokes handler.
16. For text input, `backend/spawns/handlers/text.py:TextCommandHandler`:
   - Parses first token + args.
   - Replays history references and expands personal aliases before normal
     command routing. Expanded text is redispatched with recursion guards. Its
     correlated resolution replaces the original tracked echo in place, so an
     alias or `!<number>` replay keeps the same receipt without adding a second
     transcript line.
   - Splits command chains after alias expansion.
   - Resolves command using `resolve_text_handler` (prefix match across registered text commands).
   - Maps parsed args into payload fields expected by handlers (`direction`, `target`, `item`, etc.).
   - Delegates to resolved domain handler (`look`, `scan`, `move`, `drop`, `help`, `/load`, ...).
   - Resolves built-in command synonyms in the registered handler. For example,
     `loot` routes to the item handler as `get all corpse`.
   - `help <target>` first checks command help, then checks abilities the player
     already knows or can learn right now. Ability help is returned as plain
     console text with `data.ability`, not as the structured command help table.
17. If no handler is found:
   - Builder command (`/something`) => `cmd./something.error`.
   - Non-builder unknown => `cmd.text.error`.

## 5) Domain logic, events, and message construction

18. Concrete handlers in `backend/spawns/handlers/` call action classes in `backend/spawns/actions/`.
19. Actions return `ActionResult` containing one or more `GameEvent` objects (`backend/spawns/events.py`).
20. Event payload includes:
   - `type` (for example `cmd.look.success`, `cmd.move.success`, `cmd.help.success`)
   - `data` (structured payload for UI state + rendering)
   - optional `text` (pre-rendered lines for console display)
   - `recipients` (one or many players)
21. Many text bodies are generated by `backend/spawns/text_output.py:render_event_text`.

At the actor publication boundary, every correlated terminal
`cmd.*.success`, `cmd.*.error`, or `cmd.*.cancelled` response receives
`data.receipt_status == "completed"` unless its producer explicitly marked a
genuine processing failure. The receipt status describes server processing,
not the requested in-world outcome: an unknown command, a missing target, an
authored refusal, or a delayed action cancelled because the actor moved is
still an authoritative completed response. Recipient-specific stamping keeps
request identity and receipt status off room and third-party notifications.

For a command-fallback Trigger with typed steps, successful durable run
creation adds a private, correlated `cmd.trigger.accepted` event to the game
outbox. The status is truthful only after conditions, gating, active-run
limits, and the start transaction succeed. Completion and cancellation use
correlated control events as well. Player-safe cancellation prose is a
separate private player notification so a reconnecting player can still see it
without attaching a stale status to the new console.

## 6) Publish path back to the client

22. Handlers publish events via `publish_events(...)`.
23. `publish_events` calls `fastapi_app/game_ws.py:publish_to_player`, which publishes into Redis channel `game:pub`.
24. `GameConnectionManager` pub/sub listener in `fastapi_app/game_ws.py` consumes Redis messages and relays them to the active WebSocket.
25. If a `connection_id` is present and does not match the current connection for that player, the message is dropped as stale.

## 7) Frontend receives backend response

26. In `frontend/src/store/modules/game.ts`, `openWebSocket` sets `onmessage` to `receiveMessage`.
27. `receiveMessage` parses JSON and then:
   - Consumes receipt and Trigger lifecycle control frames before transcript
     handling and updates the echo with the same request id.
   - Leaves `cmd.request.queued` unresolved: broker acceptance proves receipt,
     not successful command execution.
   - Consumes a private `cmd.request.segments` plan before command-chain
     results. Every listed segment starts pending, so a fast first segment
     cannot settle the whole echo while later segments still run.
   - Applies correlated `cmd.*.success`, `cmd.*.error`, and asynchronous
     `cmd.*.cancelled` results to their `data.request_segment` immediately.
     Explicit `receipt_status` is authoritative: `completed` produces `✓`,
     `failed` produces a red `×`, and gateway `unconfirmed` remains a distinct
     red `×` state because broker publication may be ambiguous. The output and
     receipt symbol therefore render from the same WebSocket message.
   - Consumes private `cmd.request.completed` in the same way as success when a
     command finishes without actor-facing terminal output.
   - Treats handled `cmd.trigger.rejected` and controlled
     `cmd.trigger.cancelled` outcomes as completed processing while their
     refusal text is added to the transcript. An unexpected Trigger-step
     exception explicitly reports `receipt_status == "failed"` without
     exposing its internal exception text.
   - Applies lifecycle transitions monotonically. A late gateway receipt
     cannot regress an accepted Trigger, while a later positive server result
     can resolve an earlier `delivery unconfirmed` state.
   - Commits `message_add` for most message types (some periodic/utility types are skipped).
   - Updates live state slices (room, map, player, effects, targeting, etc.) based on message `type`.
   - Stores `last_message` and specific tracking pointers like `last_viewed_room_message`.
   - Console renderers use those tracking pointers to make item names
     interactive only in the current output for each view. Clicking an active
     item runs the same primary action shown in its hover lookup; superseded
     output remains readable but inert. Renderers also compare each item
     against the live room, inventory, or equipment context so a moved item
     cannot acquire a different action inside an older snapshot.

## 8) Console rendering

28. `frontend/src/components/game/console/Console.vue` reads `store.getters["game/consoleMessages"]`.
29. `game/consoleMessages` filters which messages should appear in console output.
30. For each message, `Console.vue` selects a component by `message.type`:
   - Room-like outputs (`cmd.look.success`, `cmd.move.success`, `cmd.state.sync.success`, etc.) -> `LookRoom.vue`
   - Help output -> `Help.vue`
   - Inventory output -> `Inventory.vue`
   - Combat/chat/etc. -> dedicated components
   - Fallback -> `Message.vue`
31. `Message.vue` renders plain text by splitting `message.text` on newline
    boundaries. For a local command echo it also renders one compact receipt
    symbol on the same line: `…` while unresolved, `✓` after an authoritative
    server outcome, or a red `×` on failure. The check acknowledges completed
    processing; it does not promise that the requested in-world action
    occurred. Pending and acknowledged marks are plain and non-interactive;
    only the failure mark discloses safe details on hover, keyboard focus, or
    mobile tap.
32. Console autoscroll behavior keeps view pinned to bottom unless the user has intentionally scrolled up.

`scan <direction>` follows this same fallback rendering path: the backend emits
`cmd.scan.success` with `data.chars` for the adjacent room and pre-rendered
text, and `Console.vue` renders it with the generic `Message.vue` component.

## Quick Example (`look`)

1. Player submits `look` in `Input.vue`.
2. Frontend echoes `{ type: "cmd.text", text: "look", request_id: <uuid>,
   echo: true }` with an unresolved `…` indicator.
3. Frontend sends the same request id with the command.
4. The gateway returns `cmd.request.queued`; the indicator remains `…` because
   the command has not finished yet.
5. Backend resolves `text -> look` and runs `LookAction`.
6. Backend emits a correlated `cmd.look.success` with room payload and
   rendered text.
7. Frontend receives that terminal result, changes the echo to `✓`, updates
   room/map/player state, and renders the output via `LookRoom.vue` in the same
   update.

## Primary Files In This Flow

- `frontend/src/components/game/Input.vue`
- `frontend/src/store/modules/game.ts`
- `frontend/src/components/game/console/Console.vue`
- `frontend/src/components/game/console/Message.vue`
- `frontend/src/components/game/console/LookRoom.vue`
- `fastapi_app/main.py`
- `fastapi_app/game_ws.py`
- `backend/spawns/tasks.py`
- `backend/spawns/handlers/registry.py`
- `backend/spawns/handlers/text.py`
- `backend/spawns/handlers/base.py`
- `backend/spawns/handlers/information.py`
- `backend/spawns/handlers/movement.py`
- `backend/spawns/events.py`
- `backend/spawns/text_output.py`
