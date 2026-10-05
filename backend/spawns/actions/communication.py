from __future__ import annotations

import re

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.communications import record_answer, record_communication, record_question
from spawns.events import GameEvent
from spawns.models import Mob, Player
from spawns.state_payloads import serialize_char_from_mob, serialize_char_from_player
from spawns.text_output import render_event_text

MUTED_ERROR = (
    "Your communication privileges have been removed, "
    "you can only send tells to builders."
)
SAY_LIMIT = 280
EMOTE_LIMIT = 560
CHANNEL_LIMIT = 560
PUBLIC_CHANNELS = ("ask", "chat", "gossip")


def _is_muted(actor: Player | Mob) -> bool:
    return isinstance(actor, Player) and (actor.is_muted or actor.user.is_muted)


def _channel_names(actor: Player) -> set[str]:
    return set(str(actor.channels or "").lower().split())


def _communication_identity(actor: Player) -> dict:
    # Chat does not need combat stats, equipment, faction or room state.
    return {"id": actor.id, "key": actor.key, "name": actor.name,
            "is_builder": actor.is_builder}


def _word_pattern(value: str) -> str:
    return rf"(^|[[:space:]]){re.escape(value)}([[:space:]]|$)"


def _communication_events(actor: Player, command: str, data: dict,
                          recipient_ids: list[int]) -> ActionResult:
    events = [GameEvent(
        type=f"cmd.{command}.success", recipients=[actor.key], data=data,
        text=render_event_text(f"cmd.{command}.success", data, viewer=actor),
    )]
    if command == "answer":
        asker_id = (data.get("target") or {}).get("id")
        if asker_id in recipient_ids:
            # Personalize only the asker; other listeners still share one event.
            # Use the already-filtered audience to preserve mutes and presence.
            recipient_ids = [pk for pk in recipient_ids if pk != asker_id]
            event_type = "notification.cmd.answer.success"
            events.append(GameEvent(
                type=event_type, recipients=[f"player.{asker_id}"], data=data,
                text=render_event_text(event_type, data, viewer_id=asker_id),
            ))
    if recipient_ids:
        event_type = f"notification.cmd.{command}.success"
        events.append(GameEvent(
            type=event_type,
            recipients=[f"player.{pk}" for pk in recipient_ids], data=data,
            text=render_event_text(event_type, data, viewer=None),
        ))
    return ActionResult(events=events, data=data)


def _normalize_text(text: str | None) -> str:
    return str(text or "").strip()


def _actor_payload(actor: Player | Mob) -> dict:
    if isinstance(actor, Player):
        return serialize_char_from_player(actor).model_dump()
    return serialize_char_from_mob(actor).model_dump()


def _room_player_recipient_ids(actor: Player | Mob) -> list[int]:
    room_id = getattr(actor, "room_id", None)
    world_id = getattr(actor, "world_id", None)
    if not room_id or not world_id:
        return []

    qs = Player.objects.filter(
        world_id=world_id,
        room_id=room_id,
        in_game=True,
    )
    if isinstance(actor, Player):
        qs = qs.exclude(pk=actor.id)
    return list(qs.values_list("id", flat=True))


def _zone_player_recipient_ids(actor: Player | Mob) -> list[int]:
    room = getattr(actor, "room", None)
    zone_id = getattr(room, "zone_id", None)
    world_id = getattr(actor, "world_id", None)
    if not zone_id or not world_id:
        return []

    qs = Player.objects.filter(
        world_id=world_id,
        room__zone_id=zone_id,
        in_game=True,
    )
    if isinstance(actor, Player):
        qs = qs.exclude(pk=actor.id)
    return list(qs.values_list("id", flat=True))


class SayAction:
    def execute(self, actor: Player | Mob, text: str | None) -> ActionResult:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError("Say what?", code="invalid_args")

        if _is_muted(actor):
            raise ActionError(MUTED_ERROR, code="muted")

        if isinstance(actor, Player):
            normalized_text = normalized_text[:SAY_LIMIT]

        data = {
            "actor": _actor_payload(actor),
            "text": normalized_text,
        }
        actor_text = render_event_text(
            "cmd.say.success",
            data,
            viewer=actor if isinstance(actor, Player) else None,
        )

        events = [
            GameEvent(
                type="cmd.say.success",
                recipients=[actor.key],
                data=data,
                text=actor_text,
            )
        ]

        recipient_ids = _room_player_recipient_ids(actor)
        if recipient_ids:
            notify_text = render_event_text(
                "notification.cmd.say.success",
                data,
                viewer=None,
            )
            events.append(
                GameEvent(
                    type="notification.cmd.say.success",
                    recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                    data=data,
                    text=notify_text,
                )
            )

        record_communication(actor, "say", normalized_text)
        return ActionResult(events=events)


class YellAction:
    def execute(self, actor: Player | Mob, text: str | None) -> ActionResult:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError("What do you want to yell?", code="invalid_args")

        if _is_muted(actor):
            raise ActionError(MUTED_ERROR, code="muted")

        if isinstance(actor, Player):
            normalized_text = normalized_text[:SAY_LIMIT]

        data = {
            "actor": _actor_payload(actor),
            "text": normalized_text,
        }
        actor_text = render_event_text(
            "cmd.yell.success",
            data,
            viewer=actor if isinstance(actor, Player) else None,
        )

        events = [
            GameEvent(
                type="cmd.yell.success",
                recipients=[actor.key],
                data=data,
                text=actor_text,
            )
        ]

        recipient_ids = _zone_player_recipient_ids(actor)
        if recipient_ids:
            notify_text = render_event_text(
                "notification.cmd.yell.success",
                data,
                viewer=None,
            )
            events.append(
                GameEvent(
                    type="notification.cmd.yell.success",
                    recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                    data=data,
                    text=notify_text,
                )
            )

        record_communication(actor, "yell", normalized_text)
        return ActionResult(events=events)


class EmoteAction:
    def execute(self, actor: Player | Mob, text: str | None) -> ActionResult:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError("What do you want to express?", code="invalid_args")

        if _is_muted(actor):
            raise ActionError(MUTED_ERROR, code="muted")

        normalized_text = normalized_text[:EMOTE_LIMIT]
        data = {
            "actor": _actor_payload(actor),
            "text": normalized_text,
        }

        actor_text = render_event_text(
            "cmd.emote.success",
            data,
            viewer=actor if isinstance(actor, Player) else None,
        )
        events = [
            GameEvent(
                type="cmd.emote.success",
                recipients=[actor.key],
                data=data,
                text=actor_text,
            )
        ]

        recipient_ids = _room_player_recipient_ids(actor)
        if recipient_ids:
            notify_text = render_event_text(
                "notification.cmd.emote.success",
                data,
                viewer=None,
            )
            events.append(
                GameEvent(
                    type="notification.cmd.emote.success",
                    recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                    data=data,
                    text=notify_text,
                )
            )

        record_communication(actor, "emote", normalized_text)
        return ActionResult(events=events)


class TalkAction:
    def execute(self, actor: Player, target_selector: str | None) -> ActionResult:
        if not actor.room_id or not getattr(actor, "room", None):
            raise ActionError("You are nowhere. Cannot talk to anyone.", code="no_room")

        target_mob = resolve_room_mob_target(
            actor.room,
            target_selector,
            world=actor.world,
            empty_error="Talk to whom?",
            not_found_error="You don't see them here.",
            allow_single_match_when_empty=True,
        )
        if not target_mob.talkable:
            raise ActionError("You cannot talk to them.", code="not_talkable")

        data = {
            "actor": _actor_payload(actor),
            "target": serialize_char_from_mob(target_mob).model_dump(),
        }
        actor_text = render_event_text(
            "cmd.talk.success",
            data,
            viewer=actor,
        )
        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.talk.success",
                    recipients=[actor.key],
                    data=data,
                    text=actor_text,
                )
            ]
        )


class ChannelAction:
    """One channel message and one batched audience lookup.

    A future clan action can override validate_membership/recipients and reuse
    publication and the same moderation log without changing the public channels.
    """
    def validate_membership(self, actor: Player, channel: str) -> None:
        if channel not in PUBLIC_CHANNELS:
            raise ActionError("Unknown channel.", code="invalid_channel")
        if channel not in _channel_names(actor):
            raise ActionError(
                f"You are not listening to {channel}. Use 'listen {channel} on' first.",
                code="not_listening",
            )

    def recipients(self, actor: Player, channel: str, *,
                   asker_id: int | None = None) -> list[int]:
        listening = Q(channels__regex=_word_pattern(channel))
        if asker_id is not None:
            # Include the asker even when they have opted out of other questions.
            listening |= Q(pk=asker_id)
        audience = Player.objects.filter(
            listening,
            world_id=actor.world_id, in_game=True,
        ).exclude(pk=actor.pk)
        if not actor.is_builder:
            audience = audience.exclude(mute_list__iregex=_word_pattern(actor.name))
        return list(audience.values_list("id", flat=True))

    def execute(self, actor: Player, command: str, text: str | None,
                question_id: int | None = None) -> ActionResult:
        channel = "ask" if command == "answer" else command
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError(f"What do you want to {command}?", code="invalid_args")
        if _is_muted(actor):
            raise ActionError(MUTED_ERROR, code="muted")
        if actor.nochat or actor.user.nochat:
            raise ActionError("Your channel communication privileges have been removed.",
                              code="nochat")
        if command != "ask":
            self.validate_membership(actor, channel)
        normalized_text = normalized_text[:CHANNEL_LIMIT]
        # Event dispatch can use this context without looking up the actor's
        # world again for each audience's version of the message.
        data = {"actor": _communication_identity(actor), "text": normalized_text,
                "channel": channel, "is_builder": actor.is_builder,
                "world_id": actor.world_id}

        asker_id = None
        if command == "ask":
            question = record_question(actor, normalized_text)
            data["question_id"] = question.question_number
        elif command == "answer":
            if question_id is not None and (question_id < 1 or question_id > 2**63 - 1):
                raise ActionError("Question IDs must be positive numbers.", code="invalid_question")
            answer = record_answer(actor, normalized_text, question_id)
            if answer is None:
                raise ActionError("No such recent question in this world session.",
                                  code="question_not_found")
            data["question_id"] = answer.answer_to_question_number
            data["target"] = {"id": answer.target_id, "name": answer.target_name}
            asker_id = answer.target_id
        else:
            record_communication(actor, channel, normalized_text)
        return _communication_events(actor, command, data, self.recipients(
            actor, channel, asker_id=asker_id,
        ))


class ListenAction:
    def execute(self, actor: Player, channel: str | None = None,
                mode: str | None = None) -> ActionResult:
        channel = str(channel or "").strip().lower()
        mode = str(mode or "").strip().lower()
        if channel and channel not in PUBLIC_CHANNELS:
            raise ActionError("Available channels: ask, chat, gossip.", code="invalid_channel")
        if mode not in {"", "on", "off"} or (mode and not channel):
            raise ActionError("Use listen [ask|chat|gossip] [on|off].", code="invalid_args")
        data = {}
        if channel:
            # Lock only this player's preference update so concurrent toggles
            # cannot overwrite subscriptions to a different channel.
            with transaction.atomic():
                player = Player.objects.select_for_update().only("channels").get(pk=actor.pk)
                channels = _channel_names(player)
                listening = mode == "on" or (not mode and channel not in channels)
                if listening:
                    channels.add(channel)
                else:
                    channels.discard(channel)
                player.channels = " ".join(sorted(channels))
                player.save(update_fields=["channels"])
                actor.channels = player.channels
            data.update(channel=channel, listening=listening)
        data["channels"] = [name for name in PUBLIC_CHANNELS if name in _channel_names(actor)]
        return _communication_events(actor, "listen", data, [])


class DirectMessageAction:
    def execute(self, actor: Player, command: str, target_selector: str | None,
                text: str | None) -> ActionResult:
        selector = str(target_selector or "").strip().lower()
        normalized_text = _normalize_text(text)
        if not selector or not normalized_text:
            raise ActionError(f"Use {command} <player> <message>.", code="invalid_args")
        candidates = Player.objects.filter(world_id=actor.world_id, in_game=True)
        if command == "whisper":
            if not actor.room_id:
                raise ActionError("You are nowhere. Cannot whisper.", code="no_room")
            candidates = candidates.filter(room_id=actor.room_id)
        if not actor.is_builder:
            candidates = candidates.filter(is_invisible=False)
        # Use the existing (world, lower(name)) live-player index; don't fetch
        # every character just to match a name.
        target = candidates.alias(lower_name=Lower("name")).filter(
            lower_name=selector,
        ).only("id", "name", "is_builder", "user_id", "mute_list").order_by("id").first()
        if target is None or target.pk == actor.pk:
            raise ActionError("That player is not available.", code="target_not_found")
        if _is_muted(actor) and not (command == "tell" and target.is_builder):
            raise ActionError(MUTED_ERROR, code="muted")
        muted_names = str(target.mute_list or "").casefold().split()
        if not actor.is_builder and actor.name.casefold() in muted_names:
            raise ActionError("They don't want to interact with you.", code="target_muted")
        normalized_text = normalized_text[:SAY_LIMIT]
        data = {"actor": _communication_identity(actor), "target": _communication_identity(target),
                "text": normalized_text}
        record_communication(actor, command, normalized_text, target=target)
        return _communication_events(actor, command, data, [target.pk])
