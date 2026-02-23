from unittest.mock import patch

from django.test import override_settings

from rest_framework.reverse import reverse

from spawns.events import GameEvent, publish_events
from tests.base import WorldTestCase


class TestAIIntentIngress(WorldTestCase):
    @override_settings(WR_CORE_AI_INGRESS_TOKEN="dev-secret-token")
    @patch("spawns.views.dispatch_command")
    def test_accepts_valid_intent_and_dispatches_as_mob(self, mock_dispatch):
        mob = self.create_mob("Archivist")
        payload = {
            "intent_id": "intent-1",
            "world_key": self.world.key,
            "room_key": self.room.key,
            "mob_key": mob.key,
            "intent_type": "say",
            "text": "Welcome to the archive.",
            "source_event_id": "evt-1",
            "metadata": {"provider": "openai", "dry_run": True},
        }

        resp = self.client.post(
            reverse("internal-ai-intents"),
            payload,
            format="json",
            HTTP_AUTHORIZATION="Bearer dev-secret-token",
        )

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["status"], "accepted")
        self.assertEqual(resp.data["mob_key"], mob.key)
        self.assertEqual(resp.data["command"], "say Welcome to the archive.")
        mock_dispatch.assert_called_once_with(
            command_type="text",
            actor_type="mob",
            actor_id=mob.id,
            payload={
                "text": "say Welcome to the archive.",
                "__ai_intent_source": True,
            },
        )

    @override_settings(WR_CORE_AI_INGRESS_TOKEN="dev-secret-token")
    def test_rejects_missing_bearer_token(self):
        mob = self.create_mob("Archivist")
        payload = {
            "intent_id": "intent-1",
            "world_key": self.world.key,
            "mob_key": mob.key,
            "intent_type": "say",
            "text": "Welcome.",
            "source_event_id": "evt-1",
        }

        resp = self.client.post(
            reverse("internal-ai-intents"),
            payload,
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    @override_settings(WR_CORE_AI_INGRESS_TOKEN="dev-secret-token")
    def test_rejects_mismatched_world_key(self):
        mob = self.create_mob("Archivist")
        payload = {
            "intent_id": "intent-1",
            "world_key": "world.999999",
            "mob_key": mob.key,
            "intent_type": "say",
            "text": "Welcome.",
            "source_event_id": "evt-1",
        }

        resp = self.client.post(
            reverse("internal-ai-intents"),
            payload,
            format="json",
            HTTP_AUTHORIZATION="Bearer dev-secret-token",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("world_key", str(resp.data).lower())


class TestAIEventForwarding(WorldTestCase):
    @override_settings(
        WR_AI_EVENT_FORWARD_URL="http://localhost:8071/v1/events",
        WR_AI_EVENT_TYPES="cmd.say.success,cmd.move.success",
    )
    @patch("spawns.tasks.forward_event_to_ai_sidecar.delay")
    @patch("spawns.events.publish_to_player")
    def test_publish_events_enqueues_forward_for_player_say(
        self,
        _mock_publish,
        mock_forward_delay,
    ):
        event = GameEvent(
            type="cmd.say.success",
            recipients=[self.player.key],
            data={
                "actor": {"key": self.player.key, "name": self.player.name},
                "text": "hello archive",
            },
            text="say event",
        )

        publish_events([event], actor_key=self.player.key)

        mock_forward_delay.assert_called_once()
        kwargs = mock_forward_delay.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "cmd.say.success")
        self.assertEqual(kwargs["actor_key"], self.player.key)
        self.assertEqual(kwargs["event_data"]["text"], "hello archive")

    @override_settings(
        WR_AI_EVENT_FORWARD_URL="http://localhost:8071/v1/events",
        WR_AI_EVENT_TYPES="cmd.say.success,cmd.move.success",
    )
    @patch("spawns.tasks.forward_event_to_ai_sidecar.delay")
    @patch("spawns.events.publish_to_player")
    def test_publish_events_skips_non_player_actor(
        self,
        _mock_publish,
        mock_forward_delay,
    ):
        mob = self.create_mob("Sage")
        event = GameEvent(
            type="cmd.say.success",
            recipients=[mob.key],
            data={
                "actor": {"key": mob.key, "name": mob.name},
                "text": "hello",
            },
            text="say event",
        )

        publish_events([event], actor_key=mob.key)

        mock_forward_delay.assert_not_called()
