from config import constants as adv_consts
from config import game_settings as adv_config
from spawns.handlers import dispatch_command
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


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
