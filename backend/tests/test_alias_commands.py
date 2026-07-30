import uuid

from config import constants as adv_consts
from spawns.handlers.registry import dispatch_command
from spawns.models import Alias
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command


class TestAliasCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.stamina = 10
        self.player.save(update_fields=["stamina"])

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type):
        return [
            msg["message"]
            for msg in messages
            if msg["message"].get("type") == message_type
        ]

    def test_alias_definition_keeps_semicolons_in_replacement(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "alias y south ; x")

        alias = Alias.objects.get(player=self.player, match="y")
        self.assertEqual(alias.replacement, "south ; x")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertIsNone(self._message_by_type(messages, "cmd.move.success"))

        message = self._message_by_type(messages, "cmd.alias.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["alias"]["replacement"], "south ; x")

    def test_alias_definition_accepts_equals_separator(self):
        dispatch_text_command(self.player.id, "alias x = east")

        alias = Alias.objects.get(player=self.player, match="x")
        self.assertEqual(alias.replacement, "east")

    def test_single_alias_argument_shows_existing_alias(self):
        dispatch_text_command(self.player.id, "alias x east")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "alias x")

        message = self._message_by_type(messages, "cmd.alias.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "x -> east")
        self.assertEqual(message["data"]["alias"]["replacement"], "east")

    def test_alias_expands_nested_aliases_and_runs_resulting_chain(self):
        south_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        east_room = south_room.create_at(adv_consts.DIRECTION_EAST)

        dispatch_text_command(self.player.id, "alias y south ; x")
        dispatch_text_command(self.player.id, "alias x east")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "y")

        resolve_message = self._message_by_type(messages, "cmd.alias.resolve")
        self.assertIsNotNone(resolve_message)
        self.assertEqual(resolve_message["text"], "y -> south ; east")
        self.assertTrue(resolve_message["echo"])
        self.assertEqual(
            resolve_message["data"],
            {"command": "y", "resolved": "south ; east"},
        )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, east_room.id)
        self.assertEqual(
            [
                message["data"]["direction"]
                for message in self._messages_by_type(messages, "cmd.move.success")
            ],
            ["south", "east"],
        )

    def test_alias_appends_input_args_to_replacement(self):
        dispatch_text_command(self.player.id, "alias k = kill")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "k bear")

        resolve_message = self._message_by_type(messages, "cmd.alias.resolve")
        self.assertIsNotNone(resolve_message)
        self.assertEqual(resolve_message["text"], "k bear -> kill bear")

        error_message = self._message_by_type(messages, "cmd.kill.error")
        self.assertIsNotNone(error_message)

    def test_alias_resolution_preserves_command_receipt_identity(self):
        dispatch_text_command(self.player.id, "alias x = look")
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "x",
                    "_request_id": str(request_id),
                    "_request_segment": "r.2",
                },
            )

        resolve_message = self._message_by_type(
            messages,
            "cmd.alias.resolve",
        )
        self.assertEqual(
            resolve_message["data"]["request_id"],
            str(request_id),
        )
        self.assertEqual(
            resolve_message["data"]["request_segment"],
            "r.2",
        )

    def test_player_alias_can_override_builtin_loot_shortcut(self):
        dispatch_text_command(self.player.id, "alias loot = inventory")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "loot")

        resolve_message = self._message_by_type(messages, "cmd.alias.resolve")
        self.assertIsNotNone(resolve_message)
        self.assertEqual(resolve_message["data"]["resolved"], "inventory")
        self.assertIsNotNone(
            self._message_by_type(messages, "cmd.inventory.success")
        )
        self.assertIsNone(self._message_by_type(messages, "cmd.get.success"))
        self.assertIsNone(self._message_by_type(messages, "cmd.get.error"))

    def test_alias_loop_returns_error_without_dispatching(self):
        dispatch_text_command(self.player.id, "alias x = y")
        dispatch_text_command(self.player.id, "alias y = x")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "x")

        error_message = self._message_by_type(messages, "cmd.alias.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["text"], "Alias loop: x -> y -> x")
        self.assertEqual(error_message["data"]["code"], "alias_loop")
        self.assertEqual(error_message["data"]["chain"], ["x", "y", "x"])
        self.assertIsNone(self._message_by_type(messages, "cmd.text.error"))

    def test_unalias_removes_alias(self):
        dispatch_text_command(self.player.id, "alias x = look")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unalias x")

        self.assertFalse(Alias.objects.filter(player=self.player, match="x").exists())
        message = self._message_by_type(messages, "cmd.unalias.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["aliases"], {})
