# Asking for Help and Communication

Need help getting started or figuring something out? Type `ask` followed by
your question. Players listening to **ask** in your world can answer.

New characters listen to ask automatically, and any player listening can
answer questions. **Chat** and **gossip** are optional conversation channels;
join either when you want to take part.

## Asking And Answering

```text
ask Where can I buy a lantern?
answer At the harbor shop.
answer 3 At the harbor shop.
```

You can ask a question even when you are not listening to ask. Everyone currently
listening in your runtime world hears it. Listeners see a short number afterward
in gray brackets, such as `[ 3 ]`, so they can answer that specific question.
Your own question confirmation does not show a number.

Click a gray question number in the console or Communication Log to fill the
command input with `answer 3 ` (using that question's number) and start typing
your reply. Clicking prepares the command; press Enter when you are ready to
send it. On mobile, this also opens the Type tab.

`answer <message>` answers the latest retained question in your current runtime
world, including a question asked before you joined the channel. To answer an
earlier question, include its number: `answer 3 <message>`. This keeps an answer
attached to the intended question when someone asks another question while you
are typing. Answers reach ask listeners and the original asker, even if the
asker has turned listening off. You must be online in the same runtime world to
receive an answer. The wording depends on who sees it. If Alden answers Mira:

- Alden sees `You answer 'At the harbor shop.'`
- Mira sees `Alden answers you 'At the harbor shop.'`
- Other listeners see `Alden answers Mira 'At the harbor shop.'`

Answers do not display a question number.

A leading number is treated as a question number. If your answer itself begins
with a number, use `answer -- 42 is the answer.` to answer the latest question.

Question numbers start at 1 and restart when the world is restarted. Numbers
belong to the current runtime world and restart session; questions from another
world, another instance, or an earlier restart cannot be answered here. Questions
also stop accepting answers when their three-day retention period expires.

## Choosing Channels

| Command | Effect |
| --- | --- |
| `listen` | Show the channels and whether you are listening. |
| `listen ask` | Toggle ask on or off. |
| `listen ask on` | Start listening to questions and answers. |
| `listen ask off` | Stop hearing other players' questions and their answers. Replies to your own questions still reach you. |
| `listen chat on` | Join chat. |
| `listen gossip on` | Join gossip. |
| `chat <message>` | Speak to chat listeners. |
| `gossip <message>` | Speak to gossip listeners. |

The toggle and explicit `on`/`off` forms work for ask, chat, and gossip. Choices
are saved on your character. You must listen to chat or gossip before sending to
that channel, and listen to ask to use `answer`. The `ask` command works whether
you are listening or not; asking does not change your subscription. Communication
restrictions still apply. Chat and gossip are off for new characters.

Channels reach online listeners throughout the same runtime world. Separate
worlds and parallel instances have separate audiences. Clan chat is not yet
available.

The client's **Communication Log** keeps your latest 200 channel and private
messages from the current session. Incoming questions retain their reply IDs;
your own questions and answers have no displayed ID. The log is separate from
the server's moderation archive.

## Nearby And Private Messages

| Command | Who receives it |
| --- | --- |
| `say <message>` | Other players in your room. |
| `yell <message>` | Other players in your zone. |
| `emote <action>` | Other players in your room, as an action beginning with your name. |
| `tell <player> <message>` | The named online player anywhere in your runtime world. |
| `whisper <player> <message>` | The named online player in your room. |

For example, `tell River Meet me at the gate.` sends River a private message.
`whisper River Follow my lead.` works while River is in the room with you.
Neither sends its message text to room witnesses. A recipient's personal mute
list can prevent direct messages.

Say, yell, tell, and whisper allow up to 280 characters per message. Emotes and
channel messages allow up to 560. Longer messages are shortened to those limits.

Communication restrictions may prevent speaking or using channels. A muted
character can still send a tell to a builder to ask for help.

## Moderation And Retention

Successful communications, including **private tells and whispers**, are saved
in the server's moderation archive for three days. Say, yell, emote, chat,
gossip, questions, and answers are included too. Private delivery determines
which players see a message; it does not exempt the message from moderation.

The archive supports staff review and automated moderation analysis. Expired
records are removed by scheduled cleanup. This retention period concerns the
server archive; it does not erase messages already displayed to their recipients.
