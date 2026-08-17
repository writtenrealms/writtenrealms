# Following

Use `follow` to have your character travel behind another visible character in
your current room:

```text
follow hermes
```

You can follow one character at a time. Following someone else replaces your
current leader. Use either form below to stop:

```text
unfollow
unfollow hermes
```

A chain may contain at most 16 following links. The game rejects a `follow`
command that would create a longer chain, so nobody is silently left behind
because the chain exceeded the movement propagation limit.

Following applies to directional locomotion through an adjacent exit. When your
leader moves that way, the game attempts the same direction for you. Your move
still obeys every normal restriction, including combat locks, stamina costs,
movement policies, and closed or locked doors. Following never teleports you or
lets you bypass an exit; if your move is blocked, you stay where you are.
When a leader moves while invisible, a non-builder follower neither moves nor
receives a clue about the route. The relationship remains and can resume after
the characters are visible and together again.

A direction-based `/transfer` that moves a mob through an adjacent exit also
counts as directional movement and can pull that mob's followers. Player
transfers, mob transfers using `here` or a room reference, instance entry or
exit, death routing, resets, and similar non-directional location changes move
only their explicit targets.

Following is a locomotion relationship, not a group or party. It does not share
combat participation, rewards, currency, experience, loot, quest progress, or
any other party benefit.
