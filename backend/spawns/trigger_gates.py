"""Trigger gates whose durations follow their runtime world's gameplay clock."""

from datetime import timedelta
import hashlib
import uuid

from django.core.cache import cache
from django.db import transaction

from spawns.instance_clock import gameplay_now, time_control_run
from spawns.models import InstanceClockWork


CONTROLLED_GATE_PREFIX = "trigger_gate:"


def _controlled_gate_key(gate_key):
    return CONTROLLED_GATE_PREFIX + hashlib.sha256(gate_key.encode()).hexdigest()


def _controlled_scope_world_id(scope_key):
    parts = str(scope_key).split(":", 2)
    if len(parts) < 2 or parts[0] != "runtime":
        return None
    try:
        world_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    return world_id if time_control_run(world_id) else None


def gate_is_allowed(gate_key, scope_key):
    world_id = _controlled_scope_world_id(scope_key)
    if world_id is None:
        return not bool(cache.get(gate_key))
    gate = InstanceClockWork.objects.filter(
        dedupe_key=_controlled_gate_key(gate_key), kind="trigger_gate",
    ).values("due_at").first()
    return gate is None or (
        gate["due_at"] is not None and gameplay_now(world_id) >= gate["due_at"]
    )


def claim_gate(gate_key, scope_key, delay):
    """Claim transactionally for controlled runs; retain live cache.add behavior.

    Gate claims and renewals must roll back with the entire simulation turn.
    Durable rows also survive cache eviction and expire on gameplay time. A
    unique key plus row lock protects claims even outside a simulation scope.
    """
    token = uuid.uuid4().hex
    world_id = _controlled_scope_world_id(scope_key)
    if world_id is not None:
        storage_key = _controlled_gate_key(gate_key)
        now = gameplay_now(world_id)
        deadline = None if delay < 0 else now + timedelta(seconds=delay)
        with transaction.atomic():
            gate, created = InstanceClockWork.objects.get_or_create(
                dedupe_key=storage_key,
                defaults={"world_id": world_id, "kind": "trigger_gate",
                          "payload": {"token": token}, "due_at": deadline},
            )
            if not created:
                gate = InstanceClockWork.objects.select_for_update().get(pk=gate.pk)
                if gate.due_at is None or gate.due_at > now:
                    return None
                gate.due_at = deadline
                gate.payload = {"token": token}
                gate.save(update_fields=["due_at", "payload"])
        return storage_key, token
    elif not cache.add(gate_key, token, timeout=None if delay < 0 else delay):
        return None
    return gate_key, token


def release_gate(claim):
    if claim is None:
        return
    gate_key, token = claim
    if gate_key.startswith(CONTROLLED_GATE_PREFIX):
        InstanceClockWork.objects.filter(
            dedupe_key=gate_key, kind="trigger_gate", payload__token=token,
        ).delete()
        return
    value = cache.get(gate_key)
    current_token = value.get("token") if isinstance(value, dict) else value
    if current_token == token:
        cache.delete(gate_key)
