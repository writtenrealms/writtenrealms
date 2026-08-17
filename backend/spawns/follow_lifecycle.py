"""Lifecycle cleanup for player movement-follow relationships."""

from collections.abc import Iterable
import hashlib

from django.db import connection
from django.db.models import Q

from spawns.models import MovementFollow


def _movement_follow_graph_lock_key(runtime_world_id: int) -> int:
    digest = hashlib.blake2b(
        f"spawns.movement_follow:{runtime_world_id}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def lock_movement_follow_graph(runtime_world_id: int) -> None:
    """Serialize registrations and bulk teardown for one runtime world."""
    normalized_world_id = int(runtime_world_id)
    if normalized_world_id <= 0:
        raise ValueError("A runtime world is required for a follow graph lock.")
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Movement-follow graph locks require an active transaction."
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [_movement_follow_graph_lock_key(normalized_world_id)],
        )


def clear_movement_follows_for_players(player_ids: Iterable[int]) -> int:
    """Delete outgoing and player-led follow edges for the supplied players."""
    normalized_ids = set()
    for player_id in player_ids:
        if player_id is None:
            continue
        normalized_id = int(player_id)
        if normalized_id > 0:
            normalized_ids.add(normalized_id)
    if not normalized_ids:
        return 0

    deleted, _details = MovementFollow.objects.filter(
        Q(follower_id__in=normalized_ids)
        | Q(leader_player_id__in=normalized_ids)
    ).delete()
    return deleted


def clear_movement_follows_for_world(runtime_world_id: int) -> int:
    """Delete every edge involving a player in one runtime world."""
    normalized_world_id = int(runtime_world_id)
    if normalized_world_id <= 0:
        return 0

    deleted, _details = MovementFollow.objects.filter(
        Q(follower__world_id=normalized_world_id)
        | Q(leader_player__world_id=normalized_world_id)
    ).delete()
    return deleted
