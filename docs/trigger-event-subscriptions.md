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
- Trigger executor: `backend/spawns/triggers.py` (`execute_mob_event_triggers`)

`publish_events()` dispatches subscriptions once per emitted `GameEvent`.

## Current Subscriptions

- `cmd.say.success` -> `MOB_REACTION_EVENT_SAYING`
- `cmd.move.success` -> `MOB_REACTION_EVENT_ENTERING`

Subscriptions only trigger mob event reactions for **player-originated** events.
This avoids recursion when mobs execute scripts that emit the same event types.

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
