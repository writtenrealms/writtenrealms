# WR2 Zones, Rooms, And Doors Builder Guide

Rooms are authored as `kind: room` YAML manifests. Use **Rooms > Edit** to
edit the currently selected room in place. The page loads canonical YAML only
when opened, saves it through the shared world manifest endpoint, and then
refreshes both the room and the YAML shown in the editor.

Zones use the same workflow in **Zone > Config**. The Config page edits one
canonical `kind: zone` manifest; the separate **Zone > Utils** page contains
the operational **Move Zone** tool.

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

## Editing A Zone

1. Select a zone in the world editor.
2. Open **Zone > Config**.
3. Edit the loaded YAML, keeping `metadata.ref` unchanged.
4. Select **Save YAML**.
5. Review the refreshed canonical YAML.

The Config page accepts one zone manifest. Use **World > Edit World** for a
batch containing a zone and related room or spawn-plan documents. Use
**Zone > Utils** when moving every room in a zone by a coordinate offset; that
operation is not part of the zone manifest.

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: zone
metadata:
  ref: zone@2
  name: North Harbor
spec:
  description: Warehouses crowd the edge of the northern quay.
  notes: Builder-only encounter notes.
  initial_state:
    curfew_active: false
  respawn:
    mode: fixed
    seconds: 300
  door_reset:
    mode: none
  pvp_zone: false
  center: room@42
```

| Field | Purpose |
| --- | --- |
| `metadata.ref` | Stable zone identity as `zone@<relative_id>`. |
| `metadata.name` | Player-facing zone name. |
| `spec.description` | Zone description. |
| `spec.notes` | Builder-only notes. |
| `spec.initial_state` | State defaults copied into the zone for each new runtime world. |
| `spec.respawn` | Default replacement policy for `inherit_zone` spawn plans that omit their own `seconds`. |
| `spec.door_reset` | Independent automatic door-reset policy. |
| `spec.pvp_zone` | Whether the zone permits PvP when the world uses zone-gated PvP. |
| `spec.center` | Optional portable room ref used as the zone's map center. The room must already belong to this zone. |

Both zone policy objects use `mode: fixed` with an explicit non-negative
integer `seconds`, or `mode: none` with no `seconds` field. New zones default
both to `fixed` with `seconds: 300`. `respawn` is only the default for spawn
plans that explicitly inherit it without a plan-level `seconds` override.
`door_reset` affects only materialized runtime doorway state; `none` means
doors do not reset automatically.

Changing `spec.center` never moves a room between zones. Assign the room to
the zone first, then select it as the center.

Door-reset schedules are isolated per runtime world. Parallel instances using
the same authored zone do not share door state or timers. `/repop --doors` is
an explicit reset of the current runtime zone and does not consume or advance
the automatic schedule. A doorway between zones belongs to both endpoint
policies: either endpoint's due fixed schedule can reset it, but it resets at
most once during one world reconciliation pass.

## Navigating Builder Maps

The Room Details, zone, and whole-world maps share the same plane selector.
When the currently selected room has an up or down exit, the selector appears
in the lower-left corner of the map. Select an arrow to move to that connected
room and recenter the map on its plane. Selecting a normal room square also
makes that room the center of the zone or world map.

## Importing A Complete Room Set

**Create World** starts a world with scaffold `room@1` at `(0, 0, 0)`. A
complete multi-document import does not need to invent or remap a source room
to `room@1`. When the stream contains a `kind: world` document with an explicit
`starting_room` that is also declared by a room document, WR2 can replace the
untouched scaffold even if the incoming room references are sparse.

The offline Builder character and editor bookmark created by the Lobby are
moved to the imported starting room during that replacement. The operation is
atomic. If the scaffold was edited, contains authored content, or has an active
or ordinary player or any other dependent record, WR2 preserves it and rejects
an incoming coordinate collision instead. Single-room and partial streams do
not invoke complete-world scaffold replacement.

## Complete Example

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: room
metadata:
  ref: room@42
  name: North Gate
spec:
  coordinates:
    x: 10
    y: 4
    z: 0
  zone: zone@2
  description: An ironbound gate closes the northern road.
  note: Builder-only note about the gate encounter.
  type: road
  color: "#8a8175"
  is_landmark: true
  initial_state:
    gate_alarm_raised: false
  merchant:
    profile: merchantprofile.north-gate-market
  crafting:
    profile: craftingprofile.north-gate-forge
  trainer:
    profile: trainerprofile.north-gate-training
  exits:
    north: room@43
    east: null
    south: room@44
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
      to_room: room@43
      key: itemdefinition.north-gate-key
      destroy_key: false
      default_state: locked
```

## Identity And Location

`metadata.ref` is the room's stable, world-relative identity:

```yaml
metadata:
  ref: room@42
```

The positive relative id is immutable and is not reused after deletion.
Preserve it during ordinary edits. Move the room by changing its separate
coordinates:

```yaml
spec:
  coordinates:
    x: 12
    y: 4
    z: 0
```

Exits, spawn targets, Triggers, quests, and other semantic references continue
to use `room@42` after the move. In authored manifests, database keys such as
`room.187` and legacy coordinate refs such as `room@10,4,0` are import aliases.
They resolve inside the selected authored world and canonical YAML rewrites
them as stable relative refs; database keys are not portable to another
installation. The direct builder `/edit` command also accepts a database key
for local navigation, as described below, but does not turn it into a portable
content reference.

The builder uses that same stable identity in its canonical room URL, without
repeating the `room@` type prefix that is already supplied by the path:

```text
/build/worlds/23/rooms/42
```

Here `23` is the installation-local world database id and `42` is the room's
world-relative id. Moving the room does not change this URL. Hovering a room
link in the world, zone, and room breadcrumb navigation therefore exposes the
relative id a builder should use, while the room screen presents `room@42` as
the primary manifest identity and keeps its copy action in Technical details.
That panel also lists the room's relative id and installation-local database
id.
While playing in a world, builders also see that same identity after each room
name, for example `[ room@42 ]`.

From the game console, `/edit` opens a room's canonical builder URL in a new
browser tab. With no argument it uses the room the builder currently occupies:

```text
/edit
```

It also accepts three explicit selector forms. For this command only, a bare
positive number is a room database id, `room.<database_id>` is the explicit
database form, and `room@<relative_id>` is the stable relative ref:

```text
/edit 187
/edit room.187
/edit room@42
```

If database room 187 has the ref `room@42` in authored world 23, all three
explicit examples open `/build/worlds/23/rooms/42`. Database selectors resolve
only within the current authored world, and selector namespaces never fall
back to one another. For example, `/edit 187` fails when database room 187 is
absent even if `room@187` exists. This command-specific rule differs from
`/jump 187`, where the bare number means relative id 187.

Inside an instance run, `/edit` opens the corresponding authored room in the
instance-template world rather than a disposable runtime room. The resulting
URL still uses the template world's database id and the authored room's stable
relative id.

For staff troubleshooting, a room can also be opened by database id:

```text
/build/worlds/23/rooms/db/187
```

That path is a lookup alias, not a second canonical identity. After resolving
the room inside the selected world, the builder replaces it with
`/build/worlds/23/rooms/42`. Every builder who can view the room can see the
database id in the room screen's less-prominent Technical details when
diagnosing an installation-specific problem; the database-id lookup route
remains staff-only. Ordinary builder labels, searches, and room pickers use
`room@42` instead.

Do not interpret a bare room URL segment as either kind of id depending on
what happens to exist. `/rooms/42` always means relative id 42, and
`/rooms/db/42` always means database id 42. `/edit 42` treating the number as a
database id is a command-input rule; after resolution it still opens the
canonical relative-id URL. This is an intentional pre-launch breaking cutover
from the former `/rooms/<database_id>` route; old development bookmarks are not
compatibility aliases because an ambiguous fallback could open the wrong room.

Zones follow the same rule. A zone whose manifest reference is `zone@5` uses
the canonical builder route `/build/worlds/23/zones/5`, where `23` is the
installation-local world database id and `5` is the zone's world-relative id.
The zone's Rooms, Paths, Spawns, Config, and Utils routes retain that same
relative-id segment. Staff can use `/build/worlds/23/zones/db/38` to look up
database zone 38; the builder immediately replaces that alias with the
canonical relative-id route. The zone screen exposes the database id only to
staff in its collapsed technical details.

As with rooms, `/zones/5` never falls back to database zone 5. Keeping the
relative and database namespaces explicit prevents a valid number in one
namespace from silently selecting a different zone in the other.

New rooms require all three coordinates. An update may omit
`spec.coordinates` to preserve the current position, although canonical room
YAML always includes it.

`metadata.name` is the player-facing room name. `spec.zone` uses a portable
`zone@<relative_id>` ref. Copy that value from the zone screen or an existing
room rather than using a database id or a possibly duplicated zone name.

## Room Fields

| Field | Purpose |
| --- | --- |
| `metadata.name` | Player-facing room name. |
| `spec.coordinates` | Current map position as integer `x`, `y`, and `z`. |
| `spec.zone` | Owning zone as `zone@<relative_id>`, or blank for no zone. |
| `spec.description` | Main room prose shown to players. |
| `spec.note` | Builder-only authoring note. |
| `spec.type` | Terrain/type used by movement and room presentation. |
| `spec.color` | Optional builder-map display color. |
| `spec.is_landmark` | Whether the room is marked as a landmark. |
| `spec.initial_state` | State defaults copied into this room for each new runtime world. |
| `spec.merchant.profile` | Merchant Profile exposed directly by this room. |
| `spec.crafting.profile` | Crafting Profile exposed directly by this room. |
| `spec.trainer.profile` | Trainer Profile exposed directly by this room. |
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

## Room Services

Room Config can attach one Merchant Profile directly to the room. This is the
recommended shape for a market, counter, or other shop that should always be
available without a mob or Spawn Plan:

```yaml
spec:
  merchant:
    profile: merchantprofile.north-gate-market
```

The room automatically exposes its List and Offer actions. The room is the shop
provider, not a command issuer, and live stock remains in a runtime ledger
isolated to each running copy of the world. A decorative mob can be placed
separately. To make NPC presence or death control availability, leave the room
attachment empty and attach the Merchant Profile to that mob definition
instead.

Crafting uses the parallel room-provider shape:

```yaml
spec:
  crafting:
    profile: craftingprofile.north-gate-forge
```

Ability training uses a Trainer Profile containing existing ability slugs:

```yaml
spec:
  trainer:
    profile: trainerprofile.north-gate-training
```

Room Config exposes this attachment in the **ABILITY TRAINING** service card.
Choose a **Trainer Profile**, use **OPEN PROFILE** to inspect its YAML, or use
**CLEAR** and **SAVE** to remove the attachment. Trainer attachments require
builder rank 3 because attaching the first provider, or removing the last one,
changes world-wide learning and unlearning availability. Lower-rank room
editors can inspect the attachment but cannot change it.

The room automatically exposes Learn and Unlearn actions and remains an
available provider without a mob or Spawn Plan. Trainer `availability` belongs
only on mob-definition attachments and is rejected on a room. A decorative NPC
may still be placed here without a Trainer Profile.

Only profiles attached to at least one room or mob definition gate their
abilities. An unattached profile is a safe draft and does not change where
players can learn or unlearn anything.

The profile may use `spec.learning.conditions` and `max_known` to let eligible
players choose up to a fixed number from its catalog. The allowance follows the
profile, so attaching it to another room does not grant another set of slots.
A room accepts one Trainer Profile; use a mob provider or a different room when
native and cross-training catalogs need separate quotas. See
[Ability Trainers](ability-builder-guide.md#ability-trainers) for the full
policy shape and counting rules.

Omitting `merchant`, `crafting`, or `trainer` from a patch preserves the current
attachment. Set the section to `null` or use an empty mapping to clear it.

## Exits

Exit values use stable `room@<relative_id>` refs. Set a direction to `null`
when there is no exit:

```yaml
exits:
  north: room@43
  east: null
  south: room@44
  west: null
  up: null
  down: null
```

An exit is directional. When both rooms should link to each other, make sure
the neighboring room has the reverse exit as well. For coordinated layout
changes, apply both room documents together through **World > Edit World**.

## Cross-World Instance Links

The room refs in a `kind: room` document are local to that room's authored
world. Ordinary exits and doors do not use a database id or a bare `room@N` to
cross from a base world into an instance template.

When a base world and its instance templates are exported together, supported
cross-world fields live in the first `kind: worldbundle` document:

```yaml
kind: worldbundle
spec:
  worlds:
    - ref: world@base
      role: base
      name: Phalanx
    - ref: instance.hades
      role: instance
      slug: hades
      name: Hades
      parent: world@base
  links_mode: replace
  links:
    - relation: room.transfer_to
      source:
        world: world@base
        room: room@42
      target:
        world: instance.hades
        room: room@1
    - relation: room.enters_instance
      source:
        world: world@base
        room: room@42
      target:
        world: instance.hades
    - relation: room.exits_to
      source:
        world: instance.hades
        room: room@9
      target:
        world: world@base
        room: room@42
```

The explicit world scope disambiguates identical local refs in different
worlds. The base-room Instance Link picker uses the same idea in its labels,
for example `Hades Entrance (hades/room@1)`, because multiple templates may
each contain a local `room@1`. Family export gathers these links from the
authored foreign keys; family import restores them after all rooms have been
applied. Individual room
documents intentionally omit `transfer_to`, `enters_instance`, and
cross-world `exits_to`.

Export every instance through its base-world bundle. A standalone instance
export is rejected because it cannot carry the stable family scope, inherited
catalogs, and both sides of cross-world relationships portably.

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

`/open`, `/close`, and `/lock` can replace their normal room notification with
custom text. A direction target accepts the text directly; use `--` after a
door name so the target boundary stays unambiguous:

```text
/lock north The bronze doors close behind you. Nobody touches them.
/open ironbound gate -- The ironbound gate rises into the ceiling.
```

The override is one doorway state-change message, not an additional echo, and
is sent only when the state actually changes.

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
  target: room@120
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

For a multi-document import, WR2 reserves every declared room identity and
coordinate before applying relationships. Rooms may therefore reference rooms
declared later in the stream, including reciprocal exits and doors. The whole
stream remains atomic, and an unknown stable ref does not create an untitled
room unless a matching `kind: room` document declares it.

Rank 1-2 builders may edit rooms covered by their room or zone assignment.
Creating a room through YAML requires rank 3 or higher. The same permission
rules apply whether YAML is saved from **Rooms > Edit** or
**World > Edit World**. When the current builder may inspect a room but not
alter it, **Rooms > Edit** is view-only and disables save.

## Related Docs

- [YAML Manifest Guide](yaml-manifests.md)
- [trigger-builder-guide.md](trigger-builder-guide.md)
- [condition-builder-guide.md](condition-builder-guide.md)
- [spawn-plan-builder-guide.md](spawn-plan-builder-guide.md)
