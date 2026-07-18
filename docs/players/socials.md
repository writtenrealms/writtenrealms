# Socials

Socials are world-authored emote commands. Use `socials` to list the commands
available in your current world:

```text
socials
```

Use a social by typing its command. Add a local player or mob target when the
social supports one:

```text
wave
wave guard
```

The first form shows the social's standalone message. The second shows its
targeted messages to you, the target, and the other in-game players in the
room. A social may support only one of those forms. If it requires a target,
WR2 tells you so. If it has only a standalone form, adding a target still uses
that standalone form.

Targets must be visible in your room and cannot be your own character. Socials
use the first target selector after the command, so short names and numbered
selectors fit naturally. A player who has muted your character cannot be the
direct target of your social. A global communication mute also prevents using
socials.

## Short Commands

You can abbreviate a social by typing a prefix. Exact commands always win. If
more than one social begins with the prefix, the world's builder-defined
priority chooses one, with alphabetical order breaking a tie.

Socials are fallback commands. A built-in command, ability command, or
available contextual command trigger with the same text takes precedence.

## Mob Reactions

Builders can make a mob react when a player directly targets it with a
particular social. Only the targeted mob is eligible: a standalone social,
targeting another player, or merely standing near the mob does not trigger that
reaction.

Instance worlds use their base world's social catalog, so entering an instance
does not change the available social vocabulary.
