from datetime import timedelta

from django.contrib.contenttypes.models import ContentType

from builders.models import Trigger
from config import constants as adv_consts
from core.trigger_steps import SCRIPT_COMMAND_PROVENANCE_KEY
from spawns.handlers import dispatch_command
from spawns.models import GameEventOutbox, Mob, ScheduledTriggerRun
from spawns.trigger_steps import process_due_trigger_runs, start_trigger_steps
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import InstanceParticipant, World, WorldConfig


class TestExitInstanceCommand(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.is_builder = True
        self.player.in_game = True
        self.player.save(update_fields=["is_builder", "in_game"])

        self.other_base_runtime = self.world.create_spawn_world()
        self.base_destination = self.room.create_at("east")
        instance_config = WorldConfig.objects.create()
        self.instance_template = World.objects.new_world(
            name="Forked Road",
            author=self.user,
            config=instance_config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        self.instance_room = self.instance_template.config.starting_room
        self.instance_collision_room = self.instance_room.create_at("east")
        self.run = World.enter_instance(
            player=self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id,
        ).instance_run
        self.player.refresh_from_db()
        GameEventOutbox.objects.all().delete()

    @property
    def destination_ref(self):
        return f"world@base/room@{self.base_destination.relative_id}"

    @staticmethod
    def _message_by_type(messages, event_type):
        return next(
            (
                entry["message"]
                for entry in messages
                if entry["message"].get("type") == event_type
            ),
            None,
        )

    def test_builder_exits_to_authored_base_room_in_recorded_runtime(self):
        self.assertEqual(
            self.base_destination.relative_id,
            self.instance_collision_room.relative_id,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/exitinstance self {self.destination_ref}",
            )

        self.player.refresh_from_db()
        participant = InstanceParticipant.objects.get(
            run=self.run,
            player=self.player,
        )
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertNotEqual(self.player.world_id, self.other_base_runtime.id)
        self.assertEqual(self.player.room_id, self.base_destination.id)
        self.assertEqual(
            participant.exit_reason,
            InstanceParticipant.EXIT_REASON_FORCED,
        )
        self.assertIsNotNone(participant.exited_at)
        self.assertIsNone(participant.return_runtime_world_id)
        self.assertIsNotNone(
            self._message_by_type(messages, "cmd./exitinstance.success")
        )
        state = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertEqual(state["data"]["room"]["id"], self.base_destination.id)
        self.assertIsNone(state["data"]["world"]["instance_of_id"])

    def test_exit_fires_only_the_base_destination_enter_trigger(self):
        room_type = ContentType.objects.get_for_model(self.base_destination)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_type,
            target_id=self.base_destination.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The road reaches Athens.",
            display_action_in_room=False,
            gate_delay=0,
        )
        Trigger.objects.create(
            world=self.instance_template,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_type,
            target_id=self.instance_collision_room.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The wrong threshold opens.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/exitinstance self {self.destination_ref}",
            )

        echo_texts = [
            entry["message"].get("text", "")
            for entry in messages
            if entry["message"].get("type") == "cmd./echo.success"
        ]
        self.assertEqual(echo_texts.count("The road reaches Athens."), 1)
        self.assertNotIn("The wrong threshold opens.", echo_texts)

    def test_trusted_room_and_mob_scripts_can_exit_local_player(self):
        issuer_cases = (
            ("room", self.instance_room.id, {"world_id": self.run.spawned_world_id}),
            (
                "mob",
                Mob.objects.create(
                    world=self.run.spawned_world,
                    room=self.instance_room,
                    name="a crossroads guide",
                    keywords="guide",
                ).id,
                {},
            ),
        )

        for index, (actor_type, actor_id, runtime_payload) in enumerate(
            issuer_cases
        ):
            with self.subTest(actor_type=actor_type):
                if index:
                    self.run = World.enter_instance(
                        player=self.player,
                        transfer_to_id=self.instance_room.id,
                        transfer_from_id=self.base_destination.id,
                        ref=self.run.ref,
                    ).instance_run
                    self.player.refresh_from_db()
                with capture_game_messages():
                    dispatch_command(
                        command_type="text",
                        actor_type=actor_type,
                        actor_id=actor_id,
                        payload={
                            "text": (
                                f"/exitinstance {self.player.key} "
                                f"{self.destination_ref}"
                            ),
                            **runtime_payload,
                        },
                        script_source=True,
                    )
                self.player.refresh_from_db()
                self.assertEqual(self.player.world_id, self.spawn_world.id)
                self.assertEqual(self.player.room_id, self.base_destination.id)

    def test_room_script_cannot_exit_a_player_outside_issuer_room(self):
        self.player.room = self.instance_collision_room
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.instance_room.id,
                payload={
                    "text": (
                        f"/exitinstance {self.player.key} {self.destination_ref}"
                    ),
                    "world_id": self.run.spawned_world_id,
                },
                script_source=True,
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.run.spawned_world_id)
        error = self._message_by_type(messages, "cmd./exitinstance.error")
        self.assertEqual(error["data"]["code"], "invalid_target")

    def test_unqualified_destination_and_player_script_are_rejected(self):
        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                "/exitinstance self "
                f"room@{self.base_destination.relative_id}",
            )
        invalid_ref = self._message_by_type(
            messages,
            "cmd./exitinstance.error",
        )
        self.assertEqual(invalid_ref["data"]["code"], "invalid_base_room")

        self.player.is_builder = False
        self.player.save(update_fields=["is_builder"])
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": f"/exitinstance self {self.destination_ref}",
                },
                script_source=True,
            )
        permission = self._message_by_type(
            messages,
            "cmd./exitinstance.error",
        )
        self.assertIn("permission", permission["text"].lower())
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.run.spawned_world_id)

    def test_terminal_trigger_step_exits_actor_and_outboxes_provenance(self):
        trigger = Trigger.objects.create(
            world=self.instance_template,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(self.instance_room),
            target_id=self.instance_room.id,
            name="Choose Athens",
            match="go right",
            script="",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                "/exitinstance {{ actor_key }} "
                                f"{self.destination_ref}"
                            ),
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.instance_room,
        )

        self.assertTrue(result.started)
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertEqual(self.player.room_id, self.base_destination.id)
        self.assertEqual(
            ScheduledTriggerRun.objects.get(trigger=trigger).status,
            ScheduledTriggerRun.STATUS_COMPLETED,
        )
        lifecycle_events = list(
            GameEventOutbox.objects.filter(
                event_type="lifecycle.player.room.enter",
            )
        )
        self.assertEqual(len(lifecycle_events), 1)
        self.assertEqual(lifecycle_events[0].data["source"], "instance_leave")
        self.assertEqual(
            lifecycle_events[0].data[SCRIPT_COMMAND_PROVENANCE_KEY]["source"],
            "trigger_step",
        )
        ordered_types = list(
            GameEventOutbox.objects.order_by("batch_id", "sequence", "id")
            .values_list("event_type", flat=True)
        )
        self.assertLess(
            ordered_types.index("cmd.state.sync.success"),
            ordered_types.index("lifecycle.player.room.enter"),
        )
        self.assertTrue(
            GameEventOutbox.objects.filter(event_type="instance.left").exists()
        )

    def test_delayed_terminal_trigger_step_exits_actor(self):
        trigger = Trigger.objects.create(
            world=self.instance_template,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(self.instance_room),
            target_id=self.instance_room.id,
            name="Delayed road choice",
            match="wait then go right",
            script="",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": "The road begins to form.",
                        },
                    ],
                },
                {
                    "after_seconds": 1,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                "/exitinstance {{ actor_key }} "
                                f"{self.destination_ref}"
                            ),
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )

        started = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.instance_room,
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.player.refresh_from_db()
        self.assertTrue(started.started)
        self.assertEqual(self.player.world_id, self.run.spawned_world_id)

        result = process_due_trigger_runs(
            now=run.started_ts + timedelta(seconds=1),
        )

        self.assertEqual(result["completed"], 1)
        self.player.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertEqual(self.player.room_id, self.base_destination.id)
