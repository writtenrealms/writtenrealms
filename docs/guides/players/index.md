# Player Guides

These guides cover the player-facing systems currently available in Written
Realms Core.

## Entering a World

On a server featuring one main world, the homepage and **Lobby** open that
world directly. Read its description, create a character, or choose **PLAY AS**
to continue an existing character. Private worlds require an account with
access. Servers configured for multiple worlds show a **Worlds** directory
instead; direct links to a world work in either setup.

## First Steps: Ask for Help

Unsure where to go or how something works? Type `ask` followed by your question
in the command input, then press Enter:

```text
ask I'm new here. What should I do first?
```

Players listening to the **ask** channel in your world will hear your question
and can reply. New characters listen automatically, and **any player listening
can answer**. When someone answers your question, you see a message such as
`Alden answers you 'Try the harbor merchant.'`

To help another player, use `answer <message>` for the latest question. To
answer a specific question, click its gray number, such as `[ 3 ]`, to prepare
`answer 3 `, then type your reply and press Enter.

Use `listen ask off` if you want to stop hearing other players' questions.
You can still use `ask` and receive replies to your own questions while online
in that world. Use `listen ask on` to hear and answer questions again.

See [Asking for Help and Communication](communication.md) for the full command
reference, other chat channels, and private messages.

## Explore the Guides

- [Asking for Help and Communication](communication.md)
- [Map](map.md)
- [Following](following.md)
- [Doors and Keys](doors-and-keys.md)
- [Combat](combat.md)
- [Abilities and Training](abilities.md)
- [Duels](duels.md)
- [Currencies](currencies.md)
- [Merchants](merchants.md)
- [Scripted World Interactions](scripted-interactions.md)
- [Crafting](crafting-player-guide.md)
- [Socials](socials.md)

## World Introductions

Some worlds and instances show a full-screen introduction when you enter.
It may appear all at once, reveal another paragraph every three seconds, or
replace each paragraph with the next every three seconds. Click or tap anywhere
to dismiss it at any time; Enter, Space, and Escape work too. Long messages can
be scrolled. The world continues running while the introduction is visible.

## Command Output

Commands appear directly above their first response, including quest
confirmations. Follow-up narration and separate events have paragraph spacing
to keep each exchange easy to scan. A uniform small gap separates commands from
their first response, whether that response is text or a quest card.

## Quest Log

Some quests offer work directly in the room, such as **WASH CLAY**. Click the
room button or type its command. You will see short narrated updates while the
work runs, followed by the next available action. Stay in the room until that
action finishes: leaving interrupts the current work, but keeps completed quest
steps. Repeating the command does not start a second copy. These work sequences
follow instance time when playing in a paused adventure.
The same work button appears in `quest info`, quest update cards, and the Quest
Log while the action is available in your current room. It disappears while
the work runs or when you leave, so you do not need to issue `look` to use it.

Open **QUESTS** in the sidebar, or **Quest Log** in the mobile menu, to browse
Active, Repeatable, and Resolved quests. Each entry uses the same card as
`quest info <slug>`, with a status badge, story text, recap, and objective
progress. Click a title to expand or collapse its details. Repeatable quests
show when they will be ready again; **INFO** opens an active quest in the console.
**ABANDON**, beside **INFO** on active quests, ends the current attempt using
the same command as `quest abandon <slug>`.
Click the highlighted quest name in acceptance, progress, or abandonment
messages to display its info in the console.
The log keeps the same size when switching tabs. Longer lists scroll below the
tabs, keeping the title and tab buttons in place.

Quests belonging to an instance start fresh in each new run. Leaving hides
that run's quests; returning to the same retained run resumes your progress.
Base-world quests stay with your character. Some repeatable quests reset at a
fixed real-world time each day. Their countdown continues while an instance is
paused, and the Quest Log shows when they become available again. Unfinished
dailies survive a reset; completing one uses the day in which you finish it.
Abandoning one does not consume that day's completion. Some quests give extra
pay on your first successful completion; that bonus does not return at reset.

Adventures may separately record
lasting outcomes, but an instance quest's completion does not carry into the
next run.

## Clearing the Console

The console follows new output and changing room actions while you are at the
bottom, including replies from **TALK** and other popup actions. It keeps
following as older messages leave the 200-message history. Scroll up to read
earlier messages without being pulled back down. On
desktop, **JUMP TO BOTTOM** returns to the latest output and resumes following.

Type `clear` on its own to empty the game console on desktop or mobile. New
messages appear normally afterward. Your character, map, combat, and separate
logs are unaffected, and commands already in progress continue to run. The
command works immediately, including while combat is paused or the connection
is unavailable.

## Account Preferences

Some independently hosted servers require an invitation. On those servers,
use an email address approved by the administrator when signing in or creating
an account. Email, Google, and Alpha sign-in follow the same admission policy;
guest play is unavailable. Contact the administrator if your address has not
been admitted. An invitation grants access to the server; it does not grant
builder or administrator permissions.

The account menu lets you update your profile name and preferences. Your login
email and account verification are managed separately from those settings;
profile edits cannot change them. Email login links establish ownership of the
address used to sign in. Contact staff if you need help changing that address.

## Signing in from Alpha

When Alpha offers a Core world in **Featured Worlds**, open its card to enter
Core using your Alpha account. If you are signed out of Alpha, log in there
first; the handoff then continues to the selected Core world.

A confirmed Alpha email can create your Core account without another email
step. If you already have a Core account with that address, or your Alpha email
is unconfirmed, Core asks for a one-time eight-digit email code before connecting
the accounts. Keep the Core tab open and enter the code there. Later visits use
the saved connection. If the browser is signed into a different Core account,
Core asks before switching accounts.

Characters and progress stay separate between Alpha and Core. Signing out of
one site does not sign you out of the other. If a handoff expires, return to
Alpha and open the world again; ordinary Core email login also remains available.

## Client Preferences

Open the game menu, choose **Settings**, and use **Save Changes** to apply
preferences to your character. They remain saved when you reconnect.

- **Room Brief Mode** hides full descriptions on movement and other automatic
  room displays. Room names, exits, items, characters, and available actions
  remain visible. Type `look` (or `l`) to read the full description.
- **Combat Brief Mode** shows compact combat lines with attack or ability
  labels and aligned damage or healing amounts. Dodges, absorbed damage,
  critical hits, and combat effect durations remain visible. Turn it off to
  return to full combat sentences.

The two modes work independently on desktop and mobile, and saving them also
updates the messages already in your console.
