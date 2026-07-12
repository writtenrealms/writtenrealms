import os
import math
import random
import uuid

from celery import shared_task

from builders.models import Path
from config import constants as api_consts
from config import game_settings as adv_config
from backend.config.exceptions import ServiceError
from core.computations import compute_stats
from core.world_config import inherited_system_config
from django.core.cache import cache
from django.db.models import F, Q
from spawns.services import WorldGate
from spawns.models import CombatEncounter, Mob, Player
from spawns.serializers import PlayerConfigSerializer
from spawns.events import GameEvent, flush_game_event_outbox, publish_events
from spawns.handlers import (
    ActorNotFoundError,
    dispatch_command,
    HandlerNotFoundError,
    PlayerNotFoundError,
)
from spawns.state_payloads import safe_capitalize, serialize_char_from_player
from spawns.state_payloads import door_state_lookup, serialize_char_from_mob
from worlds.models import Room, RoomFlag, World, Zone
from worlds.serializers import WorldSerializer

from fastapi_app.game_ws import publish_to_player
from fastapi_app.forge_ws import complete_job, exit_world as notify_exit_world

WR2_STANDING_REGEN_RATE = adv_config.PLAYER_STARTING_STAMINA_REGEN
WR2_RESTING_REGEN_MULTIPLIER = 3
DEFAULT_MOB_ROAM_CHANCE = getattr(adv_config, "DEFAULT_MOB_ROAM_CHANCE", 10)
GAME_HEARTBEAT_LOCK_KEY = "heartbeat_regen_lock"


def _notify_world_lifecycle(player: Player, world: World, action: str) -> None:
    if player.is_invisible:
        return

    recipient_ids = list(
        Player.objects.filter(
            world=world,
            in_game=True,
        )
        .exclude(pk=player.id)
        .values_list("id", flat=True)
    )
    if not recipient_ids:
        return

    if action == "enter":
        event_type = "notification.world.enter"
        text = f"{safe_capitalize(player.name)} has entered the world."
    elif action == "leave":
        event_type = "notification.world.leave"
        text = f"{safe_capitalize(player.name)} has left the world."
    else:
        return

    actor_payload = serialize_char_from_player(player).model_dump()
    publish_events(
        [
            GameEvent(
                type=event_type,
                recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                data={"actor": actor_payload},
                text=text,
            )
        ],
        actor_key=player.key,
    )


def _heartbeat_interval_seconds() -> float:
    raw_interval = getattr(adv_config, "GAME_HEARTBEAT_INTERVAL_SECONDS", 2)
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        return 2.0
    return max(interval, 1.0)


def _heartbeat_lock_timeout_seconds() -> int:
    return max(int(math.ceil(_heartbeat_interval_seconds() * 4)), 10)


def _as_non_negative_int(value, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _as_percent_int(value, default: int = 0) -> int:
    return min(_as_non_negative_int(value, default=default), 100)


def _world_default_roam_chance(world: World) -> int:
    config = inherited_system_config(world)
    if not config:
        return _as_percent_int(DEFAULT_MOB_ROAM_CHANCE, default=10)
    return _as_percent_int(
        getattr(config, "default_roam_chance", DEFAULT_MOB_ROAM_CHANCE),
        default=DEFAULT_MOB_ROAM_CHANCE,
    )


def _mob_roam_chance(mob: Mob, *, world_default_chance: int) -> int:
    explicit_chance = _as_percent_int(getattr(mob, "roam_chance", 0), default=0)
    if explicit_chance:
        return explicit_chance
    return world_default_chance


def _room_with_roam_exits(room_id: int) -> Room | None:
    return (
        Room.objects.select_related(
            "north",
            "east",
            "south",
            "west",
            "up",
            "down",
            "zone",
            "world",
        )
        .filter(pk=room_id)
        .first()
    )


def _room_is_no_roam(room_id: int | None) -> bool:
    if not room_id:
        return True
    return RoomFlag.objects.filter(
        room_id=room_id,
        code=api_consts.ROOM_FLAG_NO_ROAM,
    ).exists()


def _roam_target_allows_room(roams, room: Room) -> bool:
    if isinstance(roams, Zone):
        return room.zone_id == roams.id
    if isinstance(roams, Path):
        return roams.rooms.filter(pk=room.id).exists()
    return False


def _eligible_mob_roam_options(mob: Mob) -> list[tuple[str, Room]]:
    if not mob.room_id or not mob.roams:
        return []

    current_room = _room_with_roam_exits(mob.room_id)
    if current_room is None or _room_is_no_roam(current_room.id):
        return []

    door_states = door_state_lookup(mob.world, [current_room.id]).get(current_room.id, {})
    options: list[tuple[str, Room]] = []
    for direction in api_consts.DIRECTIONS:
        if door_states.get(direction) in ("closed", "locked"):
            continue
        destination = getattr(current_room, direction, None)
        if destination is None or _room_is_no_roam(destination.id):
            continue
        if not _roam_target_allows_room(mob.roams, destination):
            continue
        options.append((direction, destination))
    return options


def _arrival_source_text(reverse_direction: str) -> str:
    if reverse_direction == "up":
        return "above"
    if reverse_direction == "down":
        return "below"
    return f"the {reverse_direction}"


def _publish_mob_roam_events(
    *,
    mob: Mob,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    event_group: str | None = None,
) -> None:
    if getattr(mob, "is_invisible", False):
        return

    actor_payload = serialize_char_from_mob(mob).model_dump()
    actor_name = safe_capitalize(actor_payload.get("name") or mob.name or "Someone")
    events: list[GameEvent] = []

    origin_recipient_ids = (
        Player.objects.filter(
            world=mob.world,
            room_id=origin_room_id,
            in_game=True,
        )
        .values_list("id", flat=True)
    )
    if origin_recipient_ids:
        events.append(
            GameEvent(
                type="notification.movement.exit",
                recipients=[f"player.{player_id}" for player_id in origin_recipient_ids],
                data={"actor": actor_payload, "direction": direction},
                text=f"{actor_name} leaves {direction}.",
                group=event_group,
            )
        )

    destination_recipient_ids = (
        Player.objects.filter(
            world=mob.world,
            room_id=destination_room_id,
            in_game=True,
        )
        .values_list("id", flat=True)
    )
    if destination_recipient_ids:
        reverse_direction = api_consts.REVERSE_DIRECTIONS[direction]
        events.append(
            GameEvent(
                type="notification.movement.enter",
                recipients=[f"player.{player_id}" for player_id in destination_recipient_ids],
                data={"actor": actor_payload, "direction": reverse_direction},
                text=f"{actor_name} has arrived from {_arrival_source_text(reverse_direction)}.",
                group=event_group,
            )
        )

    if events:
        publish_events(events, actor_key=mob.key)


def _record_roaming_aggro_target(
    aggro_mob_ids_by_world_room: dict[tuple[int, int], set[int]] | None,
    *,
    world_id: int,
    room_id: int,
    mob_id: int,
) -> None:
    if aggro_mob_ids_by_world_room is None:
        return
    aggro_mob_ids_by_world_room.setdefault((world_id, room_id), set()).add(mob_id)


def _publish_roaming_aggro(
    aggro_mob_ids_by_world_room: dict[tuple[int, int], set[int]]
) -> None:
    if not aggro_mob_ids_by_world_room:
        return

    from spawns.actions.combat import ScanRoomAggroAction

    world_ids = {world_id for world_id, _ in aggro_mob_ids_by_world_room.keys()}
    room_ids = {room_id for _, room_id in aggro_mob_ids_by_world_room.keys()}
    aggro_action = ScanRoomAggroAction()
    players = (
        Player.objects.filter(
            world_id__in=world_ids,
            room_id__in=room_ids,
            in_game=True,
        )
        .only("id", "world_id", "room_id")
        .order_by("id")
    )
    for player in players:
        mob_ids = aggro_mob_ids_by_world_room.get((player.world_id, player.room_id))
        if not mob_ids:
            continue
        result = aggro_action.execute(player.id, mob_ids=mob_ids)
        if result.events:
            publish_events(result.events, actor_key=player.key)


def _try_roam_mob(
    mob: Mob,
    *,
    world_default_chance: int,
    event_group: str | None = None,
    aggro_mob_ids_by_world_room: dict[tuple[int, int], set[int]] | None = None,
) -> bool:
    chance = _mob_roam_chance(mob, world_default_chance=world_default_chance)
    if chance <= 0 or random.randint(1, 100) > chance:
        return False

    options = _eligible_mob_roam_options(mob)
    if not options:
        return False

    direction, destination = random.choice(options)
    origin_room_id = mob.room_id
    mob.room = destination
    mob.save(update_fields=["room", "modified_ts"])
    _publish_mob_roam_events(
        mob=mob,
        origin_room_id=origin_room_id,
        destination_room_id=destination.id,
        direction=direction,
        event_group=event_group,
    )
    _record_roaming_aggro_target(
        aggro_mob_ids_by_world_room,
        world_id=mob.world_id,
        room_id=destination.id,
        mob_id=mob.id,
    )
    return True


def _mob_roam_group_id(mob: Mob) -> str:
    if not mob.group_id:
        return ""
    placement = getattr(mob, "spawn_placement", None)
    state = placement.state if placement and isinstance(placement.state, dict) else {}
    if not state.get("cohort_slug"):
        return ""
    return mob.group_id


def _mob_cohort_role(mob: Mob) -> str:
    placement = getattr(mob, "spawn_placement", None)
    state = placement.state if placement and isinstance(placement.state, dict) else {}
    return str(state.get("cohort_role") or "member").strip().lower()


def _cohort_roam_leader(members: list[Mob]) -> Mob | None:
    for member in members:
        if _mob_cohort_role(member) == "leader":
            return member
    return members[0] if members else None


def _try_roam_cohort(
    mob: Mob,
    *,
    world_default_chance: int,
    active_combat_mob_ids: set[int],
    event_group: str | None = None,
    aggro_mob_ids_by_world_room: dict[tuple[int, int], set[int]] | None = None,
) -> int:
    group_id = _mob_roam_group_id(mob)
    if not group_id:
        return 1 if _try_roam_mob(
            mob,
            world_default_chance=world_default_chance,
            event_group=event_group,
            aggro_mob_ids_by_world_room=aggro_mob_ids_by_world_room,
        ) else 0

    members = list(
        Mob.objects.filter(
            world_id=mob.world_id,
            group_id=group_id,
            is_pending_deletion=False,
            room_id__isnull=False,
        )
        .select_related("world", "definition", "spawn_placement")
        .order_by("id")
    )
    leader = _cohort_roam_leader(members)
    if leader is None:
        return 0
    if any(member.id in active_combat_mob_ids for member in members):
        return 0

    chance = _mob_roam_chance(leader, world_default_chance=world_default_chance)
    if chance <= 0 or random.randint(1, 100) > chance:
        return 0

    options = _eligible_mob_roam_options(leader)
    if not options:
        return 0

    direction, destination = random.choice(options)
    origin_room_id = leader.room_id
    movable_members = [
        member
        for member in members
        if member.room_id == origin_room_id
        and member.roams
        and _roam_target_allows_room(member.roams, destination)
    ]
    if not any(member.id == leader.id for member in movable_members):
        return 0

    for member in movable_members:
        member.room = destination
        member.save(update_fields=["room", "modified_ts"])
        _publish_mob_roam_events(
            mob=member,
            origin_room_id=origin_room_id,
            destination_room_id=destination.id,
            direction=direction,
            event_group=event_group,
        )
        _record_roaming_aggro_target(
            aggro_mob_ids_by_world_room,
            world_id=member.world_id,
            room_id=destination.id,
            mob_id=member.id,
        )
    return len(movable_members)


def run_mob_roaming(*, active_combat_mob_ids: set[int] | None = None) -> int:
    active_combat_mob_ids = active_combat_mob_ids or set()
    running_worlds = list(
        World.objects.filter(
            context__isnull=False,
            lifecycle=api_consts.WORLD_LIFECYCLE_RUNNING,
        ).select_related(
            "config",
            "context",
            "context__config",
            "context__instance_of",
            "context__instance_of__config",
        )
    )
    if not running_worlds:
        return 0

    worlds_by_id = {world.id: world for world in running_worlds}
    default_chance_by_world_id = {
        world.id: _world_default_roam_chance(world)
        for world in running_worlds
    }
    roamed_count = 0
    mobs_qs = (
        Mob.objects.filter(
            is_pending_deletion=False,
            room_id__isnull=False,
            roams_type__isnull=False,
            roams_id__isnull=False,
            world_id__in=worlds_by_id.keys(),
        )
        .select_related("world", "definition", "spawn_placement")
        .order_by("id")
    )
    heartbeat_event_group = f"heartbeat.mob_roaming.{uuid.uuid4().hex}"
    processed_group_ids: set[str] = set()
    aggro_mob_ids_by_world_room: dict[tuple[int, int], set[int]] = {}
    for mob in mobs_qs.iterator(chunk_size=200):
        if mob.id in active_combat_mob_ids:
            continue
        world_default_chance = default_chance_by_world_id.get(mob.world_id, 0)
        group_id = _mob_roam_group_id(mob)
        if group_id:
            if group_id in processed_group_ids:
                continue
            processed_group_ids.add(group_id)
            roamed_count += _try_roam_cohort(
                mob,
                world_default_chance=world_default_chance,
                active_combat_mob_ids=active_combat_mob_ids,
                event_group=heartbeat_event_group,
                aggro_mob_ids_by_world_room=aggro_mob_ids_by_world_room,
            )
        elif _try_roam_mob(
            mob,
            world_default_chance=world_default_chance,
            event_group=heartbeat_event_group,
            aggro_mob_ids_by_world_room=aggro_mob_ids_by_world_room,
        ):
            roamed_count += 1
    _publish_roaming_aggro(aggro_mob_ids_by_world_room)
    return roamed_count


def _regen_resource(current_value: int, max_value: int, regen_amount: int) -> int:
    current = _as_non_negative_int(current_value)
    cap = max(_as_non_negative_int(max_value), current)
    amount = _as_non_negative_int(regen_amount)

    if amount <= 0 or current >= cap:
        return current
    return min(current + amount, cap)


def _apply_regen(
    actor: Player | Mob,
    *,
    health_max: int,
    energy_max: int,
    stamina_max: int,
    health_add: int,
    energy_add: int,
    stamina_add: int,
) -> bool:
    update_fields: list[str] = []

    next_health = _regen_resource(actor.health, health_max, health_add)
    if next_health != actor.health:
        actor.health = next_health
        update_fields.append("health")

    next_energy = _regen_resource(actor.energy, energy_max, energy_add)
    if next_energy != actor.energy:
        actor.energy = next_energy
        update_fields.append("energy")

    next_stamina = _regen_resource(actor.stamina, stamina_max, stamina_add)
    if next_stamina != actor.stamina:
        actor.stamina = next_stamina
        update_fields.append("stamina")

    if not update_fields:
        return False

    actor.save(update_fields=update_fields)
    return True


def _regen_player(player: Player, *, in_combat: bool = False) -> dict[str, int | str] | None:
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=player.world,
    )

    health_max = max(_as_non_negative_int(stats.get("health_max")), _as_non_negative_int(player.health))
    energy_max = max(_as_non_negative_int(stats.get("energy_max")), _as_non_negative_int(player.energy))
    stamina_max = max(_as_non_negative_int(stats.get("stamina_max")), _as_non_negative_int(player.stamina))
    energy_base = _as_non_negative_int(stats.get("energy_base"), default=energy_max)

    health_regen = _as_non_negative_int(getattr(player, "health_regen", 0)) + _as_non_negative_int(
        stats.get("health_regen")
    )
    energy_regen = _as_non_negative_int(getattr(player, "energy_regen", 0)) + _as_non_negative_int(
        stats.get("energy_regen")
    )
    stamina_regen = _as_non_negative_int(getattr(player, "stamina_regen", 0)) + _as_non_negative_int(
        stats.get("stamina_regen")
    )

    if in_combat:
        health_add = health_regen
        energy_add = energy_regen
        stamina_add = stamina_regen or WR2_STANDING_REGEN_RATE
    else:
        base_regen_rate = WR2_STANDING_REGEN_RATE
        if getattr(player, "state", None) == api_consts.CHARACTER_STATE_RESTING:
            base_regen_rate *= WR2_RESTING_REGEN_MULTIPLIER
        health_add = math.ceil(health_max * base_regen_rate / 100) + health_regen
        energy_add = math.ceil(energy_base * base_regen_rate / 100) + energy_regen
        stamina_add = max(stamina_regen, base_regen_rate)

    changed = _apply_regen(
        player,
        health_max=health_max,
        energy_max=energy_max,
        stamina_max=stamina_max,
        health_add=health_add,
        energy_add=energy_add,
        stamina_add=stamina_add,
    )
    if not changed:
        return None

    return {
        "key": player.key,
        "state": getattr(player, "state", api_consts.CHARACTER_STATE_STANDING),
        "health": player.health,
        "health_max": health_max,
        "health_regen": health_regen,
        "energy": player.energy,
        "energy_max": energy_max,
        "energy_regen": energy_regen,
        "stamina": player.stamina,
        "stamina_max": stamina_max,
        "stamina_regen": stamina_regen,
    }


def _regen_mob(mob: Mob, *, in_combat: bool = False) -> bool:
    health_max = _as_non_negative_int(getattr(mob, "health_max", mob.health), default=mob.health)
    energy_max = _as_non_negative_int(getattr(mob, "energy_max", mob.energy), default=mob.energy)
    stamina_max = _as_non_negative_int(getattr(mob, "stamina_max", mob.stamina), default=mob.stamina)
    regen_rate = _as_non_negative_int(getattr(mob, "regen_rate", WR2_STANDING_REGEN_RATE))

    health_regen = _as_non_negative_int(getattr(mob, "health_regen", 0))
    energy_regen = _as_non_negative_int(getattr(mob, "energy_regen", 0))
    stamina_regen = _as_non_negative_int(getattr(mob, "stamina_regen", 0))
    if in_combat:
        health_add = health_regen
        energy_add = energy_regen
        stamina_add = WR2_STANDING_REGEN_RATE + stamina_regen
    else:
        health_add = math.ceil(health_max * regen_rate / 100) + health_regen
        energy_add = math.ceil(energy_max * regen_rate / 100) + energy_regen
        stamina_add = WR2_STANDING_REGEN_RATE + stamina_regen

    return _apply_regen(
        mob,
        health_max=health_max,
        energy_max=energy_max,
        stamina_max=stamina_max,
        health_add=health_add,
        energy_add=energy_add,
        stamina_add=stamina_add,
    )


def run_game_heartbeat() -> dict[str, int]:
    from spawns.actions.abilities import ability_state_event, decrement_ability_cooldowns
    from spawns.actions.combat import resolve_due_character_effects
    from spawns.actions.effects import (
        combat_tagged_actor_ids,
    )

    players_regenerated = 0
    player_cooldowns_updated = 0
    player_effects_updated = 0
    mobs_regenerated = 0
    mobs_roamed = 0

    # Recover any effects whose state committed before a previous worker died
    # while publishing their events.
    flush_game_event_outbox(publisher=publish_events)

    active_players = Player.objects.filter(
        in_game=True,
        world__lifecycle=api_consts.WORLD_LIFECYCLE_RUNNING,
    ).only(
        "id",
        "world_id",
        "level",
        "archetype",
        "health",
        "energy",
        "stamina",
        "state",
        "known_abilities",
        "ability_hotkeys",
        "ability_cooldowns",
    )
    active_world_ids = list(active_players.values_list("world_id", flat=True).distinct())
    active_combat_player_ids = set(
        CombatEncounter.objects.filter(
            status=CombatEncounter.STATUS_ACTIVE,
            player__in_game=True,
            player__world__lifecycle=api_consts.WORLD_LIFECYCLE_RUNNING,
        ).values_list("player_id", flat=True)
    )
    active_combat_mob_ids = set(
        CombatEncounter.objects.filter(
            status=CombatEncounter.STATUS_ACTIVE,
            player__in_game=True,
            player__world__lifecycle=api_consts.WORLD_LIFECYCLE_RUNNING,
            mob_id__isnull=False,
        ).values_list("mob_id", flat=True)
    )
    tagged_player_ids, tagged_mob_ids = combat_tagged_actor_ids()
    active_combat_player_ids.update(tagged_player_ids)
    active_combat_mob_ids.update(tagged_mob_ids)

    for player in active_players.iterator(chunk_size=200):
        actor_update = _regen_player(
            player,
            in_combat=player.id in active_combat_player_ids,
        )
        if actor_update:
            players_regenerated += 1
            publish_to_player(
                player.key,
                {
                    "type": "notification.regen",
                    "data": {
                        "actor": actor_update,
                    },
                },
            )
        if player.id not in active_combat_player_ids:
            cooldowns_changed = decrement_ability_cooldowns(player)
            update_fields = []
            if cooldowns_changed:
                update_fields.append("ability_cooldowns")
                player_cooldowns_updated += 1
            if update_fields:
                player.save(update_fields=update_fields)
            if cooldowns_changed:
                publish_events(
                    [ability_state_event(player)],
                    actor_key=player.key,
                )

    effect_events = resolve_due_character_effects(
        world_ids=active_world_ids,
        persist_events=True,
    )
    if effect_events:
        player_effects_updated = len(
            {
                recipient
                for event in effect_events
                if event.type == "player.abilities.update"
                for recipient in event.recipients
            }
        )
        flush_game_event_outbox(publisher=publish_events)

    if active_world_ids:
        mobs_qs = (
            Mob.objects.filter(
                is_pending_deletion=False,
                world_id__in=active_world_ids,
            )
            .filter(
                Q(health__lt=F("health_max"))
                | Q(energy__lt=F("energy_max"))
                | Q(stamina__lt=F("stamina_max"))
            )
            .only(
                "id",
                "health",
                "energy",
                "stamina",
                "health_max",
                "energy_max",
                "stamina_max",
                "health_regen",
                "energy_regen",
                "stamina_regen",
                "regen_rate",
                "is_pending_deletion",
            )
        )
    else:
        mobs_qs = Mob.objects.none()

    for mob in mobs_qs.iterator(chunk_size=200):
        if _regen_mob(mob, in_combat=mob.id in active_combat_mob_ids):
            mobs_regenerated += 1

    mobs_roamed = run_mob_roaming(active_combat_mob_ids=active_combat_mob_ids)

    return {
        "players": players_regenerated,
        "mobs": mobs_regenerated,
        "mobs_roamed": mobs_roamed,
        "ability_cooldowns": player_cooldowns_updated,
        "active_effects": player_effects_updated,
    }


def run_heartbeat_regen() -> dict[str, int]:
    return run_game_heartbeat()


def _run_game_heartbeat_task() -> dict[str, int] | dict[str, bool]:
    lock_timeout = _heartbeat_lock_timeout_seconds()
    if not cache.add(GAME_HEARTBEAT_LOCK_KEY, 1, timeout=lock_timeout):
        return {"skipped": True}
    try:
        return run_game_heartbeat()
    finally:
        cache.delete(GAME_HEARTBEAT_LOCK_KEY)


@shared_task(name="spawns.tasks.game_heartbeat", ignore_result=True)
def game_heartbeat():
    return _run_game_heartbeat_task()


@shared_task(name="spawns.tasks.heartbeat_regen", ignore_result=True)
def heartbeat_regen():
    return _run_game_heartbeat_task()


@shared_task
def enter_world(player_id, world_id, client_id=None, ip=None):
    print("enter_world IDs: player_id=%s, world_id=%s" % (player_id, world_id))

    player = Player.objects.get(pk=player_id)

    if not player.world.context:
        raise RuntimeError('Player is not in a spawn world.')

    spawn_world = player.world

    print("%s [ %s ] entering %s [ %s ]" % (player.name, player.id, spawn_world.name, spawn_world.id))

    try:
        # Enter the world
        WorldGate(player=player, world=spawn_world).enter(ip=ip)
        _notify_world_lifecycle(player, spawn_world, "enter")

        # - Instance Follow system -
        # This whether this instance assignment that had followers with
        # it was created
        assignment = player.player_instances.filter(
            instance=spawn_world
        ).first()
        if assignment and assignment.member_ids:
            game_world = spawn_world.game_world
            for member_id in assignment.member_ids.split():
                print('sending out command for %s to %s', (member_id, spawn_world.instance_ref))
                # add_timing(
                #     type='timing.defer',
                #     world=game_world.key,
                #     data={
                #         'actor': 'player.%s' % member_id,
                #         'cmd': 'enter %s' % spawn_world.instance_ref
                #     },
                #     db=game_world.db,)
            assignment.member_ids = None
            assignment.save()

        if client_id:
            # Determine the websocket uri
            host = os.getenv('WR_HOST', 'localhost')
            if host == 'localhost':
                ws_uri = 'ws://localhost:8001/ws/game/cmd'
            else:
                ws_uri = f'wss://{host}/ws/game/cmd'

            complete_job(
                client_id=client_id,
                job="enter_world",
                data={
                    "world": WorldSerializer(spawn_world).data,
                    "player_config": PlayerConfigSerializer(player.config).data,
                    "player_id": player.id,
                    "ws_uri": ws_uri,
                    "motd": spawn_world.context.motd,
                })

    except ServiceError as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="enter_world",
                status='error',
                data={'error': str(e)})
        else:
            print('error entering world:', str(e))


@shared_task
def exit_world(player_id, world_id,
               player_data_id=None,
               transfer_to=None,
               transfer_from=None,
               ref=None,
               leave_instance=False,
               member_ids=None):
    """
    Unlike the enter_world task, there is no client id being passed in.
    This is because we can't rely on it. Users may use the 'quit' command
    or the quit option from the menu, but they also may just close out the
    tab. Because of that, the trigger to exit a world has to be the
    game websocket having been severed, not the forge websocket.
    """
    player = Player.objects.get(pk=player_id)
    world = World.objects.get(pk=world_id)

    print("%s [ %s ] exiting %s [ %s ]" % (
        player.name,
        player.id,
        world.name,
        world.id))

    world_gate = WorldGate(player=player, world=world)
    world_gate.exit(player_data_id=player_data_id,
                    transfer_to=transfer_to,
                    transfer_from=transfer_from,
                    ref=ref,
                    leave_instance=leave_instance,
                    member_ids=member_ids)
    _notify_world_lifecycle(player, world, "leave")

    # Notify frontend
    notify_exit_world(
        player_id=player_id,
        world_id=world_id,
        exit_to=world.context.id)


@shared_task
def exit_current_world(player_id):
    player = Player.objects.select_related("world", "world__context").get(pk=player_id)
    if not player.in_game:
        return {"skipped": "not_in_game"}

    exit_world(
        player_id=player.id,
        world_id=player.world_id,
    )
    return {"exited": True, "world_id": player.world_id}


def _parse_actor_ref(actor_key: str | None) -> tuple[str, int] | None:
    actor_ref = str(actor_key or "").strip()
    if "." not in actor_ref:
        return None

    actor_kind, actor_id_str = actor_ref.split(".", 1)
    actor_kind = actor_kind.strip().lower()
    if actor_kind not in {"player", "mob"}:
        return None

    try:
        actor_id = int(actor_id_str)
    except ValueError:
        return None

    return actor_kind, actor_id


def _parse_player_id(player_key: str | None) -> int | None:
    parsed = _parse_actor_ref(player_key)
    if not parsed:
        return None
    actor_kind, actor_id = parsed
    if actor_kind != "player":
        return None
    return actor_id


def _publish_game_error(player_key: str | None, command_type: str, text: str, connection_id: str | None = None):
    """Publish an error message to a player's WebSocket connection."""
    if not player_key:
        return
    publish_to_player(
        player_key,
        {
            "type": f"cmd.{command_type}.error",
            "text": text,
            "data": {"error": text},
        },
        connection_id=connection_id,
    )


@shared_task
def execute_trigger_script_segments(
    actor_type: str,
    actor_id: int,
    segments: list[str],
    issuer_scope: str | None = None,
    connection_id: str | None = None,
):
    """
    Execute scripted trigger segments as a delayed trigger line.
    """
    for segment in segments or []:
        segment_text = str(segment or "").strip()
        if not segment_text:
            continue

        payload: dict[str, object] = {
            "text": segment_text,
            "skip_triggers": True,
        }
        if issuer_scope:
            payload["issuer_scope"] = issuer_scope

        try:
            dispatch_command(
                command_type="text",
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
                connection_id=connection_id,
                script_source=True,
            )
        except (ActorNotFoundError, HandlerNotFoundError, ValueError):
            return


@shared_task(ignore_result=True)
def resolve_combat_encounter(encounter_id: int):
    from spawns.actions.combat import resolve_combat_encounter_step

    result = resolve_combat_encounter_step(
        encounter_id,
        auto_advance=True,
    )
    if result.events:
        publish_events(
            result.events,
            actor_key=result.actor_key,
        )


@shared_task
def handle_game_command(
    command_type: str,
    player_id: int | None = None,
    player_key: str | None = None,
    payload: dict | None = None,
    connection_id: str | None = None,
):
    """
    Celery task entry point for game commands.

    This is a thin wrapper that:
    1. Resolves player_id/player_key
    2. Dispatches to the appropriate handler
    3. Catches and publishes errors

    All command logic lives in spawns.handlers package.
    """
    payload = payload or {}

    # Resolve player_key <-> player_id
    if not player_key and player_id:
        player_key = f"player.{player_id}"
    if not player_id and player_key:
        player_id = _parse_player_id(player_key)

    # Validate we have a player_id
    if not player_id:
        _publish_game_error(
            player_key,
            command_type,
            "Missing player_id for command.",
            connection_id=connection_id,
        )
        return

    # Dispatch to handler
    try:
        dispatch_command(
            command_type=command_type,
            player_id=player_id,
            payload=payload,
            connection_id=connection_id,
        )
    except PlayerNotFoundError as e:
        _publish_game_error(
            player_key,
            command_type,
            str(e),
            connection_id=connection_id,
        )
    except HandlerNotFoundError as e:
        _publish_game_error(
            player_key,
            command_type,
            f"Unhandled command: {command_type}",
            connection_id=connection_id,
        )
