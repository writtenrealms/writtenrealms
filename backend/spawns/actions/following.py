from __future__ import annotations

from django.db import OperationalError, transaction

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import (
    find_room_char_target,
    room_char_matches_selector,
)
from spawns.events import GameEvent
from spawns.follow_lifecycle import lock_movement_follow_graph
from spawns.following import MAX_FOLLOW_PROPAGATION_DEPTH
from spawns.models import Mob, MovementFollow, Player


def _character_ref(character: Player | Mob) -> dict:
    name = str(character.name or "").strip()
    if not name and isinstance(character, Mob) and character.definition:
        name = str(character.definition.name or "").strip()
    return {
        "id": character.id,
        "key": character.key,
        "name": name or "someone",
        "type": "player" if isinstance(character, Player) else "mob",
    }


def _current_follow(player: Player, *, for_update: bool = False) -> MovementFollow | None:
    follows = MovementFollow.objects.select_related(
        "leader_player",
        "leader_mob",
        "leader_mob__definition",
    )
    if for_update:
        follows = follows.select_for_update(of=("self",))
    return follows.filter(follower_id=player.id).first()


def _ensure_follow_chain(
    *,
    follower: Player,
    leader: Player | Mob,
) -> None:
    upstream_depth = 1
    if isinstance(leader, Player):
        current_id: int | None = leader.id
        visited_upstream: set[int] = set()
        while current_id is not None:
            if current_id == follower.id or current_id in visited_upstream:
                raise ActionError(
                    "That would create a circle of followers.",
                    code="follow_cycle",
                )
            if upstream_depth > MAX_FOLLOW_PROPAGATION_DEPTH:
                raise ActionError(
                    "That following chain is too long.",
                    code="follow_chain_too_long",
                )
            visited_upstream.add(current_id)
            next_id = (
                MovementFollow.objects.filter(
                    follower_id=current_id,
                    leader_player_id__isnull=False,
                )
                .values_list("leader_player_id", flat=True)
                .first()
            )
            if next_id is None:
                break
            current_id = next_id
            upstream_depth += 1

    remaining_downstream_depth = MAX_FOLLOW_PROPAGATION_DEPTH - upstream_depth
    frontier = {follower.id}
    visited = {follower.id}
    for downstream_depth in range(1, remaining_downstream_depth + 2):
        next_ids = set(
            MovementFollow.objects.filter(
                leader_player_id__in=frontier,
            ).values_list("follower_id", flat=True)
        )
        if next_ids.intersection(visited):
            raise ActionError(
                "That would create a circle of followers.",
                code="follow_cycle",
            )
        next_ids.difference_update(visited)
        if not next_ids:
            return
        if downstream_depth > remaining_downstream_depth:
            raise ActionError(
                "That following chain is too long.",
                code="follow_chain_too_long",
            )
        visited.update(next_ids)
        frontier = next_ids


def _validate_locked_follower(
    player: Player,
    *,
    expected_world_id: int,
    expected_room_id: int | None,
) -> None:
    if (
        player.world_id != expected_world_id
        or player.room_id != expected_room_id
    ):
        raise ActionError(
            "Your location changed. Try following again.",
            code="context_changed",
        )
    if not player.in_game:
        raise ActionError(
            "You are not currently in the game.",
            code="not_in_game",
        )


def _validate_locked_target(
    *,
    follower: Player,
    target: Player | Mob,
) -> None:
    is_available = bool(
        target.world_id == follower.world_id
        and target.room_id == follower.room_id
        and target.room_id is not None
    )
    if isinstance(target, Player):
        is_available = bool(is_available and target.in_game)
    else:
        is_available = bool(
            is_available
            and not target.is_pending_deletion
            and int(target.health or 0) > 0
        )
    if not follower.is_builder and target.is_invisible:
        is_available = False
    if not is_available:
        raise ActionError(
            "They are no longer here.",
            code="target_changed",
        )


def _lock_follow_participants(
    *,
    follower_snapshot: Player,
    target_snapshot: Player | Mob,
) -> tuple[Player, Player | Mob]:
    """Lock the resolved actors and revalidate every selection predicate."""
    if isinstance(target_snapshot, Player):
        participant_ids = sorted({follower_snapshot.id, target_snapshot.id})
        locked_players = {
            player.id: player
            for player in (
                Player.objects.select_for_update(
                    of=("self",),
                    nowait=True,
                )
                .select_related("room", "world")
                .filter(pk__in=participant_ids)
                .order_by("pk")
            )
        }
        player = locked_players.get(follower_snapshot.id)
        target = locked_players.get(target_snapshot.id)
        if player is None:
            raise ActionError(
                "You are no longer available.",
                code="actor_changed",
            )
        if target is None:
            raise ActionError(
                "They are no longer here.",
                code="target_changed",
            )
    else:
        try:
            player = (
                Player.objects.select_for_update(
                    of=("self",),
                    nowait=True,
                )
                .select_related("room", "world")
                .get(pk=follower_snapshot.id)
            )
        except Player.DoesNotExist as exc:
            raise ActionError(
                "You are no longer available.",
                code="actor_changed",
            ) from exc
        try:
            target = (
                Mob.objects.select_for_update(
                    of=("self",),
                    nowait=True,
                )
                .select_related("definition")
                .get(pk=target_snapshot.id)
            )
        except Mob.DoesNotExist as exc:
            raise ActionError(
                "They are no longer here.",
                code="target_changed",
            ) from exc

    _validate_locked_follower(
        player,
        expected_world_id=follower_snapshot.world_id,
        expected_room_id=follower_snapshot.room_id,
    )
    _validate_locked_target(follower=player, target=target)
    return player, target


def _is_lock_not_available(error: OperationalError) -> bool:
    cause = error.__cause__
    return (
        getattr(cause, "pgcode", None) == "55P03"
        or getattr(cause, "sqlstate", None) == "55P03"
    )


def _follow_target(player: Player, selector: str | None) -> Player | Mob:
    normalized = str(selector or "").strip()
    if not normalized:
        raise ActionError("Follow whom?", code="missing_target")
    if not player.room_id or not player.room:
        raise ActionError(
            "You are nowhere. Cannot follow anyone.",
            code="no_room",
        )

    self_selectors = {
        "self",
        "me",
        str(player.key or "").casefold(),
    }
    if normalized.casefold() in self_selectors:
        raise ActionError(
            "You cannot follow yourself.",
            code="cannot_follow_self",
        )

    can_see_invisible = bool(player.is_builder)
    target = find_room_char_target(
        player.room,
        normalized,
        viewer=player,
        world=player.world,
        exclude=player,
        lean=True,
        include_invisible_players=can_see_invisible,
        include_invisible_mobs=can_see_invisible,
        require_unambiguous=True,
    )
    if target is None:
        raise ActionError(
            "You don't see them here.",
            code="target_not_found",
        )
    if target == player:
        raise ActionError(
            "You cannot follow yourself.",
            code="cannot_follow_self",
        )
    return target


def _leader_matches_selector(leader: Player | Mob, selector: str) -> bool:
    normalized = str(selector or "").strip().casefold()
    if not normalized:
        return True
    if normalized == str(leader.key or "").casefold():
        return True
    if normalized == str(leader.name or "").strip().casefold():
        return True
    return room_char_matches_selector(leader, normalized)


def _leader_notification(
    *,
    event_type: str,
    follower: Player,
    leader: Player | Mob,
) -> GameEvent | None:
    if not isinstance(leader, Player):
        return None
    verb = "begins" if event_type.endswith("started") else "stops"
    return GameEvent(
        type=event_type,
        recipients=[leader.key],
        data={
            "follower": _character_ref(follower),
            "leader": _character_ref(leader),
        },
        text=f"{follower.name} {verb} following you.",
    )


class FollowAction:
    def execute(self, player_id: int, selector: str | None) -> ActionResult:
        follower_snapshot = (
            Player.objects.select_related("room", "world").get(pk=player_id)
        )
        if not follower_snapshot.in_game:
            raise ActionError(
                "You are not currently in the game.",
                code="not_in_game",
            )
        target_snapshot = _follow_target(follower_snapshot, selector)

        with transaction.atomic():
            lock_movement_follow_graph(follower_snapshot.world_id)
            try:
                player, target = _lock_follow_participants(
                    follower_snapshot=follower_snapshot,
                    target_snapshot=target_snapshot,
                )
            except OperationalError as exc:
                if not _is_lock_not_available(exc):
                    raise
                raise ActionError(
                    "They are busy moving. Try following again.",
                    code="follow_busy",
                    data={"retryable": True},
                ) from exc
            current = _current_follow(player, for_update=True)

            target_player_id = target.id if isinstance(target, Player) else None
            target_mob_id = target.id if isinstance(target, Mob) else None
            unchanged = bool(
                current
                and current.leader_player_id == target_player_id
                and current.leader_mob_id == target_mob_id
            )
            previous_leader = current.leader if current else None

            if unchanged:
                status = "unchanged"
            else:
                _ensure_follow_chain(
                    follower=player,
                    leader=target,
                )
                if current is None:
                    current = MovementFollow.objects.create(
                        follower=player,
                        leader_player=(
                            target if isinstance(target, Player) else None
                        ),
                        leader_mob=(target if isinstance(target, Mob) else None),
                        last_processed_sequence=int(
                            target.follow_move_sequence or 0
                        ),
                    )
                    status = "started"
                else:
                    current.leader_player = (
                        target if isinstance(target, Player) else None
                    )
                    current.leader_mob = (
                        target if isinstance(target, Mob) else None
                    )
                    current.last_processed_sequence = int(
                        target.follow_move_sequence or 0
                    )
                    current.save(
                        update_fields=[
                            "leader_player",
                            "leader_mob",
                            "last_processed_sequence",
                            "modified_ts",
                        ]
                    )
                    status = "switched"

            data = {
                "status": status,
                "follower": _character_ref(player),
                "leader": _character_ref(target),
            }
            if status == "unchanged":
                text = f"You are already following {data['leader']['name']}."
            else:
                text = f"You begin following {data['leader']['name']}."

            events = [
                GameEvent(
                    type="cmd.follow.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
            if status != "unchanged":
                if previous_leader is not None:
                    stopped = _leader_notification(
                        event_type="notification.follow.stopped",
                        follower=player,
                        leader=previous_leader,
                    )
                    if stopped is not None:
                        events.append(stopped)
                started = _leader_notification(
                    event_type="notification.follow.started",
                    follower=player,
                    leader=target,
                )
                if started is not None:
                    events.append(started)

        return ActionResult(events=events, data=data)


class UnfollowAction:
    def execute(self, player_id: int, selector: str | None = None) -> ActionResult:
        expected_world_id = Player.objects.values_list(
            "world_id",
            flat=True,
        ).get(pk=player_id)
        with transaction.atomic():
            player = (
                Player.objects.select_for_update(of=("self",))
                .select_related("world")
                .get(pk=player_id)
            )
            if player.world_id != expected_world_id:
                raise ActionError(
                    "Your location changed. Try unfollowing again.",
                    code="context_changed",
                )
            if not player.in_game:
                raise ActionError(
                    "You are not currently in the game.",
                    code="not_in_game",
                )
            current = _current_follow(player, for_update=True)

            if current is None:
                data = {
                    "status": "unchanged",
                    "follower": _character_ref(player),
                    "leader": None,
                }
                events = [
                    GameEvent(
                        type="cmd.unfollow.success",
                        recipients=[player.key],
                        data=data,
                        text="You are not following anyone.",
                    )
                ]
            else:
                leader = current.leader
                if selector and not _leader_matches_selector(leader, selector):
                    raise ActionError(
                        "You are not following them.",
                        code="not_following_target",
                    )
                leader_data = _character_ref(leader)
                current.delete()
                data = {
                    "status": "stopped",
                    "follower": _character_ref(player),
                    "leader": leader_data,
                }
                events = [
                    GameEvent(
                        type="cmd.unfollow.success",
                        recipients=[player.key],
                        data=data,
                        text=f"You stop following {leader_data['name']}.",
                    )
                ]
                stopped = _leader_notification(
                    event_type="notification.follow.stopped",
                    follower=player,
                    leader=leader,
                )
                if stopped is not None:
                    events.append(stopped)

        return ActionResult(events=events, data=data)
