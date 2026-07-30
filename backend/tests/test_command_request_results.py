import uuid
from unittest.mock import patch

from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import dispatch_command
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command


class _SilentCommandHandler(CommandHandler):
    command_type = "test.silent"

    def handle(self, ctx: CommandContext) -> None:
        return


class _StartedCommandHandler(CommandHandler):
    command_type = "test.started"

    def handle(self, ctx: CommandContext) -> None:
        ctx.publish({
            "type": "cmd.test.started",
            "data": {"status": "started"},
            "text": "The action begins.",
        })


class TestCommandRequestResults(WorldTestCase):
    def _messages(self, captured, event_type, *, recipient=None):
        return [
            entry
            for entry in captured
            if entry["message"].get("type") == event_type
            and (
                recipient is None
                or entry["player_key"] == recipient
            )
        ]

    def test_actor_success_is_correlated_without_leaking_to_observers(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        observer = self.create_player("Observer", room=self.room)
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        request_id = str(uuid.uuid4())

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "roll 8",
                    "_request_id": request_id,
                    "_request_segment": "r.2",
                },
            )

        actor_result = self._messages(
            messages,
            "cmd.roll.success",
            recipient=self.player.key,
        )[0]["message"]
        self.assertEqual(actor_result["data"]["request_id"], request_id)
        self.assertEqual(actor_result["data"]["request_segment"], "r.2")

        observer_result = self._messages(
            messages,
            "notification.cmd.roll.success",
            recipient=observer.key,
        )[0]["message"]
        self.assertNotIn("request_id", observer_result["data"])
        self.assertNotIn("request_segment", observer_result["data"])
        self.assertFalse(
            self._messages(messages, "cmd.request.completed")
        )

    def test_direct_error_is_correlated_on_the_error_frame(self):
        request_id = str(uuid.uuid4())

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "definitely-not-a-command",
                    "_request_id": request_id,
                },
            )

        error = self._messages(
            messages,
            "cmd.text.error",
            recipient=self.player.key,
        )[0]["message"]
        self.assertEqual(error["data"]["request_id"], request_id)
        self.assertEqual(error["data"]["request_segment"], "r")
        self.assertFalse(
            self._messages(messages, "cmd.request.completed")
        )

    def test_alias_expanded_chain_is_planned_before_terminal_results(self):
        observer = self.create_player("Observer", room=self.room)
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        dispatch_text_command(
            self.player.id,
            "alias x = say one ; say two",
        )
        request_id = str(uuid.uuid4())

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "x",
                    "_request_id": request_id,
                },
            )

        plan_entries = self._messages(
            messages,
            "cmd.request.segments",
            recipient=self.player.key,
        )
        self.assertEqual(len(plan_entries), 1)
        plan = plan_entries[0]["message"]
        self.assertEqual(
            plan["data"],
            {
                "request_id": request_id,
                "request_segments": ["r.0", "r.1"],
            },
        )

        actor_results = self._messages(
            messages,
            "cmd.say.success",
            recipient=self.player.key,
        )
        self.assertEqual(
            [
                entry["message"]["data"]["request_segment"]
                for entry in actor_results
            ],
            ["r.0", "r.1"],
        )
        self.assertTrue(all(
            entry["message"]["data"]["request_id"] == request_id
            for entry in actor_results
        ))
        self.assertLess(
            messages.index(plan_entries[0]),
            messages.index(actor_results[0]),
        )
        self.assertFalse(
            self._messages(messages, "cmd.request.completed")
        )

        observer_results = self._messages(
            messages,
            "notification.cmd.say.success",
            recipient=observer.key,
        )
        self.assertTrue(observer_results)
        self.assertTrue(all(
            "request_id" not in entry["message"]["data"]
            for entry in observer_results
        ))

    def test_history_redispatch_settles_only_from_replayed_result(self):
        dispatch_text_command(self.player.id, "say hello")
        request_id = str(uuid.uuid4())

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "!1",
                    "_request_id": request_id,
                    "_request_segment": "r.4",
                },
            )

        result = self._messages(
            messages,
            "cmd.say.success",
            recipient=self.player.key,
        )[0]["message"]
        self.assertEqual(result["data"]["request_id"], request_id)
        self.assertEqual(result["data"]["request_segment"], "r.4")
        self.assertFalse(
            self._messages(messages, "cmd.request.completed")
        )

    def test_silent_command_gets_private_terminal_control(self):
        request_id = str(uuid.uuid4())
        connection_id = "connection-current"

        with patch(
            "spawns.handlers.registry.get_handler",
            return_value=_SilentCommandHandler(),
        ):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type="test.silent",
                    player_id=self.player.id,
                    payload={"_request_id": request_id},
                    connection_id=connection_id,
                )

        completed = self._messages(
            messages,
            "cmd.request.completed",
            recipient=self.player.key,
        )
        self.assertEqual(len(completed), 1)
        self.assertEqual(
            completed[0]["message"]["data"],
            {
                "request_id": request_id,
                "request_segment": "r",
                "status": "completed",
            },
        )
        self.assertEqual(
            completed[0]["connection_id"],
            connection_id,
        )

    def test_started_output_remains_pending_without_generic_completion(self):
        request_id = str(uuid.uuid4())

        with patch(
            "spawns.handlers.registry.get_handler",
            return_value=_StartedCommandHandler(),
        ):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type="test.started",
                    player_id=self.player.id,
                    payload={"_request_id": request_id},
                )

        started = self._messages(
            messages,
            "cmd.test.started",
            recipient=self.player.key,
        )[0]["message"]
        self.assertNotIn("request_id", started["data"])
        self.assertFalse(
            self._messages(messages, "cmd.request.completed")
        )
