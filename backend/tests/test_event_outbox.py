import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.utils import timezone

from config import constants as adv_consts
from quests.subscriptions import dispatch_quest_subscriptions_for_event
from spawns.actions.combat import resolve_due_character_effects
from spawns.events import GameEvent, enqueue_game_events, flush_game_event_outbox
from spawns.models import EventSubscriptionReceipt, GameEventOutbox
from spawns.trigger_subscriptions import dispatch_trigger_subscriptions_for_event
from tests.base import WorldTestCase
from tests.utils import create_active_effect


class TestGameEventOutbox(WorldTestCase):
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

    def test_death_trigger_subscriber_processes_retried_event_once(self):
        event_id = str(uuid.uuid4())
        event_data = {
            "_event_id": event_id,
            "actor": {"key": self.player.key},
            "room": {"id": self.room.id},
        }

        with patch(
            "spawns.trigger_subscriptions.execute_room_event_triggers"
        ) as execute_mock:
            dispatch_trigger_subscriptions_for_event(
                event_type="affect.death",
                event_data=event_data,
            )
            dispatch_trigger_subscriptions_for_event(
                event_type="affect.death",
                event_data=event_data,
            )

        execute_mock.assert_called_once()
        self.assertEqual(
            EventSubscriptionReceipt.objects.filter(
                event_id=event_id,
                subscriber="triggers",
            ).count(),
            1,
        )
