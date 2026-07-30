import uuid
from unittest.mock import patch

from django.test import SimpleTestCase

from spawns.handlers import HandlerNotFoundError, PlayerNotFoundError
from spawns.tasks import handle_game_command


class TestHandleGameCommandFailures(SimpleTestCase):
    def _assert_failed_receipt(
        self,
        publish_mock,
        *,
        request_id: str,
        code: str,
    ) -> dict:
        publish_mock.assert_called_once()
        player_key, message = publish_mock.call_args.args
        self.assertEqual(player_key, "player.42")
        self.assertEqual(
            publish_mock.call_args.kwargs["connection_id"],
            "connection.current",
        )
        self.assertEqual(message["type"], "cmd.text.error")
        self.assertEqual(message["data"]["request_id"], request_id)
        self.assertEqual(message["data"]["request_segment"], "r.4")
        self.assertEqual(message["data"]["receipt_status"], "failed")
        self.assertEqual(message["data"]["code"], code)
        return message

    def test_missing_player_is_a_correlated_processing_failure(self):
        request_id = str(uuid.uuid4())
        with patch(
            "spawns.tasks.dispatch_command",
            side_effect=PlayerNotFoundError(42),
        ), patch("spawns.tasks.publish_to_player") as publish_mock:
            handle_game_command.run(
                "text",
                player_id=42,
                payload={
                    "text": "look",
                    "_request_id": request_id,
                    "_request_segment": "r.4",
                },
                connection_id="connection.current",
            )

        self._assert_failed_receipt(
            publish_mock,
            request_id=request_id,
            code="player_not_found",
        )

    def test_missing_handler_is_a_correlated_processing_failure(self):
        request_id = str(uuid.uuid4())
        with patch(
            "spawns.tasks.dispatch_command",
            side_effect=HandlerNotFoundError("text"),
        ), patch("spawns.tasks.publish_to_player") as publish_mock:
            handle_game_command.run(
                "text",
                player_id=42,
                payload={
                    "text": "look",
                    "_request_id": request_id,
                    "_request_segment": "r.4",
                },
                connection_id="connection.current",
            )

        self._assert_failed_receipt(
            publish_mock,
            request_id=request_id,
            code="handler_not_found",
        )

    def test_unhandled_exception_is_sanitized_correlated_and_reraised(self):
        request_id = str(uuid.uuid4())
        private_detail = "database password appeared in an exception"
        with patch(
            "spawns.tasks.dispatch_command",
            side_effect=RuntimeError(private_detail),
        ), patch("spawns.tasks.publish_to_player") as publish_mock:
            with self.assertLogs("spawns.tasks", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, private_detail):
                    handle_game_command.run(
                        "text",
                        player_id=42,
                        payload={
                            "text": "look",
                            "_request_id": request_id,
                            "_request_segment": "r.4",
                        },
                        connection_id="connection.current",
                    )

        message = self._assert_failed_receipt(
            publish_mock,
            request_id=request_id,
            code="command_processing_failed",
        )
        self.assertEqual(
            message["text"],
            "The command could not be processed.",
        )
        self.assertNotIn(private_detail, str(message))
