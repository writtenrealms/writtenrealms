from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType

from builders.models import Trigger
from config import constants as adv_consts
from spawns.handlers import dispatch_command
from spawns.models import Item
from tests.base import WorldTestCase
from worlds.models import Room
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestCommandFallbackTriggers(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _create_room_trigger(self, **overrides):
        room_ct = ContentType.objects.get_for_model(Room)
        data = {
            "world": self.world,
            "scope": adv_consts.TRIGGER_SCOPE_ROOM,
            "kind": adv_consts.TRIGGER_KIND_COMMAND,
            "target_type": room_ct,
            "target_id": self.room.id,
            "actions": "touch altar",
            "script": "/echo -- The altar hums.",
            "display_action_in_room": True,
        }
        data.update(overrides)
        return Trigger.objects.create(**data)

    def test_room_look_includes_matching_trigger_action(self):
        self._create_room_trigger(actions="touch altar or touch stone")

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertIn("touch altar", message["data"]["target"]["actions"])

    def test_unknown_text_runs_trigger_script_without_echo_fallback(self):
        self._create_room_trigger(
            script="/cmd room -- /echo -- The altar hums.",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("The altar hums.", echo_message.get("text", ""))
        self.assertIsNone(self._message_by_type(messages, "cmd.text.echo"))

    def test_multiline_script_executes_first_line_and_schedules_followups(self):
        self._create_room_trigger(
            script=(
                "/cmd room -- /echo -- First line.\n"
                "/cmd room -- /echo -- Second line.\n"
                "/cmd room -- /echo -- Third line."
            ),
        )

        with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as mock_apply_async:
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "touch altar")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("First line.", echo_message.get("text", ""))

        self.assertEqual(mock_apply_async.call_count, 2)
        self.assertEqual(
            [call.kwargs["countdown"] for call in mock_apply_async.call_args_list],
            [2.0, 4.0],
        )
        self.assertEqual(
            [call.kwargs["kwargs"]["segments"] for call in mock_apply_async.call_args_list],
            [
                ["/cmd room -- /echo -- Second line."],
                ["/cmd room -- /echo -- Third line."],
            ],
        )

    def test_multiline_script_delay_is_configurable(self):
        self._create_room_trigger(
            script=(
                "/cmd room -- /echo -- First line.\n"
                "/cmd room -- /echo -- Second line.\n"
                "/cmd room -- /echo -- Third line."
            ),
        )

        with patch("config.game_settings.GAME_HEARTBEAT_INTERVAL_SECONDS", 5):
            with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as mock_apply_async:
                dispatch_text_command(self.player.id, "touch altar")

        self.assertEqual(mock_apply_async.call_count, 2)
        self.assertEqual(
            [call.kwargs["countdown"] for call in mock_apply_async.call_args_list],
            [5.0, 10.0],
        )

    def test_trigger_condition_failure_can_publish_detail(self):
        self._create_room_trigger(
            script="/echo -- Should not run.",
            conditions="name someoneelse",
            show_details_on_failure=True,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        failure_message = self._message_by_type(messages, "cmd.text.trigger")
        self.assertIsNotNone(failure_message)
        self.assertIn("Name does not match", failure_message.get("text", ""))
        self.assertIsNone(self._message_by_type(messages, "cmd.text.echo"))
        self.assertIsNone(self._message_by_type(messages, "cmd./echo.success"))

    def test_room_inventory_item_includes_trigger_actions(self):
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            name="ancient relic",
            keywords="relic",
            type="inert",
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Item),
            target_id=item.id,
            actions="inspect relic",
            script="/echo -- The relic glows faintly.",
            display_action_in_room=True,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        room_items = message["data"]["target"]["inventory"]
        payload_item = next((entry for entry in room_items if entry["key"] == item.key), None)
        self.assertIsNotNone(payload_item)
        self.assertIn("inspect relic", payload_item["actions"])

    def test_player_inventory_item_includes_trigger_actions(self):
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name="mysterious orb",
            keywords="orb",
            type="inert",
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Item),
            target_id=item.id,
            actions="focus orb",
            script="/echo -- The orb hums in your hand.",
            display_action_in_room=True,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        actor_items = message["data"]["actor"]["inventory"]
        payload_item = next((entry for entry in actor_items if entry["key"] == item.key), None)
        self.assertIsNotNone(payload_item)
        self.assertIn("focus orb", payload_item["actions"])

    def test_room_mob_includes_trigger_actions(self):
        mob = self.create_mob("Town Guide")
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(mob.__class__),
            target_id=mob.id,
            actions="greet guide",
            script="/echo -- Welcome, traveler.",
            display_action_in_room=True,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        chars = message["data"]["target"]["chars"]
        payload_mob = next((entry for entry in chars if entry["key"] == mob.key), None)
        self.assertIsNotNone(payload_mob)
        self.assertIn("greet guide", payload_mob["actions"])
