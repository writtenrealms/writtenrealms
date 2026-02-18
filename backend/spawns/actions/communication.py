from __future__ import annotations

from spawns.actions.base import ActionError, ActionResult
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


def _normalize_text(text: str | None) -> str:
    return str(text or "").strip()


def _actor_payload(actor: Player | Mob) -> dict:
    if isinstance(actor, Player):
        return serialize_char_from_player(actor).model_dump()
    return serialize_char_from_mob(actor).model_dump()


def _room_player_recipient_ids(actor: Player | Mob) -> list[int]:
    room_id = getattr(actor, "room_id", None)
    if not room_id:
        return []

    qs = Player.objects.filter(
        room_id=room_id,
        in_game=True,
    )
    if isinstance(actor, Player):
        qs = qs.exclude(pk=actor.id)
    return list(qs.values_list("id", flat=True))


def _zone_player_recipient_ids(actor: Player | Mob) -> list[int]:
    room = getattr(actor, "room", None)
    zone_id = getattr(room, "zone_id", None)
    if not zone_id:
        return []

    qs = Player.objects.filter(
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

        if isinstance(actor, Player) and actor.is_muted:
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

        return ActionResult(events=events)


class YellAction:
    def execute(self, actor: Player | Mob, text: str | None) -> ActionResult:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError("What do you want to yell?", code="invalid_args")

        if isinstance(actor, Player) and actor.is_muted:
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

        return ActionResult(events=events)


class EmoteAction:
    def execute(self, actor: Player | Mob, text: str | None) -> ActionResult:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            raise ActionError("What do you want to express?", code="invalid_args")

        if isinstance(actor, Player) and actor.is_muted:
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

        return ActionResult(events=events)
