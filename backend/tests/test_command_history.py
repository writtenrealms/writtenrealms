import uuid

from spawns.handlers.registry import dispatch_command
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command


class TestCommandHistory(WorldTestCase):
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

    def test_history_lists_recent_commands_and_excludes_history(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "north")
            dispatch_text_command(self.player.id, "say hi")
            dispatch_text_command(self.player.id, "history")

        message = self._message_by_type(messages, "cmd.history.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "1. say hi\n2. north")
        self.assertEqual(
            message["data"]["commands"],
            [
                {"index": 1, "command": "say hi"},
                {"index": 2, "command": "north"},
            ],
        )

        self.player.refresh_from_db()
        self.assertEqual(self.player.command_history, ["say hi", "north"])

    def test_partial_history_command_is_not_recorded(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")
            dispatch_text_command(self.player.id, "hist")

        message = self._message_by_type(messages, "cmd.history.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "1. look")

        self.player.refresh_from_db()
        self.assertEqual(self.player.command_history, ["look"])

    def test_history_keeps_last_twenty_commands(self):
        with capture_game_messages() as messages:
            for index in range(25):
                dispatch_text_command(self.player.id, f"say message {index}")
            dispatch_text_command(self.player.id, "history")

        message = self._message_by_type(messages, "cmd.history.success")
        self.assertIsNotNone(message)
        commands = message["data"]["commands"]
        self.assertEqual(len(commands), 20)
        self.assertEqual(commands[0], {"index": 1, "command": "say message 24"})
        self.assertEqual(commands[-1], {"index": 20, "command": "say message 5"})

        self.player.refresh_from_db()
        self.assertEqual(len(self.player.command_history), 20)
        self.assertEqual(self.player.command_history[0], "say message 24")
        self.assertEqual(self.player.command_history[-1], "say message 5")

    def test_history_replay_shows_resolution_and_executes_command(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "say hi")
            messages.clear()

            dispatch_text_command(self.player.id, "!1")

        replay_message = self._message_by_type(messages, "cmd.history.replay")
        self.assertIsNotNone(replay_message)
        self.assertEqual(replay_message["text"], "!1 -> say hi")
        self.assertTrue(replay_message["echo"])
        self.assertEqual(
            replay_message["data"],
            {"index": 1, "command": "say hi", "reference": "!1"},
        )

        say_messages = self._messages_by_type(messages, "cmd.say.success")
        self.assertEqual(len(say_messages), 1)
        self.assertEqual(say_messages[0]["text"], "You say 'hi'")

        self.player.refresh_from_db()
        self.assertEqual(self.player.command_history, ["say hi"])

    def test_history_replay_preserves_command_receipt_identity(self):
        dispatch_text_command(self.player.id, "say hi")
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "!1",
                    "_request_id": str(request_id),
                    "_request_segment": "r.3",
                },
            )

        replay_message = self._message_by_type(
            messages,
            "cmd.history.replay",
        )
        self.assertEqual(
            replay_message["data"]["request_id"],
            str(request_id),
        )
        self.assertEqual(
            replay_message["data"]["request_segment"],
            "r.3",
        )

    def test_history_replay_rejects_missing_index(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "!1")

        message = self._message_by_type(messages, "cmd.history.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "no_history_entry")
        self.assertEqual(message["data"]["index"], 1)
