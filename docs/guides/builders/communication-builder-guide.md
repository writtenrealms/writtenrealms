# Communication And Moderation

Core provides an ask channel for game questions, optional chat and gossip
channels, and local and private communication commands. See the
[player communication guide](../players/communication.md) for syntax and
audiences.

## Helping Players

New characters automatically listen to ask. Builders can use `listen ask on` to
hear questions and answer with either `answer <message>` or
`answer <number> <message>`.

Prefer the explicit number when several questions are active. For example,
`answer 3 The harbor merchant sells lanterns.` remains attached to question 3
even if another player asks a question before the answer is sent. Every ask
listener in that runtime world hears the answer, as does the original asker if
they are online there, even with ask listening turned off. Only incoming
questions display the gray reply number, formatted as `[ 3 ]`.
Click that number in the console or Communication Log to prepare `answer 3 `
and focus the command input. It does not send anything until you press Enter.
The answering player sees `You answer 'Try the harbor merchant.'`; the asker
sees `Alden answers you 'Try the harbor merchant.'`. Other listeners see
`Alden answers Mira 'Try the harbor merchant.'` so they can follow overlapping
questions.

The default answer target is the latest retained question in the current
runtime world, rather than the latest question a particular builder personally
heard. Numbers reset to 1 on a world restart and are independent across runtime
worlds and instances. Old sessions and expired questions cannot be answered.

Chat and gossip start off for new characters. Subscriptions are character
preferences, controlled with `listen`. Chat, gossip, and answering require the
corresponding subscription. Players can ask questions without listening to ask;
asking does not subscribe them. They can leave ask whenever they choose and
still receive replies to their own questions. Clan chat is reserved for future
clan support and is not an available command or subscription yet.

## Restrictions

Character and account communication mutes block freeform communication. Muted
players retain the ability to send tells to builders. Character and account
channel restrictions also prevent sending to ask, chat, and gossip, including
answers. A target's personal mute list prevents tells and whispers from that
sender.

These restrictions are checked before a message is broadcast or archived.
Failed commands do not create communication records. Local messages, tells,
and whispers retain their room, zone, and runtime-world boundaries.

## Moderation Archive

Successful say, yell, emote, tell, whisper, chat, gossip, ask, and answer
messages are persisted in PostgreSQL as communication records. The default
retention period is three days, followed by scheduled cleanup. This includes
private messages; communicate that policy to players as part of world rules.

Each record stores one original message, rather than a duplicate for every
recipient. Runtime-world and speaker context lets moderation queries group
messages by world, participant, channel, and time. Ask and answer records retain
their session and question relationship so reused short numbers from different
restarts remain distinguishable during review.

This archive supplies durable data for staff review and future Celery-based AI
moderation. Staff can inspect records in Django admin as described below;
automated review requires a separate moderation integration. Such integrations
should query unexpired records and process them in bounded batches. Retention
cleanup runs separately from the player command path.

The archive is runtime data. It is not part of authored-world YAML manifests or
world content exports, and it does not provide players with a chat-history
command.

### Staff Review

Server staff can inspect **Spawns > Communication messages** in Django admin.
Grant the `spawns.view_communicationmessage` permission to staff who should
review this data; superusers also have access. World-builder status alone does
not grant access to the server archive. The admin is read-only: staff can view
and search message text, sender names, and target names, and filter by channel
and creation time. They cannot add, edit, or delete records there.

For server-side review jobs, query `spawns.models.CommunicationMessage`. For
example, this retrieves at most 200 recent unexpired messages for one runtime
world:

```python
from django.utils import timezone
from spawns.models import CommunicationMessage

messages = list(
    CommunicationMessage.objects
    .filter(world_id=runtime_world_id, expires_at__gt=timezone.now())
    .order_by("-created_ts", "-id")
    .values("id", "created_ts", "channel", "sender_name", "target_name", "text")[:200]
)
```

### Retention Settings

Server operators can set these environment variables:

| Setting | Default | Effect |
| --- | --- | --- |
| `COMMUNICATION_RETENTION_DAYS` | `3` | Real-time retention in whole days for newly recorded messages; minimum 1 day. Existing records keep their expiry. |
| `COMMUNICATION_PRUNE_BATCH_SIZE` | `10000` | Maximum expired records removed per cleanup task; clamped to 1–10,000. |

Celery Beat schedules `spawns.tasks.prune_communication_messages` once per
minute. Each run deletes one bounded batch in expiry order and skips rows
already locked by another cleanup worker. Both Beat and a worker must be
running for cleanup to proceed. If an expiry backlog exceeds one batch, later
runs continue removing it. Question lookup rejects expired questions immediately,
even while their records await physical deletion.
