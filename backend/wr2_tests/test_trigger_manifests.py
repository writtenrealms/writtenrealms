import yaml

from django.contrib.contenttypes.models import ContentType

from rest_framework.reverse import reverse

from builders.models import BuilderAssignment, Trigger, WorldBuilder
from config import constants as adv_consts
from tests.base import WorldTestCase
from worlds.models import Room


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestTriggerManifests(AuthenticatedBuilderWorldTestCase):
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
            actions="touch stone",
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
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

    def test_room_trigger_list_includes_yaml_manifest(self):
        resp = self.client.get(self.list_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["triggers"]), 1)
        self.assertIn("new_trigger_template", resp.data)

        trigger_data = resp.data["triggers"][0]
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
        self.assertIn("actions", template_manifest["spec"])
        self.assertIn("script", template_manifest["spec"])

        parsed_template_yaml = yaml.safe_load(template["yaml"])
        self.assertEqual(parsed_template_yaml["kind"], "trigger")
        self.assertEqual(parsed_template_yaml["metadata"]["world"], f"world.{self.world.id}")
        self.assertEqual(parsed_template_yaml["spec"]["target"]["key"], f"room.{self.room.id}")

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
  actions: pull lever or pull chain
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
        self.assertEqual(self.trigger.actions, "pull lever or pull chain")
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
  actions: touch statue
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
        self.assertEqual(created_trigger.actions, "touch statue")
        self.assertEqual(
            created_trigger.script,
            "/cmd room -- /echo -- The statue vibrates.",
        )
        self.assertEqual(created_trigger.conditions, "level 1")
        self.assertEqual(created_trigger.gate_delay, 3)
        self.assertEqual(created_trigger.order, 12)
        self.assertTrue(created_trigger.is_active)

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
  actions: inspect mural
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operation"], "updated")

        self.trigger.refresh_from_db()
        self.assertEqual(self.trigger.actions, "inspect mural")
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
