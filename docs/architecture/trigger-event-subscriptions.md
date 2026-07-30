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
- `lifecycle.player.room.enter` -> destination mob-definition `enter`
  reactions and destination room-scoped `event: enter` triggers
- `lifecycle.player.room.enter` with `source: move` -> `after_move_exit` and
  `after_move_enter` compatibility room events
- `lifecycle.player.room.enter` with `source: death` ->
  `after_death_room_enter` compatibility room events
- `affect.social` -> `MOB_REACTION_EVENT_SOCIAL`
- legacy `notification./transfer.enter` -> transferred-mob reactions and
  compatibility for already queued pre-canonical player events

Voluntary-input subscriptions such as speech and social accept player-originated
events only. Structural room entry is intentionally different: a player really
changed location, so it remains triggerable when an audited Trigger command
caused the move. Transferred mobs retain their older mob-reaction continuation
but never run the player-only room hook.

Movement policy hooks such as `before_move_enter` are not subscriptions. They
run inside the movement handler before room and stamina state are changed.

### Canonical Player Room Entry

Every supported player-arrival path emits one internal
`lifecycle.player.room.enter` event rather than wiring room triggers directly
into each command:

| `event.source` | Arrival path |
| --- | --- |
| `move` | Ordinary movement and adjacent-room charge |
| `flee` | Successful PvE or PvP flee |
| `transfer` | `/transfer` that changes the player's room |
| `death` | Final committed death-routing destination |
| `jump` | `/jump` that changes the player's room |
| `character_reset` | Connected-player reset that changes room or runtime world |
| `instance_enter` | Entry into an instance runtime |
| `instance_leave` | Return from an instance runtime |
| `instance_reset` | Relocation/reset epoch inside an instance |

The event also carries the actor key, runtime-world id, location sequence,
origin and destination room ids, and an optional direction. Direction is
normalized for movement, charge, flee, and directional `/jump`, and is empty
for non-directional sources. Trigger condition context exposes these as
`event.source`, `event.direction`, `event.origin_room`, and
`event.destination_room`; the destination is the room/state context.

The room form is:

```yaml
spec:
  scope: room
  kind: event
  target:
    type: room
    key: room.<id>
  event: enter
```

It is distinct from the existing mob form:

```yaml
spec:
  scope: world
  kind: event
  target:
    type: mobdefinition
    key: mobdefinition.<id>
  event: enter
```

Scope and target type disambiguate the shared event text. On a canonical
arrival the executor runs, in order:

1. origin `after_move_exit`, but only for `source: move`;
2. destination mob-definition `enter` reactions;
3. destination room `enter` triggers;
4. destination `after_move_enter` for `source: move`, or
   `after_death_room_enter` for `source: death`.

The compatibility events remain supported so existing authored worlds do not
change meaning. New source-agnostic room behavior should use `enter`.

An ordinary relocation that does not change the player's location epoch emits
no canonical arrival. In particular, same-room transfer and jump operations
emit none, and `character_reset` is emitted only when a connected player's room
or runtime world changes. Death and instance reset deliberately increment the
location sequence and emit one even when the authored room id is unchanged.
Instance entry and leave also count across the runtime-world boundary. Before
running destination work, the subscriber verifies that the player is still in
game and still has the event's runtime world, destination room, and location
sequence. This
suppresses stale outbox deliveries and every intermediate arrival superseded
by a later location change. Durable event ids retain the existing
subscription-receipt deduplication. Login, reconnect, and offline location
repair do not emit this lifecycle event.

For outbox-backed arrivals, the subscriber captures room/mob reaction output
while the destination room and player are locked, inserts it as one bounded
derived outbox batch in that follow-up transaction, and flushes it after that
transaction commits. Trigger gates use atomic cache claims under concurrent
arrivals. Room-hook lookup reuses the existing world/event/scope/target index
and finite cache; the canonical fan-in avoids adding a separate trigger scan
to every movement implementation. Script-caused arrivals inherit command
depth, and reaction scripts stop at the existing eight-layer bound.

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
2. If the action relocates a player, emit `lifecycle.player.room.enter` with a
   new bounded `source` only when the established source list cannot describe
   it; do not add another room-arrival subscriber.
3. Otherwise add an entry in `backend/spawns/trigger_subscriptions.py` for the
   new event type.
4. Map event payload into trigger context (`actor`, `room`, optional match
   text/value).
5. Add backend tests proving subscription dispatch, stale-delivery suppression,
   and trigger behavior.

## Failure Behavior

Trigger matching/execution should not block primary event publication. Trigger-side
errors should be handled in trigger execution paths and surfaced to scripts/logs,
not by dropping the base game event.
