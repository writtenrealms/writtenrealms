# Combat

## Combat Text

Enable **Combat Brief Mode** in **Settings > Preferences**, then choose
**Save Changes** for compact combat output. Each attack shows its label,
attacker, target, and damage or healing amount. A `!` marks a critical hit;
`(dodge)` and `(12 abs)` indicate a dodge and absorbed damage respectively.
Combat effect applications show their duration in rounds. Uncheck the setting
to restore full combat sentences. This preference changes the display only;
combat timing and outcomes stay the same.

## Timed Instance Clears

Some instances, including a configured Persian Outpost, complete when every
member of their initial target population has died. The timer starts on first
entry and stops at the final target's death. You receive an **Instance Complete**
notice, using the same framed style as Time Control and the Message of the Day,
with your clear time, and the server saves the timestamps and participant
history for future records.

The timer measures real elapsed time, including pauses and time outside the
instance. Reentering an unfinished run does not reset it. After completion,
you can leave normally; `enter` reopens your latest completed run, keeping its
Instance ID and clear time. **Status: Completed** appears below the ID when
you return or reconnect. Previous group participants can return with
`enter <instance_ref>`. Combat remains disabled in a completed run; builder
`/reset` starts a new attempt.

## Turn-Based Instances

Some builders offer single-player instances with **Time Control**. Each run
belongs to the character who first enters it; other players cannot join that
copy, even when its owner is away.

Entering a Time Control instance shows a **Time Control** notice styled like the
Message of the Day. It explains that combat can wait for you between rounds and
highlights the `pause`, `advance`, and `resume` commands.

Open **Settings > Time Control** and check **Pause before every combat
round**. On desktop, the small **Pause Combat** checkbox sits just above the
right end of the command bar. On mobile, it is in the Time Control bar. The
setting belongs to this run and starts unchecked in a new adventure.

Unchecked, the instance uses its ordinary timing and commands. Checked, you
still explore normally until combat starts. The whole instance then waits
before the first round and between every subsequent round: combat, recovery,
roaming, respawning, timed doors, merchant restocking, and scripted delays.
Reading or spending longer thinking does not advance those systems. Gameplay
reactions to reading or speech also wait for your next turn.

Choose an ability or issue another gameplay command to prepare your next
action. A new action replaces the previous one. On desktop, it appears as an
**Action: …** button inside the right end of the command bar; click it to
advance. Hotkeys show their resolved command, such as **Action: crest**. With
no action queued, the button reads **Advance Turn** and still advances the round.
On mobile, review the Action in the Time Control bar and click **Advance Turn**.
You can also type `advance`, or press **Enter** with an empty command input while
combat is paused, including when no action is set. One round passes,
the results appear, and the instance waits again. An invalid action returns an
error without consuming the turn. Click the **×** beside the desktop Action,
use **Clear action** on mobile, or type `cancelturn` to clear the queued choice.
Clearing does not advance time or cancel an ability that is already charging;
the button returns to **Advance Turn**, allowing the existing cast to continue.
With no prepared action, combat uses your normal basic attack or continues an
existing cast. Inspection, help, and communication remain immediately available.

Abilities are checked before they replace your queued choice. If you are already
charging an ability, or the new ability fails its knowledge, requirements,
cooldown, or resource checks, the command reports the reason and leaves your
previous choice intact. You can still queue `flee` while charging; accepting that
action on advance cancels the cast and begins your escape. Target and execution
checks still run when you advance, so a queued action can fail if circumstances
change.

Each advance progresses two seconds of gameplay time and one round of active
combat. An action with a longer delay needs additional advances to finish.
Combat initiative remains unchanged. When your combat ends, exploration and
ordinary timing resume automatically; the checkbox stays checked for the next
fight. Remaining cooldowns and character effects continue on the normal world
heartbeat outside combat, including after an instance reset. Time spent paused
does not add an extra delay before they start counting down again.

Uncheck the box at any time to resume ordinary timing, including during a
fight. Remaining timer durations are preserved; the instance does not catch up
on time spent thinking. Any prepared action is submitted through the ordinary
command rules when you resume.

Quick commands:

- `pause` enables pausing before every combat round, immediately if fighting.
- `resume` disables combat pausing and restores ordinary timing.
- `time toggle` switches the checkbox on or off.
- `time` shows the current setting; `time pause` and `time resume` also work.

These commands confirm the resulting setting in the console: **Combat pauses
before each round.** or **Normal world timing is enabled.** Repeating `pause`
or `resume` confirms the same setting without toggling it.

While combat is paused, disconnecting does not advance it, and thinking does
not trigger idle logout. Normal cleanup rules still apply to abandoned or
offline instances. With combat pausing off, ordinary connection and idle rules
apply.

Combat abilities can be granted at character creation or learned during play.
See [Abilities and Training](abilities.md) for `learn`, `unlearn`, requirements,
and training rooms or NPCs.

## Fighting Together

Players and mobs in a compatible fight share one encounter with two opposing
sides. Each combatant gets one turn per round, even when several enemies target
them. Joining an existing fight does not grant an extra turn or reroll anyone's
combat order; the newcomer becomes eligible in the next round.

The Combat section in the desktop right sidebar shows the round, allies and
enemies, their health, and their active effects. It replaces the sidebar's
character list; the left-side status, map, and target panel keep their usual
layout. Quest Log and Communication Log are available under the expandable
Logs heading at the top of the right sidebar.

Click an enemy in the roster or use `kill <target>` to select your current opponent.
Changing targets keeps you in the same fight. If that opponent leaves or dies,
your next attack selects another visible opponent, preferring higher target
priority. An ability queued at a specific target keeps that target and fails
if it is no longer valid.

Allies must have an established relationship, such as matching core factions
or an explicit faction alliance. Attacking the same enemy does not by itself
make two actors allies. A join that would require a third side, or exceed the
32-combatant limit, is rejected before it spends resources or changes the fight.
Ordinary player attacks on other players remain restricted to authorized duels.

In worlds with manual combat, each connected participating player submits an
action before the shared round advances. `kill` readies a basic attack; an
ability or `flee` readies that action. Disconnected participants remain in
combat and do not hold up other players' readiness. Manual duels retain their
existing command-driven pacing: either contestant can advance the shared round.

Leaving or dying removes your character from the fight. Other combatants keep
fighting while both sides still have members. NPC fights in an unobserved room
can continue for a short activity window, then pause until someone returns.
Returning to watch does not put you back in combat: occupied mobs keep fighting
their current opponents. You rejoin when you attack or another actor engages
you. Idle hostile mobs can still attack you on entry.

Room descriptions show combatants as **“Name is here, fighting target.”**
(or **“fighting you”**) instead of their usual idle descriptions. When a mob
starts attacking, everyone who can see the fight receives an announcement,
including when a friendly mob attacks an arriving enemy.

When a mob dies, experience and currency are split equally among living player
opponents still participating in the encounter who damaged that mob. Those
players receive quest kill credit even if an allied NPC delivers the final
blow. The mob creates one corpse and one loot roll. Leaving before the kill
forfeits this participation credit; damage to a different mob does not qualify.
A fight with no eligible player contributor creates no reward or loot corpse.

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
rounds advance, shows the exact rounds remaining on each badge, and removes an
effect when its remaining duration is consumed. Beneficial barriers such as
**Crest** appear there as buffs; their details include the remaining absorb
pool, and the badge disappears immediately when that pool is spent even if the
effect had rounds left.
The Combat panel also marks your current target with its active round-based
effects. Statuses use combat-facing labels such as **Stunned** and update or
disappear as each round is resolved.

## Casts And Interrupts

An ordinary zero-windup hostile ability is still a queued combat action. It
resolves when your turn arrives in the stored encounter order; "instant" does
not normally mean that it jumps ahead of initiative. A ready interrupt aimed at
an active cast is the narrow exception described below.

Once a windup begins, its ability is committed and is shown as casting. An
interrupt can cancel that committed cast, but it cannot cancel an ability that
is only queued and can still be replaced by its owner. The interrupted ability
spends no resource. Most abilities start their cooldown when they finish, so
interrupting them leaves them ready to try again next round. Some abilities
start their cooldown when casting begins; interrupting one of these leaves its
remaining cooldown running, giving you time before the next attempt. When the
interrupted combatant's turn arrives, they use their basic attack instead if
one is legal.

When a mob charges an ability, the combat log shows **charges** and the ability
name in bold and the primary color. Later charging rounds highlight
**continues charging** the same way. Interrupt messages highlight **interrupt**
or **interrupts** alongside the canceled ability, for example,
"You **interrupt** Tigranes the spear-bearer's cast of **Crush**."

For example, **Kick** is a zero-windup attack with a 12-round cooldown. It deals
0.25x physical damage and interrupts the target when the hit lands. If you
prepare Kick while that target already has a committed cast or channel, Kick
gets primary-action priority immediately before the target, regardless of the
stored encounter order. This priority does not change the stored order for
later actions or rounds. If multiple interrupt actions qualify for the same
position, their relative order remains the stored encounter order.

Priority only creates the opportunity to interrupt. Kick's `on_hit` interrupt
still requires its damage to land; if the attack misses or is dodged, the
target's committed ability continues on its turn. An ability that is merely
queued is not yet committed, so it is immune to both interruption and this
response priority. Zero-windup abilities without an interrupt component remain
initiative-bound.

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

Use `rest` (or `r`) to begin resting and `stand` (or `st` or `sta`) to stand up.
The shared prefixes `st` and `sta` resolve to `stand`; use `stat` or `stats` to
review your stats.

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
them. With **Pause Combat** enabled, room-entry hostiles join and your initial
target is selected before the opening pause, without advancing a combat round.
Explicit opener abilities such as Charge can temporarily override that
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

If your current mob opponent is configured not to fight back, use `disengage`
to stop fighting that opponent without leaving the room. Disengaging is immediate: it
does not choose an exit, cost movement stamina, fire movement events, or give a
tracker an opportunity to pursue you. Any ability or flee attempt queued for
that player is canceled; stamina already reserved by the canceled flee is
refunded when you leave combat.

`disengage` works only against a mob with `fights_back: false`. It cannot end a
fight with a retaliating mob or another player. When several mobs have engaged
you, it removes only an eligible passive target from the shared encounter.
The target cannot disengage while another participant is attacking it or a
hostile encounter effect still involves it. The remaining fight continues.

The **Rooted** status prevents `flee` while it is active. If you are already
Rooted, the command is rejected before an escape route is chosen or stamina is
reserved. Fleeing takes time, so Rooted is checked again when your escape would
complete. If the effect lands while you are looking for an opening, the pending
attempt is canceled, its reserved stamina is refunded, and you remain in combat.
That failed escape uses your action for the round while effects and enemies
continue to act. You can try again after Rooted expires.

Rooted applies specifically to `flee`; it does not prevent `disengage` against
an eligible passive mob or add a separate restriction to ordinary direction
commands. Direction commands still follow the round-zero and combat movement
rules above.

Some mobs have the `tracker` trait. If a tracker has aggroed you during the
round-zero opening, it follows you through the exit used for ordinary movement
and immediately re-engages in the next room. Other hostile mobs remain behind.
Your initial view of the destination does not list the pursuer; its arrival is
announced afterward when it actually crosses the exit.

When `flee` succeeds, you leave the room and your encounter participation ends.
Tracker opponents follow your final escape route and re-engage in the
destination room if they are free to leave; a tracker still fighting another
participant stays in the original fight. Multiple free trackers can pursue you. Mobs that remain in
the origin room are no longer shown as fighting you on nearby scans unless they
later reach and engage you again.

Entering or leaving an instance is a stronger boundary than moving between
rooms. Ordinary mob combat ends before you cross into the other runtime, queued
combat actions are cleared, encounter-only statuses such as **Stunned** are
removed, and stamina reserved by an unfinished flee is refunded. Buffs and
other character-scoped effects continue to follow you.

Disconnecting or reloading is not a way to escape a valid fight. When you enter
the world again, the game checks that the encounter, opponent, runtime, and room
still agree. A valid fight continues and any missing round schedule is restored;
an impossible stale fight is closed and its encounter-only statuses are
removed.

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
