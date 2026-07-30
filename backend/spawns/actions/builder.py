from __future__ import annotations

from dataclasses import dataclass
import json
import re

from builders.models import ItemDefinition
from config import constants as adv_consts
from core.economy import (
    EconomyConfigurationError,
    MAX_CURRENCY_AMOUNT,
    currency_payload,
    format_currency,
    money_payload,
    resolve_currency,
)
from core.leveling import (
    LevelingConfigError,
    clamp_level,
    get_world_leveling_config,
    progress_for_experience,
    set_player_level,
)
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    clear_state_value,
    coerce_state_command_value,
    get_state_snapshot,
    get_state_value,
    increment_state_value,
    normalize_state_scope,
    resolve_scope_owner,
    set_state_value,
)
from core.stat_system import (
    StatSystemValidationError,
    compute_stats,
    get_world_stat_system,
)
from core.utils import capfirst, format_actor_msg
from django.contrib.contenttypes.models import ContentType
from django.db import OperationalError, transaction
from django.utils import timezone
from rest_framework import serializers as drf_serializers

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.combat import apply_player_death
from spawns.actions.effects import active_combat_effects
from spawns.actions.information import LookAction
from spawns.actions.targeting import find_room_char_target
from spawns.ability_prepare_state import (
    ability_prepare_state_event,
    ability_prepare_state_events_for_players,
)
from spawns.events import (
    GameEvent,
    PLAYER_ROOM_ENTER_EMITTED_KEY,
    TRANSFER_LOCATION_SEQUENCE_KEY,
    TRANSFER_RUNTIME_WORLD_KEY,
    player_room_enter_event,
)
from spawns.handlers.base import ChoiceResolutionError, resolve_unambiguous_choice
from spawns.handlers.registry import (
    ActorNotFoundError,
    HandlerNotFoundError,
    dispatch_command,
    resolve_text_handler,
)
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    CombatParticipant,
    Equipment,
    Item,
    Mob,
    Player,
)
from spawns.serializers import LoadDefinitionSerializer
from spawns.state_payloads import (
    door_state_lookup,
    get_player_with_related,
    room_payload_key_for,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_inventory,
    serialize_room,
    serialize_world,
)
from spawns.wallet import WalletError, set_balance
from quests.entity_refs import resolve_room_ref_id
from worlds.models import Room, World, Zone

ECHO_SCOPES = ("room", "zone", "world")
CMD_SCOPE_TARGETS = ("room", "zone", "world")
JUMP_DIRECTIONS = {
    "n": adv_consts.DIRECTION_NORTH,
    "north": adv_consts.DIRECTION_NORTH,
    "e": adv_consts.DIRECTION_EAST,
    "east": adv_consts.DIRECTION_EAST,
    "s": adv_consts.DIRECTION_SOUTH,
    "south": adv_consts.DIRECTION_SOUTH,
    "w": adv_consts.DIRECTION_WEST,
    "west": adv_consts.DIRECTION_WEST,
    "u": adv_consts.DIRECTION_UP,
    "up": adv_consts.DIRECTION_UP,
    "d": adv_consts.DIRECTION_DOWN,
    "down": adv_consts.DIRECTION_DOWN,
}
MOB_DIRECT_STAT_FIELDS = (
    "health_max",
    "health_regen",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
    "armor",
    "dodge",
    "crit",
    "resilience",
    "attack_power",
    "ability_power",
)
PLAYER_SET_FIELD_CHOICES = (
    "level",
    "experience",
    "health",
    "energy",
    "stamina",
    "attributes",
    "glory",
)
PLAYER_SET_FIELDS = set(PLAYER_SET_FIELD_CHOICES)
MOB_SET_FIELD_CHOICES = (
    "name",
    "room_description",
    "description",
    "attackable",
    "level",
    "experience",
    "health",
    "energy",
    "stamina",
    "attributes",
    "aggression",
    "exp_worth",
    *MOB_DIRECT_STAT_FIELDS,
)
MOB_SET_FIELDS = set(MOB_SET_FIELD_CHOICES)
RESOURCE_CURRENT_TO_MAX = {
    "health": "health_max",
    "energy": "energy_max",
    "stamina": "stamina_max",
}
RESOURCE_MAX_TO_CURRENT = {
    max_field: current_field
    for current_field, max_field in RESOURCE_CURRENT_TO_MAX.items()
}
PLAYER_COMPUTED_STAT_FIELDS = {
    "health_max",
    "health_regen",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
    "armor",
    "dodge",
    "crit",
    "resilience",
    "attack_power",
    "ability_power",
    "energy_base",
}
REGEN_RESOURCES = ("health", "energy", "stamina")


def _first_error_message(detail: object) -> str:
    if isinstance(detail, dict):
        for value in detail.values():
            msg = _first_error_message(value)
            if msg:
                return msg
    if isinstance(detail, list):
        for value in detail:
            msg = _first_error_message(value)
            if msg:
                return msg
    if isinstance(detail, str):
        return detail
    return ""


def _tokenize_keywords(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _entity_tokens(entity: Item | Mob) -> set[str]:
    keywords = getattr(entity, "keywords", "") or ""
    if not keywords and getattr(entity, "definition", None):
        keywords = entity.definition.keywords or ""
    if not keywords:
        keywords = getattr(entity, "name", "") or ""
    tokens = set(_tokenize_keywords(keywords))
    tokens.add("item" if isinstance(entity, Item) else "mob")
    return tokens


def _entity_matches(entity: Item | Mob, selector: str) -> bool:
    if not selector:
        return False
    key = getattr(entity, "key", None)
    if key and str(key).lower() == selector:
        return True
    return selector in _entity_tokens(entity)


def _entity_name(entity: Item | Mob) -> str:
    name = getattr(entity, "name", "") or ""
    if name:
        return name
    definition = getattr(entity, "definition", None)
    if definition and definition.name:
        return definition.name
    return "target"


def _get_single_room_payload(player: Player):
    room = player.room
    if not room:
        return serialize_room(None, {}, {})
    room_key_lookup = {room.id: room_payload_key_for(room)}
    door_states = door_state_lookup(player.world, [room.id])
    return serialize_room(
        room,
        room_key_lookup,
        door_states,
        viewer=player,
        runtime_world=player.world,
    )


def _collect_purge_targets(player: Player, selector: str) -> list[Item | Mob]:
    selector = selector.strip().lower()
    room = player.room

    if selector.startswith("mob."):
        try:
            mob_id = int(selector.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        mob = room.mobs.filter(
            pk=mob_id,
            world=player.world,
        ).first()
        return [mob] if mob else []

    if selector.startswith("item."):
        try:
            item_id = int(selector.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        item = player.inventory.filter(pk=item_id, is_pending_deletion=False).first()
        if item:
            return [item]
        item = room.inventory.filter(
            pk=item_id,
            world=player.world,
            is_pending_deletion=False,
        ).first()
        return [item] if item else []

    room_mobs = list(
        room.mobs.filter(world=player.world).select_related("definition")
    )
    room_items = list(
        room.inventory.filter(
            world=player.world,
            is_pending_deletion=False,
        ).select_related("definition", "currency")
    )
    inventory_items = list(
        player.inventory.filter(is_pending_deletion=False).select_related("definition", "currency")
    )

    targets: list[Item | Mob] = [mob for mob in room_mobs if _entity_matches(mob, selector)]
    if targets:
        return targets

    targets = [item for item in inventory_items if _entity_matches(item, selector)]
    if targets:
        return targets

    return [item for item in room_items if _entity_matches(item, selector)]


def _collect_nested_item_ids(items: list[Item]) -> set[int]:
    item_ids: set[int] = set()
    for item in items:
        item_ids.add(item.id)
        item_ids.update(item.get_contained_ids())
    return item_ids


def _purge_mob_cleanly(*, mob: Mob) -> None:
    item_ids = _collect_nested_item_ids(list(mob.inventory.all()))
    if mob.equipment_id:
        equipment_type = ContentType.objects.get_for_model(Equipment)
        equipment_items = list(
            Item.objects.filter(
                container_type=equipment_type,
                container_id=mob.equipment_id,
            )
        )
        item_ids.update(_collect_nested_item_ids(equipment_items))

    if item_ids:
        Item.objects.filter(id__in=item_ids).delete()

    Mob.objects.filter(pk=mob.id).delete()


def _active_encounter_player_ids_for_mobs(mobs: list[Mob]) -> set[int]:
    mob_ids = [mob.id for mob in mobs if mob.id]
    if not mob_ids:
        return set()
    return set(
        CombatEncounter.objects.select_for_update()
        .filter(
            mob_id__in=mob_ids,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        .values_list("player_id", flat=True)
    )


def _split_chained_commands(cmd: str) -> list[str]:
    return [segment.strip() for segment in cmd.split("&&") if segment.strip()]


def _first_token(cmd: str) -> str | None:
    stripped = cmd.strip()
    if not stripped:
        return None
    return stripped.split()[0].lower()


def _first_dispatched_error(messages: list[dict]) -> str | None:
    for message in messages:
        msg_type = str(message.get("type", "")).lower()
        if not msg_type.endswith(".error"):
            continue
        text = message.get("text")
        if text:
            return str(text)
        data = message.get("data", {})
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
        return "Nested command failed."
    return None


def _collect_room_mob_targets(room: Room, selector: str, *, world: World | None = None) -> list[Mob]:
    normalized = selector.strip().lower()
    if not normalized:
        return []

    room_mobs_qs = room.mobs.filter(
        is_pending_deletion=False,
    ).select_related("definition")
    if world is not None:
        room_mobs_qs = room_mobs_qs.filter(world=world)

    if normalized.startswith("mob."):
        try:
            mob_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        mob = room_mobs_qs.filter(pk=mob_id).first()
        return [mob] if mob else []

    room_mobs = list(room_mobs_qs)
    return [mob for mob in room_mobs if _entity_matches(mob, normalized)]


def _player_matches(player: Player, selector: str) -> bool:
    if not selector:
        return False
    key = getattr(player, "key", None)
    if key and str(key).lower() == selector:
        return True
    name = str(getattr(player, "name", "") or "").strip().lower()
    if not name:
        return False
    return name == selector or name.startswith(selector)


def _collect_room_player_targets(room: Room, selector: str, *, world: World | None = None) -> list[Player]:
    normalized = selector.strip().lower()
    if not normalized:
        return []

    room_players_qs = room.players.select_related("world", "world__config", "user")
    if world is not None:
        room_players_qs = room_players_qs.filter(world=world)

    if normalized.startswith("player."):
        try:
            player_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        player = room_players_qs.filter(pk=player_id).first()
        return [player] if player else []

    room_players = list(room_players_qs)
    return [player for player in room_players if _player_matches(player, normalized)]


def _collect_world_player_targets(world: World, selector: str) -> list[Player]:
    normalized = selector.strip().lower()
    if not normalized:
        return []

    world_players_qs = Player.objects.filter(
        world=world,
        in_game=True,
    ).select_related("world", "world__config", "user", "room")

    if normalized.startswith("player."):
        try:
            player_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        player = world_players_qs.filter(pk=player_id).first()
        return [player] if player else []

    exact_matches = list(world_players_qs.filter(name__iexact=normalized))
    if exact_matches:
        return exact_matches

    prefix_matches = list(world_players_qs.filter(name__istartswith=normalized))
    if prefix_matches:
        return prefix_matches

    world_players = list(world_players_qs)
    return [player for player in world_players if _player_matches(player, normalized)]


def _resolve_world_player_target(
    *,
    actor: BuilderCommandActor,
    target_selector: str,
    runtime_world: World | None = None,
) -> Player:
    world = _actor_world(actor, runtime_world=runtime_world)
    if not world:
        raise ActionError("No runtime world is available for player resolution.", code="no_world")

    normalized_target = str(target_selector or "").strip().lower()
    if not normalized_target:
        raise ActionError("Target player is required.", code="invalid_target")
    if normalized_target in {"self", "me"}:
        if isinstance(actor, Player):
            return actor
        raise ActionError("Only player actors can target self.", code="invalid_target")

    targets = _collect_world_player_targets(world, normalized_target)
    if not targets:
        raise ActionError("Send recipient not found.", code="invalid_target")
    if len(targets) > 1:
        raise ActionError("Send recipient is ambiguous.", code="ambiguous_target")
    return targets[0]


def _resolve_player_class_key(world, selector: str) -> str:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        raise ActionError("Class is required.", code="invalid_args")

    try:
        stat_system = get_world_stat_system(world)
    except StatSystemValidationError as exc:
        raise ActionError(str(exc), code="invalid_stat_system")

    class_profiles = stat_system.get("class_profiles") or {}
    if not class_profiles:
        raise ActionError("This world has no class profiles.", code="classless_world")

    class_labels = (stat_system.get("labels") or {}).get("classes") or {}
    lookup: dict[str, str] = {}
    for class_key in class_profiles.keys():
        key = str(class_key or "").strip()
        if not key:
            continue
        lookup[key.lower()] = key
        label = str(class_labels.get(key) or class_profiles[key].get("label") or "").strip()
        if label:
            lookup[label.lower()] = key

    exact = lookup.get(normalized)
    if exact:
        return exact

    matches = sorted({class_key for label, class_key in lookup.items() if label.startswith(normalized)})
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ActionError("Class is ambiguous.", code="ambiguous_class")
    raise ActionError("Class not found.", code="invalid_class")


BuilderCommandActor = Player | Mob | Room | Zone | World


def _actor_kind(actor: BuilderCommandActor) -> str:
    if isinstance(actor, Player):
        return "player"
    if isinstance(actor, Mob):
        return "mob"
    if isinstance(actor, Room):
        return "room"
    if isinstance(actor, Zone):
        return "zone"
    if isinstance(actor, World):
        return "world"
    return str(getattr(actor, "model_type", "") or "actor")


def _actor_summary(actor: BuilderCommandActor) -> dict[str, object]:
    return {
        "key": actor.key,
        "name": getattr(actor, "name", "Unknown"),
        "char_type": _actor_kind(actor),
    }


def _message_actor_summary(actor: BuilderCommandActor) -> dict[str, object]:
    return {
        "id": actor.id,
        **_actor_summary(actor),
    }


def _actor_world(actor: BuilderCommandActor, *, runtime_world: World | None = None) -> World | None:
    if runtime_world is not None:
        return runtime_world
    if isinstance(actor, World):
        return actor
    return getattr(actor, "world", None)


def _actor_room(actor: BuilderCommandActor) -> Room | None:
    if isinstance(actor, Room):
        return actor
    if isinstance(actor, Zone):
        return actor.center
    if isinstance(actor, World):
        return None
    return getattr(actor, "room", None)


def _actor_zone(actor: BuilderCommandActor) -> Zone | None:
    if isinstance(actor, Zone):
        return actor
    if isinstance(actor, World):
        return None
    if isinstance(actor, Room):
        return actor.zone
    room = getattr(actor, "room", None)
    return getattr(room, "zone", None)


def _render_command_segment(
    segment: str,
    *,
    actor: BuilderCommandActor,
    character: Player | Mob | None = None,
    quest_instance=None,
) -> str:
    if not isinstance(actor, (Player, Mob)):
        return str(segment or "").strip()
    rendered = format_actor_msg(
        segment,
        actor,
        character=character,
        quest_instance=quest_instance,
    )
    return str(rendered or segment).strip()


def _collect_scope_player_keys(
    actor: BuilderCommandActor,
    scope: str,
    *,
    runtime_world: World | None = None,
) -> list[str]:
    world = _actor_world(actor, runtime_world=runtime_world)
    if not world:
        raise ActionError("You are nowhere. Cannot echo.", code="no_world")

    qs = Player.objects.filter(world=world, in_game=True)
    normalized_scope = scope.strip().lower()

    if normalized_scope == "room":
        room = _actor_room(actor)
        if not room:
            raise ActionError("You are nowhere. Cannot echo to room.", code="no_room")
        qs = qs.filter(room=room)
    elif normalized_scope == "zone":
        zone = _actor_zone(actor)
        if not zone:
            raise ActionError("You are nowhere. Cannot echo to zone.", code="no_zone")
        qs = qs.filter(room__zone=zone)
    elif normalized_scope == "world":
        pass
    else:
        raise ActionError("Scope must be room, zone, or world.", code="invalid_scope")

    return [f"player.{player_id}" for player_id in qs.values_list("id", flat=True)]


def _parse_room_selector(room_selector: str) -> int:
    token = room_selector.strip().lower()
    if token.startswith("room."):
        token = token.split(".", 1)[1]
    try:
        return int(token)
    except (TypeError, ValueError):
        raise ActionError("Room ID must be a number.", code="invalid_room_id")


def _resolve_room_in_world(room_world, room_selector_id: int):
    room = room_world.rooms.filter(pk=room_selector_id).first()
    if room:
        return room
    return room_world.rooms.filter(relative_id=room_selector_id).first()


def _room_reference_payload(room: Room | None) -> dict[str, object] | None:
    if room is None:
        return None
    return {
        "id": room.id,
        "key": room_payload_key_for(room),
        "name": room.name or "",
    }


def _normalize_jump_direction(room_selector: str) -> str | None:
    return JUMP_DIRECTIONS.get(room_selector.strip().lower())


def _resolve_room_character_target(
    *,
    actor: BuilderCommandActor,
    target_selector: str,
    runtime_world: World | None = None,
    allow_self: bool = True,
) -> Player | Mob:
    room = _actor_room(actor)
    if not room:
        raise ActionError("There is no current room for target resolution.", code="no_room")

    normalized_target = str(target_selector or "").strip().lower()
    if not normalized_target:
        raise ActionError("Target is required.", code="invalid_target")
    if allow_self and normalized_target in {"self", "me"}:
        if isinstance(actor, (Player, Mob)):
            return actor
        raise ActionError("Room actors must specify a target.", code="invalid_target")

    world = _actor_world(actor, runtime_world=runtime_world)
    player_targets = _collect_room_player_targets(room, normalized_target, world=world)
    mob_targets = _collect_room_mob_targets(room, normalized_target, world=world)
    targets: list[Player | Mob] = [*player_targets, *mob_targets]
    if not targets:
        raise ActionError("Target not found in this room.", code="invalid_target")
    if len(targets) > 1:
        raise ActionError("Target is ambiguous.", code="ambiguous_target")
    return targets[0]


def _parse_character_key(selector: str) -> tuple[str, int] | None:
    normalized = str(selector or "").strip().lower()
    if not (normalized.startswith("player.") or normalized.startswith("mob.")):
        return None
    actor_type, raw_id = normalized.split(".", 1)
    try:
        return actor_type, int(raw_id)
    except (TypeError, ValueError):
        return None


def _resolve_world_character_key(
    *,
    world: World,
    selector: str,
) -> Player | Mob | None:
    parsed = _parse_character_key(selector)
    if parsed is None:
        return None
    actor_type, actor_id = parsed
    if actor_type == "player":
        return (
            Player.objects.select_related("world", "world__config", "user", "room", "equipment")
            .filter(pk=actor_id, world=world)
            .first()
        )
    return (
        Mob.objects.select_related("world", "room", "definition", "equipment")
        .filter(pk=actor_id, world=world, is_pending_deletion=False)
        .first()
    )


def _resolve_builder_character_target(
    *,
    actor: BuilderCommandActor,
    target_selector: str | None,
    runtime_world: World | None = None,
    allow_self: bool = True,
) -> Player | Mob:
    normalized_target = str(target_selector or "").strip().lower()
    if not normalized_target:
        if allow_self and isinstance(actor, (Player, Mob)):
            return actor
        raise ActionError("Target is required.", code="invalid_target")

    if normalized_target in {"self", "me"}:
        if allow_self and isinstance(actor, (Player, Mob)):
            return actor
        raise ActionError("Room actors must specify a target.", code="invalid_target")

    if _parse_character_key(normalized_target) is not None:
        world = _actor_world(actor, runtime_world=runtime_world)
        if not world:
            raise ActionError("No runtime world is available for target resolution.", code="no_world")
        target = _resolve_world_character_key(world=world, selector=normalized_target)
        if target is None:
            raise ActionError("Target not found in this world.", code="invalid_target")
        return target

    return _resolve_room_character_target(
        actor=actor,
        target_selector=normalized_target,
        runtime_world=runtime_world,
        allow_self=allow_self,
    )


def _serialize_mob_stats_target(mob: Mob) -> dict[str, object]:
    payload = serialize_char_from_mob(mob, include_equipment=True).model_dump()
    stats = {field: getattr(mob, field, 0) for field in MOB_DIRECT_STAT_FIELDS}
    payload.update(
        {
            "experience": int(getattr(mob, "experience", 0) or 0),
            "exp_worth": int(getattr(mob, "exp_worth", 0) or 0),
            "currency_rewards": dict(mob.currency_reward_snapshot or {}),
            "aggression": adv_consts.canonical_mob_aggression(
                getattr(mob, "aggression", "")
            ),
            "stamina": int(getattr(mob, "stamina", 0) or 0),
            "stamina_max": int(getattr(mob, "stamina_max", 0) or 0),
            "stamina_regen": int(getattr(mob, "stamina_regen", 0) or 0),
            "energy_regen": int(getattr(mob, "energy_regen", 0) or 0),
            "health_regen": int(getattr(mob, "health_regen", 0) or 0),
            "attributes": dict(getattr(mob, "attributes", {}) or {}),
            "stats": stats,
        }
    )
    return payload


def _serialize_builder_stats_target(target: Player | Mob) -> tuple[dict[str, object], str]:
    if isinstance(target, Player):
        updated_target = get_player_with_related(target.id)
        return serialize_actor(updated_target, updated_target.room).model_dump(), "player"

    updated_target = (
        Mob.objects.select_related("world", "room", "definition", "equipment")
        .get(pk=target.id)
    )
    return _serialize_mob_stats_target(updated_target), "mob"


def _label_from_world(world_payload: dict[str, object], category: str, key: str, fallback: str | None = None) -> str:
    labels = world_payload.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    category_labels = labels.get(category) or {}
    if not isinstance(category_labels, dict):
        category_labels = {}
    label = category_labels.get(key)
    if label:
        return str(label)
    return fallback or key.replace("_", " ").title()


def _ordered_keys(world_payload: dict[str, object], category: str, values: dict[str, object]) -> list[str]:
    labels = world_payload.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    order = ((labels.get("order") or {}) if isinstance(labels.get("order"), dict) else {}).get(category) or []
    ordered = [str(key) for key in order if str(key) in values]
    ordered.extend(sorted(str(key) for key in values.keys() if str(key) not in ordered))
    return ordered


def _format_stat_lines(
    *,
    title: str,
    values: dict[str, object],
    world_payload: dict[str, object],
    label_category: str,
) -> list[str]:
    if not values:
        return []
    lines = [f"{title}:"]
    for key in _ordered_keys(world_payload, label_category, values):
        label = _label_from_world(world_payload, label_category, key)
        lines.append(f"  {label}: {values[key]}")
    return lines


def _render_builder_stats_text(
    *,
    target_payload: dict[str, object],
    target_type: str,
    world_payload: dict[str, object],
) -> str:
    name = str(target_payload.get("name") or "Target")
    key = str(target_payload.get("key") or "")
    lines = [
        f"{name} ({key})",
        f"Type: {target_type}",
        f"Level: {target_payload.get('level', 1)}",
    ]
    if target_payload.get("archetype"):
        lines.append(f"Class: {target_payload.get('archetype')}")
    if target_type == "mob" and target_payload.get("aggression"):
        lines.append(f"Aggression: {target_payload.get('aggression')}")

    resource_pairs = [
        ("health", "health_max", "health_regen"),
        ("energy", "energy_max", "energy_regen"),
        ("stamina", "stamina_max", "stamina_regen"),
    ]
    for current_key, max_key, regen_key in resource_pairs:
        if current_key not in target_payload and max_key not in target_payload:
            continue
        label = _label_from_world(world_payload, "resources", current_key, current_key.title())
        current_value = target_payload.get(current_key, 0)
        max_value = target_payload.get(max_key, 0)
        regen_value = target_payload.get(regen_key)
        lines.append(f"{label}: {current_value} / {max_value}")
        if regen_value not in (None, ""):
            regen_label = _label_from_world(world_payload, "stats", regen_key, f"{label} Regen")
            lines.append(f"{regen_label}: {regen_value}")

    if target_type == "player":
        lines.append(f"Experience: {target_payload.get('experience', 0)}")
        lines.append(f"Glory: {target_payload.get('glory', 0)}")
        balances = (target_payload.get("economy") or {}).get("balances") or {}
        if balances:
            lines.append("Currencies:")
            lines.extend(
                f"  {code}: {amount}"
                for code, amount in sorted(balances.items())
            )
    else:
        lines.append(f"Experience worth: {target_payload.get('exp_worth', 0)}")
        rewards = target_payload.get("currency_rewards") or {}
        if rewards:
            lines.append("Currency rewards:")
            lines.extend(
                f"  {code}: {amount}"
                for code, amount in sorted(rewards.items())
            )

    attributes = target_payload.get("attributes") or {}
    if isinstance(attributes, dict):
        lines.extend(
            _format_stat_lines(
                title="Attributes",
                values=attributes,
                world_payload=world_payload,
                label_category="attributes",
            )
        )

    stats = target_payload.get("stats") or {}
    if isinstance(stats, dict):
        resource_regen_keys = {"health_regen", "energy_regen", "stamina_regen"}
        stat_lines = []
        for key in _ordered_keys(world_payload, "stats", stats):
            if key in resource_regen_keys:
                continue
            label = _label_from_world(world_payload, "stats", key)
            stat_lines.append(f"{label}: {stats[key]}")
        lines.extend(stat_lines)

    return "\n".join(lines)


def _normalize_set_field(field_name: str) -> tuple[str, str | None]:
    normalized = str(field_name or "").strip()
    if not normalized:
        raise ActionError("Field is required.", code="invalid_args")
    normalized = normalized.replace("-", "_")
    lowered = normalized.lower()
    for prefix in ("attribute.", "attributes.", "attr."):
        if lowered.startswith(prefix):
            attr_key = lowered.split(".", 1)[1].strip()
            if not attr_key:
                raise ActionError("Attribute key is required.", code="invalid_args")
            return "attributes", attr_key
    return lowered, None


def _coerce_model_field_value(target: Player | Mob, field_name: str, raw_value: object) -> object:
    try:
        model_field = target._meta.get_field(field_name)
    except Exception as exc:
        raise ActionError(f"Unknown field '{field_name}'.", code="invalid_field") from exc

    internal_type = model_field.get_internal_type()
    if isinstance(target, Mob) and field_name == "aggression":
        value = coerce_state_command_value(raw_value)
        aggression = adv_consts.canonical_mob_aggression(value)
        if aggression not in adv_consts.MOB_AGGRESSION_OPTIONS:
            raise ActionError(
                "aggression must be one of: "
                f"{', '.join(adv_consts.MOB_AGGRESSION_OPTIONS)}.",
                code="invalid_value",
            )
        return aggression

    if internal_type in {"CharField", "TextField"}:
        coerced = str(raw_value)
        max_length = getattr(model_field, "max_length", None)
        if max_length is not None and len(coerced) > max_length:
            raise ActionError(
                f"{field_name} cannot exceed {max_length} characters.",
                code="invalid_value",
            )
        if not getattr(model_field, "blank", True) and not coerced.strip():
            raise ActionError(f"{field_name} cannot be blank.", code="invalid_value")
        return coerced

    value = coerce_state_command_value(raw_value)
    if internal_type in {"IntegerField", "PositiveIntegerField", "PositiveSmallIntegerField"}:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            raise ActionError(f"{field_name} must be an integer.", code="invalid_value")
        if internal_type.startswith("Positive") and coerced < 0:
            raise ActionError(f"{field_name} cannot be negative.", code="invalid_value")
        return coerced
    if internal_type == "FloatField":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ActionError(f"{field_name} must be a number.", code="invalid_value")
    if internal_type == "BooleanField":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ActionError(f"{field_name} must be true or false.", code="invalid_value")
    if internal_type == "JSONField":
        return value
    return str(value)


def _resource_max_for_target(target: Player | Mob, max_field: str) -> int:
    if isinstance(target, Player):
        stats = compute_stats(
            target.level,
            target.archetype,
            char=target,
            world=target.world,
        )
        max_value = int(stats.get(max_field) or 0)
        return max(1, max_value) if max_field == "health_max" else max(0, max_value)

    return int(getattr(target, max_field, 0) or 0)


def _normalize_regen_resource(resource: str | None) -> str | None:
    normalized = str(resource or "").strip().lower()
    if not normalized:
        return None
    try:
        return resolve_unambiguous_choice(
            normalized,
            choices=REGEN_RESOURCES,
            aliases={
                "hp": "health",
                "mp": "energy",
                "mana": "energy",
                "endurance": "stamina",
            },
        )
    except ChoiceResolutionError as exc:
        if exc.code == "ambiguous_choice":
            raise ActionError(
                f"Resource is ambiguous: {', '.join(exc.matches)}.",
                code="ambiguous_resource",
                data={"matches": exc.matches},
            ) from exc
        raise ActionError(
            "Resource must be health, energy, or stamina.",
            code="invalid_resource",
            data={"resource": normalized},
        ) from exc


def _regen_resource_snapshot(target: Player | Mob) -> dict[str, int]:
    return {
        "health": int(getattr(target, "health", 0) or 0),
        "energy": int(getattr(target, "energy", 0) or 0),
        "stamina": int(getattr(target, "stamina", 0) or 0),
    }


def _actor_payload_for_regen(actor: BuilderCommandActor) -> dict[str, object]:
    if isinstance(actor, Player):
        updated_actor = get_player_with_related(actor.id)
        return serialize_actor(updated_actor, updated_actor.room).model_dump()
    return _actor_summary(actor)


def _set_character_stat_value(
    *,
    target: Player | Mob,
    field_name: str,
    raw_value: object,
) -> tuple[object, object, str]:
    normalized_field, attribute_key = _normalize_set_field(field_name)
    allowed_fields = PLAYER_SET_FIELDS if isinstance(target, Player) else MOB_SET_FIELDS
    if normalized_field not in allowed_fields:
        if isinstance(target, Player) and normalized_field in PLAYER_COMPUTED_STAT_FIELDS:
            raise ActionError(
                f"{normalized_field} is computed for players. Set attributes or equipment instead.",
                code="computed_player_stat",
            )
        raise ActionError(f"{normalized_field} cannot be set on this target.", code="invalid_field")

    if normalized_field == "attributes":
        previous_attributes = dict(getattr(target, "attributes", {}) or {})
        if attribute_key:
            previous_value = previous_attributes.get(attribute_key)
            new_value = coerce_state_command_value(raw_value)
            attributes = dict(previous_attributes)
            if new_value is None:
                attributes.pop(attribute_key, None)
            else:
                attributes[attribute_key] = new_value
            target.attributes = attributes
            target.save(update_fields=["attributes"])
            return previous_value, new_value, f"attributes.{attribute_key}"

        new_attributes = coerce_state_command_value(raw_value)
        if not isinstance(new_attributes, dict):
            raise ActionError("attributes must be a JSON object.", code="invalid_value")
        target.attributes = new_attributes
        target.save(update_fields=["attributes"])
        return previous_attributes, new_attributes, "attributes"

    previous_value = getattr(target, normalized_field)
    new_value = _coerce_model_field_value(target, normalized_field, raw_value)

    max_field = RESOURCE_CURRENT_TO_MAX.get(normalized_field)
    if max_field:
        max_value = _resource_max_for_target(target, max_field)
        if int(new_value) > max_value:
            raise ActionError(
                (
                    f"{normalized_field} cannot exceed {max_field} ({max_value}). "
                    f"Set {max_field} first."
                ),
                code="invalid_value",
                data={
                    "field": normalized_field,
                    "max_field": max_field,
                    "max_value": max_value,
                },
            )

    update_fields = []
    if previous_value != new_value:
        setattr(target, normalized_field, new_value)
        update_fields.append(normalized_field)

    current_field = RESOURCE_MAX_TO_CURRENT.get(normalized_field)
    if current_field:
        current_value = int(getattr(target, current_field, 0) or 0)
        if current_value > int(new_value):
            setattr(target, current_field, int(new_value))
            update_fields.append(current_field)

    if update_fields:
        target.save(update_fields=update_fields)
    return previous_value, new_value, normalized_field


class LoadDefinitionAction:
    def execute(
        self,
        *,
        actor: Player | Mob | Room,
        runtime_world: World | None = None,
        definition_type: str,
        definition_id: int | str,
        cmd: str | None = None,
    ) -> ActionResult:
        actor_type = _actor_kind(actor)
        if actor_type not in ("player", "mob", "room"):
            raise ActionError("Only players, mobs, and rooms can load definitions.", code="unsupported_actor")

        load_actor: Player | Mob | Room
        room: Room | None
        spawn_world: World | None
        if isinstance(actor, Player):
            load_actor = get_player_with_related(actor.id)
            room = load_actor.room
            spawn_world = load_actor.world
        elif isinstance(actor, Mob):
            load_actor = actor
            room = actor.room
            spawn_world = actor.world
        else:
            load_actor = actor
            room = actor
            spawn_world = _actor_world(actor, runtime_world=runtime_world)

        if not room:
            raise ActionError("You are nowhere. Cannot load definitions.", code="no_room")
        if not spawn_world:
            raise ActionError("No runtime world is available for loading definitions.", code="no_world")

        payload = {
            "world_id": spawn_world.id,
            "definition_type": definition_type,
            "definition_id": definition_id,
            "actor_type": actor_type,
            "actor_id": load_actor.id,
            "room": room.id,
        }
        if cmd:
            payload["cmd"] = cmd

        serializer = LoadDefinitionSerializer(data=payload)
        try:
            serializer.is_valid(raise_exception=True)
        except drf_serializers.ValidationError as exc:
            message = _first_error_message(exc.detail) or "Unable to load definition."
            raise ActionError(message, code="invalid_load")

        vd = serializer.validated_data
        loaded_key = None
        loaded_name = None
        loaded_type = vd["definition_type"]

        if vd["definition_type"] == "item":
            item = vd["definition"].spawn(vd["actor"], vd["spawn_world"])
            loaded_key = item.key
            loaded_name = item.name or (item.definition.name if item.definition else "item")
        elif vd["definition_type"] == "mob":
            room = vd["room"] if vd["actor_type"] == "room" else vd["actor"].room
            mob = vd["definition"].spawn(room, vd["spawn_world"])
            loaded_key = mob.key
            loaded_name = (
                mob.name
                or (mob.definition.name if mob.definition else "")
                or "mob"
            )
        else:
            raise ActionError("Unknown definition type.", code="invalid_type")

        if isinstance(load_actor, Player):
            updated_actor = get_player_with_related(load_actor.id)
            actor_payload = serialize_actor(updated_actor, updated_actor.room).model_dump()
            recipient_key = updated_actor.key
        else:
            actor_payload = _actor_summary(load_actor)
            recipient_key = load_actor.key

        data = {
            "actor": actor_payload,
            "loaded": {
                "type": loaded_type,
                "key": loaded_key,
                "name": loaded_name,
            },
        }
        if cmd:
            data["loaded"]["cmd"] = cmd

        text = f"You wave your hands, and {loaded_name} appears!"

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./load.success",
                    recipients=[recipient_key],
                    data=data,
                    text=text,
                )
            ]
        )


class GrantItemAction:
    def _target_payload(self, target: Player | Mob) -> tuple[dict[str, object], str]:
        if isinstance(target, Player):
            updated_target = get_player_with_related(target.id)
            return serialize_actor(updated_target, updated_target.room).model_dump(), updated_target.key

        updated_target = Mob.objects.select_related("definition", "room", "world").get(pk=target.id)
        return serialize_char_from_mob(updated_target).model_dump(), updated_target.key

    def _context_world(self, spawn_world: World) -> World:
        context = spawn_world.context
        return context.instance_of or context

    def _resolve_item_definitions(
        self,
        *,
        context: World,
        item_refs: list[str],
    ) -> list[ItemDefinition]:
        normalized_refs = [str(item_ref).strip() for item_ref in item_refs if str(item_ref).strip()]
        numeric_ids = {
            int(item_ref)
            for item_ref in normalized_refs
            if item_ref.isdigit()
        }
        ref_values = set(normalized_refs)

        item_definitions_by_id = {
            item_definition.id: item_definition
            for item_definition in ItemDefinition.objects.filter(world=context, pk__in=numeric_ids)
        }
        item_definitions_by_slug = {
            item_definition.slug: item_definition
            for item_definition in ItemDefinition.objects.filter(world=context, slug__in=ref_values)
        }

        definitions: list[ItemDefinition] = []
        for item_ref in normalized_refs:
            definition = None
            if item_ref.isdigit():
                definition = item_definitions_by_id.get(int(item_ref))
            if definition is None:
                definition = item_definitions_by_slug.get(item_ref)
            if definition is None:
                raise ActionError(
                    "Item definition does not belong to this world",
                    code="invalid_grant",
                    data={"item": item_ref},
                )
            definitions.append(definition)

        return definitions

    def _loaded_item_name(self, item: Item) -> str:
        name = getattr(item, "name", "") or ""
        if name:
            return name
        definition = getattr(item, "definition", None)
        if definition and definition.name:
            return definition.name
        return "item"

    def _loaded_item_payload(
        self,
        item: Item,
        item_payload: dict,
    ) -> dict[str, object]:
        loaded_name = self._loaded_item_name(item)
        return {
            "type": "item",
            "key": item.key,
            "name": loaded_name,
            "item": item_payload,
        }

    def execute(
        self,
        *,
        actor: Player | Mob | Room,
        target_selector: str,
        item_id: int | str,
        item_ids: list[int | str] | None = None,
        runtime_world: World | None = None,
    ) -> ActionResult:
        return self.execute_many(
            actor=actor,
            target_selector=target_selector,
            item_ids=item_ids or [item_id],
            runtime_world=runtime_world,
        )

    def execute_many(
        self,
        *,
        actor: Player | Mob | Room,
        target_selector: str,
        item_ids: list[int | str],
        runtime_world: World | None = None,
    ) -> ActionResult:
        actor_type = _actor_kind(actor)
        if actor_type not in ("player", "mob", "room"):
            raise ActionError("Only players, mobs, and rooms can grant items.", code="unsupported_actor")

        normalized_item_ids = [
            str(item_id).strip()
            for item_id in item_ids
            if str(item_id).strip()
        ]
        if not normalized_item_ids:
            raise ActionError("Usage: /grantitem <target> <item_definition_id|item_slug>", code="invalid_args")

        with transaction.atomic():
            target = _resolve_room_character_target(
                actor=actor,
                target_selector=target_selector,
                runtime_world=runtime_world,
            )
            spawn_world = _actor_world(actor, runtime_world=runtime_world)
            if not spawn_world:
                raise ActionError("No runtime world is available for granting items.", code="no_world")

            definitions = self._resolve_item_definitions(
                context=self._context_world(spawn_world),
                item_refs=normalized_item_ids,
            )
            spawned_items = [
                definition.spawn(target, spawn_world)
                for definition in definitions
            ]
            serialized_items = serialize_inventory(
                spawned_items,
                viewer=target,
            )
            loaded_items = [
                self._loaded_item_payload(item, payload.model_dump())
                for item, payload in zip(spawned_items, serialized_items)
            ]
            target_payload, target_key = self._target_payload(target)

            if isinstance(actor, Player):
                updated_actor = get_player_with_related(actor.id)
                actor_payload = serialize_actor(updated_actor, updated_actor.room).model_dump()
                recipient_key = updated_actor.key
            else:
                actor_payload = _actor_summary(actor)
                recipient_key = actor.key

        if len(loaded_items) == 1:
            loaded_data = loaded_items[0]
            text = f"Granted {loaded_data['name']} to {getattr(target, 'name', None) or 'target'}."
            notification_text = f"You receive {loaded_data['name']}."
        else:
            loaded_data = {
                "type": "items",
                "count": len(loaded_items),
                "name": f"{len(loaded_items)} items",
                "items": loaded_items,
            }
            text = f"Granted {len(loaded_items)} items to {getattr(target, 'name', None) or 'target'}."
            notification_text = f"You receive {len(loaded_items)} items."

        data = {
            "actor": actor_payload,
            "target": target_payload,
            "target_type": _actor_kind(target),
            "loaded": loaded_data,
            "loaded_items": loaded_items,
            "loaded_count": len(loaded_items),
        }

        events = [
            GameEvent(
                type="cmd./grantitem.success",
                recipients=[recipient_key],
                data=data,
                text=text,
            )
        ]

        if isinstance(target, Player) and target_key != recipient_key:
            events.append(
                GameEvent(
                    type="notification./grantitem",
                    recipients=[target_key],
                    data={
                        "actor": target_payload,
                        "issuer": actor_payload,
                        "target": target_payload,
                        "target_type": "player",
                        "loaded": loaded_data,
                        "loaded_items": loaded_items,
                        "loaded_count": len(loaded_items),
                    },
                    text=notification_text,
                )
            )

        return ActionResult(events=events)


class SetCurrencyAction:
    def execute(
        self,
        *,
        actor: Player,
        target_selector: str | None,
        currency_reference: str | int,
        amount: object,
        runtime_world: World | None = None,
    ) -> ActionResult:
        target = _resolve_builder_character_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
            allow_self=True,
        )
        if not isinstance(target, Player):
            raise ActionError(
                "Currency balances can only be set on players.",
                code="invalid_target",
            )

        try:
            currency = resolve_currency(target.world, currency_reference)
        except EconomyConfigurationError:
            raise ActionError(
                f"Currency '{currency_reference}' was not found in this world.",
                code="invalid_currency",
            )

        try:
            expected_world = _actor_world(
                actor,
                runtime_world=runtime_world,
            )
            mutation = set_balance(
                target,
                currency,
                amount,
                reason="builder.set_currency",
                expected_world_id=expected_world.pk if expected_world else None,
            )
        except WalletError as error:
            message = (
                "Amount must be a nonnegative whole number no greater than "
                f"{MAX_CURRENCY_AMOUNT:,}."
                if error.code == "invalid_amount"
                else str(error)
            )
            raise ActionError(message, code=error.code)

        if mutation.changes:
            change = mutation.changes[0]
            before = change.before
            after = change.after
        else:
            before = after = int(amount)

        actor_payload = _actor_summary(actor)
        target_payload = _actor_summary(target)
        data = {
            "actor": actor_payload,
            "target": target_payload,
            "target_type": "player",
            "currency": currency_payload(currency),
            "before": before,
            "after": after,
            "delta": after - before,
            "money": money_payload(after, currency),
            "wallet_revision": mutation.revision,
            "changed": bool(mutation.changes),
        }
        text = (
            f"Set {target.name}'s {currency.name} balance to "
            f"{format_currency(after, currency)}."
        )
        events = [
            GameEvent(
                type="cmd./setcurrency.success",
                recipients=[actor.key],
                data=data,
                text=text,
            )
        ]
        if mutation.changes and target.key != actor.key:
            events.append(
                GameEvent(
                    type="notification./setcurrency",
                    recipients=[target.key],
                    data={
                        "actor": target_payload,
                        "issuer": actor_payload,
                        "target": target_payload,
                        "target_type": "player",
                        "currency": currency_payload(currency),
                        "before": before,
                        "after": after,
                        "delta": after - before,
                        "money": money_payload(after, currency),
                        "wallet_revision": mutation.revision,
                    },
                    text=(
                        f"Your {currency.name} balance was set to "
                        f"{format_currency(after, currency)}."
                    ),
                )
            )
        return ActionResult(events=events)


class PurgeAction:
    def execute(
        self,
        *,
        player_id: int,
        target: str | None = None,
    ) -> ActionResult:
        normalized_target = (target or "").strip().lower()

        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot purge.", code="no_room")

            room = player.room
            affected_player_ids: set[int] = set()
            if not normalized_target or normalized_target == "all":
                items = list(
                    room.inventory.filter(
                        world=player.world,
                        is_pending_deletion=False,
                    )
                )
                mobs = list(room.mobs.filter(world=player.world))
                affected_player_ids = _active_encounter_player_ids_for_mobs(mobs)

                for item in items:
                    item.delete()
                for mob in mobs:
                    _purge_mob_cleanly(mob=mob)

                out_text = "The world feels a little cleaner."

            elif normalized_target == "items":
                items = list(
                    room.inventory.filter(
                        world=player.world,
                        is_pending_deletion=False,
                    )
                )
                for item in items:
                    item.delete()
                out_text = "You purge all items in the room."

            elif normalized_target == "mobs":
                mobs = list(room.mobs.filter(world=player.world))
                affected_player_ids = _active_encounter_player_ids_for_mobs(mobs)
                for mob in mobs:
                    _purge_mob_cleanly(mob=mob)
                out_text = "You purge all mobs in the room."

            else:
                targets = _collect_purge_targets(player, normalized_target)
                if not targets:
                    raise ActionError("Incorrect purge target.", code="invalid_target")

                affected_player_ids = _active_encounter_player_ids_for_mobs(
                    [entity for entity in targets if isinstance(entity, Mob)]
                )
                lines = []
                for entity in targets:
                    lines.append(f"You purge {_entity_name(entity)} from this world.")
                    if isinstance(entity, Mob):
                        _purge_mob_cleanly(mob=entity)
                    else:
                        entity.delete()
                out_text = "\n".join(lines)

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = _get_single_room_payload(updated_player)

        data = {
            "actor": actor_payload.model_dump(),
            "room": room_payload.model_dump(),
            "target": normalized_target or "all",
        }

        return ActionResult(events=[
            GameEvent(
                type="cmd./purge.success",
                recipients=[updated_player.key],
                data=data,
                text=out_text,
            ),
            *ability_prepare_state_events_for_players(affected_player_ids),
        ])


class RepopAction:
    """Force zone spawn reconciliation and optionally reset runtime doorways."""

    def execute(
        self,
        *,
        actor: Player | Room,
        runtime_world: World | None = None,
        reset_doors: bool = False,
    ) -> ActionResult:
        zone = _actor_zone(actor)
        if zone is None:
            raise ActionError(
                "There is no current zone to repopulate.",
                code="no_zone",
            )

        spawn_world = _actor_world(actor, runtime_world=runtime_world)
        if spawn_world is None or spawn_world.context_id is None:
            raise ActionError(
                "There is no runtime world to repopulate.",
                code="no_runtime_world",
            )
        if zone.world_id != spawn_world.context_id:
            raise ActionError(
                "The current zone is outside this runtime world.",
                code="invalid_world_context",
            )

        # Door reset timing remains owned by the periodic lifecycle runner.
        # The optional manual reset does not advance that shared zone timer.
        # Reconciliation shares one zone-scoped live-placement cache and
        # rechecks misses while each plan is locked, preventing duplicate
        # output under concurrent runs.
        from spawns.loading import repopulate_spawn_plans_for_zone

        output = repopulate_spawn_plans_for_zone(
            world=spawn_world,
            zone_id=zone.id,
            reset_doors=reset_doors,
        )
        plan_results = output["spawn_plans"]
        plan_count = len(plan_results)
        reconciled_count = sum(
            1 for result in plan_results if not result.get("skipped")
        )
        placement_count = sum(
            int(result.get("placements") or 0)
            for result in plan_results
        )
        spawned_count = sum(
            int(result.get("spawned") or 0)
            for result in plan_results
        )
        plan_label = "plan" if plan_count == 1 else "plans"
        spawn_label = "spawn" if spawned_count == 1 else "spawns"

        data = {
            "actor": _actor_summary(actor),
            "world_id": spawn_world.id,
            "zone": {
                "id": zone.id,
                "key": zone.key,
                "name": zone.name,
            },
            "spawn_plans_checked": plan_count,
            "spawn_plans_reconciled": reconciled_count,
            "placements_checked": placement_count,
            "spawned": spawned_count,
            "doors": output["doors"],
        }
        door_text = ""
        if reset_doors:
            doorways_checked = output["doors"]["doorways_checked"]
            door_states_reset = output["doors"]["door_states_reset"]
            doorway_label = "doorway" if doorways_checked == 1 else "doorways"
            state_label = "state" if door_states_reset == 1 else "states"
            door_text = (
                f" Reset {door_states_reset} runtime door {state_label} "
                f"across {doorways_checked} zone {doorway_label}."
            )
        return ActionResult(events=[
            GameEvent(
                type="cmd./repop.success",
                recipients=[actor.key],
                data=data,
                text=(
                    f"Repopulated {zone.name}: checked {plan_count} active "
                    f"spawn {plan_label} and restored {spawned_count} missing "
                    f"{spawn_label}.{door_text}"
                ),
            ),
        ])


class EchoAction:
    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        scope: str,
        message: str,
        runtime_world: World | None = None,
    ) -> ActionResult:
        normalized_scope = str(scope or "").strip().lower()
        if normalized_scope not in ECHO_SCOPES:
            raise ActionError("Scope must be room, zone, or world.", code="invalid_scope")

        normalized_message = _render_command_segment(str(message or ""), actor=actor)
        if not normalized_message:
            raise ActionError(
                "Usage: /echo [room|zone|world] <message>",
                code="invalid_args",
            )

        recipients = _collect_scope_player_keys(
            actor,
            normalized_scope,
            runtime_world=runtime_world,
        )
        data = {
            "actor": _actor_summary(actor),
            "scope": normalized_scope,
            "message": normalized_message,
        }

        events: list[GameEvent] = []
        events.append(
            GameEvent(
                type="cmd./echo.success",
                recipients=[actor.key],
                data=data,
                text=normalized_message,
            )
        )
        if isinstance(actor, Player):
            recipients = [recipient for recipient in recipients if recipient != actor.key]

        if recipients:
            events.append(
                GameEvent(
                    type="notification./echo",
                    recipients=recipients,
                    data=data,
                    text=normalized_message,
                )
            )

        return ActionResult(events=events)


class SendAction:
    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str,
        message: str,
        runtime_world: World | None = None,
    ) -> ActionResult:
        target = _resolve_world_player_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
        )
        normalized_message = _render_command_segment(
            str(message or ""),
            actor=actor,
            character=target,
        )
        if not normalized_message:
            raise ActionError("Usage: /send <player> <message>", code="invalid_args")

        actor_payload = _message_actor_summary(actor)
        recipient_key = actor.key
        target_payload = _message_actor_summary(target)
        data = {
            "actor": actor_payload,
            "target": target_payload,
            "target_type": "player",
            "message": normalized_message,
        }

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./send.success",
                    recipients=[recipient_key],
                    data=data,
                    text=normalized_message,
                ),
                GameEvent(
                    type="notification./send",
                    recipients=[target.key],
                    data=data,
                    text=normalized_message,
                ),
            ]
        )


class SendExceptAction:
    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str,
        message: str,
        runtime_world: World | None = None,
    ) -> ActionResult:
        target = _resolve_world_player_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
        )
        normalized_message = _render_command_segment(
            str(message or ""),
            actor=actor,
            character=target,
        )
        if not normalized_message:
            raise ActionError(
                "Usage: /sendexcept <player> <message>",
                code="invalid_args",
            )

        world = _actor_world(actor, runtime_world=runtime_world)
        if world is None:
            raise ActionError(
                "No runtime world is available for player resolution.",
                code="no_world",
            )

        if target.room_id is None:
            raise ActionError(
                "Send-except recipient is not in a room.",
                code="no_room",
            )

        actor_payload = _message_actor_summary(actor)
        recipient_key = actor.key

        recipient_ids = list(
            Player.objects.filter(
                world=world,
                room_id=target.room_id,
                in_game=True,
            )
            .exclude(pk=target.id)
            .order_by("id")
            .values_list("id", flat=True)
        )
        recipient_keys = [
            f"player.{player_id}"
            for player_id in recipient_ids
        ]
        if isinstance(actor, Player):
            recipient_keys = [
                key
                for key in recipient_keys
                if key != recipient_key
            ]

        target_payload = _message_actor_summary(target)
        data = {
            "actor": actor_payload,
            "target": target_payload,
            "target_type": "player",
            "room": _room_reference_payload(target.room),
            "message": normalized_message,
        }
        events = [
            GameEvent(
                type="cmd./sendexcept.success",
                recipients=[recipient_key],
                data=data,
                text=(
                    "Message sent."
                    if recipient_key == target.key
                    else normalized_message
                ),
            ),
        ]
        if recipient_keys:
            events.append(
                GameEvent(
                    type="notification./sendexcept",
                    recipients=recipient_keys,
                    data=data,
                    text=normalized_message,
                )
            )
        return ActionResult(events=events)


class StateAction:
    def _resolve_character_owner(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str | None,
        runtime_world: World | None,
    ) -> Player | Mob:
        if not target_selector:
            raise ActionError(
                "Character state commands require a target.",
                code="invalid_target",
            )

        target = _resolve_builder_character_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
            allow_self=True,
        )
        return target

    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        operation: str,
        scope: str,
        target_selector: str | None = None,
        key: str | None = None,
        value: object | None = None,
        amount: int | float | None = None,
        runtime_world: World | None = None,
    ) -> ActionResult:
        normalized_operation = str(operation or "").strip().lower()
        normalized_scope = normalize_state_scope(scope)
        if target_selector and normalized_scope != STATE_SCOPE_CHARACTER:
            raise ActionError(
                "State targets are only supported for character state.",
                code="invalid_target",
            )
        character = None
        if normalized_scope == STATE_SCOPE_CHARACTER:
            character = self._resolve_character_owner(
                actor=actor,
                target_selector=target_selector,
                runtime_world=runtime_world,
            )
        state_runtime_world = _actor_world(actor, runtime_world=runtime_world)
        owner = resolve_scope_owner(
            normalized_scope,
            actor=actor,
            world=state_runtime_world,
            zone=_actor_zone(actor),
            room=_actor_room(actor),
            character=character,
        )
        if owner is None:
            raise ActionError(
                f"There is no current {normalized_scope} state owner here.",
                code="missing_state_owner",
            )

        data: dict[str, object] = {
            "actor": _actor_summary(actor),
            "scope": normalized_scope,
            "operation": normalized_operation,
        }
        if target_selector:
            data["target"] = _actor_summary(character)
        text = None

        if normalized_operation == "show":
            snapshot = get_state_snapshot(
                normalized_scope,
                owner,
                runtime_world=state_runtime_world,
            )
            data["state"] = snapshot
            text = json.dumps(snapshot, sort_keys=True)
        elif normalized_operation == "get":
            if not key:
                raise ActionError("Usage: /state get <scope> <key>", code="invalid_args")
            current_value = get_state_value(
                normalized_scope,
                owner,
                key,
                runtime_world=state_runtime_world,
            )
            data["key"] = key
            data["value"] = current_value
            rendered_value = json.dumps(current_value) if isinstance(current_value, (dict, list, bool, type(None))) else str(current_value)
            text = f"{normalized_scope}.{key} = {rendered_value}"
        elif normalized_operation == "set":
            if not key:
                raise ActionError(
                    "Usage: /state set <scope> <key> -- <value>",
                    code="invalid_args",
                )
            new_value = set_state_value(
                normalized_scope,
                owner,
                key,
                coerce_state_command_value(value),
                runtime_world=state_runtime_world,
            )
            data["key"] = key
            data["value"] = new_value
            rendered_value = json.dumps(new_value) if isinstance(new_value, (dict, list, bool, type(None))) else str(new_value)
            text = f"Set {normalized_scope}.{key} = {rendered_value}"
        elif normalized_operation == "clear":
            if not key:
                raise ActionError("Usage: /state clear <scope> <key>", code="invalid_args")
            cleared = clear_state_value(
                normalized_scope,
                owner,
                key,
                runtime_world=state_runtime_world,
            )
            data["key"] = key
            data["cleared"] = bool(cleared)
            text = (
                f"Cleared {normalized_scope}.{key}"
                if cleared
                else f"{normalized_scope}.{key} was already unset."
            )
        elif normalized_operation == "add":
            if not key:
                raise ActionError(
                    "Usage: /state add <scope> <key> [amount]",
                    code="invalid_args",
                )
            new_value = increment_state_value(
                normalized_scope,
                owner,
                key,
                coerce_state_command_value(amount) if amount is not None else 1,
                runtime_world=state_runtime_world,
            )
            data["key"] = key
            data["value"] = new_value
            text = f"{normalized_scope}.{key} = {new_value}"
        else:
            raise ActionError(
                "State operation must be get, set, clear, add, or show.",
                code="invalid_args",
            )

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./state.success",
                    recipients=[actor.key],
                    data=data,
                    text=text,
                )
            ]
        )


class WizKillAction:
    def _resolve_target(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str,
        runtime_world: World | None,
    ) -> Player:
        target = _resolve_room_character_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
            allow_self=False,
        )
        if not isinstance(target, Player):
            raise ActionError(
                "/kill currently supports player targets.",
                code="invalid_target",
            )
        return target

    def _target_text(
        self,
        *,
        actor: BuilderCommandActor,
        target: Player,
        message: str | None,
    ) -> str:
        if message:
            return message
        if isinstance(actor, Room):
            return "You perish to your environment."
        if isinstance(actor, Mob):
            return f"You die to {actor.name}."
        if isinstance(actor, Player):
            return f"{capfirst(actor.name)} snaps you out of existence."
        return "You have been slain."

    def _room_text(self, *, actor: BuilderCommandActor, target: Player) -> str:
        target_name = capfirst(target.name)
        if isinstance(actor, Room):
            return f"{target_name} perishes to their environment."
        if isinstance(actor, Mob):
            return f"{target_name} dies to {actor.name}."
        if isinstance(actor, Player):
            return f"{capfirst(actor.name)} snaps {target.name} out of existence."
        return f"{target_name} dies."

    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str,
        message: str | None = None,
        runtime_world: World | None = None,
        death_token=None,
    ) -> ActionResult:
        target = self._resolve_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
        )
        origin_room = target.room
        target_text = self._target_text(actor=actor, target=target, message=message)
        room_text = self._room_text(actor=actor, target=target)
        from spawns import duels

        active_duel = duels.get_active_duel_match(target)
        if active_duel is not None:
            opponent = duels.duel_opponent(target, match=active_duel)
            if opponent is None:
                raise ActionError(
                    "The active duel has no opposing contestant.",
                    code="duel_result_invalid",
                )
            duels.resolve_duel_defeat(
                active_duel,
                opponent,
                target,
                reason="scripted_defeat",
            )
            target.refresh_from_db()
            updated_target = target
            death_events = []
        else:
            updated_target, death_events = apply_player_death(
                player=target,
                origin_room=origin_room,
                killer=actor,
                target_text=target_text,
                room_text=room_text,
                death_token=death_token,
                cause="builder_forced",
                forced=True,
            )

        actor_payload = (
            serialize_actor(actor, actor.room).model_dump()
            if isinstance(actor, Player)
            else _actor_summary(actor)
        )
        target_payload = serialize_actor(updated_target, updated_target.room).model_dump()
        success_text = f"You snap {target.name} out of existence."

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./kill.success",
                    recipients=[actor.key],
                    data={
                        "actor": actor_payload,
                        "target": target_payload,
                        "message": message or "",
                    },
                    text=success_text,
                ),
                *death_events,
            ]
        )


class BuilderStatsAction:
    def execute(
        self,
        *,
        actor: Player,
        target_selector: str | None = None,
        runtime_world: World | None = None,
    ) -> ActionResult:
        target = _resolve_builder_character_target(
            actor=actor,
            target_selector=target_selector,
            runtime_world=runtime_world,
            allow_self=True,
        )
        target_payload, target_type = _serialize_builder_stats_target(target)
        world = _actor_world(actor, runtime_world=runtime_world)
        if not world:
            raise ActionError("No runtime world is available for stats.", code="no_world")
        world_payload = serialize_world(world)

        updated_actor = get_player_with_related(actor.id)
        actor_payload = serialize_actor(updated_actor, updated_actor.room).model_dump()
        text = _render_builder_stats_text(
            target_payload=target_payload,
            target_type=target_type,
            world_payload=world_payload,
        )

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./stats.success",
                    recipients=[updated_actor.key],
                    data={
                        "actor": actor_payload,
                        "target": target_payload,
                        "target_type": target_type,
                        "world": world_payload,
                    },
                    text=text,
                )
            ]
        )


class RegenAction:
    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str | None = None,
        resource: str | None = None,
        runtime_world: World | None = None,
    ) -> ActionResult:
        normalized_resource = _normalize_regen_resource(resource)
        resources = [normalized_resource] if normalized_resource else list(REGEN_RESOURCES)

        with transaction.atomic():
            target = _resolve_builder_character_target(
                actor=actor,
                target_selector=target_selector,
                runtime_world=runtime_world,
                allow_self=True,
            )
            if isinstance(target, Player):
                target = Player.objects.select_for_update().get(pk=target.pk)
            else:
                target = Mob.objects.select_for_update().get(pk=target.pk)

            previous_resources = _regen_resource_snapshot(target)
            update_fields: list[str] = []
            for resource_name in resources:
                max_value = _resource_max_for_target(target, f"{resource_name}_max")
                if int(getattr(target, resource_name, 0) or 0) != max_value:
                    setattr(target, resource_name, max_value)
                    update_fields.append(resource_name)

            if update_fields:
                target.save(update_fields=update_fields)

        target_payload, target_type = _serialize_builder_stats_target(target)
        actor_payload = _actor_payload_for_regen(actor)
        target_name = str(target_payload.get("name") or getattr(target, "name", None) or "target")
        resource_label = normalized_resource if normalized_resource else "resources"
        text = (
            f"Regenerated your {resource_label}."
            if isinstance(actor, (Player, Mob)) and actor.key == target.key
            else f"Regenerated {target_name}'s {resource_label}."
        )
        data = {
            "actor": actor_payload,
            "target": target_payload,
            "target_type": target_type,
            "resource": normalized_resource,
            "resources": resources,
            "previous_resources": previous_resources,
            "current_resources": _regen_resource_snapshot(target),
        }
        if isinstance(actor, Player):
            updated_actor = get_player_with_related(actor.id)
            recipient_key = updated_actor.key
            data["room"] = _get_single_room_payload(updated_actor).model_dump()
        else:
            recipient_key = actor.key

        events = [
            GameEvent(
                type="cmd./regen.success",
                recipients=[recipient_key],
                data=data,
                text=text,
            )
        ]
        if isinstance(target, Player) and target.key != recipient_key:
            updated_target = get_player_with_related(target.id)
            target_actor_payload = serialize_actor(updated_target, updated_target.room).model_dump()
            events.append(
                GameEvent(
                    type="notification.regen",
                    recipients=[updated_target.key],
                    data={"actor": target_actor_payload},
                    text=f"Your {resource_label} has been restored.",
                )
            )
        return ActionResult(events=events)


class SetStatAction:
    def execute(
        self,
        *,
        actor: Player | Room,
        target_selector: str,
        field_name: str,
        value: object,
        runtime_world: World | None = None,
    ) -> ActionResult:
        with transaction.atomic():
            if isinstance(actor, Room):
                if runtime_world is None:
                    raise ActionError(
                        "No runtime world is available for room-issued set commands.",
                        code="no_world",
                    )
                authored_world_id = runtime_world.context_id or runtime_world.id
                if actor.world_id != authored_world_id:
                    raise ActionError(
                        "The issuer room is not part of this runtime world.",
                        code="invalid_world_context",
                    )
                target = _resolve_room_character_target(
                    actor=actor,
                    target_selector=target_selector,
                    runtime_world=runtime_world,
                    allow_self=True,
                )
            else:
                target = _resolve_builder_character_target(
                    actor=actor,
                    target_selector=target_selector,
                    runtime_world=runtime_world,
                    allow_self=True,
                )

            target_model = Player if isinstance(target, Player) else Mob
            target_queryset = target_model.objects.select_for_update(
                of=("self",),
            ).select_related("world", "world__config")
            target_world = _actor_world(actor, runtime_world=runtime_world)
            if target_world is not None:
                target_queryset = target_queryset.filter(world_id=target_world.id)
            if isinstance(actor, Room):
                target_queryset = target_queryset.filter(room_id=actor.id)
            if target_model is Mob:
                target_queryset = target_queryset.filter(is_pending_deletion=False)
            target = target_queryset.filter(pk=target.pk).first()
            if target is None:
                if isinstance(actor, Room):
                    message = "Target is no longer in this room."
                else:
                    message = "Target is no longer in this runtime world."
                raise ActionError(message, code="invalid_target")

            previous_value, new_value, normalized_field = _set_character_stat_value(
                target=target,
                field_name=field_name,
                raw_value=value,
            )

        target_payload, target_type = _serialize_builder_stats_target(target)
        target_name = str(target_payload.get("name") or "target")
        rendered_value = (
            json.dumps(new_value, sort_keys=True)
            if isinstance(new_value, (dict, list, bool, type(None)))
            else str(new_value)
        )
        text = f"Set {target_name}'s {normalized_field} to {rendered_value}."

        if isinstance(actor, Player):
            updated_actor = get_player_with_related(actor.id)
            actor_payload = serialize_actor(updated_actor, updated_actor.room).model_dump()
            room_payload = _get_single_room_payload(updated_actor).model_dump()
            recipient_key = updated_actor.key
        else:
            actor_payload = _actor_summary(actor)
            room_payload = {
                "id": actor.id,
                "key": actor.key,
                "name": actor.name,
            }
            recipient_key = actor.key

        data = {
            "actor": actor_payload,
            "room": room_payload,
            "target": target_payload,
            "target_type": target_type,
            "field": normalized_field,
            "previous_value": previous_value,
            "new_value": new_value,
        }
        events = [
            GameEvent(
                type="cmd./set.success",
                recipients=[recipient_key],
                data=data,
                text=text,
            )
        ]
        if isinstance(target, Player) and target.key != recipient_key:
            events.append(
                GameEvent(
                    type="notification./set",
                    recipients=[target.key],
                    data={
                        "actor": target_payload,
                        "issuer": actor_payload,
                        "target": target_payload,
                        "target_type": "player",
                        "field": normalized_field,
                        "previous_value": previous_value,
                        "new_value": new_value,
                    },
                    text=f"Your {normalized_field} was set to {rendered_value}.",
                )
            )

        return ActionResult(
            events=events,
        )


class SetLevelAction:
    def _resolve_target(
        self,
        *,
        actor: Player,
        target_selector: str | None,
    ) -> Player | Mob:
        room = actor.room
        if not room:
            raise ActionError("You are nowhere. Cannot set levels.", code="no_room")

        normalized_target = str(target_selector or "").strip().lower()
        if not normalized_target or normalized_target in {"self", "me"}:
            return actor

        player_targets = _collect_room_player_targets(room, normalized_target, world=actor.world)
        mob_targets = _collect_room_mob_targets(room, normalized_target, world=actor.world)
        targets: list[Player | Mob] = [*player_targets, *mob_targets]
        if not targets:
            raise ActionError("Target not found in this room.", code="invalid_target")
        if len(targets) > 1:
            raise ActionError("Target is ambiguous.", code="ambiguous_target")
        return targets[0]

    def execute(
        self,
        *,
        actor: Player,
        level: int | str,
        target_selector: str | None = None,
    ) -> ActionResult:
        target = self._resolve_target(
            actor=actor,
            target_selector=target_selector,
        )
        leveling_config = get_world_leveling_config(getattr(target, "world", None))
        try:
            new_level = clamp_level(level, leveling_config)
        except LevelingConfigError as exc:
            raise ActionError(str(exc), code="invalid_level")

        previous_level = int(getattr(target, "level", 1) or 1)
        previous_experience = int(getattr(target, "experience", 0) or 0)

        if isinstance(target, Player):
            result = set_player_level(target, new_level, reset_resources=True)
            target.save(update_fields=["level", "experience", "health", "energy", "stamina"])
            target.refresh_from_db()
            target_data = serialize_actor(target, target.room).model_dump()
            target_type = "player"
            experience = target.experience
            experience_progress = result.experience_progress
            experience_needed = result.experience_needed
        else:
            target.level = new_level
            target.save(update_fields=["level"])
            progress = progress_for_experience(
                previous_experience,
                level=target.level,
                config_obj=leveling_config,
            )
            target_data = serialize_char_from_mob(target).model_dump()
            target_type = "mob"
            experience = None
            experience_progress = progress.experience_progress
            experience_needed = progress.experience_needed

        updated_actor = get_player_with_related(actor.id)
        actor_payload = serialize_actor(updated_actor, updated_actor.room)
        room_payload = _get_single_room_payload(updated_actor)
        target_name = getattr(target, "name", None) or "target"
        text = f"Set {target_name} to level {new_level}."

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./setlevel.success",
                    recipients=[updated_actor.key],
                    data={
                        "actor": actor_payload.model_dump(),
                        "room": room_payload.model_dump(),
                        "target": target_data,
                        "target_type": target_type,
                        "previous_level": previous_level,
                        "new_level": new_level,
                        "previous_experience": previous_experience,
                        "experience": experience,
                        "experience_progress": experience_progress,
                        "experience_needed": experience_needed,
                        "max_level": leveling_config.max_level,
                    },
                    text=text,
                )
            ]
        )


class SetClassAction:
    def _resolve_target(
        self,
        *,
        actor: Player | Room,
        runtime_world: World | None,
        target_selector: str | None,
    ) -> Player:
        room = _actor_room(actor)
        if not room:
            raise ActionError("You are nowhere. Cannot set classes.", code="no_room")

        normalized_target = str(target_selector or "").strip().lower()
        if isinstance(actor, Player) and (not normalized_target or normalized_target in {"self", "me"}):
            return actor
        if not normalized_target or normalized_target in {"self", "me"}:
            raise ActionError("Target player is required.", code="invalid_target")

        targets = _collect_room_player_targets(
            room,
            normalized_target,
            world=_actor_world(actor, runtime_world=runtime_world),
        )
        if not targets:
            raise ActionError("Player not found in this room.", code="invalid_target")
        if len(targets) > 1:
            raise ActionError("Player target is ambiguous.", code="ambiguous_target")
        return targets[0]

    def execute(
        self,
        *,
        actor: Player | Room,
        class_selector: str,
        target_selector: str | None = None,
        runtime_world: World | None = None,
    ) -> ActionResult:
        target = self._resolve_target(
            actor=actor,
            runtime_world=runtime_world,
            target_selector=target_selector,
        )
        new_class = _resolve_player_class_key(target.world, class_selector)
        previous_class = str(target.archetype or "")

        with transaction.atomic():
            target = Player.objects.select_for_update().get(pk=target.pk)
            previous_abilities = list(target.known_abilities or [])
            target.archetype = new_class
            stats = compute_stats(
                target.level,
                target.archetype,
                char=target,
                world=target.world,
            )
            target.health = max(1, int(stats.get("health_max") or 1))
            target.energy = int(stats.get("energy_max") or 0)
            target.stamina = int(stats.get("stamina_max") or 0)
            target.known_abilities = []
            target.ability_hotkeys = {}
            target.ability_cooldowns = {}
            target.save(update_fields=[
                "archetype",
                "health",
                "energy",
                "stamina",
                "known_abilities",
                "ability_hotkeys",
                "ability_cooldowns",
            ])
            CombatEncounter.objects.filter(
                player=target,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exclude(pending_player_ability={}).update(pending_player_ability={})

        updated_target = get_player_with_related(target.id)
        target_payload = serialize_actor(updated_target, updated_target.room)
        class_labels = get_world_stat_system(updated_target.world)["labels"]["classes"]
        class_label = class_labels.get(new_class, new_class)
        target_name = getattr(updated_target, "name", None) or "target"
        text = f"Set {target_name}'s class to {class_label}."

        if isinstance(actor, Player):
            updated_actor = get_player_with_related(actor.id)
            actor_payload = serialize_actor(updated_actor, updated_actor.room).model_dump()
            room_payload = _get_single_room_payload(updated_actor).model_dump()
            recipient_key = updated_actor.key
        else:
            actor_payload = _actor_summary(actor)
            room_payload = {"key": actor.key, "name": actor.name}
            recipient_key = actor.key

        return ActionResult(events=[
            GameEvent(
                type="cmd./setclass.success",
                recipients=[recipient_key],
                data={
                    "actor": actor_payload,
                    "room": room_payload,
                    "target": target_payload.model_dump(),
                    "target_type": "player",
                    "previous_class": previous_class,
                    "new_class": new_class,
                    "class_label": class_label,
                    "unlearned_abilities": previous_abilities,
                },
                text=text,
            ),
            ability_prepare_state_event(updated_target),
        ])


class CmdAction:
    @staticmethod
    def _dispatch_actor_ref(actor: BuilderCommandActor) -> tuple[str, int]:
        return _actor_kind(actor), actor.id

    def _resolve_scope_actor(
        self,
        *,
        actor: BuilderCommandActor,
        scope: str,
        runtime_world: World | None = None,
    ) -> BuilderCommandActor:
        if scope == "room":
            room = _actor_room(actor)
            if not room:
                raise ActionError("There is no current room for this command.", code="no_room")
            return room
        if scope == "zone":
            zone = _actor_zone(actor)
            if not zone:
                raise ActionError("There is no current zone for this command.", code="no_zone")
            return zone
        if scope == "world":
            world = _actor_world(actor, runtime_world=runtime_world)
            if not world:
                raise ActionError("There is no current world for this command.", code="no_world")
            return world
        raise ActionError(
            "Usage: /cmd <room|zone|world|target> -- <command>",
            code="invalid_args",
        )

    def _dispatch_segment(
        self,
        *,
        dispatch_actor: BuilderCommandActor,
        segment: str,
        issuer_scope: str | None = None,
        runtime_world: World | None = None,
        skip_triggers: bool = False,
        script_source: bool = False,
    ) -> str | None:
        rendered_segment = _render_command_segment(segment, actor=dispatch_actor)
        command_token = _first_token(rendered_segment)
        if not command_token:
            return None

        resolved = resolve_text_handler(command_token, include_builder=True)
        dispatch_actor_type, dispatch_actor_id = self._dispatch_actor_ref(dispatch_actor)
        resolved_social = None
        if not resolved:
            from spawns.socials import resolve_social_for_command

            if dispatch_actor_type in ("player", "mob"):
                resolved_social = resolve_social_for_command(
                    runtime_world or getattr(dispatch_actor, "world", None),
                    command_token,
                )
            if resolved_social is None:
                return f"Unknown command: {command_token}"
        else:
            resolved_command, handler = resolved
            if dispatch_actor_type not in getattr(handler, "supported_actor_types", ("player",)):
                return f"{dispatch_actor_type.capitalize()}s cannot execute {resolved_command}."

        dispatched_messages: list[dict] = []
        command_type = "text"
        if resolved_social is not None:
            tokens = rendered_segment.split()
            command_type = "social"
            payload: dict[str, object] = {
                "social": resolved_social["command"],
                "target": tokens[1] if len(tokens) > 1 else None,
            }
        else:
            payload = {"text": rendered_segment}
        if issuer_scope:
            payload["issuer_scope"] = issuer_scope
        if runtime_world:
            payload["world_id"] = runtime_world.id
        if skip_triggers:
            payload["skip_triggers"] = True

        try:
            dispatch_command(
                command_type=command_type,
                actor_type=dispatch_actor_type,
                actor_id=dispatch_actor_id,
                payload=payload,
                script_source=script_source,
                published_messages=dispatched_messages,
            )
        except (ActorNotFoundError, HandlerNotFoundError, ValueError) as err:
            return str(err)
        return _first_dispatched_error(dispatched_messages)

    def execute(
        self,
        *,
        actor: BuilderCommandActor,
        target_selector: str,
        cmd: str,
        runtime_world: World | None = None,
        skip_triggers: bool = False,
        script_source: bool = False,
    ) -> ActionResult:
        normalized_target = str(target_selector or "").strip().lower()
        if not normalized_target:
            raise ActionError(
                "Usage: /cmd <room|zone|world|target> -- <command>",
                code="invalid_args",
            )

        chained_segments = _split_chained_commands(cmd or "")
        if not chained_segments:
            raise ActionError(
                "Usage: /cmd <room|zone|world|target> -- <command>",
                code="invalid_args",
            )

        dispatch_actor: BuilderCommandActor = actor
        issuer_scope: str | None = None
        target_data: dict[str, object]

        if normalized_target in CMD_SCOPE_TARGETS:
            issuer_scope = normalized_target
            dispatch_actor = self._resolve_scope_actor(
                actor=actor,
                scope=normalized_target,
                runtime_world=runtime_world,
            )
            target_data = {
                "type": "scope",
                "scope": normalized_target,
                "key": dispatch_actor.key,
                "name": getattr(dispatch_actor, "name", normalized_target),
            }
        else:
            room = _actor_room(actor)
            if not room:
                raise ActionError("There is no current room for target commands.", code="no_room")
            if normalized_target.startswith("mob:"):
                normalized_target = normalized_target.split(":", 1)[1].strip().lower()
            targets = _collect_room_mob_targets(
                room,
                normalized_target,
                world=_actor_world(actor, runtime_world=runtime_world),
            )
            if not targets:
                raise ActionError("Target not found.", code="invalid_target")
            target_mob = targets[0]
            dispatch_actor = target_mob
            target_data = {
                "type": "mob",
                "key": target_mob.key,
                "name": _entity_name(target_mob),
            }

        errors: list[str] = []
        for segment in chained_segments:
            dispatched_error = self._dispatch_segment(
                dispatch_actor=dispatch_actor,
                segment=segment,
                issuer_scope=issuer_scope,
                runtime_world=_actor_world(actor, runtime_world=runtime_world),
                skip_triggers=skip_triggers,
                script_source=script_source,
            )
            if dispatched_error:
                errors.append(dispatched_error)

        text = None
        if errors:
            text = "\n".join(f"Error: {error}" for error in errors)

        data = {
            "actor": _actor_summary(actor),
            "target": target_data,
            "cmd": cmd,
            "errors": errors,
        }

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./cmd.success",
                    recipients=[actor.key],
                    data=data,
                    text=text,
                )
            ]
        )


@dataclass(frozen=True)
class _TransferTargetRef:
    target_type: str
    target_id: int
    required_room_id: int | None = None


class TransferAction:
    """Move one player or mob without applying ordinary movement rules."""

    @staticmethod
    def _runtime_authored_world_id(runtime_world: World) -> int:
        return runtime_world.context_id or runtime_world.id

    def _validate_runtime_context(
        self,
        *,
        actor: Player | Mob | Room,
        issuer_room: Room,
        runtime_world: World,
    ) -> None:
        if issuer_room.world_id != self._runtime_authored_world_id(runtime_world):
            raise ActionError(
                "The issuer room is not part of this runtime world.",
                code="invalid_world_context",
            )
        if isinstance(actor, (Player, Mob)) and actor.world_id != runtime_world.id:
            raise ActionError(
                "The issuer is not part of this runtime world.",
                code="invalid_world_context",
            )

    def _resolve_destination(
        self,
        *,
        issuer_room: Room,
        runtime_world: World,
        selector: str,
    ) -> Room:
        normalized = str(selector or "").strip().lower()
        if not normalized:
            raise ActionError(
                "Usage: /transfer <target> <room_id|room@x,y,z|direction|here>",
                code="invalid_args",
            )

        destination = None
        if normalized == "here":
            destination = issuer_room

        direction = _normalize_jump_direction(normalized)
        if destination is None and direction:
            destination = getattr(issuer_room, direction, None)
            if not destination:
                raise ActionError(
                    f"There is no exit {direction}.",
                    code="no_exit",
                )
        if destination is None and normalized.isdigit():
            # Bare WR1 room ids were world-local ids. Prefer that legacy
            # meaning when a database id and relative id happen to collide.
            destination = issuer_room.world.rooms.filter(
                relative_id=int(normalized),
            ).first()

        if destination is None:
            room_id = resolve_room_ref_id(world=issuer_room.world, value=normalized)
            if room_id is not None:
                destination = issuer_room.world.rooms.filter(pk=room_id).first()

        if destination is None:
            raise ActionError(
                "Invalid room reference.",
                code="invalid_room",
            )
        if destination.world_id != self._runtime_authored_world_id(runtime_world):
            raise ActionError(
                "The destination room is not part of this runtime world.",
                code="invalid_world_context",
            )
        return destination

    @staticmethod
    def _limited_player_name_matches(
        *,
        runtime_world: World,
        selector: str,
    ) -> list[int]:
        players = Player.objects.filter(
            world=runtime_world,
            in_game=True,
        )
        return list(
            players.filter(name__iexact=selector)
            .order_by("id")
            .values_list("id", flat=True)[:2]
        )

    def _resolve_target_ref(
        self,
        *,
        actor: Player | Mob | Room,
        issuer_room: Room,
        runtime_world: World,
        selector: str,
    ) -> _TransferTargetRef:
        normalized = str(selector or "").strip().lower()
        if not normalized:
            raise ActionError("Target is required.", code="invalid_target")

        if normalized in {"self", "me"}:
            if isinstance(actor, Player):
                return _TransferTargetRef("player", actor.id)
            if isinstance(actor, Mob):
                return _TransferTargetRef("mob", actor.id, issuer_room.id)
            raise ActionError("Room actors must specify a target.", code="invalid_target")

        parsed_key = _parse_character_key(normalized)
        if parsed_key is not None:
            target_type, target_id = parsed_key
            if target_type == "player":
                exists = Player.objects.filter(
                    pk=target_id,
                    world=runtime_world,
                    in_game=True,
                ).exists()
                if exists:
                    return _TransferTargetRef("player", target_id)
            else:
                exists = Mob.objects.filter(
                    pk=target_id,
                    world=runtime_world,
                    room=issuer_room,
                    is_pending_deletion=False,
                ).exists()
                if exists:
                    return _TransferTargetRef("mob", target_id, issuer_room.id)
            raise ActionError(
                "Target not found in this runtime world.",
                code="invalid_target",
            )

        player_ids = self._limited_player_name_matches(
            runtime_world=runtime_world,
            selector=normalized,
        )
        if len(player_ids) > 1:
            raise ActionError("Player target is ambiguous.", code="ambiguous_target")
        if player_ids:
            return _TransferTargetRef("player", player_ids[0])

        local_target = find_room_char_target(
            issuer_room,
            normalized,
            viewer=actor if isinstance(actor, Player) else None,
            world=runtime_world,
        )
        if isinstance(local_target, Player):
            return _TransferTargetRef(
                "player",
                local_target.id,
                issuer_room.id,
            )
        if isinstance(local_target, Mob):
            return _TransferTargetRef(
                "mob",
                local_target.id,
                issuer_room.id,
            )

        raise ActionError("Target not found.", code="invalid_target")

    @staticmethod
    def _active_encounters_for_update(target_ref: _TransferTargetRef):
        encounters = CombatEncounter.objects.select_for_update(nowait=True).filter(
            status=CombatEncounter.STATUS_ACTIVE,
            duel_match_id__isnull=True,
        )
        if target_ref.target_type == "player":
            encounters = encounters.filter(player_id=target_ref.target_id)
        else:
            encounters = encounters.filter(mob_id=target_ref.target_id)
        return list(encounters.order_by("id"))

    @staticmethod
    def _active_pvp_encounter_ids(
        *,
        target_ref: _TransferTargetRef,
        runtime_world: World,
    ) -> list[int]:
        if target_ref.target_type != "player":
            return []
        return list(
            CombatParticipant.objects.filter(
                player_id=target_ref.target_id,
                is_active=True,
                encounter__world=runtime_world,
                encounter__status=CombatEncounter.STATUS_ACTIVE,
                encounter__duel_match_id__isnull=False,
            )
            .order_by("encounter_id")
            .values_list("encounter_id", flat=True)
            .distinct()
        )

    @classmethod
    def _finish_active_pvp_encounters(
        cls,
        *,
        target_ref: _TransferTargetRef,
        runtime_world: World,
    ) -> tuple[list[int], list[GameEvent]]:
        from spawns.actions.pvp import finish_pvp_encounter

        encounter_ids = cls._active_pvp_encounter_ids(
            target_ref=target_ref,
            runtime_world=runtime_world,
        )
        events: list[GameEvent] = []
        for encounter_id in encounter_ids:
            events.extend(finish_pvp_encounter(encounter_id))
        finished_ids = list(
            CombatEncounter.objects.filter(
                pk__in=encounter_ids,
                status=CombatEncounter.STATUS_FINISHED,
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        return finished_ids, events

    @staticmethod
    def _finish_active_encounters(encounters: list[CombatEncounter]) -> list[int]:
        encounter_ids = [encounter.id for encounter in encounters]
        if not encounter_ids:
            return []
        ActiveEffect.objects.filter(
            encounter_id__in=encounter_ids,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
        ).delete()
        CombatEncounter.objects.filter(pk__in=encounter_ids).update(
            status=CombatEncounter.STATUS_FINISHED,
            next_resolution_ts=None,
            pending_player_ability={},
            pending_mob_ability={},
            pending_flee={},
        )
        return encounter_ids

    @staticmethod
    def _lock_target(
        *,
        target_ref: _TransferTargetRef,
        runtime_world: World,
    ) -> Player | Mob:
        if target_ref.target_type == "player":
            target = (
                Player.objects.select_for_update(of=("self",), nowait=True)
                .select_related("room")
                .filter(
                    pk=target_ref.target_id,
                    world=runtime_world,
                    in_game=True,
                )
                .first()
            )
        else:
            target = (
                Mob.objects.select_for_update(of=("self",), nowait=True)
                .select_related("room")
                .filter(
                    pk=target_ref.target_id,
                    world=runtime_world,
                    is_pending_deletion=False,
                )
                .first()
            )
        if target is None:
            raise ActionError(
                "Target is no longer part of this runtime world.",
                code="invalid_target",
            )
        if (
            target_ref.required_room_id is not None
            and target.room_id != target_ref.required_room_id
        ):
            raise ActionError(
                "Target is no longer in the issuer's room.",
                code="invalid_target",
            )
        authored_world_id = TransferAction._runtime_authored_world_id(runtime_world)
        if target.room_id and target.room.world_id != authored_world_id:
            raise ActionError(
                "The target's room is not part of this runtime world.",
                code="invalid_world_context",
            )
        return target

    @staticmethod
    def _room_player_recipient_ids(
        *,
        runtime_world: World,
        room_id: int | None,
        transferred_player_id: int | None,
    ) -> list[int]:
        if room_id is None:
            return []
        players = Player.objects.filter(
            world=runtime_world,
            room_id=room_id,
            in_game=True,
        )
        if transferred_player_id is not None:
            players = players.exclude(pk=transferred_player_id)
        return list(players.order_by("id").values_list("id", flat=True))

    @staticmethod
    def _combat_effect_state_events(
        encounters: list[CombatEncounter],
    ) -> list[GameEvent]:
        player_ids = sorted({encounter.player_id for encounter in encounters})
        if not player_ids:
            return []
        players = Player.objects.filter(pk__in=player_ids).order_by("id")
        return [
            GameEvent(
                type="player.combat_effects.update",
                recipients=[player.key],
                data={
                    "target": {"key": player.key},
                    "active_effects": active_combat_effects(player),
                },
            )
            for player in players
        ]

    def execute(
        self,
        *,
        actor: Player | Mob | Room,
        target_selector: str,
        room_selector: str,
        runtime_world: World | None = None,
        trigger_step: bool = False,
    ) -> ActionResult:
        issuer_room = _actor_room(actor)
        if issuer_room is None:
            raise ActionError(
                "There is no current room for this transfer.",
                code="no_room",
            )

        resolved_runtime_world = _actor_world(actor, runtime_world=runtime_world)
        if resolved_runtime_world is None:
            raise ActionError(
                "No runtime world is available for this transfer.",
                code="no_world",
            )
        self._validate_runtime_context(
            actor=actor,
            issuer_room=issuer_room,
            runtime_world=resolved_runtime_world,
        )

        destination = self._resolve_destination(
            issuer_room=issuer_room,
            runtime_world=resolved_runtime_world,
            selector=room_selector,
        )
        target_ref = self._resolve_target_ref(
            actor=actor,
            issuer_room=issuer_room,
            runtime_world=resolved_runtime_world,
            selector=target_selector,
        )

        pvp_finished_encounter_ids: list[int] = []
        pvp_cleanup_events: list[GameEvent] = []
        door_cancellation_events: list[GameEvent] = []
        if target_ref.target_type == "player":
            target_room_id = (
                Player.objects.filter(
                    pk=target_ref.target_id,
                    world=resolved_runtime_world,
                    in_game=True,
                )
                .values_list("room_id", flat=True)
                .first()
            )
            if target_room_id is not None and target_room_id != destination.id:
                if (
                    trigger_step
                    and self._active_pvp_encounter_ids(
                        target_ref=target_ref,
                        runtime_world=resolved_runtime_world,
                    )
                ):
                    raise ActionError(
                        "The target is busy in player combat. Try the transfer again.",
                        code="target_busy",
                    )
                (
                    pvp_finished_encounter_ids,
                    pvp_cleanup_events,
                ) = self._finish_active_pvp_encounters(
                    target_ref=target_ref,
                    runtime_world=resolved_runtime_world,
                )

        try:
            with transaction.atomic():
                # Acquire both encounter and target rows without waiting. This
                # avoids joining either encounter-first resolution cycles or
                # player-first combat-start cycles; callers can retry instead.
                active_encounters = self._active_encounters_for_update(target_ref)
                target = self._lock_target(
                    target_ref=target_ref,
                    runtime_world=resolved_runtime_world,
                )
                origin_room_id = target.room_id
                origin_room = target.room if target.room_id else None
                moved = origin_room_id != destination.id

                if (
                    moved
                    and isinstance(target, Player)
                    and self._active_pvp_encounter_ids(
                        target_ref=target_ref,
                        runtime_world=resolved_runtime_world,
                    )
                ):
                    raise ActionError(
                        "The target's combat state changed. Try the transfer again.",
                        code="target_busy",
                    )

                if moved and isinstance(target, Player):
                    from spawns.actions.doors import (
                        cancel_pending_player_door_action,
                    )

                    door_cancellation_events.extend(
                        cancel_pending_player_door_action(
                            player=target,
                            code="actor_transferred",
                            message=(
                                "You stop working with the door as you are "
                                "transferred."
                            ),
                        )
                    )

                finished_encounter_ids = list(pvp_finished_encounter_ids)
                combat_effect_events: list[GameEvent] = []
                ability_prepare_events = list(pvp_cleanup_events)
                if moved:
                    finished_encounter_ids.extend(
                        self._finish_active_encounters(active_encounters)
                    )

                    target.room_id = destination.id
                    target_update_fields = ["room"]
                    if isinstance(target, Player):
                        target.location_sequence = (
                            int(target.location_sequence or 0) + 1
                        )
                        target_update_fields.append("location_sequence")
                    target.save(update_fields=target_update_fields)
                    if isinstance(target, Player):
                        target.viewed_rooms.add(destination.id)
                    combat_effect_events = self._combat_effect_state_events(
                        active_encounters,
                    )
                    ability_prepare_events.extend(
                        ability_prepare_state_events_for_players(
                            encounter.player_id
                            for encounter in active_encounters
                        )
                    )
                elif isinstance(target, Player):
                    target.viewed_rooms.add(destination.id)

                transferred_player_id = (
                    target.id if isinstance(target, Player) else None
                )
                is_visible = not isinstance(target, Player) or not target.is_invisible
                origin_recipient_ids = []
                destination_recipient_ids = []
                if moved and is_visible:
                    origin_recipient_ids = self._room_player_recipient_ids(
                        runtime_world=resolved_runtime_world,
                        room_id=origin_room_id,
                        transferred_player_id=transferred_player_id,
                    )
                    destination_recipient_ids = self._room_player_recipient_ids(
                        runtime_world=resolved_runtime_world,
                        room_id=destination.id,
                        transferred_player_id=transferred_player_id,
                    )

                origin_payload = _room_reference_payload(origin_room)
                destination_payload = _room_reference_payload(destination)

                if target_ref.target_type == "player":
                    updated_target = get_player_with_related(target_ref.target_id)
                    transferred_payload = serialize_char_from_player(
                        updated_target,
                    ).model_dump()
                    target_name = updated_target.name or "target"
                    look_result = LookAction().execute(
                        updated_target.id,
                        isolate_runtime_world=True,
                    )
                    look_event = next(
                        (
                            event
                            for event in look_result.events
                            if event.type == "cmd.look.success"
                        ),
                        None,
                    )
                    if look_event is None:
                        raise ActionError(
                            "Could not build the transferred player's room state.",
                            code="state_sync_failed",
                        )
                    if moved:
                        target_state_events = [
                            GameEvent(
                                type="affect.transfer",
                                recipients=look_event.recipients,
                                data={
                                    **look_event.data,
                                    "room": look_event.data["target"],
                                },
                                text=look_event.text,
                            )
                        ]
                    else:
                        target_state_events = [look_event]
                else:
                    updated_target = (
                        Mob.objects.select_related("definition", "room", "world")
                        .get(pk=target_ref.target_id)
                    )
                    transferred_payload = serialize_char_from_mob(
                        updated_target,
                    ).model_dump()
                    target_name = updated_target.name or "target"
                    target_state_events = []
        except OperationalError as exc:
            cause = getattr(exc, "__cause__", None)
            sqlstate = getattr(cause, "sqlstate", None) or getattr(
                cause,
                "pgcode",
                None,
            )
            if sqlstate == "55P03":
                raise ActionError(
                    "The target is busy. Try the transfer again.",
                    code="target_busy",
                ) from exc
            raise

        movement_data = {
            "actor": transferred_payload,
            "origin_room": origin_payload,
            "destination_room": destination_payload,
            TRANSFER_RUNTIME_WORLD_KEY: resolved_runtime_world.id,
        }
        if isinstance(updated_target, Player):
            movement_data[TRANSFER_LOCATION_SEQUENCE_KEY] = int(
                updated_target.location_sequence or 0
            )
            movement_data[PLAYER_ROOM_ENTER_EMITTED_KEY] = True
        events: list[GameEvent] = [
            *door_cancellation_events,
            GameEvent(
                type="cmd./transfer.success",
                recipients=[actor.key],
                data={
                    "transferred": transferred_payload,
                    "transferred_type": target_ref.target_type,
                    "target": destination_payload,
                    "target_type": "room",
                    "origin_room": origin_payload,
                    "destination_room": destination_payload,
                    "moved": moved,
                    "finished_encounter_ids": finished_encounter_ids,
                },
                text=f"You transfer {target_name} to {destination.name}.",
            )
        ]
        if moved and is_visible:
            events.append(
                GameEvent(
                    type="notification./transfer.exit",
                    recipients=[
                        f"player.{player_id}"
                        for player_id in origin_recipient_ids
                    ],
                    data=movement_data,
                    text=f"{target_name} disappears.",
                )
            )
        events.extend(target_state_events)
        events.extend(combat_effect_events)
        events.extend(ability_prepare_events)
        if moved:
            events.append(
                GameEvent(
                    type="notification./transfer.enter",
                    recipients=[
                        f"player.{player_id}"
                        for player_id in destination_recipient_ids
                    ],
                    data=movement_data,
                    text=f"{target_name} appears." if is_visible else None,
                )
            )
            if isinstance(updated_target, Player):
                events.append(
                    player_room_enter_event(
                        player=updated_target,
                        origin_room_id=origin_room_id,
                        destination_room_id=destination.id,
                        source="transfer",
                    )
                )

        return ActionResult(
            events=events,
            data={
                "target_id": target_ref.target_id,
                "target_key": updated_target.key,
                "target_type": target_ref.target_type,
                "moved": moved,
            },
        )


class JumpAction:
    def execute(
        self,
        *,
        player_id: int,
        room_selector: str,
    ) -> ActionResult:
        normalized_selector = (room_selector or "").strip()
        if not normalized_selector:
            raise ActionError("Usage: /jump <room_id|direction>", code="invalid_args")
        jump_direction = _normalize_jump_direction(normalized_selector)
        room_selector_id = None if jump_direction else _parse_room_selector(normalized_selector)
        door_cancellation_events: list[GameEvent] = []

        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot jump.", code="no_room")

            origin_room_id = player.room_id
            try:
                origin_room = Room.objects.select_related(
                    "world",
                    *adv_consts.DIRECTIONS,
                ).get(pk=origin_room_id)
            except Room.DoesNotExist:
                raise ActionError("Current room is invalid.", code="invalid_room")

            room_world = origin_room.world
            target_room = (
                getattr(origin_room, jump_direction, None)
                if jump_direction
                else _resolve_room_in_world(room_world, room_selector_id)
            )
            if not target_room:
                if jump_direction:
                    raise ActionError("You cannot jump that way.", code="no_exit")
                raise ActionError("Invalid room ID.", code="invalid_room")

            if target_room.id != origin_room_id:
                from spawns.actions.doors import (
                    cancel_pending_player_door_action,
                )

                door_cancellation_events.extend(
                    cancel_pending_player_door_action(
                        player=player,
                        code="actor_moved",
                        message="You stop working with the door as you jump.",
                    )
                )
            if player.room_id != target_room.id:
                player.room_id = target_room.id
                player.location_sequence = (
                    int(player.location_sequence or 0) + 1
                )
            player.last_action_ts = timezone.now()
            player.save(
                update_fields=[
                    "room",
                    "location_sequence",
                    "last_action_ts",
                ]
            )
            player.viewed_rooms.add(target_room.id)

            origin_recipients: list[int] = []
            destination_recipients: list[int] = []
            if not player.is_invisible:
                origin_recipients = list(
                    Player.objects.filter(
                        world_id=player.world_id,
                        room_id=origin_room_id,
                        in_game=True,
                    )
                    .exclude(pk=player.id)
                    .values_list("id", flat=True)
                )
                destination_recipients = list(
                    Player.objects.filter(
                        world_id=player.world_id,
                        room_id=target_room.id,
                        in_game=True,
                    )
                    .exclude(pk=player.id)
                    .values_list("id", flat=True)
                )

        updated_player = get_player_with_related(player_id)
        room_payload = _get_single_room_payload(updated_player).model_dump()
        actor_payload = serialize_actor(updated_player, updated_player.room).model_dump()

        events: list[GameEvent] = list(door_cancellation_events)
        actor_name = updated_player.name
        actor_char = serialize_char_from_player(updated_player).model_dump()

        if origin_recipients:
            events.append(
                GameEvent(
                    type="notification./jump.exit",
                    recipients=[f"player.{recipient_id}" for recipient_id in origin_recipients],
                    data={"actor": actor_char},
                    text=f"{actor_name} disappears in a flash of white light.",
                )
            )

        events.append(
            GameEvent(
                type="cmd./jump.success",
                recipients=[updated_player.key],
                data={
                    "actor": actor_payload,
                    "target": room_payload,
                    "target_type": "room",
                    "room": room_payload,
                },
                text=(
                    "You launch yourself very high in the air and land in "
                    f"{updated_player.room.name}, in a satisfying thump."
                ),
            )
        )
        if origin_room_id != updated_player.room_id:
            events.append(
                player_room_enter_event(
                    player=updated_player,
                    origin_room_id=origin_room_id,
                    destination_room_id=updated_player.room_id,
                    source="jump",
                    direction=jump_direction,
                )
            )
        events.append(ability_prepare_state_event(updated_player))

        if destination_recipients:
            events.append(
                GameEvent(
                    type="notification./jump.enter",
                    recipients=[f"player.{recipient_id}" for recipient_id in destination_recipients],
                    data={"actor": actor_char},
                    text=f"{actor_name} appears in a flash of white light.",
                )
            )

        return ActionResult(events=events)


class InvisibleAction:
    def execute(self, *, player_id: int) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            player.is_invisible = not player.is_invisible
            player.save(update_fields=["is_invisible"])

        updated_player = get_player_with_related(player_id)
        text = (
            "You are now invisible."
            if updated_player.is_invisible
            else "You are now visible."
        )
        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./invisible.success",
                    recipients=[updated_player.key],
                    data={
                        "actor": serialize_actor(
                            updated_player,
                            updated_player.room,
                        ).model_dump(),
                        "is_invisible": updated_player.is_invisible,
                    },
                    text=text,
                )
            ]
        )
