# WR2 State Builder Guide

This guide explains the WR2 `state` system for builders.

Use `state` as the single concept for mutable runtime values.

Do not author new content around separate legacy terms like:

- `facts`
- `marks`
- `zone_data`

Those older names now exist only as compatibility paths behind the scenes.

## Mental Model

State is sparse key/value data attached to a scope.

Supported builder-facing scopes today:

- `world`: shared across the current running world instance
- `zone`: shared across the current zone
- `room`: shared across the current room
- `character`: stored on the current player character
- `quest`: quest-local state inside an active quest instance

Examples:

- `state.world.weather = "rainy"`
- `state.room.lever_pulled = true`
- `state.character.met_quartermaster = true`
- `state.quest.delivery_count = 2`

## Key Rules

- Use lowercase `snake_case` keys.
- Keep keys specific: `weather`, `lever_pulled`, `north_control`.
- Use state for mutable runtime values, not for fixed tags like room flags.
- Prefer one clear key over inventing a new noun.

## Builder Commands

Use `/state` to inspect or change current state directly.

Examples:

```text
/state show world
/state get world weather
/state set world weather -- rainy
/state set room lever_pulled true
/state add character rumor_count 1
/state clear room lever_pulled
```

Supported command scopes are `world`, `zone`, `room`, and `character`.

## Output Templates

Command text and quest text can render state values with Jinja-style templates:

```text
/echo -- The weather is {{ state.world.weather }}.
/cmd room -- say The lever is {{ state.room.lever_pulled }}.
```

This works in places such as:

- `/echo`
- trigger scripts
- `mob_command` output text in quests
- quest `recap`
- quest `text.body`
- quest choice text

Available template state objects:

- `state.world`
- `state.zone`
- `state.room`
- `state.character`
- `state.quest` when rendering inside a quest instance

## Triggers

Triggers can both read and write state.

For full trigger authoring guidance aimed at builders, also read
[trigger-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/trigger-builder-guide.md).
For condition operators and paths, also read
[condition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/condition-builder-guide.md).

Use structured conditions with the shared WR2 condition DSL:

```yaml
kind: trigger
metadata:
  world: world.<world_id>
  name: Weather Bell
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
      - rainy
  script: |
    /state set room bell_rung true
    /echo -- The bell rings through the rain.
    /echo -- Current weather: {{ state.world.weather }}.
```

Common trigger patterns:

- gate an action with `state.world.*` or `state.room.*`
- flip a room flag-like runtime value with `/state set room ...`
- broadcast dynamic text with `{{ state.* }}` substitutions

For post-entry room events such as traps, use room state to keep the event from
repeating:

```yaml
conditions:
  not:
    eq:
      - state.room.trap_sprung
      - true
script: |
  /cmd room -- /echo -- Spears snap out from the walls.
  /cmd room -- /state set room trap_sprung true
```

## Quests

Quests can read state in conditions and write state in effects.

### Conditions

Use state paths anywhere the shared condition DSL accepts a path:

```yaml
visible_if:
  eq:
    - state.world.weather
    - stormy
```

Other common places:

- `accept_if`
- objective tracker `where`
- choice `if`
- transition `when`

### Effects

Use these typed effects:

```yaml
effects:
  - type: set_state
    scope: character
    key: met_quartermaster
    value: true
  - type: increment_state
    scope: character
    key: deliveries_completed
    amount: 1
  - type: clear_state
    scope: room
    key: lever_pulled
```

Quest-local state uses `scope: quest`:

```yaml
effects:
  - type: set_state
    scope: quest
    key: branch
    value: harbor
```

For effect values that should copy from another state path, use brace-style
path references:

```yaml
effects:
  - type: set_state
    scope: character
    key: last_seen_weather
    value: "{state.world.weather}"
```

### Quest Text

Quest text can render current state directly:

```yaml
steps:
  - id: offer
    kind: storylet
    recap: The sky is {{ state.world.weather }}.
    text:
      body: The watch captain glances at the {{ state.world.weather }} horizon.
    choices:
      - id: continue
        text: Continue while it is {{ state.world.weather }}.
        goto: resolved
```

## Ability State

Abilities can also write state with `type: state` components. This is commonly
used for combo points or charges:

```yaml
components:
  - type: state
    scope: character
    key: combo_points
    op: increment
    amount: 1
    max: 5
    apply: on_hit
```

Ability damage and healing components can read state with `scaling.from`:

```yaml
components:
  - type: damage
    profile: basic_physical
    scaling:
      from: state.character.combo_points
      multiplier_per_point: 0.5
  - type: state
    scope: character
    key: combo_points
    op: clear
```

See [ability-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/ability-builder-guide.md)
for full examples and ordering notes.

## Zone Manifests

Zone manifests now use `spec.state` for zone-scoped mutable state.

Example:

```yaml
kind: zone
metadata:
  name: Harbor District
spec:
  state:
    fog_level: 2
    harbor_weather: windy
```

`spec.zone_data` is accepted as a legacy import alias, but new authored content
should use `spec.state`.

## Legacy Notes

Legacy names still map internally:

- world facts -> `state.world`
- player marks -> `state.character`
- zone_data -> `state.zone`

But new builder-facing content should use only `state`.
