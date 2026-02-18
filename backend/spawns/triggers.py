from __future__ import annotations

import re
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Q

from builders.models import ItemTemplate, MobTemplate, Trigger
from config import constants as adv_consts
from core.conditions import evaluate_conditions
from spawns.handlers.registry import (
    ActorNotFoundError,
    HandlerNotFoundError,
    dispatch_command,
    resolve_text_handler,
)
from spawns.models import Item, Mob, Player
from worlds.models import Room, World, Zone


TRIGGER_GATED_TEXT = "More time is needed."
DEFAULT_CONDITION_FAILURE_TEXT = "Action could not be completed."
TRIGGER_ACTION_SPLIT_RE = re.compile(r"\s+or\s+")
TRIGGER_SCOPE_PRIORITY = {
    adv_consts.TRIGGER_SCOPE_ROOM: 0,
    adv_consts.TRIGGER_SCOPE_ZONE: 1,
    adv_consts.TRIGGER_SCOPE_WORLD: 2,
}
_scope_content_types_cache: dict[type, ContentType] | None = None


@dataclass(frozen=True)
class TriggerExecutionResult:
    handled: bool
    feedback: str | None = None


def _normalized_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def _iter_action_tokens(actions_text: str | None) -> list[str]:
    actions = _normalized_text(actions_text)
    if not actions:
        return []
    return [token.strip() for token in TRIGGER_ACTION_SPLIT_RE.split(actions) if token.strip()]


def _first_action_label(actions_text: str | None) -> str | None:
    tokens = _iter_action_tokens(actions_text)
    if tokens:
        return tokens[0]
    return None


def _actions_match(actions_text: str | None, command_text: str) -> bool:
    if not command_text:
        return False
    return command_text in _iter_action_tokens(actions_text)


def _split_trigger_script(script: str | None) -> list[str]:
    segments: list[str] = []
    for line in str(script or "").splitlines():
        for chunk in line.split("&&"):
            segment = chunk.strip()
            if segment:
                segments.append(segment)
    return segments


def _first_token(cmd: str) -> str | None:
    stripped = cmd.strip()
    if not stripped:
        return None
    return stripped.split()[0].lower()


def _first_dispatched_error(messages: list[dict]) -> str | None:
    for message in messages:
        msg_type = str(message.get("type", "")).lower()
        if not msg_type.endswith(".error"):
            continue
        text = message.get("text")
        if text:
            return str(text)
        data = message.get("data", {})
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
        return "Nested command failed."
    return None


def _actor_kind(actor: Player | Mob) -> str:
    return "player" if isinstance(actor, Player) else "mob"


def _scope_content_types() -> dict[type, ContentType]:
    global _scope_content_types_cache
    if _scope_content_types_cache is None:
        _scope_content_types_cache = ContentType.objects.get_for_models(
            Item,
            ItemTemplate,
            Mob,
            MobTemplate,
            Room,
            World,
            Zone,
        )
    return _scope_content_types_cache


def _resolve_trigger_world(actor: Player | Mob, room: Room | None) -> World | None:
    if room and room.world_id:
        return room.world

    actor_world = getattr(actor, "world", None)
    if not actor_world:
        return None

    context_world = getattr(actor_world, "context", None)
    if context_world:
        return getattr(context_world, "instance_of", None) or context_world

    return getattr(actor_world, "instance_of", None) or actor_world


def _get_applicable_command_fallback_triggers(
    actor: Player | Mob,
    *,
    room: Room | None = None,
) -> tuple[list[Trigger], Room | None, Zone | None, World | None]:
    resolved_room = room or getattr(actor, "room", None)
    if not resolved_room:
        return [], None, None, None

    trigger_world = _resolve_trigger_world(actor, resolved_room)
    if not trigger_world:
        return [], resolved_room, resolved_room.zone, None

    resolved_zone = resolved_room.zone
    cts = _scope_content_types()
    room_ct = cts[Room]
    zone_ct = cts[Zone]
    world_ct = cts[World]

    scope_filter = (
        Q(
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=world_ct,
            target_id=trigger_world.id,
        )
        | Q(
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type__isnull=True,
            target_id__isnull=True,
        )
    )
    if resolved_zone and resolved_zone.id:
        scope_filter |= Q(
            scope=adv_consts.TRIGGER_SCOPE_ZONE,
            target_type=zone_ct,
            target_id=resolved_zone.id,
        )
    scope_filter |= Q(
        scope=adv_consts.TRIGGER_SCOPE_ROOM,
        target_type=room_ct,
        target_id=resolved_room.id,
    )

    triggers = list(
        Trigger.objects.filter(
            world_id=trigger_world.id,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            is_active=True,
        )
        .filter(scope_filter)
        .order_by("order", "created_ts", "id")
    )

    return _ordered_triggers(triggers), resolved_room, resolved_zone, trigger_world


def _ordered_triggers(triggers: list[Trigger]) -> list[Trigger]:
    triggers.sort(
        key=lambda trigger: (
            TRIGGER_SCOPE_PRIORITY.get(trigger.scope, 99),
            trigger.order,
            trigger.created_ts,
            trigger.id,
        )
    )
    return triggers


def _actor_scope_context(actor: Player | Mob) -> tuple[Room | None, Zone | None, World | None]:
    room = getattr(actor, "room", None)
    zone = room.zone if room else None
    world = _resolve_trigger_world(actor, room)
    return room, zone, world


def _targeted_command_fallback_triggers(
    actor: Player | Mob,
    *,
    target_pairs: list[tuple[ContentType, int]],
) -> list[Trigger]:
    if not target_pairs:
        return []

    room, _, trigger_world = _actor_scope_context(actor)
    if room is None and trigger_world is None:
        return []
    if not trigger_world:
        return []

    target_filter = Q()
    has_targets = False
    for target_type, target_id in target_pairs:
        target_filter |= Q(target_type=target_type, target_id=target_id)
        has_targets = True
    if not has_targets:
        return []

    triggers = list(
        Trigger.objects.filter(
            world_id=trigger_world.id,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            is_active=True,
        )
        .filter(target_filter)
        .order_by("order", "created_ts", "id")
    )
    return _ordered_triggers(triggers)


def _trigger_scope_key(
    trigger: Trigger,
    *,
    room: Room | None,
    zone: Zone | None,
    world: World | None,
) -> str:
    if trigger.scope == adv_consts.TRIGGER_SCOPE_ZONE and zone:
        return f"zone:{zone.id}"
    if trigger.scope == adv_consts.TRIGGER_SCOPE_WORLD and world:
        return f"world:{world.id}"
    if room:
        return f"room:{room.id}"
    return "unknown"


def _trigger_gate_cache_key(trigger: Trigger, scope_key: str) -> str:
    return f"spawns.trigger_gate.{trigger.id}.{scope_key}"


def _gate_delay(trigger: Trigger) -> int:
    try:
        return int(trigger.gate_delay or 0)
    except (TypeError, ValueError):
        return 0


def _is_gate_allowed(trigger: Trigger, scope_key: str) -> bool:
    gate_delay = _gate_delay(trigger)
    if gate_delay == 0:
        return True
    gate_key = _trigger_gate_cache_key(trigger, scope_key)
    return not bool(cache.get(gate_key))


def _consume_gate(trigger: Trigger, scope_key: str) -> None:
    gate_delay = _gate_delay(trigger)
    if gate_delay == 0:
        return
    gate_key = _trigger_gate_cache_key(trigger, scope_key)
    timeout = None if gate_delay < 0 else gate_delay
    cache.set(gate_key, 1, timeout=timeout)


def _dispatch_trigger_script_segment(
    *,
    actor: Player | Mob,
    segment: str,
    issuer_scope: str | None = None,
    connection_id: str | None = None,
) -> str | None:
    command_token = _first_token(segment)
    if not command_token:
        return None

    resolved = resolve_text_handler(command_token, include_builder=True)
    if not resolved:
        return f"Unknown command: {command_token}"

    resolved_command, handler = resolved
    actor_type = _actor_kind(actor)
    if actor_type not in getattr(handler, "supported_actor_types", ("player",)):
        return f"{actor_type.capitalize()}s cannot execute {resolved_command}."

    dispatched_messages: list[dict] = []
    payload: dict[str, object] = {
        "text": segment,
        "skip_triggers": True,
        "__trigger_source": True,
    }
    if issuer_scope:
        payload["issuer_scope"] = issuer_scope

    try:
        dispatch_command(
            command_type="text",
            actor_type=actor_type,
            actor_id=actor.id,
            payload=payload,
            connection_id=connection_id,
            published_messages=dispatched_messages,
        )
    except (ActorNotFoundError, HandlerNotFoundError, ValueError) as err:
        return str(err)

    return _first_dispatched_error(dispatched_messages)


def _collect_display_action_labels(
    *,
    actor: Player | Mob,
    triggers: list[Trigger],
    room: Room | None,
    zone: Zone | None,
    world: World | None,
) -> list[str]:
    labels: list[str] = []
    seen_labels: set[str] = set()

    for trigger in triggers:
        if not trigger.display_action_in_room:
            continue

        action_label = _first_action_label(trigger.actions)
        if not action_label or action_label in seen_labels:
            continue

        if trigger.conditions:
            evaluated = evaluate_conditions(actor, trigger.conditions)
            if not evaluated.get("result"):
                continue

        scope_key = _trigger_scope_key(
            trigger,
            room=room,
            zone=zone,
            world=world,
        )
        if not _is_gate_allowed(trigger, scope_key):
            continue

        seen_labels.add(action_label)
        labels.append(action_label)

    return labels


def get_room_action_labels_for_actor(actor: Player | Mob | None, room: Room | None) -> list[str]:
    if not actor or not room:
        return []

    triggers, resolved_room, resolved_zone, trigger_world = (
        _get_applicable_command_fallback_triggers(actor, room=room)
    )
    if not triggers:
        return []

    return _collect_display_action_labels(
        actor=actor,
        triggers=triggers,
        room=resolved_room,
        zone=resolved_zone,
        world=trigger_world,
    )


def get_item_action_labels_for_actor(actor: Player | Mob | None, item: Item | None) -> list[str]:
    if not actor or not item:
        return []

    cts = _scope_content_types()
    target_pairs: list[tuple[ContentType, int]] = [(cts[Item], item.id)]
    if item.template_id:
        target_pairs.append((cts[ItemTemplate], item.template_id))

    triggers = _targeted_command_fallback_triggers(
        actor,
        target_pairs=target_pairs,
    )
    if not triggers:
        return []

    room, zone, world = _actor_scope_context(actor)
    return _collect_display_action_labels(
        actor=actor,
        triggers=triggers,
        room=room,
        zone=zone,
        world=world,
    )


def get_char_action_labels_for_actor(actor: Player | Mob | None, char: Player | Mob | None) -> list[str]:
    if not actor or not char or not isinstance(char, Mob):
        return []

    cts = _scope_content_types()
    target_pairs: list[tuple[ContentType, int]] = [(cts[Mob], char.id)]
    if char.template_id:
        target_pairs.append((cts[MobTemplate], char.template_id))

    triggers = _targeted_command_fallback_triggers(
        actor,
        target_pairs=target_pairs,
    )
    if not triggers:
        return []

    room, zone, world = _actor_scope_context(actor)
    return _collect_display_action_labels(
        actor=actor,
        triggers=triggers,
        room=room,
        zone=zone,
        world=world,
    )


def execute_command_fallback_trigger(
    *,
    actor: Player | Mob,
    text: str,
    connection_id: str | None = None,
) -> TriggerExecutionResult:
    command_text = _normalized_text(text)
    if not command_text:
        return TriggerExecutionResult(handled=False)

    triggers, resolved_room, resolved_zone, trigger_world = (
        _get_applicable_command_fallback_triggers(actor)
    )
    if not triggers:
        return TriggerExecutionResult(handled=False)

    matched_any = False
    executed_any = False
    failure_text: str | None = None
    script_errors: list[str] = []

    for trigger in triggers:
        if not _actions_match(trigger.actions, command_text):
            continue
        matched_any = True

        if trigger.conditions:
            evaluated = evaluate_conditions(actor, trigger.conditions)
            if not evaluated.get("result"):
                if trigger.show_details_on_failure and not failure_text:
                    failure_text = (
                        trigger.failure_message
                        or evaluated.get("detail")
                        or DEFAULT_CONDITION_FAILURE_TEXT
                    )
                continue

        scope_key = _trigger_scope_key(
            trigger,
            room=resolved_room,
            zone=resolved_zone,
            world=trigger_world,
        )
        if not _is_gate_allowed(trigger, scope_key):
            return TriggerExecutionResult(handled=True, feedback=TRIGGER_GATED_TEXT)
        _consume_gate(trigger, scope_key)

        script_segments = _split_trigger_script(trigger.script)
        if not script_segments:
            executed_any = True
            continue

        for segment in script_segments:
            dispatched_error = _dispatch_trigger_script_segment(
                actor=actor,
                segment=segment,
                issuer_scope=trigger.scope,
                connection_id=connection_id,
            )
            if dispatched_error:
                script_errors.append(dispatched_error)
        executed_any = True

    if executed_any:
        if script_errors:
            error_text = "\n".join(f"Error: {error}" for error in script_errors)
            return TriggerExecutionResult(handled=True, feedback=error_text)
        return TriggerExecutionResult(handled=True)

    if failure_text:
        return TriggerExecutionResult(handled=True, feedback=failure_text)

    if matched_any:
        return TriggerExecutionResult(handled=True)

    return TriggerExecutionResult(handled=False)
