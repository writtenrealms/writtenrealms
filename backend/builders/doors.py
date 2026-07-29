from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from builders.models import ItemDefinition
from config import constants as adv_consts
from worlds.models import Door, Doorway, Room


@dataclass(frozen=True)
class DoorFaceSpec:
    direction: str
    name: str
    to_room: Room
    key_id: int | None
    destroy_key: bool
    default_state: str


def _mutual_reverse_room(room: Room, *, direction: str, to_room: Room) -> bool:
    reverse_direction = adv_consts.REVERSE_DIRECTIONS[direction]
    return getattr(to_room, f"{reverse_direction}_id", None) == room.id


def _reverse_face(
    *,
    room: Room,
    direction: str,
    to_room: Room,
) -> Door | None:
    if not _mutual_reverse_room(room, direction=direction, to_room=to_room):
        return None
    return (
        Door.objects.select_related("doorway")
        .filter(
            from_room=to_room,
            to_room=room,
            direction=adv_consts.REVERSE_DIRECTIONS[direction],
        )
        .first()
    )


def _delete_orphan_doorways(doorway_ids: Iterable[int]) -> None:
    ids = {int(doorway_id) for doorway_id in doorway_ids if doorway_id}
    if not ids:
        return
    Doorway.objects.filter(id__in=ids, faces__isnull=True).delete()


@transaction.atomic
def delete_door_faces(queryset) -> int:
    """Delete an explicitly scoped face queryset and its orphan aggregates."""
    doorway_ids = list(
        queryset.order_by("doorway_id")
        .values_list("doorway_id", flat=True)
        .distinct()
    )
    deleted, _ = queryset.delete()
    _delete_orphan_doorways(doorway_ids)
    return deleted


def _configure_doorway(
    doorway: Doorway,
    *,
    key_id: int | None,
    destroy_key: bool,
    default_state: str,
) -> None:
    doorway.key_id = key_id
    doorway.destroy_key = bool(destroy_key)
    doorway.default_state = default_state
    doorway.save(update_fields=["key", "destroy_key", "default_state", "modified_ts"])


@transaction.atomic
def upsert_door_face(
    *,
    room: Room,
    spec: DoorFaceSpec,
    create_reverse: bool = False,
    reject_reverse_conflict: bool = False,
) -> tuple[Door, Door | None]:
    """Create or update one face while preserving a reciprocal doorway aggregate."""
    # Reciprocal edits take these two bounded locks in stable order so
    # concurrent builders cannot create two aggregates for the same doorway.
    list(
        Room.objects.select_for_update()
        .filter(pk__in=sorted((room.id, spec.to_room.id)))
        .order_by("id")
        .values_list("id", flat=True)
    )
    if room.world_id != spec.to_room.world_id:
        raise ValueError("Door faces cannot connect rooms from different worlds.")
    if room.id == spec.to_room.id:
        raise ValueError("A door cannot connect a room to itself.")
    if getattr(room, f"{spec.direction}_id", None) != spec.to_room.id:
        raise ValueError("The door destination must match the room exit.")
    if spec.key_id and not ItemDefinition.objects.filter(
        pk=spec.key_id,
        world_id=room.world_id,
    ).exists():
        raise ValueError("The door key must belong to the same authored world.")

    existing = (
        Door.objects.select_related("doorway")
        .filter(from_room=room, direction=spec.direction)
        .first()
    )
    orphan_candidates: set[int] = set()

    reverse = _reverse_face(
        room=room,
        direction=spec.direction,
        to_room=spec.to_room,
    )

    # Re-pointing one face of a reciprocal doorway must split the aggregate:
    # the old reverse face remains a valid authored one-way door, while the
    # moved face begins a distinct physical doorway. Preserve both face row
    # identities so room-exit edits do not silently erase builder content.
    if existing is not None and existing.to_room_id != spec.to_room.id:
        has_other_faces = Door.objects.filter(
            doorway_id=existing.doorway_id,
        ).exclude(pk=existing.pk).exists()
        if has_other_faces:
            existing.doorway = (
                reverse.doorway
                if reverse is not None
                else Doorway.objects.create(
                    world=room.world,
                    key_id=spec.key_id,
                    destroy_key=spec.destroy_key,
                    default_state=spec.default_state,
                )
            )
        existing.to_room = spec.to_room
        existing.save(update_fields=["doorway", "to_room", "modified_ts"])

    doorway = (
        existing.doorway
        if existing is not None
        else reverse.doorway
        if reverse is not None
        else Doorway.objects.create(
            world=room.world,
            key_id=spec.key_id,
            destroy_key=spec.destroy_key,
            default_state=spec.default_state,
        )
    )
    if doorway.world_id != room.world_id:
        raise ValueError("The doorway belongs to a different authored world.")

    if (
        existing is not None
        and reverse is not None
        and existing.doorway_id != reverse.doorway_id
    ):
        orphan_candidates.add(reverse.doorway_id)
        reverse.doorway = doorway
        reverse.save(update_fields=["doorway", "modified_ts"])

    if reject_reverse_conflict and reverse is not None:
        reverse_doorway = reverse.doorway
        requested_config = (
            spec.key_id,
            bool(spec.destroy_key),
            spec.default_state,
        )
        reverse_config = (
            reverse_doorway.key_id,
            bool(reverse_doorway.destroy_key),
            reverse_doorway.default_state,
        )
        if reverse_config != requested_config:
            raise ValueError(
                "Reciprocal door faces must use the same key, destroy_key, "
                "and default_state settings."
            )

    _configure_doorway(
        doorway,
        key_id=spec.key_id,
        destroy_key=spec.destroy_key,
        default_state=spec.default_state,
    )

    if existing is None:
        existing = Door.objects.create(
            doorway=doorway,
            from_room=room,
            to_room=spec.to_room,
            direction=spec.direction,
            name=spec.name or "door",
        )
    else:
        existing.to_room = spec.to_room
        existing.name = spec.name or "door"
        existing.save(update_fields=["to_room", "name", "modified_ts"])

    if create_reverse and _mutual_reverse_room(
        room,
        direction=spec.direction,
        to_room=spec.to_room,
    ):
        reverse_direction = adv_consts.REVERSE_DIRECTIONS[spec.direction]
        if reverse is None:
            reverse = Door.objects.create(
                doorway=doorway,
                from_room=spec.to_room,
                to_room=room,
                direction=reverse_direction,
                name=spec.name or "door",
            )
        elif reverse.doorway_id != doorway.id:
            orphan_candidates.add(reverse.doorway_id)
            reverse.doorway = doorway
            reverse.save(update_fields=["doorway", "modified_ts"])

    _delete_orphan_doorways(orphan_candidates)
    return existing, reverse


@transaction.atomic
def replace_room_door_faces(
    *,
    room: Room,
    specs: Iterable[DoorFaceSpec],
    reject_reverse_conflicts: bool = False,
) -> list[Door]:
    """Replace one room's face collection without deleting reciprocal faces."""
    desired = {spec.direction: spec for spec in specs}
    existing = {
        face.direction: face
        for face in Door.objects.select_related("doorway").filter(from_room=room)
    }
    orphan_candidates: set[int] = set()
    for direction, face in existing.items():
        if direction in desired:
            continue
        orphan_candidates.add(face.doorway_id)
        face.delete()

    faces: list[Door] = []
    for spec in desired.values():
        face, _ = upsert_door_face(
            room=room,
            spec=spec,
            reject_reverse_conflict=reject_reverse_conflicts,
        )
        faces.append(face)
    _delete_orphan_doorways(orphan_candidates)
    return faces


@transaction.atomic
def remove_door_face(
    *,
    room: Room,
    direction: str,
    remove_reverse: bool,
) -> tuple[Door | None, Door | None]:
    face = (
        Door.objects.select_related("doorway", "to_room")
        .filter(from_room=room, direction=direction)
        .first()
    )
    if face is None:
        return None, None

    doorway_id = face.doorway_id
    reverse = _reverse_face(
        room=room,
        direction=direction,
        to_room=face.to_room,
    )
    face.delete()
    if remove_reverse and reverse is not None:
        reverse.delete()
    _delete_orphan_doorways([doorway_id])
    return face, reverse if remove_reverse else None
