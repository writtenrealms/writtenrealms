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

Trigger steps cannot currently force movement, combat, inventory changes, or
other mutating player commands through this generic command path. Builders use
explicit typed actions for state changes that need transactional guarantees.
