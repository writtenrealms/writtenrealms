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
- `No`: the actor cannot run the command.

| Command | Builder Player | Player Script | Mob Script | Room Script | Zone Script | World Script |
| --- | --- | --- | --- | --- | --- | --- |
| `/load` | Direct | Script | Script | Script | No | No |
| `/grantitem` | Direct | Script | Script | Script | No | No |
| `/purge` | Direct | No | No | No | No | No |
| `/echo`, `/zecho`, `/wecho` | Direct | Script | Script | Script | Script | Script |
| `/state` | Direct | Script | Script | Script | Script | Script |
| `/setlevel` | Direct | No | No | No | No | No |
| `/setclass` | Direct | Script | No | Script | No | No |
| `/cmd`, `/force`, `/rcmd`, `/zcmd`, `/wcmd` | Direct | Script | Script | Script | Script | Script |
| `/jump` | Direct | No | No | No | No | No |
| `/resync` | Direct | No | No | No | No | No |

## Command Details

### `/load`

Format:

```text
/load <item|mob> <template_id|definition_id|slug> [cmd]
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

Selectors can reference legacy templates or WR2 definitions by numeric id or
slug. For new content, prefer item definitions and mob definitions.

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
/grantitem <target> <item_template_id|item_definition_id|item_slug>
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
/cmd room -- /grantitem {{ actor_key }} tidecaller-starter-trident
```

Use `/grantitem` for pledge rewards, starter equipment, quest rewards, and any
scripted reward that should not appear on the room floor.

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

### `/state`

Formats:

```text
/state show <world|zone|room|character>
/state get <world|zone|room|character> [--target <target>] <key>
/state set <world|zone|room|character> [--target <target>] <key> <value>
/state set <world|zone|room|character> [--target <target>] <key> -- <value with spaces>
/state add <world|zone|room|character> [--target <target>] <key> [amount]
/state clear <world|zone|room|character> [--target <target>] <key>
```

Reads or mutates scoped runtime state.

Room, zone, and world issuers should use scopes they actually own. A room issuer
can mutate room, zone, or world state. A zone issuer can mutate zone or world
state. A world issuer can mutate world state.

For `character` state, use `--target <player>` when the issuer is not the
player whose state should change. Targeted character state currently resolves
players in the issuer's current room.

Examples:

```text
/state show room
/state get world weather
/state set room lever_pulled true
/state set world weather -- stormy
/state add character favor 1
/state set character --target joe pull_lever true
/state clear room lever_pulled
/cmd room -- /state set character --target {{ actor_key }} pull_lever true
```

For state authoring guidance, see
[state-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/state-builder-guide.md).

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

### `/resync`

Formats:

```text
/resync item <template_id|all>
/resync mob <template_id|all>
```

Reapplies legacy template fields to spawned item or mob instances in the
builder player's current world.

Examples:

```text
/resync item 509
/resync item all
/resync mob 456
/resync mob all
```

This command is direct-builder-only. WR2 item definition and mob definition
sync paths are separate from this legacy template resync command.

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
script: /cmd room -- /state set character --target {{ actor_key }} pull_lever true
```

Change the triggering player's class:

```yaml
script: /cmd room -- /setclass {{ actor_key }} tidecaller
```

## Related Docs

- [trigger-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/trigger-builder-guide.md)
- [state-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/state-builder-guide.md)
- [item-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/item-definition-builder-guide.md)
- [mob-definition-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/mob-definition-builder-guide.md)
- [ambient-command-issuers-plan.md](/Users/teebes/code/writtenrealms/docs/architecture/ambient-command-issuers-plan.md)
