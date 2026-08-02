# WR2 Quest System End State

## Purpose

This document describes the desired end state for quests in WR2 once the new
system is fully implemented.

It is intentionally a replacement design, not an extension of the legacy quest
stack built around:

- `backend/builders/models.py:Quest`
- `backend/builders/models.py:Objective`
- `backend/builders/models.py:Reward`
- `backend/spawns/models.py:PlayerQuest`
- `backend/spawns/models.py:PlayerEnquire`

The new system should fit WR2's event-driven direction, align with the existing
YAML manifest workflow, and avoid carrying forward WR1-era assumptions like
"every quest belongs to one mob template" or "quest state is just a pair of
interaction timestamps."

## Design Summary

WR2 quests should be a declarative, event-driven storylet graph with three
separate concerns:

1. Discovery
2. Progression
3. Narration

That means:

- discovery decides what the player can currently notice
- progression decides what state a quest instance is in
- narration decides what the player reads in scenes, recaps, and the journal

## Non-goals

- No WR1 data migration strategy.
- No compatibility layer that keeps the old quest authoring model alive.
- No new quest content authored in legacy `conditions` strings plus imperative
  dialogue/completion command scripts.
- No inline code inside quest manifests.

## Core Concepts

### Opportunity

An opportunity is visible content the player can notice but has not committed
to yet.

Examples:

- a rumor surfaced by a bulletin board
- a room prompt that appears when you enter a shrine
- an NPC dialogue hook
- a world event phase that becomes relevant in the current area

Not every opportunity becomes an active quest.

### Quest Template

An authored quest definition stored in WR2 YAML and validated by schema. It is
world-scoped authored content and may represent:

- `quest`
- `contract`
- `world_event`

Arcs are authored separately as collections of quest templates.

### Quest Instance

A runtime record for a specific player, party, guild, or world scope.

It stores:

- the template being played
- current step id
- resolution state
- slot bindings
- local quest state
- active objective progress

### Step

A named node in the quest graph. Steps replace numeric stages.

Allowed step roles include:

- storylet
- objective
- branch
- timer
- resolution

### Objective

A generic event tracker, not a bespoke "fetch quest" or "kill quest" class.

An objective is defined by:

- what event it listens for
- how it filters those events
- how progress is accumulated
- how completion is determined

### Quest Item

A quest item is a typed item that exists to support quest progression, not a
generic shared world drop.

Examples:

- a packet handed to the player when they accept a courier job
- a relic picked up from a shrine during an active step
- a piece of evidence that must be turned in to an NPC

Important design rules:

- builders should not solve quest pickup problems by placing shared world items
  on the ground
- discovery-time quest pickups may be viewer-specific and effectively virtual
  until claimed
- once claimed, quest items should participate in normal inventory and turn-in
  flows where appropriate
- quest lifecycle rules, especially abandon/resolve cleanup, should be owned by
  the quest runtime rather than by ad hoc room-loader behavior

### Slot

A symbolic binding resolved at runtime.

Examples:

- `suspect`
- `witness`
- `hideout`
- `client`
- `stolen_item`

Slots let one engine support both fixed authored quests and generated
contracts.

### Resolution

Every resolved quest instance ends in one explicit state:

- `complete`
- `compromised`
- `failed_forward`
- `expired`
- `abandoned`

`failed_forward` is a first-class outcome, not an error case.

### Arc

A top-level grouping of related quests with its own summary, recap, and
completion state. An arc does not replace quest instances; it organizes them.

## Player Experience

### Discovery Buckets

The player-facing quest UX should have three buckets:

- `Opportunities`: visible but not yet accepted
- `Active Quests`: committed content with recap and objectives
- `Completed Stories`: resolved content with a readable ending summary

### Journal Tone

Each major step owns:

- `recap`: what happened

The journal should read like a strong memory aid, not a debug dump.

### Recap Command

WR2 should expose a `quest info` command backed by the same quest service used
by HTTP endpoints. The output should be short and contain:

- current recap
- visible objectives
- latest major journal entry

### Scope Rules

Quest scope defaults to `player`.

Shared scopes are opt-in:

- `party`
- `guild`
- `world`

World-mutating consequences should be rare and explicit.

## Authoring Model

### Builder Workflow

Authoring should follow the same broad pattern as existing WR2 trigger and
world manifests:

1. read current state in the builder UI
2. export/copy YAML
3. edit YAML
4. apply YAML in world editing flow
5. validate with linting, preview, and simulation

The builder should be world-scoped, not zone-scoped. The current zone-based UI
for legacy quests does not fit contracts, arcs, or world events.

### Manifest Shape

Quest authoring should use `kind: quest`.

Arc authoring should use `kind: questarc`.

At a high level:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: quest
metadata:
  world: world.1
  slug: poisoned_well
  name: The Bitter Well

spec:
  scope: player
  arc: ashwick_outbreak
  repeatability:
    mode: never
  max_active: 1

  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: mobdefinition.12
    visible_if: {}
    accept_if: {}
    salience: 80
    cooldown_seconds: 0

  slots: {}

  steps: []

  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

### Conditions and Effects

Quest manifests should use typed predicates and typed effects. They should not
author new content in the old string-based `conditions` format or in trigger
style command scripts.

Examples of predicates:

- fact checks
- mark checks
- event payload checks
- objective completion checks
- timer status checks
- quest state checks

Examples of effects:

- set fact
- set mark
- set local quest state
- grant item
- add currency
- change faction or reputation
- unlock another opportunity
- start a follow-up quest
- schedule or cancel a quest timer

Internally, early phases may adapt some of these to existing world facts and
player marks, but the authored quest format should already be the new typed
shape.

## Runtime Architecture

### Event-driven Progression

Quest progression should subscribe to the same canonical WR2 event stream used
by other downstream systems.

The runtime loop is:

1. relevant game events are emitted
2. discovery service reevaluates visible opportunities as needed
3. active quest objectives inspect matching events
4. objective progress updates
5. current step transitions are reevaluated
6. if a transition fires, the instance moves to the next named step
7. a journal entry is written
8. resolution effects are applied if the new step resolves the quest

This keeps quest logic out of individual command handlers and aligns with the
existing `Command -> Action -> Event` WR2 direction.

### Discovery Scoring

Discovery should support a simple salience score so the game can surface the
most relevant opportunities instead of dumping every possible hook at once.

Inputs can include:

- authored priority
- same room bonus
- same zone bonus
- same arc bonus
- urgency bonus
- fresh clue bonus
- repetition penalty
- too-many-active-quests penalty

### Opportunity Surfacing

Opportunities may be surfaced through:

- dedicated quest endpoints
- room payloads
- mob payloads
- viewer-specific room item payloads
- explicit commands like `rumors`

The important rule is that the canonical source is the quest service. Room/NPC
payloads may expose a small `quest_indicator` projection, but it should stay a
derived presentation hint rather than a separate quest runtime.

## Content Types

All content types use the same underlying quest engine.

### Questlet

- 1-3 beats
- often optional
- may resolve immediately after a single choice

### Quest

- a standard multi-step authored story
- usually includes at least one machine-tracked objective

### Contract

- repeatable or semi-repeatable
- often uses slots and query-based bindings
- good for guild boards, bounties, escorts, courier work

### World Event

- phase-driven shared content
- may surface without formal acceptance
- should usually use explicit world-scoped rules and limited world mutations

### Arc

- authored collection of related quests
- top-level summary and recap
- progress computed from linked quest instances

## Example 1: Minimal Functional Questlet

This is the smallest useful example: one opportunity, one choice, one
resolution.

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: quest
metadata:
  world: world.1
  slug: read_the_notice
  name: Read the Notice

spec:
  scope: player
  repeatability:
    mode: never

  discovery:
    sources:
      - type: room_prompt
        room: room@12
    visible_if:
      not:
        fact:
          scope: player
          key: tutorial.read_notice
          op: eq
          value: true
    salience: 40

  steps:
    - id: offer
      kind: storylet
      recap: "A fresh notice has been pinned to the market board."
      text:
        body: |
          A town clerk has posted a simple message:
          "Volunteers wanted at the granary before dusk."
      choices:
        - id: read
          text: Read it carefully.
          effects:
            - type: set_fact
              scope: player
              key: tutorial.read_notice
              value: true
          goto: resolved

    - id: resolved
      kind: resolution
      resolution: complete
      recap: "You read the notice and learned the granary is hiring help."
```

## Example 2: Typical Quest

This is a standard authored quest with one offer, one investigation objective,
one decision point, and two outcomes.

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: quest
metadata:
  world: world.1
  slug: poisoned_well
  name: The Bitter Well

spec:
  scope: player
  arc: ashwick_outbreak
  repeatability:
    mode: never
  max_active: 1

  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: mobdefinition.12
    visible_if:
      all:
        - fact:
            scope: world
            key: ashwick.outbreak
            op: eq
            value: true
        - fact:
            scope: player
            key: reputation.ashwick
            op: gte
            value: 1
    salience: 80

  slots:
    suspect:
      resolve:
        type: fixed
        entity: mobdefinition.44

  steps:
    - id: offer
      kind: storylet
      recap: "Healer Toma believes someone poisoned Ashwick's well."
      text:
        body: |
          Toma keeps his voice low. "Someone did this on purpose. If you can
          find proof, we may stop it before more people fall ill."
      choices:
        - id: accept
          text: "I'll investigate."
          goto: investigate
        - id: decline
          text: "Not now."
          goto: declined

    - id: declined
      kind: resolution
      resolution: abandoned
      recap: "You left Toma without an answer."

    - id: investigate
      kind: objective
      recap: "You agreed to investigate the poisoning."
      objectives:
        - id: find_clues
          text: "Find two clues tied to the poisoning."
          tracker:
            event: clue.discovered
            filters:
              event_field:
                key: tags
                op: contains
                value: ashwick_poisoning
          progress:
            mode: unique_count
            target: 2
            distinct_by: clue_id
      transitions:
        - when:
            objective:
              id: find_clues
              op: complete
          goto: accuse

    - id: accuse
      kind: storylet
      recap: "You have enough evidence to name a suspect."
      choices:
        - id: accuse_suspect
          text: "Accuse {suspect.name}."
          goto: resolved_bad
        - id: report_privately
          text: "Bring your doubts back to Toma first."
          goto: resolved_good

    - id: resolved_good
      kind: resolution
      resolution: complete
      recap: "You helped Toma expose the poisoner before panic took hold."
      effects:
        - type: set_fact
          scope: world
          key: ashwick.well_safe
          value: true
        - type: add_faction
          scope: player
          faction: ashwick
          amount: 2

    - id: resolved_bad
      kind: resolution
      resolution: compromised
      recap: "You accused the wrong person. The sickness slows, but the village remembers the injustice."
      effects:
        - type: add_faction
          scope: player
          faction: ashwick
          amount: -1
        - type: unlock_quest
          quest: ashwick_atonement
```

## Example 3: Full-power Contract

This example shows the intended upper bound of the system: dynamic slots,
hidden objectives, timers, fail-forward, repeatability, and multiple possible
outcomes.

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: quest
metadata:
  world: world.1
  slug: knife_in_the_reeds
  name: The Knife in the Reeds

spec:
  type: contract
  scope: player
  repeatability:
    mode: cooldown
    cooldown_seconds: 259200
  max_active: 1

  discovery:
    sources:
      - type: bulletin_board
        room: room@88
      - type: npc_dialogue
        mob_definition: mobdefinition.201
    visible_if:
      all:
        - fact:
            scope: player
            key: standing.rangers_guild
            op: gte
            value: 5
        - fact:
            scope: player
            key: active_contracts
            op: lt
            value: 3
    salience: 75

  slots:
    client:
      resolve:
        type: fixed
        entity: mobdefinition.201
    witness:
      resolve:
        type: entity_query
        query:
          tags_all: [reedbank, witness]
          pick: two_distinct
    suspect:
      resolve:
        type: entity_query
        query:
          tags_all: [reedbank, suspect]
          pick: one
    hideout:
      resolve:
        type: location_query
        query:
          tags_all: [reedbank, smuggler_hideout]
          pick: nearest
    proof_item:
      resolve:
        type: generator
        generator: contraband_ledger

  steps:
    - id: offer
      kind: storylet
      recap: "{client.name} posts a Rangers' Guild contract about a murder in the marsh."
      choices:
        - id: accept
          text: "Take the contract."
          effects:
            - type: start_timer
              timer: trail_goes_cold
              duration_seconds: 1800
          goto: investigate

    - id: investigate
      kind: objective
      recap: "You accepted the contract and began questioning locals."
      objectives:
        - id: question_witnesses
          text: "Question two witnesses."
          tracker:
            event: player.spoke_to_npc
            filters:
              slot:
                name: witness
                op: contains_event_actor
          progress:
            mode: unique_count
            target: 2
            distinct_by: npc_id
        - id: recover_proof
          text: "Recover proof from the marsh."
          hidden: true
          tracker:
            event: item.obtained
            filters:
              slot:
                name: proof_item
                op: equals_event_target
          progress:
            mode: boolean
      transitions:
        - when:
            objective:
              id: question_witnesses
              op: complete
          goto: confront
        - when:
            timer:
              id: trail_goes_cold
              op: expired
          goto: ambush

    - id: confront
      kind: storylet
      recap: "The witnesses point toward {hideout.name}, and suspicion falls on {suspect.name}."
      choices:
        - id: arrest
          text: "Arrest {suspect.name} and seize the ledger."
          if:
            objective:
              id: recover_proof
              op: complete
          goto: resolved_complete
        - id: accuse_without_proof
          text: "Accuse {suspect.name} without proof."
          goto: resolved_compromised
        - id: take_the_bribe
          text: "Accept the bribe and let {suspect.name} go."
          goto: resolved_failed_forward

    - id: ambush
      kind: storylet
      recap: "You took too long. The marsh gang set an ambush and scattered."
      choices:
        - id: report_back
          text: "Report back."
          goto: resolved_expired

    - id: resolved_complete
      kind: resolution
      resolution: complete
      recap: "You closed the case cleanly and delivered both culprit and proof."
      effects:
        - type: grant_currency
          scope: player
          currency: gold
          amount: 80
        - type: add_faction
          scope: player
          faction: rangers_guild
          amount: 3

    - id: resolved_compromised
      kind: resolution
      resolution: compromised
      recap: "You named the right killer, but the case remains politically messy."
      effects:
        - type: grant_currency
          scope: player
          currency: gold
          amount: 40
        - type: add_faction
          scope: player
          faction: rangers_guild
          amount: 1

    - id: resolved_failed_forward
      kind: resolution
      resolution: failed_forward
      recap: "You took the bribe. The murder fades, but a smuggling ring hardens around Reedbank."
      effects:
        - type: set_fact
          scope: world
          key: reedbank.smugglers_emboldened
          value: true
        - type: unlock_quest
          quest: reedbank_smuggler_crackdown

    - id: resolved_expired
      kind: resolution
      resolution: expired
      recap: "The trail went cold and the guild marks the contract unresolved."
      effects:
        - type: add_faction
          scope: player
          faction: rangers_guild
          amount: -1
```

## Authoring Guardrails

These rules should remain true even after the system grows:

- no inline code in quest manifests
- named steps, never numeric stages
- recap text on every major step
- fail-forward or compromise path for almost every non-trivial quest
- sparse facts and marks
- one shared engine for authored quests, contracts, and world events
- private consequences by default, shared mutations only when explicitly scoped

## Tooling Expectations

The end state is not just runtime behavior. It includes tooling.

The builder should eventually support:

- schema validation
- manifest round-trip export/import
- graph visualization
- unreachable-step detection
- missing-transition detection
- slot resolution validation
- objective/event linting
- canned simulation traces

Without those tools, the content model will become hard to maintain.
