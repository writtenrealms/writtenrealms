from __future__ import annotations

import json
import re

from builders.models import ItemTemplate, MobTemplate
from core.leveling import (
    LevelingConfigError,
    clamp_level,
    get_world_leveling_config,
    progress_for_experience,
    set_player_level,
)
from core.model_mixins import CharMixin, ItemMixin, MobMixin
from core.scoped_state import (
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
from core.utils import format_actor_msg
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers as drf_serializers

from spawns.ai_sidecar import (
    maybe_enqueue_ai_sidecar_mob_destroyed,
    maybe_enqueue_ai_sidecar_mob_spawned,
)
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.handlers.registry import (
    ActorNotFoundError,
    HandlerNotFoundError,
    dispatch_command,
    resolve_text_handler,
)
from spawns.models import Equipment, Item, Mob, Player
from spawns.serializers import LoadTemplateSerializer
from spawns.state_payloads import (
    door_state_lookup,
    get_player_with_related,
    room_payload_key_for,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_room,
)
from worlds.models import Room

ECHO_SCOPES = ("room", "zone", "world")
CMD_SCOPE_TARGETS = ("room", "zone", "world")


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
    if not keywords and getattr(entity, "template", None):
        keywords = entity.template.keywords or ""
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
    template = getattr(entity, "template", None)
    if template and template.name:
        return template.name
    return "target"


def _get_single_room_payload(player: Player):
    room = player.room
    if not room:
        return serialize_room(None, {}, {})
    room_key_lookup = {room.id: room_payload_key_for(room)}
    door_states = door_state_lookup(player.world, [room.id])
    return serialize_room(room, room_key_lookup, door_states, viewer=player)


def _collect_purge_targets(player: Player, selector: str) -> list[Item | Mob]:
    selector = selector.strip().lower()
    room = player.room

    if selector.startswith("mob."):
        try:
            mob_id = int(selector.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        mob = room.mobs.filter(pk=mob_id).first()
        return [mob] if mob else []

    if selector.startswith("item."):
        try:
            item_id = int(selector.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        item = player.inventory.filter(pk=item_id, is_pending_deletion=False).first()
        if item:
            return [item]
        item = room.inventory.filter(pk=item_id, is_pending_deletion=False).first()
        return [item] if item else []

    room_mobs = list(room.mobs.select_related("definition", "template"))
    room_items = list(
        room.inventory.filter(is_pending_deletion=False).select_related("definition", "template", "currency")
    )
    inventory_items = list(
        player.inventory.filter(is_pending_deletion=False).select_related("definition", "template", "currency")
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


def _purge_mob_cleanly(
    *,
    mob: Mob,
    source: str,
    trigger_actor_key: str | None = None,
) -> None:
    maybe_enqueue_ai_sidecar_mob_destroyed(
        mob=mob,
        source=source,
        trigger_actor_key=trigger_actor_key,
        reason="purge",
    )

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


def _collect_room_mob_targets(room: Room, selector: str) -> list[Mob]:
    normalized = selector.strip().lower()
    if not normalized:
        return []

    if normalized.startswith("mob."):
        try:
            mob_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        mob = room.mobs.select_related("definition", "template").filter(pk=mob_id).first()
        return [mob] if mob else []

    room_mobs = list(room.mobs.select_related("definition", "template"))
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


def _collect_room_player_targets(room: Room, selector: str) -> list[Player]:
    normalized = selector.strip().lower()
    if not normalized:
        return []

    if normalized.startswith("player."):
        try:
            player_id = int(normalized.split(".", 1)[1])
        except (TypeError, ValueError):
            return []
        player = room.players.filter(pk=player_id).first()
        return [player] if player else []

    room_players = list(room.players.select_related("world", "world__config", "user"))
    return [player for player in room_players if _player_matches(player, normalized)]


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


def _actor_kind(actor: Player | Mob) -> str:
    return "player" if isinstance(actor, Player) else "mob"


def _actor_summary(actor: Player | Mob) -> dict[str, object]:
    return {
        "key": actor.key,
        "name": getattr(actor, "name", "Unknown"),
        "char_type": _actor_kind(actor),
    }


def _render_command_segment(
    segment: str,
    *,
    actor: Player | Mob,
    character: Player | None = None,
    quest_instance=None,
) -> str:
    rendered = format_actor_msg(
        segment,
        actor,
        character=character,
        quest_instance=quest_instance,
    )
    return str(rendered or segment).strip()


def _collect_scope_player_keys(actor: Player | Mob, scope: str) -> list[str]:
    world_id = getattr(actor, "world_id", None)
    if not world_id:
        raise ActionError("You are nowhere. Cannot echo.", code="no_world")

    qs = Player.objects.filter(world_id=world_id, in_game=True)
    normalized_scope = scope.strip().lower()

    if normalized_scope == "room":
        room_id = getattr(actor, "room_id", None)
        if not room_id:
            raise ActionError("You are nowhere. Cannot echo to room.", code="no_room")
        qs = qs.filter(room_id=room_id)
    elif normalized_scope == "zone":
        room = getattr(actor, "room", None)
        zone_id = getattr(room, "zone_id", None)
        if not zone_id:
            raise ActionError("You are nowhere. Cannot echo to zone.", code="no_zone")
        qs = qs.filter(room__zone_id=zone_id)
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


def _item_template_field_names() -> list[str]:
    names: list[str] = []
    for field in ItemMixin._meta.fields:
        if field.name == "id":
            continue
        names.append(field.name)
    return names


def _mob_template_field_names() -> list[str]:
    names: dict[str, bool] = {}
    for field in CharMixin._meta.fields:
        if field.name in ("id", "health", "energy", "stamina", "group_id"):
            continue
        names[field.name] = True
    for field in MobMixin._meta.fields:
        if field.name == "id":
            continue
        names[field.name] = True
    return list(names.keys())


def _template_update_values(template, field_names: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for field_name in field_names:
        values[field_name] = getattr(template, field_name)
    return values


def _normalize_values_for_model(model_class, values: dict[str, object]) -> dict[str, object]:
    normalized = dict(values)
    for field_name, value in normalized.items():
        model_field = model_class._meta.get_field(field_name)
        if value is None and not model_field.null:
            if model_field.empty_strings_allowed:
                normalized[field_name] = ""
            elif model_field.has_default():
                normalized[field_name] = model_field.get_default()
    return normalized


def _mob_template_update_values(template: MobTemplate, field_names: list[str]) -> dict[str, object]:
    values = _template_update_values(template, field_names)
    values["health"] = template.health_max
    values["energy"] = template.energy_max
    values["stamina"] = template.stamina_max
    return _normalize_values_for_model(Mob, values)


class LoadTemplateAction:
    def execute(
        self,
        *,
        player_id: int,
        template_type: str,
        template_id: int,
        cmd: str | None = None,
    ) -> ActionResult:
        player = get_player_with_related(player_id)
        if not player.room_id:
            raise ActionError("You are nowhere. Cannot load templates.", code="no_room")

        payload = {
            "world_id": player.world_id,
            "template_type": template_type,
            "template_id": template_id,
            "actor_type": "player",
            "actor_id": player.id,
            "room": player.room_id,
        }
        if cmd:
            payload["cmd"] = cmd

        serializer = LoadTemplateSerializer(data=payload)
        try:
            serializer.is_valid(raise_exception=True)
        except drf_serializers.ValidationError as exc:
            message = _first_error_message(exc.detail) or "Unable to load template."
            raise ActionError(message, code="invalid_load")

        vd = serializer.validated_data
        loaded_key = None
        loaded_name = None
        loaded_type = vd["template_type"]

        # Spawn the template
        if vd["template_type"] == "item":
            item = vd["template"].spawn(vd["actor"], vd["spawn_world"])
            loaded_key = item.key
            loaded_name = item.name or (item.template.name if item.template else "item")
        elif vd["template_type"] == "mob":
            room = vd["room"] if vd["actor_type"] == "room" else vd["actor"].room
            mob = vd["template"].spawn(room, vd["spawn_world"])
            maybe_enqueue_ai_sidecar_mob_spawned(
                mob=mob,
                source="builder.load_command",
                trigger_actor_key=player.key,
            )
            loaded_key = mob.key
            loaded_name = (
                mob.name
                or (mob.definition.name if mob.definition else "")
                or (mob.template.name if mob.template else "mob")
            )
        else:
            raise ActionError("Unknown template type.", code="invalid_type")

        updated_player = get_player_with_related(player.id)
        actor_payload = serialize_actor(updated_player, updated_player.room)

        data = {
            "actor": actor_payload.model_dump(),
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
                    recipients=[updated_player.key],
                    data=data,
                    text=text,
                )
            ]
        )


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
            if not normalized_target or normalized_target == "all":
                items = list(room.inventory.filter(is_pending_deletion=False))
                mobs = list(room.mobs.all())

                for item in items:
                    item.delete()
                for mob in mobs:
                    _purge_mob_cleanly(
                        mob=mob,
                        source="builder.purge_command",
                        trigger_actor_key=player.key,
                    )

                out_text = "The world feels a little cleaner."

            elif normalized_target == "items":
                items = list(room.inventory.filter(is_pending_deletion=False))
                for item in items:
                    item.delete()
                out_text = "You purge all items in the room."

            elif normalized_target == "mobs":
                mobs = list(room.mobs.all())
                for mob in mobs:
                    _purge_mob_cleanly(
                        mob=mob,
                        source="builder.purge_command",
                        trigger_actor_key=player.key,
                    )
                out_text = "You purge all mobs in the room."

            else:
                targets = _collect_purge_targets(player, normalized_target)
                if not targets:
                    raise ActionError("Incorrect purge target.", code="invalid_target")

                lines = []
                for entity in targets:
                    lines.append(f"You purge {_entity_name(entity)} from this world.")
                    if isinstance(entity, Mob):
                        _purge_mob_cleanly(
                            mob=entity,
                            source="builder.purge_command",
                            trigger_actor_key=player.key,
                        )
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

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./purge.success",
                    recipients=[updated_player.key],
                    data=data,
                    text=out_text,
                )
            ]
        )


class EchoAction:
    def execute(
        self,
        *,
        actor: Player | Mob,
        scope: str,
        message: str,
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

        recipients = _collect_scope_player_keys(actor, normalized_scope)
        data = {
            "actor": _actor_summary(actor),
            "scope": normalized_scope,
            "message": normalized_message,
        }

        events: list[GameEvent] = []
        if isinstance(actor, Player):
            events.append(
                GameEvent(
                    type="cmd./echo.success",
                    recipients=[actor.key],
                    data=data,
                    text=normalized_message,
                )
            )
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


class StateAction:
    def execute(
        self,
        *,
        actor: Player | Mob,
        operation: str,
        scope: str,
        key: str | None = None,
        value: object | None = None,
        amount: int | float | None = None,
    ) -> ActionResult:
        normalized_operation = str(operation or "").strip().lower()
        normalized_scope = normalize_state_scope(scope)
        owner = resolve_scope_owner(normalized_scope, actor=actor)
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
        text = None

        if normalized_operation == "show":
            snapshot = get_state_snapshot(normalized_scope, owner)
            data["state"] = snapshot
            text = json.dumps(snapshot, sort_keys=True)
        elif normalized_operation == "get":
            if not key:
                raise ActionError("Usage: /state get <scope> <key>", code="invalid_args")
            current_value = get_state_value(normalized_scope, owner, key)
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
            )
            data["key"] = key
            data["value"] = new_value
            rendered_value = json.dumps(new_value) if isinstance(new_value, (dict, list, bool, type(None))) else str(new_value)
            text = f"Set {normalized_scope}.{key} = {rendered_value}"
        elif normalized_operation == "clear":
            if not key:
                raise ActionError("Usage: /state clear <scope> <key>", code="invalid_args")
            cleared = clear_state_value(normalized_scope, owner, key)
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

        player_targets = _collect_room_player_targets(room, normalized_target)
        mob_targets = _collect_room_mob_targets(room, normalized_target)
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
        actor: Player,
        target_selector: str | None,
    ) -> Player:
        room = actor.room
        if not room:
            raise ActionError("You are nowhere. Cannot set classes.", code="no_room")

        normalized_target = str(target_selector or "").strip().lower()
        if not normalized_target or normalized_target in {"self", "me"}:
            return actor

        targets = _collect_room_player_targets(room, normalized_target)
        if not targets:
            raise ActionError("Player not found in this room.", code="invalid_target")
        if len(targets) > 1:
            raise ActionError("Player target is ambiguous.", code="ambiguous_target")
        return targets[0]

    def execute(
        self,
        *,
        actor: Player,
        class_selector: str,
        target_selector: str | None = None,
    ) -> ActionResult:
        target = self._resolve_target(
            actor=actor,
            target_selector=target_selector,
        )
        new_class = _resolve_player_class_key(target.world, class_selector)
        previous_class = str(target.archetype or "")

        with transaction.atomic():
            target = Player.objects.select_for_update().get(pk=target.pk)
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
            target.save(update_fields=["archetype", "health", "energy", "stamina"])

        updated_actor = get_player_with_related(actor.id)
        updated_target = get_player_with_related(target.id)
        actor_payload = serialize_actor(updated_actor, updated_actor.room)
        room_payload = _get_single_room_payload(updated_actor)
        target_payload = serialize_actor(updated_target, updated_target.room)
        class_labels = get_world_stat_system(updated_target.world)["labels"]["classes"]
        class_label = class_labels.get(new_class, new_class)
        target_name = getattr(updated_target, "name", None) or "target"
        text = f"Set {target_name}'s class to {class_label}."

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./setclass.success",
                    recipients=[updated_actor.key],
                    data={
                        "actor": actor_payload.model_dump(),
                        "room": room_payload.model_dump(),
                        "target": target_payload.model_dump(),
                        "target_type": "player",
                        "previous_class": previous_class,
                        "new_class": new_class,
                        "class_label": class_label,
                    },
                    text=text,
                )
            ]
        )


class CmdAction:
    @staticmethod
    def _dispatch_actor_ref(actor: Player | Mob) -> tuple[str, int]:
        return _actor_kind(actor), actor.id

    def _dispatch_segment(
        self,
        *,
        dispatch_actor: Player | Mob,
        segment: str,
        issuer_scope: str | None = None,
        skip_triggers: bool = False,
        script_source: bool = False,
    ) -> str | None:
        rendered_segment = _render_command_segment(segment, actor=dispatch_actor)
        command_token = _first_token(rendered_segment)
        if not command_token:
            return None

        resolved = resolve_text_handler(command_token, include_builder=True)
        if not resolved:
            return f"Unknown command: {command_token}"

        resolved_command, handler = resolved
        dispatch_actor_type, dispatch_actor_id = self._dispatch_actor_ref(dispatch_actor)
        if dispatch_actor_type not in getattr(handler, "supported_actor_types", ("player",)):
            return f"{dispatch_actor_type.capitalize()}s cannot execute {resolved_command}."

        dispatched_messages: list[dict] = []
        payload: dict[str, object] = {"text": rendered_segment}
        if issuer_scope:
            payload["issuer_scope"] = issuer_scope
        if skip_triggers:
            payload["skip_triggers"] = True

        try:
            dispatch_command(
                command_type="text",
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
        actor: Player | Mob,
        target_selector: str,
        cmd: str,
        skip_triggers: bool = False,
        script_source: bool = False,
    ) -> ActionResult:
        room = getattr(actor, "room", None)
        if not room:
            raise ActionError("You are nowhere. Cannot execute commands.", code="no_room")

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

        dispatch_actor: Player | Mob = actor
        issuer_scope: str | None = None
        target_data: dict[str, object]

        if normalized_target in CMD_SCOPE_TARGETS:
            issuer_scope = normalized_target
            target_data = {"type": "scope", "scope": normalized_target}
        else:
            if normalized_target.startswith("mob:"):
                normalized_target = normalized_target.split(":", 1)[1].strip().lower()
            targets = _collect_room_mob_targets(room, normalized_target)
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


class JumpAction:
    def execute(
        self,
        *,
        player_id: int,
        room_selector: str,
    ) -> ActionResult:
        normalized_selector = (room_selector or "").strip()
        if not normalized_selector:
            raise ActionError("Usage: /jump <room_id>", code="invalid_args")

        room_selector_id = _parse_room_selector(normalized_selector)

        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot jump.", code="no_room")

            origin_room_id = player.room_id
            try:
                origin_room = Room.objects.select_related("world").get(pk=origin_room_id)
            except Room.DoesNotExist:
                raise ActionError("Current room is invalid.", code="invalid_room")

            room_world = origin_room.world
            target_room = _resolve_room_in_world(room_world, room_selector_id)
            if not target_room:
                raise ActionError("Invalid room ID.", code="invalid_room")

            player.room_id = target_room.id
            player.last_action_ts = timezone.now()
            player.save(update_fields=["room", "last_action_ts"])
            player.viewed_rooms.add(target_room.id)

            origin_recipients: list[int] = []
            destination_recipients: list[int] = []
            if not player.is_invisible:
                origin_recipients = list(
                    Player.objects.filter(room_id=origin_room_id, in_game=True)
                    .exclude(pk=player.id)
                    .values_list("id", flat=True)
                )
                destination_recipients = list(
                    Player.objects.filter(room_id=target_room.id, in_game=True)
                    .exclude(pk=player.id)
                    .values_list("id", flat=True)
                )

        updated_player = get_player_with_related(player_id)
        room_payload = _get_single_room_payload(updated_player).model_dump()
        actor_payload = serialize_actor(updated_player, updated_player.room).model_dump()

        events: list[GameEvent] = []
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


class ResyncItemTemplatesAction:
    def execute(
        self,
        *,
        player_id: int,
        template_id: int | None = None,
    ) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        if not player.room_id:
            raise ActionError("You are nowhere. Cannot resync templates.", code="no_room")

        world = player.world
        context = world.context.instance_of or world.context
        template_field_names = _item_template_field_names()
        base_qs = Item.objects.filter(
            world=world,
            template__isnull=False,
            is_pending_deletion=False,
        )

        template = None
        updated = 0
        if template_id is not None:
            template = ItemTemplate.objects.filter(pk=template_id, world=context).first()
            if not template:
                raise ActionError("Template does not belong to this world.", code="invalid_template")
            updated = base_qs.filter(template=template).update(
                **_template_update_values(template, template_field_names)
            )
        else:
            template_ids = list(base_qs.values_list("template_id", flat=True).distinct())
            templates = ItemTemplate.objects.filter(pk__in=template_ids)
            for item_template in templates.iterator(chunk_size=200):
                updated += base_qs.filter(template_id=item_template.id).update(
                    **_template_update_values(item_template, template_field_names)
                )

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = _get_single_room_payload(updated_player)

        data = {
            "actor": actor_payload.model_dump(),
            "room": room_payload.model_dump(),
            "target_type": "item",
            "updated": updated,
            "template_id": template_id if template_id is not None else "all",
        }
        if template:
            data["template"] = {"id": template.id, "name": template.name}

        if template:
            if updated:
                text = (
                    f"Resynced {updated} item{'s' if updated != 1 else ''} "
                    f"from template {template.name}."
                )
            else:
                text = f"No spawned items for template {template.name} were found."
        else:
            text = f"Resynced {updated} templated item{'s' if updated != 1 else ''}."

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./resync.success",
                    recipients=[updated_player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class ResyncMobTemplatesAction:
    def execute(
        self,
        *,
        player_id: int,
        template_id: int | None = None,
    ) -> ActionResult:
        player = Player.objects.get(pk=player_id)
        if not player.room_id:
            raise ActionError("You are nowhere. Cannot resync templates.", code="no_room")

        world = player.world
        context = world.context.instance_of or world.context
        template_field_names = _mob_template_field_names()
        base_qs = Mob.objects.filter(
            world=world,
            template__isnull=False,
            is_pending_deletion=False,
        )

        template = None
        updated = 0
        if template_id is not None:
            template = MobTemplate.objects.filter(pk=template_id, world=context).first()
            if not template:
                raise ActionError("Template does not belong to this world.", code="invalid_template")
            updated = base_qs.filter(template=template).update(
                **_mob_template_update_values(template, template_field_names)
            )
        else:
            template_ids = list(base_qs.values_list("template_id", flat=True).distinct())
            templates = MobTemplate.objects.filter(pk__in=template_ids)
            for mob_template in templates.iterator(chunk_size=200):
                updated += base_qs.filter(template_id=mob_template.id).update(
                    **_mob_template_update_values(mob_template, template_field_names)
                )

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = _get_single_room_payload(updated_player)

        data = {
            "actor": actor_payload.model_dump(),
            "room": room_payload.model_dump(),
            "target_type": "mob",
            "updated": updated,
            "template_id": template_id if template_id is not None else "all",
        }
        if template:
            data["template"] = {"id": template.id, "name": template.name}

        if template:
            if updated:
                text = (
                    f"Resynced {updated} mob{'s' if updated != 1 else ''} "
                    f"from template {template.name}."
                )
            else:
                text = f"No spawned mobs for template {template.name} were found."
        else:
            text = f"Resynced {updated} templated mob{'s' if updated != 1 else ''}."

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd./resync.success",
                    recipients=[updated_player.key],
                    data=data,
                    text=text,
                )
            ]
        )
