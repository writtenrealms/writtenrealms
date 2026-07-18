import yaml

from django.urls import reverse
from rest_framework import serializers

from builders import manifests
from builders import world_export
from builders.models import MobDefinition, Social
from tests.base import WorldTestCase
from worlds.models import WorldConfig


class TestSocialManifests(WorldTestCase):
    def _manifest(self, *, command="wave", priority=10):
        return {
            "kind": "social",
            "metadata": {
                "command": command,
            },
            "spec": {
                "priority": priority,
                "targetless": {
                    "self": "You wave.",
                    "others": "{{ actor }} waves.",
                },
                "targeted": {
                    "self": "You wave at {{ target }}.",
                    "target": "{{ actor }} waves at you.",
                    "others": "{{ actor }} waves at {{ target }}.",
                },
            },
        }

    def _create_social(self, *, command="wave", priority=10):
        manifest = self._manifest(command=command, priority=priority)
        social, created = world_export.apply_social_manifest(
            world=self.world,
            manifest=manifest,
        )
        self.assertTrue(created)
        return social

    def test_apply_uses_lowercase_command_as_portable_upsert_identity(self):
        social, created = world_export.apply_social_manifest(
            world=self.world,
            manifest=self._manifest(command="  WaVe  ", priority="12"),
        )

        self.assertTrue(created)
        self.assertEqual(social.cmd, "wave")
        self.assertEqual(social.priority, 12)
        self.assertEqual(social.msg_targetless_self, "You wave.")
        self.assertEqual(social.msg_targeted_target, "{{ actor }} waves at you.")

        update_manifest = {
            "kind": "SOCIAL",
            "metadata": {"command": "WAVE"},
            "spec": {
                "priority": 25,
                "targetless": {
                    "self": "You wave enthusiastically.",
                    "others": "{{ actor }} waves enthusiastically.",
                },
            },
        }
        updated, created = world_export.apply_social_manifest(
            world=self.world,
            manifest=update_manifest,
        )

        self.assertFalse(created)
        self.assertEqual(updated.pk, social.pk)
        self.assertEqual(Social.objects.filter(world=self.world).count(), 1)
        self.assertEqual(updated.cmd, "wave")
        self.assertEqual(updated.priority, 25)
        self.assertEqual(
            updated.msg_targetless_self,
            "You wave enthusiastically.",
        )
        self.assertEqual(
            updated.msg_targeted_other,
            "{{ actor }} waves at {{ target }}.",
        )

    def test_apply_can_clear_one_complete_message_group(self):
        social = self._create_social()

        updated, created = world_export.apply_social_manifest(
            world=self.world,
            manifest={
                "kind": "social",
                "metadata": {"command": "wave"},
                "spec": {"targetless": None},
            },
        )

        self.assertFalse(created)
        self.assertEqual(updated.pk, social.pk)
        self.assertEqual(updated.msg_targetless_self, "")
        self.assertEqual(updated.msg_targetless_other, "")
        self.assertEqual(updated.msg_targeted_self, "You wave at {{ target }}.")

    def test_apply_rejects_incomplete_groups_and_unsafe_templates(self):
        incomplete = self._manifest()
        incomplete["spec"]["targeted"].pop("others")
        with self.assertRaisesRegex(
            serializers.ValidationError,
            "Targeted socials require self, target, and other messages",
        ):
            world_export.apply_social_manifest(
                world=self.world,
                manifest=incomplete,
            )

        unsafe = self._manifest(command="unsafe")
        unsafe["spec"]["targetless"]["others"] = "{{ target }} waves."
        with self.assertRaisesRegex(
            serializers.ValidationError,
            "unsupported template variable",
        ):
            world_export.apply_social_manifest(
                world=self.world,
                manifest=unsafe,
            )

    def test_identity_fields_must_agree_and_world_must_match(self):
        social = self._create_social(command="wave")
        other = self._create_social(command="nod")

        mismatched = self._manifest(command="wave")
        mismatched["metadata"].update({
            "id": other.id,
            "key": f"social.{other.id}",
        })
        with self.assertRaisesRegex(
            serializers.ValidationError,
            "refer to different socials",
        ):
            manifests.parse_social_manifest(
                world=self.world,
                manifest=mismatched,
            )

        wrong_world = self._manifest(command="wave")
        wrong_world["metadata"].update({
            "world": f"world.{self.world.id + 1000}",
            "id": social.id,
        })
        with self.assertRaisesRegex(
            serializers.ValidationError,
            "Manifest world does not match",
        ):
            manifests.parse_social_manifest(
                world=self.world,
                manifest=wrong_world,
            )

    def test_detail_serializer_includes_database_identity_and_delete_yaml(self):
        social = self._create_social(command="WAVE")

        payload = manifests.serialize_social_payload(social)

        self.assertEqual(payload["id"], social.id)
        self.assertEqual(payload["key"], f"social.{social.id}")
        self.assertEqual(payload["world"], f"world.{self.world.id}")
        self.assertEqual(
            payload["manifest"]["metadata"],
            {
                "world": f"world.{self.world.id}",
                "id": social.id,
                "key": f"social.{social.id}",
                "command": "wave",
            },
        )
        self.assertEqual(
            payload["manifest"]["spec"]["targetless"],
            {
                "self": "You wave.",
                "others": "{{ actor }} waves.",
            },
        )
        self.assertEqual(payload["delete_manifest"]["operation"], "delete")
        self.assertEqual(
            yaml.safe_load(payload["delete_yaml"]),
            payload["delete_manifest"],
        )

    def test_delete_resolves_portable_command_and_rejects_a_spec(self):
        social = self._create_social(command="wave")
        delete_manifest = {
            "kind": "social",
            "operation": "delete",
            "metadata": {"command": "WAVE"},
        }

        deleted = world_export.delete_social_manifest(
            world=self.world,
            manifest=delete_manifest,
        )

        self.assertEqual(deleted._deleted_payload["id"], social.pk)
        self.assertFalse(Social.objects.filter(pk=social.pk).exists())

        social = self._create_social(command="wave")
        invalid_delete = {
            **delete_manifest,
            "metadata": {
                "command": "wave",
                "id": social.id,
                "key": f"social.{social.id}",
            },
            "spec": {"priority": 0},
        }
        with self.assertRaisesRegex(
            serializers.ValidationError,
            "spec is not allowed",
        ):
            world_export.delete_social_manifest(
                world=self.world,
                manifest=invalid_delete,
            )

    def test_full_world_export_is_portable_deterministic_and_importable(self):
        self._create_social(command="zeta", priority=30)
        self._create_social(command="alpha", priority=5)

        first_export = world_export.serialize_world_export_payload(self.world)
        second_export = world_export.serialize_world_export_payload(self.world)
        social_documents = [
            document
            for document in first_export["documents"]
            if document["kind"] == "social"
        ]

        self.assertEqual(first_export["yaml"], second_export["yaml"])
        self.assertEqual(first_export["summary"]["socials"], 2)
        self.assertEqual(
            [document["metadata"]["command"] for document in social_documents],
            ["alpha", "zeta"],
        )
        for document in social_documents:
            self.assertEqual(set(document["metadata"]), {"command"})
            self.assertEqual(
                set(document["spec"]),
                {"priority", "targetless", "targeted"},
            )

        target_world = self.world.__class__.objects.new_world(
            name="Social Import Target",
            author=self.user,
            config=WorldConfig.objects.create(),
        )
        yaml_documents = manifests.load_yaml_documents(first_export["yaml"])
        imported_social_documents = [
            document
            for document in yaml_documents
            if world_export.parse_document_kind(document) == "social"
        ]
        for document in imported_social_documents:
            world_export.apply_social_manifest(
                world=target_world,
                manifest=document,
            )

        self.assertEqual(
            list(
                target_world.socials.order_by("cmd").values_list(
                    "cmd",
                    "priority",
                )
            ),
            [("alpha", 5), ("zeta", 30)],
        )

    def test_social_mob_reaction_trigger_requires_an_exact_match_expression(self):
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="greeter",
            name="a greeter",
        )
        trigger_manifest = {
            "kind": "trigger",
            "metadata": {
                "world": f"world.{self.world.id}",
                "name": "Wave Response",
            },
            "spec": {
                "scope": "world",
                "kind": "event",
                "target": {
                    "type": "mobdefinition",
                    "key": f"mobdefinition.{mob_definition.id}",
                },
                "event": "social",
                "script": "say Hello there.",
            },
        }

        with self.assertRaisesRegex(
            serializers.ValidationError,
            "spec.match is required for event 'social'",
        ):
            manifests.parse_trigger_manifest(
                world=self.world,
                manifest=trigger_manifest,
            )

        trigger_manifest["spec"]["match"] = "wave"
        parsed = manifests.parse_trigger_manifest(
            world=self.world,
            manifest=trigger_manifest,
        )
        self.assertEqual(parsed.event, "social")
        self.assertEqual(parsed.match, "wave")

    def test_manifest_endpoint_applies_and_deletes_a_social(self):
        self.client.force_authenticate(self.user)
        endpoint = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

        response = self.client.post(
            endpoint,
            {"manifest": yaml.safe_dump(self._manifest(command="wave"))},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["operation"], "created")
        self.assertEqual(response.data["social"]["command"], "wave")
        social = Social.objects.get(world=self.world, cmd="wave")

        response = self.client.post(
            endpoint,
            {
                "manifest": yaml.safe_dump(
                    manifests.social_delete_manifest(social)
                ),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["operation"], "deleted")
        self.assertFalse(Social.objects.filter(pk=social.pk).exists())

    def test_manifest_endpoint_rejects_instance_owned_socials(self):
        instance_world = self.world.__class__.objects.new_world(
            name="Social Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        self.client.force_authenticate(self.user)
        endpoint = reverse(
            "builder-world-manifest-apply",
            args=[instance_world.pk],
        )

        response = self.client.post(
            endpoint,
            {"manifest": yaml.safe_dump(self._manifest(command="wave"))},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("inherited from the base world", str(response.data))
        self.assertFalse(Social.objects.filter(world=instance_world).exists())
