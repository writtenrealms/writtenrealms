from django.urls import reverse

from quests.models import QuestInstance, QuestTemplate
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


def _runtime_rewards():
    return {
        "complete": [],
        "compromised": [],
        "failed_forward": [],
        "expired": [],
    }


class QuestRuntimeTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.stamina = 20
        self.player.in_game = True
        self.player.save(update_fields=["stamina", "in_game"])

    def create_runtime_quest(self, *, slug: str, name: str, quest_type: str = "quest", discovery_policy=None, steps=None):
        return QuestTemplate.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            quest_type=quest_type,
            scope="player",
            status="active",
            repeatability_mode="never",
            repeatability_cooldown_seconds=0,
            max_active=1,
            discovery_policy=discovery_policy or {
                "sources": [],
                "visible_if": {},
                "accept_if": {},
                "salience": 0,
                "cooldown_seconds": 0,
            },
            slot_schema={},
            graph={"steps": steps or []},
            reward_policy=_runtime_rewards(),
        )

    def _message_types(self, messages):
        return [msg["message"].get("type") for msg in messages]

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None


class TestMinimalQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.create_runtime_quest(
            slug="tiny_hello",
            name="Tiny Hello",
            quest_type="questlet",
            discovery_policy={
                "sources": [{"type": "auto_start"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 1,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "You notice a strange scrap of paper.",
                    "lead": "Read the note and move on.",
                    "stakes": "",
                    "text": {"body": "A minimal authored quest beat."},
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The note tells you nothing useful, but the system works.",
                    "lead": "",
                    "stakes": "",
                },
            ],
        )

    def test_look_auto_starts_minimal_questlet(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        self.assertIn("cmd.look.success", self._message_types(messages))
        self.assertIn("quest.instance.started", self._message_types(messages))

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "active")
        self.assertEqual(quest_instance.current_step_id, "offer")

    def test_quest_recap_and_choice_complete_minimal_questlet(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages() as recap_messages:
            dispatch_text_command(self.player.id, "quest recap")

        recap_message = self._message_by_type(recap_messages, "cmd.quest.success")
        self.assertIsNotNone(recap_message)
        self.assertIn("Tiny Hello", recap_message["text"])
        self.assertIn("continue", recap_message["text"])

        with capture_game_messages() as choice_messages:
            dispatch_text_command(self.player.id, "quest choose tiny_hello continue")

        self.assertIn("quest.instance.resolved", self._message_types(choice_messages))
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")


class TestObjectiveQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.room_two = self.room.create_at("east")
        self.room_three = self.room_two.create_at("east")
        self.create_runtime_quest(
            slug="shrine_survey",
            name="Shrine Survey",
            discovery_policy={
                "sources": [{"type": "room_prompt", "room": f"room.{self.room.id}"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 20,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "A weathered placard asks you to survey the shrines ahead.",
                    "lead": "Decide whether to take the survey.",
                    "stakes": "The route will stay dangerous if nobody checks it.",
                    "choices": [
                        {"id": "begin", "text": "Take the survey.", "goto": "survey"},
                    ],
                },
                {
                    "id": "survey",
                    "kind": "objective",
                    "recap": "You accepted the survey route.",
                    "lead": "Look around at both shrines to confirm they are intact.",
                    "stakes": "If either shrine has collapsed, travelers need warning.",
                    "objectives": [
                        {
                            "id": "inspect_shrines",
                            "text": "Inspect both shrines.",
                            "tracker": {
                                "event": "cmd.look.success",
                                "where": {
                                    "all": [
                                        {"eq": ["event.target_type", "room"]},
                                        {"in": ["event.target.id", [self.room_two.id, self.room_three.id]]},
                                    ]
                                },
                            },
                            "progress": {
                                "mode": "unique_count",
                                "target": 2,
                                "distinct_by": "event.target.id",
                            },
                        }
                    ],
                    "transitions": [
                        {
                            "when": {"objective_complete": "inspect_shrines"},
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "You surveyed both shrines and the route is safe enough to report.",
                    "lead": "",
                    "stakes": "",
                },
            ],
        )

    def test_room_prompt_accepts_and_progresses_via_move_and_look(self):
        with capture_game_messages() as discovery_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertIn("quest.opportunity.available", self._message_types(discovery_messages))
        self.assertFalse(QuestInstance.objects.filter(player=self.player, template__slug="shrine_survey").exists())

        with capture_game_messages() as accept_messages:
            dispatch_text_command(self.player.id, "quest accept shrine_survey")

        self.assertIn("quest.instance.started", self._message_types(accept_messages))

        with capture_game_messages() as begin_messages:
            dispatch_text_command(self.player.id, "quest choose shrine_survey begin")

        self.assertIn("quest.instance.updated", self._message_types(begin_messages))

        with capture_game_messages():
            dispatch_text_command(self.player.id, "east")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "east")
        with capture_game_messages() as final_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertIn("quest.instance.resolved", self._message_types(final_messages))
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="shrine_survey")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")


class TestQuestRuntimeEndpoints(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.quest = self.create_runtime_quest(
            slug="campfire_note",
            name="Campfire Note",
            quest_type="questlet",
            discovery_policy={
                "sources": [{"type": "room_prompt", "room": f"room.{self.room.id}"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "A note lies beside the fire.",
                    "lead": "Read it or leave it alone.",
                    "stakes": "",
                    "choices": [
                        {"id": "read", "text": "Read the note.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The note is brief, but useful.",
                    "lead": "",
                    "stakes": "",
                },
            ],
        )

    def test_runtime_endpoints_cover_opportunity_accept_choose_and_recap(self):
        headers = {"HTTP_X_PLAYER_ID": str(self.player.id)}

        opportunities_resp = self.client.get(
            reverse("game-quest-opportunity-list"),
            **headers,
        )
        self.assertEqual(opportunities_resp.status_code, 200)
        self.assertEqual(opportunities_resp.data["opportunities"][0]["slug"], "campfire_note")

        accept_resp = self.client.post(
            reverse("game-quest-opportunity-accept", args=["campfire_note"]),
            {},
            format="json",
            **headers,
        )
        self.assertEqual(accept_resp.status_code, 201)
        instance_id = accept_resp.data["quest"]["id"]

        choose_resp = self.client.post(
            reverse("game-quest-instance-choose", args=[instance_id]),
            {"choice_id": "read"},
            format="json",
            **headers,
        )
        self.assertEqual(choose_resp.status_code, 200)
        self.assertEqual(choose_resp.data["quest"]["resolution"], "complete")

        recap_resp = self.client.get(
            reverse("game-quest-instance-recap", args=[instance_id]),
            **headers,
        )
        self.assertEqual(recap_resp.status_code, 200)
        self.assertIn("Campfire Note", recap_resp.data["text"])
