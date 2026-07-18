from __future__ import annotations

from typing import Any

from core.socials import (
    SOCIAL_TARGETED_FIELDS,
    SOCIAL_TARGETLESS_FIELDS,
    SocialDefinitionError,
    build_social_template_context,
    normalize_social_command,
    render_social_template,
)
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.communication import MUTED_ERROR
from spawns.actions.targeting import find_room_char_target
from spawns.events import GameEvent
from spawns.models import Mob, Player


def _character_payload(character: Player | Mob) -> dict[str, Any]:
    return {
        "id": character.id,
        "key": character.key,
        "name": str(character.name or "someone"),
        "title": str(character.title or ""),
        "gender": str(character.gender or ""),
        "type": "player" if isinstance(character, Player) else "mob",
    }


def _room_payload(actor: Player | Mob) -> dict[str, Any]:
    room = actor.room
    return {
        "id": room.id,
        "key": room.key,
        "name": str(room.name or ""),
    }


def _has_complete_mode(social: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(str(social.get(field_name) or "").strip() for field_name in fields)


def _room_player_recipient_keys(
    actor: Player | Mob,
    *,
    target: Player | Mob | None = None,
) -> list[str]:
    if not actor.room_id or not actor.world_id:
        return []

    recipients = Player.objects.filter(
        room_id=actor.room_id,
        world_id=actor.world_id,
        in_game=True,
    )
    excluded_ids: list[int] = []
    if isinstance(actor, Player):
        excluded_ids.append(actor.id)
    if isinstance(target, Player):
        excluded_ids.append(target.id)
    if excluded_ids:
        recipients = recipients.exclude(pk__in=excluded_ids)
    return [
        f"player.{player_id}"
        for player_id in recipients.order_by("id").values_list("id", flat=True)
    ]


def _target_has_muted_actor(target: Player, actor: Player | Mob) -> bool:
    muted_names = {
        token.strip().casefold()
        for token in str(target.mute_list or "").split()
        if token.strip()
    }
    return str(actor.name or "").strip().casefold() in muted_names


def _personal_mute_error(target: Player) -> str:
    subject = str(target.pronouns[0] or "they")
    subject = subject[:1].upper() + subject[1:]
    return f"{subject} doesn't want to interact with you."


class SocialAction:
    """Render and fan out one cached, authored social definition."""

    def execute(
        self,
        actor: Player | Mob,
        social: dict[str, Any],
        target_selector: str | None = None,
    ) -> ActionResult:
        if not actor.room_id or not getattr(actor, "room", None):
            raise ActionError(
                "You are nowhere. Cannot use a social.",
                code="no_room",
            )
        if not actor.world_id:
            raise ActionError("The social is unavailable.", code="no_world")
        if isinstance(actor, Player) and actor.is_muted:
            raise ActionError(MUTED_ERROR, code="muted")

        command = normalize_social_command(social.get("command"))
        if not command:
            raise ActionError("The social is unavailable.", code="invalid_social")

        normalized_target = str(target_selector or "").strip()
        has_targetless = _has_complete_mode(social, SOCIAL_TARGETLESS_FIELDS)
        has_targeted = _has_complete_mode(social, SOCIAL_TARGETED_FIELDS)

        target: Player | Mob | None = None
        targeted = bool(normalized_target and has_targeted)
        if targeted:
            can_see_invisible = bool(getattr(actor, "is_builder", False))
            target = find_room_char_target(
                actor.room,
                normalized_target,
                viewer=actor if isinstance(actor, Player) else None,
                world=actor.world,
                exclude=actor,
                lean=True,
                include_invisible_players=can_see_invisible,
                include_invisible_mobs=can_see_invisible,
            )
            if target is None:
                raise ActionError("They aren't here.", code="target_not_found")
            if isinstance(target, Player) and _target_has_muted_actor(target, actor):
                raise ActionError(
                    _personal_mute_error(target),
                    code="target_muted",
                )
        elif not has_targetless:
            raise ActionError("A target is required.", code="target_required")

        if targeted:
            template_fields = SOCIAL_TARGETED_FIELDS
        else:
            template_fields = SOCIAL_TARGETLESS_FIELDS
        templates = [str(social.get(field_name) or "") for field_name in template_fields]
        try:
            context = build_social_template_context(
                actor=actor,
                target=target,
                templates=templates,
            )
        except SocialDefinitionError:
            raise ActionError(
                "The social is unavailable.",
                code="invalid_social",
            )

        actor_data = _character_payload(actor)
        target_data = _character_payload(target) if target is not None else None
        data: dict[str, Any] = {
            "social": command,
            "social_id": social.get("id"),
            "actor": actor_data,
            "room": _room_payload(actor),
        }
        if target_data is not None:
            data["target"] = target_data

        actor_template = (
            social["msg_targeted_self"]
            if targeted
            else social["msg_targetless_self"]
        )
        events = [
            GameEvent(
                type="cmd.dosocial.success",
                recipients=[actor.key],
                data=data,
                text=render_social_template(actor_template, context),
            )
        ]

        if targeted and target is not None:
            # A mob has no player websocket recipient, but the event still runs
            # through subscriptions so only that directly targeted mob can react.
            direct_recipients = [target.key] if isinstance(target, Player) else []
            events.append(
                GameEvent(
                    type="affect.social",
                    recipients=direct_recipients,
                    data=data,
                    text=render_social_template(
                        social["msg_targeted_target"],
                        context,
                    ),
                )
            )

        witness_recipients = _room_player_recipient_keys(actor, target=target)
        if witness_recipients:
            witness_template = (
                social["msg_targeted_other"]
                if targeted
                else social["msg_targetless_other"]
            )
            events.append(
                GameEvent(
                    type="notification.social",
                    recipients=witness_recipients,
                    data=data,
                    text=render_social_template(witness_template, context),
                )
            )

        return ActionResult(events=events, data=data)
