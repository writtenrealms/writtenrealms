import json

from django.contrib.contenttypes.models import ContentType

from builders.models import MobDefinition, Trigger
from config import constants as adv_consts
from config import game_settings as adv_config
from spawns.handlers import dispatch_command
from spawns.models import Mob
from spawns.triggers import evaluate_movement_policies
from tests.base import WorldTestCase
from worlds.models import Room
from tests.utils import capture_game_messages, dispatch_text_command


ROOM_COSTS = {
    adv_consts.ROOM_TYPE_ROAD: 1,
    adv_consts.ROOM_TYPE_CITY: 1,
    adv_consts.ROOM_TYPE_INDOOR: 1,
    adv_consts.ROOM_TYPE_FIELD: 2,
    adv_consts.ROOM_TYPE_TRAIL: 2,
    adv_consts.ROOM_TYPE_MOUNTAIN: 4,
    adv_consts.ROOM_TYPE_FOREST: 3,
    adv_consts.ROOM_TYPE_DESERT: 3,
    adv_consts.ROOM_TYPE_WATER: 3,
    adv_consts.ROOM_TYPE_SHALLOW: 3,
}


def movement_cost(room_type: str) -> int:
    return ROOM_COSTS.get(room_type, adv_config.MOVEMENT_COST)


class TestMovementCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.stamina = 10
        self.player.save(update_fields=["stamina"])

    def _message_types(self, messages):
        return [msg["message"].get("type") for msg in messages]

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_move_success_updates_room_and_stamina(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        expected_cost = movement_cost(dest_room.type)
        location_sequence_before = self.player.location_sequence

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertEqual(
            self.player.location_sequence,
            location_sequence_before + 1,
        )
        self.assertEqual(self.player.stamina, 10 - expected_cost)

        self.assertTrue("cmd.move.success" in self._message_types(messages))
        move_message = self._message_by_type(messages, "cmd.move.success")
        self.assertTrue(move_message.get("text"))

    def test_move_no_exit_returns_error(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, 10)

        self.assertTrue(
            "cmd.move.error" in self._message_types(messages)
        )

    def test_text_command_move_updates_room_and_stamina(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        expected_cost = movement_cost(dest_room.type)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "e")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertEqual(self.player.stamina, 10 - expected_cost)
        self.assertTrue("cmd.move.success" in self._message_types(messages))
        move_message = self._message_by_type(messages, "cmd.move.success")
        self.assertTrue(move_message.get("text"))

    def test_text_command_s_moves_south_not_stats(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        expected_cost = movement_cost(dest_room.type)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "s")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertEqual(self.player.stamina, 10 - expected_cost)
        self.assertTrue("cmd.move.success" in self._message_types(messages))
        self.assertFalse("cmd.stats.success" in self._message_types(messages))

    def test_semicolon_text_movement_runs_in_order(self):
        south_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        east_room = south_room.create_at(adv_consts.DIRECTION_EAST)
        expected_cost = movement_cost(south_room.type) + movement_cost(east_room.type)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "s;e")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, east_room.id)
        self.assertEqual(self.player.stamina, 10 - expected_cost)
        self.assertEqual(
            [
                message["message"]["data"]["direction"]
                for message in messages
                if message["message"].get("type") == "cmd.move.success"
            ],
            ["south", "east"],
        )
        self.assertFalse("cmd.move.error" in self._message_types(messages))

    def test_move_rejects_when_destination_cost_exceeds_stamina(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        dest_room.type = adv_consts.ROOM_TYPE_MOUNTAIN
        dest_room.save(update_fields=["type"])

        cost = movement_cost(dest_room.type)
        self.player.stamina = cost - 1
        self.player.save(update_fields=["stamina"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, cost - 1)

        error_message = self._message_by_type(messages, "cmd.move.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "exhausted")

    def test_move_success_room_chars_excludes_players_not_in_game(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)

        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        online_player = self.create_player(
            "Online Player",
            user=self.create_user("online@example.com"),
            room=dest_room,
        )
        online_player.in_game = True
        online_player.save(update_fields=["in_game"])

        offline_player = self.create_player(
            "Offline Player",
            user=self.create_user("offline@example.com"),
            room=dest_room,
        )
        offline_player.in_game = False
        offline_player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        move_message = self._message_by_type(messages, "cmd.move.success")
        self.assertIsNotNone(move_message)

        char_names = {char["name"] for char in move_message["data"]["room"]["chars"]}
        self.assertIn("Online Player", char_names)
        self.assertNotIn("Offline Player", char_names)

    def test_before_move_enter_policy_blocks_when_condition_fails(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        room_ct = ContentType.objects.get_for_model(Room)
        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=dest_room.id,
            event=adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
            conditions=json.dumps({"eq": ["actor.archetype", "warlord"]}),
            failure_message="Only warlords may enter.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, 10)

        error_message = self._message_by_type(messages, "cmd.move.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["text"], "Only warlords may enter.")
        self.assertEqual(error_message["data"]["code"], "policy_blocked")
        self.assertEqual(error_message["data"]["trigger_id"], trigger.id)

    def test_movement_policy_negative_cache_avoids_database_queries(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        policy_args = {
            "actor": self.player,
            "event": adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
            "direction": "east",
            "origin_room_id": self.room.id,
            "destination_room_id": dest_room.id,
            "world_id": dest_room.world_id,
        }
        self.assertTrue(evaluate_movement_policies(**policy_args).allowed)

        with self.assertNumQueries(0):
            result = evaluate_movement_policies(**policy_args)

        self.assertTrue(result.allowed)

    def test_before_move_enter_policy_allows_when_condition_passes(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=dest_room.id,
            event=adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
            conditions=json.dumps({"eq": ["actor.archetype", "warlord"]}),
            failure_message="Only warlords may enter.",
            display_action_in_room=False,
            gate_delay=0,
        )
        self.player.archetype = "warlord"
        self.player.save(update_fields=["archetype"])
        expected_cost = movement_cost(dest_room.type)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertEqual(self.player.stamina, 10 - expected_cost)
        self.assertTrue("cmd.move.success" in self._message_types(messages))

    def test_before_move_exit_policy_match_limits_direction(self):
        self.room.create_at(adv_consts.DIRECTION_NORTH)
        east_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
            match="north",
            conditions=json.dumps({"always": False}),
            failure_message="The northern guard bars your path.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as allowed_messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, east_room.id)
        self.assertTrue("cmd.move.success" in self._message_types(allowed_messages))

        self.player.room = self.room
        self.player.stamina = 10
        self.player.save(update_fields=["room", "stamina"])

        with capture_game_messages() as blocked_messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "north"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, 10)
        error_message = self._message_by_type(blocked_messages, "cmd.move.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["text"], "The northern guard bars your path.")

    def test_before_move_exit_policy_can_require_mob_definition_absence(self):
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name=definition.name,
        )
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
            match="east",
            conditions=json.dumps({
                "not": {
                    "mob_present": "mobdefinition.east-gate-guard",
                },
            }),
            failure_message="The guard bars the eastern way.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as blocked_messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(
            self._message_by_type(blocked_messages, "cmd.move.error")["text"],
            "The guard bars the eastern way.",
        )

        guard.is_pending_deletion = True
        guard.save(update_fields=["is_pending_deletion"])
        with capture_game_messages() as allowed_messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertIn("cmd.move.success", self._message_types(allowed_messages))

    def test_after_move_enter_room_event_trigger_runs_script(self):
        self.player.in_game = True
        self.player.stamina = 10
        self.player.save(update_fields=["in_game", "stamina"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_ct,
            target_id=dest_room.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
            script="/cmd room -- /echo -- Spears snap out from the walls.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": "east"},
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message, [msg["message"] for msg in messages])
        self.assertIn("Spears snap out", echo_message["text"])

    def test_enter_room_event_trigger_runs_after_normal_move(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_EXIT,
            script="/cmd room -- /echo -- The departure chime rings.",
            display_action_in_room=False,
            gate_delay=0,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=dest_room.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The arrival chime rings.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        echoes = [
            entry["message"]
            for entry in messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "arrival chime" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(echoes), 1)
        departure_echoes = [
            entry["message"]
            for entry in messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "departure chime" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(departure_echoes), 1)
