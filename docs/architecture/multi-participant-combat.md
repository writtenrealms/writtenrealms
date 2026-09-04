# Multi-Participant Combat Encounters

Status: proposal

This document extends the encounter-scoped combat direction in
[combat-encounter-model.md](combat-encounter-model.md) so one fight can contain
multiple players and mobs on multiple sides. It defines the target runtime
shape for:

- one player fighting several mobs
- several players fighting one or more mobs
- mobs assisting players or other mobs
- mobs fighting other mobs without a player present
- player parties fighting other parties or mob groups
- encounters with more than two independently hostile sides

Related architecture:

- [combat-encounter-model.md](combat-encounter-model.md)
- [combat-abilities-model.md](combat-abilities-model.md)
- [combat-buffs-and-effects.md](combat-buffs-and-effects.md)
- [combat-resolution-formulas.md](combat-resolution-formulas.md)
- [faction-system.md](faction-system.md)
- [ambient-command-issuers-plan.md](ambient-command-issuers-plan.md)
- [pre-action-policy-hooks.md](pre-action-policy-hooks.md)
- [yaml-manifest-system.md](yaml-manifest-system.md)

## Summary

WR2 should model a connected fight as one combat encounter containing actor
participants, encounter-local sides, and explicit relations between those
sides.

The engine should not represent a group fight as a collection of independent
player-versus-mob pairs. Pairwise encounters cannot reliably answer who gets a
turn, who is assisting whom, whether fleeing ends the whole fight, how area
abilities select targets, or how two fights merge when another actor joins.

The central runtime shape is:

```text
CombatEncounter: Cage at the Camp Entrance

  Side 1                          Side 2
  ├─ Tidecaller (player)          ├─ the Great King's headsman
  └─ freed Greek commander        └─ Persian guard

  Hostility: Side 1 <-> Side 2
```

Every active participant gets at most one primary turn per round, owns its own
current target and pending intent, and advances its effects and cooldowns once
per round. One resolver job advances the encounter, regardless of how many
hostile actor pairs exist inside it.

This is a unified combat engine. PVE, mob-versus-mob combat, parties, duels, and
team PVP should differ in admission and policy, not in their fundamental turn
resolver.

## Motivating Acceptance Scenario

The immediate motivating case is the cage at the Persian outpost:

1. A Greek commander is captive and does not participate in combat.
2. The player releases the commander, changing its runtime state to
   `captive: false` and making it attackable.
3. A Persian headsman is already in the room, or enters afterward.
4. Greek and Persian faction policy says they are hostile.
5. The commander assists the player against the headsman.
6. The player, commander, and headsman resolve turns in one encounter.
7. The encounter continues correctly if the player flees or dies while the two
   mobs can still fight.

This should be normal data-driven combat behavior. It should not require a
trigger whose script says that this particular Greek attacks this particular
headsman.

The same machinery must also work when no player is present. If the released
commander and headsman are eligible to initiate against each other, they may
form an NPC-only encounter.

## Goals

- Support any bounded mixture of player and mob combatants.
- Give every combatant one coherent turn, target, effects timeline, and exit
  state.
- Keep authored aggression, retaliation, assistance, faction diplomacy, and
  PVP permission as separate policies.
- Make encounter formation actor-neutral and idempotent.
- Use the same ability, effect, targeting, and resolution pipeline for players
  and mobs.
- Preserve deterministic resolution and ordered event output.
- Schedule work per active encounter rather than per player, mob, actor pair,
  room, or world.
- Keep database work bounded and batched so combat remains credible with
  hundreds of concurrent players and has a path to thousands.
- Fit WR2's `Command -> Action -> Event` and transactional outbox architecture.

## Non-Goals

- Reproduce WR1's runtime combat state or migrate active WR1 fights.
- Add a world-wide combat heartbeat that scans every room or actor.
- Treat faction hostility as permission to bypass PVP rules.
- Define exact damage, initiative, threat, or reward-balance formulas here.
- Make every actor in one room part of the same encounter.
- Use player following, spawn `group_id`, or faction membership as a substitute
  for a real encounter side.
- Invent a combat-only predicate language. Conditional combat configuration
  must use the existing WR2 condition framework in
  `backend/core/condition_dsl.py`.

## Current Gap

The current implementation has useful encounter pacing and deterministic
one-on-one PVE behavior, but its core assumptions remain pairwise:

- a PVE encounter directly identifies one player and one mob
- the PVE resolver advances that pair
- room aggression scans from mobs toward players
- faceoff is effectively one player's current mob
- PVP participant storage exists, but the duel path assumes one opponent per
  player and resolves a two-player exchange
- the current mob-definition `assists` value only influences spawn grouping;
  combat does not consume it as an assistance policy
- group rewards, allied mob targeting, and mob-versus-mob turns do not have a
  shared contract

Adding a second pairwise encounter whenever another actor attacks would make
these assumptions more difficult to unwind. Multi-participant combat should
instead make `CombatParticipant` the authoritative actor membership model and
make one encounter the aggregate for the connected fight.

| Current assumption | Target contract |
| --- | --- |
| `CombatEncounter.player` and `.mob` identify the fight | All actors are `CombatParticipant` rows. |
| One active encounter per player/mob pair | One encounter per connected hostile fight. |
| Pending player and mob actions live on the encounter | Every participant owns its pending intent. |
| One encounter-wide faceoff override | Every participant owns a current target. |
| PVE and duel resolvers execute different actor shapes | One actor-neutral participant resolver. |
| One scheduled task per hostile mob pair | One scheduled task per encounter. |
| `assists` implies only spawn grouping | Combat assistance has an explicit policy. |

## Core Invariants

The implementation should preserve the following invariants:

1. An active actor belongs to at most one spatial combat encounter.
2. Every active participant belongs to exactly one encounter-local side.
3. A participant cannot target itself or an inactive participant.
4. A participant's hostile target must belong to a side connected to its side
   by an active hostility edge.
5. No two sides exist unless their participants need independent relation or
   victory state.
6. Each participant becomes eligible for at most one primary action per round.
7. Effects, cooldowns, and durations advance at most once per logical round.
8. Duplicate or stale resolver delivery cannot advance a round twice.
9. The encounter finishes only when it contains no active hostile relationship
   that can still produce combat.
10. Encounter mutations and their canonical events commit atomically.
11. Spatial combatants share the same runtime-world and room scope. Reused
    authored room ids in different instance runs do not make actors colocated.
12. PVP authorization is checked before players are placed on hostile sides.

## Domain Model

The names below are recommendations. Exact Django field names may follow local
conventions, but the concepts and invariants should remain explicit.

### CombatEncounter

`CombatEncounter` is the aggregate root for one connected fight.

It should own or reference:

- runtime world and authored room scope
- optional instance run and optional PVP match
- status
- logical round number
- pacing mode and next-resolution deadline
- deterministic random seed
- monotonic state revision for snapshots and deltas
- separate schedule generation for resolver idempotency
- encounter sides
- side-relation edges
- actor participants
- encounter-scoped effects and ordered event output

An encounter does not imply that everyone in the room is fighting. Two
disconnected fights may coexist in the same room until an action or policy
connects them.

### CombatSide

`CombatSide` is an encounter-local alliance. It answers which participants
share victory and ally-relative selectors for this fight.

A side is not itself a party, faction, spawn group, or PVP team. Those objects
may inform side assignment, but encounter membership is concrete runtime state.
This distinction allows:

- a faction ally to stay out of a fight
- a charmed mob to fight against its normal faction
- two parties to cooperate temporarily
- one faction to split into hostile PVP teams
- an actor to change allegiance through an explicit combat mechanic later

Recommended fields include encounter, stable join/order key, status, and an
optional source descriptor such as `party`, `faction_assist`, `spawn_cohort`,
`match_team`, or `ad_hoc`. The source is diagnostic, not authoritative after
the side is created.

### CombatSideRelation

Relation belongs between sides, not in a single encounter-wide team boolean.
A canonical unique pair of sides records `allied` or `hostile`; the absence of
a row means `neutral`. Relations are reciprocal inside an encounter even when
the authored faction attitude that caused admission was directional.

Participants on the same side are always allied. An explicit `allied` relation
between different sides preserves cooperation when the groups still need
different match, reward, or victory identity. `encounter.allies` includes both
the actor's own side and sides connected by that relation.

Explicit relations support:

- ordinary two-side combat
- three-way fights
- two allied sides that are both hostile to a third side
- partial hostility where side A fights side B while side C is present but
  neutral

For resolution, a hostile relation is reciprocal. Directional faction
attitudes may decide who initiates, but once an attack is admitted the
resulting combat relationship allows retaliation in both directions, subject to
`fights_back`, incapacitation, and other action policy.

The canonical storage should enforce one row for `(lower_side_id,
higher_side_id)`, validate the relation enum, and prohibit self-edges.

### CombatParticipant

`CombatParticipant` becomes the authoritative link between an encounter and an
actor.

Each row identifies exactly one player or mob and should include:

- encounter and side
- actor identity
- active/left state and exit reason
- stable initiative value and tie-break order
- join sequence
- first eligible round
- current target participant
- pending combat intent
- pending flee state where applicable
- bounded AI/threat state where applicable
- contribution counters needed for reward policy
- timestamps and a participant revision where useful

The database should enforce that exactly one actor reference is present. It
should also use partial uniqueness constraints so an active player or mob
cannot be active in two encounters at once.

`current_target` is a self-referential participant key. This prevents
player-only and mob-only target columns from multiplying as new actor types or
target mechanics are added.

### Actor Snapshots

Combat events and rewards must remain meaningful when a mob is deleted during
death handling. Participant or event data should retain the minimum immutable
actor snapshot needed after deletion, such as:

- actor kind and runtime id
- display name at the relevant event
- mob-definition identity when applicable
- faction identity needed by event predicates or rewards

Snapshots are not a second canonical actor model. They preserve audit and event
meaning across actor lifecycle changes.

### Suggested Relationship Shape

```text
CombatEncounter
  ├─ CombatSide
  │    └─ CombatParticipant ──> Player | Mob
  ├─ CombatSideRelation (side pair, allied | hostile; absent = neutral)
  └─ Encounter-scoped effects / event outbox references

CombatParticipant.current_target ──> CombatParticipant
```

The existing direct `CombatEncounter.player` and `CombatEncounter.mob` fields
become compatibility data during implementation and should ultimately be
removed. New behavior must not depend on them after the unified resolver is in
place.

## Relationship, Aggression, Retaliation, And Assistance

These are different questions and should remain different configuration:

- **relationship**: are two actors allied, neutral, or hostile in this context?
- **aggression**: may this actor initiate combat, and against which actor kinds?
- **fights back**: does this actor choose normal combat actions after it has
  been attacked or admitted to a hostile encounter?
- **assistance**: does this actor voluntarily join an ally's existing fight?
- **PVP authorization**: may these particular players be hostile under world,
  consent, and match rules?

Conflating these concepts produces surprising behavior. A passive guard may
refuse to initiate but still retaliate. A brave ally may assist a friend but
not attack neutral strangers. A faction may hate another faction without
granting players permission to attack each other.

### Contextual Relationship

The runtime should expose one query-free policy function over a preloaded
combat/room snapshot:

```text
relationship(actor_a, actor_b, context) -> allied | neutral | hostile
```

The initial precedence should be:

1. explicit encounter control effects, such as charm or scripted allegiance
2. existing encounter side and relation state
3. authorized PVP match team
4. player party affinity where party assistance applies
5. an explicitly authorized hostile action for this engagement
6. explicit authored faction relationship
7. actor-specific reputation relationship where applicable
8. baseline core-faction policy
9. neutral fallback

PVP authorization is a separate admission gate, not a faction relationship.
For player-versus-player hostility, the engine checks world, zone, consent, and
match policy before accepting a `hostile` result or explicit attack. A denied
gate prevents engagement even if faction or reputation data says the players
are enemies.

As established in [faction-system.md](faction-system.md), the baseline is that
actors with the same core faction are non-hostile and actors with different
core factions are hostile. For encounter formation, the initial normalization
should treat the same core faction as `allied`, different core factions as
`hostile`, and no applicable faction relationship as `neutral`. The existing
authored `friendly` relationship value normalizes to runtime `allied`; it is
distinct from the legacy mob aggression value also named `friendly`. Authored
relationships may override the baseline.

For example, a world may make the Greek-to-Persian attitude explicit:

```yaml
kind: faction
metadata:
  code: greek
  name: Greeks
spec:
  type: core
  relationships:
    persian: hostile
```

If Persians should also initiate against Greeks, author the reciprocal
relationship on the Persian faction.

Faction relationship data may be directional for initiation policy. The room
reconciler evaluates each potential initiator separately. Admitting an attack
creates a reciprocal encounter hostility edge so ordinary retaliation remains
coherent.

Side membership and hostility are snapshotted runtime decisions. Ordinary
faction-content edits affect later admission; they do not silently move an
active participant to another side halfway through a round. An explicit
allegiance or cease-hostility mechanic may change topology at a round boundary.

### Aggression

Aggression decides whether an idle actor initiates; it does not decide who is
an ally after combat begins.

Recommended actor-neutral interpretation:

| Aggression | Initiates against |
| --- | --- |
| `passive` | nobody |
| `normal` | eligible actors whose contextual relationship is hostile |
| `players` | eligible players, preserving the current explicit player-hostile policy |
| `all` | every eligible non-allied actor kind |

The current mob aggression value `friendly` should preserve today's
faction-aware, `normal`-equivalent behavior through the transition and be
marked for deprecation unless it receives a distinct builder-facing purpose.
New group-combat code should not silently invent a different meaning for it.

Eligibility still applies before aggression. An actor cannot initiate against
itself, a dead or unavailable target, an actor in another runtime/room, a
non-attackable target, or a player for whom PVP is not authorized.

### Retaliation

`fights_back` controls an actor's normal responses after combat begins. It is
independent of aggression:

- `aggression: passive`, `fights_back: true` means do not start fights, but
  defend when attacked
- `aggression: normal`, `fights_back: false` means initiate if policy permits,
  but perform no normal attack turns afterward

Admission to an encounter and choosing an attack are therefore separate. An
actor that does not fight back may still be present for effects, dialogue,
objectives, surrender, or scripted behavior.

### Assistance

Mob definitions need an explicit combat-assistance policy. Do not silently
reinterpret the existing `assists` boolean, because it currently influences
spawn grouping and may carry content assumptions unrelated to combat.

Recommended manifest vocabulary:

```yaml
spec:
  aggression: normal
  fights_back: true
  combat:
    assist: allies
```

Initial values:

| Value | Behavior |
| --- | --- |
| `none` | Does not voluntarily join another actor's fight. |
| `same_spawn_cohort` | Joins an eligible ally from the same concrete spawn cohort. |
| `allies` | Joins an eligible actor whose contextual relationship is allied. |

Assistance never bypasses colocation, attackability, PVP authorization, or
condition checks. It also does not mean that every ally in the room must be
merged into the encounter. Only actors whose assist policy activates become
participants.

The optional WR1 authored-world converter should emit this field only where it
can prove the intended WR1 semantics. Otherwise it should emit a diagnostic for
builder review. When this manifest contract is implemented, its conversion
mapping must be recorded in the optional WR1 conversion notes in
[yaml-manifest-system.md](yaml-manifest-system.md).

### Conditional Combat Policy

Authored conditions may restrict aggression or assistance. They must compile
through the existing condition DSL and evaluate against the already loaded
actor, room, runtime, and event snapshot.

Example for the captive commander:

```yaml
kind: mobdefinition
metadata:
  slug: greek-captive-commander
  name: a Greek commander
spec:
  factions:
    core: greek
  initial_state:
    captive: true
  aggression: normal
  fights_back: true
  combat:
    attackable: false
    assist: allies
    engage_when:
      eq:
        - state.character.captive
        - false
```

The precise state namespace must match the canonical scoped-state contract.
The important rule is that this is a normal condition DSL expression, not a
new combat predicate syntax.

`engage_when` gates automatic initiation and voluntary assistance. It does not
replace `attackable`, and it does not silently prevent an otherwise legal
explicit attack against the mob. Once another actor admits that attack,
`fights_back` controls the mob's normal retaliation. In this example the
release action changes both the runtime `attackable` value and the captive
state, so automatic combat becomes eligible after commit.

## Configuration Ownership

Combat policy should stay with the object that owns its lifecycle instead of
collecting unrelated switches on the encounter row.

| Owner | Configuration responsibility |
| --- | --- |
| World config | `allow_combat`, resolution interval/pacing, PVP mode, default reward policy, and lower per-world limits. |
| Zone or instance policy | PVP-zone admission, objective-critical NPC simulation, and permitted lower overrides. |
| Faction | Authored directional attitudes and reputation thresholds. |
| Mob definition | `aggression`, `fights_back`, attackability, assistance, automatic-engagement conditions, and AI/loadout policy. |
| Spawn cohort | Concrete same-spawn assistance affinity where selected by the mob policy. |
| Party | Player assistance/readiness/reward-sharing policy and membership. |
| PVP match | Authorized contestants, teams, ruleset, and match victory/result policy. |
| Ability or effect | Legal target type, relational selectors, forced target, concealment, and combat scope. |
| Runtime encounter | Concrete sides, relations, participants, targets, intents, logical time, and due state. |

Operator hard ceilings for participants, dense side relations, reaction depth,
NPC-only scheduling, and task work are deployment safety settings. Authored
world policy may choose lower limits but cannot raise those ceilings.

Triggers may change ordinary runtime state, attackability, faction assignment,
or another documented input and then request room reconciliation. They should
not assemble participant rows, mutate encounter topology directly, or issue a
special `/kill` command to simulate assistance.

## Encounter Formation

All hostile actions and automatic aggression should converge on one
idempotent operation:

```text
engage(attacker, target, reason, context)
```

The operation validates eligibility and PVP permission, then handles four
cases:

| Existing state | Result |
| --- | --- |
| Neither actor is in combat | Create an encounter, two sides, one hostile relation, and two participants. |
| One actor is in combat | Add the other actor to an allied or new hostile side as policy requires. |
| Both actors are in the same encounter | Add or confirm the relevant side relation and update target/intent state. |
| Actors are in different encounters | Merge the connected encounters, then add or confirm hostility. |

The operation must be safe under duplicate delivery and concurrent engagement.
It should return the canonical encounter and whether topology changed.

Actors can merge only when their runtime-world, room, instance, pacing, and
match authorities are compatible. An action that would bridge unrelated PVP
matches or an isolated match and ordinary room combat is rejected unless an
explicit match policy defines that transition.

### Room Combat Reconciliation

An actor-neutral room reconciliation action discovers new eligible aggression
and assistance relationships. It should run after relevant state changes,
including:

- actor entry or spawn
- actor release or a condition-changing state mutation
- faction or aggression changes
- an actor attacking another actor
- an ally becoming engaged
- death, flee, movement, or despawn

Reconciliation is event-driven and bounded to the affected runtime room. It is
not a polling scan across the world.

The state-changing transaction should enqueue a deduplicated reconciliation
request through the normal event/outbox path. Reconciliation observes committed
state after that transaction; it must not nest a second topology mutation
inside an unrelated Trigger or movement transaction.

The action should be incremental around a capped set of changed actors:

1. claim a durable room reconciliation lease and freeze its dirty generation
2. load one bounded candidate page plus the faction/diplomacy snapshot in
   batches
3. group candidates by useful keys such as party, match team, spawn cohort, and
   faction signature
4. compare changed actors with that page rather than rebuilding every possible
   room pair, evaluating each changed/resident pair in both possible initiation
   directions
5. evaluate initiation and assistance deterministically in memory
6. apply the admitted topology changes as one bounded batch through the
   idempotent engagement service
7. advance the page cursor or mark the frozen generation applied
8. schedule each changed canonical encounter once

A participant cap does not bound nonparticipants in a crowded room. The
reconciler therefore needs independent limits for changed actors, candidate
page size, admitted topology changes, and work per task. If the changed-actor
set overflows, it records a full-room-dirty marker and scans the room through
stable pages rather than doing an unbounded pairwise pass.

A full-room pass must not cross-product every page. It first reduces actors to
precompiled relation signatures, evaluates compatible signature buckets, and
expands only admitted actor joins up to the encounter/work cap. Actor-specific
reputation or control exceptions are evaluated for the changed actor or the
bounded admitted set, not by repeatedly comparing every resident with every
other resident.

The durable coordination state should include at least dirty generation,
applied generation, continuation cursor, and lease expiry. A mutation arriving
while a worker runs increments dirty generation. When the worker commits, it
requeues if a page remains or the dirty generation advanced; it must not clear
the newer signal. Expired leases are recoverable by the normal scheduler.

Continuation is safe between reconciliation pages because no logical combat
round is in progress. Each page applies a complete bounded topology mutation;
it does not leave half an encounter round committed.

A world-wide diplomacy edit increments a versioned policy snapshot. It must not
synchronously fan out one transaction or task for every actor in the world.
Active rooms observe the new version on their next relevant event or through a
bounded reconciliation queue.

### Joining A Side

An assisting actor joins an existing side only when it is mutually allied with
every active member, shares the same encounter victory/reward identity, and has
the same relation to every other existing side. Otherwise it receives a new
side and explicit allied or hostile side relations are added as required.
Non-hostile absence remains neutral rather than being guessed as alliance.

If admitting a new actor needs to distinguish participants that were previously
on one side, the topology operation splits that side at a round boundary,
copies its existing relations, and then specializes the affected relations.
This keeps the side graph truthful for non-transitive relationships without
falling back to mutable actor-pair encounters. A bounded encounter may use
one-participant sides when that is the only exact representation.

### Merging Encounters

An action can connect two formerly independent fights. The merge protocol
should:

1. lock all affected encounter ids in ascending order
2. select a canonical encounter, normally the lowest id
3. move or coalesce sides, side relations, participants, pending intents,
   effects, and deadlines
4. set the canonical logical round to the maximum source round
5. preserve remaining logical distance for absolute timers: for example, a
   participant two rounds from eligibility in its source remains two rounds
   from eligibility after rebasing; flee, cast, and channel phases follow the
   same rule
6. preserve remaining-duration cooldown and effect counters without replaying
   elapsed source rounds
7. preserve each participant's initiative value
8. retain the canonical encounter's random seed; all moved participants use
   that seed only for future action ordinals
9. preserve the earliest valid next-resolution deadline
10. mark donor encounters as merged/finished with a pointer to the canonical id
11. increment the canonical state revision and emit one topology update

Old resolver tasks carry the donor id and schedule generation. They resolve the
canonical pointer or no-op without advancing an extra round.

### Splitting Disconnected Fights

Deaths, movement, fleeing, allegiance changes, or hostility removal can leave
an encounter with disconnected conflict components. The resolver should detect
this at a round boundary:

- a component with no active hostility finishes
- one connected hostile component may retain the original encounter
- additional hostile components become new encounters
- participants, effects, targets, pacing, and pending intents move with their
  component

A split child inherits the parent's current logical round and remaining
timers. Its random seed is derived deterministically from the parent seed,
split round, and sorted participant keys in that child, so retries create the
same children and future rolls.

Splitting at a round boundary avoids changing the topology halfway through a
resolution snapshot.

## Faceoff And Target Selection

Faceoff is per participant. It is not the identity of the whole encounter.

Each participant has a `current_target`, while the client may separately keep a
non-combat inspection cursor. This distinction lets a player inspect one actor
without silently spending a combat target change.

### Target Validity

A hostile combat target is valid only when it:

- is active in the same encounter
- is not the acting participant
- belongs to a hostile side
- is spatially targetable in the same runtime room
- satisfies visibility, concealment, and ability-specific rules

The resolver revalidates targets immediately before an action. A target that
dies, flees, changes side, becomes hidden, or otherwise becomes invalid must
not receive a stale hit.

### Target Precedence

When resolving a primary action, the effective target should be selected in
this order:

1. a valid forced target from taunt or another explicit control effect
2. a valid target explicitly supplied by the queued intent
3. the participant's valid current target
4. mob threat or authored AI strategy
5. existing `target_priority` policy where relevant
6. stable encounter join order and actor id as deterministic fallback

The engine should distinguish `current_target` from an action's effective
target. A temporary taunt can redirect an action without permanently replacing
the participant's chosen focus unless the mechanic explicitly says so.

### Command Semantics

Inside an existing encounter:

- `kill <target>` selects or updates the participant's current hostile target
  and queues the normal attack intent; it does not create a second encounter or
  grant an extra turn
- a dedicated `target <actor>` command may update only the inspection/focus
  cursor if the client needs non-action targeting
- an ability may target another valid participant without changing faceoff
  unless its definition says `sets_focus`
- being attacked may initialize an empty current target, but should not
  override a valid explicit player selection

After a target becomes invalid, automatic retargeting should follow the same
deterministic policy and emit a target-changed event. If no valid hostile target
exists, the participant has no attack target; the encounter may finish or
split.

### Existing Target Priority

`target_priority` remains useful as a player-facing or AI fallback preference,
for example choosing a shieldbearer before an archer. It should not become a
hidden replacement for threat, assistance, or side assignment.

## Initiative And Joining Mid-Round

Initiative is rolled or derived once when a participant joins and remains
stable unless an explicit mechanic changes it. Stable actor id or join sequence
breaks ties deterministically.

A participant joining after a round snapshot has begun receives
`first_eligible_round = current_round + 1`. It may be visible and targetable as
soon as topology commits, but it cannot gain an early action by racing the
resolver.

Openers such as ambush or charge may assign a first-round priority without
rerolling the persistent initiative order.

## Unified Round Resolver

One actor-neutral resolver should replace separate one-player/one-mob and
two-player exchange logic. Admission policy may differ for PVE and PVP, but
turn execution should consume the same participant snapshot.

A round should execute in this order:

1. lock and revalidate the encounter, expected round/schedule generation,
   scope, and pacing
2. batch-load active sides, hostility, participants, actor rows, pending
   intents, effects, ability data, and required inventories
3. remove or mark participants that are no longer spatially or legally valid
4. freeze the round participant snapshot and increment logical round
5. complete flee or other start-of-round transitions already due
6. advance start-of-round effects once per eligible participant
7. generate missing mob intents through the normal intent pipeline
8. order eligible actors by opener priority, stable initiative, join sequence,
   and actor id
9. revalidate each actor and effective target immediately before its action
10. resolve the action, deaths, exits, interrupts, target changes, and bounded
    reactive effects
11. advance end-of-round effects, cooldowns, and durations once
12. split or finish components that no longer contain active hostility
13. persist mutations and ordered outbox events in the same transaction
14. schedule exactly one next resolution for each remaining active encounter

Randomness should be reproducible from encounter seed, logical round,
participant id, and action ordinal. Retrying the same logical round must produce
the same decisions and rolls.

Reactive effects need a bounded depth or explicit work queue so reflection,
counterattack, or on-hit chains cannot recurse indefinitely in one transaction.

For the initial implementation, the hard participant/effect/reaction caps must
guarantee that one complete round fits one transaction. Continuations may run
between rounds or between reconciliation pages, never halfway through an
initial-release round. Supporting a battle too large for one atomic round would
require a separate persisted frozen-round state machine and an event
publication barrier; that is future work, not an implicit consequence of a
generic work cap.

## Pacing And Scheduling

The world-level `combat_resolution_interval` remains pacing policy:

- `> 0`: advance active encounters on the configured cadence
- `0`: auto-resolve without wall-clock delay
- `-1`: manual or input-gated advancement

Every active encounter stores a durable due state. A Celery task is an
acceleration mechanism, not the only evidence that work is pending. Each task
includes encounter id, due timestamp, logical round, and schedule generation;
stale or duplicate tasks lock, recheck, and no-op.

There must be one scheduled task per encounter, not one task per hostile actor
pair or mob.

### Immediate Mode

An interval of `0` must not resolve an arbitrarily large fight to completion in
the initiating HTTP request or one long database transaction. It should use
bounded chained round batches, commit each complete round, and yield to the
queue when configured limits are reached.

### Manual Mode

In a multi-player encounter, one player's command must not accidentally advance
several rounds before other participants can submit choices. Manual encounters
need one explicit readiness rule.

The recommended initial rule is:

- each controllable participant submits or replaces one intent for the next
  round
- the encounter advances once all required controllable participants are ready
- an explicit encounter-advance action may fill missing intents with their
  documented defaults where world policy allows it
- a solo player's `kill` command may submit the intent and advance immediately
  when that player is the only required controllable participant

Mob intents are generated at resolution time and do not hold the readiness
barrier open.

### Recovery

A bounded recovery task should query an indexed due slice such as `(status,
next_resolution_ts)` and enqueue or advance overdue encounters. Recovery must
not replay every missed wall-clock interval. It advances one logical round or
one bounded batch, then schedules from the new committed state.

### NPC-Only Liveness And Backpressure

NPC-only combat is supported, but it must not create permanent background work
in every populated room. Scheduling it is an activity policy layered over the
same combat semantics.

An NPC-only encounter may auto-start or continue while at least one of these is
true:

- a player is present or the room holds a recent-activity lease
- an active instance objective requires the fight
- authored world/zone/instance policy explicitly opts the area into unattended
  simulation

When the last player leaves, a bounded recent-activity lease lets an existing
fight continue for configured rounds or wall-clock time. This lets the freed
Greek and headsman finish a short fight after the player flees. When the lease
expires, an unresolved NPC-only encounter pauses, clears its due schedule, and
consumes no recurring task. Re-entry or another relevant room event reactivates
and reconciles it without replaying skipped wall-clock rounds.

The scheduler should enforce per-world and global limits for concurrently due
NPC-only encounters, prioritize player-observed and objective-critical fights,
and expose backpressure metrics. Authored unattended simulation still obeys
participant, event, and work caps; it is not permission for a world-wide combat
heartbeat.

## Abilities, Effects, And Target Selectors

Abilities and effects need relational encounter selectors, not only physical
room/type selectors.

Recommended selector vocabulary includes:

- `actor`
- `current_target`
- `ability.target`
- `effect.source`
- `effect.target`
- `encounter.allies`
- `encounter.enemies`
- `encounter.other_enemies`
- `room.players`
- `room.mobs`

`room.players` and `room.mobs` are physical actor-kind selectors.
`encounter.allies` and `encounter.enemies` are combat relationships and may
contain either players or mobs.

Selector results should be built from the preloaded round snapshot. Components
must not issue a database query per participant or target.

The engine should also preserve these rules:

- an area effect resolves against one frozen, deterministically ordered target
  set unless its definition explicitly retargets between hits
- encounter-scoped effects end when their encounter participation ends
- character-scoped effects may persist after flee or encounter completion
- a harmful periodic character effect may preserve combat tagging without
  allowing cross-room targeting
- taunt is an explicit forced-target primitive
- concealment prevents new acquisition according to its policy but does not
  automatically end an otherwise valid encounter
- mob AI submits the same typed intents players submit; it does not bypass
  ability validation or effect hooks

AI conditions must use the shared condition DSL over the round snapshot. The
resolver must not evaluate arbitrary builder scripts or perform ad hoc queries
for every mob turn.

## Parties, Cohorts, Factions, And PVP Teams

These group concepts have different lifetimes and must not be collapsed:

| Concept | Purpose |
| --- | --- |
| Party | Persistent or session-level player association and assistance/reward policy. |
| Spawn cohort | Concrete mobs created together for authored encounter behavior. |
| Faction | World identity, diplomacy, and reputation. |
| PVP team | Match-scoped authorization and victory grouping. |
| Combat side | Concrete alliance inside one active encounter. |

WR2 should introduce a real `Party`/`PartyMember` model when party gameplay is
implemented. Player following is movement behavior, not authoritative party
membership.

A party may supply automatic assistance and initial side affinity, but party
membership does not grant PVP consent. Conversely, players on different match
teams remain hostile even if some broader faction policy says they are allied.

Current duel contestants and their team numbers should map into encounter sides
through the same admission API. A team match finishes when only one authorized
contestant team remains, not merely when any one participant leaves the room.
Match result policy remains match state, not generic encounter state.

## Flee, Movement, Death, Despawn, And Disconnect

Leaving combat removes or deactivates a participant; it does not automatically
finish the entire encounter.

- **flee**: the actor spends the configured preparation/action window, leaves
  the spatial encounter on success, and moves through normal route policy
- **ordinary movement**: is blocked or follows existing combat-exit policy;
  it must not silently strand participant state
- **death**: marks that participant defeated and runs actor lifecycle handling
  once
- **despawn**: removes the mob participant and preserves required event/reward
  snapshot data
- **disconnect**: does not itself count as escape while the player's spatial
  state remains valid

### Disengage

The current `disengage` behavior for a `fights_back: false` mob remains
available, but it becomes participant-aware. `disengage <target>` clears the
requester's hostile intent and current target toward that participant. If the
target does not fight back and no other active opponent still targets it, has a
committed hostile intent against it, or maintains an encounter-scoped harmful
effect on it, the resolver deactivates that target participant.

This removes the inert participant, not a side-wide relation. Other members of
either side keep fighting. If the target fights back or another participant is
still engaging it, disengage does not provide an escape; the actor must use
normal target choice, movement policy, or flee. A bare `disengage` may continue
to mean the actor's current target for command compatibility.

After any exit, the encounter revalidates targets and hostile connectivity. If
the Greek and headsman can still fight after the player flees, their encounter
continues. If no hostile relationship remains, it finishes.

## Rewards, Quest Credit, And Loot

The current single-player reward assumption must be replaced before allied mobs
or multiple players can deal killing blows.

The encounter should keep bounded contribution summaries rather than an
unbounded damage log. At minimum it should distinguish damage, healing/support,
control, tanking/forced attention, and active participation where those values
affect policy.

Recommended default PVE behavior:

- eligible player contributors on the victorious side receive quest kill
  credit once
- eligible nearby party members may share credit according to explicit party
  policy
- an allied mob landing the final hit does not steal player credit
- total experience and currency awarded for one mob remain bounded by that
  mob's declared worth, then are split or scaled by policy
- loot is generated once per defeated mob; ownership or party-roll policy is a
  separate decision from kill credit
- rewards and credit are idempotent under resolver retry

A participant's bounded contribution summary survives deactivation until the
encounter's reward decisions finish. World policy may therefore credit a player
who contributed and then fled before an allied mob landed the final blow; that
choice does not depend on the player row still being active in the room.

An NPC-only defeat with no eligible player contribution grants no player
experience, currency, or quest credit. By default it also creates no persistent
loot object, avoiding unattended combat as a source of room clutter or passive
farming. An authored objective or explicit unattended-simulation policy may
override loot/lifecycle behavior within its own bounded cleanup contract.

The event contract may emit one idempotent credit event per player or one
structured event containing all credited players. Whichever representation is
chosen must support quest predicates without querying every participant again.

World or instance policy may later choose among contributor, surviving-side,
party-share, or killing-blow modes, but one explicit policy must own experience,
currency, quest credit, and loot decisions. Final-hit ownership must not be an
accidental consequence of action ordering.

PVP rewards and match outcomes remain under match policy and must not flow
through ordinary PVE mob-worth distribution.

## Events And Client State

The current client-facing scalar target is not enough to render a group fight.
The server should publish a visibility-aware, viewer-filtered, versioned
encounter snapshot plus ordered deltas. It must not expose concealed
participants, private intents, or internal threat/AI state to unauthorized
viewers.

Example snapshot:

```json
{
  "encounter_id": 123,
  "state_revision": 9,
  "projection": "player.7",
  "stream_sequence": 42,
  "round": 4,
  "self": "player.7",
  "participants": [
    {
      "key": "mob.1883",
      "side": 2,
      "relation": "enemy",
      "health": 391,
      "health_max": 500,
      "status": "active",
      "current_target": "player.7",
      "effects": []
    }
  ]
}
```

`self` may be null for a permitted observer. `relation` is computed relative to
the viewer and may be `self`, `ally`, `enemy`, or `neutral`.

Recommended delta events include:

- participant joined, left, defeated, or updated
- side or allied/hostile relation changed
- current/effective target changed
- intent queued, replaced, rejected, or consumed
- effect applied, advanced, or removed
- round started and resolved
- encounter merged, split, or finished

Every event should carry encounter id, global state revision, and a sequence
for the viewer's projected stream. Action-result events should also carry an
idempotent canonical action/event identity. The client cursor is
`(encounter_id, projection, stream_sequence)`, so several ordered deltas emitted
by one state transition are not mistaken for duplicates. Clients discard cursor
duplicates and request a fresh snapshot after a gap in their own projection.

State revision is a snapshot-freshness marker, not a client gap cursor. A
private intent or concealed-actor delta may advance global state without being
visible to another viewer; that omission must not create a gap in the other
viewer's stream. A reconnect or changed visibility projection establishes a new
cursor from a fresh snapshot.

The transactional outbox stores one canonical ordered event batch with audience
metadata. The publisher batches recipients, filters/renders that canonical
batch, and assigns projected stream sequences without writing one gameplay
outbox row per viewer.

A merge event on a donor encounter includes the canonical encounter id and a
snapshot handoff for the viewer's new projection. A split event lists the
retained encounter and visible child encounter ids. The client then requests
snapshots for the new topology instead of trying to infer reparenting from
actor messages.

Combat narration needs actor, target, ally, enemy, and observer variants so the
same action remains readable to everyone in the room. Publication should batch
room recipients and render recipient-specific text without rerunning combat
logic.

The initial UI can retain one prominent current-target card while adding compact
ally and enemy rosters. It should show who each visible combatant is targeting,
their key statuses, and enough encounter/round identity to group narration
correctly.

Legacy one-target payloads may be translated during a short client transition,
but the server should have one canonical multi-participant event model rather
than long-lived dual combat state.

## Transactions, Locking, And Idempotency

Multi-participant combat creates more opportunities for deadlocks. Every writer
that can touch encounter topology, movement, death, abilities, effects, rewards,
or match state must share one lock coordinator and lock order.

Target lock order:

1. authoritative instance run, when required
2. PVP match, when required
3. affected encounters in ascending id order
4. sides, side relations, and participants in ascending id order
5. player actor rows in ascending id order
6. mob actor rows in ascending id order
7. effects, inventories, wallets, reward records, and other dependent rows in a
   stable documented order

Routine intent submission should update only the acting participant and the
minimum encounter readiness/state-revision data where possible. Replacing an
intent increments client-visible state revision but does not change schedule
generation, so it cannot invalidate an otherwise valid resolver task. Intent
submission should not lock every actor merely to replace a queued choice.

The existing one-on-one PVE path intentionally uses a player-first lock order.
The target order above therefore cannot be introduced piecemeal. The unified
resolver phase must move all relevant writers to the shared coordinator in one
coherent change; until then, current one-on-one writers retain their existing
order.

Encounter creation has no row to lock initially. It should use database
uniqueness for active actor participation, retry on a uniqueness race, then
re-read and merge the winning topology. An application-only "check then
insert" is insufficient.

After acquiring the complete lock set, every command or resolver revalidates
scope, match authority, participation, target, side relation, logical round,
and topology before mutation.

Every resolver invocation carries an expected schedule generation and logical
round. Schedule generation changes only when due-state ownership changes, such
as rescheduling, pausing, merging, splitting, or finishing. State revision is
independent and increases for every client-visible committed mutation. State
changes and ordered outbox rows commit together. Deadlock and serialization
failures may retry with bounded backoff; gameplay or programming failures must
not be blindly retried.

Cross-row encounter ownership also needs database enforcement. A plain Django
foreign key cannot prove that a participant's side and current target, or both
endpoints of a side relation, belong to the same encounter. The schema should
either remove redundant encounter ownership where it can be derived safely or
use deferred PostgreSQL constraint triggers/composite constraints to enforce
same-encounter membership at commit. Service validation remains useful for
errors, but it is not the final concurrency guard.

## Performance And Scalability

The hot-path target is proportional to the materialized encounter graph and
work in one encounter:

```text
O(participants + side_relations + effects + actions)
```

Avoid behavior proportional to every possible player/mob pair or every actor in
the world.

Ordinary group fights should have few sides: a 16-versus-16 fight with two
sides has one materialized relation, not 256 actor-pair records. A dense
free-for-all can require `O(side_count²)` relations, so free-for-all side count
needs its own lower cap or a future compressed complete-hostility policy.

Required implementation practices:

- one scheduled job per encounter
- batched actor, faction, participant, effect, and ability loads
- no N+1 queries during target selection, AI, effects, rewards, or publication
- an indexed active-participant lookup for both player and mob actors
- indexed encounter due-state recovery
- a per-world/version cached diplomacy matrix or equivalent precompiled policy
- no manifest parsing or faction relationship queries inside each turn
- deterministic candidate grouping before relationship evaluation
- hard participant, effect, reaction-depth, event-volume, and work-per-task
  limits; initial combat continuation occurs only between atomic rounds
- batched room event publication rather than one independent publish operation
  per observer and combatant
- bounded contribution summaries rather than unbounded combat logs in mutable
  encounter rows

Encounter merges should be uncommon and explicitly measured. They must not
require a global search for related fights.

Representative performance tests should measure both query count and elapsed
work for at least:

- a 2-versus-3 encounter
- a 16-versus-16 encounter near the initial supported cap
- many independent small encounters resolving concurrently
- concurrent attempts to join or merge the same encounter

Exact caps should be selected from measurements, not guessed. Larger authored
battles are not admitted merely through configuration. They require the future
frozen-round continuation state machine and publication barrier to be
implemented and proven first.

## Observability

Combat metrics should include:

- active encounters by pacing mode
- participants, sides, and allied/hostile relations per encounter
- round resolution duration and database query count
- scheduler queue lag and overdue deadline count
- lock wait, deadlock retry, and serialization retry counts
- stale/duplicate resolver no-op count
- encounter creation, join, merge, split, and finish counts
- events and recipients published per round
- reward idempotency conflicts
- participant/effect/reaction cap hits

Structured logs should carry encounter id, state revision, schedule generation,
round, runtime-world id, room id, and task/action id. They should not log entire
actor snapshots or private player state by default.

## Implementation Sequence

### Phase 1: Contract And Truth Tables

- settle actor eligibility, relationship, aggression, retaliation, assistance,
  PVP authorization, target validity, and victory truth tables
- define the canonical snapshot and delta event schema
- define reward eligibility and initial participant caps
- document the shared lock coordinator

### Phase 2: Additive Runtime Schema

- add sides, side relations, expanded participants, current targets, initiative,
  eligibility round, snapshots, and required constraints/indexes
- add canonical state revision, projected stream sequencing, schedule
  generation, and task idempotency data
- enforce cross-row same-encounter integrity at the database boundary
- keep current one-on-one behavior running while the new schema is not yet
  authoritative

### Phase 3: Unified Resolver Parity

- resolve current one-player/one-mob PVE and two-player duels through the
  participant pipeline
- move all encounter writers to the unified lock coordinator atomically
- unify deadline recovery and transactional outbox behavior across modes
- prove deterministic parity before enabling larger topology

### Phase 4: Multiple Hostile Mobs And Target Switching

- allow several mobs and players in one PVE encounter
- implement per-participant faceoff, retargeting, late join, and participant
  exit behavior
- implement partial multi-side relations, merge, and round-boundary split
- implement the minimum bounded contribution, reward, quest-credit, and loot
  policy before more than one actor can receive or steal a kill
- replace pairwise scheduling with one job per encounter

### Phase 5: Actor-Neutral Aggression And Assistance

- implement mob-versus-mob admission and turns
- add explicit combat assistance and faction-relationship overrides
- trigger bounded room reconciliation from relevant state changes
- add NPC-only activity leases, pause/reactivation, and scheduler backpressure
- make the freed Greek scenario pass with and without a player present

### Phase 6: Parties, Advanced Group Rewards, Abilities, And UI

- introduce real party membership and party assistance policy
- extend the minimum reward contract with party sharing and richer contribution
  policy
- add ally/enemy and area selectors for players and mobs
- ship multi-participant snapshots, deltas, narration, and rosters

### Phase 7: Team PVP And Advanced Control

- map multi-player PVP teams onto encounter sides
- add threat strategy, taunt, concealment, and allegiance-changing mechanics
- exercise already-supported multi-side relations under team and free-for-all
  match policy
- measure and tune maximum supported encounter sizes

### Phase 8: Remove Compatibility State

- remove direct one-player/one-mob encounter ownership and old resolver paths
- remove legacy payload translation after supported clients migrate
- update builder and player guides with the final configuration and mechanics

WR2 launches with a clean database. This sequence is not a WR1 runtime data
migration, dual-write cutover, or active-combat backfill. Active WR2 encounters
are transient and should be drained or explicitly finished at a deployment
boundary if a schema phase cannot preserve them safely. The only WR1 bridge is
the optional authored-world manifest converter, whose notes must be updated
when the new authored combat contract is implemented.

## Test Matrix

All new backend tests belong under `backend/tests/`.

### Formation And Topology

- one player versus one mob retains existing behavior
- two players engage one mob concurrently and produce one encounter
- one player engages three mobs and each eligible participant acts once
- two existing encounters merge under concurrent cross-attack without
  duplicate active participants
- three sides may have partial hostility without attacking neutral sides
- disconnected hostile components split only at a round boundary
- stale donor tasks cannot advance a merged encounter

### Freed Greek Acceptance Tests

1. A captive Greek and present headsman do not engage while the condition is
   false.
2. Releasing the Greek with the headsman present forms or joins one encounter
   with them on hostile sides.
3. A headsman entering after release produces the same topology.
4. If the player is already fighting the headsman, the assisting Greek joins
   the player's side and receives exactly one eligible turn.
5. If no player is present but the room still has an activity lease or authored
   unattended-simulation policy, the released Greek and headsman can form an
   NPC-only encounter.
6. If the player flees or dies, the two mobs continue while hostility remains.
7. If either mob's policy or relationship becomes neutral before engagement,
   no special-case trigger forces combat.
8. When an unattended lease expires, an unfinished NPC-only fight pauses
   without recurring jobs and resumes deterministically when the room becomes
   active again.

### Turns And Targeting

- late join does not reroll existing initiative or act in the frozen round
- explicit target, current target, forced target, and fallback precedence are
  deterministic
- target death, flee, movement, concealment, and side change revalidate before
  impact
- changing target does not create a new encounter or grant an extra turn
- every participant advances effects and cooldowns exactly once per round
- bounded reactions cannot recurse indefinitely

### Exit And Completion

- one participant fleeing does not finish a fight that still has hostility
- death/despawn removes only the relevant participant and preserves event
  snapshots
- disconnect does not count as a spatial escape
- reused authored room ids in separate instance runs never share an encounter
- match victory and generic encounter completion remain distinct

### Abilities And Rewards

- ally selectors include allied players and mobs and exclude every enemy side
- enemy area selectors work across more than one hostile side
- mob AI abilities use the same validation and effect hooks as player abilities
- an allied mob's killing blow still grants eligible player quest credit
- total experience/currency remain bounded and reward records are idempotent
- loot is generated once under retry or duplicate task delivery

### Concurrency And Reliability

- duplicate resolver tasks cannot advance the same round twice
- a lost normal ETA task is recovered from durable due state
- simultaneous engage, flee, death, and resolver actions respect lock order
- manual readiness advances one round once, even with concurrent submissions
- immediate mode yields after bounded batches
- outbox events match committed state under worker loss
- a dirty reconciliation generation arriving under an active lease is not lost
- NPC-only scheduler limits apply backpressure without delaying observed fights
- a private or concealment-filtered delta does not create a gap in another
  viewer's projected stream

### Performance

- enforce representative query-count budgets for 2-versus-3 and 16-versus-16
  encounters
- verify one scheduled task per encounter rather than per hostile pair
- exercise many independent encounters without global room/world scans
- measure merge contention and ensure retry work is bounded

When frontend work begins, add or update unit tests for snapshot/delta reduction,
target selection, ally/enemy rosters, merge/split handling, and stale-cursor
recovery. Include the required UI screenshots with that implementation.

## Decisions Made By This Proposal

This proposal settles the following architectural direction:

- one connected fight is one encounter
- all combatants are participants, regardless of actor kind
- encounter-local sides and explicit allied/hostile relations represent combat
  relation, with absent relations remaining neutral
- faceoff/current target is per participant
- PVE and PVP share one resolver
- aggression, retaliation, assistance, relationship, and PVP permission remain
  separate policy
- NPC-only combat is supported
- encounter formation is actor-neutral, event-driven, and idempotent
- one task advances one encounter
- conditions use the existing WR2 condition DSL
- group rewards use explicit contribution/party policy rather than final-hit
  accident

The following balancing and product choices remain deliberately deferred until
their implementation phase:

- exact threat weights and mob target strategies
- exact participant and reaction caps
- exact party reward split formula
- whether directional authored faction relationships need a builder UI in the
  first release
- final group manual-readiness UX
- final roster layout and narration density

Those choices can change without returning to pairwise encounter storage or a
separate mob-versus-mob engine.
