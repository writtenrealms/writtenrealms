# Trigger Event Subscriptions

## Purpose

WR2 trigger execution is event-driven:

1. Command handlers orchestrate actions.
2. Actions emit canonical `GameEvent` objects.
3. `publish_events()` delivers those events to clients.
4. Trigger subscriptions consume emitted events and dispatch matching triggers.

This keeps trigger wiring out of individual command handlers and makes the
`Command -> Action -> Event -> Trigger` flow explicit.

## Runtime Path

- Event publisher: `backend/spawns/events.py`
- Subscription router: `backend/spawns/trigger_subscriptions.py`
- Trigger executor: `backend/spawns/triggers.py`
  (`execute_mob_event_triggers`, `execute_room_event_triggers`)

`publish_events()` dispatches subscriptions once per emitted `GameEvent`.

## Current Subscriptions

- `cmd.say.success` -> `MOB_REACTION_EVENT_SAYING`
- `cmd.move.success` -> `MOB_REACTION_EVENT_ENTERING`
- `cmd.move.success` -> `after_move_exit` room event triggers
- `cmd.move.success` -> `after_move_enter` room event triggers
- `affect.social` -> `MOB_REACTION_EVENT_SOCIAL`
- `affect.death` -> `after_death_room_enter` room event triggers

Subscriptions only trigger reactions for **player-originated** events. This
avoids recursion when mobs or room scripts emit the same event types.

Movement policy hooks such as `before_move_enter` are not subscriptions. They
run inside the movement handler before room and stamina state are changed.

### Social Subscription

`affect.social` is emitted only for a targeted social. The subscription accepts
only a player actor and a mob direct target in the same runtime world and room.
It passes the resolved social command as match text, filters reaction execution
to that mob id, and evaluates the trigger's normal WR2 `conditions` against the
player actor. Targetless socials, player targets, mob-originated socials, and
bystander mobs are ignored.

The social catalog is cached per authored base world. A social action renders
once for each applicable audience cohort (actor, direct target, and witnesses),
reuses the witness text for all witnesses, and obtains those witnesses with one
indexed room-player fanout query. Restricting subscription dispatch to the
directly targeted mob avoids a room-wide mob scan on every social.

## Why This Design

- Removes hard-coded trigger calls from specific handlers.
- Lets triggers subscribe to the same canonical event stream seen by clients.
- Scales better as new triggerable events are added.

## Adding A New Triggerable Event

1. Ensure the action emits a canonical `GameEvent` in `publish_events()` flow.
2. Add an entry in `backend/spawns/trigger_subscriptions.py` for that event type.
3. Map event payload into trigger context (`actor`, `room`, optional match text/value).
4. Add WR2 tests proving subscription dispatch and trigger behavior.

## Failure Behavior

Trigger matching/execution should not block primary event publication. Trigger-side
errors should be handled in trigger execution paths and surfaced to scripts/logs,
not by dropping the base game event.
