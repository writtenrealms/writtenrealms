from __future__ import annotations

import copy
from dataclasses import dataclass
import logging
import uuid

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from config import constants as adv_consts
from core.condition_dsl import ConditionContext, evaluate_condition
from spawns.actions.base import ActionResult
from spawns.events import GameEvent
from spawns.models import ActiveEffect, CombatEncounter, Mob, Player
from spawns.state_payloads import (
    door_state_lookup,
    safe_capitalize,
    serialize_char_from_mob,
    serialize_char_from_player,
)
from worlds.models import Room, RoomFlag


logger = logging.getLogger(__name__)

TRACKER_TRAIT_KEY = "tracker"
TRACKER_RUNTIME_CHASE_KEY = "last_chase_key"
TRACKER_RUNTIME_CHASE_KEYS = "processed_chase_keys"
MAX_TRACKER_CHASE_RECEIPTS = 32


@dataclass(frozen=True)
class TrackerEscapePlan:
    chase_key: str
    player_id: int
    world_id: int
    origin_room_id: int
    destination_room_id: int
    direction: str
    source: str
    encounter_ids: tuple[int, ...]
    tracker_mob_ids: tuple[int, ...]
    combat_locked: bool

    def action_payload(self) -> dict:
        return {
            "chase_key": self.chase_key,
            "player_id": self.player_id,
            "world_id": self.world_id,
            "origin_room_id": self.origin_room_id,
            "destination_room_id": self.destination_room_id,
            "direction": self.direction,
            "source": self.source,
            "encounter_ids": list(self.encounter_ids),
            "mob_ids": list(self.tracker_mob_ids),
        }


def _tracker_event_data(
    *,
    player_id: int,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    source: str,
) -> dict:
    return {
        "type": "player_escape",
        "source": source,
        "player_id": player_id,
        "origin_room_id": origin_room_id,
        "destination_room_id": destination_room_id,
        "direction": direction,
    }


def _trait_key(trait) -> str:
    if not isinstance(trait, dict):
        return ""
    return str(trait.get("key") or "").strip().lower()


def _has_tracker_trait_key(mob: Mob) -> bool:
    return any(
        _trait_key(trait) == TRACKER_TRAIT_KEY
        for trait in (mob.trait_instances or [])
    )


def _tracker_trait_is_active(
    mob: Mob,
    *,
    player: Player,
    room: Room,
    event_data: dict,
) -> bool:
    for trait in mob.trait_instances or []:
        if _trait_key(trait) != TRACKER_TRAIT_KEY:
            continue
        conditions = trait.get("conditions") if isinstance(trait, dict) else None
        if conditions in (None, {}, []):
            return True
        try:
            if evaluate_condition(
                conditions,
                context=ConditionContext(
                    actor=mob,
                    player=player,
                    room=room,
                    zone=room.zone,
                    world=player.world,
                    event_data=event_data,
                ),
            ):
                return True
        except Exception:
            logger.exception(
                "Failed to evaluate tracker conditions for mob %s.",
                mob.id,
            )
    return False


def _tracker_chase_was_processed(mob: Mob, chase_key: str) -> bool:
    for trait in mob.trait_instances or []:
        if _trait_key(trait) != TRACKER_TRAIT_KEY:
            continue
        runtime = trait.get("runtime") if isinstance(trait, dict) else None
        if not isinstance(runtime, dict):
            continue
        if runtime.get(TRACKER_RUNTIME_CHASE_KEY) == chase_key:
            return True
        processed_keys = runtime.get(TRACKER_RUNTIME_CHASE_KEYS)
        if isinstance(processed_keys, list) and chase_key in processed_keys:
            return True
    return False


def _mark_tracker_chase_processed(mob: Mob, chase_key: str) -> bool:
    changed = False
    trait_instances = copy.deepcopy(mob.trait_instances or [])
    for trait in trait_instances:
        if _trait_key(trait) != TRACKER_TRAIT_KEY:
            continue
        runtime = trait.get("runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            trait["runtime"] = runtime
        processed_keys = runtime.get(TRACKER_RUNTIME_CHASE_KEYS)
        if not isinstance(processed_keys, list):
            processed_keys = []
        processed_keys = [
            str(processed_key)
            for processed_key in processed_keys
            if str(processed_key or "").strip() and processed_key != chase_key
        ]
        processed_keys.append(chase_key)
        runtime[TRACKER_RUNTIME_CHASE_KEYS] = processed_keys[
            -MAX_TRACKER_CHASE_RECEIPTS:
        ]
        runtime[TRACKER_RUNTIME_CHASE_KEY] = chase_key
        changed = True
    if changed:
        mob.trait_instances = trait_instances
    return changed


def load_player_escape_encounters(
    *,
    player: Player,
    origin_room_id: int,
) -> tuple[CombatEncounter, ...]:
    """Load valid active origin encounters with one indexed, bounded query."""
    return tuple(
        CombatEncounter.objects.select_related(
            "mob",
            "mob__definition",
            "room__zone",
        )
        .filter(
            player_id=player.id,
            world_id=player.world_id,
            room_id=origin_room_id,
            status=CombatEncounter.STATUS_ACTIVE,
            mob_id__isnull=False,
            mob__room_id=origin_room_id,
            mob__is_pending_deletion=False,
            mob__health__gt=0,
        )
        .order_by("id")
    )


def plan_player_escape(
    *,
    player: Player,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    source: str,
    encounters: tuple[CombatEncounter, ...] | None = None,
) -> TrackerEscapePlan:
    """Capture a bounded tracker candidate set from active origin encounters.

    This query intentionally does not lock encounter rows. Player movement owns
    the player row lock, which serializes it against a combat round becoming
    locked without introducing the resolver's Encounter -> Player lock inverse.
    """
    if encounters is None:
        encounters = load_player_escape_encounters(
            player=player,
            origin_room_id=origin_room_id,
        )
    normalized_source = str(source or "move").strip().lower() or "move"
    combat_locked = any(encounter.is_combat_locked for encounter in encounters)
    origin_room = next(
        (
            encounter.room
            for encounter in encounters
            if encounter.room_id == origin_room_id
        ),
        None,
    )
    if encounters and origin_room is None:
        origin_room = Room.objects.select_related("zone", "world").filter(
            pk=origin_room_id
        ).first()

    event_data = _tracker_event_data(
        player_id=player.id,
        origin_room_id=origin_room_id,
        destination_room_id=destination_room_id,
        direction=direction,
        source=normalized_source,
    )
    tracker_mob_ids = []
    if origin_room is not None and not (
        normalized_source == "move" and combat_locked
    ):
        for encounter in encounters:
            mob = encounter.mob
            if mob and _tracker_trait_is_active(
                mob,
                player=player,
                room=origin_room,
                event_data=event_data,
            ):
                tracker_mob_ids.append(mob.id)

    return TrackerEscapePlan(
        chase_key=str(uuid.uuid4()),
        player_id=player.id,
        world_id=player.world_id,
        origin_room_id=origin_room_id,
        destination_room_id=destination_room_id,
        direction=direction,
        source=normalized_source,
        encounter_ids=tuple(sorted({encounter.id for encounter in encounters})),
        tracker_mob_ids=tuple(sorted(set(tracker_mob_ids))),
        combat_locked=combat_locked,
    )


def _load_tracker_rooms(
    origin_room_id: int,
    destination_room_id: int,
) -> tuple[Room | None, Room | None]:
    no_roam_flag = RoomFlag.objects.filter(
        room_id=OuterRef("pk"),
        code=adv_consts.ROOM_FLAG_NO_ROAM,
    )
    rooms = {
        room.id: room
        for room in (
            Room.objects.select_related("zone", "world")
            .annotate(_tracker_no_roam=Exists(no_roam_flag))
            .filter(pk__in=[origin_room_id, destination_room_id])
        )
    }
    return rooms.get(origin_room_id), rooms.get(destination_room_id)


def _tracker_edge_is_passable(
    *,
    player: Player,
    origin_room: Room | None,
    destination_room: Room | None,
    direction: str,
) -> bool:
    if (
        origin_room is None
        or destination_room is None
        or getattr(origin_room, "_tracker_no_roam", False)
        or getattr(destination_room, "_tracker_no_roam", False)
        or direction not in adv_consts.DIRECTIONS
        or getattr(origin_room, f"{direction}_id", None) != destination_room.id
    ):
        return False
    door_states = door_state_lookup(player.world, [origin_room.id]).get(
        origin_room.id,
        {},
    )
    return door_states.get(direction) not in (
        adv_consts.DOOR_STATE_CLOSED,
        adv_consts.DOOR_STATE_LOCKED,
    )


def _arrival_source_text(reverse_direction: str) -> str:
    if reverse_direction == "up":
        return "above"
    if reverse_direction == "down":
        return "below"
    return f"the {reverse_direction}"


def _tracker_core_faction_codes(
    mobs: list[Mob],
    *,
    world,
) -> dict[int, str | None]:
    """Resolve explicit/default core factions once for a tracker batch."""
    resolved: dict[int, str | None] = {}
    needs_default: list[int] = []
    for mob in mobs:
        assignments = getattr(mob, "_prefetched_objects_cache", {}).get(
            "faction_assignments",
            [],
        )
        explicit_code = next(
            (
                assignment.faction.code
                for assignment in assignments
                if assignment.faction
                and (
                    assignment.faction.type == "core"
                    or assignment.faction.is_core
                )
            ),
            None,
        )
        if explicit_code:
            resolved[mob.id] = explicit_code
        else:
            needs_default.append(mob.id)

    default_code = None
    if needs_default:
        from builders.models import Faction

        authored_world = world.context if world.context else world
        default_code = (
            Faction.objects.filter(
                world=authored_world,
                type="core",
                playable=True,
            )
            .order_by("-is_default", "created_ts", "id")
            .values_list("code", flat=True)
            .first()
        )
    for mob_id in needs_default:
        resolved[mob_id] = default_code
    return resolved


def _tracker_movement_events(
    *,
    mob_ids: list[int],
    player: Player,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    event_group: str,
    mob_char_payloads: dict[int, dict] | None = None,
) -> list[GameEvent]:
    if not mob_ids:
        return []

    origin_recipients = [
        f"player.{player_id}"
        for player_id in Player.objects.filter(
            world_id=player.world_id,
            room_id=origin_room_id,
            in_game=True,
        ).values_list("id", flat=True)
    ]
    destination_recipients = [
        f"player.{player_id}"
        for player_id in Player.objects.filter(
            world_id=player.world_id,
            room_id=destination_room_id,
            in_game=True,
        ).values_list("id", flat=True)
    ]
    reverse_direction = adv_consts.REVERSE_DIRECTIONS[direction]
    events: list[GameEvent] = []
    for mob_id in mob_ids:
        actor_payload = (mob_char_payloads or {}).get(mob_id)
        if not actor_payload or actor_payload.get("is_invisible"):
            continue
        actor_name = safe_capitalize(actor_payload.get("name") or "Someone")
        if origin_recipients:
            events.append(
                GameEvent(
                    type="notification.movement.exit",
                    recipients=origin_recipients,
                    data={"actor": actor_payload, "direction": direction},
                    text=f"{actor_name} leaves {direction}.",
                    group=event_group,
                )
            )
        if destination_recipients:
            events.append(
                GameEvent(
                    type="notification.movement.enter",
                    recipients=destination_recipients,
                    data={"actor": actor_payload, "direction": reverse_direction},
                    text=(
                        f"{actor_name} has arrived from "
                        f"{_arrival_source_text(reverse_direction)}."
                    ),
                    group=event_group,
                )
            )
    return events


class ResolveTrackerChaseAction:
    """Resolve one idempotent, one-edge batch of tracker pursuit."""

    def execute(
        self,
        *,
        chase_key: str,
        player_id: int,
        world_id: int,
        origin_room_id: int,
        destination_room_id: int,
        direction: str,
        encounter_ids,
        mob_ids,
        source: str,
    ) -> ActionResult:
        normalized_encounter_ids = sorted({
            int(encounter_id)
            for encounter_id in (encounter_ids or [])
            if encounter_id
        })
        normalized_mob_ids = sorted({
            int(mob_id)
            for mob_id in (mob_ids or [])
            if mob_id
        })
        chase_key = str(chase_key or "").strip()
        if not chase_key:
            return ActionResult(data={"moved_mob_ids": []})

        # Encounter cleanup has its own lock-only transaction. Keeping those
        # locks out of the Player -> Mob transition below avoids the resolver's
        # Encounter -> Player lock cycle.
        if normalized_encounter_ids:
            with transaction.atomic():
                escaped_encounters = list(
                    CombatEncounter.objects.select_for_update(of=("self",))
                    .filter(
                        id__in=normalized_encounter_ids,
                        player_id=player_id,
                        world_id=world_id,
                        room_id=origin_room_id,
                    )
                    .order_by("id")
                )

                active_encounter_ids = [
                    encounter.id
                    for encounter in escaped_encounters
                    if encounter.status == CombatEncounter.STATUS_ACTIVE
                ]
                if active_encounter_ids:
                    ActiveEffect.objects.filter(
                        encounter_id__in=active_encounter_ids,
                        scope=ActiveEffect.SCOPE_ENCOUNTER,
                    ).delete()
                    CombatEncounter.objects.filter(
                        id__in=active_encounter_ids,
                    ).update(
                        status=CombatEncounter.STATUS_FINISHED,
                        next_resolution_ts=None,
                        pending_flee={},
                        pending_player_ability={},
                        pending_mob_ability={},
                    )

        if not normalized_mob_ids:
            return ActionResult(data={"moved_mob_ids": []})

        moved_mob_ids: list[int] = []
        destination_room_snapshot: dict | None = None
        movement_events: list[GameEvent] = []
        engagement_events: list[GameEvent] = []
        mob_char_payloads: dict[int, dict] = {}
        with transaction.atomic():
            player = (
                Player.objects.select_for_update(of=("self",))
                .select_related(
                    "world",
                    "world__config",
                    "world__context",
                    "world__context__config",
                )
                .prefetch_related("faction_assignments__faction")
                .filter(pk=player_id, world_id=world_id)
                .first()
            )
            tracker_mobs = list(
                Mob.objects.select_for_update(of=("self",))
                .select_related("definition", "merchant_runtime")
                .prefetch_related("faction_assignments__faction")
                .filter(pk__in=normalized_mob_ids, world_id=world_id)
                .order_by("id")
            )

            unprocessed_mobs = [
                mob
                for mob in tracker_mobs
                if _has_tracker_trait_key(mob)
                and not _tracker_chase_was_processed(mob, chase_key)
            ]
            if not unprocessed_mobs:
                return ActionResult(data={"moved_mob_ids": []})

            origin_room, destination_room = _load_tracker_rooms(
                origin_room_id,
                destination_room_id,
            )
            player_is_valid = bool(
                player
                and player.in_game
                and not player.is_invisible
                and int(player.health or 0) > 0
                and player.room_id == destination_room_id
            )
            edge_is_passable = bool(
                player_is_valid
                and _tracker_edge_is_passable(
                    player=player,
                    origin_room=origin_room,
                    destination_room=destination_room,
                    direction=direction,
                )
            )
            busy_mob_ids = set(
                CombatEncounter.objects.filter(
                    mob_id__in=[mob.id for mob in unprocessed_mobs],
                    status=CombatEncounter.STATUS_ACTIVE,
                )
                .exclude(id__in=normalized_encounter_ids)
                .values_list("mob_id", flat=True)
            )
            event_data = _tracker_event_data(
                player_id=player_id,
                origin_room_id=origin_room_id,
                destination_room_id=destination_room_id,
                direction=direction,
                source=source,
            )
            eligible_mobs: list[Mob] = []
            if edge_is_passable and origin_room is not None:
                eligible_mobs = [
                    mob
                    for mob in unprocessed_mobs
                    if mob.id not in busy_mob_ids
                    and not mob.is_pending_deletion
                    and getattr(mob, "attackable", True)
                    and int(mob.health or 0) > 0
                    and mob.room_id == origin_room_id
                    and _tracker_trait_is_active(
                        mob,
                        player=player,
                        room=origin_room,
                        event_data=event_data,
                    )
                ]

            timestamp = timezone.now()
            for mob in unprocessed_mobs:
                _mark_tracker_chase_processed(mob, chase_key)
                mob.modified_ts = timestamp

            from core.world_config import inherited_system_config
            from spawns.actions.combat import (
                ScanRoomAggroAction,
                _combat_interval,
                _room_payload,
                _target_priority_sort_key,
                primary_active_encounter_for_player,
            )

            rules_config = inherited_system_config(player.world) if player else None
            combat_allowed = bool(
                player
                and destination_room
                and not (rules_config and not rules_config.allow_combat)
            )
            if combat_allowed and eligible_mobs:
                eligible_mobs.sort(key=_target_priority_sort_key)
                existing_primary = primary_active_encounter_for_player(
                    player,
                    room=destination_room,
                )
                primary_mob = (
                    existing_primary.mob
                    if existing_primary and existing_primary.mob
                    else eligible_mobs[0]
                )
                aggro_action = ScanRoomAggroAction()
                interval = _combat_interval(rules_config)
                death_config = player.world.effective_config
                player_char_payload = serialize_char_from_player(player).model_dump()
                core_faction_codes = _tracker_core_faction_codes(
                    eligible_mobs,
                    world=player.world,
                )
                mob_char_payloads = {
                    mob.id: serialize_char_from_mob(
                        mob,
                        core_faction_override=core_faction_codes[mob.id],
                    ).model_dump()
                    for mob in eligible_mobs
                }
                primary_mob_char_payload = mob_char_payloads.get(primary_mob.id)
                if primary_mob_char_payload is None:
                    primary_mob_char_payload = serialize_char_from_mob(
                        primary_mob
                    ).model_dump()
                destination_room_payload = _room_payload(
                    player,
                    destination_room,
                )
                destination_room_snapshot = destination_room_payload

                if interval > 0:
                    for mob in eligible_mobs:
                        mob.room_id = destination_room_id
                    Mob.objects.bulk_update(
                        unprocessed_mobs,
                        ["trait_instances", "room", "modified_ts"],
                    )
                    for mob in eligible_mobs:
                        engage_result = aggro_action._start_aggro_encounter(
                            player=player,
                            room=destination_room,
                            mob=mob,
                            primary_mob=primary_mob,
                            rules_config=rules_config,
                            death_config=death_config,
                            player_char_payload=player_char_payload,
                            mob_char_payload=mob_char_payloads[mob.id],
                            primary_mob_char_payload=primary_mob_char_payload,
                            room_payload=destination_room_payload,
                        )
                        engagement_events.extend(engage_result.events)
                        moved_mob_ids.append(mob.id)
                else:
                    Mob.objects.bulk_update(
                        unprocessed_mobs,
                        ["trait_instances", "modified_ts"],
                    )
                    for mob in eligible_mobs:
                        if (
                            int(player.health or 0) <= 0
                            or player.room_id != destination_room_id
                        ):
                            break
                        moved_mob_id = mob.id
                        mob.room_id = destination_room_id
                        mob.save(update_fields=["room", "modified_ts"])
                        engage_result = aggro_action._start_aggro_encounter(
                            player=player,
                            room=destination_room,
                            mob=mob,
                            primary_mob=primary_mob,
                            rules_config=rules_config,
                            death_config=death_config,
                            player_char_payload=player_char_payload,
                            mob_char_payload=mob_char_payloads[mob.id],
                            primary_mob_char_payload=primary_mob_char_payload,
                            room_payload=destination_room_payload,
                        )
                        engagement_events.extend(engage_result.events)
                        moved_mob_ids.append(moved_mob_id)
                        player.refresh_from_db(fields=["health", "room"])
            else:
                Mob.objects.bulk_update(
                    unprocessed_mobs,
                    ["trait_instances", "modified_ts"],
                )

            movement_events = _tracker_movement_events(
                mob_ids=moved_mob_ids,
                player=player,
                origin_room_id=origin_room_id,
                destination_room_id=destination_room_id,
                direction=direction,
                event_group=f"tracker:{chase_key}",
                mob_char_payloads=mob_char_payloads,
            )

        if not moved_mob_ids:
            return ActionResult(
                events=movement_events,
                data={"moved_mob_ids": []},
            )
        return ActionResult(
            events=[*movement_events, *engagement_events],
            data={
                "moved_mob_ids": moved_mob_ids,
                "destination_room_snapshot": destination_room_snapshot,
            },
        )
