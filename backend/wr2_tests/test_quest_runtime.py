from datetime import timedelta

from django.utils import timezone
from django.urls import reverse

from builders.models import ItemTemplate, MobTemplate
from config import constants as adv_consts
from core.computations import compute_stats
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_WORLD,
    get_state_snapshot,
    replace_state_snapshot,
)
from spawns.handlers import dispatch_command
from spawns.models import Item
from quests.models import QuestInstance, QuestTemplate
from quests.services.discovery import list_opportunities
from tests.base import WorldTestCase
from worlds.models import Room
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

    def create_runtime_quest(
        self,
        *,
        slug: str,
        name: str,
        quest_type: str = "quest",
        repeatability_mode: str = "never",
        repeatability_cooldown_seconds: int = 0,
        max_active: int = 1,
        discovery_policy=None,
        steps=None,
        reward_policy=None,
    ):
        return QuestTemplate.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            quest_type=quest_type,
            scope="player",
            status="active",
            repeatability_mode=repeatability_mode,
            repeatability_cooldown_seconds=repeatability_cooldown_seconds,
            max_active=max_active,
            discovery_policy=discovery_policy or {
                "sources": [],
                "visible_if": {},
                "accept_if": {},
                "salience": 0,
                "cooldown_seconds": 0,
            },
            slot_schema={},
            graph={"steps": steps or []},
            reward_policy=reward_policy or _runtime_rewards(),
        )

    def _message_types(self, messages):
        return [msg["message"].get("type") for msg in messages]

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _room_char_by_name(self, message, name):
        if not message:
            return None
        target = message.get("data", {}).get("target") or message.get("data", {}).get("room") or {}
        for char in target.get("chars", []):
            if char.get("name") == name:
                return char
        return None

    def _room_callouts(self, message):
        if not message:
            return []
        target = message.get("data", {}).get("target") or message.get("data", {}).get("room") or {}
        return target.get("quest_callouts", [])

    def create_completed_quest_instance(self, quest: QuestTemplate | str, *, resolution: str = "complete"):
        template = quest
        if isinstance(quest, str):
            template = QuestTemplate.objects.get(world=self.world, slug=quest)
        return QuestInstance.objects.create(
            world=template.world,
            template=template,
            player=self.player,
            status="resolved",
            resolution=resolution,
            current_step_id="resolved",
            slot_bindings={},
            local_state={},
            visible_objective_ids=[],
            resolved_at=timezone.now(),
        )


class TestMinimalQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.create_runtime_quest(
            slug="tiny_hello",
            name="Tiny Hello",
            quest_type="quest",
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
                    "text": {"body": "A minimal authored quest beat."},
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The note tells you nothing useful, but the system works.",
                },
            ],
        )

    def test_look_auto_starts_minimal_quest(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        self.assertIn("cmd.look.success", self._message_types(messages))
        self.assertIn("quest.instance.started", self._message_types(messages))

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "active")
        self.assertEqual(quest_instance.current_step_id, "offer")

    def test_state_sync_auto_starts_minimal_quest(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        self.assertIn("cmd.state.sync.success", self._message_types(messages))
        self.assertIn("quest.instance.started", self._message_types(messages))

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "active")
        self.assertEqual(quest_instance.current_step_id, "offer")

    def test_say_does_not_trigger_auto_start(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "say hello")

        self.assertIn("cmd.say.success", self._message_types(messages))
        self.assertNotIn("quest.instance.started", self._message_types(messages))
        self.assertFalse(QuestInstance.objects.filter(player=self.player, template__slug="tiny_hello").exists())

    def test_listing_active_quests_does_not_trigger_auto_start(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest list")

        self.assertIn("cmd.quest.success", self._message_types(messages))
        self.assertNotIn("quest.instance.started", self._message_types(messages))
        self.assertFalse(QuestInstance.objects.filter(player=self.player, template__slug="tiny_hello").exists())

    def test_quest_defaults_to_list_and_choice_complete_minimal_quest(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages() as info_messages:
            dispatch_text_command(self.player.id, "quest")

        info_message = self._message_by_type(info_messages, "cmd.quest.success")
        self.assertIsNotNone(info_message)
        self.assertEqual(info_message["data"]["subcommand"], "list")
        self.assertEqual(len(info_message["data"]["quests"]), 1)
        self.assertEqual(info_message["data"]["quests"][0]["template"]["slug"], "tiny_hello")
        self.assertNotIn("quest", info_message["data"])
        self.assertIn("Active quests:", info_message["text"])
        self.assertIn("Tiny Hello", info_message["text"])
        self.assertIn("tiny_hello", info_message["text"])

        with capture_game_messages() as choice_messages:
            dispatch_text_command(self.player.id, "quest choose tiny_hello continue")

        self.assertIn("quest.instance.resolved", self._message_types(choice_messages))
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")

    def test_quest_abandon_resolves_active_quest_without_arc(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages() as abandon_messages:
            dispatch_text_command(self.player.id, "quest abandon tiny_hello")

        self.assertIn("quest.instance.resolved", self._message_types(abandon_messages))

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="tiny_hello")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "abandoned")

    def test_quest_i_prefix_resolves_to_info_with_slug(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages() as info_messages:
            dispatch_text_command(self.player.id, "quest i tiny_hello")

        info_message = self._message_by_type(info_messages, "cmd.quest.success")
        self.assertIsNotNone(info_message)
        self.assertEqual(info_message["data"]["subcommand"], "info")
        self.assertIn("Tiny Hello", info_message["text"])

    def test_quest_info_slug_returns_structured_quest_payload(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages() as info_messages:
            dispatch_text_command(self.player.id, "quest info tiny_hello")

        info_message = self._message_by_type(info_messages, "cmd.quest.success")
        self.assertIsNotNone(info_message)
        self.assertEqual(info_message["data"]["subcommand"], "info")
        self.assertEqual(info_message["data"]["quest"]["template"]["slug"], "tiny_hello")
        self.assertEqual(info_message["data"]["quest"]["current_step"]["id"], "offer")
        self.assertEqual(info_message["data"]["quest"]["current_step"]["choices"][0]["id"], "continue")

    def test_quest_info_requires_slug(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest info")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "usage")
        self.assertEqual(error_message["text"], "Usage: quest info <slug-or-id>")

    def test_quest_recap_subcommand_is_no_longer_supported(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest recap")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "unknown_subcommand")
        self.assertIn("recap", error_message["text"])

    def test_quest_a_prefix_is_rejected_as_ambiguous(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest a")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "ambiguous_subcommand")
        self.assertIn("accept", error_message["text"])
        self.assertIn("abandon", error_message["text"])


class TestObjectiveQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.room_two = self.room.create_at("east")
        self.room_three = self.room_two.create_at("east")
        self.create_runtime_quest(
            slug="shrine_survey",
            name="Shrine Survey",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A weathered placard asks for a shrine survey.",
                    }
                ],
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
                    "choices": [
                        {"id": "begin", "text": "Take the survey.", "goto": "survey"},
                    ],
                },
                {
                    "id": "survey",
                    "kind": "objective",
                    "recap": "You accepted the survey route.",
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
                },
            ],
        )

    def test_room_prompt_accepts_and_progresses_via_move_and_look(self):
        with capture_game_messages() as discovery_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertNotIn("quest.opportunity.available", self._message_types(discovery_messages))
        look_message = self._message_by_type(discovery_messages, "cmd.look.success")
        self.assertIsNotNone(look_message)
        self.assertIn("A weathered placard asks for a shrine survey.", look_message.get("text", ""))
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

    def test_objective_progress_update_includes_updated_objective_payload(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept shrine_survey")

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest choose shrine_survey begin")

        with capture_game_messages():
            dispatch_text_command(self.player.id, "east")

        with capture_game_messages() as progress_messages:
            dispatch_text_command(self.player.id, "look")

        update_message = self._message_by_type(progress_messages, "quest.instance.updated")
        self.assertIsNotNone(update_message)
        self.assertEqual(update_message["data"]["quest"]["template"]["slug"], "shrine_survey")
        self.assertEqual(update_message["data"]["updated_objective"]["id"], "inspect_shrines")
        self.assertEqual(update_message["data"]["updated_objective"]["text"], "Inspect both shrines.")
        self.assertEqual(update_message["data"]["updated_objective"]["progress_current"], 1)
        self.assertEqual(update_message["data"]["updated_objective"]["progress_target"], 2)
        self.assertEqual(update_message["data"]["updated_objective"]["progress"], "1/2")
        self.assertEqual(update_message["data"]["updated_objective"]["status"], "active")

    def test_quest_opp_prefix_is_rejected_as_unknown(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest opp")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "unknown_subcommand")
        self.assertIn("opp", error_message["text"])

    def test_abandoned_non_repeatable_quest_can_be_reaccepted(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept shrine_survey")

        with capture_game_messages() as abandon_messages:
            dispatch_text_command(self.player.id, "quest abandon shrine_survey")

        self.assertIn("quest.instance.resolved", self._message_types(abandon_messages))

        abandoned_instance = QuestInstance.objects.get(
            player=self.player,
            template__slug="shrine_survey",
            status="resolved",
        )
        self.assertEqual(abandoned_instance.resolution, "abandoned")

        with capture_game_messages() as resolved_messages:
            dispatch_text_command(self.player.id, "quest resolved")

        resolved_message = self._message_by_type(resolved_messages, "cmd.quest.success")
        self.assertIsNotNone(resolved_message)
        self.assertEqual(resolved_message["data"]["subcommand"], "resolved")
        self.assertNotIn("shrine_survey", resolved_message["text"])

        self.assertIn(
            "shrine_survey",
            [opportunity["slug"] for opportunity in list_opportunities(self.player, refresh=True)],
        )

        with capture_game_messages() as accept_again_messages:
            dispatch_text_command(self.player.id, "quest accept shrine_survey")

        self.assertIn("quest.instance.started", self._message_types(accept_again_messages))
        self.assertEqual(
            QuestInstance.objects.filter(player=self.player, template__slug="shrine_survey").count(),
            2,
        )
        self.assertEqual(
            QuestInstance.objects.filter(
                player=self.player,
                template__slug="shrine_survey",
                status="active",
            ).count(),
            1,
        )

    def test_quest_completed_subcommand_is_no_longer_supported(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest completed")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "unknown_subcommand")
        self.assertIn("completed", error_message["text"])


class TestRoomPromptCalloutRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.create_runtime_quest(
            slug="coat_return",
            name="Coat Return",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A forgotten coat hangs over the back of a chair.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "A coat was left behind here.",
                    "text": {
                        "body": "A note stitched inside reads: Property of T.J. Cooper.",
                    },
                    "choices": [
                        {"id": "begin", "text": "Take responsibility for it.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "You commit to finding the coat's owner.",
                },
            ],
        )

    def test_look_shows_room_prompt_callout_and_skips_available_event(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        self.assertNotIn("quest.opportunity.available", self._message_types(messages))
        look_message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(look_message)

        callouts = self._room_callouts(look_message)
        self.assertEqual(len(callouts), 1)
        self.assertEqual(
            callouts[0]["text"],
            "A forgotten coat hangs over the back of a chair.",
        )
        self.assertEqual(callouts[0]["indicator"], "!")
        self.assertEqual(callouts[0]["command"], "inspect")
        self.assertIn("[ ! ]", look_message.get("text", ""))

    def test_inspect_presents_room_prompt_opportunity(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "inspect")

        inspect_message = self._message_by_type(messages, "cmd.inspect.success")
        self.assertIsNotNone(inspect_message)
        guidance_message = self._message_by_type(messages, "quest.opportunity.presented")
        self.assertIsNotNone(guidance_message)
        self.assertIn("Coat Return", guidance_message["text"])
        self.assertIn("Property of T.J. Cooper", guidance_message["text"])
        self.assertIn("quest accept coat_return", guidance_message["text"])


class TestPortableRoomRefsQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.room_two = self.room.create_at("east")
        self.room_three = self.room_two.create_at("east")
        self.create_runtime_quest(
            slug="portable_shrine_survey",
            name="Portable Shrine Survey",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room@{self.room.x},{self.room.y},{self.room.z}",
                        "callout": "A survey notice hangs here.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 20,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "survey",
                    "kind": "objective",
                    "recap": "Inspect both shrines.",
                    "objectives": [
                        {
                            "id": "inspect_shrines",
                            "text": "Inspect both shrines.",
                            "tracker": {
                                "event": "cmd.look.success",
                                "where": {
                                    "all": [
                                        {"eq": ["event.target_type", "room"]},
                                        {
                                            "in": [
                                                "event.target.id",
                                                [
                                                    f"room@{self.room_two.x},{self.room_two.y},{self.room_two.z}",
                                                    f"room@{self.room_three.x},{self.room_three.y},{self.room_three.z}",
                                                ],
                                            ]
                                        },
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
                    "recap": "Survey complete.",
                },
            ],
        )

    def test_room_prompt_and_room_objectives_accept_coordinate_refs(self):
        with capture_game_messages() as discovery_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertNotIn("quest.opportunity.available", self._message_types(discovery_messages))
        look_message = self._message_by_type(discovery_messages, "cmd.look.success")
        self.assertIsNotNone(look_message)
        self.assertIn("A survey notice hangs here.", look_message.get("text", ""))

        with capture_game_messages() as accept_messages:
            dispatch_text_command(self.player.id, "quest accept portable_shrine_survey")

        self.assertIn("quest.instance.started", self._message_types(accept_messages))

        with capture_game_messages():
            dispatch_text_command(self.player.id, "east")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "east")
        with capture_game_messages() as final_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertIn("quest.instance.resolved", self._message_types(final_messages))
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="portable_shrine_survey")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")


class TestGrantedItemQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.survey_token_template = ItemTemplate.objects.create(
            world=self.world,
            name="Survey Token",
            keywords="survey token token",
        )
        self.satchel_template = ItemTemplate.objects.create(
            world=self.world,
            name="Satchel",
            keywords="satchel",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        self.coin_template = ItemTemplate.objects.create(
            world=self.world,
            name="Coin",
            keywords="coin",
        )
        self.guide_template = MobTemplate.objects.create(
            world=self.world,
            name="Trail Guide",
            keywords="trail guide guide",
        )
        self.guide_template.spawn(self.room, self.spawn_world)

        self.create_runtime_quest(
            slug="survey_route",
            name="Survey Route",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A survey route placard hangs here.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 15,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "A survey token is issued for the route ahead.",
                    "effects": [
                        {"type": "grant_item", "item_template": self.survey_token_template.slug},
                    ],
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
            reward_policy={
                "complete": [
                    {"type": "grant_item", "item_template": self.coin_template.slug},
                ],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )
        self.create_runtime_quest(
            slug="guide_assignment",
            name="Guide Assignment",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": self.guide_template.slug}],
                "visible_if": {},
                "accept_if": {},
                "salience": 15,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "The trail guide presses a spare survey token into your hand.",
                    "effects": [
                        {"type": "grant_item", "item_template": self.survey_token_template.slug},
                    ],
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )

    def test_room_prompt_accept_grants_item_on_start_step(self):
        with capture_game_messages() as accept_messages:
            dispatch_text_command(self.player.id, "quest accept survey_route")

        self.assertEqual(
            self.player.inventory.filter(template=self.survey_token_template).count(),
            1,
        )
        start_message = self._message_by_type(accept_messages, "quest.instance.started")
        self.assertIsNotNone(start_message)
        self.assertIn("Survey Token", start_message["text"])

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="survey_route")
        self.assertTrue(quest_instance.local_state.get("granted_item_ids"))

    def test_npc_dialogue_accept_grants_item_on_start_step(self):
        with capture_game_messages() as accept_messages:
            dispatch_text_command(self.player.id, "quest accept guide_assignment")

        self.assertEqual(
            self.player.inventory.filter(template=self.survey_token_template).count(),
            1,
        )
        start_message = self._message_by_type(accept_messages, "quest.instance.started")
        self.assertIsNotNone(start_message)
        self.assertIn("Survey Token", start_message["text"])

    def test_abandon_removes_granted_item_from_nested_player_bag(self):
        satchel = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=self.satchel_template,
            name=self.satchel_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        coin = Item.objects.create(
            world=self.spawn_world,
            container=satchel,
            template=self.coin_template,
            name=self.coin_template.name,
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept survey_route")

        granted_item = self.player.inventory.get(template=self.survey_token_template)

        with capture_game_messages():
            dispatch_text_command(self.player.id, "put token satchel")

        granted_item.refresh_from_db()
        self.assertEqual(granted_item.container_id, satchel.id)

        with capture_game_messages() as abandon_messages:
            dispatch_text_command(self.player.id, "quest abandon survey_route")

        self.assertFalse(Item.objects.filter(pk=granted_item.id).exists())
        satchel.refresh_from_db()
        coin.refresh_from_db()
        self.assertEqual(coin.container_id, satchel.id)
        self.assertIn("quest.instance.resolved", self._message_types(abandon_messages))
        resolved_message = self._message_by_type(abandon_messages, "quest.instance.resolved")
        self.assertIsNotNone(resolved_message)
        self.assertIn("Removed quest item", resolved_message["text"])

    def test_completion_removes_granted_item_and_keeps_completion_reward_item(self):
        satchel = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=self.satchel_template,
            name=self.satchel_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept survey_route")

        granted_item = self.player.inventory.get(template=self.survey_token_template)

        with capture_game_messages():
            dispatch_text_command(self.player.id, "put token satchel")

        with capture_game_messages() as resolve_messages:
            dispatch_text_command(self.player.id, "quest choose survey_route continue")

        self.assertFalse(Item.objects.filter(pk=granted_item.id).exists())
        self.assertEqual(
            self.player.inventory.filter(template=self.coin_template).count(),
            1,
        )
        resolved_message = self._message_by_type(resolve_messages, "quest.instance.resolved")
        self.assertIsNotNone(resolved_message)
        self.assertIn("Coin", resolved_message["text"])
        self.assertIn("Removed quest item", resolved_message["text"])


class TestQuestRoomItemsRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.back_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Back Room",
            x=self.room.x + 1,
            y=self.room.y,
            z=self.room.z,
        )
        self.room.east = self.back_room
        self.room.save(update_fields=["east"])
        self.back_room.west = self.room
        self.back_room.save(update_fields=["west"])

        self.keg_template = ItemTemplate.objects.create(
            world=self.world,
            name="Saloon Keg",
            slug="saloon_keg",
            type=adv_consts.ITEM_TYPE_QUEST,
            description="A stout wooden keg stamped with the saloon's brand.",
            keywords="saloon keg keg",
        )
        self.satchel_template = ItemTemplate.objects.create(
            world=self.world,
            name="Satchel",
            keywords="satchel",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        self.chest_template = ItemTemplate.objects.create(
            world=self.world,
            name="Chest",
            keywords="chest",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        self.create_runtime_quest(
            slug="saloon_keg_run",
            name="A Keg for the Bar",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "Gus looks like he needs a hand behind the bar.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 15,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "fetch_keg",
                    "kind": "objective",
                    "recap": "Fetch a keg from the back room.",
                    "room_items": [
                        {
                            "id": "saloon_keg",
                            "room": f"room.{self.back_room.id}",
                            "item_template": self.keg_template.slug,
                            "ground_description": "A full saloon keg rests here.",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )

    def test_active_room_item_appears_in_room_and_can_be_claimed_with_get(self):
        watcher = self.create_player("Watcher", user=self.create_user("watcher@example.com"), room=self.back_room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept saloon_keg_run")

        with capture_game_messages() as move_messages:
            dispatch_text_command(self.player.id, "east")

        move_message = self._message_by_type(move_messages, "cmd.move.success")
        self.assertIsNotNone(move_message)
        room_inventory = move_message["data"]["room"]["inventory"]
        keg_entry = next((entry for entry in room_inventory if entry["name"] == "Saloon Keg"), None)
        self.assertIsNotNone(keg_entry)
        self.assertEqual(keg_entry["indicator"], "*")
        self.assertIn("[ * ]", move_message["text"])

        with capture_game_messages() as get_messages:
            dispatch_text_command(self.player.id, "get keg")

        self.assertEqual(
            self.player.inventory.filter(template=self.keg_template).count(),
            1,
        )
        get_message = self._message_by_type(get_messages, "cmd.get.success")
        self.assertIsNotNone(get_message)
        self.assertIn("Saloon Keg", get_message["text"])
        self.assertFalse(
            any(
                msg["message"].get("type") == "notification.cmd.get.success"
                for msg in get_messages
            )
        )
        self.assertFalse(
            any(entry["name"] == "Saloon Keg" for entry in get_message["data"]["room"]["inventory"])
        )

        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="saloon_keg_run")
        self.assertIn("granted_item_ids", quest_instance.local_state)
        self.assertIn("room_item_claims", quest_instance.local_state)

    def test_look_can_target_visible_quest_room_item(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept saloon_keg_run")
            dispatch_text_command(self.player.id, "east")

        with capture_game_messages() as look_messages:
            dispatch_text_command(self.player.id, "look keg")

        look_message = self._message_by_type(look_messages, "cmd.look.success")
        self.assertIsNotNone(look_message)
        self.assertEqual(look_message["data"]["target_type"], "item")
        self.assertEqual(look_message["data"]["target"]["type"], adv_consts.ITEM_TYPE_QUEST)
        self.assertEqual(look_message["data"]["target"]["indicator"], "*")
        self.assertIn("stout wooden keg", look_message["text"].lower())

    def test_bound_quest_item_cannot_be_dropped_or_put_in_room_container(self):
        chest = Item.objects.create(
            world=self.spawn_world,
            container=self.back_room,
            template=self.chest_template,
            name=self.chest_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
            is_pickable=False,
        )
        satchel = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=self.satchel_template,
            name=self.satchel_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept saloon_keg_run")
            dispatch_text_command(self.player.id, "east")
            dispatch_text_command(self.player.id, "get keg")

        with capture_game_messages() as drop_messages:
            dispatch_text_command(self.player.id, "drop keg")

        drop_message = self._message_by_type(drop_messages, "cmd.drop.error")
        self.assertIsNotNone(drop_message)
        self.assertIn("Quest items stay with you", drop_message["text"])

        with capture_game_messages() as put_room_messages:
            dispatch_text_command(self.player.id, "put keg chest")

        put_room_message = self._message_by_type(put_room_messages, "cmd.put.error")
        self.assertIsNotNone(put_room_message)
        self.assertIn("Quest items can only be carried or turned in", put_room_message["text"])

        with capture_game_messages() as put_bag_messages:
            dispatch_text_command(self.player.id, "put keg satchel")

        put_bag_message = self._message_by_type(put_bag_messages, "cmd.put.success")
        self.assertIsNotNone(put_bag_message)
        keg_item = Item.objects.get(template=self.keg_template)
        self.assertEqual(keg_item.container_id, satchel.id)


class TestQuestRepeatabilityRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.create_runtime_quest(
            slug="cooldown_trial",
            name="Cooldown Trial",
            repeatability_mode="cooldown",
            repeatability_cooldown_seconds=60,
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A trial marker stands ready here.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 15,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "You can run the trial again after a short delay.",
                    "choices": [
                        {"id": "finish", "text": "Finish the trial.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The trial is complete.",
                },
            ],
        )

    def test_completed_cooldown_quest_is_hidden_until_cooldown_expires(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept cooldown_trial")

        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest choose cooldown_trial finish")

        self.assertNotIn(
            "cooldown_trial",
            [opportunity["slug"] for opportunity in list_opportunities(self.player, refresh=True)],
        )

        quest_instance = QuestInstance.objects.get(
            player=self.player,
            template__slug="cooldown_trial",
            status="resolved",
        )
        quest_instance.resolved_at = timezone.now() - timedelta(seconds=61)
        quest_instance.save(update_fields=["resolved_at", "modified_ts"])

        self.assertIn(
            "cooldown_trial",
            [opportunity["slug"] for opportunity in list_opportunities(self.player, refresh=True)],
        )


class TestQuestRuntimeEndpoints(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.quest = self.create_runtime_quest(
            slug="campfire_note",
            name="Campfire Note",
            quest_type="quest",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A note lies beside the fire.",
                    }
                ],
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
                    "choices": [
                        {"id": "read", "text": "Read the note.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The note is brief, but useful.",
                },
            ],
        )

    def test_runtime_endpoints_cover_accept_choose_and_info(self):
        headers = {"HTTP_X_PLAYER_ID": str(self.player.id)}

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

        info_resp = self.client.get(
            reverse("game-quest-instance-info", args=[instance_id]),
            **headers,
        )
        self.assertEqual(info_resp.status_code, 200)
        self.assertIn("Campfire Note", info_resp.data["text"])


class TestNpcDialogueSlugDiscovery(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.quartermaster_template = MobTemplate.objects.create(
            world=self.world,
            name="Quartermaster",
            keywords="quartermaster",
        )
        self.quartermaster_template.spawn(self.room, self.spawn_world)
        self.create_runtime_quest(
            slug="quartermaster_request",
            name="Quartermaster Request",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": self.quartermaster_template.slug}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "The quartermaster has work for you.",
                    "choices": [
                        {"id": "accept", "text": "Listen.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "You heard the quartermaster out.",
                },
            ],
        )

    def test_npc_dialogue_discovery_accepts_mob_template_slug_without_room_entry_spam(self):
        with capture_game_messages() as discovery_messages:
            dispatch_text_command(self.player.id, "look")

        self.assertNotIn("quest.opportunity.available", self._message_types(discovery_messages))
        look_message = self._message_by_type(discovery_messages, "cmd.look.success")
        quartermaster = self._room_char_by_name(look_message, "Quartermaster")
        self.assertIsNotNone(quartermaster)
        self.assertTrue(quartermaster["quest_data"]["enquire"])


class TestTurnInQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.quartermaster_template = MobTemplate.objects.create(
            world=self.world,
            name="Quartermaster",
            keywords="quartermaster",
        )
        self.quartermaster_template.spawn(self.room, self.spawn_world)
        self.pelt_template = ItemTemplate.objects.create(world=self.world, name="Wolf Pelt")
        self.herb_template = ItemTemplate.objects.create(world=self.world, name="Moonleaf")
        self.pelt_template.spawn(self.player, self.spawn_world)
        self.pelt_template.spawn(self.player, self.spawn_world)
        self.herb_template.spawn(self.player, self.spawn_world)
        self.gold_before = self.player.gold
        self.exp_before = self.player.experience

        self.create_runtime_quest(
            slug="quartermaster_supplies",
            name="Quartermaster Supplies",
            discovery_policy={
                "sources": [{"type": "auto_start"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "turn_in",
                    "kind": "objective",
                    "recap": "The quartermaster needs pelts and moonleaf.",
                    "objectives": [
                        {
                            "id": "deliver_pelts",
                            "text": "Deliver 2 wolf pelts.",
                            "tracker": {
                                "event": "quest.item.delivered",
                                "where": {
                                    "all": [
                                        {"eq": ["event.target.template_id", self.quartermaster_template.slug]},
                                        {"eq": ["event.item.template_id", self.pelt_template.slug]},
                                    ]
                                },
                            },
                            "progress": {"mode": "count", "target": 2},
                        },
                        {
                            "id": "deliver_herb",
                            "text": "Deliver 1 moonleaf.",
                            "tracker": {
                                "event": "quest.item.delivered",
                                "where": {
                                    "all": [
                                        {"eq": ["event.target.template_id", self.quartermaster_template.slug]},
                                        {"eq": ["event.item.template_id", self.herb_template.slug]},
                                    ]
                                },
                            },
                            "progress": {"mode": "count", "target": 1},
                        },
                    ],
                    "transitions": [
                        {
                            "when": {
                                "all": [
                                    {"objective_complete": "deliver_pelts"},
                                    {"objective_complete": "deliver_herb"},
                                ]
                            },
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The quartermaster signs off on the delivery.",
                },
            ],
            reward_policy={
                "complete": [
                    {"type": "grant_gold", "amount": 10},
                    {"type": "grant_xp", "amount": 50},
                    {
                        "type": "mob_command",
                        "mob_template": self.quartermaster_template.slug,
                        "command": "/echo room Delivery accepted.",
                    },
                ],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )

    def test_turn_in_quest_progresses_from_give_and_grants_rewards(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages():
            dispatch_text_command(self.player.id, "give all.pelt quartermaster")

        with capture_game_messages() as final_messages:
            dispatch_text_command(self.player.id, "give moonleaf quartermaster")

        self.player.refresh_from_db()
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="quartermaster_supplies")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")
        self.assertEqual(self.player.gold, self.gold_before + 10)
        self.assertEqual(self.player.experience, self.exp_before + 50)
        self.assertIn("quest.instance.resolved", self._message_types(final_messages))
        self.assertIn("notification./echo", self._message_types(final_messages))


class TestQuestDiscoverability(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.bartender_template = MobTemplate.objects.create(
            world=self.world,
            name="Saloon Bartender",
            keywords="saloon bartender bartender",
            slug="saloon_bartender",
        )
        self.keg_template = ItemTemplate.objects.create(
            world=self.world,
            name="Saloon Keg",
            keywords="saloon keg keg",
            slug="saloon_keg",
        )
        self.bartender_template.spawn(self.room, self.spawn_world)
        self.create_runtime_quest(
            slug="saloon_keg_run",
            name="A Keg for the Bar",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": self.bartender_template.slug}],
                "visible_if": {},
                "accept_if": {},
                "salience": 25,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "deliver",
                    "kind": "objective",
                    "recap": "The bartender needs a fresh keg from the back room.",
                    "text": {
                        "body": (
                            '"Could you grab a keg from the back for me?" the bartender asks. '
                            '"I can\'t leave the bar unattended."'
                        )
                    },
                    "objectives": [
                        {
                            "id": "deliver_keg",
                            "text": "Bring the saloon keg to the bartender.",
                            "tracker": {
                                "event": "quest.item.delivered",
                                "where": {
                                    "all": [
                                        {"eq": ["event.target.template_id", self.bartender_template.slug]},
                                        {"eq": ["event.item.template_id", self.keg_template.slug]},
                                    ]
                                },
                            },
                            "progress": {"mode": "count", "target": 1},
                        }
                    ],
                    "transitions": [
                        {
                            "when": {"objective_complete": "deliver_keg"},
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "The bartender rolls the fresh keg into place.",
                },
            ],
        )

    def test_look_marks_npc_dialogue_offer_with_exclamation_indicator(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        look_message = self._message_by_type(messages, "cmd.look.success")
        bartender = self._room_char_by_name(look_message, "Saloon Bartender")
        self.assertIsNotNone(bartender)
        self.assertTrue(bartender["quest_data"]["enquire"])
        self.assertFalse(bartender["quest_data"]["complete"])

    def test_talk_to_offer_npc_shows_pitch_and_accept_command(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "talk bartender")

        guidance_message = self._message_by_type(messages, "quest.opportunity.presented")
        self.assertIsNotNone(guidance_message)
        self.assertIn("A Keg for the Bar", guidance_message["text"])
        self.assertIn("grab a keg from the back", guidance_message["text"].lower())
        self.assertIn("quest accept saloon_keg_run", guidance_message["text"])

    def test_return_npc_shows_question_indicator_when_turn_in_is_ready(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept saloon_keg_run")
        self.keg_template.spawn(self.player, self.spawn_world)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        look_message = self._message_by_type(messages, "cmd.look.success")
        bartender = self._room_char_by_name(look_message, "Saloon Bartender")
        self.assertIsNotNone(bartender)
        self.assertFalse(bartender["quest_data"]["enquire"])
        self.assertTrue(bartender["quest_data"]["complete"])

    def test_talk_to_turn_in_npc_without_giving_item_shows_handoff_hint(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "quest accept saloon_keg_run")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "talk bartender")

        hint_message = self._message_by_type(messages, "quest.interaction.hint")
        self.assertIsNotNone(hint_message)
        self.assertIn("A Keg for the Bar", hint_message["text"])
        self.assertIn("give <item> bartender", hint_message["text"])

    def test_quest_accept_without_slug_uses_single_visible_opportunity(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest accept")

        self.assertIn("quest.instance.started", self._message_types(messages))
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="saloon_keg_run")
        self.assertEqual(quest_instance.status, "active")


class TestQuestAcceptCommand(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.bartender_template = MobTemplate.objects.create(
            world=self.world,
            name="Saloon Bartender",
            keywords="saloon bartender bartender",
        )
        self.bartender_template.spawn(self.room, self.spawn_world)
        self.create_runtime_quest(
            slug="first_round",
            name="First Round",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": self.bartender_template.id}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "The bartender has a small job.",
                    "choices": [{"id": "continue", "text": "Continue.", "goto": "resolved"}],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )
        self.create_runtime_quest(
            slug="second_round",
            name="Second Round",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": self.bartender_template.id}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "The bartender has another job.",
                    "choices": [{"id": "continue", "text": "Continue.", "goto": "resolved"}],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )

    def test_quest_accept_without_slug_errors_when_multiple_opportunities_are_visible(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "quest accept")

        error_message = self._message_by_type(messages, "cmd.quest.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(error_message["data"]["code"], "ambiguous_opportunity")
        self.assertIn("quest accept <slug>", error_message["text"])


class TestKillReturnQuestRuntime(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        stats = compute_stats(self.player.level, self.player.archetype)
        self.player.health = stats["health_max"]
        self.player.save(update_fields=["health"])
        self.captain_template = MobTemplate.objects.create(
            world=self.world,
            name="Captain Merrow",
            keywords="captain merrow captain",
        )
        self.rat_template = MobTemplate.objects.create(
            world=self.world,
            name="Tunnel Rat",
            keywords="rat tunnel rat",
        )
        self.captain_template.spawn(self.room, self.spawn_world)
        self.rat_template.spawn(self.room, self.spawn_world)
        self.rat_template.spawn(self.room, self.spawn_world)

        self.create_runtime_quest(
            slug="rat_cull",
            name="Rat Cull",
            discovery_policy={
                "sources": [{"type": "auto_start"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "hunt",
                    "kind": "objective",
                    "recap": "Captain Merrow wants the tunnel rats culled.",
                    "objectives": [
                        {
                            "id": "kill_rats",
                            "text": "Kill 2 tunnel rats.",
                            "tracker": {
                                "event": "quest.mob.killed",
                                "where": {"eq": ["event.target.template_id", self.rat_template.id]},
                            },
                            "progress": {"mode": "count", "target": 2},
                        }
                    ],
                    "transitions": [
                        {
                            "when": {"objective_complete": "kill_rats"},
                            "goto": "report",
                        }
                    ],
                },
                {
                    "id": "report",
                    "kind": "objective",
                    "recap": "The rats are down. Report back to Captain Merrow.",
                    "objectives": [
                        {
                            "id": "report_back",
                            "text": "Talk to Captain Merrow.",
                            "tracker": {
                                "event": "cmd.talk.success",
                                "where": {"eq": ["event.target.template_id", self.captain_template.id]},
                            },
                            "progress": {"mode": "boolean", "target": 1},
                        }
                    ],
                    "transitions": [
                        {
                            "when": {"objective_complete": "report_back"},
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Captain Merrow confirms the camp is safe for now.",
                },
            ],
            reward_policy={
                "complete": [
                    {"type": "grant_gold", "amount": 8},
                    {"type": "mob_command", "command": "say Good work."},
                ],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )

    def test_kill_then_talk_quest_resolves_and_mob_responds(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")

        with capture_game_messages():
            dispatch_text_command(self.player.id, "kill rat")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "kill rat")
        with capture_game_messages() as final_messages:
            dispatch_text_command(self.player.id, "talk captain")

        self.player.refresh_from_db()
        quest_instance = QuestInstance.objects.get(player=self.player, template__slug="rat_cull")
        self.assertEqual(quest_instance.status, "resolved")
        self.assertEqual(quest_instance.resolution, "complete")
        self.assertEqual(self.player.gold, 8)
        self.assertIn("quest.instance.resolved", self._message_types(final_messages))
        self.assertIn("notification.cmd.say.success", self._message_types(final_messages))

    def test_return_to_captain_shows_question_indicator_after_kills(self):
        with capture_game_messages():
            dispatch_text_command(self.player.id, "look")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "kill rat")
        with capture_game_messages():
            dispatch_text_command(self.player.id, "kill rat")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        look_message = self._message_by_type(messages, "cmd.look.success")
        captain = self._room_char_by_name(look_message, "Captain Merrow")
        self.assertIsNotNone(captain)
        self.assertTrue(captain["quest_data"]["complete"])


class TestQuestScopedState(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        replace_state_snapshot(STATE_SCOPE_WORLD, self.spawn_world, {"weather": "stormy"})
        self.create_runtime_quest(
            slug="weather_watch",
            name="Weather Watch",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A weather log waits on the wall.",
                    }
                ],
                "visible_if": {"eq": ["state.world.weather", "stormy"]},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "The sky is {{ state.world.weather }}.",
                    "text": {"body": "Weather now: {{ state.world.weather }}."},
                    "effects": [
                        {
                            "type": "set_state",
                            "scope": "character",
                            "key": "weather_seen",
                            "value": "{state.world.weather}",
                        }
                    ],
                    "choices": [
                        {
                            "id": "continue",
                            "text": "Continue while it is {{ state.world.weather }}.",
                            "goto": "resolved",
                        }
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "You made note of the weather.",
                },
            ],
        )

    def test_quest_can_use_scoped_state_in_visibility_text_and_effects(self):
        opportunities = list_opportunities(self.player, refresh=True)
        self.assertIn("weather_watch", [opportunity["slug"] for opportunity in opportunities])
        weather_watch = next(
            opportunity for opportunity in opportunities if opportunity["slug"] == "weather_watch"
        )
        self.assertEqual(weather_watch["recap"], "The sky is stormy.")

        with capture_game_messages() as accept_messages:
            dispatch_text_command(self.player.id, "quest accept weather_watch")

        started_message = self._message_by_type(accept_messages, "quest.instance.started")
        self.assertIsNotNone(started_message)
        self.assertIn("Weather now: stormy.", started_message.get("text", ""))
        self.assertIn("Continue while it is stormy.", started_message.get("text", ""))
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player).get("weather_seen"),
            "stormy",
        )


class TestQuestCompletionPrerequisites(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_PLAYER_ID": str(self.player.id)}
        self.first_steps = self.create_runtime_quest(
            slug="first_steps",
            name="First Steps",
            discovery_policy={
                "sources": [],
                "visible_if": {},
                "accept_if": {},
                "salience": 0,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )
        self.town_favor = self.create_runtime_quest(
            slug="town_favor",
            name="Town Favor",
            discovery_policy={
                "sources": [],
                "visible_if": {},
                "accept_if": {},
                "salience": 0,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )
        self.create_runtime_quest(
            slug="gated_visible",
            name="Gated Visible",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A veteran task notice hangs here.",
                    }
                ],
                "visible_if": {"quest_completed": self.first_steps.slug},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "You have enough experience now.",
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )
        self.create_runtime_quest(
            slug="gated_visible_multi",
            name="Gated Visible Multi",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A second veteran notice hangs here.",
                    }
                ],
                "visible_if": {
                    "all": [
                        {"quest_completed": self.first_steps.slug},
                        {"quest_completed": self.town_favor.slug},
                    ]
                },
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "You cleared every prerequisite.",
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )
        self.create_runtime_quest(
            slug="gated_accept",
            name="Gated Accept",
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.room.id}",
                        "callout": "A sealed notice waits here.",
                    }
                ],
                "visible_if": {},
                "accept_if": {"quest_completed": self.first_steps.slug},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            steps=[
                {
                    "id": "offer",
                    "kind": "storylet",
                    "recap": "Visible now, but not yet startable.",
                    "choices": [
                        {"id": "continue", "text": "Continue.", "goto": "resolved"},
                    ],
                },
                {
                    "id": "resolved",
                    "kind": "resolution",
                    "recap": "Done.",
                },
            ],
        )

    def _opportunity_slugs(self):
        return [opportunity["slug"] for opportunity in list_opportunities(self.player, refresh=True)]

    def test_visible_if_can_require_a_completed_quest_by_slug(self):
        self.assertNotIn("gated_visible", self._opportunity_slugs())

        self.create_completed_quest_instance("first_steps")

        self.assertIn("gated_visible", self._opportunity_slugs())

    def test_visible_if_quest_completed_requires_complete_resolution(self):
        self.create_completed_quest_instance("first_steps", resolution="abandoned")

        self.assertNotIn("gated_visible", self._opportunity_slugs())

    def test_visible_if_can_require_multiple_completed_quests(self):
        self.create_completed_quest_instance("first_steps")
        self.assertNotIn("gated_visible_multi", self._opportunity_slugs())

        self.create_completed_quest_instance("town_favor")
        self.assertIn("gated_visible_multi", self._opportunity_slugs())

    def test_accept_if_can_require_a_completed_quest_by_slug(self):
        self.assertIn("gated_accept", self._opportunity_slugs())

        denied_resp = self.client.post(
            reverse("game-quest-opportunity-accept", args=["gated_accept"]),
            {},
            format="json",
            **self.headers,
        )
        self.assertEqual(denied_resp.status_code, 400)
        self.assertEqual(denied_resp.data["code"], "cannot_accept")

        self.create_completed_quest_instance("first_steps")

        accepted_resp = self.client.post(
            reverse("game-quest-opportunity-accept", args=["gated_accept"]),
            {},
            format="json",
            **self.headers,
        )
        self.assertEqual(accepted_resp.status_code, 201)
        self.assertEqual(accepted_resp.data["quest"]["template"]["slug"], "gated_accept")
