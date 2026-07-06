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
charge east
```

Without a direction, Charge targets a mob in your current room. With one
direction, Charge first moves you through that exit, using the normal movement
rules, then attacks the named mob in the destination room.

If you provide a direction without a target, Charge picks the first attackable
living mob in the destination room, matching the implicit targeting used by
bare `kill`.

Charge starts combat immediately. Its opening attack gets first-action priority
for the first round only; later rounds use the encounter order that was rolled
when combat started.

When Charge moves you into a room with multiple hostile mobs, the mob you
charged becomes your automatic faceoff target even if another mob has a higher
`target_priority`. Other hostile mobs in the room can still engage you.

## Rounds

Combat resolves in encounter rounds. Most queued abilities use your primary
action for the round, replacing your normal auto-attack. Some builder-authored
supplemental abilities can resolve without using that primary action, allowing
your normal auto-attack to happen in the same round. If you have not queued an
ability, you use your normal auto-attack when your turn in the encounter order
comes up.

Some effects can change what happens on a turn. For example, stun can prevent a
combatant from taking their primary action.

## Multiple Hostiles

Several hostile mobs in the same room can engage you at once. You still have one
automatic faceoff target for normal attacks. Builders can give mobs a
`target_priority`; higher-priority mobs become your automatic target first, and
the next hostile takes over after that target dies. Unset priority is `0`, so
positive values stand ahead of default mobs and negative values stand behind
them. Explicit opener abilities such as Charge can temporarily override that
priority by making the chosen opener target your current faceoff target.
Some supplemental abilities can also strike a secondary active hostile in the
same room while your normal primary attack continues against the faceoff target.

## Leaving Combat

Combat movement and flee behavior is still evolving. The current design goal is
that ordinary hostile contact starts a fight without granting either side a free
opener, while explicit opener actions such as charge commit you to combat and
grant their first-action priority.

When `flee` succeeds, you leave the room and all active hostile encounters from
that room end for you. Mobs that remain in the room are no longer shown as
fighting you on nearby scans unless they later reach and engage you again.
