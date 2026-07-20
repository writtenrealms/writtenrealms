from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import logging
from typing import Any
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import (
    IntegrityError,
    InterfaceError,
    OperationalError,
    connection,
    transaction,
)
from django.db.models import Q
from django.utils import timezone

from builders.models import ItemDefinition, Trigger
from core.conditions import evaluate_conditions
from core.trigger_steps import (
    TRIGGER_STEP_ACTION_CONSUME_ITEM,
    TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM,
    TRIGGER_STEP_ACTION_ECHO,
    TRIGGER_STEP_ACTION_GRANT_ITEM,
    TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM,
    TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM,
    TriggerStepSpecError,
    normalize_trigger_step_error_policy,
    normalize_trigger_steps,
)
from spawns.events import (
    GameEvent,
    enqueue_game_events,
    flush_game_event_outbox,
    publish_events,
)
from spawns.models import Item, Mob, Player, ScheduledTriggerRun
from worlds.models import Room, World


DEFAULT_DUE_RUN_LIMIT = 100
MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR = 16
TRIGGER_GATED_TEXT = "More time is needed."


logger = logging.getLogger(__name__)


class TriggerStepExecutionError(ValueError):
    def __init__(self, message: str, *, code: str = "step_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TriggerStepStartResult:
    started: bool
    run_id: int | None = None
    feedback: str | None = None
    code: str = ""


@dataclass
class TriggerItemChanges:
    room_items_added: dict[int, Item] = field(default_factory=dict)
    room_items_removed: list[dict[str, str]] = field(default_factory=list)
    actor_inventory_added: dict[int, Item] = field(default_factory=dict)
    actor_inventory_removed: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.room_items_added
            or self.room_items_removed
            or self.actor_inventory_added
            or self.actor_inventory_removed
        )


def _flush_queued_events() -> None:
    flush_game_event_outbox(publisher=publish_events)


def _actor_kind(actor: Player | Mob) -> str:
    return "player" if isinstance(actor, Player) else "mob"


def _actor_model(actor_type: str):
    if actor_type == "player":
        return Player
    if actor_type == "mob":
        return Mob
    return None


def _definition_world_id(runtime_world: World) -> int:
    """Resolve the base world that owns shared item definitions."""
    context = getattr(runtime_world, "context", None)
    if context is not None:
        return context.instance_of_id or context.id
    return runtime_world.instance_of_id or runtime_world.id


def _trigger_owner_world_id(room: Room) -> int | None:
    """Resolve the template world that owns local room/zone/world triggers."""
    return room.world_id


def _expected_actor_room_id(
    *,
    trigger_room_id: int,
    event_data: dict[str, Any] | None,
) -> int:
    if isinstance(event_data, dict) and event_data.get("event") == "after_move_exit":
        destination = event_data.get("destination_room")
        if isinstance(destination, dict):
            try:
                return int(destination.get("id"))
            except (TypeError, ValueError):
                pass
    return trigger_room_id


def _trigger_gate_cache_key(trigger_id: int, scope_key: str) -> str:
    return f"spawns.trigger_gate.{trigger_id}.{scope_key}"


def _trigger_gate_delay(trigger: Trigger) -> int:
    try:
        return int(trigger.gate_delay or 0)
    except (TypeError, ValueError):
        return 0


def _claim_trigger_gate(
    *,
    trigger: Trigger,
    scope_key: str | None,
) -> tuple[str, str] | None:
    """Atomically claim a typed trigger gate while the runtime-room lock is held."""
    gate_delay = _trigger_gate_delay(trigger)
    if not scope_key or gate_delay == 0:
        return None

    gate_key = _trigger_gate_cache_key(trigger.id, scope_key)
    token = uuid.uuid4().hex
    timeout = None if gate_delay < 0 else gate_delay
    try:
        claimed = cache.add(gate_key, token, timeout=timeout)
    except Exception as exc:
        raise TriggerStepExecutionError(
            "The trigger gate is temporarily unavailable.",
            code="gate_unavailable",
        ) from exc
    if not claimed:
        raise TriggerStepExecutionError(
            TRIGGER_GATED_TEXT,
            code="gated",
        )
    return gate_key, token


def _release_trigger_gate(claim: tuple[str, str] | None) -> None:
    if claim is None:
        return
    gate_key, token = claim
    try:
        if cache.get(gate_key) == token:
            cache.delete(gate_key)
    except Exception:
        logger.exception("Failed to release trigger gate %s", gate_key)


def _lock_runtime_room(*, runtime_world_id: int, room_id: int) -> None:
    """Serialize starts only within one runtime-world/room pair."""
    if connection.vendor == "postgresql":
        lock_scope = f"scheduled-trigger-room:{runtime_world_id}:{room_id}".encode()
        lock_key = int.from_bytes(
            hashlib.blake2b(lock_scope, digest_size=8).digest(),
            byteorder="big",
            signed=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [lock_key],
            )
        return

    # Non-PostgreSQL development/test databases do not offer the scoped
    # advisory primitive. A runtime-world row lock preserves correctness there.
    World.objects.select_for_update().only("id").get(pk=runtime_world_id)


def _definition_ref_parts(value: Any) -> tuple[str, int | str]:
    if isinstance(value, bool):
        raise TriggerStepExecutionError(
            "Item definition reference is invalid.",
            code="invalid_item_definition",
        )
    if isinstance(value, int):
        return "id", value
    text = str(value or "").strip()
    if not text:
        raise TriggerStepExecutionError(
            "Item definition reference is missing.",
            code="invalid_item_definition",
        )
    prefix, separator, raw_value = text.partition(".")
    if separator:
        if prefix.strip().lower() not in {"itemdefinition", "item_definition"}:
            raise TriggerStepExecutionError(
                "Item reference must name an itemdefinition.",
                code="invalid_item_definition",
            )
        text = raw_value.strip()
    if not text:
        raise TriggerStepExecutionError(
            "Item definition reference is missing.",
            code="invalid_item_definition",
        )
    # A typed ref is always a portable slug ref. Bare numeric values are the
    # legacy database-id form.
    if separator:
        return "slug", text
    if text.isdigit():
        return "id", int(text)
    return "slug", text


def _snapshot_steps_with_definition_ids(
    steps: list[dict[str, Any]],
    *,
    authored_world_id: int,
) -> list[dict[str, Any]]:
    refs: set[tuple[str, int | str]] = set()
    for step in steps:
        for action in step.get("actions") or []:
            if "item" in action:
                refs.add(_definition_ref_parts(action.get("item")))
            if "with" in action:
                refs.add(_definition_ref_parts(action.get("with")))

    ids = [value for ref_type, value in refs if ref_type == "id"]
    slugs = [value for ref_type, value in refs if ref_type == "slug"]
    definitions = list(
        ItemDefinition.objects.filter(world_id=authored_world_id)
        .filter(Q(pk__in=ids) | Q(slug__in=slugs))
        .only("id", "slug")
    )
    by_id = {definition.id: definition for definition in definitions}
    by_slug = {definition.slug: definition for definition in definitions}
    resolved: dict[tuple[str, int | str], ItemDefinition] = {}
    for ref in refs:
        ref_type, value = ref
        definition = by_id.get(value) if ref_type == "id" else by_slug.get(value)
        if definition is None:
            raise TriggerStepExecutionError(
                f"Item definition '{value}' is unavailable in the trigger world.",
                code="item_definition_missing",
            )
        resolved[ref] = definition

    cumulative_seconds = 0
    snapshot = deepcopy(steps)
    for step in snapshot:
        cumulative_seconds += int(step["after_seconds"])
        step["due_after_seconds"] = cumulative_seconds
        for action in step.get("actions") or []:
            if "item" in action:
                definition = resolved[_definition_ref_parts(action.get("item"))]
                action["item_definition_id"] = definition.id
                action["item"] = f"itemdefinition.{definition.slug}"
            if "with" in action:
                definition = resolved[_definition_ref_parts(action.get("with"))]
                action["with_item_definition_id"] = definition.id
                action["with"] = f"itemdefinition.{definition.slug}"
    return snapshot


def _step_definitions(step: dict[str, Any], *, authored_world_id: int) -> dict[int, ItemDefinition]:
    definition_ids: set[int] = set()
    for action in step.get("actions") or []:
        if action.get("item_definition_id"):
            definition_ids.add(int(action["item_definition_id"]))
        if action.get("with_item_definition_id"):
            definition_ids.add(int(action["with_item_definition_id"]))
    definitions = {
        definition.id: definition
        for definition in ItemDefinition.objects.filter(
            world_id=authored_world_id,
            pk__in=definition_ids,
        )
    }
    missing = definition_ids - set(definitions)
    if missing:
        raise TriggerStepExecutionError(
            "An item definition used by this sequence no longer exists.",
            code="item_definition_missing",
        )
    return definitions


def _consume_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
) -> list[tuple[int, str]]:
    actor_model = _actor_model(run.actor_type)
    if actor_model is None:
        raise TriggerStepExecutionError(
            "The trigger actor type cannot hold inventory.",
            code="invalid_actor",
        )
    actor = actor_model.objects.select_for_update().filter(
        pk=run.actor_id,
        world_id=run.runtime_world_id,
    ).first()
    if actor is None:
        raise TriggerStepExecutionError(
            "The trigger actor is no longer available.",
            code="actor_missing",
        )

    count = int(action.get("count") or 1)
    items = list(
        actor.inventory.select_for_update()
        .filter(
            world_id=run.runtime_world_id,
            definition_id=definition.id,
            is_pending_deletion=False,
        )
        .order_by("id")
        .only("id")[:count]
    )
    if len(items) != count:
        raise TriggerStepExecutionError(
            f"The trigger actor does not have {count} required item(s).",
            code="required_item_missing",
        )
    removed = [(item.id, item.key) for item in items]
    Item.objects.filter(pk__in=[item.id for item in items]).delete()
    return removed


def _consume_room_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
) -> list[tuple[int, str]]:
    room_type = ContentType.objects.get_for_model(Room)
    count = int(action.get("count") or 1)
    items = list(
        Item.objects.select_for_update()
        .filter(
            world_id=run.runtime_world_id,
            container_type_id=room_type.id,
            container_id=run.room_id,
            definition_id=definition.id,
            is_pending_deletion=False,
        )
        .order_by("id")
        .only("id")[:count]
    )
    if len(items) != count:
        raise TriggerStepExecutionError(
            f"The trigger room does not have {count} required item(s).",
            code="required_room_item_missing",
        )
    removed = [(item.id, item.key) for item in items]
    Item.objects.filter(pk__in=[item.id for item in items]).delete()
    return removed


def _grant_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
    runtime_world: World,
) -> list[Item]:
    actor_model = _actor_model(run.actor_type)
    if actor_model is None:
        raise TriggerStepExecutionError(
            "The trigger actor type cannot hold inventory.",
            code="invalid_actor",
        )
    actor = actor_model.objects.select_for_update().filter(
        pk=run.actor_id,
        world_id=run.runtime_world_id,
    ).first()
    if actor is None:
        raise TriggerStepExecutionError(
            "The trigger actor is no longer available.",
            code="actor_missing",
        )

    return [
        definition.spawn(actor, runtime_world)
        for _index in range(int(action.get("count") or 1))
    ]


def _spawn_room_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
    room: Room,
    runtime_world: World,
    bindings: dict[str, Any],
) -> Item:
    item = definition.spawn(room, runtime_world)
    binding = str(action.get("bind") or "").strip()
    if binding:
        bindings[binding] = {
            "type": "item",
            "id": item.id,
        }
    return item


def _replace_room_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
    room: Room,
    runtime_world: World,
    bindings: dict[str, Any],
) -> tuple[int, str, Item]:
    binding_name = str(action.get("target") or "").strip()
    binding = bindings.get(binding_name)
    if not isinstance(binding, dict) or binding.get("type") != "item":
        raise TriggerStepExecutionError(
            f"The item binding '{binding_name}' is unavailable.",
            code="binding_missing",
        )
    try:
        item_id = int(binding.get("id"))
    except (TypeError, ValueError):
        raise TriggerStepExecutionError(
            f"The item binding '{binding_name}' is invalid.",
            code="binding_invalid",
        )

    room_type = ContentType.objects.get_for_model(Room)
    item = (
        Item.objects.select_for_update()
        .filter(
            pk=item_id,
            world_id=run.runtime_world_id,
            container_type_id=room_type.id,
            container_id=run.room_id,
            is_pending_deletion=False,
        )
        .first()
    )
    if item is None:
        raise TriggerStepExecutionError(
            f"The bound room item '{binding_name}' is no longer present.",
            code="bound_item_missing",
        )

    removed_item_id = item.id
    removed_item_key = item.key
    replacement = definition.spawn(room, runtime_world)
    item.delete()
    bindings[binding_name] = {
        "type": "item",
        "id": replacement.id,
    }
    return removed_item_id, removed_item_key, replacement


def _item_change_events(
    *,
    run: ScheduledTriggerRun,
    room: Room,
    changes: TriggerItemChanges,
    room_recipient_keys: tuple[str, ...],
) -> list[GameEvent]:
    if not changes.changed:
        return []

    # Deliberately omit a viewer so each bounded payload is non-personalized.
    # Actor inventory changes go only to that actor; other room occupants get
    # room changes without learning what was privately granted or consumed.
    from spawns.state_payloads import serialize_item

    added_payloads = [
        serialize_item(item, viewer=None).model_dump()
        for item in changes.room_items_added.values()
    ]
    actor_added_payloads = [
        serialize_item(item, viewer=None).model_dump()
        for item in changes.actor_inventory_added.values()
    ]

    room_changed = bool(changes.room_items_added or changes.room_items_removed)
    actor_changed = bool(
        changes.actor_inventory_added or changes.actor_inventory_removed
    )
    events: list[GameEvent] = []
    room_recipients = set(room_recipient_keys)
    if run.actor_type == "player" and room_changed:
        # Preserve the initiating player's room delta even if a delayed step
        # finishes after they have left the trigger room.
        room_recipients.add(run.actor_key)

    def payload(*, include_room: bool, include_actor: bool) -> dict[str, Any]:
        return {
            "room": {
                "id": room.id,
                "key": room.key,
            },
            "room_items_added": added_payloads if include_room else [],
            "room_items_removed": changes.room_items_removed if include_room else [],
            "actor_inventory_added": actor_added_payloads if include_actor else [],
            "actor_inventory_removed": (
                changes.actor_inventory_removed if include_actor else []
            ),
            "actor": {
                "key": run.actor_key,
            },
        }

    if run.actor_type == "player" and actor_changed:
        events.append(GameEvent(
            type="notification.trigger.items_changed",
            recipients=[run.actor_key],
            data=payload(include_room=room_changed, include_actor=True),
        ))
        room_recipients.discard(run.actor_key)

    if room_changed and room_recipients:
        events.append(GameEvent(
            type="notification.trigger.items_changed",
            recipients=sorted(room_recipients),
            data=payload(include_room=True, include_actor=False),
        ))

    return events


def _echo_event(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    room: Room,
    room_recipient_keys: tuple[str, ...],
) -> GameEvent | None:
    if not room_recipient_keys:
        return None
    text = str(action.get("text") or "").strip()
    return GameEvent(
        type="notification./echo",
        recipients=room_recipient_keys,
        data={
            "actor": {
                "key": run.actor_key,
                "char_type": run.actor_type,
            },
            "scope": "room",
            "room": {
                "id": room.id,
                "key": room.key,
                "name": room.name or "",
            },
            "message": text,
        },
        text=text,
    )


def _room_recipient_keys(run: ScheduledTriggerRun) -> tuple[str, ...]:
    return tuple(
        f"player.{player_id}"
        for player_id in Player.objects.filter(
            world_id=run.runtime_world_id,
            room_id=run.room_id,
            in_game=True,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def _execute_current_step(
    run: ScheduledTriggerRun,
    *,
    runtime_world: World | None = None,
    room: Room | None = None,
) -> list[GameEvent]:
    steps = run.steps if isinstance(run.steps, list) else []
    if run.next_step_index >= len(steps):
        raise TriggerStepExecutionError(
            "The scheduled trigger cursor is outside its step snapshot.",
            code="invalid_cursor",
        )
    step = steps[run.next_step_index]
    if not isinstance(step, dict):
        raise TriggerStepExecutionError(
            "The scheduled trigger step snapshot is invalid.",
            code="invalid_snapshot",
        )

    if runtime_world is None:
        runtime_world = World.objects.select_related(
            "context",
            "context__instance_of",
        ).get(pk=run.runtime_world_id)
    if room is None:
        room = Room.objects.get(pk=run.room_id)
    definition_world_id = _definition_world_id(runtime_world)
    definitions = _step_definitions(step, authored_world_id=definition_world_id)
    bindings = deepcopy(run.bindings or {})
    events: list[GameEvent] = []
    item_changes = TriggerItemChanges()
    room_recipient_keys = _room_recipient_keys(run)

    for action in step.get("actions") or []:
        action_type = action.get("type")
        if action_type == TRIGGER_STEP_ACTION_CONSUME_ITEM:
            for removed_id, removed_key in _consume_item(
                run=run,
                action=action,
                definition=definitions[int(action["item_definition_id"])],
            ):
                if item_changes.actor_inventory_added.pop(removed_id, None) is None:
                    item_changes.actor_inventory_removed.append({"key": removed_key})
        elif action_type == TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM:
            for removed_id, removed_key in _consume_room_item(
                run=run,
                action=action,
                definition=definitions[int(action["item_definition_id"])],
            ):
                if item_changes.room_items_added.pop(removed_id, None) is None:
                    item_changes.room_items_removed.append({"key": removed_key})
        elif action_type == TRIGGER_STEP_ACTION_GRANT_ITEM:
            for granted_item in _grant_item(
                run=run,
                action=action,
                definition=definitions[int(action["item_definition_id"])],
                runtime_world=runtime_world,
            ):
                item_changes.actor_inventory_added[granted_item.id] = granted_item
        elif action_type == TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM:
            spawned_item = _spawn_room_item(
                run=run,
                action=action,
                definition=definitions[int(action["item_definition_id"])],
                room=room,
                runtime_world=runtime_world,
                bindings=bindings,
            )
            item_changes.room_items_added[spawned_item.id] = spawned_item
        elif action_type == TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM:
            removed_id, removed_key, replacement = _replace_room_item(
                run=run,
                action=action,
                definition=definitions[int(action["with_item_definition_id"])],
                room=room,
                runtime_world=runtime_world,
                bindings=bindings,
            )
            if item_changes.room_items_added.pop(removed_id, None) is None:
                item_changes.room_items_removed.append({"key": removed_key})
            item_changes.room_items_added[replacement.id] = replacement
        elif action_type == TRIGGER_STEP_ACTION_ECHO:
            event = _echo_event(
                run=run,
                action=action,
                room=room,
                room_recipient_keys=room_recipient_keys,
            )
            if event is not None:
                events.append(event)
        else:
            raise TriggerStepExecutionError(
                f"Unsupported scheduled trigger action '{action_type}'.",
                code="unsupported_action",
            )

    events.extend(
        _item_change_events(
            run=run,
            room=room,
            changes=item_changes,
            room_recipient_keys=room_recipient_keys,
        )
    )

    run.bindings = bindings
    run.next_step_index += 1
    if run.next_step_index >= len(steps):
        run.status = ScheduledTriggerRun.STATUS_COMPLETED
        run.completed_ts = timezone.now()
    else:
        next_step = steps[run.next_step_index]
        run.next_run_ts = run.started_ts + timedelta(
            seconds=int(next_step["due_after_seconds"]),
        )
    run.failure_code = ""
    run.last_error = ""
    run.save(
        update_fields=[
            "bindings",
            "next_step_index",
            "next_run_ts",
            "status",
            "completed_ts",
            "failure_code",
            "last_error",
            "modified_ts",
        ]
    )
    return events


def start_trigger_steps(
    *,
    trigger: Trigger | Any,
    actor: Player | Mob,
    room: Room,
    conditions: str | None = None,
    event_data: dict[str, Any] | None = None,
    gate_scope_key: str | None = None,
) -> TriggerStepStartResult:
    actor_type = _actor_kind(actor)
    actor_model = _actor_model(actor_type)
    runtime_world_id = getattr(actor, "world_id", None)
    trigger_id = getattr(trigger, "id", None)
    if actor_model is None or not runtime_world_id or not room.id or not trigger_id:
        return TriggerStepStartResult(
            started=False,
            feedback="The trigger has no stable runtime context.",
            code="invalid_context",
        )

    gate_claim: tuple[str, str] | None = None
    try:
        with transaction.atomic():
            _lock_runtime_room(
                runtime_world_id=runtime_world_id,
                room_id=room.id,
            )
            locked_actor = (
                actor_model.objects.select_for_update()
                .get(pk=actor.id)
            )
            expected_actor_room_id = _expected_actor_room_id(
                trigger_room_id=room.id,
                event_data=event_data,
            )
            if (
                locked_actor.world_id != runtime_world_id
                or locked_actor.room_id != expected_actor_room_id
            ):
                raise TriggerStepExecutionError(
                    "The trigger actor is no longer in the triggering room.",
                    code="context_changed",
                )

            current_trigger = Trigger.objects.filter(
                pk=trigger_id,
                is_active=True,
            ).first()
            if current_trigger is None:
                raise TriggerStepExecutionError(
                    "The trigger is no longer active.",
                    code="trigger_missing",
                )

            try:
                normalized_steps = normalize_trigger_steps(current_trigger.steps)
                error_policy = normalize_trigger_step_error_policy(
                    current_trigger.on_step_error
                )
            except TriggerStepSpecError as exc:
                raise TriggerStepExecutionError(
                    str(exc),
                    code="invalid_steps",
                ) from exc
            if not normalized_steps:
                raise TriggerStepExecutionError(
                    "The trigger no longer has scheduled steps.",
                    code="no_steps",
                )

            runtime_world = World.objects.select_related(
                "context",
                "context__instance_of",
            ).get(pk=runtime_world_id)
            definition_world_id = _definition_world_id(runtime_world)
            trigger_owner_world_id = _trigger_owner_world_id(room)
            if current_trigger.world_id != trigger_owner_world_id:
                raise TriggerStepExecutionError(
                    "The trigger does not belong to the triggering room's template world.",
                    code="world_mismatch",
                )

            condition_text = (
                conditions
                if conditions is not None
                else current_trigger.conditions
            )
            if condition_text:
                evaluated = evaluate_conditions(
                    locked_actor,
                    condition_text,
                    room=room,
                    zone=room.zone,
                    world=runtime_world,
                    event_data=event_data,
                )
                if not evaluated.get("result"):
                    raise TriggerStepExecutionError(
                        current_trigger.failure_message
                        or evaluated.get("detail")
                        or "Action could not be completed.",
                        code="conditions_failed",
                    )

            if ScheduledTriggerRun.objects.filter(
                trigger=current_trigger,
                runtime_world_id=runtime_world_id,
                room_id=room.id,
                actor_type=actor_type,
                actor_id=locked_actor.id,
                status=ScheduledTriggerRun.STATUS_ACTIVE,
            ).exists():
                raise TriggerStepExecutionError(
                    "That trigger already has a scheduled sequence in progress.",
                    code="already_running",
                )

            active_run_ids = list(
                ScheduledTriggerRun.objects.filter(
                    runtime_world_id=runtime_world_id,
                    actor_type=actor_type,
                    actor_id=locked_actor.id,
                    status=ScheduledTriggerRun.STATUS_ACTIVE,
                ).values_list("id", flat=True)[:MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR]
            )
            if len(active_run_ids) >= MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR:
                raise TriggerStepExecutionError(
                    "That actor already has too many scheduled sequences in progress.",
                    code="too_many_active_sequences",
                )

            gate_claim = _claim_trigger_gate(
                trigger=current_trigger,
                scope_key=gate_scope_key,
            )
            try:
                snapshot_steps = _snapshot_steps_with_definition_ids(
                    normalized_steps,
                    authored_world_id=definition_world_id,
                )
                started_ts = timezone.now()
                run = ScheduledTriggerRun.objects.create(
                    trigger=current_trigger,
                    runtime_world_id=runtime_world_id,
                    room_id=room.id,
                    actor_type=actor_type,
                    actor_id=locked_actor.id,
                    actor_key=locked_actor.key,
                    steps=snapshot_steps,
                    bindings={},
                    next_step_index=0,
                    next_run_ts=started_ts,
                    started_ts=started_ts,
                    status=ScheduledTriggerRun.STATUS_ACTIVE,
                    on_step_error=error_policy,
                )
                events = _execute_current_step(
                    run,
                    runtime_world=runtime_world,
                    room=room,
                )
                queued_event_count = enqueue_game_events(events)
                if queued_event_count:
                    transaction.on_commit(_flush_queued_events, robust=True)
            except Exception:
                # Release while the room advisory lock is still held so a
                # failed claimant cannot delete a successor's cache token.
                _release_trigger_gate(gate_claim)
                gate_claim = None
                raise
    except (OperationalError, InterfaceError):
        _release_trigger_gate(gate_claim)
        raise
    except (TriggerStepExecutionError, actor_model.DoesNotExist) as exc:
        _release_trigger_gate(gate_claim)
        return TriggerStepStartResult(
            started=False,
            feedback=str(exc),
            code=getattr(exc, "code", "actor_missing"),
        )
    except IntegrityError:
        _release_trigger_gate(gate_claim)
        if ScheduledTriggerRun.objects.filter(
            trigger_id=trigger_id,
            runtime_world_id=runtime_world_id,
            room_id=room.id,
            actor_type=actor_type,
            actor_id=actor.id,
            status=ScheduledTriggerRun.STATUS_ACTIVE,
        ).exists():
            return TriggerStepStartResult(
                started=False,
                feedback="That trigger already has a scheduled sequence in progress.",
                code="already_running",
            )
        if not Trigger.objects.filter(pk=trigger_id, is_active=True).exists():
            return TriggerStepStartResult(
                started=False,
                feedback="The trigger is no longer active.",
                code="trigger_missing",
            )
        raise
    except Exception:
        _release_trigger_gate(gate_claim)
        raise

    return TriggerStepStartResult(started=True, run_id=run.id)


def _advance_one_due_run(*, due_at) -> str | None:
    with transaction.atomic():
        run = (
            ScheduledTriggerRun.objects.select_related(
                "runtime_world__context__instance_of",
                "room",
            )
            .select_for_update(skip_locked=True, of=("self",))
            .filter(
                status=ScheduledTriggerRun.STATUS_ACTIVE,
                next_run_ts__lte=due_at,
            )
            .order_by("next_run_ts", "id")
            .first()
        )
        if run is None:
            return None

        try:
            with transaction.atomic():
                events = _execute_current_step(
                    run,
                    runtime_world=run.runtime_world,
                    room=run.room,
                )
                enqueue_game_events(events)
        except (OperationalError, InterfaceError):
            raise
        except Exception as exc:
            if isinstance(exc, TriggerStepExecutionError):
                failure_code = exc.code
            else:
                failure_code = "step_exception"
                logger.exception(
                    "Cancelling malformed scheduled trigger run %s",
                    run.id,
                )
            run.status = ScheduledTriggerRun.STATUS_CANCELLED
            run.failure_code = failure_code
            run.last_error = str(exc)[:4_000]
            run.completed_ts = timezone.now()
            run.save(
                update_fields=[
                    "status",
                    "failure_code",
                    "last_error",
                    "completed_ts",
                    "modified_ts",
                ]
            )
            return ScheduledTriggerRun.STATUS_CANCELLED
        return run.status


def process_due_trigger_runs(
    *,
    limit: int = DEFAULT_DUE_RUN_LIMIT,
    now=None,
) -> dict[str, int]:
    row_limit = max(1, min(int(limit or 1), 1_000))
    due_at = now or timezone.now()
    result = {
        "processed": 0,
        "completed": 0,
        "cancelled": 0,
    }
    for _ in range(row_limit):
        status = _advance_one_due_run(due_at=due_at)
        if status is None:
            break
        result["processed"] += 1
        if status == ScheduledTriggerRun.STATUS_COMPLETED:
            result["completed"] += 1
        elif status == ScheduledTriggerRun.STATUS_CANCELLED:
            result["cancelled"] += 1

    if result["processed"]:
        transaction.on_commit(_flush_queued_events, robust=True)
    return result


def prune_terminal_trigger_runs(
    *,
    retention_days: int = 7,
    batch_size: int = 5_000,
    max_batches: int = 20,
) -> int:
    try:
        days = max(1, int(retention_days))
    except (TypeError, ValueError):
        days = 7
    cutoff = timezone.now() - timedelta(days=days)
    try:
        row_limit = max(1, min(int(batch_size), 5_000))
    except (TypeError, ValueError):
        row_limit = 5_000
    try:
        batch_limit = max(1, min(int(max_batches), 100))
    except (TypeError, ValueError):
        batch_limit = 20

    deleted_total = 0
    for _ in range(batch_limit):
        terminal_ids = list(
            ScheduledTriggerRun.objects.filter(
                status__in=(
                    ScheduledTriggerRun.STATUS_COMPLETED,
                    ScheduledTriggerRun.STATUS_CANCELLED,
                ),
                modified_ts__lt=cutoff,
            )
            .order_by("status", "modified_ts", "id")
            .values_list("id", flat=True)[:row_limit]
        )
        if not terminal_ids:
            break
        deleted, _ = ScheduledTriggerRun.objects.filter(
            pk__in=terminal_ids,
        ).delete()
        deleted_total += deleted
        if len(terminal_ids) < row_limit:
            break
    return deleted_total
