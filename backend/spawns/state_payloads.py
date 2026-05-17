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

from builders.models import AbilityDefinition
from config import constants as adv_consts
from core.scoped_state import STATE_SCOPE_WORLD, get_state_snapshot
from core.leveling import (
    get_world_leveling_config,
    progress_for_experience,
)
from core.stat_system import (
    build_player_stat_payload,
    get_world_label_bundle,
    world_uses_classes,
)
from quests.services.interactions import room_mob_quest_indicator_map, room_quest_callouts
from quests.services.room_items import serialized_quest_room_items_for_room
from spawns.models import DoorState, Item, Mob, Player
from spawns.schemas import (
    Actor,
    Char,
    Equipment as EquipmentSchema,
    Item as ItemSchema,
    MapRoom,
    QuestData,
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


def first_keyword(value: Optional[str], fallback: Optional[str] = None) -> str:
    tokens = [token for token in str(value or "").split(" ") if token]
    if tokens:
        return tokens[0]
    return str(fallback or "").strip().lower()


def _definition_world(world: World) -> World:
    return getattr(world, "config_source_world", None) or getattr(world, "context", None) or world


def _known_ability_slugs(player: Player) -> list[str]:
    if not isinstance(player.known_abilities, list):
        return []
    known: list[str] = []
    for raw_slug in player.known_abilities:
        slug = str(raw_slug or "").strip().lower()
        if slug and slug not in known:
            known.append(slug)
    return known


def _ability_hotkeys(player: Player) -> dict[str, str]:
    if not isinstance(player.ability_hotkeys, dict):
        return {}
    hotkeys: dict[str, str] = {}
    assigned_slugs: set[str] = set()
    for raw_slot, raw_slug in player.ability_hotkeys.items():
        try:
            slot_number = int(raw_slot)
        except (TypeError, ValueError):
            continue
        if slot_number < 1 or slot_number > 8:
            continue
        slug = str(raw_slug or "").strip().lower()
        if not slug or slug in assigned_slugs:
            continue
        hotkeys[str(slot_number)] = slug
        assigned_slugs.add(slug)
    return hotkeys


def _ability_cooldowns(player: Player) -> dict[str, int]:
    if not isinstance(player.ability_cooldowns, dict):
        return {}
    cooldowns: dict[str, int] = {}
    for raw_slug, raw_rounds in player.ability_cooldowns.items():
        slug = str(raw_slug or "").strip().lower()
        if not slug:
            continue
        try:
            rounds = int(raw_rounds or 0)
        except (TypeError, ValueError):
            rounds = 0
        if rounds > 0:
            cooldowns[slug] = rounds
    return cooldowns


def _serialize_ability_definitions(world: World) -> dict[str, dict]:
    source_world = _definition_world(world)
    definitions: dict[str, dict] = {}
    order: list[str] = []
    for ability in AbilityDefinition.objects.filter(
        world=source_world,
        is_active=True,
    ).order_by("slug", "id"):
        order.append(ability.slug)
        definitions[ability.slug] = {
            "id": ability.id,
            "key": f"ability.{ability.id}",
            "slug": ability.slug,
            "name": ability.name or ability.slug,
            "command_verbs": list(ability.command_verbs or []),
            "action_type": ability.action_type,
            "target": ability.target or {},
            "cost": ability.cost or {},
            "cooldown": ability.cooldown or {},
        }
    return {
        "definitions": definitions,
        "order": order,
    }


def get_player_with_related(player_id: int) -> Player:
    """
    Reload the player with the relations we need for serialization to keep
    query counts low.
    """
    inventory_qs = Item.objects.select_related("definition", "template", "currency")
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
    Prefer authored names for definition/template-backed items when instance name is empty
    or still the legacy default placeholder.
    """
    instance_name = (item.name or "").strip()
    definition_name = (item.definition.name if item.definition else "") or ""
    template_name = (item.template.name if item.template else "") or ""
    if definition_name and (
        not instance_name
        or instance_name.lower() == "unnamed item"
    ):
        return definition_name
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


def item_stack_key(item: Item, *, item_type: str | None = None) -> str | None:
    is_container = item_type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    )
    if is_container:
        return None
    if getattr(item, "upgrade_count", 0) or getattr(item, "augment_id", None):
        return None

    if item.definition_id:
        roll_metadata = item.roll_metadata if isinstance(item.roll_metadata, dict) else {}
        if roll_metadata.get("randomized"):
            return None
        definition_slug = (
            item.definition.slug
            if item.definition
            else item.definition_slug_snapshot
        )
        revision = roll_metadata.get("rolled_at_definition_modified_ts") or ""
        if revision:
            return f"definition:{definition_slug or item.definition_id}:{revision}"
        return f"definition:{definition_slug or item.definition_id}"

    if item.template_id:
        return f"template:{item.template_id}"

    return None


def serialize_item(
    item: Item,
    *,
    viewer: Player | Mob | None = None,
    include_inventory: bool = False,
) -> ItemSchema:
    """Serialize an item into the WR2 Item schema."""
    name = resolve_item_name(item)
    currency = item.currency.code if item.currency else "gold"
    description = item.description
    if not description and item.definition:
        description = item.definition.description
    if not description and item.template:
        description = item.template.description
    armor_value = getattr(item, "armor", None)
    if armor_value is None and item.template:
        armor_value = getattr(item.template, "armor", 0)
    if armor_value is None:
        armor_value = 0
    actions = get_item_action_labels_for_actor(viewer, item)
    keywords = item.keywords or ""
    if not keywords and item.definition:
        keywords = item.definition.keywords or ""
    if not keywords and item.template:
        keywords = item.template.keywords or ""
    if not keywords:
        keywords = name.lower()
    item_type = item.type or (
        item.definition.item_type if item.definition else None
    ) or (item.template.type if item.template else None)
    stack_key = item_stack_key(item, item_type=item_type)
    inventory = []
    if include_inventory and item_type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    ):
        inventory = serialize_inventory(
            item.inventory.filter(is_pending_deletion=False)
            .select_related("definition", "template", "currency")
            .order_by("id"),
            viewer=viewer,
            include_inventory=True,
        )

    return ItemSchema(
        key=item.key,
        name=name,
        cf_name=safe_capitalize(name),
        type=item_type,
        armor_class=item.armor_class,
        description=description,
        ground_description=(
            item.ground_description
            or (item.definition.ground_description if item.definition else None)
            or (item.template.ground_description if item.template else None)
        ),
        level=item.level,
        quality=item.quality,
        is_magic=getattr(item, "is_magic", False),
        equipment_type=item.equipment_type,
        template_id=item.template_id,
        definition_id=item.definition_id,
        definition_slug=(
            item.definition.slug
            if item.definition
            else item.definition_slug_snapshot or None
        ),
        stack_key=stack_key,
        is_stackable=bool(stack_key),
        input_attributes=item.input_attributes or {},
        attack_power=item.attack_power,
        spell_power=item.spell_power,
        ability_power=item.spell_power,
        weapon_damage=item.weapon_damage,
        armor=armor_value,
        crit=item.crit,
        resilience=item.resilience,
        dodge=item.dodge,
        health_max=item.health_max,
        health_regen=item.health_regen,
        mana_max=item.mana_max,
        mana_regen=item.mana_regen,
        energy_max=item.mana_max,
        energy_regen=item.mana_regen,
        stamina_max=item.stamina_max,
        stamina_regen=item.stamina_regen,
        is_pickable=item.is_pickable,
        cost=item.cost,
        currency=currency,
        keywords=keywords,
        keyword=first_keyword(keywords, name),
        label=item.label,
        upgrade_count=item.upgrade_count,
        weapon_type=item.weapon_type,
        is_container=item_type in (
            adv_consts.ITEM_TYPE_CONTAINER,
            adv_consts.ITEM_TYPE_CORPSE,
            adv_consts.ITEM_TYPE_TRASH,
        ),
        inventory=inventory,
        actions=actions,
    )


def serialize_inventory(
    items: Iterable[Item],
    *,
    viewer: Player | Mob | None = None,
    include_inventory: bool = False,
) -> List[ItemSchema]:
    return [
        serialize_item(
            item,
            viewer=viewer,
            include_inventory=include_inventory,
        )
        for item in items
    ]


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


def serialize_char_from_player(
    player: Player,
    *,
    viewer: Player | Mob | None = None,
    include_equipment: bool = False,
) -> Char:
    keywords = getattr(player, "keywords", "") or f"{player.name.lower()} player {player.key}"
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
        keywords=keywords,
        keyword=first_keyword(keywords, player.name),
        char_type="player",
        display_faction=player.display_faction or None,
        equipment=serialize_equipment(player.equipment, viewer=viewer) if include_equipment else None,
    )


def serialize_char_from_mob(
    mob: Mob,
    *,
    viewer: Player | Mob | None = None,
    quest_indicator_map: dict[int, dict[str, bool]] | None = None,
    include_equipment: bool = False,
) -> Char:
    name = mob.name or (mob.template.name if mob.template else "Unnamed Mob")
    keywords = mob.keywords or name.lower()
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
    quest_indicator = (quest_indicator_map or {}).get(mob.id, {})
    return Char(
        id=mob.id,
        key=mob.key,
        name=name,
        title=title,
        description=description,
        archetype=mob.archetype,
        core_faction=(factions or {}).get("core"),
        room_description=safe_capitalize(room_desc or (name + " is here.")),
        state="standing",
        stance="normal",
        health=mob.health,
        health_max=getattr(mob, "health_max", mob.health),
        mana=mob.mana,
        level=mob.level,
        gender=mob.gender or "male",
        keywords=keywords,
        keyword=first_keyword(keywords, name),
        template_id=mob.template_id,
        char_type="mob",
        is_elite=getattr(mob, "is_elite", False),
        is_invisible=getattr(mob, "is_invisible", False),
        equipment=serialize_equipment(mob.equipment, viewer=viewer) if include_equipment else None,
        actions=actions,
        quest_data=QuestData(
            enquire=bool(quest_indicator.get("enquire")),
            complete=bool(quest_indicator.get("complete")),
        ),
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
    if isinstance(viewer, Player):
        room_inventory.extend(
            ItemSchema(**payload)
            for payload in serialized_quest_room_items_for_room(viewer, room.id)
        )

    room_players = room.players.filter(in_game=True).select_related("user", "equipment")
    room_mobs = list(room.mobs.select_related("template"))
    quest_indicator_map: dict[int, dict[str, bool]] = {}
    quest_callout_data: list[dict] = []
    if isinstance(viewer, Player):
        quest_indicator_map = room_mob_quest_indicator_map(viewer, room_mobs)
        quest_callout_data = room_quest_callouts(viewer, room.id)

    chars: List[Char] = []
    chars.extend(serialize_char_from_player(p) for p in room_players)
    chars.extend(
        serialize_char_from_mob(
            m,
            viewer=viewer,
            quest_indicator_map=quest_indicator_map,
        )
        for m in room_mobs
    )

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
        quest_callouts=quest_callout_data,
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
    actor_data.update(build_player_stat_payload(player))
    leveling_config = get_world_leveling_config(player.world)
    progress = progress_for_experience(
        getattr(player, "experience", 0),
        level=getattr(player, "level", 1),
        config_obj=leveling_config,
    )
    actor_data.update(
        {
            "experience": int(getattr(player, "experience", 0) or 0),
            "experience_progress": progress.experience_progress,
            "experience_needed": progress.experience_needed,
            "known_abilities": _known_ability_slugs(player),
            "ability_hotkeys": _ability_hotkeys(player),
            "ability_cooldowns": _ability_cooldowns(player),
        }
    )
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
            "starting_level": int(config.starting_level) if config else 1,
            "max_level": int(config.max_level) if config else 20,
            "leveling_curve": list(config.leveling_curve or []) if config else [],
            "combat_resolution_interval": float(config.combat_resolution_interval) if config else 0.0,
            "death_gold_penalty": config.death_gold_penalty if config else 0.0,
            "has_corpse_decay": config.has_corpse_decay if config else True,
            "auto_equip": config.auto_equip if config else True,
            "globals_enabled": config.globals_enabled if config else False,
            "factions": {},
            "death_mode": config.death_mode if config else "flee",
            "abilities": {},
            "flee_to_unknown_rooms": config.flee_to_unknown_rooms if config else False,
            "death_route": config.death_route if config else "",
            "allow_pvp": config.allow_pvp if config else False,
            "allow_combat": config.allow_combat if config else True,
            "players_can_set_title": config.players_can_set_title if config else False,
            "facts": get_state_snapshot(STATE_SCOPE_WORLD, world),
            "classless": not world_uses_classes(world) if config else False,
            "tier": world.tier,
            "socials": {"cmds": {}, "order": []},
            "currencies": {},
            "leader": world.leader.key if world.leader else None,
        }

    if data.get("currencies"):
        data["currencies"] = {str(k): v for k, v in data["currencies"].items()}

    data["labels"] = get_world_label_bundle(world)
    data["abilities"] = _serialize_ability_definitions(world)

    # Normalize world-config room references to the same room key contract used
    # across WR2 room/map payloads.
    config = world.config
    if config:
        data["starting_room"] = room_payload_key_from_id(config.starting_room_id)
        data["death_room"] = room_payload_key_from_id(config.death_room_id)
        data["starting_level"] = int(config.starting_level)
        data["max_level"] = int(config.max_level)
        data["leveling_curve"] = list(config.leveling_curve or [])
        data["combat_resolution_interval"] = float(config.combat_resolution_interval)

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
