import uuid
from datetime import timedelta
from unittest.mock import patch

from config import constants as adv_consts
from django.db import OperationalError, connection, transaction
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from spawns.handlers import dispatch_command
from spawns.actions.builder import TransferAction
from spawns.actions.mob_movement import ResolveTrackerChaseAction
from spawns.events import (
    FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
    FOLLOW_HAS_FOLLOWERS_KEY,
    PLAYER_ROOM_ENTER_EVENT_TYPE,
    durable_follow_directional_move_events,
    flush_game_event_outbox,
    follow_directional_move_event,
    publish_events,
)
from spawns.following import (
    FOLLOW_LEADER_ROOM_SNAPSHOT_KEY,
    FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY,
    FOLLOW_OUTBOX_RETRY_BASE_SECONDS,
    MAX_FOLLOW_TASK_RETRIES,
    MAX_FOLLOW_UNRESOLVED_SWEEPS,
    FollowMovementBatchResult,
    _claim_and_enqueue_follow_movement,
    propagate_follow_movement_batch,
)
from spawns.models import GameEventOutbox, MovementFollow
from spawns.tasks import propagate_follow_movement, run_mob_roaming
from tests.base import WorldTestCase
from tests.utils import capture_game_messages


class TestFollowingMovement(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.stamina = 20
        self.player.health = 20
        self.player.save(
            update_fields=["in_game", "stamina", "health"],
        )

    def _online_player(self, name, *, room=None):
        player = self.create_player(name, room=room)
        player.in_game = True
        player.stamina = 20
        player.health = 20
        player.save(
            update_fields=["in_game", "stamina", "health"],
        )
        return player

    def _committed_edge(
        self,
        leader,
        *,
        destination,
        direction=adv_consts.DIRECTION_EAST,
        sequence=1,
        source="move",
        root_id=None,
        depth=0,
    ):
        origin_room_id = leader.room_id
        leader.room = destination
        leader.follow_move_sequence = sequence
        leader.save(update_fields=["room", "follow_move_sequence"])
        return follow_directional_move_event(
            actor=leader,
            origin_room_id=origin_room_id,
            destination_room_id=destination.id,
            direction=direction,
            source=source,
            root_id=root_id,
            depth=depth,
        ).data

    def _messages_of_type(self, messages, message_type):
        return [
            entry
            for entry in messages
            if entry["message"].get("type") == message_type
        ]

    def test_ordinary_move_schedules_one_sequenced_edge_after_commit(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_command(
                        command_type="move",
                        player_id=self.player.id,
                        payload={"direction": adv_consts.DIRECTION_EAST},
                    )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(self.player.follow_move_sequence, 1)
        enqueue.assert_called_once()
        edge = enqueue.call_args.kwargs["kwargs"]["event_data"]
        self.assertEqual(edge["actor"]["key"], self.player.key)
        self.assertEqual(edge["origin_room_id"], self.room.id)
        self.assertEqual(edge["destination_room_id"], destination.id)
        self.assertEqual(edge["direction"], adv_consts.DIRECTION_EAST)
        self.assertEqual(edge["source"], "move")
        self.assertEqual(edge["sequence"], 1)
        self.assertEqual(edge["depth"], 0)
        self.assertFalse(
            self._messages_of_type(messages, FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE)
        )
        self.assertTrue(
            GameEventOutbox.objects.filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            ).exists()
        )

    def test_ordinary_move_without_followers_creates_no_outbox_or_task(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        enqueue.assert_not_called()
        self.assertEqual(callbacks, [])
        self.assertFalse(
            GameEventOutbox.objects.filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            ).exists()
        )

    def test_follow_edge_is_recorded_inside_move_transaction_and_rolls_back(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with self.assertRaisesRegex(RuntimeError, "rollback leader move"):
            with transaction.atomic():
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )
                self.assertTrue(
                    GameEventOutbox.objects.filter(
                        event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
                    ).exists()
                )
                raise RuntimeError("rollback leader move")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.follow_move_sequence, 0)
        self.assertFalse(
            GameEventOutbox.objects.filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            ).exists()
        )

    def test_broker_failure_leaves_edge_for_heartbeat_and_task_acknowledges(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with self.assertLogs(
            "spawns.following",
            level="ERROR",
        ) as logs, patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        self.assertIn(
            "Failed to enqueue durable follow movement",
            "\n".join(logs.output),
        )

        row = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNone(row.claim_token)
        self.assertIsNone(row.claimed_until)
        follower.refresh_from_db()
        self.assertEqual(follower.room_id, self.room.id)

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            handed_off = flush_game_event_outbox(
                publisher=publish_events,
                now=row.available_ts + timedelta(seconds=1),
            )
        self.assertEqual(handed_off, 1)
        enqueue.assert_called_once()
        task_kwargs = enqueue.call_args.kwargs["kwargs"]
        self.assertEqual(task_kwargs["outbox_event_id"], str(row.event_id))
        self.assertTrue(task_kwargs["claim_token"])
        self.assertTrue(
            GameEventOutbox.objects.filter(pk=row.pk).exists()
        )

        propagate_follow_movement.run(**task_kwargs)
        follower.refresh_from_db()
        self.assertEqual(follower.room_id, destination.id)
        self.assertFalse(
            GameEventOutbox.objects.filter(pk=row.pk).exists()
        )

    def test_visible_publication_failure_cannot_lose_committed_edge(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.events.publish_to_player",
            side_effect=RuntimeError("websocket unavailable"),
        ), patch("spawns.events.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        self.player.refresh_from_db()
        follower.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(follower.room_id, self.room.id)
        row = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNone(row.claim_token)
        self.assertIsNone(row.claimed_until)
        self.assertIsNotNone(row.depends_on_batch_id)
        self.assertTrue(
            GameEventOutbox.objects.filter(
                batch_id=row.depends_on_batch_id,
            ).exists()
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                delivered = flush_game_event_outbox(
                    now=timezone.now() + timedelta(minutes=10),
                )

        self.assertGreater(delivered, 0)
        row.refresh_from_db()
        self.assertIsNone(row.depends_on_batch_id)
        enqueue.assert_called_once()

    def test_gated_control_cannot_be_claimed_before_visible_ack(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with self.captureOnCommitCallbacks(execute=False):
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": adv_consts.DIRECTION_EAST},
            )

        row = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNotNone(row.depends_on_batch_id)
        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            claimed = _claim_and_enqueue_follow_movement(
                row.data,
                outbox_event_id=str(row.event_id),
            )

        self.assertFalse(claimed)
        enqueue.assert_not_called()
        row.refresh_from_db()
        self.assertIsNone(row.claim_token)

    def test_follow_context_outbox_insert_failure_rolls_back_progress(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with patch(
            "spawns.models.GameEventOutbox.objects.bulk_create",
            side_effect=RuntimeError("outbox insert failed"),
        ), patch("spawns.following.logger.exception"):
            result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.retry_after_id, 0)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(relationship.last_processed_sequence, 0)
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_leaf_follow_move_keeps_visible_entry_durable_until_recovery(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with patch(
            "spawns.events.publish_to_player",
            side_effect=RuntimeError("websocket unavailable"),
        ), patch("spawns.events.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(relationship.last_processed_sequence, 1)
        self.assertTrue(GameEventOutbox.objects.exists())
        self.assertFalse(
            GameEventOutbox.objects.filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            ).exists()
        )

        with patch(
            "spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event",
        ) as trigger_dispatch:
            delivered = flush_game_event_outbox(
                now=timezone.now() + timedelta(minutes=10),
            )

        self.assertGreater(delivered, 0)
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertTrue(any(
            call.kwargs["event_type"] == PLAYER_ROOM_ENTER_EVENT_TYPE
            for call in trigger_dispatch.call_args_list
        ))

    def test_visible_ack_failure_keeps_dependency_fenced_for_retry(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.events._acknowledge_visible_batch_and_activate_follow_edges",
            side_effect=RuntimeError("ack database unavailable"),
        ), patch("spawns.events.logger.exception"), patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        enqueue.assert_not_called()
        control = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNotNone(control.depends_on_batch_id)
        self.assertTrue(
            GameEventOutbox.objects.filter(
                batch_id=control.depends_on_batch_id,
            ).exists()
        )

    def test_schedule_failure_after_ack_is_recovered_by_heartbeat(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with self.assertLogs(
            "django.test",
            level="ERROR",
        ) as logs, patch(
            "spawns.events._schedule_follow_event_data",
            side_effect=RuntimeError("process stopped before schedule"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        self.assertIn("process stopped before schedule", "\n".join(logs.output))

        control = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNone(control.depends_on_batch_id)
        self.assertEqual(GameEventOutbox.objects.count(), 1)

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            handed_off = flush_game_event_outbox(now=timezone.now())

        self.assertEqual(handed_off, 1)
        enqueue.assert_called_once()

    def test_durable_move_preserves_connection_for_output_and_entry(self):
        self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )
        connection_id = "connection.follow-ordering"

        with capture_game_messages() as messages, patch(
            "spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event",
        ) as trigger_dispatch, patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                    connection_id=connection_id,
                )

        success = self._messages_of_type(messages, "cmd.move.success")
        self.assertEqual(len(success), 1)
        self.assertEqual(success[0]["connection_id"], connection_id)
        room_entry_calls = [
            call
            for call in trigger_dispatch.call_args_list
            if call.kwargs["event_type"] == PLAYER_ROOM_ENTER_EVENT_TYPE
        ]
        self.assertEqual(len(room_entry_calls), 1)
        self.assertEqual(
            room_entry_calls[0].kwargs["connection_id"],
            connection_id,
        )

    def test_post_commit_claim_failure_does_not_turn_move_into_failure(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with self.assertLogs(
            "django.test",
            level="ERROR",
        ) as logs, patch(
            "spawns.following._claim_and_enqueue_follow_movement",
            side_effect=RuntimeError("claim database unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )

        self.assertIn("claim database unavailable", "\n".join(logs.output))

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        row = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIsNone(row.claim_token)

    def test_stale_claim_token_cannot_move_or_ack_followers(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )
        stale_kwargs = enqueue.call_args.kwargs["kwargs"]
        row = GameEventOutbox.objects.get(
            event_id=stale_kwargs["outbox_event_id"],
        )
        replacement_token = uuid.uuid4()
        row.claim_token = replacement_token
        row.claimed_until = timezone.now() + timedelta(minutes=5)
        row.save(update_fields=["claim_token", "claimed_until"])

        self.assertEqual(propagate_follow_movement.run(**stale_kwargs), 0)
        follower.refresh_from_db()
        self.assertEqual(follower.room_id, self.room.id)
        self.assertNotEqual(follower.room_id, destination.id)
        row.refresh_from_db()
        self.assertEqual(row.claim_token, replacement_token)

    def test_exceptional_follower_does_not_starve_later_follower(self):
        from spawns.handlers.registry import dispatch_command as real_dispatch

        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        later_follower = self._online_player("Later Follower")
        first = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        second = MovementFollow.objects.create(
            follower=later_follower,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        def dispatch_with_one_failure(*args, **kwargs):
            if kwargs.get("actor_id") == first.follower_id:
                raise RuntimeError("injected follower failure")
            return real_dispatch(*args, **kwargs)

        with self.assertLogs(
            "spawns.following",
            level="ERROR",
        ) as logs, patch(
            "spawns.handlers.registry.dispatch_command",
            side_effect=dispatch_with_one_failure,
        ):
            result = propagate_follow_movement_batch(edge, batch_size=2)

        self.assertIn("Failed follow movement", "\n".join(logs.output))

        self.player.refresh_from_db()
        later_follower.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(first.last_processed_sequence, 0)
        self.assertEqual(later_follower.room_id, destination.id)
        self.assertEqual(second.last_processed_sequence, 1)
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.retry_after_id, 0)

    def test_mob_follow_presence_is_batched_for_many_edges(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        mobs = [self.create_mob(f"Guide {index}") for index in range(3)]
        for mob in mobs:
            mob.room = destination
            mob.follow_move_sequence = 1
            mob.save(update_fields=["room", "follow_move_sequence"])
        events = [
            follow_directional_move_event(
                actor=mob,
                origin_room_id=self.room.id,
                destination_room_id=destination.id,
                direction=adv_consts.DIRECTION_EAST,
                source="tracker",
            )
            for mob in mobs
        ]

        with CaptureQueriesContext(connection) as captured:
            prepared = durable_follow_directional_move_events(events)

        follow_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "spawns_movementfollow" in query["sql"].lower()
        ]
        self.assertEqual(len(follow_queries), 1)
        self.assertTrue(all(
            event.data[FOLLOW_HAS_FOLLOWERS_KEY] is False
            for event in prepared
        ))
        self.assertFalse(
            GameEventOutbox.objects.filter(
                event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            ).exists()
        )

    def test_page_with_retry_and_continuation_schedules_only_one_next_task(self):
        result = FollowMovementBatchResult(
            processed=4,
            next_after_id=100,
            retry_after_id=0,
        )
        with patch(
            "spawns.following.propagate_follow_movement_batch",
            return_value=result,
        ), patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "one-chain"},
            )

        self.assertEqual(processed, 4)
        enqueue.assert_called_once()
        continuation = enqueue.call_args.kwargs["kwargs"]
        self.assertEqual(continuation["after_id"], 100)
        self.assertTrue(continuation["sweep_needs_retry"])

    def test_page_failure_retry_cap_defers_to_durable_slow_backoff(self):
        claim_token = uuid.uuid4()
        outbox = GameEventOutbox.objects.create(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            data={"movement_id": "broken-page"},
            recipients=[],
            claim_token=claim_token,
            claimed_until=timezone.now() + timedelta(minutes=5),
            attempt_count=2,
        )
        started_at = timezone.now()

        with self.assertLogs(
            "spawns.tasks",
            level="ERROR",
        ) as logs, patch(
            "spawns.following.propagate_follow_movement_batch",
            side_effect=RuntimeError("page database unavailable"),
        ), patch(
            "spawns.tasks._follow_task_retry_count",
            return_value=MAX_FOLLOW_TASK_RETRIES,
        ), patch.object(
            propagate_follow_movement,
            "retry",
        ) as retry:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "broken-page"},
                outbox_event_id=str(outbox.event_id),
                claim_token=str(claim_token),
            )

        self.assertIn(
            "Follow movement broken-page page failed",
            "\n".join(logs.output),
        )

        self.assertEqual(processed, 0)
        retry.assert_not_called()
        outbox.refresh_from_db()
        self.assertIsNone(outbox.claim_token)
        self.assertIsNone(outbox.claimed_until)
        self.assertIn("page failed after 5 task retries", outbox.last_error)
        self.assertGreaterEqual(
            outbox.available_ts,
            started_at + timedelta(
                seconds=FOLLOW_OUTBOX_RETRY_BASE_SECONDS * 2,
            ),
        )

    def test_continuation_enqueue_retry_cap_defers_durable_edge(self):
        claim_token = uuid.uuid4()
        outbox = GameEventOutbox.objects.create(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            data={"movement_id": "broken-continuation"},
            recipients=[],
            claim_token=claim_token,
            claimed_until=timezone.now() + timedelta(minutes=5),
            attempt_count=1,
        )
        result = FollowMovementBatchResult(
            processed=4,
            next_after_id=100,
            retry_after_id=None,
        )

        with self.assertLogs(
            "spawns.tasks",
            level="ERROR",
        ) as logs, patch(
            "spawns.following.propagate_follow_movement_batch",
            return_value=result,
        ), patch(
            "spawns.following._enqueue_follow_task",
            side_effect=RuntimeError("broker unavailable"),
        ), patch(
            "spawns.tasks._follow_task_retry_count",
            return_value=MAX_FOLLOW_TASK_RETRIES,
        ), patch.object(
            propagate_follow_movement,
            "retry",
        ) as retry:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "broken-continuation"},
                outbox_event_id=str(outbox.event_id),
                claim_token=str(claim_token),
            )

        self.assertIn(
            "Failed to continue follow movement broken-continuation",
            "\n".join(logs.output),
        )

        self.assertEqual(processed, 4)
        retry.assert_not_called()
        outbox.refresh_from_db()
        self.assertIsNone(outbox.claim_token)
        self.assertIsNone(outbox.claimed_until)
        self.assertIn(
            "continuation enqueue failed after 5 task retries",
            outbox.last_error,
        )

    def test_unresolved_sweep_cap_releases_durable_lease_with_slow_backoff(self):
        claim_token = uuid.uuid4()
        outbox = GameEventOutbox.objects.create(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            data={"movement_id": "poison-edge"},
            recipients=[],
            claim_token=claim_token,
            claimed_until=timezone.now() + timedelta(minutes=5),
            attempt_count=3,
        )
        result = FollowMovementBatchResult(
            processed=0,
            next_after_id=None,
            retry_after_id=0,
        )
        started_at = timezone.now()

        with patch(
            "spawns.following.propagate_follow_movement_batch",
            return_value=result,
        ), patch(
            "spawns.following.has_unresolved_follow_movement",
            return_value=True,
        ), patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "poison-edge"},
                outbox_event_id=str(outbox.event_id),
                claim_token=str(claim_token),
                attempt=MAX_FOLLOW_UNRESOLVED_SWEEPS - 1,
            )

        self.assertEqual(processed, 0)
        enqueue.assert_not_called()
        outbox.refresh_from_db()
        self.assertIsNone(outbox.claim_token)
        self.assertIsNone(outbox.claimed_until)
        self.assertEqual(outbox.attempt_count, 3)
        self.assertIn("after 8 sweeps", outbox.last_error)
        expected_backoff = FOLLOW_OUTBOX_RETRY_BASE_SECONDS * 4
        self.assertGreaterEqual(
            outbox.available_ts,
            started_at + timedelta(seconds=expected_backoff),
        )
        self.assertLess(
            outbox.available_ts,
            timezone.now() + timedelta(seconds=expected_backoff + 2),
        )

    def test_unresolved_sweep_cap_stops_legacy_chain_without_outbox(self):
        result = FollowMovementBatchResult(
            processed=0,
            next_after_id=None,
            retry_after_id=0,
        )
        with patch(
            "spawns.following.propagate_follow_movement_batch",
            return_value=result,
        ), patch(
            "spawns.following.has_unresolved_follow_movement",
            return_value=True,
        ), patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "legacy-edge"},
                attempt=MAX_FOLLOW_UNRESOLVED_SWEEPS - 1,
            )

        self.assertEqual(processed, 0)
        enqueue.assert_not_called()

    def test_unresolved_sweep_release_cannot_clear_a_newer_claim(self):
        stale_token = uuid.uuid4()
        replacement_token = uuid.uuid4()
        outbox = GameEventOutbox.objects.create(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            data={"movement_id": "reclaimed-edge"},
            recipients=[],
            claim_token=stale_token,
            claimed_until=timezone.now() + timedelta(minutes=5),
            attempt_count=2,
        )
        result = FollowMovementBatchResult(
            processed=0,
            next_after_id=None,
            retry_after_id=0,
        )

        def reclaim_before_release(_event_data):
            GameEventOutbox.objects.filter(pk=outbox.pk).update(
                claim_token=replacement_token,
                claimed_until=timezone.now() + timedelta(minutes=5),
            )
            return True

        with patch(
            "spawns.following.propagate_follow_movement_batch",
            return_value=result,
        ), patch(
            "spawns.following.has_unresolved_follow_movement",
            side_effect=reclaim_before_release,
        ), patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            processed = propagate_follow_movement.run(
                event_data={"movement_id": "reclaimed-edge"},
                outbox_event_id=str(outbox.event_id),
                claim_token=str(stale_token),
                attempt=MAX_FOLLOW_UNRESOLVED_SWEEPS - 1,
            )

        self.assertEqual(processed, 0)
        enqueue.assert_not_called()
        outbox.refresh_from_db()
        self.assertEqual(outbox.claim_token, replacement_token)
        self.assertIsNotNone(outbox.claimed_until)
        self.assertEqual(outbox.last_error, "")

    def test_page_failure_backoff_uses_celery_retry_count(self):
        with self.assertLogs(
            "spawns.tasks",
            level="ERROR",
        ) as logs, patch(
            "spawns.following.propagate_follow_movement_batch",
            side_effect=RuntimeError("page database unavailable"),
        ), patch(
            "spawns.tasks._follow_task_retry_count",
            return_value=4,
        ), patch.object(
            propagate_follow_movement,
            "retry",
            side_effect=RuntimeError("celery retry requested"),
        ) as retry:
            with self.assertRaisesRegex(
                RuntimeError,
                "celery retry requested",
            ):
                propagate_follow_movement.run(
                    event_data={"movement_id": "retry-backoff"},
                    attempt=0,
                )

        self.assertIn(
            "Follow movement retry-backoff page failed",
            "\n".join(logs.output),
        )

        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs["countdown"], 16)

    def test_direct_follower_uses_the_ordinary_move_handler(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        stamina_before = self.player.stamina

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, destination.id)
        self.assertLess(self.player.stamina, stamina_before)
        self.assertEqual(self.player.follow_move_sequence, 1)
        self.assertEqual(relationship.last_processed_sequence, 1)
        success = self._messages_of_type(messages, "cmd.move.success")
        self.assertEqual(len(success), 1)
        self.assertEqual(success[0]["player_key"], self.player.key)
        arrivals = self._messages_of_type(
            messages,
            "notification.movement.enter",
        )
        self.assertTrue(
            any(entry["player_key"] == leader.key for entry in arrivals)
        )

    def test_invisible_player_edge_snapshot_is_durable_and_silently_consumed(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        follower = self._online_player("Follower")
        self.player.is_invisible = True
        self.player.save(update_fields=["is_invisible"])
        relationship = MovementFollow.objects.create(
            follower=follower,
            leader_player=self.player,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="move",
                    player_id=self.player.id,
                    payload={"direction": adv_consts.DIRECTION_EAST},
                )
        edge = enqueue.call_args.kwargs["kwargs"]["event_data"]
        self.assertIs(edge["actor"]["is_invisible"], True)
        outbox = GameEventOutbox.objects.get(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
        )
        self.assertIs(outbox.data["actor"]["is_invisible"], True)

        # The committed edge remains concealed even if the leader becomes
        # visible before the asynchronous propagation task runs.
        self.player.is_invisible = False
        self.player.save(update_fields=["is_invisible"])
        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        follower.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(follower.room_id, self.room.id)
        self.assertEqual(follower.follow_move_sequence, 0)
        self.assertEqual(relationship.last_processed_sequence, 1)
        self.assertTrue(
            MovementFollow.objects.filter(pk=relationship.id).exists()
        )
        self.assertFalse([
            entry
            for entry in messages
            if entry["player_key"] == follower.key
        ])

    def test_visible_player_edge_still_follows_after_leader_turns_invisible(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        self.assertIs(edge["actor"]["is_invisible"], False)
        leader.is_invisible = True
        leader.save(update_fields=["is_invisible"])

        result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(relationship.last_processed_sequence, 1)

    def test_invisible_mob_edge_snapshot_is_silently_consumed_after_toggle(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self.create_mob("Hermes")
        leader.is_invisible = True
        leader.save(update_fields=["is_invisible"])
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        self.assertIs(edge["actor"]["is_invisible"], True)
        leader.is_invisible = False
        leader.save(update_fields=["is_invisible"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(relationship.last_processed_sequence, 1)
        self.assertFalse([
            entry
            for entry in messages
            if entry["player_key"] == self.player.key
        ])

    def test_visible_mob_edge_still_follows_after_mob_turns_invisible(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self.create_mob("Hermes")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        self.assertIs(edge["actor"]["is_invisible"], False)
        leader.is_invisible = True
        leader.save(update_fields=["is_invisible"])

        result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(relationship.last_processed_sequence, 1)

    def test_builder_follower_may_follow_invisible_mob_edge(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self.create_mob("Hermes")
        leader.is_invisible = True
        leader.save(update_fields=["is_invisible"])
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(relationship.last_processed_sequence, 1)

    def test_blocked_follower_gets_private_error_and_keeps_link(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        self.player.stamina = 0
        self.player.save(update_fields=["stamina"])
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.follow_move_sequence, 0)
        self.assertEqual(relationship.last_processed_sequence, 1)
        self.assertTrue(
            MovementFollow.objects.filter(pk=relationship.id).exists()
        )
        errors = self._messages_of_type(messages, "cmd.move.error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["player_key"], self.player.key)
        self.assertEqual(errors[0]["message"]["data"]["code"], "exhausted")
        self.assertEqual(
            errors[0]["message"]["text"],
            "You cannot follow Guide: You are too exhausted to move.",
        )
        self.assertFalse(
            self._messages_of_type(messages, "notification.movement.exit")
        )
        self.assertFalse(
            self._messages_of_type(messages, "notification.movement.enter")
        )

    def test_blocked_follow_error_outbox_failure_rolls_back_progress(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        self.player.stamina = 0
        self.player.save(update_fields=["stamina"])
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with patch(
            "spawns.models.GameEventOutbox.objects.bulk_create",
            side_effect=RuntimeError("error outbox insert failed"),
        ), patch("spawns.following.logger.exception"):
            result = propagate_follow_movement_batch(edge)

        relationship.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.retry_after_id, 0)
        self.assertEqual(relationship.last_processed_sequence, 0)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_blocked_follow_error_survives_publication_failure(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        self.player.stamina = 0
        self.player.save(update_fields=["stamina"])
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with patch(
            "spawns.events.publish_to_player",
            side_effect=RuntimeError("websocket unavailable"),
        ), patch("spawns.events.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                result = propagate_follow_movement_batch(edge)

        relationship.refresh_from_db()
        self.assertEqual(result.processed, 1)
        self.assertEqual(relationship.last_processed_sequence, 1)
        error_row = GameEventOutbox.objects.get(event_type="cmd.move.error")
        self.assertEqual(error_row.recipients, [self.player.key])
        self.assertEqual(error_row.data["code"], "exhausted")

        with capture_game_messages() as messages, patch(
            "spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event",
        ) as trigger_dispatch, patch(
            "quests.subscriptions.dispatch_quest_subscriptions_for_event",
        ) as quest_dispatch:
            delivered = flush_game_event_outbox(
                now=timezone.now() + timedelta(minutes=10),
            )

        self.assertEqual(delivered, 1)
        self.assertFalse(GameEventOutbox.objects.filter(pk=error_row.pk).exists())
        errors = self._messages_of_type(messages, "cmd.move.error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["player_key"], self.player.key)
        self.assertEqual(errors[0]["message"]["data"]["code"], "exhausted")
        self.assertEqual(
            errors[0]["message"]["text"],
            "You cannot follow Guide: You are too exhausted to move.",
        )
        trigger_dispatch.assert_not_called()
        quest_dispatch.assert_not_called()

    def test_out_of_order_edges_retry_then_catch_up_in_sequence(self):
        first_destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        second_destination = first_destination.create_at(
            adv_consts.DIRECTION_EAST,
        )
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )

        leader.room = first_destination
        leader.follow_move_sequence = 1
        first_edge = follow_directional_move_event(
            actor=leader,
            origin_room_id=self.room.id,
            destination_room_id=first_destination.id,
            direction=adv_consts.DIRECTION_EAST,
            source="move",
        ).data
        leader.room = second_destination
        leader.follow_move_sequence = 2
        second_edge = follow_directional_move_event(
            actor=leader,
            origin_room_id=first_destination.id,
            destination_room_id=second_destination.id,
            direction=adv_consts.DIRECTION_EAST,
            source="move",
        ).data
        leader.save(update_fields=["room", "follow_move_sequence"])

        early_result = propagate_follow_movement_batch(second_edge)
        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertIsNotNone(early_result.retry_after_id)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(relationship.last_processed_sequence, 0)

        propagate_follow_movement_batch(first_edge)
        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(self.player.room_id, first_destination.id)
        self.assertEqual(relationship.last_processed_sequence, 1)

        propagate_follow_movement_batch(second_edge)
        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(self.player.room_id, second_destination.id)
        self.assertEqual(relationship.last_processed_sequence, 2)

    def test_relocated_leader_advances_stale_edge_and_future_edge_resumes(self):
        first_destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        resumed_destination = self.room.create_at(adv_consts.DIRECTION_NORTH)
        unrelated_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        stale_edge = self._committed_edge(
            leader,
            destination=first_destination,
        )

        # A non-directional relocation does not create a follow edge or bump
        # the directional sequence.  The queued edge must still be consumed
        # per relationship instead of leaving every later edge in a gap.
        leader.room = unrelated_room
        leader.save(update_fields=["room"])
        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                stale_result = propagate_follow_movement_batch(stale_edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(stale_result.processed, 1)
        self.assertIsNone(stale_result.retry_after_id)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(relationship.last_processed_sequence, 1)
        errors = self._messages_of_type(messages, "cmd.move.error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(
            errors[0]["message"]["data"]["code"],
            "follow_leader_moved",
        )

        leader.room = self.room
        leader.save(update_fields=["room"])
        next_edge = self._committed_edge(
            leader,
            destination=resumed_destination,
            direction=adv_consts.DIRECTION_NORTH,
            sequence=2,
        )
        resumed_result = propagate_follow_movement_batch(next_edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(resumed_result.processed, 1)
        self.assertIsNone(resumed_result.retry_after_id)
        self.assertEqual(self.player.room_id, resumed_destination.id)
        self.assertEqual(relationship.last_processed_sequence, 2)

    def test_player_follow_chain_preserves_root_and_increments_depth(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        middle = self._online_player("Scout")
        root_id = str(uuid.uuid4())
        MovementFollow.objects.create(
            follower=middle,
            leader_player=leader,
            last_processed_sequence=0,
        )
        MovementFollow.objects.create(
            follower=self.player,
            leader_player=middle,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(
            leader,
            destination=destination,
            root_id=root_id,
        )

        with patch("spawns.following.schedule_follow_movement") as schedule:
            with self.captureOnCommitCallbacks(execute=True):
                propagate_follow_movement_batch(edge)
            middle.refresh_from_db()
            self.assertEqual(middle.room_id, destination.id)
            schedule.assert_called_once()
            chained_edge = schedule.call_args.args[0]
            self.assertEqual(chained_edge["actor"]["key"], middle.key)
            self.assertEqual(chained_edge["root_id"], root_id)
            self.assertEqual(chained_edge["depth"], 1)
            self.assertEqual(chained_edge["source"], "follow")

            with self.captureOnCommitCallbacks(execute=True):
                propagate_follow_movement_batch(chained_edge)

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)

    def test_fanout_loads_leader_once_for_a_bounded_page(self):
        from spawns import following

        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        second_follower = self._online_player("Second Follower")
        MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        MovementFollow.objects.create(
            follower=second_follower,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)

        with patch.object(
            following,
            "_leader_snapshot",
            wraps=following._leader_snapshot,
        ) as load_leader:
            result = propagate_follow_movement_batch(edge, batch_size=2)

        self.assertEqual(result.processed, 2)
        self.assertEqual(load_leader.call_count, 1)
        self.player.refresh_from_db()
        second_follower.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(second_follower.room_id, destination.id)

    def test_durable_leader_snapshot_is_immutable_across_fanout_pages(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        unrelated_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        leader = self._online_player("Guide")
        followers = [
            self.player,
            self._online_player("Second Follower"),
            self._online_player("Third Follower"),
        ]
        for follower in followers:
            MovementFollow.objects.create(
                follower=follower,
                leader_player=leader,
                last_processed_sequence=0,
            )
        edge = self._committed_edge(leader, destination=destination)
        outbox = GameEventOutbox.objects.create(
            event_type=FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
            data=edge,
            recipients=[],
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            claimed = _claim_and_enqueue_follow_movement(
                edge,
                outbox_event_id=str(outbox.event_id),
            )

        self.assertTrue(claimed)
        claimed_edge = enqueue.call_args.kwargs["kwargs"]["event_data"]
        self.assertEqual(
            claimed_edge[FOLLOW_LEADER_SEQUENCE_SNAPSHOT_KEY],
            1,
        )
        self.assertEqual(
            claimed_edge[FOLLOW_LEADER_ROOM_SNAPSHOT_KEY],
            destination.id,
        )
        outbox.refresh_from_db()
        self.assertEqual(outbox.data, claimed_edge)

        first_page = propagate_follow_movement_batch(
            claimed_edge,
            batch_size=2,
        )
        self.assertIsNotNone(first_page.next_after_id)

        # Relocation between pages must not make the last page observe a
        # different validation decision from the first page.
        leader.room = unrelated_room
        leader.save(update_fields=["room"])
        second_page = propagate_follow_movement_batch(
            claimed_edge,
            after_id=first_page.next_after_id,
            batch_size=2,
        )

        self.assertEqual(first_page.processed, 2)
        self.assertEqual(second_page.processed, 1)
        for follower in followers:
            follower.refresh_from_db()
            self.assertEqual(follower.room_id, destination.id)

    def test_follow_fanout_uses_nowait_for_follower_player_lock_only(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        player_lock_modes = []
        original_select_for_update = QuerySet.select_for_update

        def record_player_lock(queryset, *args, **kwargs):
            if queryset.model is type(self.player):
                player_lock_modes.append(bool(kwargs.get("nowait", False)))
            return original_select_for_update(queryset, *args, **kwargs)

        with patch.object(
            QuerySet,
            "select_for_update",
            new=record_player_lock,
        ):
            propagate_follow_movement_batch(edge)

        self.assertIn(True, player_lock_modes)

        self.player.room = self.room
        self.player.save(update_fields=["room"])
        player_lock_modes.clear()
        with patch.object(
            QuerySet,
            "select_for_update",
            new=record_player_lock,
        ):
            dispatch_command(
                command_type="move",
                player_id=self.player.id,
                payload={"direction": adv_consts.DIRECTION_EAST},
            )

        self.assertTrue(player_lock_modes)
        self.assertNotIn(True, player_lock_modes)

    def test_contended_follower_player_lock_defers_without_blocking_page(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self._online_player("Guide")
        relationship = MovementFollow.objects.create(
            follower=self.player,
            leader_player=leader,
            last_processed_sequence=0,
        )
        edge = self._committed_edge(leader, destination=destination)
        original_get = QuerySet.get

        def reject_nowait_player_lock(queryset, *args, **kwargs):
            if (
                queryset.model is type(self.player)
                and queryset.query.select_for_update_nowait
            ):
                raise OperationalError("could not obtain lock on row")
            return original_get(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", new=reject_nowait_player_lock):
            result = propagate_follow_movement_batch(edge)

        self.player.refresh_from_db()
        relationship.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(relationship.last_processed_sequence, 0)
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.retry_after_id, 0)

    def test_single_mob_roam_emits_edge_and_moves_player_follower(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        self.world.config.default_roam_chance = 100
        self.world.config.save(update_fields=["default_roam_chance"])
        leader = self.create_mob("Hermes", roams=self.zone)
        MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )

        with patch(
            "spawns.tasks.propagate_follow_movement.apply_async",
        ) as enqueue:
            with capture_game_messages():
                with self.captureOnCommitCallbacks(execute=True):
                    roamed = run_mob_roaming()

        leader.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(leader.room_id, destination.id)
        self.assertEqual(leader.follow_move_sequence, 1)
        enqueue.assert_called_once()
        edge = enqueue.call_args.kwargs["kwargs"]["event_data"]
        self.assertEqual(edge["actor"]["key"], leader.key)
        self.assertEqual(edge["source"], "roam")

        propagate_follow_movement_batch(edge)
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)

    def test_tracker_move_emits_edge_and_moves_player_follower(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        target = self._online_player("Target", room=destination)
        leader = self.create_mob(
            "Hermes",
            keywords="hermes",
            health=20,
            health_max=20,
            attack_power=0,
            fights_back=False,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
            trait_instances=[{"key": "tracker"}],
        )
        MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )

        with patch("spawns.following.schedule_follow_movement") as schedule:
            with self.captureOnCommitCallbacks(execute=True):
                result = ResolveTrackerChaseAction().execute(
                    chase_key=str(uuid.uuid4()),
                    player_id=target.id,
                    world_id=self.spawn_world.id,
                    origin_room_id=self.room.id,
                    destination_room_id=destination.id,
                    direction=adv_consts.DIRECTION_EAST,
                    encounter_ids=[],
                    mob_ids=[leader.id],
                    source="move",
                )

        leader.refresh_from_db()
        self.assertEqual(leader.room_id, destination.id)
        self.assertEqual(leader.follow_move_sequence, 1)
        self.assertEqual(result.events, [])
        schedule.assert_called_once()
        edge_data = schedule.call_args.args[0]
        self.assertEqual(edge_data["actor"]["key"], leader.key)
        self.assertEqual(edge_data["source"], "tracker")

        with self.captureOnCommitCallbacks(execute=True):
            propagate_follow_movement_batch(edge_data)
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)

    def test_directional_mob_transfer_emits_last_edge_and_moves_follower(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        leader = self.create_mob("Hermes", keywords="hermes")
        MovementFollow.objects.create(
            follower=self.player,
            leader_mob=leader,
            last_processed_sequence=0,
        )

        with patch("spawns.following.schedule_follow_movement") as schedule:
            with self.captureOnCommitCallbacks(execute=True):
                result = TransferAction().execute(
                    actor=self.player,
                    target_selector="hermes",
                    room_selector=adv_consts.DIRECTION_EAST,
                    runtime_world=self.spawn_world,
                )

        leader.refresh_from_db()
        self.assertEqual(leader.room_id, destination.id)
        self.assertEqual(leader.follow_move_sequence, 1)
        self.assertEqual(result.events, [])
        schedule.assert_called_once()
        edge_data = schedule.call_args.args[0]
        self.assertEqual(edge_data["actor"]["key"], leader.key)
        self.assertEqual(edge_data["source"], "transfer")

        with self.captureOnCommitCallbacks(execute=True):
            propagate_follow_movement_batch(edge_data)
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
