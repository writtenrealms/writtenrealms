# Leveling Builder Guide

WR2 worlds define player progression in the world config YAML.

```yaml
kind: world
spec:
  starting_level: 1
  max_level: 5
  leveling_curve:
    - 0
    - 30
    - 100
    - 400
    - 1000
```

`leveling_curve` is a cumulative XP threshold list:

- entry 1 is level 1 and must be `0`
- entry 2 is the total XP required for level 2
- entry 3 is the total XP required for level 3
- each later entry must be greater than the previous entry

The short example above defines five reachable levels. A 20-level world needs
20 entries in `leveling_curve`.

`starting_level` is the level assigned when a new player is initialized or a
builder reset uses the world default. The player starts with the cumulative XP
threshold for that level.

`max_level` caps automatic level-ups and must be less than or equal to the
number of entries in `leveling_curve`.

## XP Rewards

Mob combat rewards use the mob template or spawned mob `exp_worth` value. When
the player crosses a threshold, combat updates the player's level and reward
message:

```text
You gain 50 experience.
You are now level 2!
```

Quest rewards that grant `xp`, `exp`, or `experience` use the same leveling
logic.

## Builder Testing

Use `/setlevel` in game as a builder:

```text
/setlevel 5
/setlevel joe 3
/setlevel guard 8
/setlevel mob.123 2
```

The first form targets your own player. The target form looks for a player or
mob in the current room. Player targets have their total XP moved to the
threshold for the requested level and their health, energy, and stamina restored
to the new level's maximums. Mob targets only have their level changed.

`/setlevel` rejects levels below 1 or above the world's `max_level`.
