import uuid
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase

from spawns.handlers import HandlerNotFoundError, PlayerNotFoundError
from spawns.tasks import (
    handle_game_command,
    resolve_combat_encounter,
    run_due_combat_encounters,
)


class _DriverDatabaseError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class TestResolveCombatEncounterFailures(SimpleTestCase):
    def _operational_error(self, sqlstate):
        error = OperationalError("database conflict")
        error.__cause__ = _DriverDatabaseError(sqlstate)
        return error

    def test_deadlock_is_retried_with_bounded_delay(self):
        error = self._operational_error("40P01")
        with patch(
            "spawns.actions.combat.resolve_combat_encounter_step",
            side_effect=error,
        ), patch.object(
            resolve_combat_encounter,
            "retry",
            side_effect=RuntimeError("retry requested"),
        ) as retry:
            with self.assertLogs("spawns.tasks", level="WARNING"):
                with self.assertRaisesRegex(RuntimeError, "retry requested"):
                    resolve_combat_encounter.run(42)

        retry.assert_called_once()
        self.assertIs(retry.call_args.kwargs["exc"], error)
        self.assertGreater(retry.call_args.kwargs["countdown"], 0)
        self.assertLessEqual(retry.call_args.kwargs["countdown"], 4)

    def test_unrelated_operational_error_is_not_retried(self):
        error = self._operational_error("08006")
        with patch(
            "spawns.actions.combat.resolve_combat_encounter_step",
            side_effect=error,
        ), patch.object(resolve_combat_encounter, "retry") as retry:
            with self.assertRaises(OperationalError):
                resolve_combat_encounter.run(42)

        retry.assert_not_called()

    def test_task_redelivers_after_worker_loss(self):
        self.assertTrue(resolve_combat_encounter.acks_late)
        self.assertTrue(resolve_combat_encounter.reject_on_worker_lost)
        self.assertEqual(resolve_combat_encounter.max_retries, 5)

    def test_overlapping_recovery_poll_is_skipped(self):
        with patch("spawns.tasks.cache.add", return_value=False):
            self.assertEqual(run_due_combat_encounters.run(), {"skipped": True})


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
