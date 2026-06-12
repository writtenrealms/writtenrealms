# Combat

## Encounter Order

When combat starts, the encounter rolls a combat order and keeps that order for
the rest of the encounter. The order does not randomly change from round to
round.

This means a fight may start with you acting before the mob, or the mob acting
before you. Once the order is set, you can plan around it until the encounter
ends.

Opener abilities, such as Charge, can override the first combat action only.
After that opening action, the encounter returns to its stored combat order.

## Charge

Charge can only be used while you are out of combat.

```text
charge rabbit
charge rabbit east
charge east rabbit
```

Without a direction, Charge targets a mob in your current room. With one
direction, Charge first moves you through that exit, using the normal movement
rules, then attacks the named mob in the destination room.

Charge starts combat immediately. Its opening attack gets first-action priority
for the first round only; later rounds use the encounter order that was rolled
when combat started.

## Rounds

Combat resolves in encounter rounds. If you have queued an ability, that ability
uses your primary action for the round. If you have not queued an ability, you
use your normal auto-attack when your turn in the encounter order comes up.

Some effects can change what happens on a turn. For example, stun can prevent a
combatant from taking their primary action.

## Leaving Combat

Combat movement and flee behavior is still evolving. The current design goal is
that ordinary hostile contact starts a fight without granting either side a free
opener, while explicit opener actions such as charge commit you to combat and
grant their first-action priority.
