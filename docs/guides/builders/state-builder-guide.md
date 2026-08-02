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

- `world`: shared across the exact current runtime world
- `zone`: shared across the current zone in that runtime world
- `room`: shared across the current room in that runtime world
- `character`: stored on the current player or mob
- `quest`: quest-local state inside an active quest instance

Examples:

- `state.world.weather = "rainy"`
- `state.room.lever_pulled = true`
- `state.character.met_quartermaster = true`
- `state.quest.delivery_count = 2`

Authored worlds, zones, and rooms hold `initial_state`, which is a seed for a
new runtime world. It is not the live value. `/state`, conditions, and
`{{ state.* }}` templates always read the current runtime copy.

That distinction is especially important for instances:

- a base world and an instance template each author their own defaults
- each active instance run gets its own world, zone, and room state
- two runs of the same template never share mutable state
- instance state does not implicitly read or write base-world runtime state
- resetting an instance reseeds only that run from the template defaults

Player character state follows the player between the base world and
instances. Mob character state belongs to one spawned mob and is deleted with
that mob.

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
/state add character self rumor_count 1
/state set character joe pull_lever true
/state clear room lever_pulled
```

Supported command scopes are `world`, `zone`, `room`, and `character`.

For `character` state, always provide the target. Use `self` for your own
player character, or use a player or mob name/key. Scripted room triggers
should use `{{ actor_key }}` for the triggering character:

```text
/cmd room -- /state set character {{ actor_key }} pull_lever true
```

For example, a builder can inspect or change a mob in the current runtime
world:

```text
/state show character greek-commander
/state set character mob.42 captive true
```

The command changes only that spawned mob. It does not change the mob
definition or future copies.

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
[trigger-builder-guide.md](trigger-builder-guide.md).
For condition operators and paths, also read
[condition-builder-guide.md](condition-builder-guide.md).

Use structured conditions with the shared WR2 condition DSL:

```yaml
kind: trigger
metadata:
  world: world.<world_id>
  name: Weather Bell
spec:
  scope: room
  kind: command
  target: room@<room_relative_id>
  match: ring bell
  conditions:
    eq:
      - state.world.weather
      - rainy
  script: |
    /cmd room -- /state set room bell_rung true
    /cmd room -- /echo -- The bell rings through the rain.
    /cmd room -- /echo -- Current weather: {{ state.world.weather }}.
```

Common trigger patterns:

- gate an action with `state.world.*` or `state.room.*`
- flip a room flag-like runtime value with `/cmd room -- /state set room ...`
- mark the triggering player with
  `/cmd room -- /state set character {{ actor_key }} ...`
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

See [ability-builder-guide.md](ability-builder-guide.md)
for full examples and ordering notes.

## Initial State In Manifests

Use `spec.initial_state` on authored world, zone, and room manifests. These
values seed new runtime worlds; they are not a way to edit a running world's
current state.

Creating a zone with **World > Zones > Add** assigns the next available
world-relative zone ID. The resulting `zone@<relative_id>` manifest reference
is independent of the zone's database ID and remains stable when copied
between environments.

World example:

```yaml
kind: world
spec:
  initial_state:
    weather: clear
    invasion_active: false
```

Zone example:

```yaml
kind: zone
metadata:
  ref: zone@1
  name: Harbor District
spec:
  initial_state:
    fog_level: 2
    harbor_weather: windy
```

Room example:

```yaml
kind: room
metadata:
  ref: room@12
  name: Prison Cell
spec:
  coordinates:
    x: 4
    y: 2
    z: 0
  zone: zone@1
  initial_state:
    cell_door_open: false
```

Applying an `initial_state` edit does not overwrite live state. A base world
uses it when a new runtime world is created. An instance template uses it for
new runs and when an authorized builder resets a run. Ordinary stop/start of
an existing runtime preserves that runtime's current state.

Use `spec.initial_state` on a mob definition when every newly spawned copy
should begin with the values:

```yaml
kind: mobdefinition
metadata:
  slug: greek-captive-commander
  name: a Greek commander
spec:
  initial_state:
    captive: true
```

Spawn-plan mob entries can add or override seed values for one placement:

```yaml
kind: spawnplan
metadata:
  slug: camp-spawns
  name: Camp Spawns
spec:
  zone: zone@3
  respawn:
    mode: none
  entries:
    - slug: greek-commander
      source: mobdefinition.greek-captive-commander
      target: room@12
      count: 1
      initial_state:
        captive: true
```

`entries[].initial_state` is valid only for mob sources. Each new or respawned
mob receives a separate copy. Definition values are merged first and entry
values override matching keys. Reapplying the definition or plan does not
overwrite a surviving mob's current state.

Do not use a trait for an ordinary mutable condition such as `captive`.
Traits describe intrinsic or placement-time capabilities and modifiers; state
records a value that gameplay can change.

## Legacy Notes

Legacy concepts have clear WR2 conversion targets, not new authoring names:

- authored world facts -> world `initial_state`
- authored zone data -> zone `initial_state`
- player marks conceptually correspond to `state.character`, but the optional
  authored-world converter never exports player runtime data

The optional WR1 authored-world converter may map authored default facts and
zone data into `initial_state`. It must not import live player marks or other
runtime state. New builder-facing content should use only `state` and
`initial_state`.
