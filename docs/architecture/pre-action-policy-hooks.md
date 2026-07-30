# WR2 Pre-Action Policy Hooks

## Purpose

WR2 uses first-class policy and event triggers for authored content that must
veto or react to common runtime actions. This replaces WR1 `RoomCheck` rather
than preserving a parallel room-check subsystem.

The immediate driver is movement gating:

- only warlords may enter a warlord-only room
- a locked ritual chamber can require quest state
- a faction area can reject hostile players
- a guard mob can block one direction while it is present

The same architecture should also support post-action room behavior:

- traps that spring when a player enters
- ambushes or room flavor on entry
- quest/event reactions to movement

This document defines the implemented v1 shape and the WR1 exporter boundary.

## Current State

WR2 ordinary movement and direction-based Charge evaluate room-scoped
`before_move_exit` and `before_move_enter` policies before changing the
player's room. Successful movement then publishes `cmd.move.success` and may
run post-move event triggers.

Flee is a delayed combat action. Its candidate directions use the movement
policies when the player starts preparing, and the chosen direction is
revalidated when the flee completes. This prevents a route from remaining
usable when a door, guard mob, or other authored condition changes during the
preparation delay. Before either route-policy pass, flee also checks the actor's
active-effect action rules. A root therefore blocks the action without scanning
candidate routes, and the completion check catches roots applied after
preparation begins.

WR2 has no `RoomCheck`, `RoomCommandCheck`, or `RoomCommandCheckState` model,
runtime payload, API, or builder screen. The former **Rooms > Checks** slot is
**Rooms > Edit**, an on-demand YAML editor for the selected `kind: room`
manifest. Movement policies are authored separately in **Rooms > Triggers**.

Any conversion of live WR1 content happens before import in the WR1 manifest
exporter. WR2 starts from the exported room and trigger documents; there is no
in-place legacy-row migration or compatibility table.

## Design Decision

Use a small generic policy hook layer built on the existing WR2 trigger,
manifest, and condition systems.

Do not add a new room-check-specific subsystem.

Use the trigger kind:

- `policy`: evaluates before a resolved action is applied and may veto it

Keep existing trigger/event behavior for post-action reactions:

- `event`: reacts after an action has emitted a canonical event

The authoring distinction is:

- policy hooks answer "may this happen?"
- event triggers answer "what happens after this happened?"

Active-effect `action_rule` primitives are a separate mechanical precondition.
They answer whether the affected actor may attempt a named action, regardless of
which room or route is involved. For example, a root uses `phase:
before_action`, `rule: prevent`, `actions: [flee]`, and `reason: rooted`. Room
policies still decide whether each otherwise-available exit may be traversed.

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

Supported movement hooks:

- `before_move_exit`: evaluated against the origin room before leaving it
- `before_move_enter`: evaluated against the destination room before entering it
- `after_move_exit`: post-action event hook for leaving a room
- `after_move_enter`: source-specific post-action hook for `source: move`
  (ordinary movement and adjacent-room charge)

The separate room-scoped `event: enter` lifecycle is the recommended universal
player-arrival hook. It also covers flee, transfer, death, jump, connected
character reset to a different room or runtime world, and instance
entry/leave/reset; those non-policy relocation paths do not become
movement-policy checks. Login,
reconnect, and offline location repair do not emit it.

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

### Mob-Guarded Exit Policy

The structured `mob_present` condition checks for a spawned mob from a
particular definition in the policy's context room. Conditions on a policy
describe when the action is allowed, so negate the presence check to block an
exit while the mob is present.

```yaml
kind: trigger
metadata:
  world: world.23
  name: Guard Blocks East
spec:
  scope: room
  kind: policy
  event: before_move_exit
  target:
    type: room
    key: room.120
  match: east
  conditions:
    not:
      mob_present: mobdefinition.guard
  failure_message: The guard bars the eastern way.
  order: 0
  is_active: true
```

This policy applies to normal movement, movement-based abilities such as
Charge, and flee-route selection. For `before_move_exit`, `mob_present` checks
the origin room. For `before_move_enter`, it checks the destination room.

### Universal Entry Trap

```yaml
kind: trigger
metadata:
  world: world.23
  name: Spear Trap
spec:
  scope: room
  kind: event
  event: enter
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
happen. It reacts after any supported player arrival succeeds. Use
`after_move_enter` instead only when the trap is intentionally limited to
ordinary movement and adjacent-room charge.

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
8. Publish the canonical room-entry lifecycle, which dispatches
   `after_move_exit`, destination mob and room `enter`, then
   `after_move_enter`.

Policies should run before mutating room/stamina state.

Post-action triggers should run after state and base client events are
published, and trigger-side failures should not undo the movement.

## Runtime Flee Flow

Flee uses the movement policy layer without turning every combat round into a
policy scan:

1. Evaluate the actor's active-effect `before_action` rules for `flee`. If a
   rule prevents it, fail before choosing a route or reserving stamina.
2. Build the mechanically available flee destinations.
3. Evaluate both `before_move_exit` and `before_move_enter` for each candidate.
4. Randomly choose from the candidates that remain, reserve the movement cost,
   and begin flee preparation.
5. When the delayed flee completes, evaluate the `flee` action rule again. If a
   root landed during preparation, clear the pending flee, refund the reserved
   movement cost, and keep the player in combat.
6. If the action is still allowed, revalidate only the stored direction.
7. If it is now blocked, rebuild the candidate list and choose another eligible
   exit when possible.
8. If no eligible exit remains, fail the flee without moving the player.

The completion-time check is required because authored state can change after
the initial choice. A root can land, a guard can enter, a door can close, or a
state-backed policy can change while the player prepares. Action-rule failure is
not a route failure, so it does not try to reroute around the effect.

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
- query only the acting character's indexed active-effect rows for action rules,
  and stop before route-policy work when `flee` is prevented
- keep the flee completion fast path to revalidating only its stored direction
- rebuild all flee candidates only when that stored direction becomes invalid
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

`mob_present` should resolve its typed mob-definition ref in the effective
world and use a narrow existence query for the context room. It must not build
the legacy full room-condition payload merely to answer the presence question.
Policy hook negative caching still ensures that rooms with no matching hooks do
not pay that presence-query cost.

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

## WR1 Export Relationship

WR1 room checks map into policy trigger documents:

- `prevent=enter` -> `kind: policy`, `event: before_move_enter`, target room
- `prevent=exit` -> `kind: policy`, `event: before_move_exit`, target room
- `prevent=all` -> one entry policy and one exit policy
- direction field -> policy applicability through event direction
- blocking predicate -> an equivalent **allow** predicate in the shared WR2
  condition framework
- `failure_msg` -> `failure_message`

The polarity change matters: a WR1 check described when movement was blocked,
while `spec.conditions` on a policy describes when movement is allowed. For
example, a legacy guard-present block becomes `not: {mob_present:
mobdefinition.guard}`.

The WR1 exporter should emit policy triggers only where it can preserve the
predicate exactly and should report unsupported rows for author review. The
authoritative field-by-field mappings and unsupported condition list live in
[yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md).

`RoomCommandCheck` is not equivalent to an ordinary `kind: command` trigger.
The former vetoed already resolved commands, while the latter handles an
authored matched command. Until WR2 implements a `before_command` policy hook,
the exporter must report command-check rows instead of silently changing them
or recreating their model.

## V1 Implementation Status

### Phase 1: Movement Entry Policies

Implemented:

- `policy` is an accepted trigger kind
- `before_move_enter` is evaluated before movement mutation
- room policy lookup is cached by world, room, and event
- class/archetype-gated entry has regression coverage

### Phase 2: Exit Policies And Direction Context

Implemented:

- `before_move_exit` is evaluated before movement mutation
- conditions receive `event.direction`, origin, and destination refs
- direction-specific exit behavior has regression coverage

### Phase 3: Post-Move Room Events

Implemented:

- `after_move_enter` and `after_move_exit` are room event triggers
- the canonical movement path dispatches them after movement succeeds
- scripts remain post-action behavior rather than veto logic
- room-scoped `event: enter` is the universal player-arrival hook shared with
  non-movement relocation paths

### Phase 4: Builder UI And WR1 Export Boundary

Implemented in WR2:

- policy triggers appear in the room trigger UI
- policy authoring is documented in the trigger and condition builder guides
- **Rooms > Edit** exposes the selected room's canonical YAML
- legacy room-check UI, models, APIs, and runtime payloads are removed

Remaining outside WR2 runtime:

- teach the WR1 manifest exporter to emit supported movement policy triggers
  and explicit unsupported-row diagnostics using the mapping above

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
