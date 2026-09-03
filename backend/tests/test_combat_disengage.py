from datetime import timedelta

from django.utils import timezone

from spawns.actions.combat import resolve_combat_encounter_step
from spawns.handlers.registry import resolve_text_handler
from spawns.models import ActiveEffect, CombatEncounter, Mob
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, create_active_effect, dispatch_text_command


class TestCombatDisengage(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.stamina = 33
        self.player.location_sequence = 7
        self.player.follow_move_sequence = 11
        self.player.save(
            update_fields=[
                "in_game",
                "stamina",
                "location_sequence",
                "follow_move_sequence",
            ]
        )

    def _mob(
        self,
        name="a training dummy",
        *,
        fights_back=False,
        target_priority=0,
    ):
        return Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name=name,
            keywords=name.removeprefix("a ").removeprefix("an "),
            health=50,
            health_max=50,
            fights_back=fights_back,
            target_priority=target_priority,
        )

    def _encounter(self, mob, **kwargs):
        return CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=1.5,
            **kwargs,
        )

    def _player_messages(self, messages, message_type):
        return [
            entry["message"]
            for entry in messages
            if entry["player_key"] == self.player.key
            and entry["message"].get("type") == message_type
        ]

    def test_disengage_finishes_passive_encounter_without_moving(self):
        observer = self.create_player("Observer", room=self.room)
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        mob = self._mob()
        encounter = self._encounter(
            mob,
            next_resolution_ts=timezone.now() + timedelta(minutes=1),
            pending_player_ability={"ability": "heavy-blow", "status": "casting"},
            pending_mob_ability={"ability": "brace", "status": "queued"},
            pending_flee={"status": "preparing", "movement_cost": 7},
        )
        encounter_effect = create_active_effect(
            target=self.player,
            source=mob,
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            payload={
                "effect": "root",
                "label": "Pinned",
                "remaining_rounds": 2,
            },
        )
        player_effect = create_active_effect(
            target=self.player,
            source=mob,
            encounter=encounter,
            payload={
                "effect": "dot",
                "label": "Splinters",
                "remaining_rounds": 2,
            },
        )
        mob_effect = create_active_effect(
            target=mob,
            source=self.player,
            encounter=encounter,
            payload={
                "effect": "dot",
                "label": "Smoldering",
                "remaining_rounds": 2,
            },
        )
        old_tick = timezone.now() - timedelta(minutes=1)
        ActiveEffect.objects.filter(pk__in=[player_effect.id, mob_effect.id]).update(
            next_tick_ts=old_tick
        )
        original_room_id = self.player.room_id
        original_location_sequence = self.player.location_sequence
        original_follow_sequence = self.player.follow_move_sequence
        original_health = mob.health

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "disengage")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        player_effect.refresh_from_db()
        mob_effect.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertIsNone(encounter.next_resolution_ts)
        self.assertEqual(encounter.pending_player_ability, {})
        self.assertEqual(encounter.pending_mob_ability, {})
        self.assertEqual(encounter.pending_flee, {})
        self.assertFalse(ActiveEffect.objects.filter(pk=encounter_effect.id).exists())
        self.assertEqual(self.player.room_id, original_room_id)
        self.assertEqual(mob.room_id, original_room_id)
        self.assertEqual(self.player.location_sequence, original_location_sequence)
        self.assertEqual(self.player.follow_move_sequence, original_follow_sequence)
        self.assertEqual(self.player.stamina, 40)
        self.assertEqual(mob.health, original_health)
        self.assertGreater(player_effect.next_tick_ts, old_tick)
        self.assertGreater(mob_effect.next_tick_ts, old_tick)

        success = self._player_messages(messages, "cmd.disengage.success")
        self.assertEqual(len(success), 1)
        self.assertEqual(success[0]["data"]["encounter_id"], encounter.id)
        self.assertFalse(success[0]["data"]["still_in_combat"])
        self.assertIsNone(success[0]["data"]["actor"]["target"])
        self.assertEqual(success[0]["data"]["target"]["state"], "standing")
        observer_events = [
            entry["message"]
            for entry in messages
            if entry["player_key"] == observer.key
            and entry["message"].get("type")
            == "notification.combat.disengage"
        ]
        self.assertEqual(len(observer_events), 1)
        self.assertIsNone(observer_events[0]["data"]["actor"]["target"])
        self.assertIsNone(observer_events[0]["data"]["target"]["target"])
        self.assertEqual(
            self._player_messages(
                messages,
                "player.ability_preparations.update",
            )[0]["data"]["abilities"],
            [],
        )
        self.assertEqual(
            self._player_messages(
                messages,
                "player.combat_effects.update",
            )[0]["data"]["active_effects"],
            [],
        )
        message_types = {entry["message"].get("type") for entry in messages}
        self.assertNotIn("cmd.flee.success", message_types)
        self.assertFalse(
            any(
                message_type.startswith("notification.cmd.flee")
                for message_type in message_types
            )
        )
        self.assertNotIn("lifecycle.player.room.enter", message_types)

        resolved = resolve_combat_encounter_step(encounter.id, auto_advance=True)
        self.assertFalse(resolved.encounter_active)
        self.assertEqual(resolved.events, [])

    def test_disengage_rejects_a_mob_that_fights_back_without_mutation(self):
        mob = self._mob(name="an angry wolf", fights_back=True)
        next_resolution_ts = timezone.now() + timedelta(minutes=1)
        encounter = self._encounter(
            mob,
            next_resolution_ts=next_resolution_ts,
            pending_player_ability={"ability": "heavy-blow", "status": "casting"},
            pending_mob_ability={"ability": "bite", "status": "queued"},
            pending_flee={"status": "preparing", "movement_cost": 7},
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "disengage")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        error = self._player_messages(messages, "cmd.disengage.error")
        self.assertEqual(len(error), 1)
        self.assertEqual(error[0]["data"]["code"], "target_fights_back")
        self.assertEqual(
            error[0]["text"],
            "You cannot disengage while an angry wolf is fighting back.",
        )
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.next_resolution_ts, next_resolution_ts)
        self.assertEqual(
            encounter.pending_player_ability,
            {"ability": "heavy-blow", "status": "casting"},
        )
        self.assertEqual(
            encounter.pending_mob_ability,
            {"ability": "bite", "status": "queued"},
        )
        self.assertEqual(
            encounter.pending_flee,
            {"status": "preparing", "movement_cost": 7},
        )
        self.assertEqual(self.player.stamina, 33)

    def test_disengage_finishes_only_the_primary_passive_encounter(self):
        passive_mob = self._mob(target_priority=10)
        hostile_mob = self._mob(name="an angry wolf", fights_back=True)
        passive_encounter = self._encounter(passive_mob)
        hostile_encounter = self._encounter(
            hostile_mob,
            pending_player_ability={"ability": "counter", "status": "queued"},
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "disengage")

        passive_encounter.refresh_from_db()
        hostile_encounter.refresh_from_db()
        self.assertEqual(passive_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(hostile_encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(
            hostile_encounter.pending_player_ability,
            {"ability": "counter", "status": "queued"},
        )
        success = self._player_messages(messages, "cmd.disengage.success")[0]
        self.assertTrue(success["data"]["still_in_combat"])
        self.assertEqual(success["data"]["actor"]["state"], "combat")
        self.assertEqual(
            success["data"]["actor"]["target"]["key"],
            hostile_mob.key,
        )
        self.assertEqual(success["data"]["next_target"]["key"], hostile_mob.key)
        self.assertEqual(success["data"]["next_target"]["state"], "combat")
        self.assertEqual(
            success["data"]["next_target"]["target"]["key"],
            self.player.key,
        )
        self.assertEqual(
            self._player_messages(
                messages,
                "player.ability_preparations.update",
            )[0]["data"]["abilities"],
            ["counter"],
        )

    def test_disengage_requires_active_pve_combat(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "disengage")

        error = self._player_messages(messages, "cmd.disengage.error")
        self.assertEqual(len(error), 1)
        self.assertEqual(error[0]["data"]["code"], "not_in_combat")

    def test_disengage_help_and_prefix_are_registered(self):
        command, handler = resolve_text_handler("di")
        down_command, down_handler = resolve_text_handler("d")

        self.assertEqual(command, "disengage")
        self.assertEqual(handler.command_type, "disengage")
        self.assertEqual(down_command, "down")
        self.assertEqual(down_handler.command_type, "move")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help disengage")

        help_message = self._player_messages(messages, "cmd.help.success")[0]
        self.assertEqual(help_message["data"]["command"]["name"], "Disengage")
        self.assertIn("does not fight back", help_message["text"])
