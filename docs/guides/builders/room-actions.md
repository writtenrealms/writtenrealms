# Room Actions

Room actions are custom text commands attached to one room. They remain useful
for small interactions created through **Rooms > Actions**. For portable YAML,
event subscriptions, movement policies, or more complex scripting, use a
[room-scoped trigger](trigger-builder-guide.md) instead.

## Fields

| Field | Meaning |
| --- | --- |
| Name | Builder-facing label for the action. |
| Action | Command text a player types. Use `or` to provide alternate phrases. |
| Display Action in Room | Shows an eligible action after the room description. |
| Commands | Slash commands executed by the room, one per line. |
| Conditions | Optional legacy text conditions that must pass. |
| Show Failure Message | Reveals a failure reason instead of behaving like an unknown command. |
| Failure Message | Custom text shown when the conditions fail. |
| Action Cooldown | Debounce time that prevents immediate repeated execution. |

## Authoring Advice

- Keep action phrases specific enough that they do not compete with ordinary
  game commands or socials.
- Use [builder slash commands](builder-command-reference.md) in the command
  body and test them with an ordinary player character.
- Room-action conditions use an older text format. New state-aware interactions
  should use the structured [condition system](condition-builder-guide.md)
  through a trigger.
- Only enable **Display Action in Room** when showing the action helps the
  player. Hidden actions are useful for optional discoveries, but should still
  be discoverable through the room's writing or surrounding context.
- Use a cooldown appropriate to the side effect, especially for actions that
  grant items, currency, state, or messages to multiple players.
