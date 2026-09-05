# Multi-Participant Combat Encounters

Status: initial two-sided implementation; later extensions remain proposals.

## Implemented Boundary

The initial implementation uses `combat_encounters.py` for admission and lock
coordination, `combat_rounds.py` for PVE/PVP/NPC turns, and
`combat_reconciliation.py` for durable room checks. Participant rows own targets,
intents, initiative, and contribution counters. The obsolete encounter-level
player/mob ownership and intent fields have been removed. Ordered events and
filtered complete roster snapshots share the state transaction's outbox.

Current policy is deliberately narrow:

- two sides; existing allies must be mutually compatible; player hostility
  requires an active duel with opposing contestant teams
- at most 32 participants, 256 active effects across locked actors, 16 reactive
  applications per round, and 1,024 generated round events; oversized admission
  rejects atomically and effect/event overflow rolls the transaction back
- manual rounds wait for connected player participants' readiness; disconnected
  participants retain membership; manual duels preserve either-contestant
  advancement, and NPC-only manual fights require explicit steps
- NPC-only paced fights pause when their 30-second observation/activity lease
  expires and resume on renewed room activity
- positive damage to the defeated mob qualifies an active, living opposing
  player for equal XP/currency shares and quest credit; deterministic remainders
  preserve totals, one receipt protects rewards, and loot rolls once
- no eligible player contributor means no reward or loot corpse; party-wide
  sharing, departed-player credit, and healing contribution are deferred
- room reconciliation pages 8 sources against 32 targets, admits at most 8
  changes per task, and saves its cursor and dirty generation for continuation
- ordinary joins preserve due time and schedule generation; merges, pause,
  completion, and resolver advancement invalidate stale scheduled work

Ordinary instance combat takes a shared run lifecycle lock before encounter
locks; teardown/reset takes an exclusive run lock. Duels take exclusive match
and run authority. Actor and membership discovery is revalidated after locking,
with up to three retries for topology, uniqueness, deadlock, or serialization
races. Commands currently lock their bounded encounter together; narrowing
intent-only locks is a future optimization, not a second locking protocol.

The query regression probe measures ordinary rounds at 2, 8, and 32 participants.
The current measurements are 37, 79, and 247 queries respectively, with the
32-participant round taking roughly 0.3 seconds in the local container test run.
Policy comparisons themselves are query-free after the bounded policy load.
These probes establish linear query growth for this scenario; they do not prove
fleet throughput or a production latency budget. Deployment load testing must
still measure many simultaneous encounters, instance lifecycle contention,
crowded rooms, and publication fan-out before raising capacity.

The migrations explicitly finish transient WR2 encounters, refund reserved flee
stamina, and remove encounter effects before replacing pair ownership. They are
validated in a disposable database. Applying them to a populated development or
production runtime requires a deployment boundary; this is not a WR1 state
migration. See the player [combat guide](../guides/players/combat.md) and builder
[mob guide](../guides/builders/mob-definition-builder-guide.md) for the enabled
behavior. The remainder of this document records the design, rationale, and
future release checks; later-extension sections do not describe enabled code.

This document extends the encounter-scoped combat direction in
[combat-encounter-model.md](combat-encounter-model.md) so one fight can contain
multiple players and mobs on multiple sides. It defines the long-term runtime
shape below, with a smaller initial release defined separately:

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

## Initial Release Boundary

The first usable group-combat milestone is the freed-Greek scenario, backed by
ordinary two-sided group combat. It includes:

- multiple combatants of either actor kind across two opposing sides
- parity for existing one-on-one PVE and two-player duels
- per-participant turns, targets, intents, effects, and exits
- compatible encounter merges within measured hard limits
- actor-neutral aggression, explicit assistance, and bounded NPC-only activity
- minimum contribution, quest-credit, reward, and loot rules
- viewer-filtered snapshots, readable narration, basic ally/enemy rosters, and
  the corresponding builder/player guides

Keep `CombatSide` and `CombatSideRelation` as the extensible representation.
Initially an active encounter has two sides and one hostile edge. Admission
must preserve that shape and reject an engagement that would require a third
side, a side split, or an unauthorized relationship. A shared enemy alone does
not make two actors allies. These are explicit initial product limits, not
permission to misrepresent relationships to fit the schema.

Multiple independently hostile sides, alliances between separate sides,
allegiance changes, and automatic side/encounter splitting are later extensions.
Real parties, advanced contribution formulas, and a viewer-specific delta
protocol also follow demonstrated gameplay or performance needs. Sections
describing those extensions establish constraints for later work; they are not
prerequisites for the first group fight.

The first release still requires durable due state, idempotent resolution,
transactional events, coordinated locking, bounded work, and measured query and
publication costs. Reducing feature scope does not remove those guarantees.

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
3. A participant's current hostile target cannot be itself or an inactive
   participant. Abilities may still legally target their caster.
4. A participant's hostile target must belong to a side connected to its side
   by an active hostility edge.
5. Participants on one side share alliance and victory identity and have the
   same admitted relations to other sides. Minimizing the number of sides is
   not itself a runtime invariant.
6. Each participant becomes eligible for at most one primary action per round.
7. Effects, cooldowns, and durations advance at most once per logical round.
8. Duplicate or stale resolver delivery cannot advance a round twice.
9. The encounter finishes only when it contains no active hostile relationship
   that can still produce combat.
10. Encounter mutations and their canonical events commit atomically.
11. Spatial combatants share the same runtime-world and room scope. Reused
    authored room ids in different instance runs do not make actors colocated.
12. PVP authorization is checked before players are placed on hostile sides.
13. Admission never exceeds the supported topology or hard encounter limits.
    Rejected admission leaves existing fights and actor state unchanged.

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
- monotonic state revision for client snapshots and future deltas
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
the actor's own side and sides directly connected by that relation. Alliance
is not transitive: an ally of an ally is not automatically a valid ally target.

The initial release materializes only the two opposing sides' hostile edge.
The same schema can later support:

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

The database should enforce exactly one live actor reference for an active
participant and prohibit both actor references on any row. Partial uniqueness
constraints prevent an active player or mob from belonging to two encounters.
A paused encounter retains active membership for this constraint.

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

If actor deletion clears a participant's live foreign key, first deactivate the
participant and retain its immutable identity and unfinished contribution data
in the same transaction. The inactive-row constraint must allow this snapshot
form. Cascading away the only reward record, or requiring a live foreign key
after deletion, would defeat the retention contract.

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

The target precedence is below. The initial release evaluates only implemented
policy sources; future party and allegiance mechanics do not need placeholder
engines or configuration:

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
allegiance or cease-hostility mechanic may later change topology at a round
boundary, once the advanced topology contract is implemented.

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
| One actor is in combat | Admit the other actor to a compatible existing side, subject to policy and limits. |
| Both actors are in the same encounter | Confirm their hostile relation and update target/intent state. |
| Actors are in different encounters | Merge only if the combined fight has a legal two-sided assignment and fits the limits. |

The operation must be safe under duplicate delivery and concurrent engagement.
It should return the canonical encounter and whether topology changed.

Actors can merge only when their runtime-world, room, instance, pacing, and
match authorities are compatible. An action that would bridge unrelated PVP
matches or an isolated match and ordinary room combat is rejected unless an
explicit match policy defines that transition.

### Admission Limits And Rejection

Before any join or merge commits, the engagement service validates the complete
proposed result under the encounter/actor locks. This includes active
participants, sides, effects, retained reward state, and the work needed for
one atomic round. Two individually valid encounters are not necessarily safe
to merge. The effective limit is the operator ceiling or any stricter applicable
world/match policy.

If the proposed result exceeds a limit or requires unsupported topology, reject
the engagement before changing membership, targets, intents, resources, damage,
or schedules. Explicit commands receive a clear capacity or unsupported-topology
failure. Automatic aggression/assistance leaves the candidate out of the fight
and records the reason; it does not repeatedly reschedule an unchanged rejection.
A relevant capacity or policy change can make the candidate eligible on a later
reconciliation. An excluded actor cannot deal damage into that encounter through
a separate pairwise fight.

Merges admit all affected active participants or none. Do not evict existing
combatants, truncate an area target set, or raise the cap to force an admission.
An area action that engages outsiders must validate all required admissions
before spending resources or applying any of its impacts. Later effect creation
also obeys explicit effect/reaction limits; admission alone cannot bound effects
that are added during subsequent rounds.

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

The initial implementation should process a capped set of changed actors
against stable candidate pages:

1. claim a durable room reconciliation lease and freeze its dirty generation
2. load one bounded candidate page plus the faction/diplomacy snapshot in
   batches
3. compare changed actors with that page rather than rebuilding every possible
   room pair, evaluating each changed/resident pair in both possible initiation
   directions
4. evaluate initiation and assistance deterministically in memory without
   per-candidate queries
5. apply a bounded number of complete admissions through the idempotent
   engagement service, revalidating each proposed result
6. commit progress and schedule each changed canonical encounter once

A participant cap does not bound nonparticipants in a crowded room. The
reconciler therefore needs independent limits for changed actors, candidate
page size, admitted topology changes, and work per task. If the changed-actor
set overflows, preserve a full-room-dirty marker and resume a bounded traversal
rather than dropping changed actors or doing an unbounded transaction. Page
cursors and ordering must let a finite pass finish once room state settles;
new dirty generations must not continually starve its later candidates.

The contract is bounded work, no lost updates, and eventual consideration of
eligible candidates. Grouping by faction, spawn cohort, or match team is an
optimization to select from crowded-room measurements, not a required generic
relation-signature compiler. Grouping may prune only comparisons that policy
proves equivalent or ineligible. Actor-specific reputation and condition DSL
expressions can still require pair-specific evaluation, and a full pass can
remain quadratic in the worst case. Measure total comparisons and queue lag as
well as per-task time; paging alone does not make excessive total work cheap.

The durable coordination state should include at least dirty generation,
applied generation, continuation cursor, and lease expiry. A mutation arriving
while a worker runs increments dirty generation. When the worker commits, it
requeues if a page remains or the dirty generation advanced; it must not clear
the newer signal. Expired leases are recoverable by the normal scheduler.

Each admission commits a complete topology mutation under the same locks as the
resolver. Rounds may run between pages; stale candidate data must be revalidated
on admission. No continuation leaves half an encounter round committed.

A world-wide diplomacy edit increments a versioned policy snapshot. It must not
synchronously fan out one transaction or task for every actor in the world.
Active rooms observe the new version on their next relevant event or through a
bounded reconciliation queue.

### Joining A Side

An assisting actor joins an existing side only when it is mutually allied with
every active member, shares the same encounter victory/reward identity, and can
legally take on that side's hostility to the entire opposing side. An explicit
temporary-alliance policy may supply this affinity; a shared attack target alone
does not. Check all newly implied player hostility through PVP authorization,
not just the two actors named in the initiating command.

If the actor needs different relationships, reject the initial-release join.
Do not infer alliance from neutrality, split an existing side, or quietly add a
third side. The later multi-side extension may add a separate side and explicit
relations once its admission, support, reward, and split behavior is implemented.

### Merging Encounters

An action can connect two formerly independent fights. Initially a merge is
supported only when the two source sides can be mapped onto two resulting
sides while preserving existing relations, alliance/victory identity, and all
new admission permissions. This is a bounded side-assignment check, not a
general graph repartitioning operation. The merge protocol should:

1. lock all affected encounter ids in ascending order
2. revalidate scope, two-sided assignment, permissions, and combined capacity;
   reject the whole engagement on failure
3. select a canonical encounter, normally the lowest id, and move participants,
   intents, effects, and retained contribution state to the mapped sides
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
11. invalidate source schedules, establish one canonical schedule generation,
    increment the canonical state revision, and emit a merge event plus snapshot

Old resolver tasks carry the source id and schedule generation and no-op.
Donor pointers support client/command lookup; following one does not authorize
an old task to resolve the canonical encounter. Manual readiness is recomputed
over the merged participants, and a merge itself grants no extra turn.

### Connectivity And Future Splitting

The initial release does not split encounters. With two fixed sides and one
hostile edge, ordinary participant exits leave one fight or no remaining
hostility. Resolve the remaining fight or finish it; do not introduce child
encounters as a cleanup requirement.

Before enabling partial multi-side relations or allegiance changes, define
connected components over active sides using both hostile and direct allied
edges. A healer on a separate allied side stays connected to the fight even if
that side has no hostile edge of its own. Connectivity does not make alliances
transitive or grant new targeting permissions.

Live effects or control mechanics that require shared resolution across sides
also prevent separating those sides until the dependency ends or has an
explicit transfer rule. Historical provenance alone is not a dependency;
target-owned character effects can already outlive their source's participation.
Dependencies preserve coherent resolution, but do not keep an encounter alive
once no hostility can produce combat.

If splitting is later required, perform it only at round boundaries. Components
with no combat finish; other components retain or receive an encounter with
their participants, intents, effects, and remaining timers. Specify reward
ownership, deterministic child identities/seeds, schedule invalidation, and
client snapshot handoff in that implementation. These mechanisms are deferred
together rather than partly implemented in the initial resolver.

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
continue for other participants. A temporarily untargetable enemy does not by
itself end the fight.

### Existing Target Priority

`target_priority` remains useful as a player-facing or AI fallback preference,
for example choosing a shieldbearer before an archer. It should not become a
hidden replacement for threat, assistance, or side assignment.

## Initiative And Joining Mid-Round

Initiative is rolled or derived once when a participant joins and remains
stable unless an explicit mechanic changes it. Stable actor id or join sequence
breaks ties deterministically.

Joining and round resolution take the same encounter lock. A join committed
before the next round freezes can participate in that round; a join racing an
already resolving round waits for its commit and is first eligible in the next
unresolved round. Store that eligibility explicitly as `current_round + 1`,
where `current_round` is the last committed round at admission. Topology cannot
become targetable halfway through an atomic round.

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
12. finish the encounter if no remaining hostility can produce combat
13. persist mutations and ordered outbox events in the same transaction
14. establish one next due resolution if the encounter remains active and its
    pacing/readiness/activity policy permits advancement

Randomness should be reproducible from encounter seed, logical round,
participant id, and action ordinal. Retrying the same logical round must produce
the same decisions and rolls.

Reactive effects need a bounded depth or explicit work queue so reflection,
counterattack, or on-hit chains cannot recurse indefinitely in one transaction.
The later multi-side extension may add boundary splitting before persistence;
the initial resolver has no split state machine.

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
pair or mob. This means one logical due resolution; recovery or broker retries
may deliver redundant tasks, which the generation/round check rejects.

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

Use the existing scheduler/queue facilities to bound NPC-only work and reserve
capacity for player-observed and objective-critical fights. Start with the
activity gate, bounded due batches, and measured queue lag; add more elaborate
per-world scheduling only if these controls cannot meet the load target.
Authored unattended simulation still obeys operator limits. A paused encounter
retains actor membership, so detached effect scheduling must not independently
advance its participants' combat clocks.

## Abilities, Effects, And Target Selectors

Abilities and effects use the same actor-neutral validation and effects pipeline
from the initial release. Add relational selectors as the abilities that use
them ship; a complete area-ability and control vocabulary is not a prerequisite
for ordinary group attacks.

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

New threat strategies, taunt, concealment mechanics, and allegiance changes are
later work. Any such rules already implemented by an existing ability still
need parity during resolver replacement; deferral does not remove current
behavior.

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
unbounded damage log. Start with the counters consumed by the chosen initial
reward policy. Damage, healing/support, control, tanking/forced attention, and
active participation are possible inputs; do not build a scoring engine for
unused categories. Define attribution per defeated mob and across merges before
enabling group rewards, so contribution to an unrelated earlier opponent does
not accidentally earn every later kill.

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
The initial server should publish complete, viewer-filtered, versioned encounter
snapshots after rounds, topology changes, and other public combat-state changes.
Private intent acknowledgements can use the existing command/event path without
rebroadcasting a roster. Do not expose concealed participants, private intents,
or internal threat/AI state to unauthorized viewers, including through target
references or effect-source metadata.

Example snapshot:

```json
{
  "encounter_id": 123,
  "state_revision": 9,
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

Within one encounter and viewing context, the client replaces its roster with
the newest complete snapshot and ignores older or duplicate revisions. Revision
jumps are valid: a private intent may advance canonical state without producing
a public snapshot, and a complete snapshot needs no preceding deltas. Reconnect,
re-entry, or a changed viewing context requests a fresh authorized snapshot and
resets the local view. Visibility-changing actor state must invalidate the view
even if it changed outside the combat resolver.

Action-result and narration events retain idempotent canonical event identities
and committed ordering. Do not deduplicate them by snapshot revision: several
different events may share one revision. Private intent acknowledgements carry
the accepted intent identity/revision so an older response cannot replace a
newer queued choice.

The transactional outbox stores one canonical ordered event batch with audience
metadata and the committed snapshot data needed for publication. The publisher
batches recipients and filters/renders that state without writing one gameplay
outbox row per viewer. A snapshot's revision must describe the state actually
serialized; do not read later mutable rows and label them with an older event's
revision. Coalesce superseded snapshots where safe while preserving canonical
action-result ordering.

A merge event on a donor encounter includes the canonical encounter id and a
fresh authorized snapshot or an instruction to fetch it. The client retires the
donor roster and ignores delayed donor snapshots. A finish event clears the
active roster. Future splits use the same explicit snapshot handoff rather than
requiring clients to infer reparenting from actor messages.

Combat narration needs actor, target, ally, enemy, and observer variants so the
same action remains readable to everyone in the room. Publication should batch
room recipients and render recipient-specific text without rerunning combat
logic.

The initial UI should retain one prominent current-target card while adding
compact ally and enemy rosters. It should show who each visible combatant is
targeting, their key statuses, and enough encounter/round identity to group
narration correctly. Ship this with the first enabled group encounters,
including player and builder documentation and frontend tests.

Legacy one-target payloads may be translated during a short client transition,
but the server should have one canonical multi-participant event model rather
than long-lived dual combat state.

### Optional Delta Protocol

Measure snapshot bytes, serialization time, publication lag, and recipient
fan-out at the supported encounter cap. Full snapshots simplify initial client
recovery but their cost grows with participants times viewers; they are not an
assumption of unlimited bandwidth. If measured budgets require deltas, introduce
them as a separate delivery change while preserving full snapshot recovery.

A filtered delta stream needs its own cursor, such as
`(encounter_id, projection, stream_sequence)`. Global state revision cannot
detect gaps because private or concealed changes are legitimately omitted for
some viewers. Assign ordered sequences per projection, deduplicate by cursor,
and fetch a snapshot after a real gap or projection change. Specify publisher
retry behavior and cursor lifetime when implementing this protocol. It is not
part of the initial runtime schema or a prerequisite for multi-participant turns.

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

The coordinator defines ordering, not a requirement to lock every row category
for every command. Exclusive instance/match locks belong to operations that
mutate shared lifecycle, membership, or result authority, or otherwise require
serialization with those transitions. Merely reading a participant's instance
identity is not sufficient reason to take an exclusive run lock every round.

Before enabling concurrent encounters in one instance, document how ordinary
rounds validate authority against teardown/completion without serializing all
fights on the run row. A compatible shared lifecycle guard is one option; any
chosen protocol must be used by both rounds and lifecycle writers and tested
under races. Where exclusive authority locking is necessary, measure its lock
wait and critical-section duration. Plan authority locks before lower-order
locks, including for deaths/results a round may produce; never acquire a run
lock late from a nested death handler.

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
the actual targeting and resolution work in one encounter:

```text
O(participants + side_relations + effects + candidate_checks + target_applications)
```

Count each area hit and reactive effect application as work, not merely one
queued ability. Query-free target selection can still be quadratic in CPU work;
reuse legal target rosters where rules allow and measure remaining candidate
checks. Ordinary single-target fights should approach linear work. Avoid
rebuilding every possible actor pair or scanning actors outside the affected
encounter/room.

Ordinary group fights should have few sides: a 16-versus-16 fight with two
sides has one materialized relation, not 256 actor-pair records. Future dense
free-for-all combat can require `O(side_count²)` relations and needs separate
measurements and a side cap before admission is enabled.

Required implementation practices:

- one scheduled job per encounter
- batched actor, faction, participant, effect, and ability loads
- no N+1 queries during target selection, AI, effects, rewards, or publication
- an indexed active-participant lookup for both player and mob actors
- indexed encounter due-state recovery
- a per-world/version cached diplomacy matrix or equivalent precompiled policy
- no manifest parsing or faction relationship queries inside each turn
- deterministic, bounded candidate traversal; grouping where measurements and
  policy equivalence justify it
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
- a 16-versus-16 encounter as a capacity probe, plus the measured release cap
- many independent small encounters resolving concurrently
- independent encounters sharing one instance, including lifecycle contention
- concurrent attempts to join or merge the same encounter
- crowded-room reconciliation with actor-specific conditions and repeated dirties
- snapshot publication with participants and observers at supported limits

Exact caps should be selected from measurements before admission is enabled,
not guessed or postponed until advanced PVP. Larger authored battles are not
admitted merely through configuration. Battles exceeding the measured atomic
round budget require the future frozen-round continuation state machine and
publication barrier to be implemented and proven first.

## Observability

Combat metrics should include:

- active encounters by pacing mode
- participants, sides, and allied/hostile relations per encounter
- round resolution duration and database query count
- scheduler queue lag and overdue deadline count
- lock wait, deadlock retry, and serialization retry counts
- stale/duplicate resolver no-op count
- encounter creation, join, merge, finish, and admission-rejection counts
- events, snapshot bytes, serialization time, and recipients published per round
- reconciliation comparisons, continuation backlog, and dirty-generation age
- reward idempotency conflicts
- participant/effect/reaction cap hits

Structured logs should carry encounter id, state revision, schedule generation,
round, runtime-world id, room id, and task/action id. They should not log entire
actor snapshots or private player state by default.

## Implementation Sequence

These are delivery milestones, not a requirement to hold one large change open
until every future combat feature exists. Within each milestone, use reviewable
changes and keep incomplete behavior disabled. Each enabled mechanic ships with
its tests and relevant builder/player guides.

### Milestone 1: Participant Resolver With Existing Behavior

- settle initial two-sided admission, PVP permission, target validity, readiness,
  reward eligibility, and capacity-failure truth tables
- add sides, relations, participant targets/intents/initiative, actor snapshots,
  state revision, schedule generation, and necessary constraints/indexes
- define the shared lock coordinator and instance lifecycle coordination before
  switching all relevant writers coherently
- route current one-player/one-mob PVE and two-player duels through the unified
  participant resolver, with deterministic behavior and existing ability parity
- use one durable encounter schedule and transactional event path across modes
- remove old authoritative pair ownership/resolver paths once parity and their
  callers are converted; temporary schema or payload compatibility exists only
  for that transition

This milestone must work without general graph splitting, party models, or
projected delta streams.

### Milestone 2: First Playable Group Combat And The Freed Greek

- admit multiple players/mobs on two sides and merge compatible fights within
  measured participant, effect, event, and atomic-round budgets
- ship per-participant targeting, late join, flee/death/disengage behavior, and
  explicit capacity/unsupported-topology failures
- implement minimum per-mob contribution, rewards, quest credit, and loot before
  allied mobs or multiple players can deal killing blows
- enable actor-neutral aggression, explicit assistance, faction policy, and
  condition-driven room reconciliation
- support NPC-only formation and continuation under the activity lease, with
  pause/reactivation and bounded scheduler work
- ship filtered snapshots, private intent acknowledgements, readable narration,
  and basic current-target/ally/enemy UI together
- update builder/player guides and the optional WR1 authored-content conversion
  notes when the assistance manifest contract is implemented
- pass the freed-Greek scenario with and without a player present, plus the
  concurrency, query-count, reconciliation, and publication budgets below

This is the first group-combat release. Its exit criterion is an authored,
playable scenario with reliable behavior under concurrent load.

### Later Extensions

Choose these independently when gameplay or measurements justify them:

- **Parties and richer rewards:** real party membership, assistance, sharing,
  and only the additional contribution categories consumed by policy.
- **Abilities and team PVP:** new relational/area selectors, threat or control
  mechanics, and multi-player match teams on the same participant resolver.
- **Multi-side topology:** partial hostility, direct alliances between separate
  sides, allegiance changes, and side/encounter splitting with effect/reward
  ownership and snapshot handoff tested together.
- **Delivery optimization:** a projected delta protocol if measured snapshot
  costs exceed budgets; preserve complete snapshot recovery.
- **Larger battles or crowded rooms:** stronger scheduling/reconciliation
  optimizations from profiles. Battles too large for atomic rounds require a
  separately designed continuation and publication contract.

WR2 launches with a clean database. This sequence is not a WR1 runtime data
migration, dual-write cutover, or active-combat backfill. Active WR2 encounters
are transient and should be drained or explicitly finished at a deployment
boundary if a schema phase cannot preserve them safely. The only WR1 bridge is
the optional authored-world manifest converter, whose notes must be updated
when the new authored combat contract is implemented.

## Test Matrix

All new backend tests belong under `backend/tests/`. The following initial
tests gate Milestones 1 and 2; later extensions have a separate matrix.

### Formation And Topology

- one player versus one mob retains existing behavior
- two compatible allied players engage one mob concurrently and produce one
  encounter
- one player engages three mobs and each eligible participant acts once
- two compatible existing encounters merge under concurrent cross-attack
  without duplicate active participants
- joins/merges that require a third side or side split reject without changing
  existing fights, resources, intents, damage, or schedules
- two encounters individually below the cap cannot merge above the cap
- concurrent admissions cannot exceed capacity after locked revalidation
- every newly implied player hostility is authorized, including other members
  of the two sides
- an automatic capacity rejection does not create an unchanged retry loop;
  a later capacity change permits reconsideration
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
- explicit target, current target, existing control effects, and fallback
  precedence are deterministic
- target death, flee, movement, and existing visibility rules revalidate before
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

- existing ability selectors and effects retain parity over player/mob actors
- any enabled area action validates outsider admission before any impacts and
  cannot bypass capacity or PVP policy
- mob AI abilities use the same validation and effect hooks as player abilities
- an allied mob's killing blow still grants eligible player quest credit
- total experience/currency remain bounded and reward records are idempotent
- loot is generated once under retry or duplicate task delivery
- contribution attribution remains correct per defeated mob across joins,
  exits, and merges
- actor deletion retains immutable identity and unfinished reward attribution
  without violating participant constraints

### Concurrency And Reliability

- duplicate resolver tasks cannot advance the same round twice
- a lost normal ETA task is recovered from durable due state
- simultaneous engage, flee, death, and resolver actions respect lock order
- manual readiness advances one round once, even with concurrent submissions
- immediate mode yields after bounded batches
- outbox events match committed state under worker loss
- a dirty reconciliation generation arriving under an active lease is not lost
- a full-room-dirty pass eventually covers later candidates once state settles
- pair-specific conditions remain correct with any candidate grouping
- NPC-only scheduler limits apply backpressure without delaying observed fights
- independent fights in one instance do not take an exclusive run lock merely
  to validate identity, and teardown/completion races preserve authority

### Client State And Publication

- complete snapshots replace state correctly after skipped revisions; private
  intent changes do not cause false gap detection
- hidden actors, target references, and private state remain filtered
- reconnect and visibility changes establish a fresh authorized view
- delayed donor snapshots cannot resurrect a merged encounter's roster
- duplicate action events are ignored by event identity, while different
  events sharing one revision remain visible
- snapshots describe the committed state matching their attached revision
- intent acknowledgements cannot replace a newer choice with an older response

### Performance

- enforce query-count and elapsed-work budgets for 2-versus-3 and the measured
  release cap, using 16-versus-16 as a capacity probe
- verify one scheduled task per encounter rather than per hostile pair
- exercise many independent encounters without global room/world scans
- measure merge contention and ensure retry work is bounded
- measure independent same-instance fights, crowded-room total comparisons,
  reconciliation backlog, and snapshot bytes/serialization/recipient fan-out

Frontend unit tests for snapshot replacement, target selection, ally/enemy
rosters, merge/finish handling, and reconnect recovery ship with Milestone 2.
Include the required UI screenshots with that implementation.

### Later Extension Tests

- partial hostility never authorizes attacks on neutral sides
- direct alliance does not imply transitive alliance or PVP permission
- an allied healer without its own hostile edge remains connected to the fight
- splitting waits for a round boundary and preserves shared effect/control
  dependencies, remaining timers, reward attribution, and client handoff
- allegiance changes preserve legal targets, turn eligibility, and effect clocks
- party contribution and team match results follow their explicit policies
- area selectors work across multiple hostile sides without unbounded work
- projected deltas recover from real gaps, preserve sequence identity under
  publisher retries, and do not mistake another viewer's private event for a gap

## Decisions Made By This Proposal

This proposal settles the following architectural direction:

- one connected fight is one encounter
- all combatants are participants, regardless of actor kind
- encounter-local sides and relation rows represent combat relationships; the
  initial release admits two opposing sides, with richer topology deferred
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
- initial client state uses filtered complete snapshots; projected deltas are a
  measured delivery optimization
- first group-combat delivery includes the freed Greek, basic UI, and guides

Before the first group release, measurements and product truth tables must
settle exact participant/effect/reaction limits, the minimum reward formula,
and manual-readiness defaults. These are release gates, not open-ended promises.
The following choices can wait for later extensions:

- exact threat weights and mob target strategies
- exact party reward split formula
- advanced side/encounter splitting mechanics and multi-side match rules
- whether diplomacy editing needs a dedicated builder UI beyond manifests
- richer readiness controls, roster layout, and narration density
- whether measurements justify projected deltas or specialized reconciliation
  and scheduling optimizations

Those choices can change without returning to pairwise encounter storage or a
separate mob-versus-mob engine.
