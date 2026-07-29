# WR2 Room Builder Guide

Rooms are authored as `kind: room` YAML manifests. Use **Rooms > Edit** to
edit the currently selected room in place. The page loads canonical YAML only
when opened, saves it through the shared world manifest endpoint, and then
refreshes both the room and the YAML shown in the editor.

Room movement rules and interactions are separate triggers. Use
**Rooms > Triggers** for command triggers, movement policies, and
post-movement events. WR2 has no Room Checks screen or room-check model.

## Editing A Room

1. Select a room in the world editor.
2. Open **Rooms > Edit**.
3. Edit the loaded YAML.
4. Keep `metadata.ref` unchanged when updating that room.
5. Select **Save YAML**.
6. Review the refreshed room and canonical YAML.

The editor accepts one room manifest. Use **World > Edit World** when applying
multiple related documents, such as a room plus a zone, neighboring rooms,
triggers, and spawn plans.

## Complete Example

```yaml
kind: room
metadata:
  ref: room@10,4,0
  name: North Gate
spec:
  zone: zone@2
  description: An ironbound gate closes the northern road.
  note: Builder-only note about the gate encounter.
  type: road
  color: "#8a8175"
  is_landmark: true
  initial_state:
    gate_alarm_raised: false
  exits:
    north: room@10,5,0
    east: null
    south: room@10,3,0
    west: null
    up: null
    down: null
  flags:
    - no_roam
  details:
    - keywords: gate ironbound
      description: Rivets run in black rows across the gate.
      is_hidden: false
  doors:
    - direction: north
      name: ironbound gate
      to_room: room@10,5,0
      key: itemdefinition.north-gate-key
      destroy_key: false
      default_state: locked
```

## Identity And Location

`metadata.ref` is the room's portable coordinate identity:

```yaml
metadata:
  ref: room@10,4,0
```

Coordinates are world-relative and survive export/import even when database
ids differ. Changing this ref does not move the currently selected row. It
means "apply a room at these coordinates" and may create or update a different
room. Preserve it during ordinary edits.

`metadata.name` is the player-facing room name. `spec.zone` uses a portable
`zone@<relative_id>` ref. Copy that value from the zone screen or an existing
room rather than using a database id or a possibly duplicated zone name.

## Room Fields

| Field | Purpose |
| --- | --- |
| `metadata.name` | Player-facing room name. |
| `spec.zone` | Owning zone as `zone@<relative_id>`, or blank for no zone. |
| `spec.description` | Main room prose shown to players. |
| `spec.note` | Builder-only authoring note. |
| `spec.type` | Terrain/type used by movement and room presentation. |
| `spec.color` | Optional builder-map display color. |
| `spec.is_landmark` | Whether the room is marked as a landmark. |
| `spec.initial_state` | State defaults copied into this room for each new runtime world. |
| `spec.exits` | Direction-to-room mappings. |
| `spec.flags` | Complete set of room behavior flags. |
| `spec.details` | Complete set of inspectable room details. |
| `spec.doors` | Complete set of doors originating in the room. |

Supported room types are `road`, `city`, `indoor`, `field`, `mountain`,
`forest`, `desert`, `water`, `shallow`, and `trail`.

`initial_state` is authored seed data, not the room's current live state. Use
it for resettable values such as `gate_alarm_raised: false`. At runtime,
`state.room.gate_alarm_raised` belongs to the exact current runtime world, so
parallel instance runs that use this room never share the value. Edit live
state with `/state`; edit the manifest when future runs should start
differently.

## Exits

Exit values use `room@x,y,z` refs. Set a direction to `null` when there is no
exit:

```yaml
exits:
  north: room@10,5,0
  east: null
  south: room@10,3,0
  west: null
  up: null
  down: null
```

An exit is directional. When both rooms should link to each other, make sure
the neighboring room has the reverse exit as well. For coordinated layout
changes, apply both room documents together through **World > Edit World**.

## Flags, Details, And Doors Replace Their Lists

The canonical room YAML includes the complete `flags`, `details`, and `doors`
collections. When any of those keys is present, saving replaces that whole
collection for the room. Do not omit an existing entry that should remain.

Flags are string codes. Current choices are `no_ride`, `no_load`, `no_roam`,
`dark`, `no_spell`, `peaceful`, `interest`, `fountain`, `trainer`, `inn`,
`exp`, `horse`, `shop`, `food`, `choke`, `smob`, `action`, `herb`, and
`no_quit`.

A detail needs search keywords and description text:

```yaml
details:
  - keywords: inscription runes
    description: The weathered runes name a forgotten king.
    is_hidden: false
```

A door identifies its direction and destination independently from the exit
map. `key` is optional; when present, use an `itemdefinition.<slug>` ref.
`default_state` is `open`, `closed`, or `locked`.

## Door Runtime Behavior

An authored door is one logical doorway. Reciprocal door entries provide a face
in each connected room and share `key`, `destroy_key`, and `default_state`;
manifest validation rejects reciprocal entries that disagree on those fields.
A deliberately one-way door remains a valid one-faced logical doorway.
Changing a connected exit from mutual to one-way removes only the reverse
face; changing it back adds the reciprocal face without discarding the door's
settings. Repointing one side to a different room splits that face into a new
logical doorway and preserves the former reverse face as a one-way door.

Its live state is `open`, `closed`, or `locked`. Every transition updates all
of its faces atomically in the current runtime world. Separate instance runs do
not share live door state.

Ordinary player behavior is deliberately passage-friendly:

- `open` is immediate.
- `open` on a locked door atomically unlocks and opens it when the player
  carries the configured key.
- `unlock` provides the precise alternative of unlocking while leaving the
  door closed.
- `close` has a 2.5-second wind-up.
- `lock` is immediate on a closed door; on an open door, it uses the same
  2.5-second wind-up before closing and locking.

The close wind-up prevents rapid repeated closing from acting like an
unconfigured lock. It is a runtime rule rather than a per-door authoring
setting.

Authored command-fallback Triggers may still use these verbs for actions such
as `open cage`. The built-in command takes precedence when its target resolves
to a real door; otherwise the normal command Trigger gets a chance to handle
the text.

Builders can force door states directly:

```text
/open north
/close ironbound gate
/lock north
/unlock north
```

Trusted Trigger scripts can use a mob or room issuer through `/cmd`:

```yaml
script: /cmd room -- /close north
script: /cmd gatekeeper -- /lock north
```

A builder's direct `/cmd room -- ...` does not delegate builder authority; use
the slash command directly outside a Trigger.

The slash forms are immediate, bypass keys, and are safe to retry. `/close`
never turns a locked door into an unlocked one. A repeated command for the
current state is a successful no-op and does not fire a door state-change
event. For the full permission matrix and command behavior, see
[builder-command-reference.md](builder-command-reference.md#open-close-lock-unlock).

## Movement Rules Use Policy Triggers

Do not add `checks` or `room_checks` to a room manifest. Gate entry or exit
with a room-scoped policy trigger:

```yaml
kind: trigger
metadata:
  name: Warlord Gate
spec:
  scope: room
  kind: policy
  event: before_move_enter
  target:
    type: room
    key: room.120
  conditions:
    eq:
      - actor.archetype
      - warlord
  failure_message: Only warlords may enter.
  order: 0
  is_active: true
```

Use `before_move_exit` on the origin room to gate leaving. Add `match: north`
or another direction when the policy should affect only one route. Policy
conditions state when movement is allowed; a false condition blocks movement.

For more examples, see
[trigger-builder-guide.md](trigger-builder-guide.md)
and
[condition-builder-guide.md](condition-builder-guide.md).

## Applying And Validation

Room manifests support `operation: apply` only. They do not have a room-delete
operation. Saving validates refs, room types, flags, directions, item keys, and
door states before applying the document.

Rank 1-2 builders may edit rooms covered by their room or zone assignment.
Creating a room through YAML requires rank 3 or higher. The same permission
rules apply whether YAML is saved from **Rooms > Edit** or
**World > Edit World**. When the current builder may inspect a room but not
alter it, **Rooms > Edit** is view-only and disables save.

## Related Docs

- [yaml-manifest-system.md](/Users/teebes/code/writtenrealms/docs/architecture/yaml-manifest-system.md)
- [trigger-builder-guide.md](trigger-builder-guide.md)
- [condition-builder-guide.md](condition-builder-guide.md)
- [spawn-plan-builder-guide.md](spawn-plan-builder-guide.md)
