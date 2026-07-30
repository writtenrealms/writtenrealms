import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.utils import timezone

from builders.models import MobDefinition, Trigger
from config import constants as adv_consts
from core.trigger_steps import SCRIPT_COMMAND_DEPTH_KEY
from quests.subscriptions import dispatch_quest_subscriptions_for_event
from spawns.actions.combat import resolve_due_character_effects
from spawns.actions.movement import ChangeRoomAction
from spawns.events import (
    GameEvent,
    PLAYER_ROOM_ENTER_EVENT_TYPE,
    PRIVATE_CONTROL_EVENT_KEY,
    enqueue_game_events,
    flush_game_event_outbox,
    publish_events,
)
from spawns.models import EventSubscriptionReceipt, GameEventOutbox
from spawns.script_commands import MAX_SCRIPT_COMMAND_DEPTH
from spawns.trigger_subscriptions import dispatch_trigger_subscriptions_for_event
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, create_active_effect


class TestGameEventOutbox(WorldTestCase):
    def test_private_control_event_is_not_dispatched_to_game_subscribers(self):
        event = GameEvent(
            type="cmd.trigger.accepted",
            recipients=[self.player.key],
            connection_id="connection.original",
            data={
                PRIVATE_CONTROL_EVENT_KEY: True,
                "request_id": str(uuid.uuid4()),
                "status": "accepted",
            },
        )

        with capture_game_messages() as messages, patch(
            "spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event",
        ) as trigger_dispatch, patch(
            "quests.subscriptions.dispatch_quest_subscriptions_for_event",
        ) as quest_dispatch:
            publish_events([event])

        trigger_dispatch.assert_not_called()
        quest_dispatch.assert_not_called()
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0]["connection_id"],
            "connection.original",
        )
        self.assertNotIn(
            PRIVATE_CONTROL_EVENT_KEY,
            messages[0]["message"]["data"],
        )

    def test_outbox_insert_failure_rolls_back_effect_pulse(self):
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        effect = create_active_effect(
            target=self.player,
            source=self.player,
            payload={
                "effect": "shout",
                "remaining_rounds": 1,
                "duration_rounds": 1,
            },
        )

        with patch(
            "spawns.actions.combat.enqueue_game_events",
            side_effect=RuntimeError("insert failed"),
        ), self.assertRaises(RuntimeError):
            resolve_due_character_effects(persist_events=True)

        effect.refresh_from_db()
        self.assertEqual(effect.remaining_rounds, 1)
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_failed_delivery_stays_pending_and_retries_with_stable_event_id(self):
        enqueue_game_events(
            [
                GameEvent(
                    type="notification.combat.effect",
                    recipients=[self.player.key],
                    data={"actor": {"key": self.player.key}},
                    text="The curse burns.",
                )
            ]
        )
        event_id = str(GameEventOutbox.objects.get().event_id)
        now = timezone.now()

        with patch("spawns.events.logger.exception"):
            self.assertEqual(
                flush_game_event_outbox(
                    publisher=Mock(side_effect=RuntimeError("publish failed")),
                    now=now,
                ),
                0,
            )

        self.assertEqual(GameEventOutbox.objects.count(), 1)
        publisher = Mock()
        self.assertEqual(
            flush_game_event_outbox(
                publisher=publisher,
                now=now + timedelta(minutes=10),
            ),
            1,
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        delivered_event = publisher.call_args.args[0][0]
        self.assertEqual(delivered_event.data["_event_id"], event_id)

    def test_poison_batch_does_not_block_later_batches(self):
        enqueue_game_events(
            [GameEvent(type="test.bad", recipients=[], data={}, text="bad")]
        )
        enqueue_game_events(
            [GameEvent(type="test.good", recipients=[], data={}, text="good")]
        )
        delivered = []

        def publisher(events):
            if events[0].type == "test.bad":
                raise RuntimeError("poison")
            delivered.append(events[0].type)

        with patch("spawns.events.logger.exception"):
            self.assertEqual(
                flush_game_event_outbox(publisher=publisher, now=timezone.now()),
                1,
            )

        self.assertEqual(delivered, ["test.good"])
        self.assertEqual(
            list(GameEventOutbox.objects.values_list("event_type", flat=True)),
            ["test.bad"],
        )

    def test_quest_subscriber_processes_retried_event_once(self):
        event_id = str(uuid.uuid4())
        event_data = {
            "_event_id": event_id,
            "actor": {"key": self.player.key},
            "target": {"key": "mob.1"},
        }

        with patch(
            "quests.subscriptions.refresh_player_quests",
            return_value=None,
        ) as refresh_mock, patch(
            "quests.subscriptions.progress_player_quests_for_event",
            return_value=SimpleNamespace(
                events=[
                    GameEvent(
                        type="quest.instance.progressed",
                        recipients=[self.player.key],
                        data={"actor": {"key": self.player.key}},
                    )
                ]
            ),
        ) as progress_mock:
            dispatch_quest_subscriptions_for_event(
                event_type="quest.mob.killed",
                event_data=event_data,
            )
            dispatch_quest_subscriptions_for_event(
                event_type="quest.mob.killed",
                event_data=event_data,
            )

        refresh_mock.assert_called_once()
        progress_mock.assert_called_once()
        self.assertEqual(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="quests",
            ).count(),
            1,
        )
        self.assertEqual(
            list(GameEventOutbox.objects.values_list("event_type", flat=True)),
            ["quest.instance.progressed"],
        )

    def test_room_enter_subscriber_processes_retried_death_event_once(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        event_id = str(uuid.uuid4())
        event_data = {
            "_event_id": event_id,
            "actor": {"key": self.player.key},
            "origin_room": {"id": self.room.id},
            "destination_room": {"id": self.room.id},
            "runtime_world_id": self.player.world_id,
            "location_sequence": self.player.location_sequence,
            "source": "death",
        }

        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers"
        ) as mob_execute_mock, patch(
            "spawns.trigger_subscriptions.execute_room_event_triggers"
        ) as room_execute_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type=PLAYER_ROOM_ENTER_EVENT_TYPE,
                event_data=event_data,
            )
            dispatch_trigger_subscriptions_for_event(
                event_type=PLAYER_ROOM_ENTER_EVENT_TYPE,
                event_data=event_data,
            )

        mob_execute_mock.assert_called_once()
        self.assertEqual(room_execute_mock.call_count, 2)
        self.assertEqual(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="triggers",
            ).count(),
            1,
        )

    def test_room_enter_subscriber_ignores_stale_location_sequence(self):
        self.player.in_game = True
        self.player.location_sequence = 7
        self.player.save(update_fields=["in_game", "location_sequence"])
        event_id = str(uuid.uuid4())

        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers"
        ) as mob_execute_mock, patch(
            "spawns.trigger_subscriptions.execute_room_event_triggers"
        ) as room_execute_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type=PLAYER_ROOM_ENTER_EVENT_TYPE,
                event_data={
                    "_event_id": event_id,
                    "actor": {"key": self.player.key},
                    "origin_room": {"id": self.room.id},
                    "destination_room": {"id": self.room.id},
                    "runtime_world_id": self.player.world_id,
                    "location_sequence": 6,
                    "source": "jump",
                },
            )

        mob_execute_mock.assert_not_called()
        room_execute_mock.assert_not_called()
        self.assertEqual(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="triggers",
            ).count(),
            1,
        )

    def test_direct_transfer_room_enter_uses_serialized_dispatch(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        event_data = {
            "actor": {"key": self.player.key},
            "origin_room": {"id": self.room.id},
            "destination_room": {"id": self.room.id},
            "runtime_world_id": self.player.world_id,
            "location_sequence": self.player.location_sequence,
            "source": "transfer",
        }

        with patch(
            "spawns.trigger_steps.lock_trigger_runtime_room"
        ) as lock_mock, patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers"
        ) as mob_execute_mock, patch(
            "spawns.trigger_subscriptions.execute_room_event_triggers"
        ) as room_execute_mock, patch(
            "spawns.trigger_subscriptions._player_room_entry_aggro_events",
            return_value=[],
        ):
            dispatch_trigger_subscriptions_for_event(
                event_type=PLAYER_ROOM_ENTER_EVENT_TYPE,
                event_data=event_data,
            )

        lock_mock.assert_called_once_with(
            runtime_world_id=self.player.world_id,
            room_id=self.room.id,
        )
        mob_execute_mock.assert_called_once()
        room_execute_mock.assert_called_once()

    def test_transfer_reaction_loop_is_durable_and_depth_bounded(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        other_room = self.room.create_at("east")
        room_refs = {
            self.room.id: f"room@{self.room.x},{self.room.y},{self.room.z}",
            other_room.id: (
                f"room@{other_room.x},{other_room.y},{other_room.z}"
            ),
        }
        for index, (room, destination) in enumerate(
            (
                (self.room, other_room),
                (other_room, self.room),
            ),
            start=1,
        ):
            definition = MobDefinition.objects.create(
                world=self.world,
                slug=f"loop-watcher-{index}",
                name=f"Loop Watcher {index}",
            )
            definition.spawn(room, self.spawn_world)
            Trigger.objects.create(
                world=self.world,
                kind=adv_consts.TRIGGER_KIND_EVENT,
                scope=adv_consts.TRIGGER_SCOPE_WORLD,
                target_type=ContentType.objects.get_for_model(MobDefinition),
                target_id=definition.id,
                event=adv_consts.MOB_REACTION_EVENT_ENTERING,
                script=(
                    f"/transfer {{{{ actor_key }}}} "
                    f"{room_refs[destination.id]}"
                ),
                display_action_in_room=False,
                gate_delay=0,
            )

        enqueue_game_events([
            GameEvent(
                type="notification./transfer.enter",
                recipients=[],
                data={
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": self.room.id},
                    SCRIPT_COMMAND_DEPTH_KEY: 1,
                },
            )
        ])

        with capture_game_messages() as messages, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            return_value=SimpleNamespace(events=[]),
        ) as aggro_mock:
            delivered = flush_game_event_outbox()

        self.assertGreater(delivered, 0)
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertEqual(
            EventSubscriptionReceipt.objects.filter(
                subscriber="triggers",
            ).count(),
            MAX_SCRIPT_COMMAND_DEPTH,
        )
        aggro_mock.assert_called_once_with(self.player.id)
        self.assertTrue(messages)
        self.assertTrue(all(
            SCRIPT_COMMAND_DEPTH_KEY
            not in entry["message"].get("data", {})
            for entry in messages
        ))

    def test_transfer_reaction_output_rolls_back_with_aggro_failure(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        event_id = str(uuid.uuid4())
        event_data = {
            "_event_id": event_id,
            "actor": {"key": self.player.key},
            "destination_room": {"id": self.room.id},
        }

        def publish_reaction(*args, **kwargs):
            publish_events([
                GameEvent(
                    type="notification.cmd.say.success",
                    recipients=[self.player.key],
                    data={"actor": {"key": "mob.999"}},
                    text="A watcher reacts.",
                )
            ])

        with capture_game_messages() as messages, patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
            side_effect=publish_reaction,
        ), patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            side_effect=RuntimeError("aggro failed"),
        ), self.assertRaisesRegex(RuntimeError, "aggro failed"):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )

        self.assertEqual(messages, [])
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="triggers",
            ).exists()
        )

        with capture_game_messages() as retry_messages, patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
            side_effect=publish_reaction,
        ), patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            return_value=SimpleNamespace(events=[]),
        ):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )

        self.assertEqual(retry_messages, [])
        self.assertEqual(GameEventOutbox.objects.count(), 1)
        self.assertTrue(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="triggers",
            ).exists()
        )

    def test_multiline_transfer_reaction_task_waits_for_commit(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="multiline-arrival-watcher",
            name="Multiline Arrival Watcher",
        )
        definition.spawn(self.room, self.spawn_world)
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="say Immediate reaction.\nsay Delayed reaction.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with patch(
            "spawns.tasks.execute_trigger_script_segments.apply_async",
        ) as schedule_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            side_effect=RuntimeError("aggro failed"),
        ), self.assertRaisesRegex(RuntimeError, "aggro failed"):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data={
                    "_event_id": str(uuid.uuid4()),
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": self.room.id},
                    "runtime_world_id": self.spawn_world.id,
                    "location_sequence": self.player.location_sequence,
                },
            )

        schedule_mock.assert_not_called()

    def test_transfer_reaction_gate_is_released_for_retry_on_failure(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="retry-arrival-watcher",
            name="Retry Arrival Watcher",
        )
        watcher = definition.spawn(self.room, self.spawn_world)
        trigger = Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="say The retry gate opens.",
            display_action_in_room=False,
            gate_delay=60,
        )
        event_id = str(uuid.uuid4())
        event_data = {
            "_event_id": event_id,
            "actor": {"key": self.player.key},
            "destination_room": {"id": self.room.id},
            "runtime_world_id": self.spawn_world.id,
            "location_sequence": self.player.location_sequence,
        }
        gate_key = (
            f"spawns.trigger_gate.{trigger.id}."
            f"runtime:{self.spawn_world.id}:mob:{watcher.id}"
        )

        with patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            side_effect=RuntimeError("aggro failed"),
        ), self.assertRaisesRegex(RuntimeError, "aggro failed"):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )

        self.assertIsNone(cache.get(gate_key))
        with patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            return_value=SimpleNamespace(events=[]),
        ):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )

        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="notification.cmd.say.success",
            ).count(),
            1,
        )

    def test_transfer_reaction_suppresses_direct_command_messages_until_commit(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="direct-message-arrival-watcher",
            name="Direct Message Arrival Watcher",
        )
        definition.spawn(self.room, self.spawn_world)
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="/transfer missing-destination",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            return_value=SimpleNamespace(events=[]),
        ):
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data={
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": self.room.id},
                    "runtime_world_id": self.spawn_world.id,
                    "location_sequence": self.player.location_sequence,
                },
            )

        self.assertEqual(messages, [])

    def test_stale_transfer_enter_does_not_react_or_scan_aggro(self):
        current_room = self.room.create_at("east")
        self.player.room = current_room
        self.player.in_game = True
        self.player.save(update_fields=["room", "in_game"])

        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
        ) as reaction_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
        ) as aggro_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data={
                    "_event_id": str(uuid.uuid4()),
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": self.room.id},
                },
            )

        reaction_mock.assert_not_called()
        aggro_mock.assert_not_called()

    def test_move_away_and_back_invalidates_pending_player_transfer_arrival(self):
        away_room = self.room.create_at("east")
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        transfer_location_sequence = self.player.location_sequence

        ChangeRoomAction().execute(self.player, away_room.id)
        self.player.save(
            update_fields=[
                "room",
                "location_sequence",
                "last_action_ts",
            ]
        )
        ChangeRoomAction().execute(self.player, self.room.id)
        self.player.save(
            update_fields=[
                "room",
                "location_sequence",
                "last_action_ts",
            ]
        )

        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
        ) as reaction_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
        ) as aggro_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data={
                    "_event_id": str(uuid.uuid4()),
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": self.room.id},
                    "runtime_world_id": self.spawn_world.id,
                    "location_sequence": transfer_location_sequence,
                },
            )

        reaction_mock.assert_not_called()
        aggro_mock.assert_not_called()

    def test_only_final_arrival_in_one_batch_reacts_and_scans_aggro(self):
        final_room = self.room.create_at("east")
        intermediate_room = final_room.create_at("east")
        self.player.room = final_room
        self.player.in_game = True
        self.player.save(update_fields=["room", "in_game"])
        enqueue_game_events([
            GameEvent(
                type="notification./transfer.enter",
                recipients=[],
                data={
                    "actor": {"key": self.player.key},
                    "destination_room": {"id": destination.id},
                },
            )
            for destination in (
                final_room,
                intermediate_room,
                final_room,
            )
        ])

        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
        ) as reaction_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
            return_value=SimpleNamespace(events=[]),
        ) as aggro_mock:
            delivered = flush_game_event_outbox()

        self.assertEqual(delivered, 3)
        reaction_mock.assert_called_once()
        self.assertEqual(
            reaction_mock.call_args.kwargs["room"],
            final_room.id,
        )
        aggro_mock.assert_called_once_with(self.player.id)

    def test_transfer_enter_rejects_offline_and_parallel_runtime_actor(self):
        self.player.in_game = False
        self.player.save(update_fields=["in_game"])
        event_data = {
            "actor": {"key": self.player.key},
            "destination_room": {"id": self.room.id},
            "runtime_world_id": self.spawn_world.id,
            "location_sequence": self.player.location_sequence,
        }
        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
        ) as reaction_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
        ) as aggro_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )
        reaction_mock.assert_not_called()
        aggro_mock.assert_not_called()

        parallel_runtime = self.world.create_spawn_world(
            instance_ref="parallel-transfer-enter",
        )
        self.player.world = parallel_runtime
        self.player.in_game = True
        self.player.save(update_fields=["world", "in_game"])
        with patch(
            "spawns.trigger_subscriptions.execute_mob_event_triggers",
        ) as reaction_mock, patch(
            "spawns.actions.combat.ScanRoomAggroAction.execute",
        ) as aggro_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type="notification./transfer.enter",
                event_data=event_data,
            )
        reaction_mock.assert_not_called()
        aggro_mock.assert_not_called()
