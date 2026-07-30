import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi import WebSocketDisconnect

from fastapi_app import game_ws


class FakeGameWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.accepted = False
        self.sent_json = []

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self.messages:
            raise WebSocketDisconnect()

        message = self.messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_json(self, message):
        self.sent_json.append(message)


class TestGameConnectionManager(unittest.TestCase):
    def test_disconnect_does_not_clear_newer_connection_for_same_player(self):
        async def run_test():
            manager = game_ws.GameConnectionManager()
            old_socket = MagicMock()
            new_socket = MagicMock()

            await manager.authenticate(old_socket, "player.42")
            new_connection_id = await manager.authenticate(new_socket, "player.42")
            await manager.disconnect(old_socket)

            self.assertIs(manager.active_connections["player.42"], new_socket)
            self.assertEqual(manager.player_connections["player.42"], new_connection_id)
            self.assertNotIn(old_socket, manager.connection_players)
            self.assertNotIn(old_socket, manager.connection_ids)

        asyncio.run(run_test())


class TestGameWebSocketDisconnect(unittest.TestCase):
    def _run_socket(self, websocket, celery_app):
        manager = game_ws.GameConnectionManager()
        manager.start_pubsub_listener = AsyncMock()

        with patch.object(game_ws, "game_manager", manager), patch.object(
            game_ws, "_verify_token", return_value=1
        ), patch.object(game_ws, "get_celery_app", return_value=celery_app):
            asyncio.run(game_ws.handle_game_websocket(websocket))

    def test_system_disconnect_queues_current_world_exit(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        self.assertTrue(websocket.accepted)
        self.assertEqual(
            [message["type"] for message in websocket.sent_json],
            ["system.connect.success", "system.disconnect.success"],
        )
        celery_app.send_task.assert_any_call(
            "spawns.tasks.handle_game_command",
            kwargs={
                "command_type": "state.sync",
                "player_id": 42,
                "player_key": "player.42",
                "payload": {},
                "connection_id": ANY,
            },
        )
        celery_app.send_task.assert_any_call(
            "spawns.tasks.exit_current_world",
            kwargs={"player_id": 42},
        )

    def test_socket_close_queues_current_world_exit(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                WebSocketDisconnect(),
            ]
        )

        self._run_socket(websocket, celery_app)

        celery_app.send_task.assert_any_call(
            "spawns.tasks.exit_current_world",
            kwargs={"player_id": 42},
        )

    def test_text_command_forwards_client_request_id(self):
        celery_app = MagicMock()
        request_id = "a87d7492-075a-4e92-8d5a-a89e93c02c1d"
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {
                    "type": "cmd.text",
                    "text": "craft a blue-crested helm",
                    "request_id": request_id,
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        celery_app.send_task.assert_any_call(
            "spawns.tasks.handle_game_command",
            kwargs={
                "command_type": "text",
                "player_id": 42,
                "player_key": "player.42",
                "payload": {
                    "text": "craft a blue-crested helm",
                    "_request_id": request_id,
                },
                "connection_id": ANY,
            },
        )
        receipt = next(
            message
            for message in websocket.sent_json
            if message["type"] == "cmd.request.queued"
        )
        self.assertEqual(
            receipt["data"],
            {
                "request_id": request_id,
                "command_type": "text",
            },
        )

    def test_structured_command_sends_connection_local_queued_receipt(self):
        celery_app = MagicMock()
        request_id = "d520556a-b03c-47fd-aa36-aa00f896560a"
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {
                    "type": "cmd.salvage",
                    "data": {
                        "spoils": True,
                        "request_id": request_id,
                    },
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        celery_app.send_task.assert_any_call(
            "spawns.tasks.handle_game_command",
            kwargs={
                "command_type": "salvage",
                "player_id": 42,
                "player_key": "player.42",
                "payload": {
                    "spoils": True,
                    "_request_id": request_id,
                },
                "connection_id": ANY,
            },
        )
        receipt = next(
            message
            for message in websocket.sent_json
            if message["type"] == "cmd.request.queued"
        )
        self.assertEqual(
            receipt["data"],
            {
                "request_id": request_id,
                "command_type": "salvage",
            },
        )

    def test_enqueue_failure_returns_error_without_queued_receipt(self):
        celery_app = MagicMock()
        request_id = "682d69f3-d434-4b57-af9a-eb00c554187c"

        def send_task(task_name, *, kwargs):
            if (
                task_name == "spawns.tasks.handle_game_command"
                and kwargs["command_type"] == "text"
            ):
                raise RuntimeError("broker unavailable")
            return MagicMock()

        celery_app.send_task.side_effect = send_task
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {
                    "type": "cmd.text",
                    "text": "pay charon",
                    "request_id": request_id,
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        self.assertFalse(
            any(
                message["type"] == "cmd.request.queued"
                for message in websocket.sent_json
            )
        )
        error = next(
            message
            for message in websocket.sent_json
            if message["type"] == "cmd.text.error"
        )
        self.assertEqual(
            error["text"],
            "Unable to confirm command delivery.",
        )
        self.assertEqual(
            error["data"],
            {
                "error": "Unable to confirm command delivery.",
                "code": "command_delivery_unconfirmed",
                "request_id": request_id,
                "command_type": "text",
            },
        )
        self.assertIn(
            "system.disconnect.success",
            [message["type"] for message in websocket.sent_json],
        )

    def test_invalid_text_request_id_is_rejected_instead_of_replaced(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {
                    "type": "cmd.text",
                    "text": "salvage spoils",
                    "request_id": "not-a-uuid",
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        error = next(
            message
            for message in websocket.sent_json
            if message["type"] == "cmd.text.error"
        )
        self.assertEqual(error["data"]["code"], "invalid_request_id")
        queued_commands = [
            call.kwargs["kwargs"]["command_type"]
            for call in celery_app.send_task.call_args_list
            if call.args and call.args[0] == "spawns.tasks.handle_game_command"
        ]
        self.assertEqual(queued_commands, ["state.sync"])

    def test_invalid_structured_request_id_is_rejected_instead_of_replaced(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {
                    "type": "cmd.salvage",
                    "data": {"spoils": True, "request_id": "still-not-a-uuid"},
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        error = next(
            message
            for message in websocket.sent_json
            if message["type"] == "cmd.salvage.error"
        )
        self.assertEqual(error["data"]["code"], "invalid_request_id")
        queued_commands = [
            call.kwargs["kwargs"]["command_type"]
            for call in celery_app.send_task.call_args_list
            if call.args and call.args[0] == "spawns.tasks.handle_game_command"
        ]
        self.assertEqual(queued_commands, ["state.sync"])
