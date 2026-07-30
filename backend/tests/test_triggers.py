import json
import uuid
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType

from builders.models import ItemDefinition, MobDefinition, Trigger
from config import constants as adv_consts
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_WORLD,
    get_state_snapshot,
    replace_state_snapshot,
)
from spawns.events import GameEvent, publish_events
from spawns.handlers import dispatch_command
from spawns.models import Item, Mob
from spawns.triggers import (
    TriggerExecutionResult,
    command_trigger_result_message,
)
from tests.base import WorldTestCase
from worlds.models import Room
from tests.utils import capture_game_messages, dispatch_text_command


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
            "match": "touch altar",
            "script": "/echo -- The altar hums.",
            "display_action_in_room": True,
        }
        data.update(overrides)
        return Trigger.objects.create(**data)

    def _mob_notification_message(self, messages, mob_key):
        return next(
            (
                msg["message"]
                for msg in messages
                if (
                    msg["message"].get("type") == "notification.cmd.say.success"
                    and msg["message"].get("data", {}).get("actor", {}).get("key") == mob_key
                )
            ),
            None,
        )

    def test_room_look_includes_matching_trigger_action(self):
        self._create_room_trigger(match="touch altar or touch stone")

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

    def test_command_trigger_actions_support_dsl_with_parentheses(self):
        self._create_room_trigger(
            match="touch altar and (pray or kneel)",
            script="/cmd room -- /echo -- The altar awakens.",
        )

        with capture_game_messages() as matching_messages:
            dispatch_text_command(self.player.id, "touch altar pray")

        matching_echo = self._message_by_type(matching_messages, "cmd./echo.success")
        self.assertIsNotNone(matching_echo)
        self.assertIn("The altar awakens.", matching_echo.get("text", ""))

        with capture_game_messages() as non_matching_messages:
            dispatch_text_command(self.player.id, "touch altar bow")

        error_message = self._message_by_type(non_matching_messages, "cmd.text.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(
            error_message.get("text"),
            "Unknown command: 'touch altar bow'. Type 'help' for help.",
        )
        self.assertIsNone(self._message_by_type(non_matching_messages, "cmd./echo.success"))

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
        self.assertEqual(
            {
                (
                    call.kwargs["kwargs"]["expected_world_id"],
                    call.kwargs["kwargs"]["expected_room_id"],
                )
                for call in mock_apply_async.call_args_list
            },
            {(self.player.world_id, self.player.room_id)},
        )

    def test_multiline_script_renders_templates_before_scheduling_followups(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="delayed-grant-trident",
            name="a delayed grant trident",
        )
        self._create_room_trigger(
            script=(
                "/cmd room -- /echo -- First line.\n"
                "/cmd room -- /grantitem {{ actor_key }} delayed-grant-trident"
            ),
        )

        with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as mock_apply_async:
            dispatch_text_command(self.player.id, "touch altar")

        self.assertEqual(mock_apply_async.call_count, 1)
        scheduled_kwargs = mock_apply_async.call_args.kwargs["kwargs"]
        self.assertEqual(
            scheduled_kwargs["segments"],
            [f"/cmd room -- /grantitem {self.player.key} delayed-grant-trident"],
        )

        from spawns.tasks import execute_trigger_script_segments

        execute_trigger_script_segments(**scheduled_kwargs)

        loaded_item = self.player.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)

    def test_delayed_multiline_script_does_not_follow_actor_to_other_runtime(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="runtime-bound-trident",
            name="a runtime-bound trident",
        )
        self._create_room_trigger(
            script=(
                "/cmd room -- /echo -- First line.\n"
                "/cmd room -- /grantitem {{ actor_key }} runtime-bound-trident"
            ),
        )

        with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as mock_apply_async:
            dispatch_text_command(self.player.id, "touch altar")

        scheduled_kwargs = mock_apply_async.call_args.kwargs["kwargs"]
        other_runtime = self.world.create_spawn_world(
            instance_ref="other-trigger-runtime",
        )
        self.player.world = other_runtime
        self.player.save(update_fields=["world"])

        from spawns.tasks import execute_trigger_script_segments

        execute_trigger_script_segments(**scheduled_kwargs)

        self.assertFalse(
            self.player.inventory.filter(definition=item_definition).exists()
        )

    def test_delayed_multiline_script_does_not_follow_actor_to_other_room(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="room-bound-trident",
            name="a room-bound trident",
        )
        self._create_room_trigger(
            script=(
                "/cmd room -- /echo -- First line.\n"
                "/cmd room -- /grantitem {{ actor_key }} room-bound-trident"
            ),
        )

        with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as mock_apply_async:
            dispatch_text_command(self.player.id, "touch altar")

        scheduled_kwargs = mock_apply_async.call_args.kwargs["kwargs"]
        other_room = self.room.create_at("north")
        self.player.room = other_room
        self.player.save(update_fields=["room"])

        from spawns.tasks import execute_trigger_script_segments

        execute_trigger_script_segments(**scheduled_kwargs)

        self.assertFalse(
            self.player.inventory.filter(definition=item_definition).exists()
        )

    def test_broker_failure_fallback_keeps_delayed_line_location_bound(self):
        from spawns.triggers import _schedule_trigger_script_line_segments

        with patch(
            "spawns.tasks.execute_trigger_script_segments.apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ), self.captureOnCommitCallbacks(execute=False) as callbacks:
            errors = _schedule_trigger_script_line_segments(
                actor=self.player,
                line_segments=["say Delayed reaction."],
                line_index=1,
                issuer_scope=adv_consts.TRIGGER_SCOPE_ROOM,
                defer_until_commit=True,
            )

        self.assertEqual(errors, [])
        self.assertEqual(len(callbacks), 1)
        other_room = self.room.create_at("north")
        self.player.room = other_room
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            callbacks[0]()

        self.assertEqual(messages, [])

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

    def test_handled_trigger_execution_failure_completes_receipt(self):
        request_id = str(uuid.uuid4())
        rejection = command_trigger_result_message(
            TriggerExecutionResult(
                handled=True,
                feedback="The Trigger command could not be dispatched.",
                status="rejected",
                code="trigger_failed",
            ),
            request_id=request_id,
            request_segment="r.2",
        )

        self.assertIsNotNone(rejection)
        self.assertEqual(rejection["type"], "cmd.trigger.rejected")
        self.assertEqual(rejection["data"]["request_id"], request_id)
        self.assertEqual(rejection["data"]["request_segment"], "r.2")
        self.assertEqual(rejection["data"]["status"], "rejected")
        self.assertEqual(rejection["data"]["code"], "trigger_rejected")
        self.assertEqual(rejection["data"]["reason_code"], "trigger_failed")
        self.assertEqual(
            rejection["data"]["receipt_status"],
            "completed",
        )
        self.assertEqual(
            rejection["text"],
            "The Trigger command could not be dispatched.",
        )

    def test_expected_refusals_are_acknowledged_without_detail(self):
        request_id = str(uuid.uuid4())

        for reason_code in ("conditions_failed", "gated"):
            with self.subTest(reason_code=reason_code):
                rejection = command_trigger_result_message(
                    TriggerExecutionResult(
                        handled=True,
                        status="rejected",
                        code=reason_code,
                    ),
                    request_id=request_id,
                )

                self.assertEqual(
                    rejection["type"],
                    "cmd.trigger.rejected",
                )
                self.assertNotIn("text", rejection)
                self.assertEqual(
                    rejection["data"]["reason_code"],
                    reason_code,
                )
                self.assertEqual(
                    rejection["data"]["receipt_status"],
                    "completed",
                )
                self.assertNotIn("message", rejection["data"])

    def test_trigger_supports_structured_state_conditions(self):
        replace_state_snapshot(STATE_SCOPE_WORLD, self.spawn_world, {"weather": "rainy"})
        self._create_room_trigger(
            script="/echo -- The altar hums.",
            conditions=json.dumps({"eq": ["state.world.weather", "rainy"]}),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("The altar hums.", echo_message.get("text", ""))

    def test_trigger_script_renders_state_template(self):
        replace_state_snapshot(STATE_SCOPE_WORLD, self.spawn_world, {"weather": "stormy"})
        self._create_room_trigger(
            script="/echo -- Weather: {{ state.world.weather }}.",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("Weather: stormy.", echo_message.get("text", ""))

    def test_trigger_script_renders_actor_state_template(self):
        replace_state_snapshot(
            STATE_SCOPE_CHARACTER,
            self.player,
            {"badge": "sun"},
        )
        self._create_room_trigger(
            script="/echo -- Badge: {{ actor_state.badge }}.",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("Badge: sun.", echo_message.get("text", ""))

    def test_trigger_script_sets_triggering_player_character_state(self):
        self._create_room_trigger(
            script="/cmd room -- /state set character {{ actor_key }} starter_gear_issued true",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player).get("starter_gear_issued"),
            True,
        )
        state_message = self._message_by_type(messages, "cmd./state.success")
        self.assertIsNotNone(state_message)
        self.assertEqual(
            state_message.get("data", {}).get("target", {}).get("key"),
            self.player.key,
        )

    def test_room_trigger_sets_runtime_mob_aggression(self):
        guard_definition = MobDefinition.objects.create(
            world=self.world,
            slug="training-guard",
            name="Training Guard",
            keywords="training guard",
            base_properties={
                "aggression": adv_consts.MOB_AGGRESSION_PASSIVE,
            },
        )
        local_guard = guard_definition.spawn(self.room, self.spawn_world)
        parallel_world = self.world.create_spawn_world()
        parallel_guard = guard_definition.spawn(self.room, parallel_world)
        self._create_room_trigger(
            script="/cmd room -- /set guard aggression normal",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        local_guard.refresh_from_db()
        parallel_guard.refresh_from_db()
        self.assertEqual(
            local_guard.aggression,
            adv_consts.MOB_AGGRESSION_NORMAL,
        )
        self.assertEqual(
            parallel_guard.aggression,
            adv_consts.MOB_AGGRESSION_PASSIVE,
        )
        set_message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(set_message)
        self.assertEqual(set_message["data"]["actor"]["char_type"], "room")
        self.assertEqual(set_message["data"]["target"]["key"], local_guard.key)

        fresh_room = self.room.create_at("east")
        fresh_guard = guard_definition.spawn(fresh_room, self.spawn_world)
        self.assertEqual(
            fresh_guard.aggression,
            adv_consts.MOB_AGGRESSION_PASSIVE,
        )

    def test_room_trigger_sets_runtime_mob_text_fields(self):
        guard_definition = MobDefinition.objects.create(
            world=self.world,
            slug="sleeping-guard",
            name="Sleeping Guard",
            keywords="sleeping guard",
            description="The guard sleeps with one hand on a battered shield.",
            room_description="A sleeping guard slumps against the wall.",
        )
        guard = guard_definition.spawn(self.room, self.spawn_world)
        runtime_name = "The Awakened Guard"
        runtime_room_description = (
            "The awakened guard watches from beneath the archway."
        )
        runtime_description = (
            "Old scars cross the awakened guard's weathered face."
        )
        self._create_room_trigger(
            script=(
                f"/cmd room -- /set guard name -- {runtime_name} && "
                "/cmd room -- /set guard room_description -- "
                f"{runtime_room_description} && "
                "/cmd room -- /set guard description -- "
                f"{runtime_description}"
            ),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        guard.refresh_from_db()
        self.assertEqual(guard.name, runtime_name)
        self.assertEqual(guard.room_description, runtime_room_description)
        self.assertEqual(guard.description, runtime_description)
        self.assertEqual(guard.keywords, guard_definition.keywords)
        set_messages = [
            msg["message"]
            for msg in messages
            if msg["message"].get("type") == "cmd./set.success"
        ]
        self.assertEqual(
            [message["data"]["field"] for message in set_messages],
            ["name", "room_description", "description"],
        )

        guard_definition.refresh_from_db()
        self.assertEqual(guard_definition.name, "Sleeping Guard")
        self.assertEqual(
            guard_definition.room_description,
            "A sleeping guard slumps against the wall.",
        )
        self.assertEqual(
            guard_definition.description,
            "The guard sleeps with one hand on a battered shield.",
        )

        fresh_room = self.room.create_at("east")
        fresh_guard = guard_definition.spawn(fresh_room, self.spawn_world)
        self.assertEqual(fresh_guard.name, guard_definition.name)
        self.assertEqual(
            fresh_guard.room_description,
            guard_definition.room_description,
        )
        self.assertEqual(fresh_guard.description, guard_definition.description)

        with capture_game_messages() as look_messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        look_message = self._message_by_type(look_messages, "cmd.look.success")
        self.assertIsNotNone(look_message)
        room_guard = next(
            char
            for char in look_message["data"]["target"]["chars"]
            if char["key"] == guard.key
        )
        self.assertEqual(room_guard["name"], runtime_name)
        self.assertEqual(room_guard["room_description"], runtime_room_description)
        self.assertEqual(room_guard["description"], runtime_description)
        self.assertIn(runtime_room_description, look_message.get("text", ""))
        self.assertNotIn(runtime_description, look_message.get("text", ""))

        with capture_game_messages() as target_look_messages:
            dispatch_text_command(self.player.id, f"look {guard.key}")

        target_look_message = self._message_by_type(
            target_look_messages,
            "cmd.look.success",
        )
        self.assertIsNotNone(target_look_message)
        self.assertEqual(target_look_message["data"]["target_type"], "char")
        target_guard = target_look_message["data"]["target"]
        self.assertEqual(target_guard["char_type"], "mob")
        self.assertEqual(target_guard["name"], runtime_name)
        self.assertEqual(
            target_guard["room_description"],
            runtime_room_description,
        )
        self.assertEqual(target_guard["description"], runtime_description)
        self.assertIn(runtime_name, target_look_message.get("text", ""))
        self.assertIn(runtime_description, target_look_message.get("text", ""))

    def test_room_trigger_clears_runtime_mob_description_overrides(self):
        guard_definition = MobDefinition.objects.create(
            world=self.world,
            slug="authored-guard",
            name="Authored Guard",
            keywords="authored guard",
            description="The authored guard studies every visitor.",
            room_description="An authored guard stands watch here.",
        )
        guard = guard_definition.spawn(self.room, self.spawn_world)
        self._create_room_trigger(
            script=(
                "/cmd room -- /set guard room_description -- && "
                "/cmd room -- /set guard description --"
            ),
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, "touch altar")

        guard.refresh_from_db()
        self.assertEqual(guard.room_description, "")
        self.assertEqual(guard.description, "")

        with capture_game_messages() as look_messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        look_message = self._message_by_type(look_messages, "cmd.look.success")
        self.assertIsNotNone(look_message)
        room_guard = next(
            char
            for char in look_message["data"]["target"]["chars"]
            if char["key"] == guard.key
        )
        self.assertEqual(room_guard["description"], guard_definition.description)
        self.assertEqual(
            room_guard["room_description"],
            guard_definition.room_description,
        )

    def test_room_trigger_transfers_triggering_player_in_runtime_world(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        destination = self.room.create_at("east")
        self._create_room_trigger(
            script=(
                "/cmd room -- /send {{ actor_key }} -- The floor shifts. && "
                f"/cmd room -- /transfer {{{{ actor_key }}}} "
                f"room@{destination.x},{destination.y},{destination.z}"
            ),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        transfer_message = self._message_by_type(
            messages,
            "cmd./transfer.success",
        )
        self.assertIsNotNone(transfer_message)
        self.assertEqual(
            transfer_message["data"]["target"]["id"],
            destination.id,
        )
        send_message = self._message_by_type(messages, "cmd./send.success")
        self.assertIsNotNone(send_message)

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
            match="inspect relic",
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

    def test_built_in_inspect_still_allows_specific_trigger_matches(self):
        Item.objects.create(
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
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            match="inspect relic",
            script="/echo -- The relic glows faintly.",
            display_action_in_room=True,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "inspect relic")

        echo_message = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_message)
        self.assertIn("The relic glows faintly.", echo_message.get("text", ""))

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
            match="focus orb",
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
            match="greet guide",
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

    def test_say_event_trigger_runs_mob_reaction_script(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Sage",
        )
        mob = Mob.objects.create(
            name="Sage",
            world=self.spawn_world,
            room=self.room,
            definition=mob_definition,
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=mob_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="hello and (traveler or friend)",
            script="say Greetings, traveler.",
            display_action_in_room=False,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "say hello traveler")

        notification = self._mob_notification_message(messages, mob.key)
        self.assertIsNotNone(
            notification,
            [msg["message"] for msg in messages],
        )
        self.assertEqual(notification["data"]["text"], "Greetings, traveler.")

    def test_enter_event_trigger_runs_when_player_enters_room(self):
        self.player.in_game = True
        self.player.stamina = 100
        self.player.save(update_fields=["in_game", "stamina"])

        next_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Sanctum",
            x=self.room.x + 1,
            y=self.room.y,
            z=self.room.z,
        )
        self.room.north = next_room
        self.room.save(update_fields=["north"])
        next_room.south = self.room
        next_room.save(update_fields=["south"])

        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Watcher",
        )
        mob = Mob.objects.create(
            name="Watcher",
            world=self.spawn_world,
            room=next_room,
            definition=mob_definition,
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=mob_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="say You are expected.",
            display_action_in_room=False,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "north")

        notification = self._mob_notification_message(messages, mob.key)
        self.assertIsNotNone(
            notification,
            [msg["message"] for msg in messages],
        )
        self.assertEqual(notification["data"]["text"], "You are expected.")

    def test_trigger_subscriptions_dispatch_from_emitted_events(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Archivist",
        )
        mob = Mob.objects.create(
            name="Archivist",
            world=self.spawn_world,
            room=self.room,
            definition=mob_definition,
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=mob_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="archive or ledger",
            script="say Records are eternal.",
            display_action_in_room=False,
        )

        event = GameEvent(
            type="cmd.say.success",
            recipients=[self.player.key],
            data={
                "actor": {"key": self.player.key, "name": self.player.name},
                "text": "show me the archive ledger",
            },
            text="synthetic say event",
        )
        with capture_game_messages() as messages:
            publish_events(
                [event],
                actor_key=self.player.key,
            )

        notification = self._mob_notification_message(messages, mob.key)
        self.assertIsNotNone(
            notification,
            [msg["message"] for msg in messages],
        )
        self.assertEqual(notification["data"]["text"], "Records are eternal.")
