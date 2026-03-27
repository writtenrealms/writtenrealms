# WR2 Quest Builder Guide

This guide is for builders authoring quests in the new WR2 manifest system.
It explains how discovery works, what players are expected to type, and how to
author the common quest loops that are already supported in game.

## Mental Model

- A `quest template` is the authored YAML definition.
- An `opportunity` is a visible quest the player has not accepted yet.
- A `quest instance` is the running quest on one player.
- A `storylet` is one authored step inside the quest graph.

Today, builders author `kind: quest` manifests and players interact with them
through normal game verbs plus a few `quest` subcommands.

## Auto-Start Qualifying Events

`auto_start` quests do not start on every discovery refresh.

They currently auto-start only when one of these qualifying events happens:

- `cmd.state.sync.success`
- `cmd.look.success`
- `cmd.move.success`

That means:

- connecting or reconnecting can now start an `auto_start` quest immediately
- looking around can start one
- entering a new room can start one
- `say`, `talk`, `give`, `kill`, and `quest opportunities` do not auto-start
  quests, even though some of those flows still refresh quest discovery or
  progression for other reasons

## Player Interaction Contract

This is the intended player loop right now:

1. The player sees a quest source in the world.
2. If the source is an NPC dialogue offer, the room UI shows `[ ! ]`.
3. The player uses an in-world verb like `talk bartender`.
4. The game shows the authored pitch text and an explicit accept command:
   `quest accept <quest-slug>`.
5. Once accepted, quest progress happens through world actions:
   `look`, `move`, `say`, `talk`, `give`, `kill`.
6. When an NPC is ready for report-back or a hand-in, the room UI shows `[ ? ]`.
7. Completion still happens through the world verb that matches the objective:
   `talk captain`, `give keg bartender`, and so on.

There is intentionally no `quest complete` command right now.

That is by design: quests watch canonical game events. The player should
complete quests by doing something in the world, not by firing a generic
out-of-band completion verb.

## What `!` And `?` Mean

- `[ ! ]` means this mob currently has a visible `npc_dialogue` opportunity the
  player can enquire about.
- `[ ? ]` means this mob is currently a ready hand-in or report-back point for
  an active quest.

For item turn-ins, `[ ? ]` appears when the player already has what they need
to finish the current quest step at that NPC.

For kill-and-return quests, `[ ? ]` appears after the kill objective is done
and the player can report back now.

## Supported Runtime Surface

Builders can rely on these pieces today:

- Discovery sources:
  - `auto_start`
  - `room_prompt`
  - `npc_dialogue`
- Step kinds:
  - `storylet`
  - `objective`
  - `resolution`
- Common objective tracker events:
  - `cmd.look.success`
  - `cmd.move.success`
  - `cmd.say.success`
  - `cmd.talk.success`
  - `quest.item.delivered`
  - `quest.mob.killed`
- Common reward/effect types:
  - `grant_gold`
  - `grant_xp`
  - constrained `mob_command`

## Bartender Example

This is the exact shape for the saloon bartender quest described in design
discussion.

Expected player flow:

```text
An Oak Bar
[ exits: E W D ]
The bartender polishes a glass behind the bar. [ ! ]

talk bartender
You talk to Saloon Bartender.
Quest available: A Keg for the Bar
"Could you grab a keg from the back for me?" the bartender asks. "I can't leave the bar unattended."
Bring the saloon keg to the bartender.
Accept with: quest accept saloon_keg_run

quest accept saloon_keg_run
Quest started: A Keg for the Bar
The bartender needs a fresh keg from the back room.
Lead: Bring the saloon keg to the bartender.

get keg
You pick up a saloon keg.

look
The bartender polishes a glass behind the bar. [ ? ]

give keg bartender
You give Saloon Keg to Saloon Bartender.
Quest resolved: A Keg for the Bar
The bartender rolls the fresh keg into place.
```

Manifest:

```yaml
kind: quest
metadata:
  world: world.<world_id>
  slug: saloon_keg_run
  name: A Keg for the Bar
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
        mob_template: saloon_bartender
    visible_if: {}
    accept_if: {}
    salience: 25
    cooldown_seconds: 0
  slots: {}
  steps:
    - id: deliver
      kind: objective
      recap: The bartender needs a fresh keg from the back room.
      text:
        body: |
          "Could you grab a keg from the back for me?" the bartender asks.
          "I can't leave the bar unattended."
      objectives:
        - id: deliver_keg
          text: Bring the saloon keg to the bartender.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.template_id, saloon_bartender]
                - eq: [event.item.template_id, saloon_keg]
          progress:
            mode: count
            target: 1
      transitions:
        - when:
            objective_complete: deliver_keg
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The bartender rolls the fresh keg into place.
  rewards:
    complete:
      - type: grant_gold
        amount: 10
      - type: grant_xp
        amount: 50
      - type: mob_command
        mob_template: saloon_bartender
        command: say Much obliged. Drinks keep flowing now.
    compromised: []
    failed_forward: []
    expired: []
```

Notes:

- `saloon_bartender` is a mob template slug reference.
- `saloon_keg` is an item template slug reference.
- The quest pitch text lives in the first step’s `text.body`.
- The player still accepts explicitly with `quest accept saloon_keg_run`.
- Turn-in happens with `give keg bartender`, not with `quest complete`.
- Picking up the keg does not change the journal, because this quest progresses
  from `quest.item.delivered`, not from `get`.

## How Journal Entries Work

Builders do not author quest journal entries directly today. The runtime writes
them automatically from quest state changes.

What creates a journal entry:

- entering a new step writes a journal entry using that step's `recap`
- updating objective progress writes another journal entry using the current
  step's `recap`
- resolving a quest writes a journal entry using the resolution step's `recap`
- abandoning a quest writes a journal entry with `You abandoned <quest name>.`

What the player sees in `quest recap`:

- `Recap` is the current step's `recap`
- `Objectives` come from the current step's visible objectives
- `Choices` come from the current step's visible choices
- `Last change` is the newest journal entry's `recap`

This has two important consequences for authors:

- if an objective updates but the quest stays on the same step, `Recap` and
  `Last change` may be identical because both are driven by that step's
  `recap`
- if you want the journal to feel meaningfully different after progress, split
  the flow into more steps with new `recap` text instead of expecting a
  separate per-objective journal message

Applied to the bartender example above:

- `quest accept saloon_keg_run` creates a journal entry with `The bartender
  needs a fresh keg from the back room.`
- `get keg` does not create a journal entry
- `give keg bartender` completes the objective and immediately transitions to
  `resolved`, so the newest journal entry becomes `The bartender rolls the
  fresh keg into place.`

## Kill Then Return Example

This is the other common loop now supported in game.

```text
Captain Merrow stands watch here. [ ! ]

talk captain
Quest available: Rat Cull
Captain Merrow wants the tunnel rats culled.
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
```

The authoring pattern is:

- step 1 objective tracks `quest.mob.killed`
- transition goes to a report-back objective step
- report-back objective tracks `cmd.talk.success`
- that step transitions into `resolution`

## Builder Rules Of Thumb

- If the player should notice a quest by talking to an NPC, use
  `discovery.sources: [{type: npc_dialogue, mob_template: <slug>}]`.
- If the player should commit to the quest, keep acceptance explicit with
  `quest accept <slug>`.
- Put the NPC’s actual ask in the first step’s `text.body`.
- Put the memory aid and immediate player-facing summary in `recap`.
- For item turn-ins, progress from `quest.item.delivered`.
- For report-back steps, progress from `cmd.talk.success`.
- Use slugs for mob and item template references whenever possible. The runtime
  accepts ids too, but slugs are much easier to read.

## Testing Your Quest

Recommended smoke-test loop:

1. Apply the manifest in `World > Edit World`.
2. Move the player into the quest room.
3. `look` and confirm `[ ! ]` appears on the offering NPC.
4. `talk <mob>` and confirm the authored pitch plus `quest accept <slug>`.
5. `quest accept <slug>`.
6. Perform the required world actions.
7. Return and `look` again for `[ ? ]`.
8. Use the relevant world verb to finish:
   `talk <mob>` or `give <item> <mob>`.
9. `quest recap` if the flow feels unclear at any point.

## Current Limitation To Remember

The `?` icon means “this NPC is the ready completion point now,” not “typing
`talk` will always finish it.”

Examples:

- report-back quest: `?` usually means `talk captain`
- item hand-in quest: `?` usually means `give keg bartender`

That distinction is important and is the reason the system currently does not
have a generic `quest complete` command.
