import spawns.handlers  # noqa: F401
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models.query import QuerySet

from builders.models import Trigger
from config import constants as adv_consts
from spawns import trigger_steps as trigger_steps_module
from spawns.handlers.base import TRIGGER_STEP_MODE_TRANSACTIONAL
from spawns.models import (
    GameEventOutbox,
    MovementFollow,
    Player,
    ScheduledTriggerRun,
)
from spawns.script_commands import ScriptCommandError, ScriptCommandRunner
from spawns.trigger_steps import process_due_trigger_runs, start_trigger_steps
from tests.base import WorldTestCase
from worlds.models import Room


class TestFollowTriggerCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.group_id = "unrelated-roaming-cohort"
        self.player.save(update_fields=["in_game", "group_id"])
        self.hermes = self.create_mob(
            "Hermes",
            keywords="hermes messenger",
            follow_move_sequence=12,
        )

    def _execute(self, command, *, subject=None):
        return ScriptCommandRunner().execute(
            issuer=self.room,
            subject=subject or self.player,
            command=command,
            render_actor=self.player,
            runtime_world=self.spawn_world,
        )

    def _create_trigger(self, *, steps, name="Follow Hermes"):
        return Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name=name,
            match=name.casefold(),
            script="",
            steps=steps,
            conditions="",
            gate_delay=0,
            display_action_in_room=False,
        )

    def assert_player_group_unchanged(self):
        self.player.refresh_from_db()
        self.assertEqual(self.player.group_id, "unrelated-roaming-cohort")

    def test_runner_executes_transactional_follow_and_unfollow(self):
        with transaction.atomic():
            follow_result = self._execute("follow hermes")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(follow_result.command, "follow hermes")
        self.assertEqual(
            follow_result.mode,
            TRIGGER_STEP_MODE_TRANSACTIONAL,
        )
        self.assertEqual(link.leader_mob_id, self.hermes.id)
        self.assertEqual(link.last_processed_sequence, 12)
        self.assertIn(
            "cmd.follow.success",
            {event.type for event in follow_result.events},
        )
        self.assert_player_group_unchanged()

        with transaction.atomic():
            unfollow_result = self._execute("unfollow hermes")

        self.assertEqual(unfollow_result.command, "unfollow hermes")
        self.assertEqual(
            unfollow_result.mode,
            TRIGGER_STEP_MODE_TRANSACTIONAL,
        )
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )
        self.assertIn(
            "cmd.unfollow.success",
            {event.type for event in unfollow_result.events},
        )
        self.assert_player_group_unchanged()

    def test_runner_follow_and_unfollow_participate_in_outer_rollback(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self._execute("follow hermes")
                self.assertTrue(
                    MovementFollow.objects.filter(follower=self.player).exists()
                )
                raise RuntimeError("roll back follow")

        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

        with transaction.atomic():
            self._execute("follow hermes")
        original_link = MovementFollow.objects.get(follower=self.player)

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self._execute("unfollow")
                self.assertFalse(
                    MovementFollow.objects.filter(follower=self.player).exists()
                )
                raise RuntimeError("roll back unfollow")

        restored_link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(restored_link.id, original_link.id)
        self.assertEqual(restored_link.leader_mob_id, self.hermes.id)
        self.assert_player_group_unchanged()

    def test_runner_rejects_room_and_mob_follow_subjects(self):
        for command in ("follow hermes", "unfollow"):
            for subject in (self.room, self.hermes):
                with self.subTest(command=command, subject=subject.key):
                    with self.assertRaises(ScriptCommandError) as raised:
                        with transaction.atomic():
                            self._execute(command, subject=subject)

                    self.assertEqual(
                        raised.exception.code,
                        "unsupported_command_subject",
                    )

        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )
        self.assert_player_group_unchanged()

    def test_scheduled_final_step_issues_exact_follow_command_to_actor(self):
        final_action = {
            "type": "command",
            "subject": "trigger_actor",
            "command": "follow hermes",
        }
        trigger = self._create_trigger(
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "Hermes turns toward the southern door.",
                        },
                    ],
                },
                {
                    "after_seconds": 1,
                    "actions": [final_action],
                },
            ],
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertTrue(result.started)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(run.next_step_index, 1)
        self.assertEqual(run.steps[-1]["actions"], [final_action])
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            processed = process_due_trigger_runs(
                limit=10,
                now=run.next_run_ts,
            )

        self.assertEqual(processed["completed"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_mob_id, self.hermes.id)
        self.assertEqual(link.last_processed_sequence, 12)
        self.assert_player_group_unchanged()

    def test_immediate_follow_prelocks_graph_before_player_row(self):
        trigger = self._create_trigger(
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "follow hermes",
                        },
                    ],
                },
            ],
        )
        order = []
        original_select_for_update = QuerySet.select_for_update

        def record_graph_lock(runtime_world_id):
            order.append("graph")
            from spawns.follow_lifecycle import lock_movement_follow_graph

            return lock_movement_follow_graph(runtime_world_id)

        def record_select_for_update(queryset, *args, **kwargs):
            if queryset.model is Player:
                order.append("player")
            return original_select_for_update(queryset, *args, **kwargs)

        with patch.object(
            trigger_steps_module,
            "lock_movement_follow_graph",
            side_effect=record_graph_lock,
        ), patch.object(
            QuerySet,
            "select_for_update",
            new=record_select_for_update,
        ):
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertTrue(result.started)
        self.assertIn("graph", order)
        self.assertIn("player", order)
        self.assertLess(order.index("graph"), order.index("player"))

    def test_delayed_follow_prelocks_graph_before_player_row(self):
        trigger = self._create_trigger(
            steps=[
                {
                    "after_seconds": 1,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "follow hermes",
                        },
                    ],
                },
            ],
        )
        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )
        self.assertTrue(result.started)
        run = ScheduledTriggerRun.objects.get(pk=result.run_id)
        order = []
        original_select_for_update = QuerySet.select_for_update

        def record_graph_lock(runtime_world_id):
            order.append("graph")
            from spawns.follow_lifecycle import lock_movement_follow_graph

            return lock_movement_follow_graph(runtime_world_id)

        def record_select_for_update(queryset, *args, **kwargs):
            if queryset.model is Player:
                order.append("player")
            return original_select_for_update(queryset, *args, **kwargs)

        with patch.object(
            trigger_steps_module,
            "lock_movement_follow_graph",
            side_effect=record_graph_lock,
        ), patch.object(
            QuerySet,
            "select_for_update",
            new=record_select_for_update,
        ):
            processed = process_due_trigger_runs(
                limit=1,
                now=run.next_run_ts,
            )

        self.assertEqual(processed["completed"], 1)
        self.assertIn("graph", order)
        self.assertIn("player", order)
        self.assertLess(order.index("graph"), order.index("player"))

    def test_failed_trigger_step_rolls_back_preceding_follow(self):
        trigger = self._create_trigger(
            name="Broken follow",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "follow hermes",
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "north",
                        },
                    ],
                },
            ],
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "command_not_step_safe")
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assert_player_group_unchanged()
