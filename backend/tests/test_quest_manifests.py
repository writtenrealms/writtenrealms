import yaml

from rest_framework.reverse import reverse

from builders.models import ItemDefinition, MobDefinition, WorldBuilder
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
        self.assertNotIn("type", manifest["spec"])
        self.assertNotIn("lead", template["yaml"])
        self.assertNotIn("stakes", template["yaml"])

    def test_item_and_mob_definitions_generate_unique_world_slugs(self):
        mob_one = MobDefinition.objects.create(world=self.world, name="Quartermaster")
        mob_two = MobDefinition.objects.create(world=self.world, name="Quartermaster")
        item_one = ItemDefinition.objects.create(world=self.world, name="Wolf Pelt")
        item_two = ItemDefinition.objects.create(world=self.world, name="Wolf Pelt")

        self.assertEqual(mob_one.slug, "quartermaster")
        self.assertEqual(mob_two.slug, "quartermaster-2")
        self.assertEqual(item_one.slug, "wolf-pelt")
        self.assertEqual(item_two.slug, "wolf-pelt-2")

    def test_apply_quest_manifest_can_create_quest_template(self):
        quartermaster = MobDefinition.objects.create(
            world=self.world,
            name="Quartermaster",
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: bitter_well
  name: The Bitter Well
spec:
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources:
      - type: npc_dialogue
        mob_definition: mobdefinition.{quartermaster.slug}
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: A healer asks for help.
      choices:
        - id: accept
          text: Help.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: The quest is complete.
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

    def test_apply_quest_manifest_accepts_mob_and_item_definition_slugs(self):
        quartermaster = MobDefinition.objects.create(
            world=self.world,
            name="Quartermaster",
        )
        wolf_pelt = ItemDefinition.objects.create(
            world=self.world,
            name="Wolf Pelt",
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: supply_delivery
  name: Supply Delivery
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
        mob_definition: {quartermaster.slug}
    salience: 80
  slots: {{}}
  steps:
    - id: turn_in
      kind: objective
      recap: Deliver supplies.
      effects:
        - type: grant_item
          item_definition: {wolf_pelt.slug}
      objectives:
        - id: deliver_pelt
          text: Deliver the pelt.
          tracker:
            event: quest.item.delivered
            where:
              all:
                - eq: [event.target.definition_id, mobdefinition.{quartermaster.slug}]
                - eq: [event.item.definition_id, {wolf_pelt.slug}]
          progress:
            mode: count
            target: 1
      transitions:
        - when:
            objective_complete: deliver_pelt
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Delivered.
  rewards:
    complete:
      - type: mob_command
        mob_definition: {quartermaster.slug}
        command: say Good.
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

        quest = QuestTemplate.objects.get(slug="supply_delivery")
        self.assertEqual(
            quest.discovery_policy["sources"][0]["mob_definition"],
            quartermaster.slug,
        )
        objective_where = quest.graph["steps"][0]["objectives"][0]["tracker"]["where"]["all"]
        self.assertEqual(
            objective_where[0]["eq"][1],
            f"mobdefinition.{quartermaster.slug}",
        )
        self.assertEqual(
            objective_where[1]["eq"][1],
            wolf_pelt.slug,
        )
        self.assertEqual(
            quest.graph["steps"][0]["effects"][0]["item_definition"],
            wolf_pelt.slug,
        )

    def test_apply_quest_manifest_accepts_room_prompt_callout(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: coat_return
  name: Coat Return
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
      - type: room_prompt
        room: room.{self.room.id}
        callout: A forgotten coat hangs over the back of a chair.
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: A coat was left behind here.
      choices:
        - id: begin
          text: Take responsibility for it.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Done.
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

        quest = QuestTemplate.objects.get(slug="coat_return")
        self.assertEqual(
            quest.discovery_policy["sources"][0]["callout"],
            "A forgotten coat hangs over the back of a chair.",
        )

    def test_apply_quest_manifest_accepts_step_room_items(self):
        quest_item = ItemDefinition.objects.create(
            world=self.world,
            name="Saloon Keg",
            slug="saloon_keg",
            item_type="quest",
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: saloon_keg_run
  name: A Keg for the Bar
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
      - type: room_prompt
        room: room.{self.room.id}
        callout: A keg request has been posted here.
    salience: 80
  slots: {{}}
  steps:
    - id: fetch_keg
      kind: objective
      recap: Fetch the keg.
      room_items:
        - id: saloon_keg
          room: room.{self.room.id}
          item_definition: {quest_item.slug}
          room_description: A full saloon keg rests here.
    - id: resolved
      kind: resolution
      recap: Done.
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

        quest = QuestTemplate.objects.get(slug="saloon_keg_run")
        room_item = quest.graph["steps"][0]["room_items"][0]
        self.assertEqual(room_item["id"], "saloon_keg")
        self.assertEqual(room_item["item_definition"], quest_item.slug)
        self.assertEqual(room_item["room"], f"room.{self.room.id}")
        self.assertEqual(
            room_item["room_description"],
            "A full saloon keg rests here.",
        )
        self.assertNotIn("ground_description", room_item)

    def test_apply_quest_manifest_rejects_removed_ground_description(self):
        quest_item = ItemDefinition.objects.create(
            world=self.world,
            name="Saloon Keg",
            slug="saloon_keg",
            item_type="quest",
        )
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: old_room_item_description
  name: Old Room Item Description
spec:
  type: quest
  scope: player
  status: active
  repeatability:
    mode: never
    cooldown_seconds: 0
  max_active: 1
  discovery:
    sources: []
  slots: {{}}
  steps:
    - id: fetch
      kind: objective
      room_items:
        - id: saloon_keg
          room: room.{self.room.id}
          item_definition: {quest_item.slug}
          ground_description: A full saloon keg rests here.
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

        self.assertEqual(resp.status_code, 400)
        self.assertIn("renamed to room_description", str(resp.data))

    def test_apply_quest_manifest_rejects_non_quest_step_room_item_definitions(self):
        inert_item = ItemDefinition.objects.create(
            world=self.world,
            name="Lantern",
            slug="lantern",
            item_type="inert",
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: invalid_pickup
  name: Invalid Pickup
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
      - type: room_prompt
        room: room.{self.room.id}
        callout: Something here is ready to be fetched.
    salience: 80
  slots: {{}}
  steps:
    - id: fetch
      kind: objective
      recap: Fetch it.
      room_items:
        - id: lantern
          room: room.{self.room.id}
          item_definition: {inert_item.slug}
    - id: resolved
      kind: resolution
      recap: Done.
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
        self.assertEqual(resp.status_code, 400)
        self.assertIn("type 'quest'", str(resp.data).lower())

    def test_apply_quest_manifest_accepts_quest_completed_conditions_by_slug(self):
        prereq = QuestTemplate.objects.create(
            world=self.world,
            slug="first_steps",
            name="First Steps",
        )

        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: veteran_work
  name: Veteran Work
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
      - type: room_prompt
        room: room.{self.room.id}
        callout: A veteran notice hangs here.
    visible_if:
      quest_completed: {prereq.slug}
    accept_if:
      quest_completed: {prereq.slug}
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: Only veterans see this.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Done.
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

        quest = QuestTemplate.objects.get(slug="veteran_work")
        self.assertEqual(quest.discovery_policy["visible_if"]["quest_completed"], prereq.slug)
        self.assertEqual(quest.discovery_policy["accept_if"]["quest_completed"], prereq.slug)

    def test_apply_quest_manifest_rejects_unknown_quest_completed_slug(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: blocked_work
  name: Blocked Work
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
      - type: room_prompt
        room: room.{self.room.id}
        callout: A blocked notice hangs here.
    visible_if:
      quest_completed: missing_prereq
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: Blocked.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Done.
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
        self.assertEqual(resp.status_code, 400)
        self.assertIn("quest_completed", str(resp.data))
        self.assertIn("unknown questtemplate", str(resp.data).lower())

    def test_apply_quest_manifest_rejects_room_prompt_without_callout(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: missing_callout
  name: Missing Callout
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
      - type: room_prompt
        room: room.{self.room.id}
    salience: 80
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: Missing authored room callout.
      choices:
        - id: continue
          text: Continue.
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Done.
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
        self.assertEqual(resp.status_code, 400)
        self.assertIn("callout", str(resp.data).lower())
        self.assertIn("required", str(resp.data).lower())

    def test_apply_quest_manifest_rejects_removed_questlet_type(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: old_questlet
  name: Old Questlet
spec:
  type: questlet
  status: draft
  steps:
    - id: offer
      kind: storylet
      recap: Deprecated type.
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
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no longer supported", str(resp.data).lower())
        self.assertIn("use 'quest'", str(resp.data).lower())

    def test_apply_quest_manifest_rejects_removed_lead_and_stakes_fields(self):
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: deprecated_fields
  name: Deprecated Fields
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
    salience: 0
  slots: {{}}
  steps:
    - id: offer
      kind: storylet
      recap: Deprecated.
      lead: Old lead.
      stakes: Old stakes.
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
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no longer supported", str(resp.data).lower())

    def test_apply_quest_manifest_supports_partial_nested_update(self):
        quest = QuestTemplate.objects.create(
            world=self.world,
            slug="bitter_well",
            name="The Bitter Well",
            quest_type="quest",
            scope="player",
            status="draft",
            discovery_policy={
                "sources": [{"type": "npc_dialogue", "mob_definition": "mobdefinition.12"}],
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
                        "choices": [
                            {"id": "continue", "text": "Continue", "goto": "resolved"}
                        ],
                    },
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": "Resolved",
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

    def test_partial_repeatability_mode_update_clears_inherited_cooldown(self):
        for mode in ("never", "always"):
            with self.subTest(mode=mode):
                quest = QuestTemplate.objects.create(
                    world=self.world,
                    slug=f"cooldown_to_{mode}",
                    name=f"Cooldown to {mode.title()}",
                    repeatability_mode="cooldown",
                    repeatability_cooldown_seconds=1200,
                    graph={"steps": [{"id": "resolved", "kind": "resolution"}]},
                )
                manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: {quest.slug}
spec:
  repeatability:
    mode: {mode}
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 200)
                quest.refresh_from_db()
                self.assertEqual(quest.repeatability_mode, mode)
                self.assertEqual(quest.repeatability_cooldown_seconds, 0)

    def test_partial_repeatability_mode_update_rejects_explicit_nonzero_cooldown(self):
        quest = QuestTemplate.objects.create(
            world=self.world,
            slug="invalid_repeatability_update",
            name="Invalid Repeatability Update",
            repeatability_mode="cooldown",
            repeatability_cooldown_seconds=1200,
            graph={"steps": [{"id": "resolved", "kind": "resolution"}]},
        )
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: {quest.slug}
spec:
  repeatability:
    mode: never
    cooldown_seconds: 60
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("only valid when mode is 'cooldown'", str(resp.data))
        quest.refresh_from_db()
        self.assertEqual(quest.repeatability_mode, "cooldown")
        self.assertEqual(quest.repeatability_cooldown_seconds, 1200)

    def test_partial_repeatability_update_can_enable_cooldown(self):
        quest = QuestTemplate.objects.create(
            world=self.world,
            slug="enable_repeatability_cooldown",
            name="Enable Repeatability Cooldown",
            repeatability_mode="never",
            repeatability_cooldown_seconds=0,
            graph={"steps": [{"id": "resolved", "kind": "resolution"}]},
        )
        manifest = f"""
kind: quest
metadata:
  world: world.{self.world.id}
  slug: {quest.slug}
spec:
  repeatability:
    mode: cooldown
    cooldown_seconds: 1200
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        quest.refresh_from_db()
        self.assertEqual(quest.repeatability_mode, "cooldown")
        self.assertEqual(quest.repeatability_cooldown_seconds, 1200)

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
      choices:
        - id: accept
          text: Accept
          goto: resolved
    - id: resolved
      kind: resolution
      recap: Resolved
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
