# WR2 Pre-Action Policy Hooks

## Purpose

WR2 needs a first-class way for authored content to veto or react to common
runtime actions without reviving WR1 `RoomCheck` as a permanent parallel
system.

The immediate driver is movement gating:

- only warlords may enter a warlord-only room
- a locked ritual chamber can require quest state
- a faction area can reject hostile players

The same architecture should also support post-action room behavior:

- traps that spring when a player enters
- ambushes or room flavor on entry
- quest/event reactions to movement

This document defines the implemented v1 shape and the remaining migration
direction.

## Current State

WR2 movement currently resolves and applies movement inside the movement
handler:

1. validate direction
2. validate an exit exists
3. validate door state
4. validate stamina and hard-coded room constraints such as water travel
5. change the player's room
6. publish `cmd.move.success`
7. event subscriptions may run trigger reactions afterward

That works for post-move reactions, but it cannot prevent the move after a
condition fails. Current trigger subscriptions run after `cmd.move.success`.

Legacy `RoomCheck` still exists in model/UI code, but it is a WR1-era concept
with its own predicate vocabulary. It should be treated as migration input, not
the WR2 end-state.

## Design Decision

Use a small generic policy hook layer built on the existing WR2 trigger,
manifest, and condition systems.

Do not add a new room-check-specific subsystem.

Add a new trigger kind:

- `policy`: evaluates before a resolved action is applied and may veto it

Keep existing trigger/event behavior for post-action reactions:

- `event`: reacts after an action has emitted a canonical event

The authoring distinction is:

- policy hooks answer "may this happen?"
- event triggers answer "what happens after this happened?"

## Policy Contract

A policy trigger is a trigger whose `spec.kind` is `policy`.

Policy triggers:

- are active only when `is_active` is true
- are selected by scope, target, and event
- evaluate `conditions` through the centralized WR2 condition framework
- pass when conditions evaluate true
- fail when conditions evaluate false or invalid runtime context makes them
  false
- veto the underlying action on first failure
- return `failure_message` to the actor when supplied
- do not run arbitrary scripts in the initial implementation

That final point is intentional. Pre-action policy hooks sit on the hottest
runtime paths, especially movement. The v1 policy path should be condition
evaluation plus failure text only.

## Movement Hook Names

Initial movement hooks:

- `before_move_exit`: evaluated against the origin room before leaving it
- `before_move_enter`: evaluated against the destination room before entering it
- `after_move_exit`: optional post-action event hook for leaving a room
- `after_move_enter`: optional post-action event hook for entering a room

`before_*` hooks are policies and may veto.

`after_*` hooks are event triggers and may run scripts. They must not alter
whether the base movement succeeded.

## Authoring Examples

### Warlord-Only Entry Policy

```yaml
kind: trigger
metadata:
  world: world.23
  name: Warlord Gate
spec:
  scope: room
  kind: policy
  event: before_move_enter
  target:
    type: room
    key: room.999
  conditions:
    eq:
      - actor.archetype
      - warlord
  failure_message: Only warlords may enter.
  order: 0
  is_active: true
```

### Direction-Specific Exit Policy

Direction should be available through event context so a room can gate one exit
without gating all exits. Direction-specific policies use `spec.match` as an
applicability filter against the movement direction.

```yaml
kind: trigger
metadata:
  world: world.23
  name: North Gate Requires Badge
spec:
  scope: room
  kind: policy
  event: before_move_exit
  target:
    type: room
    key: room.120
  match: north
  conditions:
    eq:
      - state.character.has_badge
      - true
  failure_message: The northern guard bars your path.
  order: 0
  is_active: true
```

If `match` is blank, the policy applies to every direction for that room.

### Entry Trap

```yaml
kind: trigger
metadata:
  world: world.23
  name: Spear Trap
spec:
  scope: room
  kind: event
  event: after_move_enter
  target:
    type: room
    key: room.999
  conditions:
    not:
      eq:
        - state.room.trap_sprung
        - true
  script: |
    /cmd room -- /echo -- Spears snap out from the walls.
    /cmd room -- /state set room trap_sprung true
  order: 0
  is_active: true
```

The trap is not a policy because it does not decide whether movement may
happen. It reacts after entry succeeds.

## Runtime Movement Flow

Target flow for player movement:

1. Resolve direction and destination room.
2. Apply hard-coded mechanical checks that remain core movement rules:
   no exit, closed/locked door, stamina, water travel.
3. Evaluate `before_move_exit` policies for the origin room.
4. Evaluate `before_move_enter` policies for the destination room.
5. If any policy fails, publish a movement error and stop.
6. Change the player's room and charge stamina.
7. Publish `cmd.move.success`.
8. Dispatch post-action movement events such as `after_move_exit` and
   `after_move_enter`.

Policies should run before mutating room/stamina state.

Post-action triggers should run after state and base client events are
published, and trigger-side failures should not undo the movement.

## Policy Evaluation Context

Movement policy evaluation needs a compact context:

- `actor`: the player attempting movement
- `origin_room`: the room being left
- `room`: the target room for `before_move_enter`, or origin room for
  `before_move_exit`
- `world`: the effective world context
- `event.direction`: movement direction entered by the player
- `event.origin_room`: stable room ref for the origin
- `event.destination_room`: stable room ref for the destination

Conditions must use the existing WR2 condition framework. Any missing predicate
coverage should be added there rather than creating policy-specific checks.

## Multiple Policies

When multiple policies apply:

- evaluate by `order`, then creation/id order for deterministic behavior
- all applicable policies must pass
- stop on the first failure
- use that policy's `failure_message` if present
- otherwise return a generic movement failure

This keeps the hot path simple and avoids collecting a large explanation tree
on every movement.

## Performance Requirements

Movement is one of the highest-volume runtime operations. The default path must
stay fast when a room has no policies.

Required performance design:

- cache active policy and room-event lookups by world, room, event, and kind
- cache negative lookups, meaning "there are no active hooks here"
- avoid database queries in the common no-policy movement path
- compile or normalize policy condition payloads before or during cache fill
- invalidate affected policy cache entries on trigger create/update/delete
- add database indexes for policy lookup

Suggested lookup key shape:

```text
policy-hooks:{world_id}:{room_id}:{event}
```

Direction can remain inside event context at first. If direction-specific
policies become common, add direction-aware indexing/cache keys later.

Suggested database index shape:

```text
Trigger(world, kind, event, scope, target_type, target_id, is_active, order)
```

Do not perform broad world-level scans during movement.

## Cache Payload

The cache payload should be small and runtime-focused:

- trigger id
- name/key
- order
- event
- condition payload
- failure message
- show-details flag if retained

It should not store full YAML or builder-facing metadata.

## Failure Behavior

A failed policy should produce the same kind of player-facing error as other
movement failures:

- command type: `cmd.move.error`
- text: policy failure message or generic blocked message
- data: stable error code, for example `policy_blocked`

Do not publish `cmd.move.success` when a policy vetoes movement.

Do not run `after_move_*` triggers when movement is vetoed.

## Relationship To Legacy RoomCheck

Legacy room checks map naturally into policies:

- `prevent=enter` -> `kind: policy`, `event: before_move_enter`, target room
- `prevent=exit` -> `kind: policy`, `event: before_move_exit`, target room
- direction field -> policy applicability through event direction
- `conditions` -> shared WR2 condition framework
- `failure_msg` -> `failure_message`

Migration should convert authored room checks to policy triggers where possible.

Do not add new behavior to legacy `RoomCheck` except where needed to support
migration or compatibility before removal.

## V1 Implementation Checklist

### Phase 1: Movement Entry Policies

- add `policy` as an accepted trigger kind
- support `before_move_enter`
- evaluate room-scoped policy conditions before `ChangeRoomAction`
- cache policy lookup by world, destination room, and event
- add tests for class/archetype-gated entry

### Phase 2: Exit Policies And Direction Context

- support `before_move_exit`
- expose `event.direction`, origin, and destination refs to conditions
- add tests for direction-specific exit behavior

### Phase 3: Post-Move Room Events

- add `after_move_enter` and optionally `after_move_exit`
- dispatch them from the canonical movement event path
- support scripts only in post-action event triggers
- add trap-style tests

### Phase 4: Migration And Builder UI

- show policy triggers in the existing room trigger list/detail UI
- document policy authoring in the trigger/condition builder guides
- add migration tooling for legacy room checks
- remove or hide legacy room check builder UI after parity is proven

## Non-Goals For V1

- arbitrary pre-action scripts
- a second condition language
- complete replacement of all command hooks
- complex policy result aggregation
- cross-room/world scans during movement
- durable policy state separate from scoped state

## Open Questions

- Should world/zone-scoped movement policies be in v1, or should v1 stay room
  scoped only?
- Should policy failures support both private actor text and room-visible text?
- Should cache invalidation remain model-signal based, or move into an explicit
  trigger service layer as trigger writes become more centralized?
