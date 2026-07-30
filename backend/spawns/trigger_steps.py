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

from builders.models import Currency, ItemDefinition, MobDefinition, Trigger
from core.condition_dsl import ConditionContext, evaluate_condition
from core.conditions import evaluate_conditions
from core.economy import format_currency, money_payload
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    normalize_state_snapshot,
)
from core.trigger_steps import (
    TRIGGER_ACTOR_REF,
    TRIGGER_ROOM_REF,
    TRIGGER_STEP_ACTION_COMMAND,
    TRIGGER_STEP_ACTION_CONSUME_ITEM,
    TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM,
    TRIGGER_STEP_ACTION_DEBIT_CURRENCY,
    TRIGGER_STEP_ACTION_ECHO,
    TRIGGER_STEP_ACTION_GRANT_ITEM,
    TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM,
    TRIGGER_STEP_ACTION_SET_MOB,
    TRIGGER_STEP_ACTION_SPAWN_ROOM_ITEM,
    SCRIPT_COMMAND_DEPTH_KEY,
    TriggerStepSpecError,
    normalize_trigger_step_error_policy,
    normalize_trigger_steps,
)
from spawns.events import (
    GameEvent,
    PRIVATE_CONTROL_EVENT_KEY,
    enqueue_game_events,
    flush_game_event_outbox,
    publish_events,
)
from spawns.handlers.base import TRIGGER_STEP_MODE_TRANSACTIONAL
from spawns.models import (
    Item,
    Mob,
    MobState,
    Player,
    PlayerCurrencyBalance,
    ScheduledTriggerRun,
)
from spawns.request_segments import normalize_request_segment
from spawns.script_commands import (
    ScriptCommandError,
    ScriptCommandRunner,
)
from spawns.wallet import WalletError, WalletMutation, mutate_balances
from worlds.models import Room, World


DEFAULT_DUE_RUN_LIMIT = 100
MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR = 16
MAX_TRIGGER_SET_MOB_CANDIDATES = 256
TRIGGER_GATED_TEXT = "More time is needed."
TRIGGER_CANCELLED_TEXT = "That action can no longer be completed."
_TRIGGER_PROVENANCE_BINDING_KEY = "_trigger_provenance"


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


@dataclass(frozen=True)
class TriggerStepContinuation:
    run_id: int
    expected_step_index: int


@dataclass(frozen=True)
class TriggerStepAdvanceResult:
    status: str
    run_id: int
    continuation: TriggerStepContinuation | None = None


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


@dataclass
class TriggerMobChange:
    mob: Mob
    fields: set[str] = field(default_factory=set)


@dataclass
class TriggerMobChanges:
    updated: dict[int, TriggerMobChange] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.updated)


@dataclass(frozen=True)
class TriggerStepPrelocks:
    mob_ids_by_definition: dict[int, tuple[int, ...]]
    actor_item_ids_by_definition: dict[int, tuple[int, ...]]
    room_item_ids_by_definition: dict[int, tuple[int, ...]]


def _flush_queued_events() -> None:
    flush_game_event_outbox(publisher=publish_events)


def _due_run_continuation(
    run: ScheduledTriggerRun,
    *,
    due_at,
) -> TriggerStepContinuation | None:
    if (
        run.status != ScheduledTriggerRun.STATUS_ACTIVE
        or run.next_run_ts is None
        or run.next_run_ts > due_at
    ):
        return None
    return TriggerStepContinuation(
        run_id=run.id,
        expected_step_index=run.next_step_index,
    )


def _enqueue_trigger_step_continuation(
    continuation: TriggerStepContinuation,
) -> None:
    from spawns.tasks import continue_scheduled_trigger_run

    continue_scheduled_trigger_run.apply_async(
        kwargs={
            "run_id": continuation.run_id,
            "expected_step_index": continuation.expected_step_index,
        },
    )


def _schedule_trigger_step_continuation(
    run: ScheduledTriggerRun,
    *,
    due_at,
) -> None:
    continuation = _due_run_continuation(run, due_at=due_at)
    if continuation is None:
        return

    def enqueue() -> None:
        _enqueue_trigger_step_continuation(continuation)

    transaction.on_commit(enqueue, robust=True)


def _request_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TriggerStepExecutionError(
            "Invalid request identifier.",
            code="invalid_request_id",
        ) from exc


def _trigger_request_status_data(
    run: ScheduledTriggerRun,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        PRIVATE_CONTROL_EVENT_KEY: True,
        "request_id": str(run.request_id),
        "request_segment": normalize_request_segment(run.request_segment),
        "status": status,
    }


def _trigger_accepted_event(
    run: ScheduledTriggerRun,
    *,
    first_step_delay_seconds: int,
) -> GameEvent | None:
    if (
        run.actor_type != "player"
        or run.request_id is None
        or not run.request_connection_id
    ):
        return None
    return GameEvent(
        type="cmd.trigger.accepted",
        recipients=[run.actor_key],
        connection_id=run.request_connection_id,
        data={
            **_trigger_request_status_data(run, status="accepted"),
            "delayed": first_step_delay_seconds > 0,
            "first_step_delay_seconds": first_step_delay_seconds,
            "first_step_due_at": run.next_run_ts.isoformat(),
        },
    )


def _trigger_completed_event(
    run: ScheduledTriggerRun,
) -> GameEvent | None:
    if (
        run.actor_type != "player"
        or run.request_id is None
        or not run.request_connection_id
    ):
        return None
    return GameEvent(
        type="cmd.trigger.completed",
        recipients=[run.actor_key],
        connection_id=run.request_connection_id,
        data={
            **_trigger_request_status_data(run, status="completed"),
        },
    )


def _trigger_cancelled_events(
    run: ScheduledTriggerRun,
) -> list[GameEvent]:
    if run.actor_type != "player" or run.request_id is None:
        return []
    events: list[GameEvent] = []
    if run.request_id is not None and run.request_connection_id:
        events.append(GameEvent(
            type="cmd.trigger.cancelled",
            recipients=[run.actor_key],
            connection_id=run.request_connection_id,
            data={
                **_trigger_request_status_data(
                    run,
                    status="cancelled",
                ),
                "code": "trigger_cancelled",
                "message": TRIGGER_CANCELLED_TEXT,
            },
        ))
    events.append(
        GameEvent(
            type="notification.trigger.cancelled",
            recipients=[run.actor_key],
            data={
                PRIVATE_CONTROL_EVENT_KEY: True,
                "status": "cancelled",
                "code": "trigger_cancelled",
                "message": TRIGGER_CANCELLED_TEXT,
            },
            text=TRIGGER_CANCELLED_TEXT,
        )
    )
    return events


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


def _script_command_depth(event_data: dict[str, Any] | None) -> int:
    if not isinstance(event_data, dict):
        return 0
    try:
        return max(0, int(event_data.get(SCRIPT_COMMAND_DEPTH_KEY) or 0))
    except (TypeError, ValueError):
        return 0


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


def release_trigger_gate_claims(
    claims: list[tuple[str, str]],
) -> None:
    for claim in reversed(claims):
        _release_trigger_gate(claim)


def lock_trigger_runtime_room(*, runtime_world_id: int, room_id: int) -> None:
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


def _mob_definition_ref_parts(value: Any) -> tuple[str, int | str]:
    if isinstance(value, bool):
        raise TriggerStepExecutionError(
            "Mob definition reference is invalid.",
            code="invalid_mob_definition",
        )
    if isinstance(value, int):
        return "id", value
    text = str(value or "").strip()
    if not text:
        raise TriggerStepExecutionError(
            "Mob definition reference is missing.",
            code="invalid_mob_definition",
        )
    prefix, separator, raw_value = text.partition(".")
    if separator:
        if prefix.strip().lower() not in {"mobdefinition", "mob_definition"}:
            raise TriggerStepExecutionError(
                "Mob reference must name a mobdefinition.",
                code="invalid_mob_definition",
            )
        text = raw_value.strip()
    if not text:
        raise TriggerStepExecutionError(
            "Mob definition reference is missing.",
            code="invalid_mob_definition",
        )
    if separator:
        return "slug", text
    if text.isdigit():
        return "id", int(text)
    return "slug", text


def _command_mob_subject(
    action: dict[str, Any],
) -> dict[str, Any] | None:
    if action.get("type") != TRIGGER_STEP_ACTION_COMMAND:
        return None
    subject = action.get("subject")
    if not isinstance(subject, dict) or subject.get("type") != "mob":
        return None
    return subject


def _step_mob_selector(
    action: dict[str, Any],
) -> dict[str, Any] | None:
    if action.get("type") == TRIGGER_STEP_ACTION_SET_MOB:
        return action
    return _command_mob_subject(action)


def _snapshot_steps_with_definition_ids(
    steps: list[dict[str, Any]],
    *,
    authored_world_id: int,
) -> list[dict[str, Any]]:
    refs: set[tuple[str, int | str]] = set()
    mob_refs: set[tuple[str, int | str]] = set()
    currency_codes: set[str] = set()
    for step in steps:
        for action in step.get("actions") or []:
            if "item" in action:
                refs.add(_definition_ref_parts(action.get("item")))
            if "with" in action:
                refs.add(_definition_ref_parts(action.get("with")))
            if action.get("type") == TRIGGER_STEP_ACTION_SET_MOB:
                mob_refs.add(_mob_definition_ref_parts(action.get("mob")))
            command_subject = _command_mob_subject(action)
            if command_subject is not None:
                mob_refs.add(
                    _mob_definition_ref_parts(command_subject.get("mob"))
                )
            if action.get("type") == TRIGGER_STEP_ACTION_DEBIT_CURRENCY:
                currency_codes.add(str(action.get("currency") or "").strip().lower())

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

    mob_ids = [value for ref_type, value in mob_refs if ref_type == "id"]
    mob_slugs = [value for ref_type, value in mob_refs if ref_type == "slug"]
    mob_definitions = list(
        MobDefinition.objects.filter(world_id=authored_world_id)
        .filter(Q(pk__in=mob_ids) | Q(slug__in=mob_slugs))
        .only("id", "slug")
    )
    mobs_by_id = {definition.id: definition for definition in mob_definitions}
    mobs_by_slug = {definition.slug: definition for definition in mob_definitions}
    resolved_mobs: dict[tuple[str, int | str], MobDefinition] = {}
    for ref in mob_refs:
        ref_type, value = ref
        definition = (
            mobs_by_id.get(value)
            if ref_type == "id"
            else mobs_by_slug.get(value)
        )
        if definition is None:
            raise TriggerStepExecutionError(
                f"Mob definition '{value}' is unavailable in the trigger world.",
                code="mob_definition_missing",
            )
        resolved_mobs[ref] = definition

    currencies = {
        currency.code: currency
        for currency in Currency.objects.filter(
            world_id=authored_world_id,
            code__in=currency_codes,
        ).only("id", "code")
    }
    missing_currency_codes = currency_codes - set(currencies)
    if missing_currency_codes:
        missing_code = sorted(missing_currency_codes)[0]
        raise TriggerStepExecutionError(
            f"Currency '{missing_code}' is unavailable in the trigger world.",
            code="currency_missing",
        )

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
            if action.get("type") == TRIGGER_STEP_ACTION_SET_MOB:
                definition = resolved_mobs[
                    _mob_definition_ref_parts(action.get("mob"))
                ]
                action["mob_definition_id"] = definition.id
                action["mob"] = f"mobdefinition.{definition.slug}"
            command_subject = _command_mob_subject(action)
            if command_subject is not None:
                definition = resolved_mobs[
                    _mob_definition_ref_parts(command_subject.get("mob"))
                ]
                command_subject["mob_definition_id"] = definition.id
                command_subject["mob"] = f"mobdefinition.{definition.slug}"
            if action.get("type") == TRIGGER_STEP_ACTION_DEBIT_CURRENCY:
                currency = currencies[str(action.get("currency") or "").lower()]
                action["currency_id"] = currency.id
                action["currency"] = currency.code
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


def _set_mob_candidate_ids(
    *,
    runtime_world_id: int,
    room_id: int,
    definition_id: int,
    has_candidate_predicate: bool,
    action_label: str = "set_mob",
) -> tuple[int, ...]:
    action_display = action_label.replace("_", " ")
    query_limit = (
        MAX_TRIGGER_SET_MOB_CANDIDATES + 1
        if has_candidate_predicate
        else 2
    )
    candidate_ids = tuple(
        Mob.objects.filter(
            world_id=runtime_world_id,
            room_id=room_id,
            definition_id=definition_id,
            is_pending_deletion=False,
        )
        .order_by("id")
        .values_list("id", flat=True)[:query_limit]
    )
    if (
        has_candidate_predicate
        and len(candidate_ids) > MAX_TRIGGER_SET_MOB_CANDIDATES
    ):
        raise TriggerStepExecutionError(
            f"The {action_display} action has too many candidates to evaluate "
            f"safely (maximum {MAX_TRIGGER_SET_MOB_CANDIDATES}).",
            code=f"{action_label}_candidate_limit",
        )
    return candidate_ids


def _lock_mob_rows(
    *,
    runtime_world_id: int,
    mob_ids: set[int],
) -> tuple[int, ...]:
    if not mob_ids:
        return ()
    return tuple(
        Mob.objects.select_for_update(of=("self",))
        .filter(
            pk__in=mob_ids,
            world_id=runtime_world_id,
            is_pending_deletion=False,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def _step_locks_mob_actor(
    *,
    run: ScheduledTriggerRun,
    actions: list[dict[str, Any]],
    include_mob_actor: bool,
) -> bool:
    if _actor_model(run.actor_type) is not Mob:
        return False
    return (
        include_mob_actor
        or any(
            action.get("type") == TRIGGER_STEP_ACTION_COMMAND
            for action in actions
        )
        or any(
            action.get("type") == TRIGGER_STEP_ACTION_SET_MOB
            for action in actions
        )
        or any(
            action.get("type")
            in {
                TRIGGER_STEP_ACTION_CONSUME_ITEM,
                TRIGGER_STEP_ACTION_GRANT_ITEM,
            }
            for action in actions
        )
    )


def _prelock_step_mobs(
    *,
    run: ScheduledTriggerRun,
    actions: list[dict[str, Any]],
    include_mob_actor: bool = False,
) -> dict[int, tuple[int, ...]]:
    mob_actions_by_definition: dict[int, list[dict[str, Any]]] = {}
    set_mob_definition_ids: set[int] = set()
    for action in actions:
        selector = _step_mob_selector(action)
        if selector is not None and selector.get("mob_definition_id"):
            definition_id = int(selector["mob_definition_id"])
            mob_actions_by_definition.setdefault(
                definition_id,
                [],
            ).append(selector)
            if action.get("type") == TRIGGER_STEP_ACTION_SET_MOB:
                set_mob_definition_ids.add(definition_id)

    prelocked_mob_ids: dict[int, tuple[int, ...]] = {}
    mob_ids_to_lock: set[int] = set()
    for definition_id, definition_actions in sorted(
        mob_actions_by_definition.items()
    ):
        candidate_ids = _set_mob_candidate_ids(
            runtime_world_id=run.runtime_world_id,
            room_id=run.room_id,
            definition_id=definition_id,
            has_candidate_predicate=any(
                action.get("where") not in (None, {}, [])
                for action in definition_actions
            ),
            action_label=(
                "set_mob"
                if definition_id in set_mob_definition_ids
                else "command_subject"
            ),
        )
        prelocked_mob_ids[definition_id] = candidate_ids
        mob_ids_to_lock.update(candidate_ids)

    if _step_locks_mob_actor(
        run=run,
        actions=actions,
        include_mob_actor=include_mob_actor,
    ):
        mob_ids_to_lock.add(run.actor_id)
    # Actor and target Mobs share one globally ordered lock acquisition.
    _lock_mob_rows(
        runtime_world_id=run.runtime_world_id,
        mob_ids=mob_ids_to_lock,
    )
    return prelocked_mob_ids


def _prelock_step_resources(
    *,
    run: ScheduledTriggerRun,
    actions: list[dict[str, Any]],
    bindings: dict[str, Any],
    include_mob_actor: bool = False,
    prelocked_mob_ids_by_definition: (
        dict[int, tuple[int, ...]] | None
    ) = None,
    mob_rows_prelocked: bool = False,
) -> TriggerStepPrelocks | None:
    """Lock a mixed step's existing Mob, then Item rows in aggregate order."""
    mob_actions = [
        selector
        for action in actions
        if (selector := _step_mob_selector(action)) is not None
    ]
    existing_item_actions = [
        action
        for action in actions
        if action.get("type")
        in {
            TRIGGER_STEP_ACTION_CONSUME_ITEM,
            TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM,
            TRIGGER_STEP_ACTION_GRANT_ITEM,
            TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM,
        }
    ]
    has_debit = any(
        action.get("type") == TRIGGER_STEP_ACTION_DEBIT_CURRENCY
        for action in actions
    )
    actor_model = _actor_model(run.actor_type)
    lock_mob_actor = _step_locks_mob_actor(
        run=run,
        actions=actions,
        include_mob_actor=include_mob_actor,
    )
    needs_prelock = (
        has_debit
        or lock_mob_actor
        or bool(mob_actions)
        or len(existing_item_actions) > 1
    )
    if not needs_prelock:
        return None

    if mob_rows_prelocked:
        prelocked_mob_ids = dict(
            prelocked_mob_ids_by_definition or {}
        )
    else:
        prelocked_mob_ids = _prelock_step_mobs(
            run=run,
            actions=actions,
            include_mob_actor=include_mob_actor,
        )

    actor_definition_counts: dict[int, int] = {}
    room_definition_counts: dict[int, int] = {}
    for action in actions:
        action_type = action.get("type")
        if action_type not in {
            TRIGGER_STEP_ACTION_CONSUME_ITEM,
            TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM,
        }:
            continue
        definition_id = int(action["item_definition_id"])
        target_counts = (
            actor_definition_counts
            if action_type == TRIGGER_STEP_ACTION_CONSUME_ITEM
            else room_definition_counts
        )
        target_counts[definition_id] = (
            target_counts.get(definition_id, 0)
            + int(action.get("count") or 1)
        )

    actor_item_ids: dict[int, tuple[int, ...]] = {}
    if actor_definition_counts and actor_model is not None:
        actor_type_id = ContentType.objects.get_for_model(actor_model).id
        for definition_id, count in sorted(actor_definition_counts.items()):
            actor_item_ids[definition_id] = tuple(
                Item.objects.filter(
                    world_id=run.runtime_world_id,
                    container_type_id=actor_type_id,
                    container_id=run.actor_id,
                    definition_id=definition_id,
                    is_pending_deletion=False,
                )
                .order_by("id")
                .values_list("id", flat=True)[:count]
            )

    room_item_ids: dict[int, tuple[int, ...]] = {}
    if room_definition_counts:
        room_type_id = ContentType.objects.get_for_model(Room).id
        replacement_slack = sum(
            1
            for action in actions
            if action.get("type") == TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM
        )
        for definition_id, count in sorted(room_definition_counts.items()):
            room_item_ids[definition_id] = tuple(
                Item.objects.filter(
                    world_id=run.runtime_world_id,
                    container_type_id=room_type_id,
                    container_id=run.room_id,
                    definition_id=definition_id,
                    is_pending_deletion=False,
                )
                .order_by("id")
                .values_list("id", flat=True)[:count + replacement_slack]
            )

    bound_item_ids: set[int] = set()
    for action in actions:
        if action.get("type") != TRIGGER_STEP_ACTION_REPLACE_ROOM_ITEM:
            continue
        binding = bindings.get(str(action.get("target") or "").strip())
        if not isinstance(binding, dict) or binding.get("type") != "item":
            continue
        try:
            bound_item_ids.add(int(binding["id"]))
        except (KeyError, TypeError, ValueError):
            continue

    item_ids = {
        item_id
        for ids_by_definition in (actor_item_ids, room_item_ids)
        for candidate_ids in ids_by_definition.values()
        for item_id in candidate_ids
    }
    item_ids.update(bound_item_ids)
    if item_ids:
        # Candidate selection is capped by the sum of authored consume counts.
        # Lock that exact stable set together so a later drop cannot introduce
        # a new, out-of-order Item lock.
        list(
            Item.objects.select_for_update()
            .filter(
                pk__in=item_ids,
                world_id=run.runtime_world_id,
                is_pending_deletion=False,
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
    return TriggerStepPrelocks(
        mob_ids_by_definition=prelocked_mob_ids,
        actor_item_ids_by_definition=actor_item_ids,
        room_item_ids_by_definition=room_item_ids,
    )


def _consume_item(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    definition: ItemDefinition,
    candidate_ids: tuple[int, ...] | None = None,
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
    candidates = actor.inventory.select_for_update().filter(
        world_id=run.runtime_world_id,
        definition_id=definition.id,
        is_pending_deletion=False,
    )
    if candidate_ids is not None:
        candidates = candidates.filter(pk__in=candidate_ids)
    items = list(candidates.order_by("id").only("id")[:count])
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
    candidate_ids: tuple[int, ...] | None = None,
) -> list[tuple[int, str]]:
    room_type = ContentType.objects.get_for_model(Room)
    count = int(action.get("count") or 1)
    candidates = Item.objects.select_for_update().filter(
        world_id=run.runtime_world_id,
        container_type_id=room_type.id,
        container_id=run.room_id,
        definition_id=definition.id,
        is_pending_deletion=False,
    )
    if candidate_ids is not None:
        candidates = candidates.filter(pk__in=candidate_ids)
    items = list(candidates.order_by("id").only("id")[:count])
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


def _load_trigger_actor(run: ScheduledTriggerRun) -> Player | Mob | None:
    actor_model = _actor_model(run.actor_type)
    if actor_model is None:
        return None
    queryset = actor_model.objects.filter(
        pk=run.actor_id,
        world_id=run.runtime_world_id,
    )
    if actor_model is Mob:
        queryset = queryset.filter(is_pending_deletion=False)
    return queryset.first()


def _set_mob_context(
    *,
    mob: Mob,
    trigger_actor: Player | Mob | None,
    room: Room,
    runtime_world: World,
    state_snapshot: dict[str, Any] | None = None,
    invariant_state_cache: dict[str, dict[str, Any]] | None = None,
) -> ConditionContext:
    if state_snapshot is None:
        state_record = mob._state.fields_cache.get("character_state_record")
        state_snapshot = dict(getattr(state_record, "data", {}) or {})
    state_cache = dict(invariant_state_cache or {})
    state_cache[STATE_SCOPE_CHARACTER] = state_snapshot
    return ConditionContext(
        actor=mob,
        player=trigger_actor if isinstance(trigger_actor, Player) else None,
        room=room,
        zone=room.zone,
        world=runtime_world,
        state_cache=state_cache,
    )


def _matches_set_mob_where(
    where: Any,
    *,
    mob: Mob,
    trigger_actor: Player | Mob | None,
    room: Room,
    runtime_world: World,
    invariant_state_cache: dict[str, dict[str, Any]],
    state_snapshot: dict[str, Any] | None = None,
) -> bool:
    context = _set_mob_context(
        mob=mob,
        trigger_actor=trigger_actor,
        room=room,
        runtime_world=runtime_world,
        state_snapshot=state_snapshot,
        invariant_state_cache=invariant_state_cache,
    )
    matches = evaluate_condition(where, context=context)
    for scope, snapshot in context.state_cache.items():
        if scope != STATE_SCOPE_CHARACTER:
            invariant_state_cache[scope] = snapshot
    return matches


def _lock_or_create_mob_state(mob: Mob) -> tuple[MobState, bool]:
    state_record = (
        MobState.objects.select_for_update()
        .filter(mob_id=mob.id)
        .first()
    )
    if state_record is not None:
        return state_record, False

    try:
        with transaction.atomic():
            state_record = MobState.objects.create(
                mob=mob,
                data={},
                version=0,
            )
        return state_record, True
    except IntegrityError:
        # A state writer may have created the one-to-one row after the
        # initial lookup. Lock its row before rechecking the predicate.
        return (
            MobState.objects.select_for_update().get(mob_id=mob.id),
            False,
        )


def _select_step_mob(
    *,
    run: ScheduledTriggerRun,
    selector: dict[str, Any],
    room: Room,
    runtime_world: World,
    trigger_actor: Player | Mob | None,
    candidate_ids: tuple[int, ...] | None,
    action_label: str,
    not_found_code: str,
    ambiguous_code: str,
) -> tuple[Mob, dict[str, dict[str, Any]]]:
    action_display = action_label.replace("_", " ")
    try:
        definition_id = int(selector["mob_definition_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TriggerStepExecutionError(
            f"The {action_display} action has no valid mob definition.",
            code="invalid_mob_definition",
        ) from exc

    where = selector.get("where")
    if candidate_ids is None:
        candidate_ids = _set_mob_candidate_ids(
            runtime_world_id=run.runtime_world_id,
            room_id=run.room_id,
            definition_id=definition_id,
            has_candidate_predicate=where not in (None, {}, []),
            action_label=action_label,
        )
    candidates = (
        Mob.objects.select_for_update(of=("self",))
        .select_related(
            "definition",
            "character_state_record",
            "world",
            "world__context",
            "world__context__instance_of",
        )
        .filter(
            pk__in=candidate_ids,
            world_id=run.runtime_world_id,
            room_id=run.room_id,
            definition_id=definition_id,
            is_pending_deletion=False,
        )
        .order_by("id")
    )
    matches: list[Mob] = []
    invariant_state_cache: dict[str, dict[str, Any]] = {}
    for candidate in candidates.iterator(chunk_size=32):
        if where not in (None, {}, []) and not _matches_set_mob_where(
            where,
            mob=candidate,
            trigger_actor=trigger_actor,
            room=room,
            runtime_world=runtime_world,
            invariant_state_cache=invariant_state_cache,
        ):
            continue
        matches.append(candidate)
        if len(matches) == 2:
            break

    if not matches:
        raise TriggerStepExecutionError(
            "No matching mob is available in the trigger room.",
            code=not_found_code,
        )
    if len(matches) > 1:
        raise TriggerStepExecutionError(
            f"More than one mob matches the {action_display} action.",
            code=ambiguous_code,
        )
    return matches[0], invariant_state_cache


def _resolve_command_subject(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    room: Room,
    runtime_world: World,
    trigger_actor: Player | Mob | None,
    candidate_ids: tuple[int, ...] | None = None,
) -> Player | Mob | Room:
    subject = action.get("subject")
    if subject == TRIGGER_ROOM_REF:
        return room
    if subject == TRIGGER_ACTOR_REF:
        if trigger_actor is None:
            raise TriggerStepExecutionError(
                "The trigger actor is no longer available.",
                code="command_subject_unavailable",
            )
        if isinstance(trigger_actor, Player) and not trigger_actor.in_game:
            raise TriggerStepExecutionError(
                "The trigger player is no longer in the game.",
                code="command_subject_unavailable",
            )
        return trigger_actor
    if not isinstance(subject, dict) or subject.get("type") != "mob":
        raise TriggerStepExecutionError(
            "The command action has an invalid subject.",
            code="invalid_command_subject",
        )

    mob, invariant_state_cache = _select_step_mob(
        run=run,
        selector=subject,
        room=room,
        runtime_world=runtime_world,
        trigger_actor=trigger_actor,
        candidate_ids=candidate_ids,
        action_label="command_subject",
        not_found_code="command_subject_not_found",
        ambiguous_code="command_subject_ambiguous",
    )
    where = subject.get("where")
    if where in (None, {}, []):
        return mob

    state_record, state_record_created = _lock_or_create_mob_state(mob)
    locked_state = dict(state_record.data or {})
    matches = _matches_set_mob_where(
        where,
        mob=mob,
        trigger_actor=trigger_actor,
        room=room,
        runtime_world=runtime_world,
        state_snapshot=locked_state,
        invariant_state_cache=invariant_state_cache,
    )
    if state_record_created and not state_record.data:
        state_record.delete()
    if not matches:
        raise TriggerStepExecutionError(
            "No matching mob is available in the trigger room.",
            code="command_subject_not_found",
        )
    return mob


def _set_mob(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    room: Room,
    runtime_world: World,
    trigger_actor: Player | Mob | None,
    candidate_ids: tuple[int, ...] | None = None,
) -> Mob:
    mob, invariant_state_cache = _select_step_mob(
        run=run,
        selector=action,
        room=room,
        runtime_world=runtime_world,
        trigger_actor=trigger_actor,
        candidate_ids=candidate_ids,
        action_label="set_mob",
        not_found_code="set_mob_not_found",
        ambiguous_code="set_mob_ambiguous",
    )
    where = action.get("where")
    state_record, state_record_created = _lock_or_create_mob_state(mob)
    locked_state = dict(state_record.data or {})
    if where not in (None, {}, []) and not _matches_set_mob_where(
        where,
        mob=mob,
        trigger_actor=trigger_actor,
        room=room,
        runtime_world=runtime_world,
        state_snapshot=locked_state,
        invariant_state_cache=invariant_state_cache,
    ):
        raise TriggerStepExecutionError(
            "No matching mob is available in the trigger room.",
            code="set_mob_not_found",
        )

    fields = action.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise TriggerStepExecutionError(
            "The set_mob action has no fields to update.",
            code="invalid_set_mob_fields",
        )
    update_fields: list[str] = []
    for field_name, value in fields.items():
        if field_name not in {
            "name",
            "room_description",
            "description",
            "attackable",
        }:
            raise TriggerStepExecutionError(
                f"The set_mob field '{field_name}' is unsupported.",
                code="invalid_set_mob_field",
            )
        setattr(mob, field_name, value)
        update_fields.append(field_name)
    if update_fields:
        mob.save(update_fields=[*update_fields, "modified_ts"])

    state = action.get("state")
    if state is not None:
        try:
            normalized_state = normalize_state_snapshot(
                state,
                field_name="set_mob.state",
            )
        except ValueError as exc:
            raise TriggerStepExecutionError(
                str(exc),
                code="invalid_set_mob_state",
            ) from exc
        if normalized_state:
            state_record.data = normalize_state_snapshot(
                {
                    **locked_state,
                    **normalized_state,
                },
                field_name="character.state",
            )
            state_record.version = int(state_record.version or 0) + 1
            state_record.save(
                update_fields=["data", "version", "modified_ts"],
            )
    if state_record_created and not state_record.data:
        # Character-state rows are deliberately sparse. The temporary row
        # exists only to serialize predicate rechecks against concurrent state
        # writers; remove it when this action did not persist any state.
        state_record.delete()

    return mob


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
    from spawns.state_payloads import serialize_inventory

    added_payloads = [
        payload.model_dump()
        for payload in serialize_inventory(
            changes.room_items_added.values(),
            viewer=None,
        )
    ]
    actor_added_payloads = [
        payload.model_dump()
        for payload in serialize_inventory(
            changes.actor_inventory_added.values(),
            viewer=None,
        )
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


def _mob_change_events(
    *,
    run: ScheduledTriggerRun,
    room: Room,
    changes: TriggerMobChanges,
    room_recipient_keys: tuple[str, ...],
) -> list[GameEvent]:
    if not changes.changed or not room_recipient_keys:
        return []

    from spawns.state_payloads import room_payload_key_for, safe_capitalize

    mobs: list[dict[str, Any]] = []
    for change in changes.updated.values():
        mob = change.mob
        definition = mob.definition
        name = (
            mob.name
            or (definition.name if definition else "")
            or "Unnamed Mob"
        )
        field_values = {
            "name": name,
            "room_description": safe_capitalize(
                mob.room_description
                or (definition.room_description if definition else "")
                or f"{name} is here."
            ),
            "description": (
                mob.description
                or (definition.description if definition else None)
            ),
            "attackable": mob.attackable,
        }
        mobs.append({
            "key": mob.key,
            **{
                field_name: field_values[field_name]
                for field_name in sorted(change.fields)
            },
        })
    return [
        GameEvent(
            type="notification.trigger.mobs_changed",
            recipients=room_recipient_keys,
            data={
                "room": {
                    "id": room.id,
                    "key": room_payload_key_for(room),
                },
                "mobs": mobs,
                "actor": {
                    "key": run.actor_key,
                },
            },
        ),
    ]


def _debit_deltas(
    debit_actions: list[dict[str, Any]],
) -> dict[int, int]:
    deltas: dict[int, int] = {}
    for action in debit_actions:
        currency_id = int(action["currency_id"])
        deltas[currency_id] = (
            deltas.get(currency_id, 0) - int(action["amount"])
        )
    return deltas


def _preflight_step_currency_debits(
    *,
    debit_actions: list[dict[str, Any]],
    trigger_actor: Player,
) -> None:
    """Validate aggregate funds without taking balance-row locks.

    The Trigger step already owns the Player row, and all wallet writers lock
    Player before balances. That makes this read stable while preserving
    balance rows as the final aggregate lock acquired by the step.
    """
    deltas = _debit_deltas(debit_actions)
    balances = dict(
        PlayerCurrencyBalance.objects.filter(
            player_id=trigger_actor.id,
            currency_id__in=sorted(deltas),
        ).values_list("currency_id", "amount")
    )
    if any(
        int(balances.get(currency_id, 0)) + delta < 0
        for currency_id, delta in deltas.items()
    ):
        raise TriggerStepExecutionError(
            "Insufficient funds.",
            code="insufficient_funds",
        )


def _debit_step_currencies(
    *,
    debit_actions: list[dict[str, Any]],
    trigger_actor: Player,
) -> tuple[WalletMutation, dict[int, Currency]]:
    deltas = _debit_deltas(debit_actions)

    try:
        mutation = mutate_balances(
            trigger_actor,
            deltas,
            reason="trigger.debit_currency",
            emit_event=False,
        )
    except WalletError as exc:
        raise TriggerStepExecutionError(
            str(exc),
            code=exc.code,
        ) from exc
    return (
        mutation,
        {
            change.currency.id: change.currency
            for change in mutation.changes
        },
    )


def _wallet_balance_event(mutation: WalletMutation) -> GameEvent:
    return GameEvent(
        type="currency.balances_changed",
        recipients=[mutation.player.key],
        data=mutation.payload(reason="trigger.debit_currency"),
    )


def _currency_debit_events(
    *,
    run: ScheduledTriggerRun,
    action: dict[str, Any],
    currency: Currency,
    actor: Player,
    room: Room,
    room_recipient_keys: tuple[str, ...],
) -> list[GameEvent]:
    amount = int(action["amount"])
    display = format_currency(amount, currency)
    amount_prefix = f"{amount} "
    currency_label = display[len(amount_prefix):]
    if (
        currency_label
        and " " not in currency_label
        and not currency_label.isupper()
    ):
        # Catalog labels are commonly title-cased for standalone wallet UI.
        # A one-word unit is a common noun in this sentence; preserve acronyms
        # and authored multi-word capitalization.
        display = (
            f"{amount_prefix}"
            f"{currency_label[:1].lower()}{currency_label[1:]}"
        )
    actor_name = actor.name or "Someone"
    data = {
        "actor": {
            "key": actor.key,
            "name": actor_name,
            "char_type": "player",
        },
        "room": {
            "id": room.id,
            "key": room.key,
            "name": room.name or "",
        },
        "money": money_payload(amount, currency),
    }
    events = [
        GameEvent(
            type="notification.trigger.currency_debited",
            recipients=[run.actor_key],
            data={**data, "perspective": "actor"},
            text=f"You part with {display}.",
        )
    ]
    observer_recipients = tuple(
        recipient
        for recipient in room_recipient_keys
        if recipient != run.actor_key
    )
    if observer_recipients and actor.in_game and not actor.is_invisible:
        from spawns.state_payloads import safe_capitalize

        events.append(
            GameEvent(
                type="notification.trigger.currency_debited",
                recipients=observer_recipients,
                data={**data, "perspective": "room"},
                text=(
                    f"{safe_capitalize(actor_name)} parts with {display}."
                ),
            )
        )
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


def _room_recipient_keys_for(
    *,
    runtime_world_id: int,
    room_id: int,
) -> tuple[str, ...]:
    return tuple(
        f"player.{player_id}"
        for player_id in Player.objects.filter(
            world_id=runtime_world_id,
            room_id=room_id,
            in_game=True,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def _room_recipient_keys(run: ScheduledTriggerRun) -> tuple[str, ...]:
    return _room_recipient_keys_for(
        runtime_world_id=run.runtime_world_id,
        room_id=run.room_id,
    )


def _execute_current_step(
    run: ScheduledTriggerRun,
    *,
    runtime_world: World | None = None,
    room: Room | None = None,
    trigger_actor: Player | Mob | None = None,
    prelocks: TriggerStepPrelocks | None = None,
    resources_prelocked: bool = False,
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
        room = Room.objects.select_related("zone").get(pk=run.room_id)
    definition_world_id = _definition_world_id(runtime_world)
    definitions = _step_definitions(step, authored_world_id=definition_world_id)
    bindings = deepcopy(run.bindings or {})
    events: list[GameEvent] = []
    item_changes = TriggerItemChanges()
    mob_changes = TriggerMobChanges()
    actions = step.get("actions") or []
    debit_actions = [
        action
        for action in actions
        if action.get("type") == TRIGGER_STEP_ACTION_DEBIT_CURRENCY
    ]
    command_actions = [
        action
        for action in actions
        if action.get("type") == TRIGGER_STEP_ACTION_COMMAND
    ]
    if debit_actions:
        if run.actor_type != "player":
            raise TriggerStepExecutionError(
                "Only a player trigger actor can be charged currency.",
                code="invalid_actor",
            )
    actor_row_actions = {
        TRIGGER_STEP_ACTION_COMMAND,
        TRIGGER_STEP_ACTION_CONSUME_ITEM,
        TRIGGER_STEP_ACTION_GRANT_ITEM,
        TRIGGER_STEP_ACTION_DEBIT_CURRENCY,
    }
    if (
        run.actor_type == "player"
        and any(action.get("type") in actor_row_actions for action in actions)
    ):
        if (
            not isinstance(trigger_actor, Player)
            or trigger_actor.id != run.actor_id
            or trigger_actor.world_id != run.runtime_world_id
        ):
            trigger_actor = (
                Player.objects.select_for_update()
                .filter(
                    pk=run.actor_id,
                    world_id=run.runtime_world_id,
                )
                .first()
            )
        if trigger_actor is None:
            raise TriggerStepExecutionError(
                "The trigger actor is no longer available.",
                code="actor_missing",
            )
    if not resources_prelocked:
        prelocks = _prelock_step_resources(
            run=run,
            actions=actions,
            bindings=bindings,
        )
    if command_actions and trigger_actor is None:
        trigger_actor = _load_trigger_actor(run)
    if command_actions and trigger_actor is None:
        raise TriggerStepExecutionError(
            "The trigger actor is no longer available.",
            code="actor_missing",
        )
    if debit_actions:
        _preflight_step_currency_debits(
            debit_actions=debit_actions,
            trigger_actor=trigger_actor,
        )

    for action in actions:
        action_type = action.get("type")
        if action_type == TRIGGER_STEP_ACTION_DEBIT_CURRENCY:
            continue
        elif action_type == TRIGGER_STEP_ACTION_COMMAND:
            continue
        elif action_type == TRIGGER_STEP_ACTION_CONSUME_ITEM:
            definition_id = int(action["item_definition_id"])
            candidate_ids = None
            if prelocks is not None:
                candidate_ids = (
                    prelocks.actor_item_ids_by_definition.get(
                        definition_id,
                        (),
                    )
                    + tuple(
                        item.id
                        for item in item_changes.actor_inventory_added.values()
                        if item.definition_id == definition_id
                    )
                )
            for removed_id, removed_key in _consume_item(
                run=run,
                action=action,
                definition=definitions[definition_id],
                candidate_ids=candidate_ids,
            ):
                if item_changes.actor_inventory_added.pop(removed_id, None) is None:
                    item_changes.actor_inventory_removed.append({"key": removed_key})
        elif action_type == TRIGGER_STEP_ACTION_CONSUME_ROOM_ITEM:
            definition_id = int(action["item_definition_id"])
            candidate_ids = None
            if prelocks is not None:
                candidate_ids = (
                    prelocks.room_item_ids_by_definition.get(
                        definition_id,
                        (),
                    )
                    + tuple(
                        item.id
                        for item in item_changes.room_items_added.values()
                        if item.definition_id == definition_id
                    )
                )
            for removed_id, removed_key in _consume_room_item(
                run=run,
                action=action,
                definition=definitions[definition_id],
                candidate_ids=candidate_ids,
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
        elif action_type == TRIGGER_STEP_ACTION_SET_MOB:
            if trigger_actor is None:
                trigger_actor = _load_trigger_actor(run)
            updated_mob = _set_mob(
                run=run,
                action=action,
                room=room,
                runtime_world=runtime_world,
                trigger_actor=trigger_actor,
                candidate_ids=(
                    None
                    if prelocks is None
                    else prelocks.mob_ids_by_definition.get(
                        int(action["mob_definition_id"]),
                        (),
                    )
                ),
            )
            change = mob_changes.updated.setdefault(
                updated_mob.id,
                TriggerMobChange(mob=updated_mob),
            )
            change.mob = updated_mob
            change.fields.update(action["fields"])
        elif action_type == TRIGGER_STEP_ACTION_ECHO:
            continue
        else:
            raise TriggerStepExecutionError(
                f"Unsupported scheduled trigger action '{action_type}'.",
                code="unsupported_action",
            )

    # Typed item and mob mutations are an authored prefix. Publish their
    # deltas before any command can emit a full character/room snapshot.
    mutation_room_recipient_keys = _room_recipient_keys(run)
    events.extend(
        _item_change_events(
            run=run,
            room=room,
            changes=item_changes,
            room_recipient_keys=mutation_room_recipient_keys,
        )
    )
    events.extend(
        _mob_change_events(
            run=run,
            room=room,
            changes=mob_changes,
            room_recipient_keys=mutation_room_recipient_keys,
        )
    )

    action_events: dict[int, list[GameEvent]] = {}
    debit_contexts: dict[int, tuple[Room, tuple[str, ...]]] = {}
    trigger_room_recipient_keys: tuple[str, ...] | None = (
        mutation_room_recipient_keys
    )
    debit_context_cache: dict[
        tuple[int | None, bool, bool],
        tuple[Room, tuple[str, ...]],
    ] = {}
    trigger_provenance = bindings.get(
        _TRIGGER_PROVENANCE_BINDING_KEY,
        {},
    )
    if not isinstance(trigger_provenance, dict):
        trigger_provenance = {}

    for action_index, action in enumerate(actions):
        action_type = action.get("type")
        if action_type == TRIGGER_STEP_ACTION_DEBIT_CURRENCY:
            if not isinstance(trigger_actor, Player):
                raise TriggerStepExecutionError(
                    "Only a player trigger actor can be charged currency.",
                    code="invalid_actor",
                )
            context_key = (
                trigger_actor.room_id,
                bool(trigger_actor.in_game),
                bool(trigger_actor.is_invisible),
            )
            debit_context = debit_context_cache.get(context_key)
            if debit_context is None:
                debit_room = None
                if trigger_actor.room_id == room.id:
                    debit_room = room
                elif trigger_actor.room_id is not None:
                    debit_room = Room.objects.filter(
                        pk=trigger_actor.room_id,
                    ).first()
                debit_room = debit_room or room
                debit_recipients: tuple[str, ...] = ()
                if (
                    trigger_actor.room_id is not None
                    and trigger_actor.in_game
                    and not trigger_actor.is_invisible
                ):
                    debit_recipients = _room_recipient_keys_for(
                        runtime_world_id=run.runtime_world_id,
                        room_id=debit_room.id,
                    )
                debit_context = (debit_room, debit_recipients)
                debit_context_cache[context_key] = debit_context
            debit_contexts[action_index] = debit_context
        elif action_type == TRIGGER_STEP_ACTION_COMMAND:
            command_subject = _command_mob_subject(action)
            candidate_ids = None
            if command_subject is not None and prelocks is not None:
                candidate_ids = prelocks.mob_ids_by_definition.get(
                    int(command_subject["mob_definition_id"]),
                    (),
                )
            subject = _resolve_command_subject(
                run=run,
                action=action,
                room=room,
                runtime_world=runtime_world,
                trigger_actor=trigger_actor,
                candidate_ids=candidate_ids,
            )
            try:
                command_result = ScriptCommandRunner().execute(
                    issuer=room,
                    subject=subject,
                    command=str(action.get("command") or ""),
                    render_actor=trigger_actor,
                    runtime_world=runtime_world,
                    provenance={
                        "trigger_id": (
                            trigger_provenance.get("id")
                            or run.trigger_id
                        ),
                        "trigger_key": trigger_provenance.get("key"),
                        "run_id": run.id,
                        "step_index": run.next_step_index,
                        "action_index": action_index,
                        "command_depth": bindings.get(
                            SCRIPT_COMMAND_DEPTH_KEY,
                            0,
                        ),
                    },
                )
            except ScriptCommandError as exc:
                raise TriggerStepExecutionError(
                    str(exc),
                    code=exc.code,
                ) from exc
            action_events[action_index] = list(command_result.events)
            if command_result.mode == TRIGGER_STEP_MODE_TRANSACTIONAL:
                # The initial transactional command contract can move only
                # the Trigger actor. Refresh it before later command subjects,
                # debit witness snapshots, or room echoes are resolved.
                trigger_actor.refresh_from_db()
                trigger_room_recipient_keys = None
                debit_context_cache.clear()
        elif action_type == TRIGGER_STEP_ACTION_ECHO:
            if trigger_room_recipient_keys is None:
                trigger_room_recipient_keys = _room_recipient_keys(run)
            event = _echo_event(
                run=run,
                action=action,
                room=room,
                room_recipient_keys=trigger_room_recipient_keys,
            )
            action_events[action_index] = [] if event is None else [event]

    wallet_mutation: WalletMutation | None = None
    debited_currencies: dict[int, Currency] = {}
    if debit_actions:
        # Commands have finished and all of their output remains captured.
        # Acquire ordered balance rows last, write the complete debit batch
        # once, and let any later failure roll back every command and mutation.
        wallet_mutation, debited_currencies = _debit_step_currencies(
            debit_actions=debit_actions,
            trigger_actor=trigger_actor,
        )

    for action_index, action in enumerate(actions):
        action_type = action.get("type")
        if action_type == TRIGGER_STEP_ACTION_DEBIT_CURRENCY:
            debit_room, debit_recipients = debit_contexts[action_index]
            events.extend(
                _currency_debit_events(
                    run=run,
                    action=action,
                    currency=debited_currencies[int(action["currency_id"])],
                    actor=wallet_mutation.player,
                    room=debit_room,
                    room_recipient_keys=debit_recipients,
                )
            )
        elif action_type in {
            TRIGGER_STEP_ACTION_COMMAND,
            TRIGGER_STEP_ACTION_ECHO,
        }:
            events.extend(action_events.get(action_index, ()))

    if wallet_mutation is not None:
        # A transfer event contains a full pre-debit character snapshot. Keep
        # the authoritative aggregate wallet state sync last in the batch so
        # clients cannot overwrite the final balance with that snapshot.
        events.append(_wallet_balance_event(wallet_mutation))

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
    gate_claim_collector: list[tuple[str, str]] | None = None,
    request_id: uuid.UUID | str | None = None,
    request_segment: str = "r",
    request_connection_id: str | None = None,
    emit_acceptance: bool = True,
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
        parsed_request_id = _request_uuid(request_id)
        normalized_request_segment = normalize_request_segment(
            request_segment
        )
        with transaction.atomic():
            lock_trigger_runtime_room(
                runtime_world_id=runtime_world_id,
                room_id=room.id,
            )
            expected_actor_room_id = _expected_actor_room_id(
                trigger_room_id=room.id,
                event_data=event_data,
            )
            locked_actor = None
            if actor_model is Player:
                locked_actor = (
                    Player.objects.select_for_update()
                    .get(pk=actor.id)
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

            snapshot_steps = _snapshot_steps_with_definition_ids(
                normalized_steps,
                authored_world_id=definition_world_id,
            )
            first_step_delay_seconds = int(
                snapshot_steps[0]["due_after_seconds"]
            )
            command_depth = _script_command_depth(event_data)
            initial_bindings = {
                _TRIGGER_PROVENANCE_BINDING_KEY: {
                    "id": current_trigger.id,
                    "key": current_trigger.key,
                },
            }
            if command_depth:
                initial_bindings[SCRIPT_COMMAND_DEPTH_KEY] = command_depth
            initial_prelocks = None
            resources_prelocked = False
            initial_mob_prelocks: dict[int, tuple[int, ...]] = {}
            prelock_context = None
            if actor_model is Mob:
                if first_step_delay_seconds == 0:
                    prelock_context = ScheduledTriggerRun(
                        runtime_world_id=runtime_world_id,
                        room_id=room.id,
                        actor_type=actor_type,
                        actor_id=actor.id,
                    )
                    initial_mob_prelocks = _prelock_step_mobs(
                        run=prelock_context,
                        actions=snapshot_steps[0].get("actions") or [],
                        include_mob_actor=True,
                    )
                locked_actor = (
                    Mob.objects.select_for_update(of=("self",))
                    .get(
                        pk=actor.id,
                        world_id=runtime_world_id,
                        is_pending_deletion=False,
                    )
                )

            if (
                locked_actor.world_id != runtime_world_id
                or locked_actor.room_id != expected_actor_room_id
            ):
                raise TriggerStepExecutionError(
                    "The trigger actor is no longer in the triggering room.",
                    code="context_changed",
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
            if gate_claim is not None and gate_claim_collector is not None:
                gate_claim_collector.append(gate_claim)
            try:
                if prelock_context is not None:
                    initial_prelocks = _prelock_step_resources(
                        run=prelock_context,
                        actions=snapshot_steps[0].get("actions") or [],
                        bindings=initial_bindings,
                        include_mob_actor=True,
                        prelocked_mob_ids_by_definition=(
                            initial_mob_prelocks
                        ),
                        mob_rows_prelocked=True,
                    )
                    resources_prelocked = True
                started_ts = timezone.now()
                run = ScheduledTriggerRun.objects.create(
                    trigger=current_trigger,
                    runtime_world_id=runtime_world_id,
                    room_id=room.id,
                    actor_type=actor_type,
                    actor_id=locked_actor.id,
                    actor_key=locked_actor.key,
                    request_id=(
                        parsed_request_id
                        if actor_type == "player"
                        else None
                    ),
                    request_segment=normalized_request_segment,
                    request_connection_id=(
                        request_connection_id
                        if (
                            actor_type == "player"
                            and parsed_request_id
                            and emit_acceptance
                        )
                        else None
                    ),
                    steps=snapshot_steps,
                    bindings=initial_bindings,
                    next_step_index=0,
                    next_run_ts=(
                        started_ts
                        + timedelta(seconds=first_step_delay_seconds)
                    ),
                    started_ts=started_ts,
                    status=ScheduledTriggerRun.STATUS_ACTIVE,
                    on_step_error=error_policy,
                )
                events: list[GameEvent] = []
                if emit_acceptance:
                    accepted_event = _trigger_accepted_event(
                        run,
                        first_step_delay_seconds=(
                            first_step_delay_seconds
                        ),
                    )
                    if accepted_event is not None:
                        events.append(accepted_event)
                if first_step_delay_seconds == 0:
                    events.extend(
                        _execute_current_step(
                            run,
                            runtime_world=runtime_world,
                            room=room,
                            trigger_actor=locked_actor,
                            prelocks=initial_prelocks,
                            resources_prelocked=resources_prelocked,
                        )
                    )
                    if (
                        run.status
                        == ScheduledTriggerRun.STATUS_COMPLETED
                    ):
                        completed_event = _trigger_completed_event(run)
                        if completed_event is not None:
                            events.append(completed_event)
                queued_event_count = enqueue_game_events(events)
                if queued_event_count:
                    transaction.on_commit(_flush_queued_events, robust=True)
                _schedule_trigger_step_continuation(
                    run,
                    due_at=timezone.now(),
                )
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


def _advance_one_due_run(
    *,
    due_at,
    run_id: int | None = None,
    expected_step_index: int | None = None,
) -> TriggerStepAdvanceResult | None:
    with transaction.atomic():
        runs = (
            ScheduledTriggerRun.objects.select_related(
                "runtime_world__context__instance_of",
                "room",
                "room__zone",
            )
            .select_for_update(skip_locked=True, of=("self",))
            .filter(
                status=ScheduledTriggerRun.STATUS_ACTIVE,
                next_run_ts__lte=due_at,
            )
        )
        if run_id is not None:
            runs = runs.filter(
                pk=run_id,
                next_step_index=expected_step_index,
            )
        run = runs.order_by("next_run_ts", "id").first()
        if run is None:
            return None

        try:
            with transaction.atomic():
                events = _execute_current_step(
                    run,
                    runtime_world=run.runtime_world,
                    room=run.room,
                )
                if run.status == ScheduledTriggerRun.STATUS_COMPLETED:
                    completed_event = _trigger_completed_event(run)
                    if completed_event is not None:
                        events.append(completed_event)
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
            enqueue_game_events(_trigger_cancelled_events(run))
            return TriggerStepAdvanceResult(
                status=ScheduledTriggerRun.STATUS_CANCELLED,
                run_id=run.id,
            )
        return TriggerStepAdvanceResult(
            status=run.status,
            run_id=run.id,
            continuation=_due_run_continuation(
                run,
                due_at=due_at,
            ),
        )


def advance_due_trigger_run(
    *,
    run_id: int,
    expected_step_index: int,
    now=None,
) -> str | None:
    """Advance one expected due step for one run.

    The expected cursor makes duplicate task deliveries idempotent. A
    concurrent worker that wins the row lock advances the cursor, so every
    stale duplicate becomes a no-op instead of executing the following step.
    """
    advance = _advance_one_due_run(
        due_at=now or timezone.now(),
        run_id=run_id,
        expected_step_index=expected_step_index,
    )
    if advance is None:
        return None

    transaction.on_commit(_flush_queued_events, robust=True)
    if advance.continuation is not None:
        transaction.on_commit(
            lambda: _enqueue_trigger_step_continuation(
                advance.continuation
            ),
            robust=True,
        )
    return advance.status


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
    continuations: dict[int, TriggerStepContinuation] = {}
    for _ in range(row_limit):
        advance = _advance_one_due_run(due_at=due_at)
        if advance is None:
            break
        status = advance.status
        result["processed"] += 1
        if status == ScheduledTriggerRun.STATUS_COMPLETED:
            result["completed"] += 1
        elif status == ScheduledTriggerRun.STATUS_CANCELLED:
            result["cancelled"] += 1
        continuations.pop(advance.run_id, None)
        if advance.continuation is not None:
            continuations[advance.run_id] = advance.continuation

    if result["processed"]:
        transaction.on_commit(_flush_queued_events, robust=True)
        for continuation in continuations.values():
            transaction.on_commit(
                lambda continuation=continuation: (
                    _enqueue_trigger_step_continuation(continuation)
                ),
                robust=True,
            )
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
