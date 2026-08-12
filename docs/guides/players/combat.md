# Combat

Combat abilities can be granted at character creation or learned during play.
See [Abilities and Training](abilities.md) for `learn`, `unlearn`, requirements,
and training rooms or NPCs.

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

In an active 1v1 duel, Charge can instead target the opposing contestant. The
same current-room and adjacent-room forms apply, and a directed Charge moves
you into the destination arena room before starting that combat encounter.

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

When you prepare a hotkeyed ability, its button in the Combat panel uses the
primary-color background. It remains highlighted while queued and charging,
then returns to normal when the ability resolves, is replaced, or is canceled.

Some effects can change what happens on a turn. For example, stun can prevent a
combatant from taking their primary action, while Rooted prevents a character
from fleeing.

Your active round-based effects appear beside your current posture in the
status panel. This includes both character effects, such as buffs that can span
encounters, and encounter-scoped effects such as stun. The display updates as
rounds advance and removes an effect when its remaining duration is consumed.

## Casts And Interrupts

A zero-windup hostile ability is still a queued combat action. It resolves when
your turn arrives in the stored encounter order; "instant" does not mean that
it jumps ahead of initiative.

Once a windup begins, its ability is committed and is shown as casting. An
interrupt can cancel that committed cast, but it cannot cancel an ability that
is only queued and can still be replaced by its owner. The interrupted ability
spends no resource and starts no cooldown. When the interrupted combatant's
turn arrives, they use their basic attack instead if one is legal.

For example, a Hoplite's **Kick** is a zero-windup attack with a 12-round
cooldown. It deals 0.25x physical damage and interrupts the target when the hit
lands. Because encounter order still applies, Kick must resolve before the
enemy completes its cast.

In a duel, hostile cast narration identifies the opposing contestant and the
ability being prepared, so both players can see the committed cast and its
interruption in the combat log. Channel execution is not available yet, though
the interrupt contract already recognizes committed channeling state for that
future behavior.

## Recovery

Health, energy, and stamina recover automatically while you are in the game.
Resting increases the normal out-of-combat recovery rate. During combat, the
explicit regeneration values from your stats still apply, and stamina keeps its
baseline recovery. These passive updates refresh the vitals display silently;
they do not add entries to the game console.

## Death Destinations

A world may use one fixed death room or deterministic routes based on facts
about your character and the place where you died. For example, core factions
may have separate infirmaries, lower- and higher-level characters may use
different recovery areas, classes may return to different divine domains, and
actions taken during play may set character state that changes a later death
destination. Routes are evaluated in builder-authored order and the first
matching route wins.

Instance deaths stay inside the current instance by default. An instance may
instead be configured to use the base world's death routing. In that case its
own death penalty is applied in the instance first, then you and any surviving
carried equipment return atomically to the exact base-world runtime from which
you entered. Items or a corpse left by the penalty remain in the instance.

After every death, your current health, energy (mana in worlds that use that
label), and stamina are each set to 1. They then recover through normal
regeneration; death does not refill them.

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

## Player Duels

Player-versus-player combat is available in private duel arenas configured by a
builder. The base world can keep PvP disabled while a linked arena instance
permits combat only between the two contestants in an accepted match.

Inside an active duel, use `kill <opponent>` and supported player-targeted
hostile abilities. Charge-style movement openers can cross one arena exit and
engage the opposing contestant in the adjacent room. Friendly `room.allies`
effects apply only to their caster in the current 1v1 format; broad
`room.players` and `room.hostiles` selectors remain unsupported for PvP. You
cannot attack another player who is not your opposing contestant. See the
[Duels guide](duels.md) for challenges, surrender, records, and rematches.

## Looting

Use `loot` after a kill to take every pickable item from the first matching
corpse:

```text
loot
```

This built-in shortcut is equivalent to `get all corpse`. If more than one
corpse is present, add the normal numbered selector:

```text
loot 2.corpse
```

Personal aliases take precedence over the built-in shortcut, so
`alias loot = <command>` can replace it. Removing that personal alias with
`unalias loot` restores the built-in behavior.

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

In a duel arena, `flee` is ordinary arena gameplay. A successful flee ends only
the current combat encounter and never forfeits the duel. The match remains
active, so contestants can move through the arena, pursue one another, and
re-engage in the same or another room. Use `duel surrender` only when you
intend to concede the match.
