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

| Command | Builder Player | Player Script | Mob Actor | Room Script | Zone Script | World Script |
| --- | --- | --- | --- | --- | --- | --- |
| `/load` | Direct | Script | Script | Script | No | No |
| `/grantitem` | Direct | Script | Script | Script | No | No |
| `/kill` | Direct | No | Script | Script | No | No |
| `/transfer` | Direct | No | Script | Script | No | No |
| `/purge` | Direct | No | No | No | No | No |
| `/echo`, `/zecho`, `/wecho` | Direct | Script | Script | Script | Script | Script |
| `/send` | Direct | No | Script | Script | Script | Script |
| `/state` | Direct | No | Script | Script | Script | Script |
| `/stats` | Direct | No | No | No | No | No |
| `/regen` | Direct | No | Mob | No | No | No |
| `/set` | Direct | No | No | Script | No | No |
| `/setlevel` | Direct | No | No | No | No | No |
| `/setclass` | Direct | Script | No | Script | No | No |
| `/cmd`, `/force`, `/rcmd`, `/zcmd`, `/wcmd` | Direct | Script | Script | Script | Script | Script |
| `/jump` | Direct | No | No | No | No | No |
| `/reset` | Direct | No | No | No | No | No |

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
  death_room: room@0,0,0
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

### `/send`

Formats:

```text
/send <player> <message>
/send <player> -- <message>
```

Sends private text to one connected player in the issuer's runtime world.
Targets can be `player.<id>`, exact player names, or unambiguous name prefixes.

Use `/send` when only one player should receive the text. Use `/echo` when the
message should be visible to a room, zone, or world.

Examples:

```text
/send aria The altar hums beneath your hand.
/send player.123 -- You hear distant surf.
/cmd room -- /send {{ actor_key }} -- You feel watched.
```

Player-trigger scripts cannot run `/send` directly. Use an explicit ambient
actor when a trigger should send private text:

```text
/cmd room -- /send {{ actor_key }} -- The inscription burns behind your eyes.
```

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

Runs a nested command as another issuer.

Scope targets:

- `/cmd room -- ...`: run the nested command as the current room
- `/cmd zone -- ...`: run the nested command as the current zone
- `/cmd world -- ...`: run the nested command as the current world
- `/rcmd`, `/zcmd`, and `/wcmd` are scope aliases

Mob target:

- `/cmd guard -- say Halt!`: run the nested command as the matching mob
- `/force guard -- say Halt!`: same target-driven behavior

Examples:

```text
/cmd room -- /echo -- The torch sputters.
/cmd room -- /grantitem {{ actor_key }} starter-blade
/cmd mob:guard -- say Halt!
/force guard -- emote salutes.
/zcmd -- /echo -- The zone grows quiet.
```

Use `&&` to chain nested commands on one line:

```text
/cmd room -- /state set room lever_pulled true && /echo -- The lever clicks.
```

### `/jump`

Format:

```text
/jump <room_id|direction>
```

Moves the builder player to another room in the current world. The room can be
selected by absolute room id, `room.<id>` style key, or a connected direction
from the current room. Directional jumps bypass normal movement policy triggers.

Examples:

```text
/jump 50201
/jump room.50201
/jump north
/jump n
```

This command is direct-builder-only.

### `/transfer`

Format:

```text
/transfer <target> <room_id|room@x,y,z|direction|here>
```

Instantly moves a player or mob without using ordinary movement. Transfer does
not spend stamina or traverse doors, and it does not run movement policy
triggers. A direction selector still reads the issuer room's exit topology.
Moving a character also finishes that character's active combat encounters
before the room changes.

Target behavior:

- `player.<id>` selects a player in the current live runtime world
- an exact active player name can select a player elsewhere in that same
  runtime world; prefixes never select remote players
- `mob.<id>` selects a mob in the issuer's current room
- an untyped keyword or ordinal such as `guard` or `2.guard` uses normal local
  character order (players first, then mobs) in the issuer's room
- `self` selects an embodied player or mob issuer

Player targets must currently be in game. This prevents scripts from producing
ghost room notifications or starting combat for disconnected characters.

Transfers never move a character between parallel runtime worlds or instance
runs, and they do not replace the instance enter/leave workflow.

Destination behavior:

- `room@x,y,z` is the portable form and should be used in trigger YAML
- a bare numeric selector is the WR1-compatible, world-relative room id
- `room.<id>` selects an explicit WR2 room database id for interactive testing
- a direction such as `north` or `n` uses the issuer room's exit
- `here` means the issuer's current room

Examples:

```text
/transfer player.123 room@10,4,0
/transfer aria 50201
/transfer guard north
/cmd room -- /transfer {{ actor_key }} room@10,4,0
```

Direct use requires a builder player. Mob and room issuers require a trusted
script context. Player-issued scripts cannot use `/transfer`; room triggers
should dispatch it through `/cmd room` and pass the triggering character with
`{{ actor_key }}`. Transfer sends a complete `affect.transfer` room snapshot to
player targets, refreshes combat-effect state, and runs destination mob
`entering` reactions in the same runtime world.

WR2 does not accept WR1's optional trailing command on `/transfer`. Put custom
feedback before the transfer as explicit script commands. For immediate ordered
room behavior, repeat the room wrapper for each chained segment:

```yaml
script: /cmd room -- /send {{ actor_key }} -- The wall folds around you. && /cmd room -- /transfer {{ actor_key }} room@10,4,0
```

This explicit form still emits transfer's standard disappearance notification.
Exporters that relied on WR1's trailing command to suppress that text should
flag the script for an authoring review.

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
script: /cmd room -- /transfer {{ actor_key }} room@10,4,0
```

Have a scripted mob restore health:

```yaml
script: /cmd healer -- /regen {{ actor_key }} health
```

## Related Docs

- [trigger-builder-guide.md](trigger-builder-guide.md)
- [state-builder-guide.md](state-builder-guide.md)
- [item-definition-builder-guide.md](item-definition-builder-guide.md)
- [mob-definition-builder-guide.md](mob-definition-builder-guide.md)
- [ambient-command-issuers-plan.md](/Users/teebes/code/writtenrealms/docs/architecture/ambient-command-issuers-plan.md)
