from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from core.computations import compute_stats
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.events import GameEvent
from spawns.models import Item, Mob, Player
from spawns.state_payloads import (
    door_state_lookup,
    room_payload_key_for,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_item,
    serialize_room,
)
from worlds.models import Room


MAX_AUTO_RESOLVE_ROUNDS = 100


@dataclass(frozen=True)
class CombatStats:
    player_attack_power: int
    player_health_max: int
    player_mana_max: int
    player_stamina_max: int


def _player_combat_stats(player: Player) -> CombatStats:
    stats = compute_stats(player.level, player.archetype)
    return CombatStats(
        # TODO: Replace this placeholder AP=damage model with the real formulas layer.
        player_attack_power=int(stats.get("attack_power") or 0),
        player_health_max=max(1, int(stats.get("health_max") or 1)),
        player_mana_max=int(stats.get("mana_max") or 0),
        player_stamina_max=int(stats.get("stamina_max") or 0),
    )


def _room_payload(viewer: Player, room: Room) -> dict:
    door_states = door_state_lookup(viewer.world, [room.id])
    return serialize_room(
        room,
        {room.id: room_payload_key_for(room)},
        door_states,
        viewer=viewer,
    ).model_dump()


def _combat_recipients(player: Player, room: Room) -> list[str]:
    return [
        f"player.{pid}"
        for pid in Player.objects.filter(room_id=room.id, in_game=True)
        .exclude(pk=player.id)
        .values_list("id", flat=True)
    ]


def _ensure_corpse(mob: Mob) -> int:
    corpse = mob.inventory.filter(type="corpse").order_by("id").first()
    if corpse:
        return corpse.id

    # TODO: Consider a virtual corpse presentation if we want to stop
    # pre-creating corpse records for every spawned mob.
    return mob.create_corpse().id


def _serialize_corpse(corpse_id: int, *, viewer: Player | None = None) -> dict:
    corpse = Item.objects.select_related("template", "currency").get(pk=corpse_id)
    return serialize_item(corpse, viewer=viewer, include_inventory=True).model_dump()


def _actor_attack_text(target_name: str, damage: int) -> str:
    return f"You hit {target_name} for {damage} damage."


def _actor_hit_text(actor_name: str, damage: int) -> str:
    return f"{actor_name} hits you for {damage} damage."


def _room_attack_text(actor_name: str, target_name: str, damage: int) -> str:
    return f"{actor_name} hits {target_name} for {damage} damage."


def _empty_corpse_payload() -> dict:
    return {"key": "", "name": "", "inventory": []}


class KillAction:
    def _combat_attack_events(
        self,
        *,
        player: Player,
        room: Room,
        actor_payload: dict,
        target_payload: dict,
        damage: int,
        round_id: str,
        actor_text: str,
        room_text: str,
    ) -> list[GameEvent]:
        data = {
            "actor": actor_payload,
            "target": target_payload,
            "attack": "attack",
            "label": "Attack",
            "outcome": "hit",
            "damage_taken": damage,
            "damage_absorbed": 0,
            "is_crit_hit": False,
            "is_heal": False,
            "round_id": round_id,
        }
        events = [
            GameEvent(
                type="notification.combat.attack",
                recipients=[player.key],
                data=data,
                text=actor_text,
            )
        ]
        if player.is_invisible:
            return events

        recipients = _combat_recipients(player, room)
        if recipients:
            events.append(
                GameEvent(
                    type="notification.combat.attack",
                    recipients=recipients,
                    data=data,
                    text=room_text,
                )
            )
        return events

    def execute(self, player_id: int, target_selector: str | None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot kill anything.", code="no_room")

            config = player.world.effective_config
            if config and not config.allow_combat:
                raise ActionError("Combat is disabled here.", code="combat_disabled")
            # TODO: Route non-zero combat_resolution_interval worlds through an
            # encounter scheduler instead of this immediate placeholder loop.

            room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
            target_ref = resolve_room_mob_target(
                room,
                target_selector,
                empty_error="Kill what?",
                not_found_error="You don't see them here.",
            )
            target_mob = Mob.objects.select_for_update().get(pk=target_ref.id)

            stats = _player_combat_stats(player)
            player.health_max = stats.player_health_max
            player.mana_max = stats.player_mana_max
            player.stamina_max = stats.player_stamina_max
            mob_attack_power = int(target_mob.attack_power or 0)
            if stats.player_attack_power <= 0 and (
                not target_mob.fights_back or mob_attack_power <= 0
            ):
                raise ActionError("Neither of you can harm the other.", code="combat_stalled")

            events: list[GameEvent] = []

            for round_no in range(1, MAX_AUTO_RESOLVE_ROUNDS + 1):
                round_id = f"kill:{player.id}:{target_mob.id}:{round_no}"

                if stats.player_attack_power > 0:
                    target_mob.health = max(
                        0,
                        int(target_mob.health or 0) - stats.player_attack_power,
                    )
                    target_mob.save(update_fields=["health"])

                    player_char = serialize_char_from_player(player).model_dump()
                    target_char = serialize_char_from_mob(target_mob).model_dump()
                    target_name = target_char.get("name") or "them"
                    events.extend(
                        self._combat_attack_events(
                            player=player,
                            room=room,
                            actor_payload=player_char,
                            target_payload=target_char,
                            damage=stats.player_attack_power,
                            round_id=round_id,
                            actor_text=_actor_attack_text(target_name, stats.player_attack_power),
                            room_text=_room_attack_text(player.name, target_name, stats.player_attack_power),
                        )
                    )

                    if target_mob.health <= 0:
                        corpse_id = _ensure_corpse(target_mob)
                        deceased_payload = target_char
                        exp_reward = int(target_mob.exp_worth or 0)
                        target_mob.delete()

                        if exp_reward:
                            player.experience = int(player.experience or 0) + exp_reward
                            # TODO: Trigger level-up/progression checks once WR2 leveling exists.
                            player.save(update_fields=["experience"])

                        actor_payload = serialize_actor(player, room).model_dump()
                        corpse_payload = _serialize_corpse(corpse_id, viewer=player)
                        room_payload = _room_payload(player, room)
                        death_data = {
                            "actor": actor_payload,
                            "deceased": deceased_payload,
                            "killer": serialize_char_from_player(player).model_dump(),
                            "corpse": corpse_payload,
                            "room": room_payload,
                            "experience_gained": exp_reward,
                        }
                        events.append(
                            GameEvent(
                                type="notification.death",
                                recipients=[player.key],
                                data=death_data,
                                text=f"You kill {deceased_payload.get('name') or 'them'}.",
                            )
                        )

                        if not player.is_invisible:
                            recipients = _combat_recipients(player, room)
                            if recipients:
                                events.append(
                                    GameEvent(
                                        type="notification.death",
                                        recipients=recipients,
                                        data={
                                            "deceased": deceased_payload,
                                            "killer": serialize_char_from_player(player).model_dump(),
                                            "corpse": corpse_payload,
                                        },
                                        text=(
                                            f"{player.name} kills "
                                            f"{deceased_payload.get('name') or 'them'}."
                                        ),
                                    )
                                )

                        events.append(
                            GameEvent(
                                type="quest.mob.killed",
                                recipients=[],
                                data={
                                    "actor": actor_payload,
                                    "target": deceased_payload,
                                    "room": room_payload,
                                    "experience_gained": exp_reward,
                                },
                            )
                        )
                        return ActionResult(events=events)

                if not target_mob.fights_back or mob_attack_power <= 0:
                    if stats.player_attack_power <= 0:
                        raise ActionError("Neither of you can harm the other.", code="combat_stalled")
                    continue

                player.health = max(0, int(player.health or 0) - mob_attack_power)
                player.save(update_fields=["health"])

                mob_char = serialize_char_from_mob(target_mob).model_dump()
                player_char = serialize_char_from_player(player).model_dump()
                mob_name = mob_char.get("name") or "Something"
                events.extend(
                    self._combat_attack_events(
                        player=player,
                        room=room,
                        actor_payload=mob_char,
                        target_payload=player_char,
                        damage=mob_attack_power,
                        round_id=round_id,
                        actor_text=_actor_hit_text(mob_name, mob_attack_power),
                        room_text=_room_attack_text(mob_name, player.name, mob_attack_power),
                    )
                )

                if player.health <= 0:
                    death_room = config.death_room if config and config.death_room_id else player.get_starting_room()
                    player.health = stats.player_health_max
                    player.mana = stats.player_mana_max
                    player.stamina = stats.player_stamina_max
                    player.room = death_room
                    # TODO: Apply WR2 death penalties here once the penalty system exists.
                    player.save(update_fields=["health", "mana", "stamina", "room"])

                    affect_data = {
                        "actor": serialize_actor(player, death_room).model_dump(),
                        "room": _room_payload(player, death_room),
                    }
                    events.append(
                        GameEvent(
                            type="affect.death",
                            recipients=[player.key],
                            data=affect_data,
                            text="You have been slain.",
                        )
                    )

                    if not player.is_invisible:
                        recipients = _combat_recipients(player, room)
                        if recipients:
                            events.append(
                                GameEvent(
                                    type="notification.death",
                                    recipients=recipients,
                                    data={
                                        "deceased": player_char,
                                        "killer": mob_char,
                                        "corpse": _empty_corpse_payload(),
                                    },
                                    text=f"{mob_name} kills {player.name}.",
                                )
                            )

                    return ActionResult(events=events)

            raise ActionError("Combat stalled before anyone died.", code="combat_stalled")
