"""
Shared builders for state.sync-style payloads.

These helpers are reused by handlers (state.sync, look, etc.) to build
StateSyncData-compatible structures without duplicating query/serialization
logic.
"""
import json
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from django.db.models import F, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from builders.models import AbilityDefinition, CraftMaterial, ItemSalvageYield
from config import constants as adv_consts
from core.abilities import ability_allows_actor, definition_world
from core.combat_formulas import get_world_combat_system, rating_display_percent
from core.equipment_system import get_world_equipment_payload
from core.economy import money_payload
from core.scoped_state import STATE_SCOPE_WORLD, get_state_snapshot
from core.leveling import (
    get_world_leveling_config,
    progress_for_experience,
)
from core.stat_system import (
    build_player_stat_payload,
    get_world_class_selection,
    get_world_label_bundle,
    world_uses_classes,
)
from core.world_config import inherited_system_config
from quests.services.interactions import room_mob_quest_indicator_map, room_quest_callouts
from quests.services.room_items import serialized_quest_room_items_for_room
from spawns.actions.effects import active_character_effects, active_combat_effects
from spawns.ability_prepare_state import active_prepared_ability_slugs
from spawns.item_querysets import with_item_salvageability
from spawns.models import (
    DoorState,
    Item,
    Mob,
    Player,
    PlayerCurrencyBalance,
    PlayerMaterialBalance,
)
from spawns.schemas import (
    Actor,
    Char,
    Equipment as EquipmentSchema,
    Item as ItemSchema,
    MapRoom,
    MerchantProvider as MerchantProviderSchema,
    QuestIndicator,
    Room as RoomSchema,
    StateSyncData,
    TrainingProvider as TrainingProviderSchema,
    WhoListEntry,
    Zone as ZoneSchema,
)
from spawns.serializers import (
    AnimatePlayerSerializer,
    AnimateWorldSerializer,
    player_economy_payload,
    world_economy_payload,
)
from worlds.models import Door, Room, World


_CORE_FACTION_UNSET = object()
_SALVAGEABILITY_UNSET = object()


# ---- Utilities ----

def player_state(player: Player) -> str:
    state = str(getattr(player, "state", "") or "").strip().lower()
    if state in {"standing", "resting", "combat"}:
        return state
    return "standing"


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
    return definition_world(world)


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
        if not ability_allows_actor(ability, "player"):
            continue
        order.append(ability.slug)
        definitions[ability.slug] = {
            "id": ability.id,
            "key": f"ability.{ability.id}",
            "slug": ability.slug,
            "name": ability.name or ability.slug,
            "command_verbs": list(ability.command_verbs or []),
            "consumes_primary_action_on_resolve": bool(
                ability.consumes_primary_action_on_resolve
            ),
            "consumes_primary_action_while_casting": bool(
                ability.consumes_primary_action_while_casting
            ),
            "target": ability.target or {},
            "cost": ability.cost or {},
            "cast_time": ability.cast_time or {},
            "cooldown": ability.cooldown or {},
            "help": ability.help or {},
        }
    return {
        "definitions": definitions,
        "order": order,
    }


def _round_percent(value: float) -> float | int:
    rounded = round(float(value or 0.0), 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _serialize_combat_system(world: World) -> dict[str, dict]:
    combat_system = get_world_combat_system(world)
    ratings = {}
    for key, rating in (combat_system.get("ratings") or {}).items():
        rating_payload = {
            "stat": rating["stat"],
            "type": rating["type"],
            "base": rating["base"],
            "cap": rating["cap"],
        }
        if "constant" in rating:
            rating_payload["constant"] = rating["constant"]
        ratings[key] = rating_payload
    return {"ratings": ratings}


def _combat_rating_percentages(world: World, level: int, stats: dict[str, int]) -> dict[str, float | int]:
    combat_system = get_world_combat_system(world)
    payload = {}
    for rating_key in ("armor", "crit", "dodge", "resilience"):
        rating_config = (combat_system.get("ratings") or {}).get(rating_key)
        if not rating_config:
            continue
        stat_key = rating_config["stat"]
        payload[f"{rating_key}_perc"] = _round_percent(
            rating_display_percent(
                rating_config=rating_config,
                rating=float(stats.get(stat_key) or 0),
                opponent_level=level,
                combat_system=combat_system,
            )
        )
    return payload


def get_player_with_related(player_id: int) -> Player:
    """
    Reload the player with the relations we need for serialization to keep
    query counts low.
    """
    inventory_qs = with_item_salvageability(
        Item.objects.select_related("definition", "currency")
    )
    material_balance_qs = PlayerMaterialBalance.objects.select_related(
        "material"
    ).filter(quantity__gt=0).order_by(
        "material__order",
        "material__name",
        "material_id",
    )
    currency_balance_qs = PlayerCurrencyBalance.objects.select_related(
        "currency"
    ).filter(amount__gt=0).order_by("currency__code", "currency_id")
    return (
        Player.objects.select_related(
            "world",
            "world__default_currency",
            "world__config",
            "world__config__death_currency",
            "world__context",
            "world__context__default_currency",
            "world__context__config",
            "world__context__config__death_currency",
            "world__context__instance_of",
            "world__context__instance_of__config",
            "world__context__instance_of__default_currency",
            "world__instance_of",
            "world__instance_of__default_currency",
            "room",
            "room__merchant_profile",
            "room__trainer_profile",
            "user",
            "config",
            "equipment",
            "core_faction",
        )
        .prefetch_related(
            "aliases",
            "marks",
            "faction_assignments__faction",
            "clan_memberships__clan",
            Prefetch("inventory", queryset=inventory_qs),
            Prefetch("material_balances", queryset=material_balance_qs),
            Prefetch("currency_balances", queryset=currency_balance_qs),
        )
        .get(pk=player_id)
    )


# ---- Serialization helpers ----

def resolve_item_name(item: Item) -> str:
    """
    Prefer authored names for definition-backed items when instance name is empty
    or still the legacy default placeholder.
    """
    instance_name = (item.name or "").strip()
    definition_name = (item.definition.name if item.definition else "") or ""
    if definition_name and (
        not instance_name
        or instance_name.lower() == "unnamed item"
    ):
        return definition_name
    if instance_name:
        return instance_name
    return "Unnamed item"


def item_stack_key(item: Item, *, item_type: str | None = None) -> str | None:
    is_container = item_type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    )
    if is_container:
        return None
    if getattr(item, "augment_id", None):
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

    return None


def serialize_item(
    item: Item,
    *,
    viewer: Player | Mob | None = None,
    include_inventory: bool = False,
    salvageable_definition_ids: set[int] | None = None,
) -> ItemSchema:
    """Serialize an item into the WR2 Item schema."""
    from spawns.triggers import get_item_action_labels_for_actor

    name = resolve_item_name(item)
    description = item.description
    if not description and item.definition:
        description = item.definition.description
    armor_value = getattr(item, "armor", None)
    if armor_value is None:
        armor_value = 0
    actions = get_item_action_labels_for_actor(viewer, item)
    keywords = item.keywords or ""
    if not keywords and item.definition:
        keywords = item.definition.keywords or ""
    if not keywords:
        keywords = name.lower()
    item_type = item.type or (
        item.definition.item_type if item.definition else None
    )
    stack_key = item_stack_key(item, item_type=item_type)
    if salvageable_definition_ids is None:
        annotated_salvageability = getattr(
            item,
            "_payload_is_salvageable",
            _SALVAGEABILITY_UNSET,
        )
        if annotated_salvageability is _SALVAGEABILITY_UNSET:
            is_salvageable = bool(
                item.definition_id
                and ItemSalvageYield.objects.filter(
                    item_definition_id=item.definition_id,
                ).exists()
            )
        else:
            is_salvageable = bool(annotated_salvageability)
    else:
        is_salvageable = item.definition_id in salvageable_definition_ids
    inventory = []
    if include_inventory and item_type in (
        adv_consts.ITEM_TYPE_CONTAINER,
        adv_consts.ITEM_TYPE_CORPSE,
        adv_consts.ITEM_TYPE_TRASH,
    ):
        inventory = serialize_inventory(
            with_item_salvageability(
                item.inventory.filter(is_pending_deletion=False)
                .select_related("definition", "currency")
            ).order_by("id"),
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
        room_description=(
            item.room_description
            or (item.definition.room_description if item.definition else None)
        ),
        level=item.level,
        quality=item.quality,
        is_magic=getattr(item, "is_magic", False),
        equipment_type=item.equipment_type,
        definition_id=item.definition_id,
        definition_slug=(
            item.definition.slug
            if item.definition
            else item.definition_slug_snapshot or None
        ),
        stack_key=stack_key,
        is_stackable=bool(stack_key),
        attributes=item.attributes or {},
        attack_power=item.attack_power,
        ability_power=item.ability_power,
        weapon_damage=item.weapon_damage,
        armor=armor_value,
        crit=item.crit,
        resilience=item.resilience,
        dodge=item.dodge,
        health_max=item.health_max,
        health_regen=item.health_regen,
        energy_max=item.energy_max,
        energy_regen=item.energy_regen,
        stamina_max=item.stamina_max,
        stamina_regen=item.stamina_regen,
        is_pickable=item.is_pickable,
        is_salvageable=is_salvageable,
        value=(
            money_payload(int(item.cost), item.currency)
            if item.cost is not None and item.currency is not None
            else None
        ),
        keywords=keywords,
        keyword=first_keyword(keywords, name),
        label=item.label,
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
    item_list = list(items)
    salvageable_definition_ids: set[int] = set()
    unresolved_definition_ids: set[int] = set()
    for item in item_list:
        if not item.definition_id:
            continue
        annotated_salvageability = getattr(
            item,
            "_payload_is_salvageable",
            _SALVAGEABILITY_UNSET,
        )
        if annotated_salvageability is _SALVAGEABILITY_UNSET:
            unresolved_definition_ids.add(item.definition_id)
        elif annotated_salvageability:
            salvageable_definition_ids.add(item.definition_id)

    if unresolved_definition_ids:
        salvageable_definition_ids.update(
            ItemSalvageYield.objects.filter(
                item_definition_id__in=unresolved_definition_ids,
            ).values_list("item_definition_id", flat=True)
        )

    return [
        serialize_item(
            item,
            viewer=viewer,
            include_inventory=include_inventory,
            salvageable_definition_ids=salvageable_definition_ids,
        )
        for item in item_list
    ]


def serialize_equipment(equipment, *, viewer: Player | Mob | None = None) -> EquipmentSchema:
    if not equipment:
        return EquipmentSchema()

    slot_items = [
        (slot, eq_item)
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
        )
        if (eq_item := getattr(equipment, slot, None))
    ]
    salvageable_definition_ids = set(
        ItemSalvageYield.objects.filter(
            item_definition_id__in={
                item.definition_id
                for _, item in slot_items
                if item.definition_id
            },
        ).values_list("item_definition_id", flat=True)
    )
    slots = {
        slot: serialize_item(
            item,
            viewer=viewer,
            salvageable_definition_ids=salvageable_definition_ids,
        )
        for slot, item in slot_items
    }
    return EquipmentSchema(**slots)


def serialize_char_from_player(
    player: Player,
    *,
    viewer: Player | Mob | None = None,
    include_equipment: bool = False,
) -> Char:
    keywords = getattr(player, "keywords", "") or f"{player.name.lower()} player {player.key}"
    stat_payload = build_player_stat_payload(player)
    return Char(
        id=player.id,
        key=player.key,
        name=player.name,
        title=player.title,
        description=player.description,
        archetype=player.archetype,
        core_faction=(player.factions or {}).get("core"),
        room_description=safe_capitalize(player.name) + " is here.",
        state=player_state(player),
        stance="normal",
        health=player.health,
        health_max=int(
            stat_payload.get("health_max")
            or getattr(player, "health_max", player.health)
            or 1
        ),
        energy=player.energy,
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
    core_faction_override: str | None | object = _CORE_FACTION_UNSET,
) -> Char:
    from spawns.triggers import get_char_action_labels_for_actor

    name = (
        mob.name
        or (mob.definition.name if mob.definition else "")
        or "Unnamed Mob"
    )
    keywords = mob.keywords or ""
    if not keywords and mob.definition:
        keywords = mob.definition.keywords or ""
    if not keywords:
        keywords = name.lower()
    title = mob.title
    description = mob.description
    if not description and mob.definition:
        description = mob.definition.description
    room_desc = mob.room_description
    if not room_desc and mob.definition:
        room_desc = mob.definition.room_description
    factions = (
        mob.factions
        if core_faction_override is _CORE_FACTION_UNSET
        else (
            {"core": str(core_faction_override)}
            if core_faction_override
            else {}
        )
    )
    actions = get_char_action_labels_for_actor(viewer, mob)
    is_trainer = bool(
        not mob.is_pending_deletion
        and
        mob.definition
        and mob.definition.trainer_profile_id
        and (
            mob.definition.trainer_availability != "alive_and_present"
            or int(mob.health or 0) > 0
        )
    )
    if isinstance(viewer, Player) and is_trainer:
        normalized_actions = {action.casefold() for action in actions}
        for action in ("learn", "unlearn"):
            if action not in normalized_actions:
                actions.append(action)
                normalized_actions.add(action)
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
        energy=mob.energy,
        level=mob.level,
        gender=mob.gender or "male",
        keywords=keywords,
        keyword=first_keyword(keywords, name),
        definition_id=mob.definition_id,
        definition_slug=(
            mob.definition.slug
            if mob.definition
            else mob.definition_slug_snapshot or None
        ),
        char_type="mob",
        is_elite=getattr(mob, "is_elite", False),
        is_invisible=getattr(mob, "is_invisible", False),
        is_merchant=bool(mob.definition and mob.definition.merchant_profile_id),
        is_trainer=is_trainer,
        attackable=getattr(mob, "attackable", True),
        equipment=serialize_equipment(mob.equipment, viewer=viewer) if include_equipment else None,
        actions=actions,
        quest_indicator=QuestIndicator(
            available=bool(quest_indicator.get("available")),
            ready=bool(quest_indicator.get("ready")),
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

    doors = list(
        Door.objects.filter(from_room_id__in=room_ids).values(
            "from_room_id",
            "direction",
            "doorway_id",
            "doorway__default_state",
        )
    )
    doorway_ids = {door["doorway_id"] for door in doors}
    door_states = dict(
        DoorState.objects.filter(
            world=world,
            doorway_id__in=doorway_ids,
        ).values_list("doorway_id", "state")
    )
    lookup: Dict[int, Dict[str, str]] = {}
    for door in doors:
        state = door_states.get(
            door["doorway_id"],
            door["doorway__default_state"],
        )
        lookup.setdefault(door["from_room_id"], {})[door["direction"]] = state
    return lookup


def directional_door_payload(
    world: World,
    room_id: int,
    direction: str,
) -> Optional[dict]:
    """Return one room-facing door and its effective state without materializing it."""
    if not room_id or direction not in adv_consts.DIRECTIONS:
        return None

    authored_world_id = world.context_id
    if authored_world_id is None:
        return None

    runtime_state = (
        DoorState.objects.filter(
            world_id=world.id,
            doorway_id=OuterRef("doorway_id"),
        )
        .values("state")[:1]
    )
    door = (
        Door.objects.filter(
            from_room_id=room_id,
            direction=direction,
            doorway__world_id=authored_world_id,
        )
        .annotate(
            effective_state=Coalesce(
                Subquery(runtime_state),
                F("doorway__default_state"),
            )
        )
        .values(
            "id",
            "name",
            "direction",
            "effective_state",
        )
        .first()
    )
    if door is None:
        return None

    return {
        "id": door["id"],
        "key": f"door.{door['id']}",
        "name": door["name"] or "door",
        "direction": door["direction"],
        "state": door["effective_state"],
    }


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

    # A map room must retain all of its exit references even when the player
    # has not visited the destination yet. Resolve those destination keys in
    # one bounded query without adding the destinations to the map payload.
    exit_ids = {
        exit_id
        for room in rooms
        for direction in adv_consts.DIRECTIONS
        if (exit_id := room[f"{direction}_id"])
    }
    unresolved_exit_ids = exit_ids.difference(id_to_key)
    if unresolved_exit_ids:
        id_to_key.update(
            {
                room_id: room_payload_key(room_id, relative_id)
                for room_id, relative_id in Room.objects.filter(
                    id__in=unresolved_exit_ids
                ).values_list("id", "relative_id")
            }
        )

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
    runtime_world: World | None = None,
) -> RoomSchema:
    from spawns.triggers import get_room_action_labels_for_actor

    if room is None:
        return RoomSchema(
            id=None,
            key="room.unknown",
            name="Unknown Room",
            description="Room data is unavailable.",
        )

    room_inventory_qs = with_item_salvageability(
        room.inventory.filter(
            is_pending_deletion=False,
        ).select_related("definition", "currency")
    )
    if runtime_world is not None:
        room_inventory_qs = room_inventory_qs.filter(world=runtime_world)
    room_inventory = serialize_inventory(room_inventory_qs, viewer=viewer)
    if isinstance(viewer, Player):
        room_inventory.extend(
            ItemSchema(**payload)
            for payload in serialized_quest_room_items_for_room(viewer, room.id)
        )

    room_players = room.players.filter(in_game=True).select_related("user", "equipment")
    room_mobs_qs = room.mobs.select_related("definition").order_by("id")
    if runtime_world is not None:
        room_players = room_players.filter(world=runtime_world)
        room_mobs_qs = room_mobs_qs.filter(world=runtime_world)
    room_mobs = list(room_mobs_qs)
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
    merchant_provider = None
    room_profile = room._state.fields_cache.get("merchant_profile")
    if room.merchant_profile_id:
        merchant_provider = MerchantProviderSchema(
            type="room",
            id=room.id,
            key=room.key,
            name=(room_profile.name if room_profile else "") or room.name,
        )
    training_provider = None
    room_trainer_profile = room._state.fields_cache.get("trainer_profile")
    if room.trainer_profile_id:
        training_provider = TrainingProviderSchema(
            type="room",
            id=room.id,
            key=room.key,
            name=(room_trainer_profile.name if room_trainer_profile else "") or room.name,
            profile={
                "id": room.trainer_profile_id,
                "key": (
                    room_trainer_profile.key
                    if room_trainer_profile else f"trainerprofile.{room.trainer_profile_id}"
                ),
                "slug": room_trainer_profile.slug if room_trainer_profile else "",
                "name": room_trainer_profile.name if room_trainer_profile else "",
            },
        )
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
        merchant_provider=merchant_provider,
        training_provider=training_provider,
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
            "state": player_state(player),
            "room": None,
        }
    stat_payload = build_player_stat_payload(player)
    actor_data.update(stat_payload)
    actor_data.update(
        _combat_rating_percentages(
            player.world,
            int(stat_payload.get("level") or getattr(player, "level", 1) or 1),
            stat_payload.get("stats") or {},
        )
    )
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
            "active_effects": active_character_effects(player),
            "combat_effects": active_combat_effects(player),
        }
    )
    actor_data["room"] = {"key": room_payload_key_for(room)} if room else None
    actor_data["equipment"] = serialize_equipment(player.equipment, viewer=player)
    actor_data["inventory"] = serialize_inventory(player.inventory.all(), viewer=player)
    source_world = _definition_world(player.world)
    prefetched = getattr(player, "_prefetched_objects_cache", {})
    if "material_balances" in prefetched:
        material_balances = prefetched["material_balances"]
    else:
        material_balances = PlayerMaterialBalance.objects.filter(
            player_id=player.id,
            material__world_id=source_world.id,
            quantity__gt=0,
        ).select_related("material")
    actor_data["materials"] = {
        balance.material.slug: int(balance.quantity)
        for balance in material_balances
        if balance.quantity > 0 and balance.material.world_id == source_world.id
    }
    if "economy" not in actor_data:
        actor_data["economy"] = player_economy_payload(player)
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
        inherited_config = inherited_system_config(world)
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
            "death_currency": (
                config.death_currency.code
                if config and config.death_currency_id else None),
            "death_currency_penalty": (
                float(config.death_currency_penalty) if config else 0.0),
            "has_corpse_decay": config.has_corpse_decay if config else True,
            "auto_equip": config.auto_equip if config else True,
            "globals_enabled": config.globals_enabled if config else False,
            "factions": {},
            "death_mode": config.death_mode if config else "flee",
            "abilities": {},
            "flee_to_unknown_rooms": config.flee_to_unknown_rooms if config else False,
            "death_route": config.death_route if config else "",
            "pvp_mode": config.pvp_mode if config else adv_consts.PVP_MODE_DISABLED,
            "allow_pvp": config.allow_pvp if config else False,
            "allow_combat": config.allow_combat if config else True,
            "announce_duel_results": bool(
                inherited_config and inherited_config.announce_duel_results
            ),
            "players_can_set_title": config.players_can_set_title if config else False,
            "facts": get_state_snapshot(STATE_SCOPE_WORLD, world),
            "classless": not world_uses_classes(world) if config else False,
            "is_classless": not world_uses_classes(world) if config else False,
            "tier": world.tier,
            "economy": {},
            "equipment": get_world_equipment_payload(world),
            "leader": world.leader.key if world.leader else None,
        }

    source_world = _definition_world(world)
    data.pop("currencies", None)
    if not data.get("economy"):
        data["economy"] = world_economy_payload(world)
    data["craft_materials"] = {
        material.slug: {
            "slug": material.slug,
            "name": material.name,
            "description": material.description or "",
            "order": int(material.order),
        }
        for material in CraftMaterial.objects.filter(world=source_world).order_by(
            "order", "name", "id"
        )
    }

    data["labels"] = get_world_label_bundle(world)
    data["class_selection"] = get_world_class_selection(world)
    data["equipment"] = get_world_equipment_payload(world)
    data["abilities"] = _serialize_ability_definitions(world)
    data["combat"] = _serialize_combat_system(world)
    data["is_classless"] = bool(data.get("classless"))

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
    inherited_config = inherited_system_config(world)
    data["announce_duel_results"] = bool(
        inherited_config and inherited_config.announce_duel_results
    )

    if not data.get("context"):
        data["context"] = world.context.key if world.context else world.key
    data["context_id"] = world.context_id
    data["instance_of_id"] = None
    if world.context and world.context.instance_of_id:
        data["instance_of_id"] = world.context.instance_of_id

    return data


def is_player_visible_on_who_list(actor: Player | None, player: Player) -> bool:
    actor_is_builder = bool(getattr(actor, "is_builder", False))
    if player.is_invisible and player.is_builder:
        return False

    if player.is_invisible and not actor_is_builder:
        return False

    if not actor_is_builder and not player.is_builder:
        actor_core = (getattr(actor, "factions", None) or {}).get("core")
        player_core = (player.factions or {}).get("core")
        if actor_core != player_core:
            return False

    return True


def build_who_list(world: World, actor: Player) -> List[WhoListEntry]:
    idle_cutoff = timezone.now() - timedelta(seconds=adv_consts.IDLE_THRESHOLD)
    qs = (
        Player.objects.filter(world=world, in_game=True)
        .select_related("user", "core_faction")
        .prefetch_related("faction_assignments__faction", "clan_memberships__clan")
    )
    who_list: List[WhoListEntry] = []

    for player in qs:
        if not is_player_visible_on_who_list(actor, player):
            continue

        who_list.append(
            WhoListEntry(
                key=player.key,
                name=player.name,
                title=player.title,
                level=player.level,
                gender=player.gender or "male",
                is_builder=player.is_builder,
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
    room_payload = serialize_room(
        room,
        room_key_lookup,
        door_states,
        viewer=player,
        runtime_world=world,
    )
    actor_payload = serialize_actor(player, room)
    world_payload = serialize_world(world)
    who_list = build_who_list(world, player)

    return StateSyncData(
        map=map_rooms,
        actor=actor_payload,
        room=room_payload,
        world=world_payload,
        who_list=who_list,
        prepared_abilities=active_prepared_ability_slugs(player),
    )
