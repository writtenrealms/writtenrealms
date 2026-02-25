import yaml

from rest_framework.reverse import reverse

from builders.models import WorldBuilder
from tests.base import WorldTestCase
from worlds.models import Room


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestWorldConfigManifests(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.config_ep = reverse(
            "builder-world-config",
            args=[self.world.pk],
        )
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

    def test_world_config_endpoint_includes_manifest_yaml(self):
        resp = self.client.get(self.config_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("world", resp.data)
        self.assertIn("config", resp.data)
        self.assertIn("manifest", resp.data)
        self.assertIn("yaml", resp.data)
        self.assertEqual(resp.data["world"]["id"], self.world.id)
        self.assertEqual(resp.data["world"]["name"], self.world.name)
        self.assertEqual(resp.data["config"]["starting_room"]["id"], self.room.id)

        manifest = yaml.safe_load(resp.data["yaml"])
        self.assertEqual(manifest["kind"], "worldconfig")
        self.assertEqual(manifest["metadata"]["world"], f"world.{self.world.id}")
        self.assertEqual(manifest["spec"]["name"], self.world.name)
        self.assertEqual(manifest["spec"]["starting_room"], f"room.{self.room.id}")

    def test_apply_world_config_manifest_updates_world_and_config(self):
        spawn_world = self.world.spawned_worlds.first()
        self.assertIsNotNone(spawn_world)

        starting_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Arrival Hall",
            x=1,
            y=0,
            z=0,
        )
        death_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Infirmary",
            x=2,
            y=0,
            z=0,
        )

        manifest = f"""
kind: worldconfig
metadata:
  world: world.{self.world.id}
spec:
  name: Manifest Updated World
  description: Updated via YAML
  motd: Manifest update complete.
  is_public: true
  starting_gold: 15
  starting_room: room.{starting_room.id}
  death_room: room.{death_room.id}
  death_mode: lose_gold
  death_route: nearest_in_zone
  pvp_mode: zone
  can_select_faction: false
  auto_equip: false
  is_narrative: true
  players_can_set_title: false
  allow_pvp: false
  is_classless: true
  non_ascii_names: true
  globals_enabled: false
  decay_glory: true
  built_by: Manifest Team
  small_background: https://assets.example/card.png
  large_background: https://assets.example/banner.png
  name_exclusions: |
    admin
    system
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "worldconfig")
        self.assertEqual(resp.data["operation"], "updated")
        self.assertIn("world_config", resp.data)
        self.assertIn("yaml", resp.data["world_config"])

        self.world.refresh_from_db()
        self.world.config.refresh_from_db()
        spawn_world.refresh_from_db()

        self.assertEqual(self.world.name, "Manifest Updated World")
        self.assertEqual(self.world.description, "Updated via YAML")
        self.assertEqual(self.world.motd, "Manifest update complete.")
        self.assertTrue(self.world.is_public)

        self.assertEqual(spawn_world.name, "Manifest Updated World")
        self.assertEqual(spawn_world.description, "Updated via YAML")
        self.assertEqual(spawn_world.motd, "Manifest update complete.")
        self.assertTrue(spawn_world.is_public)

        config = self.world.config
        self.assertEqual(config.starting_gold, 15)
        self.assertEqual(config.starting_room_id, starting_room.id)
        self.assertEqual(config.death_room_id, death_room.id)
        self.assertEqual(config.death_mode, "lose_gold")
        self.assertEqual(config.death_route, "nearest_in_zone")
        self.assertEqual(config.pvp_mode, "zone")
        self.assertFalse(config.can_select_faction)
        self.assertFalse(config.auto_equip)
        self.assertTrue(config.is_narrative)
        self.assertFalse(config.allow_combat)
        self.assertFalse(config.players_can_set_title)
        self.assertFalse(config.allow_pvp)
        self.assertTrue(config.is_classless)
        self.assertTrue(config.non_ascii_names)
        self.assertFalse(config.globals_enabled)
        self.assertTrue(config.decay_glory)
        self.assertEqual(config.built_by, "Manifest Team")
        self.assertEqual(config.small_background, "https://assets.example/card.png")
        self.assertEqual(config.large_background, "https://assets.example/banner.png")
        self.assertEqual(config.name_exclusions.strip().splitlines(), ["admin", "system"])

    def test_rank_2_builder_cannot_apply_world_config_manifest(self):
        builder_user = self.create_user("rank2-builder@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        manifest = f"""
kind: worldconfig
metadata:
  world: world.{self.world.id}
spec:
  starting_gold: 123
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
