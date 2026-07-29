# Scripted World Interactions

World builders can attach timed Trigger sequences to room commands and events.
Some steps may execute a communication command as your character, such as
having you say an authored oath after accepting a ferry crossing.

When this happens:

- the output is attributed to your character just like an ordinary `say`,
  `emote`, `talk`, or social command
- ordinary communication restrictions still apply; for example, a muted
  character cannot be forced to speak
- a delayed command uses your current room in the same runtime world
- the command is part of the step transaction, so a failed payment or another
  failed same-step action prevents the speech or emote
- scripted speech is marked as authored behavior: it is visible, but does not
  advance a quest or cause another Trigger reaction as though you voluntarily
  entered the command

A Trigger step may also use the audited `/transfer` command to move the
character who activated it. The transfer, any same-step payment, and same-step
output succeed or fail together. If the step fails, your room and balance stay
unchanged. A transfer that would move you while you are in active
player-versus-player combat is rejected and rolls back the whole step.

After an actual move commits, the destination's normal `entering` reactions and
hostile-mob aggro run from the durable transfer lifecycle event. Aggro runs only
if the entering reactions leave you in that destination. Their output is queued
durably after the original step. If several scripted transfers occur in one
batch, only the final arrival runs this work. A later scripted player transfer
also invalidates an earlier pending arrival. A transfer to the room you already
occupy produces no arrival reactions or aggro.

Other movement, combat, inventory changes, and mutating player commands are not
available through the generic step-command path. Builders use explicit typed
actions for state changes that need transactional guarantees.
