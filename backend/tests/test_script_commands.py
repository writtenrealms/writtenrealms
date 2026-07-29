from unittest.mock import patch

import spawns.handlers  # noqa: F401

from core.trigger_steps import SCRIPT_COMMAND_PROVENANCE_KEY
from spawns.events import publish_events
from spawns.handlers.registry import dispatch_command, get_handler
from spawns.script_commands import (
    MAX_SCRIPT_COMMAND_DEPTH,
    SCRIPT_COMMAND_DEPTH_KEY,
    ScriptCommandError,
    ScriptCommandRunner,
)
from tests.base import WorldTestCase
from tests.utils import capture_game_messages


class TestScriptCommandRunner(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def test_dispatch_context_separates_room_issuer_from_player_subject(self):
        contexts = []
        handler = get_handler("say")

        with patch.object(handler, "handle", side_effect=contexts.append):
            dispatch_command(
                command_type="say",
                payload={"message": "The fare is paid."},
                issuer_type="room",
                issuer_id=self.room.id,
                subject_type="player",
                subject_id=self.player.id,
                capture_only=True,
            )

        self.assertEqual(len(contexts), 1)
        ctx = contexts[0]
        self.assertEqual(ctx.actor, self.player)
        self.assertEqual(ctx.actor_type, "player")
        self.assertEqual(ctx.issuer, self.room)
        self.assertEqual(ctx.issuer_type, "room")
        self.assertEqual(ctx.subject, self.player)
        self.assertEqual(ctx.subject_type, "player")
        self.assertEqual(ctx.world, self.spawn_world)
        self.assertEqual(ctx.room, self.room)

    def test_room_command_has_ambient_issuer_without_embodied_subject(self):
        contexts = []
        handler = get_handler("/echo")

        with patch.object(handler, "handle", side_effect=contexts.append):
            dispatch_command(
                command_type="/echo",
                payload={"runtime_world_id": self.spawn_world.id},
                actor_type="room",
                actor_id=self.room.id,
                issuer_type="room",
                issuer_id=self.room.id,
                capture_only=True,
                script_source=True,
            )

        self.assertEqual(len(contexts), 1)
        ctx = contexts[0]
        self.assertEqual(ctx.actor, self.room)
        self.assertEqual(ctx.issuer, self.room)
        self.assertIsNone(ctx.subject)
        self.assertIsNone(ctx.subject_type)
        self.assertEqual(ctx.world, self.spawn_world)

    def test_runner_captures_player_say_until_caller_publishes_events(self):
        observer = self.create_player(
            "Runner Observer",
            user=self.create_user("runner-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            result = ScriptCommandRunner().execute(
                issuer=self.room,
                subject=self.player,
                command="say The fare is paid.",
                render_actor=self.player,
                runtime_world=self.spawn_world,
            )
            self.assertEqual(messages, [])
            publish_events(result.events)

        self.assertEqual(
            {event.type for event in result.events},
            {"cmd.say.success", "notification.cmd.say.success"},
        )
        self.assertTrue(all(
            event.data[SCRIPT_COMMAND_PROVENANCE_KEY]["issuer"]
            == {
                "type": "room",
                "id": self.room.id,
                "key": self.room.key,
            }
            and event.data[SCRIPT_COMMAND_PROVENANCE_KEY]["subject"]
            == {
                "type": "player",
                "id": self.player.id,
                "key": self.player.key,
            }
            for event in result.events
        ))
        self.assertEqual(
            {entry["player_key"] for entry in messages},
            {self.player.key, observer.key},
        )

    def test_runner_reuses_resolved_subject_issuer_and_runtime_world(self):
        with patch(
            "spawns.handlers.registry._resolve_command_actor",
            side_effect=AssertionError("command context was fetched again"),
        ):
            result = ScriptCommandRunner().execute(
                issuer=self.room,
                subject=self.player,
                command="say No duplicate identity lookup.",
                render_actor=self.player,
                runtime_world=self.spawn_world,
            )

        self.assertTrue(result.events)

    def test_scripted_command_output_skips_trigger_and_quest_subscribers(self):
        result = ScriptCommandRunner().execute(
            issuer=self.room,
            subject=self.player,
            command="say This line is authored.",
            render_actor=self.player,
            runtime_world=self.spawn_world,
            provenance={
                "trigger_id": 17,
                "trigger_key": "trigger.17",
                "run_id": 23,
                "step_index": 5,
                "action_index": 2,
            },
        )

        with capture_game_messages() as messages, patch(
            "spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event"
        ) as trigger_dispatch, patch(
            "quests.subscriptions.dispatch_quest_subscriptions_for_event"
        ) as quest_dispatch:
            publish_events(result.events)

        trigger_dispatch.assert_not_called()
        quest_dispatch.assert_not_called()
        self.assertTrue(messages)
        self.assertTrue(all(
            SCRIPT_COMMAND_PROVENANCE_KEY
            not in entry["message"]["data"]
            for entry in messages
        ))
        self.assertTrue(all(
            event.data[SCRIPT_COMMAND_PROVENANCE_KEY]
            == {
                "trigger_id": 17,
                "trigger_key": "trigger.17",
                "run_id": 23,
                "step_index": 5,
                "action_index": 2,
                "source": "trigger_step",
                "issuer": {
                    "type": "room",
                    "id": self.room.id,
                    "key": self.room.key,
                },
                "subject": {
                    "type": "player",
                    "id": self.player.id,
                    "key": self.player.key,
                },
            }
            for event in result.events
        ))

    def test_room_subject_is_preserved_in_command_provenance(self):
        result = ScriptCommandRunner().execute(
            issuer=self.room,
            subject=self.room,
            command="/echo The room remembers.",
            render_actor=self.player,
            runtime_world=self.spawn_world,
            provenance={"trigger_id": 17},
        )

        self.assertTrue(result.events)
        self.assertTrue(all(
            event.data[SCRIPT_COMMAND_PROVENANCE_KEY]["subject"]
            == {
                "type": "room",
                "id": self.room.id,
                "key": self.room.key,
            }
            for event in result.events
        ))

    def test_runner_rejects_unapproved_and_nested_commands(self):
        with self.assertRaises(ScriptCommandError) as unsafe:
            ScriptCommandRunner().execute(
                issuer=self.room,
                subject=self.player,
                command="north",
                render_actor=self.player,
                runtime_world=self.spawn_world,
            )
        self.assertEqual(unsafe.exception.code, "command_not_step_safe")

        with self.assertRaises(ScriptCommandError) as nested:
            ScriptCommandRunner().execute(
                issuer=self.room,
                subject=self.player,
                command="/cmd room -- /echo no",
                render_actor=self.player,
                runtime_world=self.spawn_world,
            )
        self.assertEqual(nested.exception.code, "nested_command_not_allowed")

    def test_runner_rejects_zone_fanout_and_non_room_echo_subjects(self):
        mob = self.create_mob("Command Runner Mob")
        for subject in (self.player, mob):
            with self.subTest(command="yell", subject=subject.key):
                with self.assertRaises(ScriptCommandError) as raised:
                    ScriptCommandRunner().execute(
                        issuer=self.room,
                        subject=subject,
                        command="yell Too far.",
                        render_actor=self.player,
                        runtime_world=self.spawn_world,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "command_not_step_safe",
                )

        for subject in (self.player, mob):
            with self.subTest(command="/echo", subject=subject.key):
                with self.assertRaises(ScriptCommandError) as raised:
                    ScriptCommandRunner().execute(
                        issuer=self.room,
                        subject=subject,
                        command="/echo The room speaks.",
                        render_actor=self.player,
                        runtime_world=self.spawn_world,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "unsupported_command_subject",
                )

        unsafe_room_echoes = (
            "/echo zone Too far.",
            "/echo z Too far.",
            "/echo world Too far.",
            "/echo w Too far.",
            "/zecho Too far.",
            "/wecho Too far.",
        )
        for command in unsafe_room_echoes:
            with self.subTest(command=command, subject=self.room.key):
                with self.assertRaises(ScriptCommandError) as raised:
                    ScriptCommandRunner().execute(
                        issuer=self.room,
                        subject=self.room,
                        command=command,
                        render_actor=self.player,
                        runtime_world=self.spawn_world,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "command_scope_not_step_safe",
                )

        safe_room_echoes = (
            "/echo The room speaks.",
            "/echo room The room speaks.",
            "/echo -- The room speaks.",
        )
        for command in safe_room_echoes:
            with self.subTest(command=command, subject=self.room.key):
                result = ScriptCommandRunner().execute(
                    issuer=self.room,
                    subject=self.room,
                    command=command,
                    render_actor=self.player,
                    runtime_world=self.spawn_world,
                )
                self.assertTrue(result.events)

    def test_runner_propagates_and_bounds_script_command_depth(self):
        result = ScriptCommandRunner().execute(
            issuer=self.room,
            subject=self.player,
            command="say One layer deeper.",
            render_actor=self.player,
            runtime_world=self.spawn_world,
            provenance={"command_depth": 3},
        )

        self.assertTrue(result.events)
        self.assertTrue(all(
            event.data[SCRIPT_COMMAND_DEPTH_KEY] == 4
            for event in result.events
        ))

        with self.assertRaises(ScriptCommandError) as raised:
            ScriptCommandRunner().execute(
                issuer=self.room,
                subject=self.player,
                command="say Too deep.",
                render_actor=self.player,
                runtime_world=self.spawn_world,
                provenance={"command_depth": MAX_SCRIPT_COMMAND_DEPTH},
            )
        self.assertEqual(raised.exception.code, "command_depth_exceeded")
