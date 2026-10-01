import json
from types import SimpleNamespace

import yaml
from django.urls import reverse
from django.utils import timezone

from builders.instance_templates import create_instance_template
from core.condition_dsl import (
    ConditionContext,
    QUEST_CONDITION_OPERATORS,
    evaluate_condition,
    validate_condition_payload,
)
from core.conditions import evaluate_conditions
from quests.models import QuestInstance, QuestOfferState, QuestTemplate
from quests.services.engine import abandon_instance, accept_template, choose_for_instance
from tests.base import WorldTestCase
from tests.combat_fixtures import dispatch_and_drain_combat
from tests.utils import capture_game_messages


class TestQuestConditions(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.quest = QuestTemplate.objects.create(
            world=self.world, slug="watch-contract", name="Watch Contract",
            status="active", repeatability_mode="always",
            graph={"steps": [
                {"id": "work", "kind": "storylet", "choices": [
                    {"id": "finish", "text": "Finish", "goto": "done"},
                ]},
                {"id": "done", "kind": "resolution", "resolution": "complete"},
            ]},
        )
        # Load context relations outside query-count assertions.
        self.player.room.zone
        self.player.world.context

    def states(self, *, player=None, ref=None):
        context = ConditionContext(actor=player or self.player)
        return tuple(evaluate_condition(
            {operator: self.quest.slug if ref is None else ref}, context=context,
        ) for operator in QUEST_CONDITION_OPERATORS)

    def create_attempt(self, **overrides):
        values = {"world": self.spawn_world, "player": self.player, "template": self.quest}
        values.update(overrides)
        return QuestInstance.objects.create(**values)

    def test_accept_abandon_reaccept_and_complete(self):
        self.assertEqual(self.states(), (False, False, False))
        instance = accept_template(self.player, self.quest).quest_instance
        self.assertEqual(self.states(), (True, True, False))
        abandon_instance(self.player, str(instance.pk))
        self.assertEqual(self.states(), (True, False, False))
        instance = accept_template(self.player, self.quest).quest_instance
        choose_for_instance(self.player, str(instance.pk), "finish")
        self.assertEqual(self.states(), (True, False, True))
        accept_template(self.player, self.quest)
        self.assertEqual(self.states(), (True, True, True))

    def test_auto_start_counts_as_accepted(self):
        self.quest.discovery_policy = {"sources": [{"type": "auto_start"}]}
        self.quest.save(update_fields=["discovery_policy"])
        with capture_game_messages():
            dispatch_and_drain_combat(self.player.pk, "look")
        self.assertEqual(self.states(), (True, True, False))

    def test_existing_history_needs_no_character_flag_and_is_player_specific(self):
        self.create_attempt(status="resolved", resolution="abandoned")
        self.assertEqual(self.states(), (True, False, False))
        self.create_attempt(status="resolved", resolution="complete")
        self.assertEqual(self.states(), (True, False, True))
        self.assertEqual(self.states(player=self.create_player("Other")), (False, False, False))

    def test_offer_is_not_acceptance_but_acceptance_timestamp_survives_run_cleanup(self):
        offer = QuestOfferState.objects.create(player=self.player, template=self.quest, is_visible=True)
        self.assertEqual(self.states(), (False, False, False))
        offer.last_accepted_at = timezone.now()
        offer.save(update_fields=["last_accepted_at"])
        self.assertEqual(self.states(), (True, False, False))

    def test_reference_forms_and_missing_refs(self):
        self.create_attempt()
        for ref in (self.quest.pk, str(self.quest.pk), self.quest.slug, f"questtemplate.{self.quest.slug}"):
            with self.subTest(ref=ref):
                self.assertEqual(self.states(ref=ref), (True, True, False))
        for ref in ("missing", "mobdefinition.watch-contract", True, "", None):
            with self.subTest(ref=ref):
                for operator in QUEST_CONDITION_OPERATORS:
                    self.assertFalse(evaluate_condition({operator: ref}, actor=self.player))
        numeric_slug = QuestTemplate.objects.create(
            world=self.world, slug=str(self.quest.pk), name="Numeric slug",
        )
        self.assertEqual(self.states(ref=f"questtemplate.{self.quest.pk}"), (False, False, False))
        self.create_attempt(template=numeric_slug, status="resolved", resolution="complete")
        self.assertEqual(self.states(ref=f"questtemplate.{self.quest.pk}"), (True, False, True))

    def test_mob_needs_an_explicit_player_context(self):
        self.create_attempt()
        mob = self.create_mob("Guard")
        for operator in QUEST_CONDITION_OPERATORS:
            with self.assertNumQueries(0):
                self.assertFalse(evaluate_condition({operator: self.quest.slug}, actor=mob))
        self.assertTrue(evaluate_condition(
            {"quest_active": self.quest.slug}, actor=mob, player=self.player,
        ))

    def test_instance_template_slugs_and_history_are_local_to_the_current_run(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        template = create_instance_template(base_world=self.world, author=self.user, name="Orientation")
        local_quest = QuestTemplate.objects.create(world=template, slug=self.quest.slug, name="Local quest")
        old_run = template.create_spawn_world()
        self.create_attempt(template=local_quest, world=old_run, status="resolved", resolution="complete")
        self.assertEqual(self.states(), (False, False, False))
        self.player.world = template.create_spawn_world()
        self.player.room = template.config.starting_room
        self.player.save(update_fields=["world", "room"])
        self.assertEqual(self.states(), (False, False, False))
        self.player.world = old_run
        self.player.save(update_fields=["world"])
        self.assertEqual(self.states(), (True, False, True))

    def test_boolean_composition_and_cache_refresh_between_evaluations(self):
        prompt = {"all": [
            {"not": {"quest_active": self.quest.slug}},
            {"not": {"quest_completed": self.quest.slug}},
        ]}
        self.assertTrue(evaluate_conditions(self.player, json.dumps(prompt))["result"])
        attempt = self.create_attempt()
        self.assertFalse(evaluate_conditions(self.player, prompt)["result"])
        attempt.status = "resolved"
        attempt.resolution = "abandoned"
        attempt.save(update_fields=["status", "resolution"])
        self.assertTrue(evaluate_conditions(self.player, prompt)["result"])
        self.assertFalse(evaluate_conditions(self.player, {"not": {"quest_accepted": self.quest.slug}})["result"])

    def test_quest_predicates_reuse_one_indexed_query_without_legacy_room_serialization(self):
        self.create_attempt(status="resolved", resolution="complete")
        with self.assertNumQueries(1):
            self.assertEqual(self.states(), (True, False, True))
        # A busy room and long history must not be loaded into Python to test a quest.
        for index in range(50):
            self.create_mob(f"Bystander {index}")
            self.create_attempt(status="resolved", resolution="abandoned")
        with self.assertNumQueries(1):
            self.assertTrue(evaluate_conditions(self.player, {"quest_accepted": self.quest.slug})["result"])

    def test_room_action_labels_batch_distinct_quests_and_refresh_after_acceptance(self):
        from spawns.triggers import _collect_display_action_labels

        quests = [self.quest] + [QuestTemplate.objects.create(
            world=self.world, slug=f"contract-{index}", name=f"Contract {index}",
        ) for index in range(12)]
        triggers = [SimpleNamespace(
            id=index + 1, scope="room", gate_delay=0, display_action_in_room=True,
            match=f"talk guard {index}",
            conditions=json.dumps({"not": {"quest_accepted": quest.slug}}),
        ) for index, quest in enumerate(quests)]

        def labels():
            return _collect_display_action_labels(
                actor=self.player, triggers=triggers, room=self.room, zone=self.zone, world=self.world,
            )

        with self.assertNumQueries(1):
            self.assertEqual(len(labels()), len(quests))
        accept_template(self.player, self.quest)
        with self.assertNumQueries(1):
            self.assertNotIn("talk guard 0", labels())

    def test_invalid_operands_and_candidate_filters_are_rejected(self):
        for operator in QUEST_CONDITION_OPERATORS:
            for ref in (None, "", True, [], {}, ["watch-contract"], "mobdefinition.guard"):
                with self.subTest(operator=operator, ref=ref), self.assertRaises(ValueError):
                    validate_condition_payload({operator: ref})
            with self.assertRaisesRegex(ValueError, "while filtering mob candidates"):
                validate_condition_payload({"mob_present": {
                    "ref": "mobdefinition.guard", "where": {operator: self.quest.slug},
                }})

    def test_quest_and_trigger_manifests_validate_and_round_trip_new_predicates(self):
        self.client.force_authenticate(self.user)
        endpoint = reverse("builder-world-manifest-apply", args=[self.world.pk])
        conditions = {"all": [
            {"quest_accepted": self.quest.slug},
            {"not": {"quest_active": self.quest.slug}},
        ]}
        quest_document = {
            "kind": "quest", "metadata": {"slug": "next-contract", "name": "Next Contract"},
            "spec": {"status": "active", "discovery": {
                "sources": [], "visible_if": conditions, "accept_if": conditions,
            }, "steps": self.quest.graph["steps"]},
        }
        trigger_document = {
            "kind": "trigger", "metadata": {"name": "Talk guard"},
            "spec": {"scope": "room", "kind": "command", "target": f"room@{self.room.relative_id}",
                     "match": "talk guard", "script": "talk guard", "conditions": conditions},
        }
        for document in (quest_document, trigger_document):
            with self.subTest(kind=document["kind"]):
                response = self.client.post(endpoint, {"manifest": yaml.safe_dump(document)}, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                key = "quest" if document["kind"] == "quest" else "trigger"
                exported = response.data[key]["manifest"]
                self.assertIn("quest_accepted", str(exported))
                self.assertIn("quest_active", str(exported))
                response = self.client.post(endpoint, {"manifest": yaml.safe_dump(exported)}, format="json")
                self.assertIn(response.status_code, (200, 201), response.data)
                for operator in QUEST_CONDITION_OPERATORS:
                    if key == "quest":
                        document["spec"]["discovery"]["visible_if"] = {operator: "missing"}
                    else:
                        document["spec"]["conditions"] = {operator: "missing"}
                    response = self.client.post(endpoint, {"manifest": yaml.safe_dump(document)}, format="json")
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIn("unknown questtemplate", str(response.data))
