from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import BooleanField, Case, Q, Value, When

from builders.models import ItemDefinition, MobDefinition, Trigger
from config import constants as adv_consts
from config import game_settings as adv_config
from core.condition_dsl import (
    ConditionContext,
    evaluate_condition as evaluate_structured_condition,
    structured_condition_payload,
)
from core.conditions import evaluate_conditions
from core.trigger_steps import SCRIPT_COMMAND_DEPTH_KEY
from core.trigger_policy_cache import get_trigger_policy_cache_version
from core.utils import format_actor_msg
from spawns.events import COMMAND_RECEIPT_STATUS_COMPLETED
from spawns.handlers.registry import (
    ActorNotFoundError,
    HandlerNotFoundError,
    dispatch_command,
    resolve_text_handler,
)
from spawns.models import Item, Mob, Player
from spawns.request_segments import normalize_request_segment
from spawns.trigger_matcher import (
    evaluate_match_expression,
    exact_term_match,
    first_match_term,
    phrase_term_match,
)
from worlds.models import Room, World, Zone


TRIGGER_GATED_TEXT = "More time is needed."
DEFAULT_CONDITION_FAILURE_TEXT = "Action could not be completed."
DEFAULT_POLICY_FAILURE_TEXT = "You cannot go that way."
TRIGGER_HOOK_CACHE_TIMEOUT_SECONDS = 60
COMMAND_TRIGGER_CACHE_MAX_HOOKS = 512
COMMAND_TRIGGER_CACHE_MAX_BYTES = 512_000
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
    status: str | None = None
    code: str = ""


ACKNOWLEDGED_TRIGGER_REFUSAL_CODES = frozenset({
    "conditions_failed",
    "gated",
})


def _merge_trigger_rejection_code(
    current: str,
    candidate: str,
) -> str:
    """Keep a genuine failure ahead of normal authored refusals."""
    if not candidate:
        return current
    if not current:
        return candidate
    if (
        current in ACKNOWLEDGED_TRIGGER_REFUSAL_CODES
        and candidate not in ACKNOWLEDGED_TRIGGER_REFUSAL_CODES
    ):
        return candidate
    return current


def command_trigger_result_message(
    result: TriggerExecutionResult,
    *,
    request_id: uuid.UUID | str | None = None,
    request_segment: str = "r",
) -> dict | None:
    """Build one private response without turning status into room prose."""
    if result.status == "rejected" and request_id:
        data = {
            "request_id": str(request_id),
            "request_segment": normalize_request_segment(
                request_segment
            ),
            "status": "rejected",
            "code": "trigger_rejected",
            "reason_code": result.code or "trigger_rejected",
            "receipt_status": COMMAND_RECEIPT_STATUS_COMPLETED,
        }
        message = {
            "type": "cmd.trigger.rejected",
            "data": data,
        }
        if result.feedback:
            data["message"] = result.feedback
            message["text"] = result.feedback
        return message
    if result.feedback:
        return {
            "type": "cmd.text.trigger",
            "text": result.feedback,
            "data": {"text": result.feedback},
        }
    return None


@dataclass(frozen=True)
class PolicyEvaluationResult:
    allowed: bool
    feedback: str | None = None
    trigger_id: int | None = None
    code: str = "policy_blocked"


def _normalized_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def _first_match_label(match_text: str | None) -> str | None:
    return first_match_term(match_text)


def _command_match_expression_matches(match_text: str | None, command_text: str) -> bool:
    if not command_text:
        return False
    return evaluate_match_expression(
        match_text,
        term_matcher=lambda term: phrase_term_match(command_text, term),
        empty_expression=False,
    )


def _split_trigger_script_line(line: str | None) -> list[str]:
    segments: list[str] = []
    for chunk in str(line or "").split("&&"):
        segment = chunk.strip()
        if segment:
            segments.append(segment)
    return segments


def _split_trigger_script_lines(script: str | None) -> list[list[str]]:
    script_lines: list[list[str]] = []
    for line in str(script or "").splitlines():
        line_segments = _split_trigger_script_line(line)
        if line_segments:
            script_lines.append(line_segments)
    return script_lines


def _trigger_has_steps(trigger: Trigger | SimpleNamespace) -> bool:
    marker = getattr(trigger, "cached_has_steps", None)
    if marker is not None:
        return bool(marker)
    return bool(getattr(trigger, "steps", None))


def _with_cached_step_marker(queryset):
    """Avoid loading large typed-step bodies on paths that refetch at start."""
    return queryset.annotate(
        cached_has_steps=Case(
            When(steps=[], then=Value(False)),
            default=Value(True),
            output_field=BooleanField(),
        )
    ).defer("steps")


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
            ItemDefinition,
            Mob,
            MobDefinition,
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

    cache_key = None
    if not connection.in_atomic_block:
        version = get_trigger_policy_cache_version(trigger_world.id)
        cache_key = (
            f"spawns.command_trigger_hooks.{version}.{trigger_world.id}."
            f"{resolved_room.id}.{getattr(resolved_zone, 'id', 0) or 0}"
        )
        try:
            cached_hooks = cache.get(cache_key)
        except Exception:
            cached_hooks = None
        if isinstance(cached_hooks, list):
            try:
                return (
                    [SimpleNamespace(**hook) for hook in cached_hooks],
                    resolved_room,
                    resolved_zone,
                    trigger_world,
                )
            except (TypeError, ValueError):
                cached_hooks = None

    triggers = list(
        _with_cached_step_marker(Trigger.objects.filter(
            world_id=trigger_world.id,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            is_active=True,
        ))
        .filter(scope_filter)
        .order_by("order", "created_ts", "id")
    )

    triggers = _ordered_triggers(triggers)
    if cache_key is not None and len(triggers) <= COMMAND_TRIGGER_CACHE_MAX_HOOKS:
        serialized_hooks = [_serialize_cached_hook(trigger) for trigger in triggers]
        encoded_size = len(
            json.dumps(serialized_hooks, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size <= COMMAND_TRIGGER_CACHE_MAX_BYTES:
            try:
                cache.set(
                    cache_key,
                    serialized_hooks,
                    timeout=TRIGGER_HOOK_CACHE_TIMEOUT_SECONDS,
                )
            except Exception:
                pass

    return triggers, resolved_room, resolved_zone, trigger_world


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


def _coerce_room(room: Room | int | None) -> Room | None:
    if isinstance(room, Room):
        return room
    if isinstance(room, int):
        return Room.objects.select_related("zone", "world").filter(pk=room).first()
    return None


def _event_match_expression_matches(
    *,
    trigger: Trigger,
    event: str,
    match_text: str | None,
) -> bool:
    trigger_match = _normalized_text(trigger.match)
    normalized_event = _normalized_text(event)
    if not trigger_match:
        return normalized_event != adv_consts.MOB_REACTION_EVENT_SOCIAL

    if normalized_event == adv_consts.MOB_REACTION_EVENT_SAYING:
        return evaluate_match_expression(
            trigger_match,
            term_matcher=lambda term: phrase_term_match(match_text, term),
            empty_expression=True,
        )

    if normalized_event in (
        adv_consts.MOB_REACTION_EVENT_RECEIVE,
        adv_consts.MOB_REACTION_EVENT_PERIODIC,
        adv_consts.MOB_REACTION_EVENT_SOCIAL,
    ):
        return evaluate_match_expression(
            trigger_match,
            term_matcher=lambda term: exact_term_match(match_text, term),
            empty_expression=True,
        )

    return True


def _hook_match_expression_matches(match_text: str | None, value: str | None) -> bool:
    trigger_match = _normalized_text(match_text)
    if not trigger_match:
        return True
    return evaluate_match_expression(
        trigger_match,
        term_matcher=lambda term: exact_term_match(value, term),
        empty_expression=True,
    )


def _movement_event_data(
    *,
    event: str,
    direction: str,
    origin_room: Room,
    destination_room: Room,
    target_room: Room,
) -> dict:
    return {
        "event": event,
        "direction": direction,
        "target_type": "room",
        "target_id": target_room.id,
        "target": {
            "type": "room",
            "id": target_room.id,
            "key": target_room.key,
            "name": target_room.name or "",
        },
        "origin_room": {
            "id": origin_room.id,
            "key": origin_room.key,
            "name": origin_room.name or "",
        },
        "destination_room": {
            "id": destination_room.id,
            "key": destination_room.key,
            "name": destination_room.name or "",
        },
    }


def _room_hook_cache_key(
    *,
    world_id: int,
    room_id: int,
    kind: str,
    event: str,
) -> str:
    version = get_trigger_policy_cache_version(world_id)
    return f"spawns.trigger_hooks.{version}.{world_id}.{room_id}.{kind}.{event}"


def _serialize_cached_hook(trigger: Trigger) -> dict:
    return {
        "id": trigger.id,
        "world_id": trigger.world_id,
        "key": trigger.key,
        "name": trigger.name or "",
        "scope": trigger.scope,
        "kind": trigger.kind,
        "event": trigger.event or "",
        "match": trigger.match or "",
        "script": trigger.script or "",
        # Cached paths only need to choose typed execution. The durable starter
        # reloads the authoritative Trigger before validating/snapshotting it,
        # so caching the potentially 256-KiB body would amplify every room
        # cache miss for no runtime benefit.
        "steps": _trigger_has_steps(trigger),
        "on_step_error": trigger.on_step_error or "cancel",
        "conditions": trigger.conditions or "",
        "show_details_on_failure": bool(trigger.show_details_on_failure),
        "failure_message": trigger.failure_message or "",
        "display_action_in_room": bool(trigger.display_action_in_room),
        "gate_delay": int(trigger.gate_delay or 0),
        "order": int(trigger.order or 0),
    }


def _cached_room_hooks(
    *,
    world_id: int | None,
    room_id: int | None,
    kind: str,
    event: str,
) -> list[dict]:
    if not world_id or not room_id or not event:
        return []

    normalized_kind = _normalized_text(kind)
    normalized_event = _normalized_text(event)
    cache_key = _room_hook_cache_key(
        world_id=int(world_id),
        room_id=int(room_id),
        kind=normalized_kind,
        event=normalized_event,
    )
    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    room_ct = _scope_content_types()[Room]
    hooks = [
        _serialize_cached_hook(trigger)
        for trigger in _with_cached_step_marker(Trigger.objects.filter(
            world_id=world_id,
            kind=normalized_kind,
            event=normalized_event,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=room_ct,
            target_id=room_id,
            is_active=True,
        )).order_by("order", "created_ts", "id")
    ]
    if len(hooks) <= COMMAND_TRIGGER_CACHE_MAX_HOOKS:
        encoded_size = len(
            json.dumps(hooks, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size <= COMMAND_TRIGGER_CACHE_MAX_BYTES:
            try:
                cache.set(
                    cache_key,
                    hooks,
                    timeout=TRIGGER_HOOK_CACHE_TIMEOUT_SECONDS,
                )
            except Exception:
                pass
    return hooks


def _cached_trigger_gate_cache_key(hook: dict, scope_key: str) -> str:
    return f"spawns.trigger_gate.{hook.get('id')}.{scope_key}"


def _cached_gate_delay(hook: dict) -> int:
    try:
        return int(hook.get("gate_delay") or 0)
    except (TypeError, ValueError):
        return 0


def _is_cached_gate_allowed(hook: dict, scope_key: str) -> bool:
    gate_delay = _cached_gate_delay(hook)
    if gate_delay == 0:
        return True
    gate_key = _cached_trigger_gate_cache_key(hook, scope_key)
    return not bool(cache.get(gate_key))


def _consume_cached_gate(hook: dict, scope_key: str) -> None:
    gate_delay = _cached_gate_delay(hook)
    if gate_delay == 0:
        return
    gate_key = _cached_trigger_gate_cache_key(hook, scope_key)
    timeout = None if gate_delay < 0 else gate_delay
    cache.set(gate_key, 1, timeout=timeout)


def _load_movement_rooms(origin_room_id: int, destination_room_id: int) -> tuple[Room | None, Room | None]:
    rooms = {
        room.id: room
        for room in Room.objects.select_related("zone", "world").filter(
            pk__in=[origin_room_id, destination_room_id],
        )
    }
    return rooms.get(origin_room_id), rooms.get(destination_room_id)


def _condition_uses_operator(value, operator: str) -> bool:
    if isinstance(value, dict):
        if operator in value:
            return True
        return any(
            _condition_uses_operator(child, operator)
            for child in value.values()
        )
    if isinstance(value, list):
        return any(_condition_uses_operator(child, operator) for child in value)
    return False


def _evaluate_movement_policy_conditions(
    *,
    actor: Player,
    conditions,
    room: Room,
    event_data: dict,
) -> dict:
    structured = structured_condition_payload(conditions)
    if structured is not None and _condition_uses_operator(structured, "mob_present"):
        return {
            "result": evaluate_structured_condition(
                structured,
                context=ConditionContext(
                    actor=actor,
                    player=actor,
                    room=room,
                    zone=room.zone,
                    world=room.world,
                    event_data=event_data,
                ),
            ),
            "detail": "",
        }
    return evaluate_conditions(
        actor,
        conditions,
        room=room,
        zone=room.zone,
        world=room.world,
        event_data=event_data,
    )


def evaluate_movement_policies(
    *,
    actor: Player,
    event: str,
    direction: str,
    origin_room_id: int,
    destination_room_id: int,
    world_id: int | None = None,
) -> PolicyEvaluationResult:
    normalized_event = _normalized_text(event)
    if normalized_event not in adv_consts.TRIGGER_POLICY_EVENTS:
        return PolicyEvaluationResult(allowed=True)

    target_room_id = (
        origin_room_id
        if normalized_event == adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT
        else destination_room_id
    )
    hooks = _cached_room_hooks(
        world_id=world_id,
        room_id=target_room_id,
        kind=adv_consts.TRIGGER_KIND_POLICY,
        event=normalized_event,
    )
    if not hooks:
        return PolicyEvaluationResult(allowed=True)

    applicable_hooks = [
        hook
        for hook in hooks
        if _hook_match_expression_matches(hook.get("match"), direction)
    ]
    if not applicable_hooks:
        return PolicyEvaluationResult(allowed=True)

    origin_room, destination_room = _load_movement_rooms(origin_room_id, destination_room_id)
    if not origin_room or not destination_room:
        return PolicyEvaluationResult(
            allowed=False,
            feedback=DEFAULT_POLICY_FAILURE_TEXT,
        )

    target_room = origin_room if target_room_id == origin_room.id else destination_room
    event_data = _movement_event_data(
        event=normalized_event,
        direction=direction,
        origin_room=origin_room,
        destination_room=destination_room,
        target_room=target_room,
    )

    for hook in applicable_hooks:
        conditions = hook.get("conditions")
        if conditions:
            evaluated = _evaluate_movement_policy_conditions(
                actor=actor,
                conditions=conditions,
                room=target_room,
                event_data=event_data,
            )
            if not evaluated.get("result"):
                return PolicyEvaluationResult(
                    allowed=False,
                    feedback=(
                        hook.get("failure_message")
                        or evaluated.get("detail")
                        or DEFAULT_POLICY_FAILURE_TEXT
                    ),
                    trigger_id=hook.get("id"),
                )

    return PolicyEvaluationResult(allowed=True)


def execute_room_event_triggers(
    *,
    event: str,
    actor: Player | Mob | None,
    room: Room | int | None,
    origin_room_id: int | None = None,
    destination_room_id: int | None = None,
    direction: str | None = None,
    connection_id: str | None = None,
) -> None:
    normalized_event = _normalized_text(event)
    if normalized_event not in adv_consts.TRIGGER_ROOM_EVENT_EVENTS:
        return

    resolved_room = _coerce_room(room) or getattr(actor, "room", None)
    if not resolved_room:
        return

    trigger_world = _resolve_trigger_world(actor, resolved_room) if actor else _resolve_room_world(resolved_room)
    if not trigger_world:
        return

    hooks = _cached_room_hooks(
        world_id=trigger_world.id,
        room_id=resolved_room.id,
        kind=adv_consts.TRIGGER_KIND_EVENT,
        event=normalized_event,
    )
    if not hooks:
        return

    origin_room, destination_room = _load_movement_rooms(
        origin_room_id or resolved_room.id,
        destination_room_id or resolved_room.id,
    )
    origin_room = origin_room or resolved_room
    destination_room = destination_room or resolved_room
    event_data = _movement_event_data(
        event=normalized_event,
        direction=str(direction or ""),
        origin_room=origin_room,
        destination_room=destination_room,
        target_room=resolved_room,
    )
    runtime_world_id = getattr(actor, "world_id", None)
    scope_key = (
        f"runtime:{runtime_world_id}:room:{resolved_room.id}"
        if runtime_world_id
        else f"room:{resolved_room.id}"
    )

    for hook in hooks:
        if not _hook_match_expression_matches(hook.get("match"), direction):
            continue

        conditions = hook.get("conditions")
        if hook.get("steps") and actor:
            from spawns.trigger_steps import start_trigger_steps

            start_trigger_steps(
                trigger=SimpleNamespace(**hook),
                actor=actor,
                room=resolved_room,
                event_data=event_data,
                gate_scope_key=scope_key,
            )
            continue

        if conditions and actor:
            evaluated = evaluate_conditions(
                actor,
                conditions,
                room=resolved_room,
                zone=resolved_room.zone,
                world=trigger_world,
                event_data=event_data,
            )
            if not evaluated.get("result"):
                continue

        if not _is_cached_gate_allowed(hook, scope_key):
            continue

        _consume_cached_gate(hook, scope_key)

        script_lines = _split_trigger_script_lines(hook.get("script"))
        if not script_lines or not actor:
            continue

        first_line_segments = script_lines[0]
        _dispatch_trigger_script_segments(
            actor=actor,
            segments=first_line_segments,
            issuer_scope=hook.get("scope"),
            connection_id=connection_id,
        )

        for line_index, line_segments in enumerate(script_lines[1:], start=1):
            _schedule_trigger_script_line_segments(
                actor=actor,
                line_segments=line_segments,
                line_index=line_index,
                issuer_scope=hook.get("scope"),
                connection_id=connection_id,
            )


def _resolve_room_world(room: Room | None) -> World | None:
    if not room:
        return None

    room_world = getattr(room, "world", None)
    if not room_world:
        return None

    context_world = getattr(room_world, "context", None)
    if context_world:
        return getattr(context_world, "instance_of", None) or context_world

    return getattr(room_world, "instance_of", None) or room_world


def execute_mob_event_triggers(
    *,
    event: str,
    actor: Player | Mob | None = None,
    room: Room | int | None = None,
    match_text: str | None = None,
    connection_id: str | None = None,
    isolate_runtime_world: bool = False,
    target_mob_id: int | None = None,
    source_event_data: dict | None = None,
    capture_output: bool = False,
    gate_claim_collector: list[tuple[str, str]] | None = None,
) -> None:
    normalized_event = _normalized_text(event)
    if normalized_event not in adv_consts.MOB_REACTION_EVENTS:
        return

    resolved_room = _coerce_room(room) or getattr(actor, "room", None)
    if not resolved_room:
        return

    trigger_world = _resolve_trigger_world(actor, resolved_room) if actor else _resolve_room_world(resolved_room)
    if not trigger_world:
        return

    mobs_qs = Mob.objects.filter(room_id=resolved_room.id)
    if target_mob_id is not None:
        mobs_qs = mobs_qs.filter(pk=target_mob_id)
    actor_world_id = getattr(actor, "world_id", None)
    # Authored rooms are shared by runtime instances, so a room id alone is
    # never enough to select live mobs for an actor-caused event. Keep the
    # explicit flag for compatibility, but always isolate when an actor
    # provides the authoritative runtime world. An actorless caller that asks
    # for isolation has no trustworthy runtime-world identity, so fail closed.
    if actor_world_id:
        mobs_qs = mobs_qs.filter(world_id=actor_world_id)
    elif isolate_runtime_world:
        return
    if isinstance(actor, Mob):
        mobs_qs = mobs_qs.exclude(pk=actor.id)
    mobs = list(
        mobs_qs.select_related("definition").order_by("id")
    )
    if not mobs:
        return

    cts = _scope_content_types()
    mob_ct = cts[Mob]
    mob_definition_ct = cts[MobDefinition]

    target_filter = Q()
    for mob in mobs:
        target_filter |= Q(target_type=mob_ct, target_id=mob.id)
        if mob.definition_id:
            target_filter |= Q(target_type=mob_definition_ct, target_id=mob.definition_id)

    triggers = list(
        _with_cached_step_marker(Trigger.objects.filter(
            world_id=trigger_world.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=normalized_event,
            is_active=True,
        ))
        .filter(target_filter)
        .order_by("order", "created_ts", "id")
    )
    if not triggers:
        return

    trigger_event_data = {
        "event": normalized_event,
        "match": match_text or "",
    }
    command_depth = 0
    if isinstance(source_event_data, dict):
        try:
            command_depth = max(
                0,
                int(source_event_data.get(SCRIPT_COMMAND_DEPTH_KEY) or 0),
            )
        except (TypeError, ValueError):
            command_depth = 0
        if command_depth:
            trigger_event_data[SCRIPT_COMMAND_DEPTH_KEY] = command_depth
    from spawns.script_commands import MAX_SCRIPT_COMMAND_DEPTH

    trigger_by_target: dict[tuple[int, int], list[Trigger]] = {}
    for trigger in _ordered_triggers(triggers):
        if not trigger.target_type_id or not trigger.target_id:
            continue
        target_key = (trigger.target_type_id, trigger.target_id)
        trigger_by_target.setdefault(target_key, []).append(trigger)

    for mob in mobs:
        mob_trigger_list: list[Trigger] = []
        mob_trigger_list.extend(trigger_by_target.get((mob_ct.id, mob.id), []))
        if mob.definition_id:
            mob_trigger_list.extend(
                trigger_by_target.get((mob_definition_ct.id, mob.definition_id), [])
            )
        if not mob_trigger_list:
            continue

        evaluator = actor or mob
        for trigger in mob_trigger_list:
            if not _event_match_expression_matches(
                trigger=trigger,
                event=normalized_event,
                match_text=match_text,
            ):
                continue

            scope_key = f"runtime:{mob.world_id}:mob:{mob.id}"
            if _trigger_has_steps(trigger):
                from spawns.trigger_steps import start_trigger_steps

                start_trigger_steps(
                    trigger=trigger,
                    actor=evaluator,
                    room=resolved_room,
                    event_data=trigger_event_data,
                    gate_scope_key=scope_key,
                    gate_claim_collector=gate_claim_collector,
                )
                continue

            if command_depth >= MAX_SCRIPT_COMMAND_DEPTH:
                continue

            if trigger.conditions:
                evaluated = evaluate_conditions(
                    evaluator,
                    trigger.conditions,
                    room=resolved_room,
                    zone=resolved_room.zone,
                    world=getattr(evaluator, "world", None),
                )
                if not evaluated.get("result"):
                    continue

            gate_allowed, gate_claim = _consume_gate(trigger, scope_key)
            if not gate_allowed:
                continue
            if (
                gate_claim is not None
                and gate_claim_collector is not None
            ):
                gate_claim_collector.append(gate_claim)

            script_lines = _split_trigger_script_lines(trigger.script)
            if not script_lines:
                continue

            first_line_segments = script_lines[0]
            _dispatch_trigger_script_segments(
                actor=mob,
                render_actor=actor or mob,
                segments=first_line_segments,
                issuer_scope=trigger.scope,
                connection_id=connection_id,
                script_command_depth=command_depth + 1,
                capture_only=capture_output,
            )

            for line_index, line_segments in enumerate(script_lines[1:], start=1):
                _schedule_trigger_script_line_segments(
                    actor=mob,
                    render_actor=actor or mob,
                    line_segments=line_segments,
                    line_index=line_index,
                    issuer_scope=trigger.scope,
                    connection_id=connection_id,
                    script_command_depth=command_depth + 1,
                    capture_only=capture_output,
                    defer_until_commit=True,
                )


def _trigger_scope_key(
    trigger: Trigger,
    *,
    room: Room | None,
    zone: Zone | None,
    world: World | None,
    runtime_world_id: int | None = None,
) -> str:
    runtime_prefix = f"runtime:{runtime_world_id}:" if runtime_world_id else ""
    if trigger.scope == adv_consts.TRIGGER_SCOPE_ZONE and zone:
        return f"{runtime_prefix}zone:{zone.id}"
    if trigger.scope == adv_consts.TRIGGER_SCOPE_WORLD and world:
        return f"{runtime_prefix}world:{world.id}"
    if room:
        return f"{runtime_prefix}room:{room.id}"
    return f"{runtime_prefix}unknown"


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


def _consume_gate(
    trigger: Trigger,
    scope_key: str,
) -> tuple[bool, tuple[str, str] | None]:
    """Atomically claim a legacy-script gate.

    The earlier read-only gate check remains useful when rendering available
    action labels, but execution must use ``cache.add`` so two simultaneous
    arrivals cannot both pass a get-then-set race.
    """
    gate_delay = _gate_delay(trigger)
    if gate_delay == 0:
        return True, None
    gate_key = _trigger_gate_cache_key(trigger, scope_key)
    timeout = None if gate_delay < 0 else gate_delay
    token = uuid.uuid4().hex
    if not cache.add(gate_key, token, timeout=timeout):
        return False, None
    return True, (gate_key, token)


def _dispatch_trigger_script_segment(
    *,
    actor: Player | Mob,
    render_actor: Player | Mob | None = None,
    segment: str,
    issuer_scope: str | None = None,
    connection_id: str | None = None,
    script_command_depth: int = 0,
    capture_only: bool = False,
) -> str | None:
    rendered_segment = _render_trigger_script_segment(
        actor=render_actor or actor,
        segment=segment,
    )
    command_token = _first_token(rendered_segment)
    if not command_token:
        return None

    actor_type = _actor_kind(actor)
    resolved = resolve_text_handler(command_token, include_builder=True)
    resolved_social = None
    if not resolved:
        from spawns.socials import resolve_social_for_command

        resolved_social = resolve_social_for_command(actor.world, command_token)
        if resolved_social is None:
            return f"Unknown command: {command_token}"
    else:
        resolved_command, handler = resolved
        if actor_type not in getattr(handler, "supported_actor_types", ("player",)):
            return f"{actor_type.capitalize()}s cannot execute {resolved_command}."

    dispatched_messages: list[dict] = []
    command_type = "text"
    payload: dict[str, object]
    if resolved_social is not None:
        tokens = rendered_segment.split()
        command_type = "social"
        payload = {
            "social": resolved_social["command"],
            "target": tokens[1] if len(tokens) > 1 else None,
        }
    else:
        payload = {
            "text": rendered_segment,
            "skip_triggers": True,
        }
    if issuer_scope:
        payload["issuer_scope"] = issuer_scope

    try:
        from spawns.events import inherit_script_command_depth

        with inherit_script_command_depth(script_command_depth):
            dispatch_command(
                command_type=command_type,
                actor_type=actor_type,
                actor_id=actor.id,
                payload=payload,
                connection_id=connection_id,
                script_source=True,
                published_messages=dispatched_messages,
                capture_only=capture_only,
            )
    except (ActorNotFoundError, HandlerNotFoundError, ValueError) as err:
        return str(err)

    return _first_dispatched_error(dispatched_messages)


def _render_trigger_script_segment(*, actor: Player | Mob, segment: str) -> str:
    return str(format_actor_msg(segment, actor) or segment).strip()


def _dispatch_trigger_script_segments(
    *,
    actor: Player | Mob,
    render_actor: Player | Mob | None = None,
    segments: list[str],
    issuer_scope: str | None = None,
    connection_id: str | None = None,
    script_command_depth: int = 0,
    capture_only: bool = False,
) -> list[str]:
    errors: list[str] = []
    for segment in segments:
        dispatched_error = _dispatch_trigger_script_segment(
            actor=actor,
            render_actor=render_actor,
            segment=segment,
            issuer_scope=issuer_scope,
            connection_id=connection_id,
            script_command_depth=script_command_depth,
            capture_only=capture_only,
        )
        if dispatched_error:
            errors.append(dispatched_error)
    return errors


def _trigger_script_multiline_delay_seconds() -> float:
    raw_delay = getattr(adv_config, "GAME_HEARTBEAT_INTERVAL_SECONDS", 2)
    try:
        delay = float(raw_delay)
    except (TypeError, ValueError):
        return 2.0
    return max(delay, 0.0)


def _schedule_trigger_script_line_segments(
    *,
    actor: Player | Mob,
    render_actor: Player | Mob | None = None,
    line_segments: list[str],
    line_index: int,
    issuer_scope: str | None = None,
    connection_id: str | None = None,
    script_command_depth: int = 0,
    capture_only: bool = False,
    defer_until_commit: bool = False,
) -> list[str]:
    if not line_segments:
        return []

    delay_seconds = _trigger_script_multiline_delay_seconds() * max(line_index, 0)
    if delay_seconds <= 0:
        return _dispatch_trigger_script_segments(
            actor=actor,
            render_actor=render_actor,
            segments=line_segments,
            issuer_scope=issuer_scope,
            connection_id=connection_id,
            script_command_depth=script_command_depth,
            capture_only=capture_only,
        )

    from spawns import tasks as spawn_tasks

    actor_type = _actor_kind(actor)
    rendered_line_segments = [
        rendered_segment
        for segment in line_segments
        if (
            rendered_segment := _render_trigger_script_segment(
                actor=render_actor or actor,
                segment=segment,
            )
        )
    ]
    if not rendered_line_segments:
        return []

    task_kwargs = {
        "actor_type": actor_type,
        "actor_id": actor.id,
        "segments": rendered_line_segments,
        "issuer_scope": issuer_scope,
        "connection_id": connection_id,
        "expected_world_id": getattr(actor, "world_id", None),
        "expected_room_id": getattr(actor, "room_id", None),
        "script_command_depth": script_command_depth,
    }

    def enqueue_delayed_line() -> None:
        spawn_tasks.execute_trigger_script_segments.apply_async(
            kwargs=task_kwargs,
            countdown=delay_seconds,
        )

    if defer_until_commit and connection.in_atomic_block:
        def enqueue_after_commit() -> None:
            try:
                enqueue_delayed_line()
            except Exception:
                # Use the same pre-rendered payload and expected location
                # checks as the queued task. A broker failure must not make a
                # delayed line follow an actor that moved after it was
                # scheduled or render templates a second time.
                spawn_tasks.execute_trigger_script_segments(**task_kwargs)

        transaction.on_commit(enqueue_after_commit, robust=True)
        return []

    try:
        enqueue_delayed_line()
    except Exception:
        return _dispatch_trigger_script_segments(
            actor=actor,
            render_actor=render_actor,
            segments=line_segments,
            issuer_scope=issuer_scope,
            connection_id=connection_id,
            script_command_depth=script_command_depth,
            capture_only=capture_only,
        )

    return []


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

        action_label = _first_match_label(trigger.match)
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
            runtime_world_id=getattr(actor, "world_id", None),
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
    labels = _collect_display_action_labels(
        actor=actor,
        triggers=triggers,
        room=resolved_room,
        zone=resolved_zone,
        world=trigger_world,
    ) if triggers else []

    # Built-in room capabilities use already-loaded FK ids so serializing a
    # busy room does not add a database query per look.
    if isinstance(actor, Player) and room.crafting_profile_id:
        normalized_labels = {label.casefold() for label in labels}
        for action_label in ("craft", "salvage"):
            if action_label not in normalized_labels:
                labels.append(action_label)

    # Room.transfer_to is the authored base-room -> instance-room link.
    if isinstance(actor, Player) and room.transfer_to_id and "enter" not in labels:
        labels.append("enter")

    return labels


def get_item_action_labels_for_actor(actor: Player | Mob | None, item: Item | None) -> list[str]:
    if not actor or not item:
        return []

    cts = _scope_content_types()
    target_pairs: list[tuple[ContentType, int]] = [(cts[Item], item.id)]
    if item.definition_id:
        target_pairs.append((cts[ItemDefinition], item.definition_id))

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
    if char.definition_id:
        target_pairs.append((cts[MobDefinition], char.definition_id))

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
    request_id: uuid.UUID | str | None = None,
    request_segment: str = "r",
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
    succeeded_any = False
    failure_text: str | None = None
    script_errors: list[str] = []
    acceptance_emitted = False
    rejected_any = False
    rejection_code = ""

    for trigger in triggers:
        if not _command_match_expression_matches(trigger.match, command_text):
            continue
        matched_any = True

        scope_key = _trigger_scope_key(
            trigger,
            room=resolved_room,
            zone=resolved_zone,
            world=trigger_world,
            runtime_world_id=getattr(actor, "world_id", None),
        )

        if _trigger_has_steps(trigger):
            from spawns.trigger_steps import start_trigger_steps

            started = start_trigger_steps(
                trigger=trigger,
                actor=actor,
                room=resolved_room,
                event_data={"command": command_text},
                gate_scope_key=scope_key,
                request_id=request_id,
                request_segment=request_segment,
                request_connection_id=connection_id,
                emit_acceptance=not acceptance_emitted,
            )
            if started.started:
                executed_any = True
                succeeded_any = True
                acceptance_emitted = True
                # Claim the client receipt immediately. The durable accepted
                # event may publish only after an enclosing transaction
                # commits, by which point command dispatch has returned.
                from spawns.events import defer_actor_command_result

                defer_actor_command_result(actor.key)
            elif started.code == "gated":
                if succeeded_any:
                    script_errors.append(TRIGGER_GATED_TEXT)
                    rejected_any = True
                    rejection_code = _merge_trigger_rejection_code(
                        rejection_code,
                        "gated",
                    )
                    continue
                return TriggerExecutionResult(
                    handled=True,
                    feedback=TRIGGER_GATED_TEXT,
                    status="rejected",
                    code="gated",
                )
            elif started.code == "conditions_failed":
                rejected_any = True
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    started.code,
                )
                if trigger.show_details_on_failure and not failure_text:
                    failure_text = started.feedback
            elif started.code in {"trigger_missing", "no_steps"}:
                rejected_any = True
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    started.code,
                )
            elif started.feedback:
                script_errors.append(started.feedback)
                executed_any = True
                rejected_any = True
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    started.code or "trigger_failed",
                )
            continue

        if trigger.conditions:
            evaluated = evaluate_conditions(actor, trigger.conditions)
            if not evaluated.get("result"):
                rejected_any = True
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    "conditions_failed",
                )
                if trigger.show_details_on_failure and not failure_text:
                    failure_text = (
                        trigger.failure_message
                        or evaluated.get("detail")
                        or DEFAULT_CONDITION_FAILURE_TEXT
                    )
                continue

        gate_allowed, _gate_claim = _consume_gate(trigger, scope_key)
        if not gate_allowed:
            if succeeded_any:
                script_errors.append(TRIGGER_GATED_TEXT)
                rejected_any = True
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    "gated",
                )
                continue
            return TriggerExecutionResult(
                handled=True,
                feedback=TRIGGER_GATED_TEXT,
                status="rejected",
                code="gated",
            )

        script_lines = _split_trigger_script_lines(trigger.script)
        if not script_lines:
            executed_any = True
            succeeded_any = True
            continue

        prior_error_count = len(script_errors)
        first_line_segments = script_lines[0]
        attempted_segment_count = len(first_line_segments)
        for dispatched_error in _dispatch_trigger_script_segments(
            actor=actor,
            segments=first_line_segments,
            issuer_scope=trigger.scope,
            connection_id=connection_id,
        ):
            script_errors.append(dispatched_error)
            rejection_code = _merge_trigger_rejection_code(
                rejection_code,
                "trigger_failed",
            )

        for line_index, line_segments in enumerate(script_lines[1:], start=1):
            attempted_segment_count += len(line_segments)
            for dispatched_error in _schedule_trigger_script_line_segments(
                actor=actor,
                line_segments=line_segments,
                line_index=line_index,
                issuer_scope=trigger.scope,
                connection_id=connection_id,
            ):
                script_errors.append(dispatched_error)
                rejection_code = _merge_trigger_rejection_code(
                    rejection_code,
                    "trigger_failed",
                )
        executed_any = True
        new_error_count = len(script_errors) - prior_error_count
        if new_error_count < attempted_segment_count:
            succeeded_any = True

    if executed_any:
        if script_errors:
            error_text = "\n".join(f"Error: {error}" for error in script_errors)
            return TriggerExecutionResult(
                handled=True,
                feedback=error_text,
                status=(
                    None
                    if succeeded_any
                    else "rejected"
                ),
                code=rejection_code or "trigger_failed",
            )
        return TriggerExecutionResult(handled=True)

    if failure_text:
        return TriggerExecutionResult(
            handled=True,
            feedback=failure_text,
            status="rejected",
            code=rejection_code or "conditions_failed",
        )

    if matched_any:
        return TriggerExecutionResult(
            handled=True,
            status="rejected" if rejected_any else None,
            code=rejection_code,
        )

    return TriggerExecutionResult(handled=False)
