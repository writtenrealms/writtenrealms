import yaml

from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    BuilderAssignment,
    MerchantProfile,
    WorldBuilder,
)
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig


class RoomMerchantBuilderTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
        )
        self.profile = MerchantProfile.objects.create(
            world=self.world,
            slug="grand-bazaar",
            name="The Grand Bazaar",
            settlement_currency=self.currency,
        )
        self.apply_endpoint = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )
        self.room_manifest_endpoint = reverse(
            "builder-room-manifest",
            args=[self.world.pk, self.room.pk],
        )
        self.room_config_endpoint = reverse(
            "builder-room-config",
            args=[self.world.pk, self.room.pk],
        )

    def _room_manifest(self):
        response = self.client.get(self.room_manifest_endpoint)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["manifest"]

    def _apply_room_manifest(self, manifest, *, expected_status=200):
        response = self.client.post(
            self.apply_endpoint,
            {"manifest": yaml.safe_dump(manifest, sort_keys=False)},
            format="json",
        )
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def test_room_manifest_attaches_exports_and_clears_merchant_profile(self):
        manifest = self._room_manifest()
        manifest["spec"]["merchant"] = {
            "profile": "merchantprofile.grand-bazaar",
        }

        response = self._apply_room_manifest(manifest)
        self.assertEqual(response.data["kind"], "room")
        self.room.refresh_from_db()
        self.assertEqual(self.room.merchant_profile, self.profile)

        canonical = self._room_manifest()
        self.assertEqual(
            canonical["spec"]["merchant"]["profile"],
            "merchantprofile.grand-bazaar",
        )

        export_response = self.client.get(
            reverse("builder-world-export", args=[self.world.pk])
        )
        self.assertEqual(export_response.status_code, 200, export_response.data)
        documents = export_response.data["documents"]
        profile_index = next(
            index
            for index, document in enumerate(documents)
            if document.get("kind") == "merchantprofile"
        )
        room_index = next(
            index
            for index, document in enumerate(documents)
            if document.get("kind") == "room"
            and document.get("metadata", {}).get("ref")
            == f"room@{self.room.relative_id}"
        )
        self.assertLess(profile_index, room_index)
        self.assertEqual(
            documents[room_index]["spec"]["merchant"]["profile"],
            "merchantprofile.grand-bazaar",
        )

        canonical["spec"]["merchant"] = None
        self._apply_room_manifest(canonical)
        self.room.refresh_from_db()
        self.assertIsNone(self.room.merchant_profile)
        self.assertNotIn("merchant", self._room_manifest()["spec"])

    def test_room_manifest_preserves_merchant_when_field_is_omitted(self):
        self.room.merchant_profile = self.profile
        self.room.save(update_fields=["merchant_profile", "modified_ts"])
        manifest = self._room_manifest()
        manifest["spec"].pop("merchant")
        manifest["spec"]["description"] = "A market beneath striped awnings."

        self._apply_room_manifest(manifest)

        self.room.refresh_from_db()
        self.assertEqual(self.room.merchant_profile, self.profile)
        self.assertEqual(
            self.room.description,
            "A market beneath striped awnings.",
        )

    def test_room_manifest_rejects_unknown_and_cross_world_profiles(self):
        manifest = self._room_manifest()
        manifest["spec"]["merchant"] = {
            "profile": "merchantprofile.missing-shop",
        }
        self._apply_room_manifest(manifest, expected_status=400)

        other_world = World.objects.new_world(
            name="Other World",
            author=self.create_user("other-merchant-world@example.com"),
            config=WorldConfig.objects.create(),
        )
        other_currency = create_currency(
            world=other_world,
            code="crown",
            name="Crown",
        )
        other_profile = MerchantProfile.objects.create(
            world=other_world,
            slug="foreign-shop",
            name="Foreign Shop",
            settlement_currency=other_currency,
        )
        manifest["spec"]["merchant"] = {"profile": other_profile.id}
        self._apply_room_manifest(manifest, expected_status=400)

        manifest["spec"]["merchant"] = {
            "profile": "merchantprofile.grand-bazaar",
            "availability": "present",
        }
        response = self._apply_room_manifest(manifest, expected_status=400)
        self.assertIn("Unsupported spec.merchant field", str(response.data))

    def test_room_config_attaches_and_clears_profile_atomically(self):
        get_response = self.client.get(self.room_config_endpoint)
        self.assertEqual(get_response.status_code, 200, get_response.data)
        self.assertTrue(get_response.data["can_edit"])
        self.assertIsNone(get_response.data["merchant_profile"])

        attach_response = self.client.patch(
            self.room_config_endpoint,
            {"merchant_profile": self.profile.id},
            format="json",
        )
        self.assertEqual(attach_response.status_code, 200, attach_response.data)
        self.assertEqual(
            attach_response.data["merchant_profile"]["slug"],
            "grand-bazaar",
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.merchant_profile, self.profile)

        clear_response = self.client.patch(
            self.room_config_endpoint,
            {"merchant_profile": None},
            format="json",
        )
        self.assertEqual(clear_response.status_code, 200, clear_response.data)
        self.assertIsNone(clear_response.data["merchant_profile"])
        self.room.refresh_from_db()
        self.assertIsNone(self.room.merchant_profile)

    def test_room_config_rejects_profile_from_another_world(self):
        other_world = World.objects.new_world(
            name="Other World",
            author=self.create_user("config-other-world@example.com"),
            config=WorldConfig.objects.create(),
        )
        other_currency = create_currency(
            world=other_world,
            code="crown",
            name="Crown",
        )
        other_profile = MerchantProfile.objects.create(
            world=other_world,
            slug="other-shop",
            name="Other Shop",
            settlement_currency=other_currency,
        )

        response = self.client.patch(
            self.room_config_endpoint,
            {"merchant_profile": other_profile.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.room.refresh_from_db()
        self.assertIsNone(self.room.merchant_profile)

    def test_room_config_enforces_room_assignment(self):
        builder_user = self.create_user("assigned-room-merchant@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        get_response = self.client.get(self.room_config_endpoint)
        self.assertEqual(get_response.status_code, 200, get_response.data)
        self.assertFalse(get_response.data["can_edit"])
        denied_response = self.client.patch(
            self.room_config_endpoint,
            {"merchant_profile": self.profile.id},
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403, denied_response.data)

        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.room,
        )
        allowed_response = self.client.patch(
            self.room_config_endpoint,
            {"merchant_profile": self.profile.id},
            format="json",
        )
        self.assertEqual(allowed_response.status_code, 200, allowed_response.data)
        self.assertEqual(
            allowed_response.data["merchant_profile"]["id"],
            self.profile.id,
        )

    def test_instance_room_can_attach_inherited_profile(self):
        instance_world = World.objects.new_world(
            name="Market Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_world.rooms.get(relative_id=1)
        endpoint = reverse(
            "builder-room-config",
            args=[instance_world.pk, instance_room.pk],
        )

        response = self.client.patch(
            endpoint,
            {"merchant_profile": self.profile.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        instance_room.refresh_from_db()
        self.assertEqual(instance_room.merchant_profile, self.profile)
        self.assertEqual(
            response.data["merchant_profile_world"]["id"],
            self.world.id,
        )
