# WR2 Condition Builder Guide

This guide explains the shared WR2 condition system used by triggers, quests,
and ability requirements.

Conditions are small YAML mappings that answer one question: should this thing
be available right now?

Use conditions when content should depend on the player, room, nearby mobs,
world state, quest progress, event data, or an ability requirement.

## Basic Shape

Blank conditions mean "always true".

```yaml
conditions: ""
```

Structured conditions are mappings:

```yaml
conditions:
  eq:
    - actor.archetype
    - warlord
```

Most new WR2 content should use structured conditions. Some older trigger and
action fields still accept legacy text conditions such as `level 5`, but new
state-aware content should use the structured format.

## Operators

| Operator | Shape | Meaning |
| --- | --- | --- |
| `always` | `{always: true}` | Always true or false. |
| `all` | `{all: [<condition>, ...]}` | Every child condition must pass. |
| `any` | `{any: [<condition>, ...]}` | At least one child condition must pass. |
| `not` | `{not: <condition>}` | Negates a condition. |
| `eq` | `{eq: [<path>, <value>]}` | Equal. |
| `ne` | `{ne: [<path>, <value>]}` | Not equal. |
| `gte` | `{gte: [<path>, <value>]}` | Greater than or equal. |
| `lte` | `{lte: [<path>, <value>]}` | Less than or equal. |
| `in` | `{in: [<path>, [<value>, ...]]}` | Path value is in a list. |
| `mob_present` | `{mob_present: <mob_definition_ref>}` | A spawned mob from that definition is present in the context room. |
| `quest_completed` | `{quest_completed: <quest_ref>}` | Player has completed a quest template. |
| `objective_complete` | `{objective_complete: <objective_id>}` | Current quest objective is complete. |

## Paths

The left side of comparison operators is a path.

Common paths:

| Prefix | Meaning |
| --- | --- |
| `actor.<field>` | The character using the trigger or ability. |
| `player.<field>` | Alias for the player in quest and player contexts. |
| `room.<field>` | The current room or room data. |
| `zone.<field>` | The current zone. |
| `world.<field>` | The current world or world data. |
| `state.world.<key>` | World-scoped runtime state. |
| `state.zone.<key>` | Zone-scoped runtime state. |
| `state.room.<key>` | Room-scoped runtime state. |
| `state.character.<key>` | Character-scoped runtime state. |
| `state.quest.<key>` | Quest-instance runtime state. |
| `template.<field>` | Current quest or ability template, where available. |
| `ability.<field>` | Current ability definition, in ability requirements. |
| `event.<field>` | Event payload data, in quest trackers and event/policy triggers. |
| `quest.current_step_id` | Current quest step id. |
| `quest.slot_bindings.<slot>` | Current quest slot binding. |

If a comparison value is wrapped in braces, it is also resolved as a path:

```yaml
eq:
  - state.room.weather_seen
  - "{state.world.weather}"
```

## Examples

Require the actor to be a Warlord:

```yaml
conditions:
  eq:
    - actor.archetype
    - warlord
```

Require the actor to not be a Warlord:

```yaml
conditions:
  not:
    eq:
      - actor.archetype
      - warlord
```

Require stormy world state and an unpulled room lever:

```yaml
conditions:
  all:
    - eq:
        - state.world.weather
        - stormy
    - not:
        eq:
          - state.room.lever_pulled
          - true
```

Require one of several classes:

```yaml
conditions:
  in:
    - actor.archetype
    - [warlord, tidecaller, mystic]
```

Require a completed quest:

```yaml
visible_if:
  quest_completed: first_steps
```

Require a guard mob to be present in the current context room:

```yaml
conditions:
  mob_present: mobdefinition.guard
```

To require more than one, use the expanded form:

```yaml
conditions:
  mob_present:
    ref: mobdefinition.guard
    count: 2
```

Use a typed mob-definition ref rather than a spawned mob id. For movement
hooks, the context room depends on the hook: `before_move_exit` checks the
origin room, while `before_move_enter` checks the destination room.

## Triggers

Triggers use conditions in `spec.conditions`.

WR2 does not store legacy Room Checks. Put entry and exit gates on
room-scoped `kind: policy` triggers and express their allow rule with this
shared condition syntax.

```yaml
kind: trigger
metadata:
  world: world.<world_id>
  name: Ring Storm Bell
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.<room_id>
  match: ring bell
  conditions:
    eq:
      - state.world.weather
      - stormy
  script: /cmd room -- /echo -- The storm bell rings.
```

Trigger conditions can read actor, room, zone, world, and `state.*` paths. If a
trigger shows a room action, the same condition also controls whether that
action appears in the room action list.

Movement policy and movement event triggers receive extra event paths:

| Path | Meaning |
| --- | --- |
| `event.direction` | Direction the player moved or tried to move. |
| `event.origin_room.id` | Room id the player moved from. |
| `event.origin_room.key` | Room key the player moved from. |
| `event.destination_room.id` | Room id the player moved toward. |
| `event.destination_room.key` | Room key the player moved toward. |
| `event.target.id` | The room this trigger is attached to. |

For `before_move_enter` and `after_move_enter`, `room.*` and `state.room.*`
refer to the destination room. For `before_move_exit` and `after_move_exit`,
they refer to the origin room. `mob_present` follows the same context-room
rule.

For example, a movement policy whose condition is `not: {mob_present:
mobdefinition.guard}` passes while the guard is absent and blocks movement
while the guard is present. Policy conditions describe when movement is
allowed, so the `not` is important for this pattern.

Legacy trigger/action text conditions still work for old content:

```yaml
conditions: level 5
```

Prefer structured conditions for new content.

## Quests

Quests use the same condition system in several places:

| Field | When it runs |
| --- | --- |
| `spec.discovery.visible_if` | When deciding whether to show a quest opportunity. |
| `spec.discovery.accept_if` | When a player accepts a visible opportunity. |
| objective `tracker.where` | When checking whether an event advances an objective. |
| story choice `if` | When deciding whether to show or allow a choice. |
| step transition `when` | When deciding whether to move to another step. |

Discovery conditions run before a quest instance exists, so `state.quest.*`,
`quest.*`, and `objective_complete` are not useful there.

Tracker `where` conditions receive `event.*` data:

```yaml
where:
  all:
    - eq: [event.target.definition_id, mobdefinition.saloon_bartender]
    - eq: [event.item.definition_id, itemdefinition.saloon_keg]
```

Step transitions can check objective status:

```yaml
transitions:
  - when:
      objective_complete: deliver_keg
    goto: resolved
```

`quest_completed` accepts integer ids, `questtemplate.<id>`,
`questtemplate.<slug>`, or a bare quest slug. Bare slugs are preferred in
authored content.

## Abilities

Abilities have two related gates:

- `availability` is the simple class and level gate.
- `requirements` uses the shared condition system for everything else.

Use `availability` for class/level access:

```yaml
availability:
  classes: [warlord]
  min_level: 2
```

Use `requirements` for equipment, state, quest, or other runtime checks:

```yaml
requirements:
  eq:
    - actor.equipment.offhand.equipment_type
    - shield
```

If an ability needs both authored conditions and imported review metadata, put
the condition under `requirements.conditions`:

```yaml
requirements:
  conditions:
    eq:
      - state.character.oath_sworn
      - true
  wr1_export:
    notes:
      - Review the original WR1 requirement.
```

Ability requirements are checked when a player learns or uses an ability. For
queued combat abilities, requirements are checked again when the round resolves.

## Authoring Advice

Use paths and values that are explicit and stable:

- prefer `state.*` for world-specific switches and progress flags
- prefer bare slugs for quest refs when possible
- use typed refs such as `mobdefinition.guard_captain` when comparing event
  definition ids
- use `mob_present` with a typed mob-definition ref when presence of a spawned
  mob should gate behavior; do not key authored behavior to one spawned mob id
- keep large logic trees small by splitting content into multiple triggers,
  quests, or abilities when that reads better
