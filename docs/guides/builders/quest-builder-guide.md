# WR2 Quest Builder Guide

This guide is for builders authoring quests in the new WR2 manifest system.
It explains how discovery works, what players are expected to type, and how to
author the common quest loops that are already supported in game.

For the shared mutable runtime data model used across quests, triggers, and
builder commands, also read
[state-builder-guide.md](state-builder-guide.md).
For shared condition syntax, read
[condition-builder-guide.md](condition-builder-guide.md).

## Mental Model

- A `quest template` is the authored YAML definition.
- An `opportunity` is a visible quest the player has not accepted yet.
- A `quest instance` is the running quest on one player.
- A `storylet` is one authored step inside the quest graph.

Today, builders author `kind: quest` manifests and players interact with them
through normal game verbs plus a few `quest` subcommands.

## Auto-Start Qualifying Events

`auto_start` quests do not start on every discovery refresh.

They currently auto-start only when one of these qualifying events happens:

- `cmd.state.sync.success`
- `cmd.look.success`
- `cmd.move.success`

That means:

- connecting or reconnecting can now start an `auto_start` quest immediately
- looking around can start one
- entering a new room can start one
- `say`, `talk`, `give`, `kill`, and `quest list` do not auto-start
quests, even though some of those flows still refresh quest discovery or
progression for other reasons

## Player Interaction Contract

This is the intended player loop right now:

1. The player sees a quest source in the world.
2. If the source is an NPC dialogue offer, the room UI shows `[ ! ]` on that mob.
3. If the source is a room prompt, the room UI shows the authored callout text with `[ ! ]`.
4. The player uses an in-world verb like `talk bartender` or `inspect`.
5. The game shows the authored pitch text and an explicit accept command:
  `quest accept <quest-slug>`.
6. Once accepted, quest progress happens through world actions:
  `look`, `move`, `say`, `talk`, `give`, `kill`.
7. When an NPC is ready for report-back or a hand-in, the room UI shows `[ ? ]`.
8. Completion still happens through the world verb that matches the objective:
  `talk captain`, `give keg bartender`, and so on.

There is intentionally no `quest complete` command right now.

That is by design: quests watch canonical game events. The player should
complete quests by doing something in the world, not by firing a generic
out-of-band completion verb.

## Quest Log

The Quest Log and `quest info <slug>` use the same card layout: quest title and
slug, status badge, authored text, divided recap, and objective progress rows.
Click a quest title in the log to collapse or expand its details. Repeatable
quests retain their cooldown beneath the heading, and **INFO** opens the quest
in the console. A last-change recap appears only when it adds information
beyond the current step's recap.

The player-facing Quest Log projects quest history into three groups:

- **Active Quests** shows current runs.
- **Repeatable Quests** shows the latest non-abandoned run of each quest whose
  current template mode is `always` or `cooldown`. Cooldown entries show either
  `Ready to repeat in ...` or `Ready to repeat`.
- **Resolved Quests** shows the latest non-abandoned run of each quest whose
  current template mode is `never`.

An active run takes precedence over that template's completion history, and
abandoned runs do not appear. The log keeps one card per completed quest
template rather than listing every repetition. It uses the template's current
repeatability mode, cooldown, and status, so a builder edit reclassifies the
card immediately without rewriting old quest instances. A repeatable template
that is currently `draft` or `archived` remains visible as unavailable.

Instance-template quests belong to one spawned instance run. A new run starts
with fresh progress, acceptance, offers, snoozes, and cooldowns, even while an
older run still exists. `repeatability.mode: never` means once per character
**within that run**; other repeatability policies also apply within the run.
Leaving hides its quests from the log and makes them unavailable to quest
commands. Re-entering the same retained run resumes them. Cleanup deletes both
its quest attempts and offer state. An in-place builder reset keeps the same
run and therefore keeps its quests.

Base-world quests keep their character-wide history and remain available while
the character visits instances. To make an instance outcome matter outside its
run, explicitly write character state with a quest effect or Trigger and read
that state through the shared condition DSL. Instance quest completion itself
is not a persistent prerequisite.

For scalability, each group is bounded to its most recent entries. If a world
exceeds a group limit, the Quest Log says how many recent quests it is showing
instead of silently omitting older entries.

## What `!` And `?` Mean

- `[ ! ]` means this mob currently has a visible `npc_dialogue` opportunity the
player can follow up on with `talk <mob>`.
- `[ ! ]` on a room callout means the current room has a visible `room_prompt`
opportunity the player can inspect with `inspect`.
- `[ ? ]` means this mob is currently a ready hand-in or report-back point for
an active quest.

For item turn-ins, `[ ? ]` appears when the player already has what they need
to finish the current quest step at that NPC.

For kill-and-return quests, `[ ? ]` appears after the kill objective is done
and the player can report back now.

## Supported Runtime Surface

Builders can rely on these pieces today:

- Discovery sources:
  - `auto_start`
  - `room_prompt`
  - `npc_dialogue`
- Step kinds:
  - `storylet`
  - `objective`
  - `resolution`
- Common objective tracker events:
  - `cmd.look.success`
  - `cmd.move.success`
  - `cmd.say.success`
  - `cmd.talk.success`
  - `quest.item.delivered`
  - `quest.mob.killed`
- Common reward/effect types:
  - `grant_currency`
  - `grant_xp`
  - `set_state`
  - `increment_state`
  - `clear_state`
  - constrained `mob_command`

## Quest Manifest Field Reference

This section documents the current quest manifest shape as it exists in code
today. Where the manifest schema accepts more values than the runtime actually
supports, that difference is called out explicitly.

### Top-Level Fields


| Field        | Required             | Values                                   | Notes                                                        |
| ------------ | -------------------- | ---------------------------------------- | ------------------------------------------------------------ |
| `apiVersion` | no                   | `v1alpha1`, `v1alpha2`, `v1alpha3`, or their `writtenrealms.com/` forms | Optional. Full-world export uses `writtenrealms.com/v1alpha3`. |
| `kind`       | yes                  | `quest`                                  | Case-insensitive on ingest.                                  |
| `operation`  | no                   | `apply`, `delete`                        | Defaults to `apply`. Use `delete` for delete manifests.      |
| `metadata`   | yes                  | mapping                                  | Quest identity and display metadata.                         |
| `spec`       | yes for create/apply | mapping                                  | Omit for `operation: delete`.                                |


### `metadata`


| Field   | Values                                   | Notes                                                                                                                        |
| ------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `world` | integer world id or `world.<id>`         | If present, it must match the selected world in the builder.                                                                 |
| `id`    | integer quest id or `questtemplate.<id>` | Optional. Used to target an existing quest on update/delete.                                                                 |
| `key`   | `questtemplate.<id>`                     | Exported for reference, but quest ingest currently resolves updates by `metadata.id` or `metadata.slug`, not `metadata.key`. |
| `slug`  | bare slug                                | Required for create. Also accepted as an update/delete identifier. Must be unique within the world.                          |
| `name`  | free text                                | Optional on update. If omitted on create, it is derived from the slug.                                                       |


### `spec`


| Field                            | Values                              | Notes                                                                                                                   |
| -------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `type`                           | `quest`, `contract`, `world_event`  | The runtime currently only runs `quest`.                                                                                |
| `scope`                          | `player`, `party`, `guild`, `world` | The runtime currently only runs `player`.                                                                               |
| `status`                         | `draft`, `active`, `archived`       | Only `active` quests appear in runtime discovery.                                                                       |
| `arc`                            | blank/omitted or quest arc slug     | The referenced arc must already exist in the same world.                                                                |
| `repeatability.mode`             | `never`, `cooldown`, `daily`, `always`       | See the repeatability rules below.                                                                                      |
| `repeatability.cooldown_seconds` | integer `>= 0`                      | Only valid when `repeatability.mode: cooldown`.                                                                         |
| `repeatability.reset_at`         | quoted `"HH:MM"` (24-hour time)      | Required with `daily`; local calendar reset time. |
| `repeatability.timezone`         | IANA timezone, e.g. `America/New_York` | Required with `daily`; follows local daylight saving changes. |
| `max_active`                     | integer `>= 0`                      | Stored on the template, but the current runtime still effectively enforces one active instance per player per template. |
| `discovery`                      | mapping                             | Controls how the quest becomes visible or starts.                                                                       |
| `slots`                          | mapping                             | Current runtime support is limited; see slot notes below.                                                               |
| `steps`                          | non-empty list                      | Step ids must be unique. The first step is the entry step.                                                              |
| `rewards`                        | mapping                             | Reward bucket keys are `complete`, `compromised`, `failed_forward`, `expired`.                                          |


### Repeatability Rules


| `repeatability.mode` | Behavior                                                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `never`              | The player may resolve the quest once in a non-abandoned state. A later `abandon` does not consume the quest.     |
| `cooldown`           | After a non-abandoned resolution, the quest stays unavailable until `repeatability.cooldown_seconds` has elapsed. |
| `daily`              | After a non-abandoned resolution, the quest stays unavailable until the next scheduled local daily reset. |
| `always`             | The quest can be reacquired immediately whenever its discovery rules match and it is not currently active.        |


Important runtime details:

- `quest abandon <slug-or-id>` marks the current instance as `abandoned`, but it
does not count as a completed run for repeatability.
- Abandoned quests are not shown in the player-facing resolved quest list.
- A non-repeatable quest becomes available again after abandon if its discovery
conditions still match.
- Repeatability is evaluated from the quest template's current manifest, not
  copied permanently onto each completed run. Changing `never` to `always`
  makes a previously completed quest immediately repeatable; changing it to
  `cooldown` measures the new duration from the player's latest non-abandoned
  completion. Daily policies likewise use the latest completion and current
  reset schedule. Changing any repeatable mode to `never` makes any prior
  non-abandoned completion final. Existing active runs are allowed to finish.
- On a partial update, explicitly changing `repeatability.mode` to `never` or
  `always` or `daily` automatically clears an inherited `cooldown_seconds`.
  Leaving `daily` automatically clears inherited `reset_at` and `timezone`.
  Explicit incompatible fields are still rejected. Changing to `cooldown` must
  supply the desired duration; changing to `daily` must supply its schedule.

### Calendar Dailies and First-Completion Bonuses

```yaml
repeatability:
  mode: daily
  reset_at: "05:00"
  timezone: America/New_York
```

This schedule resets at 5 a.m. New York time every day, including daylight saving
changes. Calendar dailies use real-world time even while an instance's gameplay
clock is paused; `cooldown` continues to use gameplay time. Quote `reset_at` to
keep YAML from interpreting the value as a number. Both schedule fields are
required for `daily` and must be omitted or empty in other modes. Unknown
repeatability fields are rejected.

A completion at 4:59 a.m. allows another attempt at 5 a.m. A completion exactly
at 5 a.m. counts toward the new day. An unfinished attempt survives the reset;
finishing it consumes the day in which it finishes. Abandoning an attempt does
not consume a daily or erase previous completion history. Base-world limits are
per character; instance-local quests retain their normal per-run scope.

For timezones with clock changes at the configured reset time, an ambiguous time
uses its earlier occurrence. A nonexistent time moves forward by the clock gap
(for example, 02:30 becomes 03:30 during a one-hour spring jump). A reset happens
only once on either kind of day. Use a time outside the transition, such as 05:00,
when possible.

To give a larger first reward, branch before resolution using the existing
`quest_completed` condition. For an objective's final transition, use ordered
transitions such as:

```yaml
transitions:
  - when:
      all:
        - objective_complete: final-delivery
        - quest_completed: athens-daily
    goto: paid-repeat
  - when:
      objective_complete: final-delivery
    goto: paid-first
```

Give `paid-first` and `paid-repeat` separate `resolution` steps with their own
`grant_currency` effects (for example 200 and 50 Obols). Keep `rewards.complete`
empty unless an additional reward should apply to both. The history condition
must run before the resolution step, because that step records the current
completion before applying its effects. First bonuses belong to each quest and
character, have no calendar expiration, and are not restored by daily resets.

Daily readiness is calculated when quests are read or accepted, using indexed
completion history and the same boundary helper used by the Quest Log. There is
no scheduled reset task, per-player sweep, or new database write at reset time.
The log retains its bounded, prefetched projection rather than querying each
card's history individually. Acceptance and completion remain serialized per
player so concurrent requests cannot pay the same attempt twice.

### `spec.discovery`


| Field              | Values                            | Notes                                                                                   |
| ------------------ | --------------------------------- | --------------------------------------------------------------------------------------- |
| `sources`          | list of discovery source mappings | Supported source types are listed below.                                                |
| `visible_if`       | condition DSL mapping             | Evaluated for opportunity visibility.                                                   |
| `accept_if`        | condition DSL mapping             | Evaluated at accept time. A quest can be visible but still reject acceptance.           |
| `salience`         | integer                           | Stored in the manifest, but not currently used by the runtime for ordering or priority. |
| `cooldown_seconds` | integer `>= 0`                    | Stored and validated, but not currently enforced by the runtime.                        |


Supported discovery source shapes:


| `type`         | Required fields                     | Behavior                                                                                                                      |
| -------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `auto_start`   | none                                | Starts automatically on `cmd.state.sync.success`, `cmd.look.success`, or `cmd.move.success`.                                  |
| `room_prompt`  | `room` or `room_id`, plus `callout`     | Shows as an opportunity when the player is in that room. Use canonical `room@<relative_id>`. Explicit `room.<database_pk>` and `room@x,y,z` values are import-only aliases that normalize immediately; bare integers are rejected. The room view shows the authored callout line with `[ ! ]` and the player can use `inspect` to see the quest pitch. |
| `npc_dialogue` | `mob_definition` or `mob_definition_id` | Shows through NPC dialogue and room UI markers. Mob refs can be ids, `mobdefinition.<id>`, `mobdefinition.<slug>`, or bare slugs. |

Quest item and mob references use WR2 definitions. Do not author new quest
content against legacy item or mob templates. WR1 export tooling must convert
old item and mob template references into `itemdefinition` and `mobdefinition`
refs before importing into WR2.

For quests authored in an instance template, mob and item definition slugs
resolve in the base world's shared library. Author those definitions on the
base world first. Room references and quest prerequisite slugs still resolve
inside the instance template, so sibling instances can use the same quest
slug without sharing quest identity.

### Condition DSL

Quests use the shared WR2 condition DSL documented in
[condition-builder-guide.md](condition-builder-guide.md).

Quest conditions appear in `visible_if`, `accept_if`, objective tracker
`where`, story choice `if`, and transition `when`.

Some operators are only meaningful in certain contexts:

- Discovery conditions (`visible_if`, `accept_if`) run without an active quest
  instance and without an event payload.
- Objective tracker `where` conditions run with event data.
- Choice `if` and transition `when` conditions run with an active quest
  instance.

Notes:

- `quest_completed` is the supported way to gate `visible_if` or `accept_if`
  on prior quest completion. Use a bare quest slug in authored content when
  possible:

  ```yaml
  visible_if:
    quest_completed: first_steps
  ```

- For multiple quest prerequisites, compose `quest_completed` with `all`,
  `any`, or `not`:

  ```yaml
  visible_if:
    all:
      - quest_completed: first_steps
      - quest_completed: town_favor

  accept_if:
    not:
      quest_completed: rival_path
  ```

- `quest_accepted` checks whether this character has started the quest in its
  applicable scope (current run for instance quests, character history for base quests),
  including automatic starts. Completion and abandonment leave it true.
- `quest_active` checks whether this character currently has an active attempt.
- `quest_completed` checks whether the character has a resolved quest instance
  with `resolution: complete`. `abandoned` does not count. A repeatable quest
  can be both active and previously completed.
- All three accept a bare slug or `questtemplate.<slug>`. Bare integer ids and
  numeric strings are database-local references; typed numeric suffixes are
  numeric slugs. Slugs are local to the authored world or instance template.
  See [quest conditions](condition-builder-guide.md#quests) for examples,
  including room prompts before acceptance and after abandonment.
- `objective_complete` is only useful once the current quest instance exists.
  It should be used in step transitions and conditional choices, not in
  `visible_if` or `accept_if`.
- `quest.local_state.<key>` is kept as a legacy alias. New authored content
  should prefer `state.quest.<key>`.
- `event.<field>` paths are available for event-driven conditions such as
  objective tracker `where`. They are not populated in discovery conditions.
- `quest.*` paths require an active quest instance, so they are not populated in
  `visible_if` or `accept_if`.
- Text fields such as `recap`, `text.body`, and choice text support Jinja-style
  substitutions like `{{ state.world.weather }}`.
- Effect values still use path references in brace form, for example
  `value: "{state.world.weather}"`.

Definition-id comparisons in conditions can use integer ids, typed keys like
`mobdefinition.42`, typed slug refs like `mobdefinition.saloon_bartender`, or
bare slugs where the runtime can infer the definition type.

### `spec.slots`

`spec.slots` is a mapping of slot names to slot definitions. Current runtime
support is limited to fixed bindings resolved at quest start.

Supported shape today:

```yaml
slots:
  bartender:
    resolve:
      type: fixed
      entity: saloon_bartender
```

You can also use `value:` instead of `entity:`. Resolved slot bindings can be
read later through condition paths like `quest.slot_bindings.bartender`.

### `spec.steps`

Common step fields:


| Field     | Values                                | Notes                                                                                        |
| --------- | ------------------------------------- | -------------------------------------------------------------------------------------------- |
| `id`      | non-empty string                      | Must be unique within the quest.                                                             |
| `kind`    | `storylet`, `objective`, `resolution` | The manifest schema also accepts `branch` and `timer`, but the current runtime rejects them. |
| `recap`   | string                                | Used heavily in quest info output and journal output.                                        |
| `text`    | mapping                               | `text.body` is the common authored field for player-facing pitch/body text.                  |
| `room_items` | list of room item mappings         | Viewer-specific quest pickups for the active step. They render in the room with `[ * ]` and are claimed with normal `get <item>`. |
| `effects` | list of effect mappings               | Applied when the step is entered, including resolution steps.                                |

Use a YAML literal block (`body: |`) with blank lines between paragraphs in
`text.body`. Quest cards and the Quest Log preserve those line breaks and blank
lines while wrapping text to fit the available width:

```yaml
text:
  body: |
    "The company commander needs defenders," the watchman says through the bars.

    Accept this quest with quest accept a-debt-to-athens.

    After accepting, follow the watchman's directions to enter the practice court.
```


Fields commonly used on specific step kinds:


| Step kind    | Common extra fields              | Notes                                                                                                     |
| ------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `storylet`   | `choices`                        | Used for explicit player choice branches.                                                                 |
| `objective`  | `objectives`, `transitions`      | Objectives progress from runtime events, then transitions move to the next step.                          |
| `resolution` | optional `resolution`, `effects` | `resolution` defaults to `complete`. Matching reward bucket effects are then applied from `spec.rewards`. |


### Objective Specs

Each item in `step.objectives` is a mapping with these common fields:


| Field                  | Values                             | Notes                                                                                                                                        |
| ---------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                   | non-empty string                   | Should be unique within the step.                                                                                                            |
| `text`                 | string                             | Displayed to the player as the objective label.                                                                                              |
| `tracker.event`        | event type string                  | Common values are `cmd.look.success`, `cmd.move.success`, `cmd.say.success`, `cmd.talk.success`, `quest.item.delivered`, `quest.mob.killed`. |
| `tracker.where`        | condition DSL mapping              | Optional filter on the triggering event.                                                                                                     |
| `progress.mode`        | `boolean`, `count`, `unique_count` | See progress behavior below.                                                                                                                 |
| `progress.target`      | integer `>= 1`                     | Defaults to `1` when omitted.                                                                                                                |
| `progress.distinct_by` | path string                        | Used by `unique_count` to dedupe progress values.                                                                                            |


Progress mode behavior:


| `progress.mode` | Behavior                                                                              |
| --------------- | ------------------------------------------------------------------------------------- |
| `boolean`       | Marks the objective complete on the first matching event.                             |
| `count`         | Increments by 1 per matching event until `target` is reached.                         |
| `unique_count`  | Tracks unique values using `distinct_by` and counts distinct matches toward `target`. |


### Choice Specs

Each item in `step.choices` is a mapping with these common fields:


| Field     | Values                  | Notes                                                  |
| --------- | ----------------------- | ------------------------------------------------------ |
| `id`      | non-empty string        | Referenced by `quest choose <slug-or-id> <choice_id>`. |
| `text`    | string                  | Player-facing label.                                   |
| `goto`    | step id                 | Required for a meaningful branch.                      |
| `if`      | condition DSL mapping   | Optional visibility gate for the choice.               |
| `effects` | list of effect mappings | Applied before the branch moves to `goto`.             |


### Transition Specs

Each item in `step.transitions` is a mapping with these common fields:


| Field     | Values                  | Notes                                                     |
| --------- | ----------------------- | --------------------------------------------------------- |
| `when`    | condition DSL mapping   | Transition condition.                                     |
| `goto`    | step id                 | Destination step when `when` evaluates true.              |
| `effects` | list of effect mappings | Applied immediately before entering the destination step. |


### Effects and Rewards

Accepting a quest immediately enters its first step and applies that step's
`effects`. Use this to have the quest giver speak or change the world:

```yaml
effects:
  - type: mob_command
    mob_definition: mobdefinition.watchman
    command: say Report back when you have finished practicing.
```

These effects also run for an automatically started quest and whenever the
graph re-enters that step. Use `quest_accepted`, `quest_active`, or
`quest_completed` conditions to check quest lifecycle directly; no state-writing
effect is needed. Character state remains available for other authored facts
and is not automatically cleared on completion or abandonment. Use
`scope: world` only when the value should belong to the current runtime world,
such as a private instance run.

Quest effects are separate from Trigger `steps` and `actions`. There is no
builder-facing quest-acceptance Trigger hook; put acceptance behavior in the
first step's `effects`. `mob_command` only supports the verbs listed below.

Use canonical effect `type` values when authoring new manifests:


| `type`        | Fields                                      | Behavior                                            |
| ------------- | ------------------------------------------- | --------------------------------------------------- |
| `set_local`   | `key`, `value` or `set_local: [key, value]` | Writes quest-local state under `quest.local_state`. |
| `grant_currency` | `currency`, `amount`                    | Adds one authored currency to the player immediately. |
| `grant_xp`    | `amount`                                    | Adds experience and applies world leveling immediately. |
| `adjust_reputation` | `faction`, `amount`                   | Adds or subtracts player standing with a reputation faction. |
| `mob_command` | `mob_definition` and `command` or `commands` | Runs a constrained mob speech/emote/echo or door-opening command. |

`grant_currency` requires an explicit currency code and a positive whole-number
amount. It does not fall back to Gold or the current default. See
[currency-builder-guide.md](currency-builder-guide.md).


Allowed `mob_command` verbs today:

- `say`
- `yell`
- `emote`
- `/echo`
- `/zecho`
- `/wecho`
- `/open`

For a quest giver who opens a locked door on acceptance, put this effect on
the first step:

```yaml
effects:
  - type: mob_command
    mob_definition: mobdefinition.watchman
    command: /open east -- The watchman turns his key and opens the cell door.
```

`/open` immediately opens even a locked door, without requiring a player key.
The mob must be in the player's current room and exact runtime world; instance
runs cannot open each other's doors. Use a direction or an unambiguous door
name. Optional text after `--` replaces the normal door-state message for
occupants of both adjoining rooms, and appears only if the state changes.
On acceptance, including automatic starts, the player sees the quest-start
confirmation before the door-opening narration.

Door opening uses the audited transactional command executor. A missing mob,
invalid door, or later failed quest transition rolls back the quest mutation
and door change without sending the door-opening message. Command chains and
arbitrary builder commands are not supported.

`spec.rewards` is a mapping from resolution key to a list of effects:

- `complete`
- `compromised`
- `failed_forward`
- `expired`

The runtime picks the reward bucket whose key matches the final resolution
string on the resolution step. If you omit `resolution:` on that step, it
defaults to `complete`.

## Bartender Example

This is the exact shape for the saloon bartender quest described in design
discussion.

Expected player flow:

```text
An Oak Bar
[ exits: E W D ]
The bartender polishes a glass behind the bar. [ ! ]

talk bartender
You talk to Saloon Bartender.
Quest available: A Keg for the Bar
"Could you grab a keg from the back for me?" the bartender asks. "I can't leave the bar unattended."
Bring the saloon keg to the bartender.
Accept with: quest accept saloon_keg_run

quest accept saloon_keg_run
Quest started: A Keg for the Bar
The bartender needs a fresh keg from the back room.
Lead: Bring the saloon keg to the bartender.

get keg
You pick up a saloon keg.

look
The bartender polishes a glass behind the bar. [ ? ]

give keg bartender
You give Saloon Keg to Saloon Bartender.
Quest resolved: A Keg for the Bar
The bartender rolls the fresh keg into place.
```

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: saloon_keg_run
  name: A Keg for the Bar
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: saloon_bartender
    visible_if: {}
    accept_if: {}
    salience: 25
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: deliver
      kind: objective
      recap: The bartender needs a fresh keg from the back room.
      text:
        body: |
          "Could you grab a keg from the back for me?" the bartender asks.
          "I can't leave the bar unattended."
      room_items:
        - id: saloon_keg
          room: room@2
          item_definition: saloon_keg
          room_description: A full saloon keg rests here.
      objectives:
        - id: deliver_keg
          text: Bring the saloon keg to the bartender.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.definition_id, saloon_bartender]
                - eq: [event.item.definition_id, saloon_keg]
          progress:
            mode: count
            target: 1
      transitions:
        - when:
            objective_complete: deliver_keg
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The bartender rolls the fresh keg into place.
  rewards:
    complete:
      - type: grant_currency
        currency: obol
        amount: 10
      - type: grant_xp
        amount: 50
      - type: mob_command
        mob_definition: saloon_bartender
        command: say Much obliged. Drinks keep flowing now.
    compromised: []
    failed_forward: []
    expired: []
```

Notes:

- `saloon_bartender` is a mob definition slug reference.
- `saloon_keg` is an item definition slug reference.
- `room_items` belongs on the active step, not in discovery. It makes the keg
  visible in the back room with `[ * ]` and lets the player use normal
  `get keg`. Its optional `room_description` overrides the item definition's
  room look line for that quest step.
- Step room items only accept item definitions of type `quest`.
- The quest pitch text lives in the first step’s `text.body`.
- The player still accepts explicitly with `quest accept saloon_keg_run`.
- Turn-in happens with `give keg bartender`, not with `quest complete`.
- Picking up the keg does not change the journal, because this quest progresses
from `quest.item.delivered`, not from `get`.

## How Journal Entries Work

Builders do not author quest journal entries directly today. The runtime writes
them automatically from quest state changes.

What creates a journal entry:

- entering a new step writes a journal entry using that step's `recap`
- updating objective progress writes another journal entry using the current
step's `recap`
- resolving a quest writes a journal entry using the resolution step's `recap`
- abandoning a quest writes a journal entry with `You abandoned <quest name>.`

What the player sees in `quest info <slug>`:

- `Recap` is the current step's `recap`
- `Objectives` come from the current step's visible objectives
- `Choices` come from the current step's visible choices
- `Last change` is the newest journal entry's `recap`

This has two important consequences for authors:

- if an objective updates but the quest stays on the same step, `Recap` and
`Last change` may be identical because both are driven by that step's
`recap`
- if you want the journal to feel meaningfully different after progress, split
the flow into more steps with new `recap` text instead of expecting a
separate per-objective journal message

Applied to the bartender example above:

- `quest accept saloon_keg_run` creates a journal entry with `The bartender needs a fresh keg from the back room.`
- `get keg` does not create a journal entry
- `give keg bartender` completes the objective and immediately transitions to
`resolved`, so the newest journal entry becomes `The bartender rolls the fresh keg into place.`

## Kill Then Return Example

This is the other common loop now supported in game.

```text
Captain Merrow stands watch here. [ ! ]

talk captain
Quest available: Rat Cull
Captain Merrow wants the tunnel rats culled.
Accept with: quest accept rat_cull

quest accept rat_cull
Quest started: Rat Cull
Captain Merrow wants the tunnel rats culled.

kill rat
kill rat

look
Captain Merrow stands watch here. [ ? ]

talk captain
Quest resolved: Rat Cull
Captain Merrow confirms the camp is safe for now.
```

The authoring pattern is:

- step 1 objective tracks `quest.mob.killed`
- transition goes to a report-back objective step
- report-back objective tracks `cmd.talk.success`
- that step transitions into `resolution`

## Builder Rules Of Thumb

- If the player should notice a quest by talking to an NPC, use
`discovery.sources: [{type: npc_dialogue, mob_definition: <slug>}]`.
- If the player should commit to the quest, keep acceptance explicit with
`quest accept <slug>`.
- Put the NPC’s actual ask in the first step’s `text.body`.
- Put the memory aid and immediate player-facing summary in `recap`.
- For item turn-ins, progress from `quest.item.delivered`.
- For report-back steps, progress from `cmd.talk.success`.
- Use definition slugs whenever possible. The runtime accepts ids too, but
slugs are much easier to read and survive export/import better.

## Testing Your Quest

Recommended smoke-test loop:

1. Apply the manifest in `World > Edit World`.
2. Move the player into the quest room.
3. `look` and confirm `[ ! ]` appears on the offering NPC.
4. `talk <mob>` and confirm the authored pitch plus `quest accept <slug>`.
5. `quest accept <slug>`.
6. Perform the required world actions.
7. Return and `look` again for `[ ? ]`.
8. Use the relevant world verb to finish:
  `talk <mob>` or `give <item> <mob>`.
9. `quest info <slug>` if the flow feels unclear at any point.

## Current Limitation To Remember

The `?` icon means “this NPC is the ready completion point now,” not “typing
`talk` will always finish it.”

Examples:

- report-back quest: `?` usually means `talk captain`
- item hand-in quest: `?` usually means `give keg bartender`

That distinction is important and is the reason the system currently does not
have a generic `quest complete` command.
