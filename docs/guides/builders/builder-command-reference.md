# Builder Slash Command Reference

This guide is the builder-facing reference for slash commands: commands whose
text starts with `/`.

These commands are implemented as builder commands. A player can run them
directly only when that player character has builder access in the world. Some
commands are also script-safe, which means triggers or internal command dispatch
can run them as a player, mob, room, zone, or world issuer.

This guide covers slash commands only. Ordinary player commands such as `look`,
`say`, `get`, `give`, `kill`, `shop`, and `learn` are not listed here.

## Permission Model

Direct builder use:

- the issuer is a player character
- the player has `is_builder`
- the player's user can edit the current world

Script-safe use:

- the command is marked as script-safe by the runtime
- the command is executed from an internal script source such as a trigger or
  nested `/cmd`
- the issuer actor type must be supported by that command

Do not assume that every slash command is safe in room or mob scripts. Some
commands are intentionally builder-player-only because they are destructive or
admin-like.

Actor support means the command can be dispatched by that actor type. Individual
operations can still require context. For example, room-scope operations need a
current room, zone-scope operations need a current zone, and character-scope
state needs a player character.

## Issuer Matrix

Legend:

- `Direct`: a builder player can type the command directly.
- `Script`: the actor can run the command from an internal script source.
- `Mob`: a mob actor can run the command directly, including when targeted by
  nested `/cmd`.
- `No`: the actor cannot run the command.

This matrix describes ordinary direct and legacy-script issuer permissions.
Typed Trigger `command` actions have their own narrower audited contract; for
example, a player `trigger_actor` may execute the step-safe `/transfer self`
form even though arbitrary player scripts cannot issue `/transfer`.

Commands are listed alphabetically. Select a command to jump to its details.

| Command | Builder Player | Player Script | Mob Actor | Room Script | Zone Script | World Script |
| --- | --- | --- | --- | --- | --- | --- |
| [`/close`, `/lock`, `/open`, `/unlock`](#open-close-lock-unlock) | Direct | No | Script | Script | No | No |
| [`/cmd`, `/force`, `/rcmd`, `/wcmd`, `/zcmd`](#cmd-force-rcmd-zcmd-wcmd) | Direct | Script | Script | Script | Script | Script |
| [`/echo`, `/wecho`, `/zecho`](#echo-zecho-wecho) | Direct | Script | Script | Script | Script | Script |
| [`/edit`](#edit) | Direct | No | No | No | No | No |
| [`/exitinstance`](#exitinstance) | Direct | No | Script | Script | No | No |
| [`/grantitem`](#grantitem) | Direct | Script | Script | Script | No | No |
| [`/jump`](#jump) | Direct | No | No | No | No | No |
| [`/kill`](#kill) | Direct | No | Script | Script | No | No |
| [`/load`](#load) | Direct | Script | Script | Script | No | No |
| [`/purge`](#purge) | Direct | No | No | No | No | No |
| [`/regen`](#regen) | Direct | No | Mob | No | No | No |
| [`/repop`](#repop) | Direct | No | No | Script | No | No |
| [`/reset`](#reset) | Direct | No | No | No | No | No |
| [`/send`, `/sendexcept`](#send-sendexcept) | Direct | No | Script | Script | Script | Script |
| [`/set`](#set) | Direct | No | No | Script | No | No |
| [`/setclass`](#setclass) | Direct | Script | No | Script | No | No |
| [`/setcurrency`](#setcurrency) | Direct | No | No | No | No | No |
| [`/setlevel`](#setlevel) | Direct | No | No | No | No | No |
| [`/state`](#state) | Direct | No | Script | Script | Script | Script |
| [`/stats`](#stats) | Direct | No | No | No | No | No |
| [`/transfer`](#transfer) | Direct | No | Script | Script | No | No |

## Command Details

### `/load`

Format:

```text
/load <item|mob> <definition_id|slug> [cmd]
```

Loads an authored item or mob.

Item behavior depends on the issuer:

- player issuer: item loads into that player's inventory
- mob issuer: item loads into that mob's inventory
- room issuer: item loads onto the room floor

Mob behavior depends on the issuer:

- player issuer: mob loads into the player's current room
- mob issuer: mob loads into the issuer mob's current room
- room issuer: mob loads into that room

Selectors should reference WR2 item definitions or mob definitions by numeric
id or slug.

Examples:

```text
/load item starter-blade
/load mob road-bandit
/cmd room -- /load item temple-key
/cmd room -- /load mob road-bandit
```

Use `/load item` in a room script when the item should appear on the ground.
Use `/grantitem` when the item should go into a target character inventory.

### `/grantitem`

Format:

```text
/grantitem <target> <item_definition_id|item_slug>
/grantitem <target> -- <item_selector> <item_selector> ...
```

Loads an authored item into a target player or mob inventory.

The target is resolved in the issuer's current room. The target can be selected
by:

- key, such as `player.123` or `mob.456`
- exact or unambiguous player name
- mob keyword

Examples:

```text
/grantitem player.123 starter-blade
/grantitem aria starter-blade
/grantitem quartermaster supply-token
/grantitem player.123 -- starter-blade starter-shield starter-cloak
/cmd room -- /grantitem {{ actor_key }} tidecaller-starter-trident
```

Use `/grantitem` for pledge rewards, starter equipment, quest rewards, and any
scripted reward that should not appear on the room floor.

Use the `--` form when granting multiple items. The target is everything before
`--`; item selectors are whitespace-separated after `--`. Each item selector
should be a WR2 item definition id or slug. Multi-item grants are
validated before any item is spawned, so a bad selector prevents the whole grant
instead of creating a partial reward set.

### `/kill`

Format:

```text
/kill <target>
/kill <target> -- <private death message>
```

Instantly kills a player target in the issuer's current room. The target is
moved through the normal death-room pipeline; current health, energy, and
stamina are each set to 1; `affect.death` is emitted; and death-room event
triggers can run. The player recovers those resources through normal
regeneration.

The optional `--` message is sent to the killed player as the death text. Use
`&&` chaining with `/echo` for separate room flavor text.

Examples:

```text
/kill player.123
/kill aria -- The pit swallows you whole.
/cmd room -- /kill {{ actor_key }} -- The pit swallows you whole.
/cmd room -- /kill {{ actor_key }} -- The pit swallows you whole. && /cmd room -- /echo -- The floor seals again.
```

`/kill` currently targets players. It does not kill mobs; use combat or builder
cleanup commands for spawned mobs.

## World Death Modes

Player death penalties are controlled by `death_mode` on the world config. The
same mode is used for combat deaths and builder/script deaths that go through
the normal death-room pipeline.

Available modes:

| Mode | Effect |
| --- | --- |
| `lose_none` | Moves the player to the death room without changing equipment, inventory, or currency balances. |
| `lose_all` | Moves equipped items and carried inventory into a corpse in the room where the player died. |
| `lose_eq` | Moves equipped items into a corpse in the room where the player died; carried inventory stays with the player. |
| `lose_inv` | Moves carried inventory into a corpse in the room where the player died; equipment stays equipped. |
| `destroy_eq` | Deletes equipped items; carried inventory stays with the player. |
| `destroy_all` | Deletes equipped items and carried inventory. |
| `lose_currency` | On non-PvP deaths, charges `death_currency_penalty` of the player's configured `death_currency` balance, capped by the current balance. PvP deaths do not charge this cost. |

Every mode sets current health, energy, and stamina to 1. The modes only change
the item or currency penalty; none refills a player's resources.

Example world manifest fragment:

```yaml
kind: world
spec:
  death_room: room@1
  death_mode: lose_currency
  death_currency: obol
  death_currency_penalty: 0.2
```

### `/purge`

Format:

```text
/purge
/purge <target>
```

Deletes spawned content from the builder player's current room.

Targets:

- no target or `all`: remove room items and mobs
- `items`: remove room items
- `mobs`: remove room mobs
- a mob key or keyword: remove that mob
- an item key or keyword: remove that item from the builder's inventory or the
  room

Examples:

```text
/purge
/purge items
/purge mobs
/purge guard
```

This command is direct-builder-only.

### `/echo`, `/zecho`, `/wecho`

Formats:

```text
/echo <message>
/echo <room|zone|world> <message>
/echo <room|zone|world> -- <message>
/zecho <message>
/wecho <message>
```

Broadcasts text to players in a scope.

Scope behavior:

- `/echo <message>` defaults to room scope
- `/echo zone ...` sends to the current zone
- `/echo world ...` sends to the current world
- `/zecho` is a zone alias
- `/wecho` is a world alias

Examples:

```text
/echo A torch sputters.
/echo zone The bells ring in the distance.
/wecho The world trembles.
/cmd room -- /echo -- The altar hums.
```

### `/send`, `/sendexcept`

Formats:

```text
/send <player> <message>
/send <player> -- <message>
/sendexcept <player> <message>
/sendexcept <player> -- <message>
```

`/send` sends private text to one connected player in the issuer's runtime
world. `/sendexcept` sends text to every other connected player in that
target's current room, excluding the target. The room audience is constrained
to the same runtime world, including when parallel instances share the same
authored room.

Targets can be `player.<id>`, exact player names, or unambiguous name prefixes.
The target must be connected; `/sendexcept` additionally requires the target
to be in a room.

Use `/send` when only one player should receive the text. Use `/echo` when the
same message should be visible to a whole room, zone, or world. Pair `/send`
and `/sendexcept` when the acting player needs second-person text while
witnesses need separate third-person narration.

Examples:

```text
/send aria The altar hums beneath your hand.
/send player.123 -- You hear distant surf.
/sendexcept aria -- Aria studies the inscription.
/cmd room -- /send {{ actor_key }} -- You feel watched.
/cmd room -- /sendexcept {{ actor_key }} -- {{ actor }} studies the inscription.
```

Player-trigger scripts cannot run `/send` or `/sendexcept` directly. Use an
explicit ambient actor when a legacy `script` Trigger needs either operation:

```text
/cmd room -- /send {{ actor_key }} -- You pull the lever.
/cmd room -- /sendexcept {{ actor_key }} -- {{ actor }} pulls the lever.
```

Typed Trigger steps should use the native `send` and `send_except` actions
instead of wrapping these commands in a `command` action.

### `/state`

Formats:

```text
/state show <world|zone|room>
/state get <world|zone|room> <key>
/state set <world|zone|room> <key> <value>
/state set <world|zone|room> <key> -- <value with spaces>
/state add <world|zone|room> <key> [amount]
/state clear <world|zone|room> <key>

/state show character <target>
/state get character <target> <key>
/state set character <target> <key> <value>
/state set character <target> <key> -- <value with spaces>
/state add character <target> <key> [amount]
/state clear character <target> <key>
```

Reads or mutates scoped runtime state.

Room, zone, and world issuers should use scopes they actually own. A room issuer
can mutate room, zone, or world state. A zone issuer can mutate zone or world
state. A world issuer can mutate world state.

For `character` state, always provide the target. Use `self` for the issuing
player, or a player or mob name/key such as `joe`, `player.123`, or `mob.456`.
Room-issued trigger scripts should use `{{ actor_key }}` for the triggering
character.

Examples:

```text
/state show room
/state get world weather
/state set room lever_pulled true
/state set world weather -- stormy
/state add character self favor 1
/state set character joe pull_lever true
/state set character mob.456 captive false
/state clear room lever_pulled
/cmd room -- /state set character {{ actor_key }} pull_lever true
```

World, zone, and room commands always address state in the exact current
runtime world. Inside an instance they address only that run; they do not
mutate the instance template, the base world's live state, or another run.
Player state follows the player between worlds. Mob state belongs to the
spawned mob and is removed with it.

For state authoring guidance, see
[state-builder-guide.md](state-builder-guide.md).

### `/stats`

Format:

```text
/stats
/stats <target>
/stats <player.key|mob.key>
```

Shows a builder stat readout for the builder, a player or mob in the builder's
current room, or a keyed character anywhere in the current runtime world.
Player readouts include the equipped main-hand Weapon Damage, or `0` when
unarmed.

Room targets use the same builder room-character selector behavior as commands
such as `/grantitem`, `/setlevel`, and `/setclass`. Keyed targets can be
outside the builder's current room.

Examples:

```text
/stats
/stats guard
/stats aria
/stats mob.123
/stats player.456
```

### `/regen`

Formats:

```text
/regen
/regen <target>
/regen <target> <health|energy|stamina>
```

Restores resources to full. With no target, `/regen` restores the issuer's
health, energy, and stamina. With a target, it restores that player or mob. With
a resource name, it restores only that specific resource.

Targets use the same builder room-character selector behavior as `/stats` and
`/set`. Builders can target themselves, players, or mobs. Mob actors can also
run `/regen`; this is useful for scripted healers, traps, or encounter logic
that should restore the mob itself or a nearby character without granting the
command to regular player characters.

Examples:

```text
/regen
/regen guard
/regen aria energy
/regen self health
/cmd healer -- /regen self health
/cmd healer -- /regen {{ actor_key }} health
```

### `/open`, `/close`, `/lock`, `/unlock`

Formats:

```text
/open <direction|door name> [direction] [-- <room message>]
/close <direction|door name> [direction] [-- <room message>]
/lock <direction|door name> [direction] [-- <room message>]
/unlock <direction|door name>
```

These commands immediately force a door into an explicit state. They are the
builder and automation counterparts of the ordinary player commands:

| Command | Result |
| --- | --- |
| `/open` | Force the door open, bypassing its key. |
| `/close` | Force an open door closed. An already locked door remains locked. |
| `/lock` | Force the door closed and locked. |
| `/unlock` | Force the door closed and unlocked. |

Targets can be a direction or a case-insensitive door name. When a name is
ambiguous, include its direction, such as `/open iron gate north`. A missing or
ambiguous target is rejected rather than selecting a door implicitly.

`/open`, `/close`, and `/lock` accept an optional custom notification for
occupants on both sides of the doorway. With a direction target, the message
can follow the direction directly:

```text
/lock south The bronze doors close behind you. Nobody touches them.
```

Use `--` to separate the message from a multi-word door name or a name plus
direction:

```text
/open iron gate north -- The iron gates grind open.
```

The custom text replaces the normal `The <door> opens/closes...` notification;
it does not add a second echo. It is published only when the door actually
changes state, so an idempotent no-op remains silent to room occupants.

Direct use requires a builder player with access to the current world. Trusted
mob and room scripts can also use these commands, but player-backed scripts
cannot. Mob commands affect a door in the mob's current room; room commands
affect a door belonging to that room. Zone and world issuers are intentionally
unsupported because they have no unambiguous local doorway.

Examples:

```text
/open north
/lock iron gate
```

Direct `/cmd room -- ...` and `/cmd <mob> -- ...` do not inherit a builder
player's authority. Use the slash command directly when acting as a builder.
The room/mob forms are available only inside trusted Trigger scripts, where
`script_source` provenance is preserved:

```yaml
script: /cmd room -- /close north
script: /cmd gatekeeper -- /lock east
```

The three commands are also audited transactional Trigger-step commands for
`trigger_room` and selected-mob subjects. For example:

```yaml
- actions:
  - type: command
    subject: trigger_room
    command: /lock south The bronze doors close behind you. Nobody touches them.
  after_seconds: 1
```

Slash door commands are immediate: they bypass keys and the player's
2.5-second close or close-and-lock wind-up. Every actual state transition is
applied to both faces of the logical doorway in the current runtime world and
notifies occupants on both sides.

The commands are idempotent for reliable scripts. Asking for the state the door
already has succeeds with `changed: false`, does not publish a
`door.state_changed` event, and does not run state-change reactions. A real
transition publishes one `door.state_changed` event containing the previous
and resulting states, the logical doorway, issuer provenance, cause, and compact
state deltas for both faces.

### `/set`

Format:

```text
/set <target> <field> <value>
/set <target> <field> -- <value>
```

Sets a persisted stat field on a player or mob. Direct builders can target
players or mobs in their current room, or keyed characters anywhere in the
current runtime world.

A trusted room script can also use `/set`. Room issuers must specify one
unambiguous player or mob in that room; `self` and targets elsewhere in the
runtime world are rejected. Wrap Trigger usage in `/cmd room --` so the room,
not the triggering player, is the issuer. Player-backed scripts cannot invoke
`/set` directly.

For player targets, direct combat ratings such as `attack_power`, `armor`,
`crit`, `dodge`, and resource maximums are computed by the world stat system.
Set player `attributes.<key>`, equipment, level, or current resources instead.

Supported player fields:

```text
level, experience, health, energy, stamina, attributes, glory
```

Supported mob fields:

```text
name, room_description, description, attackable, level, experience, health,
energy, stamina, attributes, aggression, exp_worth, health_max, health_regen,
energy_max, energy_regen, stamina_max, stamina_regen, armor, dodge, crit,
resilience, attack_power, ability_power
```

Use `attribute.<key>`, `attributes.<key>`, or `attr.<key>` to change one
attribute value. Use `attributes -- {...}` to replace the whole attribute
object. Use the `--` form for multiword mob names and descriptions so the full
text is treated as one value.

Examples:

```text
/set guard health 25
/set guard attack_power 8
/set aria health 10
/set player.456 attribute.strength 5
/set mob.123 attributes -- {"strength": 4}
/cmd room -- /set guard aggression normal
/cmd room -- /set guard name -- the awakened guard
/cmd room -- /set guard room_description -- The awakened guard watches the archway.
/cmd room -- /set guard description -- Old scars cross the guard's weathered face.
/cmd room -- /set guard attackable true
```

Room-issued `/set` changes the selected runtime character row. It does not edit
the mob definition, so a fresh spawn retains the definition's authored values;
a later definition resync can also replace the runtime override. Changes to
`name`, `room_description`, and `description` appear the next time a player
looks at the room or mob. Changing a mob's `name` does not rewrite its keywords,
so later commands can continue to use a stable authored keyword. Changing a
description field to an empty value with a trailing `--` clears the runtime
override, so display falls back to the definition's authored text or generated
room text. A mob's `name` cannot be blank. `attackable` accepts `true` or
`false`. Changing `aggression` does not itself start combat. The new setting is
used the next time normal aggression evaluation runs.

### `/setcurrency`

Formats:

```text
/setcurrency <currency_code> <amount>
/setcurrency <target> <currency_code> <amount>
```

Sets one player's exact balance in an authored currency. Omitting the target
sets the builder's own balance. A bare target name resolves one unambiguous
player in the builder's current room; a `player.<id>` key can select a player
elsewhere in the same runtime world. Mob targets and players in parallel
runtime worlds are rejected.

The amount is the desired final balance, not an award or deduction. It must be
a whole number from `0` through `9,007,199,254,740,991`. Setting one currency
does not change any other currency. Setting the current amount again succeeds
as a no-op without changing the wallet revision, while an actual change emits
the target player's normal private wallet update.

Examples:

```text
/setcurrency obol 100
/setcurrency aria obol 25
/setcurrency player.123 guild-mark 0
```

This command is direct-builder-only and exists for testing and administrative
correction. Triggers and other scripts cannot invoke it. Use authored quest
`grant_currency`, mob rewards, merchant transactions, or typed Trigger
`grant_currency`/`debit_currency` actions for gameplay economy changes. The
quest effect and Trigger action share explicit amount-plus-currency semantics,
but the Trigger form also requires `actor: trigger_actor`.

### `/setlevel`

Formats:

```text
/setlevel <level>
/setlevel <target> <level>
```

Sets the builder player's level, or a player or mob target in the builder
player's current room.

Examples:

```text
/setlevel 5
/setlevel aria 3
/setlevel guard 8
/setlevel mob.123 2
```

This command is direct-builder-only.

### `/setclass`

Formats:

```text
/setclass <class>
/setclass <target> <class>
```

Sets a player class and refreshes that player's resources from the new computed
stats. Known abilities, ability hotkeys, and ability cooldowns are cleared.

Direct builder-player behavior:

- `/setclass <class>` changes the builder player's class
- `/setclass <target> <class>` changes a player target in the current room

Room-script behavior:

- room issuers must specify a player target
- use `{{ actor_key }}` to refer to the player who triggered a room command

Examples:

```text
/setclass hoplite
/setclass aria tidecaller
/cmd room -- /setclass {{ actor_key }} tidecaller
```

### `/cmd`, `/force`, `/rcmd`, `/zcmd`, `/wcmd`

Formats:

```text
/cmd <room|zone|world|target> -- <command>
/force <target> -- <command>
/force <target> <command>
/rcmd -- <command>
/zcmd -- <command>
/wcmd -- <command>
```

Runs a nested command through a selected ambient scope or character subject.

Scope targets:

- `/cmd room -- ...`: run the nested command as the current room
- `/cmd zone -- ...`: run the nested command as the current zone
- `/cmd world -- ...`: run the nested command as the current world
- `/rcmd`, `/zcmd`, and `/wcmd` are scope aliases

Character targets:

- `/cmd guard -- say Halt!`: run the nested command as the matching mob
- `/force guard -- say Halt!`: same target-driven behavior
- `/force aria west`: make a player in the builder's room issue one ordinary
  movement direction

Interactive builders may target either a mob or player in their current live
runtime room. Use `mob:<selector>` or `player:<selector>` when a name is
ambiguous. Mob targets may issue any otherwise supported command. Player
targets are limited to the six ordinary movement directions; forced player
input does not expand that player's aliases or enter their command history.
Movement still observes the usual exits, doors, stamina, combat restrictions,
movement-policy Triggers, follower behavior, and arrival lifecycle.

The builder remains the authenticated command issuer while the selected
character is the command subject. This interactive authority is separate from
Trigger command actions, where only the original player `trigger_actor` may
issue a bare movement direction.

Examples:

```text
/cmd room -- /echo -- The torch sputters.
/cmd room -- /grantitem {{ actor_key }} starter-blade
/cmd mob:guard -- say Halt!
/force guard -- emote salutes.
/force player:aria west
/zcmd -- /echo -- The zone grows quiet.
```

Use `&&` to chain nested commands on one line:

```text
/cmd room -- /state set room lever_pulled true && /echo -- The lever clicks.
```

Typed Trigger `command` actions do not allow nested `/cmd` or command chains.
They select `trigger_room`, `trigger_actor`, or one exact mob directly. For the
WR1 `/at` pattern, select the mob in a portable `room@<relative_id>` and give
that subject one command; a bare direction can bring the selected mob through
an adjacent exit. See [Running Commands From Steps](trigger-builder-guide.md#running-commands-from-steps).

### `/edit`

Format:

```text
/edit [database_id|room.database_id|room@relative_id]
```

Opens the selected room's canonical builder page in a new browser tab. With no
argument, `/edit` selects the room the builder currently occupies. The opened
URL always uses the authored world's database id and the room's stable relative
id:

```text
/build/worlds/23/rooms/42
```

The accepted selectors have deliberately distinct namespaces:

- a bare positive number is an installation-local room **database id**
- `room.<database_id>` is the explicit form of that same database identity
- `room@<relative_id>` is the room's stable, world-relative identity

Both database-id forms are resolved only inside the builder's current authored
world and then canonicalized to the relative-id URL. There is no namespace
fallback: if `/edit 187` cannot find database room 187 in that authored world,
the command fails even if `room@187` exists. Use `/edit room@187` to select that
relative ref. This differs intentionally from `/jump`, where a bare positive
number is a relative id.

Examples, assuming database room 187 has the stable ref `room@42` in authored
world 23:

```text
/edit
/edit 187
/edit room.187
/edit room@42
```

Each applicable form opens `/build/worlds/23/rooms/42` in a new tab. When the
builder is playing inside an instance run, `/edit` opens the corresponding room
in the authored instance-template world. It never points the editor at the
disposable runtime copy.

This command is direct-builder-only.

### `/jump`

Format:

```text
/jump <relative_id|room@relative_id|direction>
```

Moves the builder player to another room in the current world. The room can be
selected by a bare world-relative id, stable `room@<relative_id>` ref, or a
connected direction from the current room. A bare positive number is shorthand
for the same world-relative identity as `room@<relative_id>`. Database
`room.<id>` keys and legacy `room@x,y,z` coordinates are import aliases, not
gameplay command inputs. All room refs resolve only within the current authored
world. A jump that changes rooms runs the destination's room-scoped
`event: enter` triggers with
`event.source: jump`. A directional jump supplies `event.direction`; an
relative-id/ref jump leaves it empty. Jumps bypass normal movement policies and do
not run the movement-only `after_move_enter` compatibility hook. Jumping to the
current room emits no arrival.

Examples:

```text
/jump 17
/jump room@17
/jump north
/jump n
```

This command is direct-builder-only.

### `/transfer`

Format:

```text
/transfer <target> <room@relative_id|relative_id|direction|here>
```

Instantly moves a player or mob without using ordinary movement. Transfer does
not spend stamina or traverse doors, and it does not run movement policy
triggers. A direction selector reads the executing actor or subject's room
topology. Moving a character also finishes that character's active combat
encounters before the room changes, except for the typed Trigger-step PvP
restriction described below.

Target behavior:

- `player.<id>` selects a player in the current live runtime world
- an exact active player name can select a player elsewhere in that same
  runtime world; prefixes never select remote players
- `mob.<id>` selects a mob in the executing actor or subject's current room
- an untyped keyword or ordinal such as `guard` or `2.guard` uses normal local
  character order (players first, then mobs) in that room
- `self` selects an embodied player or mob execution subject

Player targets must currently be in game. This prevents scripts from producing
ghost room notifications or starting combat for disconnected characters.

Transfers never move a character between parallel runtime worlds or instance
runs, and they do not replace the instance enter/leave workflow.

Destination behavior:

- `room@<relative_id>` is the portable, move-stable form and should be used in
  Trigger YAML
- a bare positive numeric selector is interactive shorthand for the same
  world-relative room id
- a direction such as `north` or `n` uses the executing actor or subject's room
  exit
- `here` means that actor or subject's current room

Database `room.<id>` keys and legacy `room@x,y,z` coordinates are import aliases,
not gameplay command inputs. Persisted Trigger-step `command` actions must use
canonical `room@<relative_id>` for an absolute destination; bare numeric
shorthand is accepted only for direct interactive use. Directions and `here`
remain valid in Trigger steps.

Examples:

```text
/transfer player.123 room@42
/transfer aria 17
/transfer guard north
/cmd room -- /transfer {{ actor_key }} room@42
```

Direct use requires a builder player. Mob and room issuers require a trusted
script context. Player-issued scripts cannot use `/transfer`; room triggers
should dispatch it through `/cmd room` and pass the triggering character with
`{{ actor_key }}`. Transfer sends a complete `affect.transfer` room snapshot to
player targets that actually change rooms and refreshes combat-effect state.
After a moved player arrives, destination mob-definition `enter` reactions run,
followed by room-scoped `event: enter` triggers; a player still there after
those reactions also runs hostile-mob aggro. A transferred mob retains its
existing mob-reaction behavior but does not run the player-only room hook. A
same-room player transfer returns a normal look snapshot, and no same-room
transfer runs arrival work.

Typed Trigger `command` actions use a narrower audited contract than ordinary
player scripts. They may transfer only the Trigger actor, but any supported
step subject can issue the command. These are the two canonical forms:

```yaml
- type: command
  subject: trigger_room
  command: /transfer {{ actor_key }} room@42

- type: command
  subject: trigger_actor
  command: /transfer self room@42
```

An exact-one selected mob can also transfer the Trigger actor by naming that
actor explicitly:

```yaml
- type: command
  subject:
    type: mob
    room: trigger_room
    mob: mobdefinition.charon
  command: /transfer {{ actor_key }} room@42
```

`self` and `me` are accepted only when the resolved command subject is the
Trigger actor. That is normally `subject: trigger_actor`; a selected-mob
subject can also qualify when that exact mob is itself the Mob Trigger actor.
Other room or selected-mob subjects must use the rendered actor key and cannot
transfer themselves or another character. Relative `here` and direction
destinations use the subject's room. Thus `trigger_room` uses the original
Trigger room, `trigger_actor` uses the actor's current room, and a selected mob
uses that mob's room.

Do not wrap a typed step command in `/cmd`. Always use
`room@<relative_id>` in authored Trigger YAML so export/import and later room
moves preserve the destination. The room change and
all transfer output participate in the step transaction and roll back if a
later action fails. A typed transfer that would move a player in active PvP
fails with `target_busy` and rolls back the whole step; ordinary active
encounters are finished when the move succeeds.

For an actual player move, the durable transfer lifecycle event starts
destination mob-definition `enter` reactions and room-scoped `event: enter`
triggers after the step commits; a moved player still in that destination
afterward then runs aggro. Their output is captured and durably enqueued as one
bounded follow-up batch outside the original step locks. Within one event
batch, only the player's final current arrival runs this work. A later location
change invalidates an earlier pending arrival; every delivery rechecks the
player's in-game state, runtime world, room, and location sequence. Inherited
scripted reactions remain subject to the eight-layer depth limit. A same-room
transfer has no arrival lifecycle event.

The same room-scoped `event: enter` contract covers ordinary movement,
adjacent-room charge, flee, `/transfer`, death, jump, connected character reset
to a different room or runtime world, instance entry/leave, and instance reset.
`after_move_enter` remains movement-only compatibility behavior, and
`after_death_room_enter` remains death-only compatibility behavior. The
world-scoped `mobdefinition` form of `event: enter` is a separate mob reaction.
Login, reconnect, and offline location repair do not emit an arrival.

WR2 does not accept WR1's optional trailing command on `/transfer`. Put custom
feedback before the transfer as explicit script commands. For immediate ordered
room behavior, repeat the room wrapper for each chained segment:

```yaml
script: /cmd room -- /send {{ actor_key }} -- The wall folds around you. && /cmd room -- /transfer {{ actor_key }} room@42
```

This explicit form still emits transfer's standard disappearance notification.
In typed steps, use a native `send` action followed by the audited transfer
command:

```yaml
actions:
  - type: send
    actor: trigger_actor
    text: The wall folds around you.
  - type: command
    subject: trigger_room
    command: /transfer {{ actor_key }} room@42
```

After an initial item/mob mutation prefix, `command`, `debit_currency`,
`grant_currency`, `echo`, `send`, and `send_except` may interleave in authored
narrative order. A nonzero aggregate currency change emits its authoritative
wallet state event after all authored action events; an exact net-zero batch
emits its narratives but no wallet revision/state event. Exporters that relied
on WR1's trailing command to suppress that text should flag the script for an
authoring review.

### `/exitinstance`

Format:

```text
/exitinstance <player-target> world@base/room@<relative_id>
```

Exits a connected player from their active instance run and places them in one
specific authored room in that run's base world. Use this command when the
interaction itself chooses the destination—for example, when one portal leads
to Athens and another leads to Sparta. Ordinary `leave` still returns to the
remembered entrance.

The `world@base/` qualifier is required. A plain `room@42` inside an instance
means room 42 in that instance, while `world@base/room@42` means room 42 in the
active run's direct base world. This qualified token is accepted only by
`/exitinstance`; it does not make `/transfer` or ordinary manifest room fields
cross-world. Use a positive relative id in the exact
`world@base/room@<relative_id>` shape. Scoped database and coordinate aliases
are rejected. The destination must exist in the target player's active run's
base world.

WR2 returns the player to the exact base runtime recorded when that participant
entered the run. It never searches for or creates a different base runtime.
The command rejects a player who is outside an instance, has no valid recorded
return runtime, or is an active duel contestant. `/transfer` remains the
correct command for movement inside one runtime world and never crosses
between an instance and its base runtime.

On success, WR2 finishes the player's ordinary non-duel combat, cancels pending
door work, moves the entire carried/equipped item tree and character effects to
the return runtime, and records the participant exit reason as
`forced`. Non-active duel participation is marked exited, the run activity is
updated, and the normal `instance.left` plus room-arrival lifecycle is emitted
with `event.source: instance_leave`. The exited player receives a full state
sync for the destination runtime and room.

Examples:

```text
/exitinstance self world@base/room@17
/exitinstance player.123 world@base/room@42
/cmd room -- /exitinstance {{ actor_key }} world@base/room@42
```

Direct use requires a builder player. Mob and room issuers require a trusted
script context. Player, zone, and world scripts cannot issue the command.
`self` and `me` select a player issuer. `player.<id>` or one exact active player
name selects a player in the issuer's current instance runtime. An immediate
trusted room or mob script is further restricted to a target in that issuer's
room; it cannot reach across the instance by key. Mob and room issuers cannot
target themselves.

Typed Trigger steps are narrower still: the player target must be the original
`trigger_actor`, and `/exitinstance` must be the only action in the final step.
Any supported step subject may issue it. A room or selected mob names the actor
explicitly; an embodied player Trigger actor may use `self`:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: command
        subject: trigger_room
        command: /exitinstance {{ actor_key }} world@base/room@42
```

Or, as the player Trigger actor:

```yaml
steps:
  - after_seconds: 0
    actions:
      - type: command
        subject: trigger_actor
        command: /exitinstance self world@base/room@42
```

The final-only rule prevents later actions from running with the Trigger's
obsolete instance-runtime context. Put narration, charges, grants, or other
effects in earlier steps. The exit and its output remain transactional; a
validation failure rolls back the final step.

### `/repop`

Format:

```text
/repop [--doors]
```

Immediately reconciles every active spawn plan in the current zone. Direct
builder use selects the zone containing the builder. Trusted room-script use
selects the room's zone:

```yaml
script: /cmd room -- /repop
```

The command ignores each plan's ordinary respawn deadline and also refills
missing placements from plans configured with `respawn.mode: none`. It still
checks the generated population: a mob or item that remains live continues to
satisfy its placement and is not duplicated, killed, moved, or rerolled.
Inactive plans, plan and entry conditions, no-roam placement safety, and active
instance snapshot safeguards continue to apply.

Doors are unchanged by default. Add `--doors` to also reset materialized
runtime doorway states in the selected zone to their authored defaults,
regardless of its typed `spec.door_reset` policy:

```text
/repop --doors
```

The equivalent trusted room-script form is:

```yaml
script: /cmd room -- /repop --doors
```

Only the issuer's exact runtime world is affected. Inside an instance, `/repop`
refills missing placements in that run's current zone; it does not change the
template or another active run. Even with `--doors`, it does not clear combat,
reset scoped state, consume or advance the zone's door-reset schedule, or
rebuild the instance. Doorways already at their authored default remain sparse
rather than gaining unnecessary runtime state. Use `/reset` when the entire
current instance run should be rebuilt.

This command is intended for builder testing and deliberate room interactions,
not as a replacement for ordinary spawn-plan scheduling. High-frequency room
triggers should not run it because work scales with the active plans and
placements in the zone.

### `/reset`

Format:

```text
/reset
```

Resets the current instance run to its initial spawned state. The command is
only available to builder characters and only works while the builder is inside
an instance.

Reset keeps the same active run and Instance ID, moves active participants to
the instance starting room, clears spawned mobs, ground items, combat, and door
overrides, then reseeds world, zone, and room state from the instance
template's `initial_state` and reruns the initial spawn plans. Player inventory,
equipment, and character state are preserved.

The reset changes only the current runtime world. Other active runs of the same
instance template keep their own world, zone, and room state.

Examples:

```text
/reset
```

This command is direct-builder-only.

## Common Script Patterns

Grant starter gear to the triggering player:

```yaml
script: /cmd room -- /grantitem {{ actor_key }} starter-blade
```

Drop a key on the room floor:

```yaml
script: /cmd room -- /load item temple-key
```

Spawn a mob in the room:

```yaml
script: /cmd room -- /load mob road-bandit
```

Set a room state flag and echo feedback:

```yaml
script: |
  /cmd room -- /state set room lever_pulled true
  /cmd room -- /echo -- The lever clicks.
```

Set character state on the triggering player:

```yaml
script: /cmd room -- /state set character {{ actor_key }} pull_lever true
```

Give the triggering player private second-person text and everyone else in the
player's room third-person text:

```yaml
script: |
  /cmd room -- /send {{ actor_key }} -- You pull the lever.
  /cmd room -- /sendexcept {{ actor_key }} -- {{ actor }} pulls the lever.
```

Change the triggering player's class:

```yaml
script: /cmd room -- /setclass {{ actor_key }} tidecaller
```

Kill the triggering player in a room trap:

```yaml
script: /cmd room -- /kill {{ actor_key }} -- The pit swallows you whole.
```

Transfer the triggering player to another room:

```yaml
script: /cmd room -- /transfer {{ actor_key }} room@42
```

Have a scripted mob restore health:

```yaml
script: /cmd healer -- /regen {{ actor_key }} health
```

Close and lock a gate from a trusted room script:

```yaml
script: /cmd room -- /lock north
```

## Related Docs

- [trigger-builder-guide.md](trigger-builder-guide.md)
- [state-builder-guide.md](state-builder-guide.md)
- [item-definition-builder-guide.md](item-definition-builder-guide.md)
- [mob-definition-builder-guide.md](mob-definition-builder-guide.md)
- [Ambient command issuer architecture](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/ambient-command-issuers-plan.md)
