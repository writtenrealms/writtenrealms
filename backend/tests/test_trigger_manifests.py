import json
from unittest.mock import patch

import yaml

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache

from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    BuilderAssignment,
    ItemDefinition,
    MobDefinition,
    Trigger,
    WorldBuilder,
)
from config import constants as adv_consts
from core.trigger_steps import (
    MAX_TRIGGER_CONSUME_ITEM_COUNT,
    MAX_TRIGGER_ECHO_LENGTH,
    MAX_TRIGGER_STEPS_SERIALIZED_BYTES,
    TriggerStepSpecError,
    normalize_trigger_steps,
)
from core.trigger_policy_cache import (
    TRIGGER_POLICY_CACHE_VERSION_FLOOR,
    bump_trigger_policy_cache_version,
    get_trigger_policy_cache_version,
    trigger_policy_cache_version_key,
)
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestTriggerManifests(AuthenticatedBuilderWorldTestCase):
    @staticmethod
    def _trigger_hook_cache_helpers():
        import spawns.handlers  # noqa: F401
        from spawns.triggers import (
            TRIGGER_HOOK_CACHE_TIMEOUT_SECONDS,
            _cached_room_hooks,
            _room_hook_cache_key,
        )

        return (
            TRIGGER_HOOK_CACHE_TIMEOUT_SECONDS,
            _cached_room_hooks,
            _room_hook_cache_key,
        )

    def setUp(self):
        super().setUp()
        room_ct = ContentType.objects.get_for_model(Room)
        self.trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=room_ct,
            target_id=self.room.id,
            name="Old Trigger Name",
            match="touch stone",
            script="/cmd room -- /echo -- Old message.",
            conditions="",
            show_details_on_failure=False,
            failure_message="",
            display_action_in_room=True,
            gate_delay=10,
            order=0,
            is_active=True,
        )
        self.list_ep = reverse(
            "builder-room-trigger-list",
            args=[self.world.pk, self.room.pk],
        )
        self.detail_ep = reverse(
            "builder-room-trigger-detail",
            args=[self.world.pk, self.room.pk, self.trigger.pk],
        )
        self.world_list_ep = reverse(
            "builder-world-trigger-list",
            args=[self.world.pk],
        )
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

    def test_room_trigger_list_includes_yaml_manifest(self):
        resp = self.client.get(self.list_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(len(resp.data["triggers"]), 1)
        self.assertIn("new_trigger_template", resp.data)

        trigger_data = resp.data["results"][0]
        self.assertEqual(trigger_data["id"], self.trigger.id)
        self.assertEqual(trigger_data["key"], self.trigger.key)
        self.assertIn("kind: trigger", trigger_data["yaml"])
        self.assertIn(f"key: {self.trigger.key}", trigger_data["yaml"])
        self.assertIn("operation: delete", trigger_data["delete_yaml"])

        template = resp.data["new_trigger_template"]
        self.assertIn("manifest", template)
        self.assertIn("yaml", template)
        self.assertTrue(template["yaml"].strip())

        template_manifest = template["manifest"]
        self.assertEqual(template_manifest["kind"], "trigger")
        self.assertEqual(template_manifest["metadata"]["world"], f"world.{self.world.id}")
        self.assertNotIn("id", template_manifest["metadata"])
        self.assertNotIn("key", template_manifest["metadata"])
        self.assertEqual(template_manifest["spec"]["scope"], adv_consts.TRIGGER_SCOPE_ROOM)
        self.assertEqual(template_manifest["spec"]["kind"], adv_consts.TRIGGER_KIND_COMMAND)
        self.assertEqual(template_manifest["spec"]["target"]["type"], "room")
        self.assertEqual(
            template_manifest["spec"]["target"]["key"],
            f"room.{self.room.id}",
        )
        self.assertIn("match", template_manifest["spec"])
        self.assertIn("script", template_manifest["spec"])

        parsed_template_yaml = yaml.safe_load(template["yaml"])
        self.assertEqual(parsed_template_yaml["kind"], "trigger")
        self.assertEqual(parsed_template_yaml["metadata"]["world"], f"world.{self.world.id}")
        self.assertEqual(parsed_template_yaml["spec"]["target"]["key"], f"room.{self.room.id}")

    def test_room_trigger_detail_includes_yaml_manifest(self):
        resp = self.client.get(self.detail_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], self.trigger.id)
        self.assertEqual(resp.data["key"], self.trigger.key)
        self.assertEqual(resp.data["target"]["type"], "room")
        self.assertEqual(resp.data["target"]["key"], self.room.key)
        self.assertIn("kind: trigger", resp.data["yaml"])
        self.assertIn("operation: delete", resp.data["delete_yaml"])

    def test_room_trigger_list_supports_filters_and_search(self):
        other_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Other Room",
            x=self.room.x + 1,
            y=self.room.y,
            z=self.room.z,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=other_room.id,
            name="Other Room Trigger",
            match="touch stone",
            script="/cmd room -- /echo -- Other message.",
            display_action_in_room=True,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Inactive Room Trigger",
            match="pull chain",
            script="/cmd room -- /echo -- Chain message.",
            is_active=False,
            display_action_in_room=True,
        )

        resp = self.client.get(self.list_ep, {"query": "touch stone"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([trigger["id"] for trigger in resp.data["results"]], [self.trigger.id])

        resp = self.client.get(self.list_ep, {"is_active": "false"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["name"], "Inactive Room Trigger")

    def test_world_trigger_list_includes_yaml_manifest(self):
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Lorekeeper",
        )
        mob_trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=mob_definition.id,
            name="Lorekeeper Reaction",
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="hello",
            script="say Welcome.",
            display_action_in_room=False,
            gate_delay=5,
            order=2,
            is_active=False,
        )

        resp = self.client.get(self.world_list_ep)
        self.assertEqual(resp.status_code, 200)
        trigger_ids = {trigger["id"] for trigger in resp.data["results"]}
        self.assertIn(self.trigger.id, trigger_ids)
        self.assertIn(mob_trigger.id, trigger_ids)

        mob_trigger_data = next(
            trigger for trigger in resp.data["results"] if trigger["id"] == mob_trigger.id
        )
        self.assertEqual(mob_trigger_data["key"], mob_trigger.key)
        self.assertEqual(mob_trigger_data["scope"], adv_consts.TRIGGER_SCOPE_WORLD)
        self.assertEqual(mob_trigger_data["kind"], adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(mob_trigger_data["target"]["type"], "mobdefinition")
        self.assertEqual(mob_trigger_data["target"]["name"], "Lorekeeper")
        self.assertFalse(mob_trigger_data["is_active"])
        self.assertIn("kind: trigger", mob_trigger_data["yaml"])
        self.assertIn(f"key: {mob_trigger.key}", mob_trigger_data["yaml"])
        self.assertIn("operation: delete", mob_trigger_data["delete_yaml"])

        detail_ep = reverse(
            "builder-world-trigger-detail",
            args=[self.world.pk, mob_trigger.pk],
        )
        detail_resp = self.client.get(detail_ep)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.data["id"], mob_trigger.id)
        self.assertEqual(detail_resp.data["manifest"]["spec"]["event"], adv_consts.MOB_REACTION_EVENT_SAYING)

    def test_world_trigger_list_supports_filters_and_search(self):
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=MobDefinition.objects.create(world=self.world, name="Lorekeeper").id,
            name="Lorekeeper Reaction",
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="hello",
            script="say Welcome.",
            display_action_in_room=False,
            is_active=False,
        )

        resp = self.client.get(self.world_list_ep, {"scope": adv_consts.TRIGGER_SCOPE_ROOM})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([trigger["id"] for trigger in resp.data["results"]], [self.trigger.id])

        resp = self.client.get(self.world_list_ep, {"kind": adv_consts.TRIGGER_KIND_EVENT})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["name"], "Lorekeeper Reaction")

        resp = self.client.get(self.world_list_ep, {"is_active": "false"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["name"], "Lorekeeper Reaction")

        resp = self.client.get(self.world_list_ep, {"query": "touch stone"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([trigger["id"] for trigger in resp.data["results"]], [self.trigger.id])

    def test_rank_2_builder_cannot_view_world_trigger_list(self):
        builder_user = self.create_user("trigger-list-builder@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        resp = self.client.get(self.world_list_ep)
        self.assertEqual(resp.status_code, 403)

    def test_apply_trigger_manifest_updates_trigger(self):
        manifest = f"""
apiVersion: writtenrealms.com/v1alpha1
kind: Trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: {self.room.key}
  match: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
  conditions: level 1
  show_details_on_failure: true
  failure_message: Not yet.
  display_action_in_room: true
  gate_delay: 5
  order: 7
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "trigger")
        self.assertEqual(resp.data["operation"], "updated")

        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.name, "Pull Lever Trigger")
        self.assertEqual(self.trigger.match, "pull lever or pull chain")
        self.assertEqual(self.trigger.script, "/cmd room -- /echo -- The lever clicks.")
        self.assertEqual(self.trigger.conditions, "level 1")
        self.assertTrue(self.trigger.show_details_on_failure)
        self.assertEqual(self.trigger.failure_message, "Not yet.")
        self.assertTrue(self.trigger.display_action_in_room)
        self.assertEqual(self.trigger.gate_delay, 5)
        self.assertEqual(self.trigger.order, 7)
        self.assertTrue(self.trigger.is_active)

    def test_apply_trigger_manifest_can_create_trigger(self):
        manifest = f"""
apiVersion: writtenrealms.com/v1alpha1
kind: Trigger
metadata:
  world: world.{self.world.id}
  name: New Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: {self.room.key}
  match: touch statue
  script: /cmd room -- /echo -- The statue vibrates.
  conditions: level 1
  show_details_on_failure: false
  display_action_in_room: true
  gate_delay: 3
  order: 12
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["kind"], "trigger")
        self.assertEqual(resp.data["operation"], "created")
        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.world, self.world)
        self.assertEqual(created_trigger.name, "New Trigger")
        self.assertEqual(created_trigger.scope, adv_consts.TRIGGER_SCOPE_ROOM)
        self.assertEqual(created_trigger.kind, adv_consts.TRIGGER_KIND_COMMAND)
        self.assertEqual(created_trigger.target_type, ContentType.objects.get_for_model(Room))
        self.assertEqual(created_trigger.target_id, self.room.id)
        self.assertEqual(created_trigger.match, "touch statue")
        self.assertEqual(
            created_trigger.script,
            "/cmd room -- /echo -- The statue vibrates.",
        )
        self.assertEqual(created_trigger.conditions, "level 1")
        self.assertEqual(created_trigger.gate_delay, 3)
        self.assertEqual(created_trigger.order, 12)
        self.assertTrue(created_trigger.is_active)

    def test_apply_trigger_manifest_accepts_structured_conditions(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  conditions:
    eq:
      - state.world.weather
      - rainy
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.trigger.refresh_from_db()
        self.assertIn("state.world.weather", self.trigger.conditions)

        trigger_payload = resp.data["trigger"]
        self.assertEqual(
            trigger_payload["manifest"]["spec"]["conditions"],
            {"eq": ["state.world.weather", "rainy"]},
        )

    def test_apply_trigger_manifest_supports_multiline_script(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: |
    /cmd room -- /echo -- The lever clicks.
    /cmd room -- /echo -- Dust falls from the ceiling.
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "trigger")
        self.assertEqual(resp.data["operation"], "updated")

        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.script.splitlines(),
            [
                "/cmd room -- /echo -- The lever clicks.",
                "/cmd room -- /echo -- Dust falls from the ceiling.",
            ],
        )

    def test_apply_trigger_manifest_supports_typed_scheduled_steps(self):
        seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-growing",
            name="a growing barley plant",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  conditions:
    item_present:
      location: actor_inventory
      item: {seed.id}
  steps:
    - after_seconds: 0
      actions:
        - type: consume_item
          actor: trigger_actor
          item: itemdefinition.barley-seed
          count: 1
        - type: spawn_room_item
          room: trigger_room
          item: itemdefinition.barley-seedling
          bind: crop
    - after_seconds: 20
      actions:
        - type: replace_room_item
          target: crop
          with: itemdefinition.barley-growing
        - type: echo
          room: trigger_room
          text: A murmur of growth fills the air.
  on_step_error: cancel
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.script, "")
        self.assertEqual(self.trigger.on_step_error, "cancel")
        self.assertEqual(self.trigger.steps[0]["after_seconds"], 0)
        self.assertEqual(self.trigger.steps[0]["actions"][1]["bind"], "crop")
        self.assertEqual(
            self.trigger.steps[1]["actions"][0]["with"],
            "itemdefinition.barley-growing",
        )
        self.assertEqual(
            yaml.safe_load(self.trigger.conditions)["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        trigger_payload = resp.data["trigger"]
        self.assertEqual(trigger_payload["manifest"]["spec"]["steps"], self.trigger.steps)
        self.assertEqual(trigger_payload["manifest"]["spec"]["on_step_error"], "cancel")

    def test_apply_trigger_manifest_supports_currency_debit_step(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: debit_currency
          actor: trigger_actor
          currency: currency.{obol.id}
          amount: 10
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.steps[0]["actions"][0],
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 10,
            },
        )
        self.assertEqual(
            resp.data["trigger"]["manifest"]["spec"]["steps"],
            self.trigger.steps,
        )

    def test_apply_trigger_manifest_supports_command_step_subjects(self):
        charon = MobDefinition.objects.create(
            world=self.world,
            slug="charon",
            name="Charon",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: command
          subject: trigger_room
          command: /echo The ferry creaks.
    - after_seconds: 5
      actions:
        - type: command
          subject: trigger_actor
          command: say I accept the fare.
    - after_seconds: 5
      actions:
        - type: command
          subject:
            type: mob
            room: trigger_room
            mob: {charon.id}
          command: emote nods once.
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.steps[0]["actions"][0],
            {
                "type": "command",
                "subject": "trigger_room",
                "command": "/echo The ferry creaks.",
            },
        )
        self.assertEqual(
            self.trigger.steps[1]["actions"][0]["subject"],
            "trigger_actor",
        )
        self.assertEqual(
            self.trigger.steps[2]["actions"][0]["subject"]["mob"],
            "mobdefinition.charon",
        )
        self.assertEqual(
            resp.data["trigger"]["manifest"]["spec"]["steps"],
            self.trigger.steps,
        )

    def test_apply_trigger_manifest_rejects_invalid_currency_debit_steps(self):
        create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        late_item = ItemDefinition.objects.create(
            world=self.world,
            slug="late-debit-item",
            name="a late debit item",
        )
        invalid_actions = (
            (
                """
        - type: debit_currency
          actor: trigger_actor
          currency: missing
          amount: 10
""",
                "unknown currency",
            ),
            (
                """
        - type: debit_currency
          actor: trigger_actor
          currency: obol
          amount: 0
""",
                "must be a positive integer",
            ),
            (
                """
        - type: debit_currency
          actor: other_actor
          currency: obol
          amount: 10
""",
                "must be 'trigger_actor'",
            ),
            (
                """
        - type: debit_currency
          actor: trigger_actor
          currency: obol
          amount: 10
          message: custom
""",
                "unsupported field",
            ),
            (
                f"""
        - type: debit_currency
          actor: trigger_actor
          currency: obol
          amount: 10
        - type: grant_item
          actor: trigger_actor
          item: itemdefinition.{late_item.slug}
""",
                "order actions as mutations, then debits, then command/echo output",
            ),
        )

        for action_yaml, expected_error in invalid_actions:
            with self.subTest(expected_error=expected_error):
                manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
{action_yaml}
"""
                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400, resp.data)
                self.assertIn(expected_error, str(resp.data).lower())

    def test_apply_trigger_manifest_supports_typed_harvest_actions(self):
        mature = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-mature",
            name="a bunch of mature barley plants",
        )
        harvested = ItemDefinition.objects.create(
            world=self.world,
            slug="harvested-barley",
            name="a bunch of harvested barley",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: consume_room_item
          room: trigger_room
          item: {mature.id}
        - type: grant_item
          actor: trigger_actor
          item: {harvested.id}
          count: 2
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.steps,
            [
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                            "count": 1,
                        },
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                            "count": 2,
                        },
                    ],
                },
            ],
        )
        self.assertEqual(
            resp.data["trigger"]["manifest"]["spec"]["steps"],
            self.trigger.steps,
        )

    def test_apply_trigger_manifest_normalizes_set_mob_definition_ref(self):
        commander = MobDefinition.objects.create(
            world=self.world,
            slug="captive-commander",
            name="a captive commander",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: set_mob
          room: trigger_room
          mob: {commander.id}
          where:
            eq:
              - state.character.captive
              - true
          fields:
            name: a freed Greek commander
            room_description: A freed commander stands here.
            description: The commander studies the camp.
            attackable: true
          state:
            captive: false
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.steps,
            [
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.captive-commander",
                            "where": {
                                "eq": [
                                    "state.character.captive",
                                    True,
                                ],
                            },
                            "fields": {
                                "name": "a freed Greek commander",
                                "room_description": (
                                    "A freed commander stands here."
                                ),
                                "description": (
                                    "The commander studies the camp."
                                ),
                                "attackable": True,
                            },
                            "state": {
                                "captive": False,
                            },
                        },
                    ],
                },
            ],
        )
        self.assertEqual(
            resp.data["trigger"]["manifest"]["spec"]["steps"],
            self.trigger.steps,
        )

    def test_apply_trigger_manifest_rejects_unknown_set_mob_definition(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: set_mob
          room: trigger_room
          mob: mobdefinition.missing-commander
          fields:
            attackable: true
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown mob definition", str(resp.data))

    def test_apply_trigger_manifest_normalizes_outer_refs_for_set_mob_trigger(self):
        commander = MobDefinition.objects.create(
            world=self.world,
            slug="guarded-commander",
            name="a guarded commander",
        )
        key = ItemDefinition.objects.create(
            world=self.world,
            slug="cage-key",
            name="an iron cage key",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  conditions:
    all:
      - mob_present:
          ref: {commander.id}
      - item_present:
          location: actor_inventory
          item: {key.id}
  steps:
    - after_seconds: 0
      actions:
        - type: set_mob
          room: trigger_room
          mob: {commander.id}
          where:
            eq:
              - state.character.captive
              - true
          fields:
            attackable: true
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        conditions = json.loads(self.trigger.conditions)
        self.assertEqual(
            conditions["all"][0]["mob_present"]["ref"],
            "mobdefinition.guarded-commander",
        )
        self.assertEqual(
            conditions["all"][1]["item_present"]["item"],
            "itemdefinition.cage-key",
        )

    def test_apply_trigger_manifest_rejects_invalid_harvest_action_fields(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-mature",
            name="a bunch of mature barley plants",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="harvested-barley",
            name="a bunch of harvested barley",
        )
        invalid_actions = (
            (
                """
        - type: consume_room_item
          room: nearby_room
          item: itemdefinition.barley-mature
""",
                "room must be 'trigger_room'",
            ),
            (
                """
        - type: grant_item
          actor: nearby_actor
          item: itemdefinition.harvested-barley
""",
                "actor must be 'trigger_actor'",
            ),
            (
                """
        - type: grant_item
          actor: trigger_actor
          item: itemdefinition.harvested-barley
          room: trigger_room
""",
                "unsupported field(s): room",
            ),
        )

        for action_yaml, expected_error in invalid_actions:
            with self.subTest(expected_error=expected_error):
                manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
{action_yaml}
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400)
                self.assertIn(expected_error, str(resp.data))

        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.steps, [])

    def test_partial_trigger_patch_preserves_structured_conditions(self):
        seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        self.trigger.conditions = json.dumps({
            "item_present": {
                "location": "actor_inventory",
                "item": f"itemdefinition.{seed.slug}",
            },
        })
        self.trigger.save(update_fields=["conditions"])
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  gate_delay: 4
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.gate_delay, 4)
        self.assertEqual(
            json.loads(self.trigger.conditions),
            {
                "item_present": {
                    "location": "actor_inventory",
                    "item": "itemdefinition.barley-seed",
                },
            },
        )

    def test_bare_numeric_itemdefinition_ids_normalize_to_portable_slugs(self):
        seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        seedling = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  conditions:
    item_present:
      location: actor_inventory
      item: {seed.id}
  steps:
    - after_seconds: 0
      actions:
        - type: consume_item
          actor: trigger_actor
          item: {seed.id}
        - type: spawn_room_item
          room: trigger_room
          item: {seedling.id}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            json.loads(self.trigger.conditions)["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            self.trigger.steps[0]["actions"][0]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            self.trigger.steps[0]["actions"][1]["item"],
            "itemdefinition.barley-seedling",
        )

    def test_typed_numeric_itemdefinition_slug_is_not_treated_as_a_database_id(self):
        legacy_id_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="legacy-id-definition",
            name="a legacy ID definition",
        )
        numeric_slug = str(legacy_id_definition.id)
        ItemDefinition.objects.create(
            world=self.world,
            slug=numeric_slug,
            name="a numbered seed",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  conditions:
    item_present:
      location: actor_inventory
      item: itemdefinition.{numeric_slug}
  steps:
    - after_seconds: 0
      actions:
        - type: consume_item
          actor: trigger_actor
          item: itemdefinition.{numeric_slug}
        - type: spawn_room_item
          room: trigger_room
          item: item_definition.{numeric_slug}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        expected_ref = f"itemdefinition.{numeric_slug}"
        self.assertEqual(
            json.loads(self.trigger.conditions)["item_present"]["item"],
            expected_ref,
        )
        self.assertEqual(
            self.trigger.steps[0]["actions"][0]["item"],
            expected_ref,
        )
        self.assertEqual(
            self.trigger.steps[0]["actions"][1]["item"],
            expected_ref,
        )

    def test_typed_numeric_mobdefinition_slug_is_not_treated_as_a_database_id(self):
        legacy_id_definition = MobDefinition.objects.create(
            world=self.world,
            slug="legacy-mob-id-definition",
            name="a legacy mob ID definition",
        )
        numeric_slug = str(legacy_id_definition.id)
        MobDefinition.objects.create(
            world=self.world,
            slug=numeric_slug,
            name="a numbered commander",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: set_mob
          room: trigger_room
          mob: mob_definition.{numeric_slug}
          fields:
            attackable: true
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.trigger.refresh_from_db()
        self.assertEqual(
            self.trigger.steps[0]["actions"][0]["mob"],
            f"mobdefinition.{numeric_slug}",
        )

    def test_instance_trigger_steps_resolve_base_world_item_definitions(self):
        seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        instance_world = World.objects.new_world(
            name="Barley Field Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_world.config.starting_room
        apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[instance_world.id],
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{instance_world.id}
  name: Plant Instance Barley
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.{instance_room.id}
  match: plant seed
  conditions:
    item_present:
      location: actor_inventory
      item: {seed.id}
  steps:
    - after_seconds: 0
      actions:
        - type: consume_item
          actor: trigger_actor
          item: {seed.id}
"""

        resp = self.client.post(apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        trigger = Trigger.objects.get(
            world=instance_world,
            name="Plant Instance Barley",
        )
        self.assertEqual(
            json.loads(trigger.conditions)["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            trigger.steps[0]["actions"][0]["item"],
            "itemdefinition.barley-seed",
        )

        # Exercise defensive export canonicalization for older/programmatic
        # rows that still contain numeric refs.
        trigger.conditions = json.dumps({
            "item_present": {
                "location": "actor_inventory",
                "item": seed.id,
            },
        })
        trigger.steps[0]["actions"][0]["item"] = seed.id
        trigger.save(update_fields=["conditions", "steps"])
        export_resp = self.client.get(
            reverse("builder-world-export", args=[instance_world.id])
        )

        self.assertEqual(export_resp.status_code, 200, export_resp.data)
        exported_trigger = next(
            document
            for document in yaml.safe_load_all(export_resp.data["yaml"])
            if document
            and document.get("kind") == "trigger"
            and document.get("metadata", {}).get("name") == "Plant Instance Barley"
        )
        self.assertEqual(
            exported_trigger["spec"]["conditions"]["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            exported_trigger["spec"]["steps"][0]["actions"][0]["item"],
            "itemdefinition.barley-seed",
        )

    def test_instance_trigger_steps_resolve_base_world_currency(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        instance_world = World.objects.new_world(
            name="Toll Gate Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_world.config.starting_room
        apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[instance_world.id],
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{instance_world.id}
  name: Pay Instance Toll
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.{instance_room.id}
  match: pay toll
  steps:
    - after_seconds: 0
      actions:
        - type: debit_currency
          actor: trigger_actor
          currency: {obol.id}
          amount: 10
"""

        resp = self.client.post(
            apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        trigger = Trigger.objects.get(
            world=instance_world,
            name="Pay Instance Toll",
        )
        self.assertEqual(
            trigger.steps[0]["actions"][0]["currency"],
            "obol",
        )

    def test_apply_trigger_manifest_rejects_script_with_steps(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: /echo -- This conflicts.
  steps:
    - after_seconds: 0
      actions:
        - type: spawn_room_item
          room: trigger_room
          item: itemdefinition.barley-seedling
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("alternatives", str(resp.data))

    def test_apply_trigger_manifest_rejects_invalid_step_timing_and_binding(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-growing",
            name="a growing barley plant",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 1
      actions:
        - type: replace_room_item
          target: crop
          with: itemdefinition.barley-growing
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("after_seconds must be 0", str(resp.data))

    def test_apply_trigger_manifest_rejects_unknown_step_binding(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-growing",
            name="a growing barley plant",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
        - type: replace_room_item
          target: crop
          with: itemdefinition.barley-growing
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("binding created by an earlier", str(resp.data))

    def test_apply_trigger_manifest_rejects_non_string_echo_and_excessive_item_count(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        invalid_actions = (
            (
                """
        - type: echo
          room: trigger_room
          text:
            unexpected: mapping
""",
                "text must be a string",
            ),
            (
                f"""
        - type: consume_item
          actor: trigger_actor
          item: itemdefinition.barley-seed
          count: {MAX_TRIGGER_CONSUME_ITEM_COUNT + 1}
""",
                f"cannot exceed {MAX_TRIGGER_CONSUME_ITEM_COUNT}",
            ),
        )

        for action_yaml, expected_error in invalid_actions:
            with self.subTest(expected_error=expected_error):
                manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  script: ""
  steps:
    - after_seconds: 0
      actions:
{action_yaml}
"""
                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400)
                self.assertIn(expected_error, str(resp.data))

    def test_normalized_trigger_steps_have_a_serialized_size_cap(self):
        steps = [
            {
                "after_seconds": 0 if step_index == 0 else 1,
                "actions": [
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "x" * MAX_TRIGGER_ECHO_LENGTH,
                    }
                    for _ in range(16)
                ],
            }
            for step_index in range(5)
        ]

        with self.assertRaisesRegex(
            TriggerStepSpecError,
            f"cannot exceed {MAX_TRIGGER_STEPS_SERIALIZED_BYTES} bytes",
        ):
            normalize_trigger_steps(steps)

    def test_apply_trigger_manifest_without_api_version(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
  name: Trigger Without Version
spec:
  scope: room
  kind: command
  target:
    type: room
    key: {self.room.key}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "trigger")
        self.assertEqual(resp.data["operation"], "updated")
        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.name, "Trigger Without Version")

    def test_apply_trigger_manifest_allows_numeric_world_and_minimal_field_patch(self):
        trigger_name = self.trigger.name
        trigger_scope = self.trigger.scope
        trigger_kind = self.trigger.kind
        trigger_target_id = self.trigger.target_id
        trigger_gate_delay = self.trigger.gate_delay

        manifest = f"""
kind: trigger
metadata:
  world: {self.world.id}
  id: {self.trigger.id}
spec:
  match: inspect mural
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operation"], "updated")

        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.match, "inspect mural")
        self.assertEqual(self.trigger.name, trigger_name)
        self.assertEqual(self.trigger.scope, trigger_scope)
        self.assertEqual(self.trigger.kind, trigger_kind)
        self.assertEqual(self.trigger.target_id, trigger_target_id)
        self.assertEqual(self.trigger.gate_delay, trigger_gate_delay)

    def test_apply_trigger_manifest_can_delete_trigger(self):
        manifest = f"""
kind: trigger
operation: delete
metadata:
  world: world.{self.world.id}
  id: {self.trigger.id}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "trigger")
        self.assertEqual(resp.data["operation"], "deleted")
        self.assertFalse(Trigger.objects.filter(pk=self.trigger.id).exists())

    def test_apply_trigger_manifest_can_create_mob_event_trigger(self):
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Lorekeeper",
        )

        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: Lorekeeper Reaction
spec:
  scope: world
  kind: event
  target:
    type: mobdefinition
    key: mobdefinition.{mob_definition.id}
  event: say
  match: hello and (traveler or friend)
  script: say Welcome, seeker.
  display_action_in_room: false
  gate_delay: 10
  order: 0
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["operation"], "created")

        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.kind, adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(created_trigger.scope, adv_consts.TRIGGER_SCOPE_WORLD)
        self.assertEqual(created_trigger.target_type, ContentType.objects.get_for_model(MobDefinition))
        self.assertEqual(created_trigger.target_id, mob_definition.id)
        self.assertEqual(created_trigger.event, adv_consts.MOB_REACTION_EVENT_SAYING)
        self.assertEqual(created_trigger.match, "hello and (traveler or friend)")

    def test_apply_trigger_manifest_can_create_room_policy_trigger(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: Warlord Gate
spec:
  scope: room
  kind: policy
  target:
    type: room
    key: room.{self.room.id}
  event: before_move_enter
  conditions:
    eq:
      - actor.archetype
      - warlord
  failure_message: Only warlords may enter.
  order: 0
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["operation"], "created")

        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.kind, adv_consts.TRIGGER_KIND_POLICY)
        self.assertEqual(created_trigger.scope, adv_consts.TRIGGER_SCOPE_ROOM)
        self.assertEqual(created_trigger.target_type, ContentType.objects.get_for_model(Room))
        self.assertEqual(created_trigger.target_id, self.room.id)
        self.assertEqual(created_trigger.event, adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER)
        self.assertIn("actor.archetype", created_trigger.conditions)
        self.assertFalse(created_trigger.display_action_in_room)

    def test_apply_trigger_manifest_can_create_mob_guarded_exit_policy(self):
        MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: Guard Blocks East
spec:
  scope: room
  kind: policy
  target:
    type: room
    key: room.{self.room.id}
  event: before_move_exit
  match: east
  conditions:
    not:
      mob_present: mobdefinition.east-gate-guard
  failure_message: The guard bars the eastern way.
  order: 0
  is_active: true
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.match, "east")
        self.assertEqual(
            yaml.safe_load(created_trigger.conditions),
            {
                "not": {
                    "mob_present": "mobdefinition.east-gate-guard",
                },
            },
        )

    def test_apply_trigger_manifest_can_create_room_movement_event_trigger(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: Spear Trap
spec:
  scope: room
  kind: event
  target:
    type: room
    key: room.{self.room.id}
  event: after_move_enter
  conditions:
    not:
      eq:
        - state.room.trap_sprung
        - true
  script: |
    /cmd room -- /echo -- Spears snap out from the walls.
    /cmd room -- /state set room trap_sprung true
  order: 0
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["operation"], "created")

        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.kind, adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(created_trigger.scope, adv_consts.TRIGGER_SCOPE_ROOM)
        self.assertEqual(created_trigger.target_type, ContentType.objects.get_for_model(Room))
        self.assertEqual(created_trigger.target_id, self.room.id)
        self.assertEqual(created_trigger.event, adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER)
        self.assertIn("/cmd room -- /echo", created_trigger.script)
        self.assertFalse(created_trigger.display_action_in_room)

    def test_apply_trigger_manifest_can_create_death_room_event_trigger(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: Death Room Arrival
spec:
  scope: room
  kind: event
  target:
    type: room
    key: room.{self.room.id}
  event: after_death_room_enter
  script: |
    /cmd room -- /echo -- Death releases you into the quiet chamber.
  order: 0
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["operation"], "created")

        created_trigger = Trigger.objects.get(pk=resp.data["trigger"]["id"])
        self.assertEqual(created_trigger.kind, adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(created_trigger.scope, adv_consts.TRIGGER_SCOPE_ROOM)
        self.assertEqual(created_trigger.target_type, ContentType.objects.get_for_model(Room))
        self.assertEqual(created_trigger.target_id, self.room.id)
        self.assertEqual(created_trigger.event, adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER)
        self.assertIn("Death releases", created_trigger.script)

    def test_apply_trigger_manifest_invalidates_cached_room_event_hooks(self):
        _, cached_room_hooks, room_hook_cache_key = self._trigger_hook_cache_helpers()
        old_script = "/cmd room -- /echo -- Old pit script."
        new_script = "/cmd room -- /echo -- New pit script."
        room_ct = ContentType.objects.get_for_model(Room)
        event_trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_ct,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
            script=old_script,
            display_action_in_room=False,
        )
        old_cache_key = room_hook_cache_key(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        )
        cache.delete(old_cache_key)

        cached_hooks = cached_room_hooks(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        )
        self.assertEqual(cached_hooks[0]["script"], old_script)
        old_version = get_trigger_policy_cache_version(self.world.id)

        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {event_trigger.key}
spec:
  scope: room
  kind: event
  target:
    type: room
    key: {self.room.key}
  event: after_move_enter
  script: {new_script}
  display_action_in_room: false
  is_active: true
"""
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                self.apply_ep,
                {"manifest": manifest},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operation"], "updated")
        self.assertGreater(
            get_trigger_policy_cache_version(self.world.id),
            old_version,
        )
        refreshed_hooks = cached_room_hooks(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        )
        self.assertEqual(refreshed_hooks[0]["script"], new_script)

    def test_cached_room_hook_stores_only_typed_step_marker(self):
        _, cached_room_hooks, room_hook_cache_key = self._trigger_hook_cache_helpers()
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_ct,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
            script="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The room changes.",
                        },
                    ],
                },
            ],
        )
        cache_key = room_hook_cache_key(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        )
        cache.delete(cache_key)

        hooks = cached_room_hooks(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        )

        self.assertIs(hooks[0]["steps"], True)
        self.assertNotIn("actions", hooks[0])

    def test_trigger_policy_cache_does_not_reuse_legacy_version_namespace(self):
        cache_key = trigger_policy_cache_version_key(self.world.id)
        cache.set(cache_key, 1, timeout=None)

        version = get_trigger_policy_cache_version(self.world.id)

        self.assertGreaterEqual(version, TRIGGER_POLICY_CACHE_VERSION_FLOOR)

    def test_trigger_policy_cache_bump_promotes_legacy_version_namespace(self):
        cache_key = trigger_policy_cache_version_key(self.world.id)
        cache.set(cache_key, 1, timeout=None)

        bump_trigger_policy_cache_version(self.world.id)

        self.assertGreaterEqual(
            get_trigger_policy_cache_version(self.world.id),
            TRIGGER_POLICY_CACHE_VERSION_FLOOR,
        )

    def test_cached_room_hooks_use_finite_timeout(self):
        (
            hook_cache_timeout,
            cached_room_hooks,
            room_hook_cache_key,
        ) = self._trigger_hook_cache_helpers()
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_ct,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
            script="/cmd room -- /echo -- Death room hook.",
            display_action_in_room=False,
        )
        cache_key = room_hook_cache_key(
            world_id=self.world.id,
            room_id=self.room.id,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            event=adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
        )
        cache.delete(cache_key)

        with patch("spawns.triggers.cache.set") as cache_set:
            cached_room_hooks(
                world_id=self.world.id,
                room_id=self.room.id,
                kind=adv_consts.TRIGGER_KIND_EVENT,
                event=adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
            )

        self.assertEqual(cache_set.call_args.kwargs["timeout"], hook_cache_timeout)

    def test_apply_trigger_manifest_rejects_policy_outside_room_scope(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  name: World Policy
spec:
  scope: world
  kind: policy
  target:
    type: world
    key: world.{self.world.id}
  event: before_move_enter
  conditions:
    always: true
  failure_message: No.
  is_active: true
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("scope 'room'", str(resp.data))

    def test_apply_trigger_manifest_rejects_invalid_matcher_expression(self):
        manifest = f"""
kind: trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  match: touch altar and (pray or
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("matcher expression", str(resp.data).lower())

    def test_rank_2_builder_needs_assignment_to_apply_room_trigger_manifest(self):
        builder_user = self.create_user("builder@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        manifest = f"""
apiVersion: writtenrealms.com/v1alpha1
kind: Trigger
metadata:
  world: world.{self.world.id}
  key: {self.trigger.key}
spec:
  scope: room
  kind: command
  target:
    type: room
    key: {self.room.key}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.room,
        )
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        delete_manifest = f"""
kind: trigger
operation: delete
metadata:
  world: world.{self.world.id}
  id: {self.trigger.id}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": delete_manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Trigger.objects.filter(pk=self.trigger.id).exists())
