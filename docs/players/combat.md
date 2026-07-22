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
combatant from taking their primary action, while Rooted prevents a character
from fleeing.

Your active round-based effects appear beside your current posture in the
status panel. This includes both character effects, such as buffs that can span
encounters, and encounter-scoped effects such as stun. The display updates as
rounds advance and removes an effect when its remaining duration is consumed.

## Recovery

Health, energy, and stamina recover automatically while you are in the game.
Resting increases the normal out-of-combat recovery rate. During combat, the
explicit regeneration values from your stats still apply, and stamina keeps its
baseline recovery. These passive updates refresh the vitals display silently;
they do not add entries to the game console.

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

When combat begins, its encounter starts at round zero. You may still use an
ordinary direction command to leave before the first combat round resolves.
Once that first round has resolved, ordinary movement is blocked and you must
use `flee` to leave combat.

The **Rooted** status prevents `flee` while it is active. If you are already
Rooted, the command is rejected before an escape route is chosen or stamina is
reserved. Fleeing takes time, so Rooted is checked again when your escape would
complete. If the effect lands while you are looking for an opening, the pending
attempt is canceled, its reserved stamina is refunded, and you remain in combat.
That failed escape uses your action for the round while effects and enemies
continue to act. You can try again after Rooted expires.

Rooted applies specifically to `flee`; it does not add a separate restriction to
ordinary direction commands. Those commands still follow the round-zero and
combat movement rules above.

Some mobs have the `tracker` trait. If a tracker has aggroed you during the
round-zero opening, it follows you through the exit used for ordinary movement
and immediately re-engages in the next room. Other hostile mobs remain behind.
Your initial view of the destination does not list the pursuer; its arrival is
announced afterward when it actually crosses the exit.

When `flee` succeeds, you leave the room and all active hostile encounters from
that room end for you. Any tracker mobs from those encounters follow your final
escape route and re-engage in the destination room; this can include multiple
trackers from the same fight, not just your primary target. Mobs that remain in
the origin room are no longer shown as fighting you on nearby scans unless they
later reach and engage you again.

A tracker follows only the single exit you just used. It does not teleport or
search across multiple rooms. If the mob can no longer traverse that exact
route, or either of you has moved somewhere unexpected before the chase
resolves, it stays behind. Rooms flagged `no_roam` stop tracker pursuit: the
player may cross the boundary, but the tracker cannot enter or leave that room.

Fleeing respects the same room movement policies as ordinary travel. A blocked
direction is not eligible when the game chooses an escape route. The chosen
route is checked again when the delayed flee completes, because doors, mobs,
and other room conditions may change while you look for an opening. If that
route has become blocked, the game uses another eligible route when one is
available; otherwise, you lose the chance to flee and remain in combat.
