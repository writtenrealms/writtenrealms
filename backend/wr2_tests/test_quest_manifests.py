import yaml

from rest_framework.reverse import reverse

from builders.models import WorldBuilder
from quests.models import QuestArcTemplate, QuestTemplate
from tests.base import WorldTestCase


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestQuestManifests(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.quest_list_ep = reverse(
            "builder-quest-template-list",
            args=[self.world.pk],
        )
        self.quest_arc_list_ep = reverse(
            "builder-quest-arc-template-list",
            args=[self.world.pk],
        )
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

    def test_quest_template_list_includes_yaml_template(self):
        resp = self.client.get(self.quest_list_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("new_quest_template", resp.data)
        self.assertEqual(resp.data["quests"], [])

        template = resp.data["new_quest_template"]
        self.assertIn("manifest", template)
        self.assertIn("yaml", template)
        manifest = yaml.safe_load(template["yaml"])
        self.assertEqual(manifest["kind"], "quest")
        self.assertEqual(manifest["metadata"]["world"], f"world.{self.world.id}")

    def test_apply_quest_manifest_can_create_quest_template(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: bitter_well
  name: The Bitter Well
spec:
  type: quest
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_template: mobtemplate.12
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: A healer asks for help.
      lead: Investigate the poisoned well.
      stakes: The village is in danger.
      choices:
        - id: accept
          text: Help.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The quest is complete.
      lead: ""
      stakes: ""
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["kind"], "quest")
        self.assertEqual(resp.data["operation"], "created")

        quest = QuestTemplate.objects.get(slug="bitter_well")
        self.assertEqual(quest.world, self.world)
        self.assertEqual(quest.name, "The Bitter Well")
        self.assertEqual(quest.quest_type, "quest")
        self.assertEqual(quest.scope, "player")
        self.assertEqual(quest.status, "active")
        self.assertEqual(quest.graph["steps"][0]["id"], "offer")

    def test_apply_quest_manifest_supports_partial_nested_update(self):
        quest = QuestTemplate.objects.create(
            world=self.world,
            slug="bitter_well",
            name="The Bitter Well",
            quest_type="quest",
            scope="player",
            status="draft",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_template": "mobtemplate.12"}],
                "visible_if": {},
                "accept_if": {},
                "salience": 10,
                "cooldown_seconds": 0,
            },
            graph={
                "steps": [
                    {
                        "id": "offer",
                        "kind": "storylet",
                        "recap": "Old recap",
                        "lead": "Old lead",
                        "stakes": "",
                        "choices": [
                            {"id": "continue", "text": "Continue", "goto": "resolved"}
                        ],
                    },
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": "Resolved",
                        "lead": "",
                        "stakes": "",
                    },
                ]
            },
            reward_policy={
                "complete": [],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: {quest.slug}
spec:
  discovery:
    salience: 95
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        quest.refresh_from_db()
        self.assertEqual(quest.discovery_policy["salience"], 95)
        self.assertEqual(len(quest.graph["steps"]), 2)
        self.assertEqual(quest.graph["steps"][0]["id"], "offer")

    def test_apply_quest_manifest_can_delete_quest_template(self):
        quest = QuestTemplate.objects.create(
            world=self.world,
            slug="old_quest",
            name="Old Quest",
            graph={"steps": [{"id": "resolved", "kind": "resolution"}]},
        )
        manifest = f"""
kind: quest
operation: delete
metadata:
  world: world.{self.world.id}
  slug: {quest.slug}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operation"], "deleted")
        self.assertFalse(
            QuestTemplate.objects.filter(pk=quest.pk).exists()
        )

    def test_apply_quest_arc_manifest_and_reference_it_from_quest(self):
        arc_manifest = f"""
kind: questarc
metadata:
  world: world.{self.world.id}
  slug: ashwick_arc
  name: Ashwick Arc
spec:
  summary: The village faces an outbreak.
  journal_policy: {{}}
"""
        arc_resp = self.client.post(
            self.apply_ep,
            {"manifest": arc_manifest},
            format="json",
        )
        self.assertEqual(arc_resp.status_code, 201)
        self.assertEqual(arc_resp.data["kind"], "questarc")

        quest_manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: bitter_well
  name: The Bitter Well
spec:
  type: quest
  scope: player
  status: draft
  arc: ashwick_arc
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources: []
    visible_if: {{}}
    accept_if: {{}}
    salience: 0
    cooldown_seconds: 0
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: Offer
      lead: Lead
      stakes: Stakes
      choices:
        - id: accept
          text: Accept
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Resolved
      lead: ""
      stakes: ""
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
"""
        quest_resp = self.client.post(
            self.apply_ep,
            {"manifest": quest_manifest},
            format="json",
        )
        self.assertEqual(quest_resp.status_code, 201)
        quest = QuestTemplate.objects.get(slug="bitter_well")
        self.assertIsNotNone(quest.arc)
        self.assertEqual(quest.arc.slug, "ashwick_arc")

        arc_list_resp = self.client.get(self.quest_arc_list_ep)
        self.assertEqual(arc_list_resp.status_code, 200)
        self.assertEqual(len(arc_list_resp.data["quest_arcs"]), 1)

    def test_rank_2_builder_cannot_apply_quest_manifest(self):
        builder_user = self.create_user("rank2-builder@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: forbidden_quest
  name: Forbidden Quest
spec:
  type: quest
  scope: player
  status: draft
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources: []
    visible_if: {{}}
    accept_if: {{}}
    salience: 0
    cooldown_seconds: 0
  slots: {{}}
  steps:
    - id: resolved
      kind: resolution
      recap: Resolved
      lead: ""
      stakes: ""
  rewards:
    complete: []
    compromised: []
    failed_forward: []
    expired: []
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
