# Doors And Keys

Doors can be open, closed, or locked. A two-sided door has the same live state
from both sides, so opening or locking it is immediately visible to characters
in either connected room. A deliberately one-way door has only its authored
face.

## Commands

Every door command requires a target:

```text
open <direction|door name>
close <direction|door name>
lock <direction|door name>
unlock <direction|door name>
```

A direction is usually the quickest target:

```text
open north
close east
```

Door names are useful when the prose matters:

```text
open iron gate
lock cellar door
```

If a name could refer to more than one door, add its direction to disambiguate
it, such as `open iron gate north`. The game never guesses between multiple
matching doors.

## Opening And Unlocking

`open` is immediate. It opens a closed door without a key. When a door is
locked, `open` looks for its matching key in your carried inventory and performs
the unlock and open as one atomic action:

```text
You unlock and open the iron gate.
```

This is the normal convenience path; you do not have to type `unlock` first.
If the door consumes its key, the key is removed only when the unlock succeeds,
and the command reports that it was consumed. A missing or incorrect key does
not change the door.

Use `unlock` when you want to unlock a door while leaving it closed. It is also
immediate and requires the matching key.

## Closing And Locking

Closing an open door takes 2.5 seconds. The door remains open while you begin
closing it, and characters on both sides can see that the close has started.
This wind-up keeps another player from turning an ordinary door into an
instantaneous, repeatedly spammed barrier.

Repeating `close` does not queue more closes or restart the timer. Moving,
being transferred, dying, disconnecting, or leaving the runtime world cancels
the attempt. Looking, speaking, and other non-physical commands remain
available while the close is pending.

`lock` requires the matching key:

- A closed door locks immediately.
- An open door uses the same 2.5-second protected wind-up, then closes and
  locks as one operation.
- A door that is already locked remains locked.

If another action changes the door before your delayed close or close-and-lock
finishes, the pending attempt becomes stale and is cancelled instead of
overwriting the newer state. Several characters may begin closing the same
door, but only the first valid completion changes it.

## Door State And Travel

You cannot travel through a closed or locked door. If you try, the game names
the door and its current state:

```text
The bronze is closed.
```

Use `look <direction>` (or a direction abbreviation) to check a door without
changing it:

```text
look north
The bronze is closed.

look n
The bronze is open.
```

Directional look reports `open`, `closed`, or `locked`. If there is no door in
that direction, `look` continues treating the argument as an ordinary room
target.

Door changes update the map and both connected rooms immediately. Closing a
door never unlocks it: issuing `close` against an already locked door leaves it
locked.

Door changes are scoped to the current runtime world. Two separate instance
runs that use the same authored room and door do not share live door state.
