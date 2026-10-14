# World Upkeep

World upkeep removes expired corpses and abandoned items while worlds keep
running. Population replacement remains governed by spawn plans; door resets
remain governed by zone `door_reset`. Each has an independent scheduler.
`never_reload` skips scheduled population and door resets, but does not turn
off corpse or ground-item cleanup.

## Corpse and ground policy

Apply this through **World > Edit** or World Config YAML:

```yaml
kind: world
spec:
  upkeep:
    corpses:
      npc:
        unclaimed: {mode: fixed, seconds: 600}
        empty: {mode: fixed, seconds: 60}
      player:
        unclaimed: {mode: none}
        empty: {mode: fixed, seconds: 600}
    ground_items:
      abandoned: {mode: fixed, seconds: 3600}
```

These are the defaults. A `fixed` timer requires an integer from `1` through
`31536000` seconds (one year). Use `{mode: none}` to disable a timer. Omit
`seconds` for `none`; zero, negative values, booleans and unknown fields are
rejected. Disabling a timer allows objects in that category to accumulate.

The NPC unclaimed timer starts after death and loot placement, never when the
mob's empty corpse object was first created. When an NPC corpse first becomes
empty, its deadline becomes the earlier of its existing deadline and the
empty duration. Empty includes non-pickable contents: a corpse with anything
left inside is not empty.

Player corpses retaining death equipment do not expire automatically.
`player.unclaimed` currently accepts only `none`; a finite recovery policy has
not been introduced. The default player timer is **ten minutes after first
becoming empty**. Refilling an emptied corpse does not restart or cancel its
timer. At expiry, everything still inside disappears with the corpse.

Changes affect new deaths and new abandonment episodes. Existing episodes
retain the policy they started with, including the empty-corpse duration.
Partial updates preserve omitted fields. An instance template inherits the
base policy and may override individual durations. To clear all its overrides:

```yaml
kind: world
spec:
  upkeep: {mode: inherit_world}
```

Runs snapshot their effective world policy and their template's zone overrides
when created. Later authoring affects future runs. Instance time pauses also
pause item lifetimes; advancing a turn processes due items, and resuming
preserves time remaining.

## Zone overrides

Zones can override abandoned-item retention independently of corpse policy:

```yaml
kind: zone
metadata:
  ref: zone@1
  name: Starting Zone
spec:
  ground_item_upkeep: {mode: fixed, seconds: 1800}
```

Use `{mode: inherit_world}` to inherit or `{mode: none}` to disable the zone's
ground timer. Moving an already abandoned object to another zone does not
extend its deadline or start a new policy episode.

## Authored placements and storage

Items created by a spawn plan remain protected while in their original
placement. Ordinary `/load` or scripted room items receive the local ground
timer. Acquiring or displacing an authored item ends its placement protection;
its spawn provenance remains intact. Items count toward their entry's spawn
quota only at the configured destination. A picked-up item can be replaced;
returning it before the next spawn pass prevents an extra copy. Returning it
after replacement leaves surplus stock that follows ordinary cleanup policy.

Keeping a chest does not automatically keep everything deposited in it. An
ordinary retained chest applies ground retention to new deposits. Add
`upkeep.contents: preserve` to an item spawn entry to make its placement
protected storage:

```yaml
kind: spawnplan
metadata:
  slug: store-room
  name: Store Room
spec:
  zone: zone@1
  entries:
    - slug: storage-chest
      source: itemdefinition.storage-chest
      target: room@1
      count: 1
      upkeep: {contents: preserve}
```

`cleanup` is the default for `contents`. This setting is valid only for item
sources, including bundles; it is not a portable item-definition privilege.
A storage object loses this placement privilege when acquired or displaced.
Existing explicitly persistent objects retain their protection.

Picking up a bag cancels ground timers for the bag and its contents. Dropping
it again starts a fresh episode. Moving items between abandoned bags or rooms
cannot extend their earlier deadline; an item cannot outlive its expiring
container. Putting an item into explicit protected storage cancels its ground
timer. Corpses always govern their contents, including the player-equipment
exemption. Runtime death corpses cannot be nested in other containers. Rarity and value grant no cleanup exemption.

## Timing and world lifecycle

Cleanup normally wakes about every 30 seconds for a due runtime. Large batches
and busy locks can delay removal; nothing expires before its deadline. A room
notification and live room update accompany retirement. `look` shows approximate
time remaining at inspection, including a warning that container contents
share its fate. Repeated inspection does not extend a timer.

World stop/reset and instance teardown may remove items before their deadline.
Unclaimed player equipment is not a promise of persistence through those
operations. Existing runtime objects without reliable provenance are not
automatically classified or assigned retroactive deadlines.

See [Spawn Plans](spawn-plan-builder-guide.md),
[Rooms and Doors](room-builder-guide.md), and
[Items, Corpses, and Storage](../players/items-and-storage.md).
