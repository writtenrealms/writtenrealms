# WR2 Quest Reference

This guide is a manifest cookbook for builders experimenting with the WR2 quest
system.

For the overall mental model and player interaction contract, read
[quest-builder-guide.md](quest-builder-guide.md)
first. This document is more concrete: a set of typical quest manifests you can
copy, tweak, and ingest through `World > Edit World`.

For the field-by-field manifest contract, accepted enum values, and current
runtime behavior notes for settings like repeatability, read the `Quest
Manifest Field Reference` section in
[quest-builder-guide.md](quest-builder-guide.md).

For the shared `state` system that quests now use alongside triggers and
builder commands, read
[state-builder-guide.md](state-builder-guide.md).

All examples here assume:

- `metadata.world` should be replaced with your real world key
- mob and item references are written as slugs for readability
- rewards are kept intentionally small so the examples stay focused

## Current Supported Surface

At the time of writing, the runtime supports:

- discovery sources:
  - `auto_start`
  - `room_prompt`
  - `npc_dialogue`
- step kinds:
  - `storylet`
  - `objective`
  - `resolution`
- common tracker events:
  - `cmd.look.success`
  - `cmd.move.success`
  - `cmd.say.success`
  - `cmd.talk.success`
  - `quest.item.delivered`
  - `quest.mob.killed`
- current typed rewards/effects:
  - `grant_currency`
  - `grant_item`
  - `grant_xp`
  - `adjust_reputation`
  - `set_state`
  - `increment_state`
  - `clear_state`
  - constrained `mob_command`

The currency examples below assume the world defines `obol`.
`grant_currency` always requires an explicit authored code and positive integer
amount; it never assumes Gold.

For `auto_start`, the qualifying events are currently:

- `cmd.state.sync.success`
- `cmd.look.success`
- `cmd.move.success`

Other commands may still refresh discovery or progress active quests, but they
do not cause an `auto_start` quest to begin.

## 1. The Smallest Possible Quest

This is the best first quest to ingest because it is almost the minimum useful
system slice:

- it starts automatically
- it has one `storylet`
- it resolves after one choice

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: tiny_hello
  name: Tiny Hello
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 1
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: You notice a strange scrap of paper.
      text:
        body: A minimal authored quest beat.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The note tells you nothing useful, but the system works.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
look
Quest started: Tiny Hello
You notice a strange scrap of paper.
Lead: Read the note and move on.
Choices:
- continue: Continue.

quest choose tiny_hello continue
Quest resolved: Tiny Hello
The note tells you nothing useful, but the system works.
```

Why this example matters:

- it shows `auto_start`
- it shows how `storylet` choices work
- it shows the smallest end-to-end quest instance lifecycle
- it shows the simplest quest shape that can begin on connect, look, or move

## 2. Smallest Prerequisite Pair

This is the smallest pair of quests that shows quest prerequisites by slug.

Quest A is the prerequisite. Quest B is the follow-up. The follow-up uses
`quest_completed` in both `visible_if` and `accept_if` to keep the example
small and explicit.

In production content, you would usually do one of these:

- use `visible_if` when the follow-up should stay hidden until the prerequisite
  is done
- use `accept_if` when the follow-up can be visible early but should reject
  acceptance until the prerequisite is done

Manifest A:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: first_steps
  name: First Steps
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: auto_start
    visible_if: {}
    accept_if: {}
    salience: 1
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: A simple errand teaches you how the quest flow works.
      choices:
        - id: continue
          text: Finish the lesson.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: You finished the lesson.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Manifest B:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: second_steps
  name: Second Steps
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: room_prompt
        room: room.<training_room_id>
    visible_if:
      quest_completed: first_steps
    accept_if:
      quest_completed: first_steps
    salience: 5
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: A second lesson appears once the first is complete.
      choices:
        - id: continue
          text: Take the follow-up lesson.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: You completed the follow-up lesson.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
look
Quest started: First Steps
A simple errand teaches you how the quest flow works.

quest choose first_steps continue
Quest resolved: First Steps
You finished the lesson.

inspect
Quest available: Second Steps
A second lesson appears once the first is complete.
Accept with: quest accept second_steps

quest accept second_steps
Quest started: Second Steps
A second lesson appears once the first is complete.
```

What this teaches:

- `quest_completed` checks quest template completion by slug
- `visible_if` can hide a follow-up until the prerequisite resolves
- `accept_if` can enforce the same prerequisite again at accept time
- `quest_completed` currently requires a `complete` resolution, not `abandoned`

For more than one prerequisite quest, compose the same predicate with `all`:

```yaml
visible_if:
  all:
    - quest_completed: first_steps
    - quest_completed: town_favor
```

## 3. Room Prompt Gives You A Delivery Item

This is the standard "deliver this item to that mob" quest, but discovered
through the room instead of the NPC.

The important runtime behavior this example shows is:

- the quest opportunity comes from `room_prompt`
- `quest accept <slug>` immediately grants the item through `grant_item`
- the quest advances when the player uses `give <item> <mob>`
- if the player abandons before finishing, the granted item is cleaned up as
  long as it is still player-owned, including inside bags

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: shrine_packet
  name: Shrine Packet
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: room_prompt
        room: room.<courier_room_id>
    visible_if: {}
    accept_if: {}
    salience: 20
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: deliver_packet
      kind: objective
      recap: You took the sealed packet and now need to hand it to the shrine keeper.
      text:
        body: |
          A placard tied to the wall reads:
          "Courier needed. Take the sealed packet and deliver it to the shrine keeper."
      effects:
        - type: grant_item
          item_definition: sealed_packet
      objectives:
        - id: hand_in_packet
          text: Deliver the sealed packet to the shrine keeper.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.definition_id, shrine_keeper]
                - eq: [event.item.definition_id, sealed_packet]
          progress:
            mode: count
            target: 1
      transitions:
        - when:
            objective_complete: hand_in_packet
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The shrine keeper accepts the packet and breaks the wax seal.
      effects:
        - type: mob_command
          mob_definition: shrine_keeper
          command: say Good. I have been waiting for this packet.
  rewards:
    complete:
      - type: grant_currency
        currency: obol
        amount: 8
      - type: grant_xp
        amount: 25
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
look
New opportunity: Shrine Packet
A courier placard asks someone to carry a packet to the shrine keeper.

quest accept shrine_packet
Quest started: Shrine Packet
You took the sealed packet and now need to hand it to the shrine keeper.
Rewards: Sealed Packet

look
The shrine keeper waits beside the old altar. [ ? ]

give sealed_packet shrine_keeper
Quest resolved: Shrine Packet
The shrine keeper accepts the packet and breaks the wax seal.
Rewards: 8 Obols, 25 experience
```

What this teaches:

- `room_prompt` discovery for a delivery quest
- immediate item issuance on accept via `grant_item`
- item hand-in progression through `quest.item.delivered`
- the return NPC can still show `?` as a ready turn-in indicator

## 4. NPC Asks For Two Mob Kills

This is the standard kill-and-report-back loop.

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: rat_cull
  name: Rat Cull
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: camp_captain
    visible_if: {}
    accept_if: {}
    salience: 20
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: hunt
      kind: objective
      recap: Captain Merrow wants the tunnel rats culled.
      text:
        body: |
          "The tunnel rats are back," Captain Merrow says.
          "Kill two of them and report back."
      objectives:
        - id: kill_rats
          text: Kill 2 tunnel rats.
          tracker:
            event: quest.mob.killed
            where:
              eq: [event.target.definition_id, tunnel_rat]
          progress:
            mode: count
            target: 2
      transitions:
        - when:
            objective_complete: kill_rats
          goto: report
    - id: report
      kind: objective
      recap: The rats are down. Report back to Captain Merrow.
      objectives:
        - id: report_back
          text: Talk to Captain Merrow.
          tracker:
            event: cmd.talk.success
            where:
              eq: [event.target.definition_id, camp_captain]
          progress:
            mode: boolean
            target: 1
      transitions:
        - when:
            objective_complete: report_back
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Captain Merrow confirms the camp is safe for now.
      effects:
        - type: mob_command
          mob_definition: camp_captain
          command: say Good work. The stores might last the week now.
  rewards:
    complete:
      - type: grant_currency
        currency: obol
        amount: 8
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
Captain Merrow stands watch here. [ ! ]

talk captain
Quest available: Rat Cull
"The tunnel rats are back," Captain Merrow says.
"Kill two of them and report back."
Kill 2 tunnel rats.
Accept with: quest accept rat_cull

quest accept rat_cull
Quest started: Rat Cull
Captain Merrow wants the tunnel rats culled.

kill rat
kill rat

look
Captain Merrow stands watch here. [ ? ]

talk captain
Quest resolved: Rat Cull
Captain Merrow confirms the camp is safe for now.
Rewards: 8 Obols
```

What this teaches:

- kill objectives progress from `quest.mob.killed`
- report-back steps progress from `cmd.talk.success`
- `?` can mean “talk to finish,” not only “give item”

## 5. Ground Item Starts The Quest

This is not supported yet as a first-class discovery flow.

What is missing right now:

- there is no `item_read` or `item_examine` quest discovery source in the
  runtime
- there is no item-side quest indicator equivalent to the NPC `[ ! ]`
- the current discovery surface is only `auto_start`, `room_prompt`, and
  `npc_dialogue`

So a report lying on the floor cannot currently be the thing that directly
offers the quest in the same way an NPC does.

Closest current workaround:

- use `room_prompt` on the room containing the report
- or have an NPC / bulletin-board style quest giver point the player to the
  report

Once an item is already in the player’s inventory, a quest can absolutely ask
the player to deliver it to an NPC. What is not there yet is item-driven quest
discovery.

## 6. One NPC Offers Two Different Quests

This is useful because it shows what `talk <mob>` does when there is more than
one visible `npc_dialogue` opportunity on the same mob.

You need two manifests.

Manifest A:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: clerk_seal_delivery
  name: Seal Delivery
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: town_clerk
    visible_if: {}
    accept_if: {}
    salience: 15
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: The clerk needs a sealed packet delivered to the magistrate.
      text:
        body: |
          "I need a sealed packet carried across town," the clerk says.
          "Nothing glamorous. Just urgent."
      choices:
        - id: accept_packet
          text: I'll take it.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The clerk hands you the packet and sends you on your way.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Manifest B:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: clerk_archives_cleanup
  name: Archives Cleanup
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: town_clerk
    visible_if: {}
    accept_if: {}
    salience: 10
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: The clerk wants someone to clear moldy files out of the archive hall.
      text:
        body: |
          "And if that doesn't suit you," the clerk adds,
          "the archive hall needs cleaning before the damp ruins the ledgers."
      choices:
        - id: accept_cleanup
          text: I'll help with the archive hall.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The clerk gives you the archive key and a tired nod.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
The town clerk sorts ledgers behind a high desk. [ ! ]

talk clerk
Quest available: Archives Cleanup
"And if that doesn't suit you," the clerk adds,
"the archive hall needs cleaning before the damp ruins the ledgers."
Decide whether to help the clerk.
Accept with: quest accept clerk_archives_cleanup

Quest available: Seal Delivery
"I need a sealed packet carried across town," the clerk says.
"Nothing glamorous. Just urgent."
Decide whether to take the packet.
Accept with: quest accept clerk_seal_delivery
```

Current behavior to be aware of:

- the talk interaction presents all visible opportunities for that NPC
- each opportunity keeps its own `quest accept <slug>` command
- builders should keep quest names and `recap` text distinct so the list reads
  cleanly

## 7. Room Prompt Quest

This is the right pattern when the world itself should surface a quest, not an
NPC. Think placards, ruins, shrines, warning signs, cursed rooms, and so on.

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: shrine_survey
  name: Shrine Survey
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: room_prompt
        room: room.<survey_room_id>
    visible_if: {}
    accept_if: {}
    salience: 20
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: A weathered placard asks you to survey the shrines ahead.
      text:
        body: |
          A placard wired to the post reads:
          "Travelers needed. Inspect both roadside shrines and report whether they still stand."
      choices:
        - id: begin
          text: Take the survey.
          goto: survey
        - id: decline
          text: Leave it for someone else.
          goto: declined
    - id: survey
      kind: objective
      recap: You accepted the survey route.
      objectives:
        - id: inspect_shrines
          text: Inspect both shrines.
          tracker:
            event: cmd.look.success
            where:
              in: [event.target.id, [<east_shrine_room_id>, <west_shrine_room_id>]]
          progress:
            mode: unique_count
            target: 2
            distinct_by: event.target.id
      transitions:
        - when:
            objective_complete: inspect_shrines
          goto: resolved
    - id: declined
      kind: resolution
      recap: You leave the placard hanging for another traveler.
    - id: resolved
      kind: resolution
      recap: You surveyed both shrines and the route is safe enough to report.
  rewards:
    complete:
      - type: grant_xp
        amount: 20
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
look
New opportunity: Shrine Survey
Decide whether to take the survey.

quest accept shrine_survey
Quest started: Shrine Survey
A weathered placard asks you to survey the shrines ahead.

quest choose shrine_survey begin
You accepted the survey route.

look east shrine
look west shrine

Quest resolved: Shrine Survey
You surveyed both shrines and the route is safe enough to report.
Rewards: 20 experience
```

What this teaches:

- `room_prompt` is where the explicit opportunity message is useful
- it is the non-NPC complement to `[ ! ]`
- room/world-authored discoverability does not need a quest-giver mob

## 8. Dialogue Quest With Two Choices And Two Different Rewards

This is the best pattern for “one dialogue, two outcomes, different rewards.”

The key trick is:

- use a `storylet` with choices
- send each choice to a different `resolution` step
- put the differing rewards in each resolution step’s `effects`
- leave `spec.rewards.complete` empty if you do not want both branches to share
  a common reward bundle

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: guildmaster_favor
  name: The Guildmaster's Favor
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: guildmaster
    visible_if: {}
    accept_if: {}
    salience: 15
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: offer
      kind: storylet
      recap: The guildmaster offers you a choice between coin and instruction.
      text:
        body: |
          "I can pay you for your help," the guildmaster says,
          "or I can teach you something worth more than coin. Choose."
      choices:
        - id: take_coin
          text: Take the coin.
          goto: coin_reward
        - id: seek_instruction
          text: Ask for instruction.
          goto: lesson_reward
    - id: coin_reward
      kind: resolution
      recap: The guildmaster counts coins into your palm and sends you on your way.
      effects:
        - type: grant_currency
          currency: obol
          amount: 20
        - type: mob_command
          mob_definition: guildmaster
          command: say Spend it before you lose your nerve.
    - id: lesson_reward
      kind: resolution
      recap: The guildmaster spends an hour drilling you on old campaign mistakes.
      effects:
        - type: grant_xp
          amount: 40
        - type: mob_command
          mob_definition: guildmaster
          command: say Experience will outlast coin, if you let it.
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
```

Simulated log:

```text
The guildmaster studies the room from a carved chair. [ ! ]

talk guildmaster
Quest available: The Guildmaster's Favor
"I can pay you for your help," the guildmaster says,
"or I can teach you something worth more than coin. Choose."
Decide what favor to ask for.
Accept with: quest accept guildmaster_favor

quest accept guildmaster_favor
Quest started: The Guildmaster's Favor
The guildmaster offers you a choice between coin and instruction.
Choices:
- take_coin: Take the coin.
- seek_instruction: Ask for instruction.

quest choose guildmaster_favor take_coin
Quest resolved: The Guildmaster's Favor
The guildmaster counts coins into your palm and sends you on your way.
Rewards: 20 Obols
```

What this teaches:

- `storylet` choice branches
- different rewards through different resolution steps
- branch-specific NPC reaction lines via `mob_command`

## Good Builder Experiments After These

Once you are comfortable with the eight examples above, these are good next
exercises:

- a passphrase quest using `cmd.say.success`
- a multi-item hand-in quest using two separate objectives
- a quest with one optional branch and one mandatory branch
- a pair of small quests on the same NPC where one only appears after the
  other resolves

## Practical Reminders

- For NPC-given quests, `[ ! ]` plus `talk <mob>` is the intended discoverability flow.
- For non-NPC discoverability, prefer `room_prompt`.
- There is still no generic `quest complete` command. Completion should happen
  through the in-world action that makes sense: `give`, `talk`, `look`, `say`,
  or `kill`.
- If two quests on the same NPC sound too similar when presented together, the
  builder usually needs better quest names and `recap` text, not a different
  system feature.
