"""
Shared builders for state.sync-style payloads.

These helpers are reused by handlers (state.sync, look, etc.) to build
StateSyncData-compatible structures without duplicating query/serialization
logic.
"""
import json
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from django.db.models import Prefetch
from django.utils import timezone

from config import constants as adv_consts
from core.computations import compute_stats
from spawns.models import DoorState, Item, Mob, Player
from spawns.schemas import (
    Actor,
    Char,
    Equipment as EquipmentSchema,
    Item as ItemSchema,
    MapRoom,
    Room as RoomSchema,
    StateSyncData,
    WhoListEntry,
    Zone as ZoneSchema,
)
from spawns.serializers import AnimatePlayerSerializer, AnimateWorldSerializer
from spawns.triggers import (
    get_char_action_labels_for_actor,
    get_item_action_labels_for_actor,
    get_room_action_labels_for_actor,
)
from worlds.models import Door, Room, World


# ---- Utilities ----

def safe_capitalize(value: Optional[str]) -> str:
    if not value:
        return ""
    return value[0].upper() + value[1:]


def computed_player_vitals(player: Player) -> dict[str, int]:
    stats = compute_stats(player.level, player.archetype)
    health_max = int(stats.get("health_max") or 0)
    mana_max = int(stats.get("mana_max") or 0)
    stamina_max = int(stats.get("stamina_max") or 0)
    return {
        "health_max": max(health_max, int(getattr(player, "health", 0) or 0)),
        "mana_max": max(mana_max, int(getattr(player, "mana", 0) or 0)),
        "stamina_max": max(stamina_max, int(getattr(player, "stamina", 0) or 0)),
        "health_regen": int(stats.get("health_regen") or 0),
        "mana_regen": int(stats.get("mana_regen") or 0),
        "stamina_regen": int(stats.get("stamina_regen") or 0),
    }


def get_player_with_related(player_id: int) -> Player:
    """
    Reload the player with the relations we need for serialization to keep
    query counts low.
    """
    inventory_qs = Item.objects.select_related("template", "currency")
    return (
        Player.objects.select_related(
            "world",
            "world__config",
            "world__context",
            "world__context__config",
            "world__instance_of",
            "room",
            "user",
            "config",
            "equipment",
        )
        .prefetch_related(
            "aliases",
            "marks",
            "faction_assignments__faction",
            "clan_memberships__clan",
            Prefetch("inventory", queryset=inventory_qs),
        )
        .get(pk=player_id)
    )


# ---- Serialization helpers ----

def resolve_item_name(item: Item) -> str:
    """
    Prefer template names for templated items when instance name is empty
    or still the legacy default placeholder.
    """
    instance_name = (item.name or "").strip()
    template_name = (item.template.name if item.template else "") or ""
    if template_name and (
        not instance_name
        or instance_name.lower() == "unnamed item"
    ):
        return template_name
    if instance_name:
        return instance_name
    if template_name:
        return template_name
    return "Unnamed item"


def serialize_item(item: Item, *, viewer: Player | Mob | None = None) -> ItemSchema:
    """Serialize an item into the WR2 Item schema."""
    name = resolve_item_name(item)
    currency = item.currency.code if item.currency else "gold"
    description = item.description
    if not description and item.template:
        description = item.template.description
    armor_value = getattr(item, "armor", None)
    if armor_value is None and item.template:
        armor_value = getattr(item.template, "armor", 0)
    if armor_value is None:
        armor_value = 0
    actions = get_item_action_labels_for_actor(viewer, item)

    return ItemSchema(
        key=item.key,
        name=name,
        cf_name=safe_capitalize(name),
        type=item.type,
        armor_class=item.armor_class,
        description=description,
        ground_description=item.ground_description,
        level=item.level,
        quality=item.quality,
        is_magic=getattr(item, "is_magic", False),
        equipment_type=item.equipment_type,
        template_id=item.template_id,
        strength=item.strength,
        constitution=item.constitution,
        dexterity=item.dexterity,
        intelligence=item.intelligence,
        attack_power=item.attack_power,
        spell_power=item.spell_power,
        armor=armor_value,
        crit=item.crit,
        resilience=item.resilience,
        dodge=item.dodge,
        health_max=item.health_max,
        health_regen=item.health_regen,
        mana_max=item.mana_max,
        mana_regen=item.mana_regen,
        stamina_max=item.stamina_max,
        stamina_regen=item.stamina_regen,
        is_pickable=item.is_pickable,
        cost=item.cost,
        currency=currency,
        keywords=item.keywords or "",
        weapon_type=item.weapon_type,
        actions=actions,
    )


def serialize_inventory(
    items: Iterable[Item],
    *,
    viewer: Player | Mob | None = None,
) -> List[ItemSchema]:
    return [serialize_item(item, viewer=viewer) for item in items]


def serialize_equipment(equipment, *, viewer: Player | Mob | None = None) -> EquipmentSchema:
    if not equipment:
        return EquipmentSchema()

    slots = {}
    for slot in (
        "weapon",
        "offhand",
        "head",
        "body",
        "arms",
        "hands",
        "waist",
        "legs",
        "feet",
        "accessory",
    ):
        eq_item = getattr(equipment, slot, None)
        if eq_item:
            slots[slot] = serialize_item(eq_item, viewer=viewer)
    return EquipmentSchema(**slots)


def serialize_char_from_player(player: Player) -> Char:
    return Char(
        id=player.id,
        key=player.key,
        name=player.name,
        title=player.title,
        description=player.description,
        archetype=player.archetype,
        core_faction=(player.factions or {}).get("core"),
        room_description=safe_capitalize(player.name) + " is here.",
        state="standing",
        stance="normal",
        health=player.health,
        health_max=getattr(player, "health_max", player.health),
        mana=player.mana,
        level=player.level,
        gender=player.gender or "male",
        keywords=getattr(player, "keywords", "") or player.name.lower(),
        char_type="player",
        display_faction=player.display_faction or None,
    )


def serialize_char_from_mob(mob: Mob, *, viewer: Player | Mob | None = None) -> Char:
    name = mob.name or (mob.template.name if mob.template else "Unnamed Mob")
    title = mob.title
    if not title and mob.template:
        title = mob.template.title
    description = mob.description
    if not description and mob.template:
        description = mob.template.description
    room_desc = mob.room_description
    if not room_desc and mob.template:
        room_desc = mob.template.room_description
    factions = mob.template.factions if mob.template else mob.factions
    actions = get_char_action_labels_for_actor(viewer, mob)
    return Char(
        id=mob.id,
        key=mob.key,
        name=name,
        title=title,
        description=description,
        archetype=mob.archetype,
        core_faction=(factions or {}).get("core"),
        room_description=room_desc or (name + " is here."),
        state="standing",
        stance="normal",
        health=mob.health,
        health_max=getattr(mob, "health_max", mob.health),
        mana=mob.mana,
        level=mob.level,
        gender=mob.gender or "male",
        keywords=mob.keywords or name.lower(),
        template_id=mob.template_id,
        char_type="mob",
        is_elite=getattr(mob, "is_elite", False),
        is_invisible=getattr(mob, "is_invisible", False),
        actions=actions,
    )


def collect_map_room_ids(
    player: Player, room_world: World, current_room: Optional[Room]
) -> Tuple[set[int], Optional[Room]]:
    """Return a set of room PKs to include on the minimap."""
    room_ids: set[int] = set()
    starting_room = None

    if current_room:
        room_ids.add(current_room.id)

    world_config = player.world.config or room_world.config
    if world_config and world_config.starting_room_id:
        starting_room = world_config.starting_room
        if starting_room and starting_room.world_id == room_world.id:
            room_ids.add(starting_room.id)

    visited_ids = player.viewed_rooms.filter(world=room_world).values_list("id", flat=True)
    room_ids.update(visited_ids)

    landmark_ids = room_world.rooms.filter(is_landmark=True).values_list("id", flat=True)
    room_ids.update(landmark_ids)

    return room_ids, starting_room


def room_payload_key(room_id: int, relative_id: Optional[int]) -> str:
    """
    Canonical room key for client payloads.
    Prefer relative IDs so keys are stable across spawned/world copies.
    """
    if relative_id is not None:
        return f"room.{relative_id}"
    return f"room.{room_id}"


def room_payload_key_for(room: Room) -> str:
    return room_payload_key(room.id, room.relative_id)


def room_payload_key_from_id(room_id: Optional[int]) -> Optional[str]:
    if not room_id:
        return None
    try:
        room = Room.objects.only("id", "relative_id").get(id=room_id)
    except Room.DoesNotExist:
        return None
    return room_payload_key(room.id, room.relative_id)


def door_state_lookup(world: World, room_ids: Iterable[int]) -> Dict[int, Dict[str, str]]:
    room_ids = list(room_ids)
    if not room_ids:
        return {}

    door_states = {
        state.door_id: state.state
        for state in DoorState.objects.filter(world=world, door__from_room_id__in=room_ids).select_related(
            "door"
        )
    }
    lookup: Dict[int, Dict[str, str]] = {}
    for door in Door.objects.filter(from_room_id__in=room_ids).values(
        "id", "from_room_id", "direction", "default_state"
    ):
        state = door_states.get(door["id"], door["default_state"])
        lookup.setdefault(door["from_room_id"], {})[door["direction"]] = state
    return lookup


def build_map_payload(
    room_world: World, room_ids: Iterable[int], door_states: Dict[int, Dict[str, str]]
) -> Tuple[List[MapRoom], Dict[int, str]]:
    """
    Build the minimap payload. Returns the serialized rooms and a mapping of
    room PK -> room key for exit lookups.
    """
    rooms = list(
        room_world.rooms.filter(id__in=room_ids).values(
            "id",
            "relative_id",
            "x",
            "y",
            "z",
            "type",
            "color",
            "north_id",
            "east_id",
            "south_id",
            "west_id",
            "up_id",
            "down_id",
        )
    )
    if not rooms:
        return [], {}

    id_to_key = {
        room["id"]: room_payload_key(room["id"], room["relative_id"]) for room in rooms
    }

    map_rooms: List[MapRoom] = []
    for room in rooms:
        room_id = room["id"]
        ds = door_states.get(room_id, {})
        map_rooms.append(
            MapRoom(
                key=id_to_key[room_id],
                x=room["x"],
                y=room["y"],
                z=room["z"],
                type=room["type"] or "road",
                color=room["color"],
                north=id_to_key.get(room["north_id"]),
                east=id_to_key.get(room["east_id"]),
                south=id_to_key.get(room["south_id"]),
                west=id_to_key.get(room["west_id"]),
                up=id_to_key.get(room["up_id"]),
                down=id_to_key.get(room["down_id"]),
                north_door_state=ds.get("north"),
                east_door_state=ds.get("east"),
                south_door_state=ds.get("south"),
                west_door_state=ds.get("west"),
                up_door_state=ds.get("up"),
                down_door_state=ds.get("down"),
            )
        )
    return map_rooms, id_to_key


def serialize_room(
    room: Optional[Room],
    room_key_lookup: Dict[int, str],
    door_states: Dict[int, Dict[str, str]],
    *,
    viewer: Player | Mob | None = None,
) -> RoomSchema:
    if room is None:
        return RoomSchema(
            id=None,
            key="room.unknown",
            name="Unknown Room",
            description="Room data is unavailable.",
        )

    room_inventory = serialize_inventory(
        room.inventory.filter(is_pending_deletion=False).select_related("template", "currency"),
        viewer=viewer,
    )

    room_players = room.players.filter(in_game=True).select_related("user", "equipment")
    room_mobs = room.mobs.select_related("template")

    chars: List[Char] = []
    chars.extend(serialize_char_from_player(p) for p in room_players)
    chars.extend(serialize_char_from_mob(m, viewer=viewer) for m in room_mobs)

    zone = ZoneSchema(key=room.zone.key, name=room.zone.name) if room.zone else None
    details = list(room.details.filter(is_hidden=False).values_list("description", flat=True))
    flags = list(room.flags.values_list("code", flat=True))
    actions = get_room_action_labels_for_actor(viewer, room)
    ds = door_states.get(room.id, {})

    def _exit_key(room_id: Optional[int]) -> Optional[str]:
        if not room_id:
            return None
        if room_id in room_key_lookup:
            return room_key_lookup[room_id]
        return room_payload_key_from_id(room_id)

    return RoomSchema(
        id=room.id,
        key=room_key_lookup.get(room.id, room_payload_key_for(room)),
        name=room.name,
        description=room.description or "",
        color=room.color,
        inventory=room_inventory,
        chars=chars,
        actions=actions,
        x=room.x,
        y=room.y,
        z=room.z,
        type=room.type,
        zone=zone,
        hint=None,
        houses=[],
        details=details,
        flags=flags,
        north=_exit_key(room.north_id),
        east=_exit_key(room.east_id),
        south=_exit_key(room.south_id),
        west=_exit_key(room.west_id),
        up=_exit_key(room.up_id),
        down=_exit_key(room.down_id),
        north_door_state=ds.get("north"),
        east_door_state=ds.get("east"),
        south_door_state=ds.get("south"),
        west_door_state=ds.get("west"),
        up_door_state=ds.get("up"),
        down_door_state=ds.get("down"),
    )


def serialize_actor(player: Player, room: Optional[Room]) -> Actor:
    if room:
        player.room = room
    try:
        actor_data = AnimatePlayerSerializer(player).data
    except AttributeError:
        actor_data = {
            "id": player.id,
            "key": player.key,
            "name": player.name,
            "title": player.title,
            "level": player.level,
            "gender": player.gender or "male",
            "description": player.description,
            "factions": getattr(player, "factions", {}) or {},
            "room": None,
        }
    actor_data.update(computed_player_vitals(player))
    actor_data["room"] = {"key": room_payload_key_for(room)} if room else None
    actor_data["equipment"] = serialize_equipment(player.equipment, viewer=player)
    actor_data["inventory"] = serialize_inventory(player.inventory.all(), viewer=player)
    return Actor(**actor_data)


def serialize_world(world: World) -> Dict:
    """
    Use the existing animation serializer when possible, with a light
    fallback for root worlds that do not have a context.
    """
    if world.context_id:
        data = AnimateWorldSerializer(world).data
    else:
        config = world.config
        data = {
            "id": world.id,
            "key": world.key,
            "name": world.name,
            "context": world.key,
            "instance_of": None,
            "instance_ref": world.instance_ref,
            "is_multiplayer": world.is_multiplayer,
            "never_reload": config.never_reload if config else False,
            "starting_room": room_payload_key_from_id(config.starting_room_id) if config else None,
            "death_room": room_payload_key_from_id(config.death_room_id) if config else None,
            "death_gold_penalty": config.death_gold_penalty if config else 0.0,
            "has_corpse_decay": config.has_corpse_decay if config else True,
            "auto_equip": config.auto_equip if config else True,
            "globals_enabled": config.globals_enabled if config else False,
            "factions": {},
            "death_mode": config.death_mode if config else "flee",
            "skills": {},
            "flee_to_unknown_rooms": config.flee_to_unknown_rooms if config else False,
            "death_route": config.death_route if config else "",
            "allow_pvp": config.allow_pvp if config else False,
            "allow_combat": config.allow_combat if config else True,
            "players_can_set_title": config.players_can_set_title if config else False,
            "facts": json.loads(world.facts or "{}"),
            "classless": config.is_classless if config else False,
            "tier": world.tier,
            "socials": {"cmds": {}, "order": []},
            "currencies": {},
            "leader": world.leader.key if world.leader else None,
        }

    if data.get("currencies"):
        data["currencies"] = {str(k): v for k, v in data["currencies"].items()}

    # Normalize world-config room references to the same room key contract used
    # across WR2 room/map payloads.
    config = world.config
    if config:
        data["starting_room"] = room_payload_key_from_id(config.starting_room_id)
        data["death_room"] = room_payload_key_from_id(config.death_room_id)

    if not data.get("context"):
        data["context"] = world.context.key if world.context else world.key

    return data


def build_who_list(world: World, actor: Player) -> List[WhoListEntry]:
    idle_cutoff = timezone.now() - timedelta(seconds=adv_consts.IDLE_THRESHOLD)
    qs = (
        Player.objects.filter(world=world, in_game=True)
        .select_related("user")
        .prefetch_related("faction_assignments__faction", "clan_memberships__clan")
    )
    who_list: List[WhoListEntry] = []
    actor_is_immortal = getattr(actor, "is_immortal", False)
    actor_core = (actor.factions or {}).get("core")

    for player in qs:
        if player.is_invisible and not actor_is_immortal:
            continue

        player_core = (player.factions or {}).get("core")
        if (
            not actor_is_immortal
            and not player.is_immortal
            and actor_core
            and player_core
            and actor_core != player_core
        ):
            continue

        who_list.append(
            WhoListEntry(
                key=player.key,
                name=player.name,
                title=player.title,
                level=player.level,
                gender=player.gender or "male",
                is_immortal=player.is_immortal,
                is_invisible=player.is_invisible,
                is_idle=(not player.last_action_ts or player.last_action_ts <= idle_cutoff),
                is_linkless=False,
                display_faction=player.display_faction or None,
                clan=player.clan,
            )
        )

    return who_list


# ---- Aggregates ----

def build_state_sync(player: Player) -> StateSyncData:
    world = player.world
    room = player.room
    if room is None:
        if world.config and world.config.starting_room:
            room = world.config.starting_room
        elif world.context and world.context.config:
            room = world.context.config.starting_room

    room_world = room.world if room else (world.context or world)
    room_ids: set[int] = set()
    door_states: Dict[int, Dict[str, str]] = {}
    if room_world:
        room_ids, _ = collect_map_room_ids(player, room_world, room)
        door_states = door_state_lookup(world, room_ids)

    map_rooms, room_key_lookup = (
        build_map_payload(room_world, room_ids, door_states) if room_world else ([], {})
    )
    room_payload = serialize_room(room, room_key_lookup, door_states, viewer=player)
    actor_payload = serialize_actor(player, room)
    world_payload = serialize_world(world)
    who_list = build_who_list(world, player)

    return StateSyncData(
        map=map_rooms,
        actor=actor_payload,
        room=room_payload,
        world=world_payload,
        who_list=who_list,
    )
