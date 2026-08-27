# Scripted World Interactions

World builders can attach timed Trigger sequences to room commands and events.
Some steps may execute a communication command as your character, such as
having you say an authored oath after accepting a ferry crossing.

When you enter a command, its dim console echo carries one compact status:

- `…` means the command or a delayed Trigger sequence is not resolved yet
- `✓` means the server finished processing the command and reached an
  authoritative outcome. That outcome can be a completed action or an
  expected authored refusal such as “you do not have enough obols”; the mark
  does not by itself mean the requested world action occurred.
- a red `×` means delivery could not be confirmed or the server could not
  finish processing the command

The pending and acknowledgement marks stay non-interactive. If a red `×`
appears, hover over it on desktop, focus it from the keyboard, or tap it on
mobile to see the safe failure detail.

An expected Trigger refusal caused by its authored conditions or gate is
acknowledged with `✓`, not treated as a client or server error. Its refusal
message still appears in the transcript so you know why nothing happened.

The client never automatically resends an unconfirmed command. This matters
for commands that can award or spend currency or make another lasting change:
loss of the acknowledgement does not prove that the original command failed.
Transport and lifecycle statuses are interface feedback, not authored room
prose, and other characters do not see them.

When this happens:

- the output is attributed to your character just like an ordinary `say`,
  `emote`, `talk`, or social command
- ordinary communication restrictions still apply; for example, a muted
  character cannot be forced to speak
- a delayed command uses your current room in the same runtime world
- the command is part of the step transaction, so a failed currency change or
  another failed same-step action prevents the speech or emote
- scripted speech is marked as authored behavior: it is visible, but does not
  advance a quest or cause another Trigger reaction as though you voluntarily
  entered the command

A builder can also give you private second-person narration such as “You pull
the lever” while every other connected player in your current room receives a
separate third-person line such as “Joe pulls the lever.” You do not receive
the witness copy. Both lines retain their authored order and participate in the
same step transaction as any payment, item change, or audited command.

A typed step may also award currency directly to the player who activated it.
Successful awards and charges use private second-person messages, while visible
room witnesses may receive third-person messages without seeing the player's
wallet. Same-step awards never subsidize charges: the wallet at the start of
the step must already cover every charge.

A delayed sequence can be accepted before its first visible world response.
Starting conditions establish eligibility at that moment; they do not reserve
items or currency for a later step. If the needed state changes before a
delayed action executes, that step rolls back, the remaining sequence is
cancelled, and the player receives safe cancellation feedback.

A Trigger step may also issue `follow <character>` or `unfollow` as the player
who activated it. The following change and every other same-step effect commit
or roll back together. This is the same [locomotion-only relationship](following.md)
used by the ordinary commands: it creates no party and shares no combat,
rewards, or loot. Ordinary movement through an adjacent directional exit pulls
a follower. A direction-based `/transfer` of a mob through an adjacent exit can
also pull that mob's followers; player transfers, other transfer forms,
instance transitions, death routing, and other non-directional relocations do
not.

A scripted scene can also have one specific mob walk through an adjacent exit
from another room. Occupants see the same directional departure and arrival
messages as ordinary mob movement, and a closed or locked door or active fight
can prevent the move. The mob's move and every other action in that step still
commit or roll back together, so a later failed payment or command cannot leave
the mob halfway into the scene.

A Trigger step may also use the audited `/transfer` command to move the
character who activated it. The transfer, any same-step currency change, and
same-step output succeed or fail together. If the step fails, your room and
balance stay unchanged. A transfer that would move you while you are in active
player-versus-player combat is rejected and rolls back the whole step.

After an actual move commits, the destination's normal mob reactions and
player-arrival room behavior run from the structural entry lifecycle event.
Hostile-mob aggro follows only if those reactions leave you in that destination.
Their output is queued durably after the original step. If several scripted
transfers occur in one batch, only your final current arrival runs this work;
any later location change invalidates an earlier pending arrival. A transfer to
the room you already occupy produces no arrival behavior or aggro.

Builders can attach the same room arrival behavior to ordinary movement,
adjacent-room charge, fleeing, `/transfer`, death routing, jumping, instance
entry or exit, a connected character reset that changes room or runtime world,
and instance reset. This makes a room greeting, trap, or scene transition
consistent regardless of how you arrived. Death and instance reset establish a
new location epoch even when their destination uses the same authored room, so
arrival behavior may run again for those lifecycle changes. Login, reconnect,
and offline location repair do not count as room arrivals.

A Trigger may make its original player actor issue one bare movement direction.
That movement observes the same exits, doors, stamina, combat, policy,
follower, and arrival behavior as player input. Arbitrary forced player
commands, combat, and inventory changes remain unavailable through the generic
step-command path. Builders use explicit typed actions for other state changes
that need transactional guarantees.
