# Duels

Duels are private, instanced player-versus-player matches. Ordinary areas in a
world can keep PvP disabled while a linked arena instance allows two players to
fight under normal combat rules.

## Starting A Duel

Both players must stand in the same base-world room at a linked dueling arena
entrance. Challenge the other player by name:

```text
duel <player>
```

For example:

```text
duel aria
```

A challenge remains pending for five minutes. The challenged player can accept
or decline it:

```text
duel accept
duel decline
```

If more than one challenge could be relevant, add the challenger's name:

```text
duel accept aria
duel decline aria
```

The challenger can withdraw a pending challenge:

```text
duel cancel
```

Both players must remain at the same arena entrance until the challenge is
accepted. Acceptance creates a fresh private instance run and moves both
contestants into it. Bare `enter` cannot create a match run, and
`enter <instance_ref>` admits only contestants from that active accepted duel.
Both contestants must also be out of combat and free of ongoing hostile
effects when the challenge is accepted. If either condition is not met, the
challenge stays pending so it can be accepted after the danger has ended.

Use either of these commands to review your current challenge or match:

```text
duel
duel status
```

The status output also shows your lifetime duel record: fights fought, won,
and lost.

## Fighting

Once the duel begins, use `kill <player>` and supported hostile abilities
against the opposing contestant. Both players must normally be in the same
arena room to engage. Charge-style movement openers are also supported: an
ability such as `charge <opponent> <direction>` can move you into an adjacent
arena room and start combat against the opposing contestant there.

In this 1v1 version, friendly effects authored with `room.allies` are safely
scoped to the caster, so they never buff the opponent. Broad `room.players` and
`room.hostiles` selectors remain unsupported for PvP targets.

An arena may span several rooms. You can pursue your opponent with normal
movement and engage again whenever you meet.

### Fleeing Is Not A Forfeit

`flee` is the normal two-step combat escape. On the first combat step you look
for an opening; on the following step you move through an eligible exit if the
route is still available.

A successful flee ends only the current room-level combat engagement. It is
ordinary arena gameplay and **never** surrenders, forfeits, or otherwise
resolves the duel. The match stays active, and either player can pursue and
re-engage in another arena room. Rooted effects, stamina costs, and blocked
exits work the same way they do in other combat.

## Ending A Duel

A duel ends when one contestant is defeated or explicitly surrenders:

```text
duel surrender
```

`duel concede` and `duel forfeit` are equivalent aliases. Surrendering awards
the win to the opponent; fleeing does not.

When the match ends:

- both contestants' health, energy, and stamina are restored
- all combat in that instance run is disabled
- the result cannot be counted twice, even if result processing is retried
- each contestant's persistent duel record is updated

The character state record uses three independent keys:

| State key | Updated for |
| --- | --- |
| `state.character.duels_fought` | Both contestants |
| `state.character.duels_won` | The winner |
| `state.character.duels_lost` | The loser |

The completed arena remains available as a non-combat space until the players
leave. Both players must use `leave`, return to the arena entrance, and create
a new duel to fight again. A completed run cannot be reactivated or reused for
a rematch.

If both contestants disconnect and the empty arena reaches its normal cleanup
timeout, the unfinished duel is abandoned. Neither player receives a fight,
win, or loss, and both are returned safely to the base world.

If the world enables duel result announcements, online players in the base
world and its instances receive:

```text
<winner> has defeated <loser> in a duel.
```

The announcement is disabled by default.

## Current Scope

The player workflow and result service currently support 1v1 contests only.
Participant rows already carry roles and team numbers as extension points, but
teams will still require team-aware opponent selection and result fields, and
spectators will require an admission and viewing workflow.
