# Abilities and Training

Abilities remain known until you unlearn them. Worlds may grant starting
abilities and may also provide training through a room or an NPC.

## Learning

Use bare `learn` to see a numbered list of the abilities currently available
to you:

```text
learn
```

Then learn one by its displayed number, name, or command:

```text
learn 2
learn power strike
```

Numbers refer to positions in the current local training list, not permanent
ability identifiers. Learning, unlearning, or a provider arriving or leaving
can change the list, so use bare `learn` again when in doubt.

The room display shows **LEARN** and **UNLEARN** actions whenever the room
itself provides training or an available NPC trainer is present. NPC trainers
also show these actions when inspected. The room buttons update as trainers
arrive, leave, or become unavailable, and multiple trainers share one pair of
buttons.

A room that provides training directly remains available without an NPC.
An NPC trainer must be present in your current room and, depending on the
world's authoring, may stop teaching after being defeated.

If an ability is offered by a training room or NPC anywhere in the world, you
must be at an eligible local provider to learn it. Abilities with no attached
training provider remain learnable anywhere once you meet their other
requirements.

Class, level, equipment, quest, and other authored requirements still apply.
Your world may also limit how many abilities you can know. If you are at that
limit, unlearn one before learning another.

Some active abilities belong only to creatures and NPCs. Mob-only abilities do
not appear in player training or help and cannot be granted, assigned to a
hotkey, or used by a player.

Some trainers also offer a limited number of choices from their own catalog.
For example, a cross-training profile may offer seven techniques but let your
class select any two. The learning list shows the profile's eligibility and
your used and remaining selections. A limit belongs to the Trainer Profile,
not to one copy of an NPC or one room, so visiting another provider that uses
the same profile does not grant more choices.

Every ability you currently know from that profile's catalog uses one of its
slots, including starting abilities and abilities granted by a quest, item, or
builder. This remains true if an ability is inactive or you no longer meet its
other requirements. A trainer-specific limit is separate from the world's
total known-ability limit: both must have room before you can learn.

## Unlearning

Bare `unlearn` lists and numbers the known abilities you can unlearn at your
current location:

```text
unlearn
```

Choose one by its displayed number, name, or command:

```text
unlearn 2
unlearn power strike
```

Unlearn numbers likewise refer to the current local list. Use bare `unlearn`
again after your known abilities or available providers change.

Training rooms show an **UNLEARN** action. An ability that requires a provider
for learning requires one for unlearning too. Unlearning removes its hotkey and
cancels a prepared use of that ability when applicable.

Unlearning a catalog ability immediately frees its trainer-profile slot. You
may still unlearn at a local provider if the profile's learning condition no
longer accepts you or you are already over its limit. Worlds do not
automatically remove known abilities when a trainer's limit is reduced.

## Hotkeys and Help

Learning an ability assigns the next available numbered hotkey when one is
open. Reassign a known ability with `hotkey`:

```text
hotkey 3 power strike
```

Use `help <ability>` for an ability you already know or can learn right now.
The help lookup accepts its slug, authored command, exact name, or an
unambiguous name prefix.
