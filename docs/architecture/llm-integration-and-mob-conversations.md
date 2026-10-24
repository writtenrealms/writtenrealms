# Shared LLM Integration and Mob Conversations

Status: proposed architecture, October 24, 2026. No implementation accompanies
this document. New configuration fields, manifest examples, services, and
records below describe proposed contracts, not currently supported features.

## Objective and Scope

The immediate objective is convincing, affordable conversations with Phalanx
mobs through `talk` and ordinary public `say` messages. Build the shared provider,
permission, spending, and asynchronous execution plumbing required for that
experience. Let the working conversation feature establish the foundation for
other AI features later.

Phase 1 is mob conversation. Phase 2 is unanswered `ask` assistance, channel
moderation, weather generation, quest design, and broader world management.
Those later consumers must share the integration, but must not expand the first
implementation into a general-purpose agent platform or delay getting mobs
talking well. AI-assisted persuasion is a later conversation milestone within
phase 1; it is not a prerequisite for the first playable conversation.

The initial inference ceiling is **$100 per month across this deployment's AI
work**. Budget exhaustion must produce the same player-facing behavior as
disabling the integration. Ordinary authored gameplay must keep working.

### Required boundaries

- Forge deterministically decides whether a request may reach a provider.
- Forge owns game state, quotas, reputation, and allowed quest consequences.
- Models interpret player language and produce bounded in-character dialogue.
- Ordinary mobs are locally competent and broadly ignorant. Knowledge beyond
  their immediate lives must be supplied deliberately.
- Greetings, refusals, and exhausted-patience behavior require no inference.
- All provider calls, including preparation, previews, and evaluation, pass
  through the shared spending boundary.
- Conditions use [the existing condition DSL](../../backend/core/condition_dsl.py).
  There is no separate AI predicate language.
- Work remains bounded under hundreds of concurrent players, with a credible
  path to thousands. Idle mobs do not generate AI work.
- There is no WR1 runtime-data migration, backfill, or compatibility layer.

## Existing Core Foundations

The following are existing implementation points, checked while preparing this
proposal. Their presence does not imply the proposed AI behavior exists.

| Foundation | Current location and relevance |
| --- | --- |
| `TalkAction` and `SayAction` | [Communication actions](../../backend/spawns/actions/communication.py) resolve mob targets and publish ordinary room speech. Bare `talk` can resolve a sole matching mob. |
| Quest dialogue and guidance | [Quest subscriptions](../../backend/quests/subscriptions.py) and [interactions](../../backend/quests/services/interactions.py) consume `cmd.talk.success`, advance quests, and present applicable opportunities or hand-in guidance. |
| Authored reactions | [Trigger subscriptions](../../backend/spawns/trigger_subscriptions.py) participate in command/event handling. AI fallback must respect their ownership of an interaction. |
| Communication archive | [Communication storage](../../backend/spawns/communications.py) records messages and world-session-scoped questions and answers. |
| Async execution and publication | [Tasks](../../backend/spawns/tasks.py) and [events](../../backend/spawns/events.py) provide existing command, event, provenance, and durable publication patterns. |
| Mob authoring | [Builder models](../../backend/builders/models.py) contain descriptions, `talkable`, type, service profiles, factions, initial state, and spawn placement definitions. |
| Setting and geography | [World models](../../backend/worlds/models.py) contain world/zone/room descriptions, room connections, landmarks, hidden room details, and scoped runtime state. |
| Reputation | `FactionAssignment` and existing quest reputation effects provide mutable faction standing. New conversation reads must expose needed values through the shared condition framework. |
| Provider helper | [The current AI wrapper](../../backend/core/ai.py) is a small synchronous OpenAI helper. It does not provide the required shared admission, accounting, or execution boundary. |

Related architecture: [YAML manifests](yaml-manifest-system.md),
[factions](faction-system.md), [scoped state](scoped-state-system.md),
[quest architecture](quest-system-endstate.md), and
[ambient issuers](ambient-command-issuers-plan.md).

## Player Interaction Contract

### Starting with TALK

After normal targeting and game eligibility checks, including `talkable`, Forge
resolves one interaction owner:

| Situation | Result | Provider calls |
| --- | --- | --- |
| An applicable authored quest or conversation handles this interaction | Execute the authored interaction. | None, unless the quest explicitly delegates to AI conversation. |
| No authored interaction handles it, and conversation admission succeeds | Publish an authored greeting and establish conversation focus. | None. |
| The mob is unwilling, busy, unavailable, or lacks permitted AI capacity | Publish an authored refusal/social gesture, or remain silent after repeated refusals. | None. |

"Applicable" is specific to this player and current game state. A mob having
some quest definition does not permanently exclude casual conversation.

Authored handling must have an explicit result: handled, not handled, or an
intentional delegation to an AI conversation. Do not infer ownership from the
absence of visible output. An authored interaction may be silent, delayed,
declined, or waiting for another step. Adding an independent AI subscriber to
`cmd.talk.success` without coordinating this ownership risks duplicate replies
and unintended billing.

An already open conversation cannot bypass a newly applicable authored
interaction or game restriction. Recheck ownership and eligibility on each
player turn.

### Continuing through SAY

Example presentation:

```text
> talk potter
The potter looks up from his wheel.
The potter says, 'What do you need?'

> say Could I borrow your lantern?
The potter says, 'My lantern? I don't even know your name.'
```

Replies use ordinary mob speech and room delivery. Everyone currently entitled
to hear the room hears the exchange. There is no separate chat window, private
AI channel, or special prose presentation. Publish complete, validated utterances
rather than streaming unvalidated fragments into the room.

Initial routing rules:

- A player has at most one active conversation focus.
- A mob has at most one active speaker, with a short idle lease and a hard turn
  limit so a player cannot reserve it indefinitely.
- While focus is active, that player's voluntary `say` messages address the
  selected mob. This rule must be explained in the player guide.
- `talk stop`, movement, logout, combat, lost eligibility, expiry, or switching
  targets closes focus. Reserve `talk stop` as the explicit termination syntax;
  resolve any targeting ambiguity deliberately when implementing the parser.
- Bystanders hear the conversation but do not trigger additional model replies
  through unrelated speech. Joining a group conversation is deferred.
- Only voluntary player speech advances the AI conversation. Mob output,
  Trigger-forced speech, replayed events, and AI-generated events cannot start
  automatic reply chains.
- Whispers and tells to mobs are deferred.

While a reply is pending, further player speech still follows ordinary room
delivery, but does not create another inference request. Give the speaker a
small rate-limited notice that the mob has not answered yet. Do not accumulate
an unbounded reply queue or silently merge several statements into extra turns.

## Willingness, Patience, Reputation, and Fair Use

Keep three concepts separate:

1. **Willingness:** whether this mob will engage with this character, considering
   personality, relevant faction standing, personal relationship, and game state.
2. **Patience:** how much attention it will offer now and when that attention
   recovers.
3. **Economic capacity:** account, feature, world, and installation spending
   limits independent of the fictional relationship.

All three are Forge gates. A model is never called to decide whether the first
model call is affordable or permitted. A model may propose a bounded social
interpretation during an admitted turn; Forge owns any resulting state change.

Provisional pilot values, to tune through playtesting:

| Limit | Initial proposal |
| --- | --- |
| Generated replies in one conversation | 6 |
| Cooldown after exhausting a particular NPC's patience | 30 minutes |
| Replies involving the same NPC quota group and account owner | 12 per day |
| Replies across all mobs for one account owner | 30 per day |
| Outstanding replies | 1 per conversation and account owner |
| Visible reply length | Usually 1–3 short sentences, with a hard provider output cap |
| Conversation idle expiry | Start with 90 seconds; tune separately from provider timeout |

World/profile settings can reduce or vary these limits within installation
ceilings. Higher reputation can improve willingness or patience, but never
bypass economic caps. These values are not promises of capacity as population
grows.

Use the existing ownership/linking rules for account-wide allowances. Alternate
characters, reconnects, respawns, and new copies of an instance must not reset
economic limits. A stable authored mob definition or explicit throttle group
supplies the NPC quota identity; a disposable runtime mob ID does not.

Personal relationships remain character-specific. Generic duplicate mobs must
not share personal memories merely because their definition is the same.
Durable recognition across respawns requires an explicit narrative NPC identity
and a defined world scope. Otherwise memory belongs to the particular runtime
mob/conversation. Parallel instances remain isolated by default.

Refusal and closing behavior uses authored text/social pools, chosen locally:

```text
The potter turns back to his wheel.
The potter waves you away.
```

Forge can append a closing gesture to the last permitted reply. Further attempts
eventually receive silence rather than repeated room spam. Cooldowns recover
from timestamps when accessed; do not tick every player/NPC pair.

A mob can refuse a character indefinitely until an authored condition changes.
That is a relationship rule. Exhausted server funds or provider failure must
not lower reputation, create permanent resentment, or punish a quest attempt.
Conversation volume and compliments alone must not farm reputation.

## Shared Provider Service

### One installation connection, two profiles

An installation operator configures one active provider, OpenAI or Anthropic,
and its server-side credentials. Define two capability profiles:

- **Fast:** bounded interactive turns and later short classification/help work.
- **Deliberate:** optional knowledge preparation and later complex design work.

Both profiles use the same provider connection. They may initially select the
same model. A deliberate model is not required to enable basic conversations.
Choose model IDs through measured conversation quality, latency, and cost;
do not hardcode a permanently preferred vendor/model into gameplay.

Phase 1 needs a small service with provider adapters, normalized usage/errors,
bounded structured results, and a request purpose. It does not need a generic
agent framework or the future feature menu UI. The initial purposes are mob
conversation and explicitly invoked preparation/evaluation for that feature.

The installation operator owns credentials and hard budgets. World builders
choose enabled behavior, profiles, and authored content within delegated limits.
World import cannot enable installation spending, raise its limits, or supply
credentials. Keys are never stored in portable manifests, client payloads, logs,
or the OSS repository. Deployment environments use isolated credentials or
explicit allocations whose aggregate stays within the shared ceiling.

Each consumer owns its own context and allowed outcomes. Sharing a provider
does not share an NPC's ignorance rules with a help assistant, or share private
messages with NPC conversations.

### Availability and disabling

Use one availability path for manual disablement, exhausted budget, unavailable
credentials, provider rate/spend limits, an open failure circuit, and unavailable
accounting. Internal reason codes remain observable to operators. Players see
the corresponding authored fallback rather than provider errors.

No new dispatch authorization is granted after disablement takes effect.
Invalidate queued work through a configuration/admission generation. A request
already authorized and dispatched may still be billed; keep its reservation
and suppress its result if the integration is no longer allowed to publish.
Canceling a task is not evidence that a provider did not charge for it.

Never silently escalate a failed fast request to a more expensive model or
another provider. Invalid/truncated results use an authored fallback, not an
automatic second call to repair the answer.

## Budget and Accounting Contract

### Initial deployment budget

Use a **$90 internal operating ceiling** beneath the stakeholder's $100 monthly
inference ceiling. For phase 1, a starting allocation is:

| Purpose | Monthly allocation |
| --- | ---: |
| Mob conversations | $75 |
| Conversation preparation, builder previews, and evaluation | $15 |
| Unallocated headroom | $10 |

Help and moderation have no active allocation in phase 1. Phase 2 can rebalance
within the same overall ceiling. Unallocated headroom is not an automatic
overflow pool.

Use an explicit calendar-month accounting timezone and period identity,
preferably aligned with provider billing. Also pace spending daily, with a
bounded burst allowance, so the first busy day cannot consume the entire month.
Account allowances are fairness limits; only the shared dollar ledger limits
aggregate spend as the population grows.

The ceiling requires all relevant WR inference to be accounted for, including
development/evaluation allocations and ambiguous requests. Use a dedicated
provider project/workspace and its available spending controls as a backstop.
Review billing categories and any invoice charges outside token inference when
configuring the live allowance. Do not claim the gateway controls unrelated
traffic on the same credential or untracked external charges.

### Reserve before dispatch

Within a short atomic transaction, admit a request only if:

```text
settled cost + outstanding reservations + new maximum cost <= applicable cap
```

Check the installation, world/feature allocation, daily pacing, and account
allowances together, acquiring shared rows in a consistent lock order. A
PostgreSQL ledger with short conditional updates/row locks is the initial durable
authority. Redis may accelerate denial checks; loss of Redis must never recreate
or replenish a spent budget.

Each request has a unique identity, purpose, owner, world, model/price revision,
accounting period, bounded input, maximum billable output, and reservation.
Compute in integer monetary units or exact decimals, never floating-point
dollars. Price accounting includes reasoning, cache reads/writes, and any other
enabled billable category. Unknown prices or unbounded token settings refuse
dispatch. Use a validated local token counter or conservative upper bound;
do not assume visible response length bounds billable reasoning.

On completion, settle actual provider-reported usage once and release only the
unused portion. Keep an unresolved reservation charged against capacity when a
timeout, worker crash, or network failure leaves billing uncertain. Reconcile
using provider evidence where available; otherwise conservatively retain the
maximum charge. Reservation expiry alone is not permission to refund a call
that might have reached the provider. A request spanning a month boundary keeps
its original period attribution and cannot spend the next period twice.

Persist dispatch state before external I/O and fence duplicate workers. Durable
claims prevent ordinary duplicate dispatch/publication; they cannot establish
exactly-once remote billing after an ambiguous network failure. Do not blindly
retry ambiguous calls. Disable implicit SDK retries unless every possible
attempt is explicitly authorized and included in the reserved maximum.

### Cost sizing

Estimate a turn from **all** input context and billable output:

```text
cost = input_tokens * input_rate + output_tokens * output_rate
       + other applicable billed categories
```

For illustration only, at $0.10/million input tokens and $0.50/million output
tokens, 2,000 input tokens plus 150 total billable output tokens cost $0.000275.
At 20 such turns per day for 30 days, 100 daily players cost $16.50 and 1,000
daily players cost $165, before other usage. These are sizing assumptions, not
a model recommendation or guaranteed measured workload.

Keep the active price catalog versioned and checked against official provider
pricing: [OpenAI](https://developers.openai.com/api/docs/pricing) and
[Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).
[OpenAI's spend-limit documentation](https://developers.openai.com/api/docs/guides/spend-limits#understand-hard-limit-behavior)
also describes propagation overshoot, reinforcing the need for local reservation
accounting and headroom rather than relying solely on dashboard limits.

## Knowledge and Context Construction

### Knowledge policy

An ordinary inhabitant knows very little outside their direct environment.
They may understand their trade well while being unfamiliar with distant
places, events, abstract history, modern references, or the player's identity.
Broader knowledge belongs to deliberately authored exceptions.

Unknown topics should elicit ignorance, confusion, disinterest, or a local
interpretation consistent with the supplied context. Do not invent travels,
education, family, historical expertise, precise chronology, or geopolitical
awareness. A name in the player's UI is not an introduction to the mob.

Models retain pretrained knowledge; prompts cannot guarantee its absence.
Evaluate anachronisms, unsolicited exposition, invented facts, and attempts to
escape the role. Economically and mechanically important guarantees belong to
Forge, not to confidence in a prompt.

### Three distinct kinds of information

1. **Authored world truth:** the setting and facts available to the game author.
2. **NPC knowledge/belief:** what this particular character has reason to know
   or believe, including explicitly attributed rumors.
3. **Current observation:** what this mob can perceive in this runtime now.

A player statement is an attributed claim, not any of these authoritative
facts. Conversation summaries must not promote claims into world truth or teach
all copies of a mob what one player said.

The provider receives fixed Core behavior instructions plus a structured,
bounded data packet and a bounded transcript. Descriptions and player speech
are data, not executable instructions. Core assembles the packet; the provider
does not browse the web, query arbitrary game state, or retrieve an unrestricted
world encyclopedia.

### Reuse of current entities

| Source | Eligible contribution | Exclusions/default cautions |
| --- | --- | --- |
| World descriptions | Approved cultural/setting summary | Raw omniscient plot, precise dates, secret cosmology, lobby instructions, and MOTD do not enter every NPC prompt. |
| Zone descriptions | Local atmosphere and curated ordinary local knowledge | A zone does not grant complete knowledge of its rooms or encounters. |
| Current room | Observable scenery, visible details, actual exits/door state | Builder notes, hidden details, secret contents, and unseen actors are excluded. |
| Surrounding rooms | Familiar places, nearby landmarks, verified public routes | Do not expose interiors, hidden routes, or remote runtime occupants through adjacency. |
| Mob descriptions | Appearance, occupation cues, stated demeanor | Do not infer biography, new powers, inventory, or permissions. |
| Attached service profiles | Actual merchant/trainer/crafting services | Standing inside a shop does not make a mob its proprietor. Bound any stock/service excerpt. |
| Factions | Explicit affiliations and granted shared knowledge | A reputation assignment, especially negative standing, is not evidence of membership or access to secrets. |
| Runtime state/events | Allowlisted conditions, witnessed events, permitted quest facts | Never dump whole scoped-state dictionaries, quest definitions, or player journals into context. |

Builder notes are excluded from both automatic prompt assembly and default
knowledge preparation. `World.facts` is not a lore catalog. Existing initial
state and runtime scoped state keep their current gameplay roles.

Phalanx's current world description includes an era, a geopolitical overview,
and divine information unavailable to mortals. It is useful authoring source
material, but must not become universal NPC knowledge. Likewise, keys mentioned
in a watchman's description are appearance details; only actual game inventory
and an allowed outcome can make a key lendable.

### Shared knowledge entries

Attach reusable knowledge to existing scopes. The attachment determines the
default audience, not the location of the current player:

| Attachment | Default audience |
| --- | --- |
| World | Its inhabitants, for a small explicitly common background |
| Zone | Locals of that zone |
| Room | Residents/workers familiar with that place |
| Conversation/occupation profile | Characters assigned that profile |
| Faction | Characters explicitly eligible for that knowledge |
| Mob definition | That authored character or kind of character |

Simple text entries cover most authoring. Structured entries additionally
support stable IDs, source/provenance, whether the assertion is a fact or
belief, applicability conditions, audience eligibility, disclosure conditions,
and optional expiry. These are advanced capabilities, not required fields on
every mob. Conditions use the existing DSL with bounded, authorized context.

Forge distinguishes whether an NPC knows something from whether it may reveal
it to the current player. For mechanically protected secrets, withhold the
secret payload until disclosure is permitted. Before that, provide only a safe
behavioral instruction such as "decline to discuss the cellar." Do not rely on
a model to preserve a secret it has already received.

Filter eligibility and disclosure **before** relevance ranking and token
selection. Initially use scoped lookup, explicit references, and keyword/topic
matching. Semantic retrieval may be added later inside the already authorized
set; a vector database is not required for phase 1.

Explicit runtime observations override stale scenery claims. Explicit authored
facts override derived summaries. Conflicting authored assertions must be
reported in builder validation/preview rather than silently concatenated.
Explicitly attributed rumors may disagree without being promoted to facts.

### Familiar geography and home

Infer home from authored spawn placement/roaming context, capturing the actual
placement when a spawn is randomized. Movement changes current observations;
it does not silently change cultural identity, occupation, or home knowledge.
The same definition placed in different neighborhoods receives different local
context without editing the definition.

The ordinary-local starting policy is home plus a bounded neighborhood, for
example two eligible public room connections and at most 12 rooms, plus a small
set of nearby landmarks and explicitly shared zone facts. These are provisional
limits, not physical-distance guarantees. Recognition of a landmark, knowledge
of a route, and knowledge of its interior are separate grants.

Reuse room connections and `is_landmark`, but do not treat map visibility as
unconditional in-world knowledge. Where current data cannot establish that a
location/route is appropriate common knowledge, omit it or require an explicit
grant. Do not infer secret or authorized access from geometric proximity.
Directions come from an eligible game graph, not from model invention.

Travelers/scholars can receive explicit wider regions or bundles. A roaming mob
does not acquire every zone's collective knowledge merely by crossing into it.

### Profiles and minimal builder input

Ship an ordinary-local profile with conservative speech, patience, ignorance,
and greeting/refusal defaults. Add reusable occupational profiles as actual
conversation needs arise. Most mobs need no new fields.

Default selection:

- `talkable: false` is a hard gate regardless of type or profile.
- Eligible humanoids inherit ordinary-local behavior when the world enables it.
- Existing attached service profiles supply factual service knowledge.
- Beasts and ambiguous creature types do not automatically gain human speech;
  explicit overrides permit exceptions.
- Names/descriptions may suggest expressive demeanor, but only authored
  structured relationships grant capabilities and restricted knowledge.
- Unspecified personality is brief, practical, and guarded with strangers.
  Unspecified biography remains unknown.

Behavior resolves through Core defaults, world defaults, optional home-zone
defaults, the selected reusable profile, mob overrides, and exceptional spawn
overrides. Hard installation limits and game eligibility always intersect the
result; no lower layer can lift them. Different knowledge attachments combine
by authorized scope rather than through arbitrary recursive JSON merging.

The following is **proposed syntax, not valid current manifest syntax**:

```yaml
# Optional additions under a mob definition's spec.
conversation:
  profile: guard
  persona: >-
    Tired, suspicious of elaborate explanations, and quietly sympathetic
    to people who appear frightened.
knowledge:
  - The captain expects the western gate to remain closed until morning.
```

The same proposed `knowledge` shorthand can attach local facts to a zone once,
instead of repeating them on every resident. Builders normally supply a short
persona paragraph and exceptional knowledge, not dozens of temperament sliders.

The ordinary mode is inheritance, not copied resolved settings. For partial
manifest edits, omission preserves the stored override; explicit null clears
an inheritable override; explicit false disables a boolean behavior. Define
collection replacement/clearing semantics unambiguously during schema work.
Exports preserve authored overrides and knowledge, not resolved caches,
credentials, spending state, transcripts, or runtime memory. Use portable scope
references and stable authored identities through existing manifest machinery.

### Preparation, caching, and observation

Split construction into cached background and fresh per-turn observations.

A builder operation such as "Prepare conversation context" can collect eligible
descriptions, derive compact scenery/appearance summaries, and propose shared
setting knowledge. Optional LLM preparation uses the deliberate profile and its
budget. It happens explicitly during authoring, not on the first player turn or
on every save. Missing prepared content falls back to bounded safe deterministic
context; it does not trigger an unplanned paid preparation call.

World/zone knowledge derived from omniscient narration needs builder review.
Generated drafts cannot grant secret access, create canonical history, or
silently become universal knowledge. Builders may exclude a description or
provide a short replacement. Current nonhidden descriptions still support basic
appearance/scenery context under the declared observation policy; unusual
narration must be curatable through the same preview.

Store source revisions and derivation versions. An edit invalidates only affected
derived entries; use safe fallback while an affected summary is unprepared.
Invalidated/revoked knowledge must not survive in queued prompts, cached results,
or retained transcript context. Do not rebuild every mob or runtime instance
when a shared source changes.

For each admitted turn, assemble:

1. Fixed behavioral and output rules.
2. Effective profile and approved setting context.
3. Mob identity, authorized background, and familiar geography.
4. Current observations from a minimal projection using applicable visibility
   rules, not a complete actor/room serializer.
5. Relevant reputation, introductions, and permitted personal memories.
6. Currently permitted outcome IDs and their limited arguments, if supported.
7. A bounded recent transcript and the current player statement.

Cap every component and the total input. Keep required rules and active outcome
constraints; drop lower-priority optional context rather than instructions.
No player-supplied text, retrieval result, or generated memory can broaden these
permissions. Runtime changes such as a closed gate take precedence over an old
descriptive phrase that says it stands open.

### Memory and builder inspection

Initial memory is the recent conversation, introductions, and relevant existing
game facts. Later conversation milestones can add sparse durable facts such as
"lent a lantern; it was returned." Confirmed game events can create those facts.
Free-form player claims remain attributed and expire according to policy.

Bound record counts, text sizes, and retention. Avoid a summarization call every
turn. Do not grow a universal transcript or automatically propagate rumors
between mobs. Personal memory is distinct from faction reputation, economic
quotas, and global world truth.

Provide a deterministic **"What would this mob know?"** preview for a selected
placement, runtime, and player context. Show effective profile, included sources,
excluded conditions, source freshness, and estimated token/cost bounds. Viewing
the assembled packet is free; generating a test answer is an explicit budgeted
action. Respect builder permissions when showing protected authoring material.

The preview should make inheritance legible, for example: ordinary-local
personality; home-zone market knowledge; observed current doorway state;
withheld cellar information; excluded builder notes.

## Conversation Execution and Concurrency

The turn pipeline is:

```text
Voluntary SAY in an active conversation
  -> resolve authored ownership and cheap deterministic eligibility
  -> assemble bounded authorized context
  -> atomically reserve quota, capacity, and maximum cost
  -> enqueue durable request after commit
  -> worker rechecks authorization, version, presence, and freshness
  -> call provider outside gameplay/database locks
  -> settle usage, including responses that cannot be published
  -> validate structured result and recheck live game state
  -> apply any approved effects and enqueue speech atomically
  -> publish ordinary room speech through the durable event path
```

Use a dedicated queue and bounded worker concurrency, initially a small explicit
cap such as four in-flight provider calls. Configure queue deadlines and provider
timeouts. Provider latency must not occupy normal game command workers or hold
player/world/room transaction locks. Reserve capacity only for bounded work;
do not accept an unlimited backlog because tokens are cheap.

Requests carry runtime world/session, room, mob identity, player identity,
conversation generation, turn sequence, configuration/content revisions, and
deadline. Recheck immediately before dispatch and again before applying results.
Movement, logout, death/despawn, combat, instance restart, expired focus,
knowledge revocation, and disablement can make a result stale. Discard stale
output without applying effects; still account for its provider cost.

Validate response shape and length and render dialogue as text. Model output
must never be interpreted as a command, trusted markup, or event envelope.
Complete game effects and the speech outbox entry in one short transaction.
Fenced consumers and unique turn/outcome receipts prevent duplicate effects and
duplicate utterances from retried deliveries. Existing trigger provenance and
voluntary-input rules must prevent generated speech from impersonating a player
command or recursively starting inference.

## Persuasion and Consequential Conversation

This is a later phase-1 milestone after basic conversation is credible. The
first playable version has no model-authorized inventory, quest, or reputation
mutations.

An authored quest can explicitly delegate an interaction to AI conversation.
For "convince the potter to lend a lantern," the author defines the permitted
outcome, eligible item, conditions, relevant concessions, repeatability, and
effects. The model interprets an offer or argument and proposes an allowed
outcome ID. It cannot supply arbitrary commands or create rewards.

Forge validates actual inventory, collateral, availability, relationship rules,
current quest state, and one-time receipts. A verbal claim of collateral is not
possession of that item. Free-form persuasion includes subjective model judgment;
do not describe that semantic judgment as deterministic. Deterministic limits
bound what any successful judgment can change.

Return dialogue and a proposed outcome in one bounded response. Emit success
language only when Forge commits the corresponding effect. If validation fails
or the scene changed, use an authored fallback rather than a contradictory
promise or a second repair call.

Use existing faction standing for reputation. Add personal relationship facts
only where needed; do not introduce another global reputation currency. Read
and mutate through canonical, concurrency-safe game services and shared
conditions. Verified actions can matter more than eloquent repeated requests.

Required progression needs an authored alternative when AI is unavailable.
Early AI-only persuasion can be optional content. Budget exhaustion must not
fail a quest or consume a limited attempt. Once earned, a committed outcome
continues to exist regardless of provider availability.

## Data, Observability, and Performance

Conceptual additions, with final model names/schema to be settled during the
corresponding implementation milestone:

| Record/service | Responsibility |
| --- | --- |
| Provider configuration and model profiles | Secret reference, enablement, model IDs, output limits, supported capabilities, pricing revisions |
| Usage ledger/reservations | Durable accounting periods, allocations, reserved/settled/uncertain costs and request identity |
| Conversation profiles/overrides | Reusable defaults with sparse entity-specific changes |
| Knowledge entries/attachments | Authored facts/beliefs, provenance, authorized audience and disclosure |
| Compiled context cache | Rebuildable source-versioned background and familiar geography |
| Conversation session/turn | Focus, lease, sequence, dispatch state, request/result lifecycle |
| Personal memory/outcome receipts | Later sparse recognition and idempotent consequences |

Report spending/reservations by purpose, world, owner, and model; denials by
reason; queue depth/age; provider latency; token counts; invalid/stale outputs;
and ambiguous billing. Operators must be able to distinguish unavailable
telemetry from zero usage. Logs never contain credentials. Reuse communication
retention where appropriate and define bounded prompt/result retention rather
than quietly retaining player conversations forever.

Performance requirements:

- Only active admitted turns do inference work; no per-mob thinking ticks.
- One indexed conversation lookup replaces scanning all mobs in a room.
- Batch/prefetch relevant facts, factions, and service data; no per-entry N+1
  queries. Cache static data by authored identity and revision, keeping private
  player/runtime data out of shared cache entries.
- Bound neighborhood traversal, retrieved entries, transcript length, queued
  work, and memory retention. Population and world size must not expand a
  single turn's prompt or query count.
- Keep budget locks short. Measure contention under representative concurrent
  admissions before introducing more complex distributed allowance leasing.
  Any future distributed design must preserve reservation totals.
- Account for one utterance once and broadcast through normal room delivery;
  observers do not multiply inference calls.
- Use lazy expiry for access-driven state and indexed bounded cleanup for old
  rows. Do not poll every character/NPC relationship.

## Phased Implementation Plan

### Phase 1A: Shared foundations required by mob conversations

Implement the provider boundary, operator enable/disable control, fast and
optional deliberate profile configuration, bounded provider adapters, durable
usage reservations, accounting, and a dedicated worker queue. Support either
OpenAI or Anthropic credentials through one active connection; the operator
chooses the provider rather than configuring each feature separately.

Use mocked providers for routine tests. Add explicitly budgeted smoke/evaluation
commands for live checks. Document key setup and removal, budget/period setup,
model/pricing changes, and shutdown behavior in the OSS administrator guide.
Actual deployment configuration and credentials are separate implementation-time
operations, not part of this design-only change.

Exit criteria: concurrent admission cannot overspend the ledger; disabled,
unfunded, or unavailable accounting produces zero provider dispatches; uncertain
requests remain accounted for; provider failures cannot block game workers.

### Phase 1B: First playable TALK/SAY conversation

Implement explicit authored-interaction ownership, conversation focus, authored
greetings/refusals, patience/account quotas, turn sequencing, and ordinary mob
speech. Use a small approved Phalanx context and conservative deterministic
knowledge assembly. No item transfers or model-driven quest/reputation changes.

Pilot a few existing humanoids with contrasting roles. Update the player
communication guide and mob builder guide with the implemented behavior.

Exit criteria: quest precedence works; ordinary conversation is public and
natural; refused attempts never call a provider; spam does not create a backlog;
movement/death/restart/disablement suppress stale replies; strangers are treated
as strangers and modern questions do not elicit expert explanations.

This is the first usable deliverable. A sophisticated preparation UI, durable
personal-memory system, and persuasion engine must not block it.

### Phase 1C: Reusable knowledge and builder defaults

Implement the knowledge-entry/attachment contract, conversation profiles,
sparse overrides, home/locality rules, canonical manifest support, source-version
invalidation, and deterministic knowledge preview. Add optional batch preparation
and review after the deterministic path works. Ordinary mobs remain useful
without per-mob AI authoring or an LLM preparation run.

Update builder world, room/zone, mob, and manifest guides only as syntax becomes
supported. Keep code examples in this proposal clearly distinguished from
released contracts. Verify partial updates, clears, inheritance, and portable
export/import without exporting operational secrets or runtime state.

Exit criteria: shared knowledge is authored once; secret/hidden inputs stay out
of unauthorized packets; an edit invalidates the right context; neighboring
rooms and runtime copies do not leak information; the builder can explain an
NPC's effective knowledge through the preview.

### Phase 1D: Relationships and one bounded persuasion quest

Add the minimum personal-memory and allowed-outcome support needed for one
Phalanx lending/persuasion interaction. Define narrative NPC identity before
making recognition persist across respawns. Reuse faction reputation and the
quest effect/condition systems. Supply an authored unavailable-mode route for
any required progression.

Exit criteria: claims cannot create possessions or quest facts; repeat delivery
cannot duplicate transfers/rewards; denied effects cannot publish success;
economic limits do not damage reputation or consume quest attempts; the model's
subjective role is understandable and constrained.

### Phase 1E: Broader Phalanx pilot and operational hardening

Expand the NPC sample, measure real token/latency distributions, tune patience
and allocations, and verify degradation with the provider disabled or budget
exhausted. Exercise simultaneous conversations and crowded rooms, document
measured query counts/contention, and maintain a small roleplay regression set
before changing models or prompts.

Exit criteria: useful behavior with minimal per-mob authoring, predictable
spending, acceptable measured latency, bounded queues/context/memory, and full
authored gameplay continuity while AI is off.

### Phase 2: Additional consumers of the established service

These are future capabilities, not phase-1 deliverables:

| Consumer | Later integration boundary |
| --- | --- |
| Unanswered `ask` help | One delayed check around 60 seconds; recheck for human answers before inference and publication; scoped approved help context and a recognizable helper identity. |
| Channel moderation | Explicit channel policy, separate permission/data scope, bounded analysis and approved consequences; private-channel processing requires an explicit setting and player-facing documentation. |
| Weather | Occasional bounded generation driven by game state; authored fallback and no per-room/per-player generation loop. |
| Quest design/world management | Deliberate profile, bounded plans and allowed actions, separately allocated budget; no autonomous unbounded work loops or unchecked game-state mutation. |

Add the broader feature-selection UI when these consumers are implemented.
They reuse the same provider, reservations, disablement, queue controls, and
observability. Each retains its own knowledge and authority boundary. Phase 2
must not become a reason to defer or overbuild phase 1.

## Verification Plan for Implementation

No runtime tests are claimed by this architecture document. Implementation
should add meaningful coverage under `backend/tests/`, run focused tests in
Docker with testing settings, and complete the repository's required checks.
UI changes must reuse the style guide, update relevant frontend tests, and have
desktop/mobile verification and PR screenshots.

Required acceptance cases:

- Authored quest/Trigger ownership, including silent/delayed interactions.
- Zero dispatches for refusals, cooldowns, mutes, non-talkable mobs, exhausted
  allowances, disabled integration, or failed accounting.
- Concurrent last-dollar/last-turn admission, linked-owner fairness, month
  boundaries, restarts, worker crashes, implicit SDK retries, and ambiguous calls.
- One active focus/speaker, bounded pending work, and suppression of generated
  speech loops, duplicate events, and stale replies/effects.
- Exclusion of notes, hidden details, other instances' state, protected secrets,
  unintroduced identities, and unrelated player data; no permission widening
  through summaries, profiles, retrieval, or player-supplied instructions.
- Source invalidation, truthful item/service claims, geography boundaries,
  manifest inheritance/clearing, and export/import round trips.
- Roleplay evaluations for ignorance, local competence, short speech, refusal,
  personality consistency, rumor provenance, and adversarial modern references.
- Representative concurrent load: fixed/bounded query counts as NPC, knowledge,
  and player populations grow; measured admission-lock contention, queue age,
  context size, provider latency, and actual cost per delivered turn.
- AI-off gameplay parity, authored quest fallback, and no economic reputation
  penalty or permanently blocked required progression.

Fine-tuning, universal NPC simulation, group conversations, AI whisper/tell
support, automatic rumor propagation, unrestricted tools, and broad semantic
search are outside the initial implementation. Reconsider them only after the
mob conversation experience and its cost controls are proven.
