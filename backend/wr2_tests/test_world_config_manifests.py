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
        self.export_ep = reverse(
            "builder-world-export",
            args=[self.world.pk],
        )
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )

    def test_world_config_endpoint_matches_export_world_document(self):
        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)
        self.assertIn("world", config_resp.data)
        self.assertIn("config", config_resp.data)
        self.assertIn("manifest", config_resp.data)
        self.assertIn("yaml", config_resp.data)
        self.assertEqual(config_resp.data["world"]["id"], self.world.id)
        self.assertEqual(config_resp.data["world"]["name"], self.world.name)
        self.assertEqual(config_resp.data["config"]["starting_room"]["id"], self.room.id)
        self.assertEqual(config_resp.data["config"]["starting_level"], 1)
        self.assertEqual(config_resp.data["config"]["max_level"], 20)
        self.assertEqual(config_resp.data["config"]["leveling_curve"][1], 30)
        self.assertEqual(config_resp.data["config"]["combat_resolution_interval"], 0)
        self.assertEqual(config_resp.data["config"]["stat_system"], {})
        self.assertEqual(config_resp.data["config"]["combat_system"], {})

        export_resp = self.client.get(self.export_ep)
        self.assertEqual(export_resp.status_code, 200)
        export_docs = [doc for doc in yaml.safe_load_all(export_resp.data["yaml"]) if doc is not None]
        world_manifest = export_docs[-1]

        self.assertEqual(config_resp.data["manifest"], world_manifest)
        self.assertEqual(yaml.safe_load(config_resp.data["yaml"]), world_manifest)
        self.assertEqual(world_manifest["kind"], "world")
        self.assertNotIn("metadata", world_manifest)
        self.assertEqual(world_manifest["spec"]["name"], self.world.name)
        self.assertEqual(world_manifest["spec"]["starting_level"], 1)
        self.assertEqual(world_manifest["spec"]["max_level"], 20)
        self.assertEqual(world_manifest["spec"]["leveling_curve"][1], 30)
        self.assertEqual(world_manifest["spec"]["starting_room"], "room@0,0,0")
        self.assertEqual(world_manifest["spec"]["death_room"], "room@0,0,0")
        self.assertEqual(world_manifest["spec"]["combat_resolution_interval"], 0)
        self.assertNotIn("is_classless", world_manifest["spec"])
        self.assertNotIn("stats", world_manifest["spec"])
        self.assertNotIn("combat", world_manifest["spec"])

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
kind: world
metadata:
  world: world.{self.world.id}
spec:
  name: Manifest Updated World
  description: Updated via YAML
  motd: Manifest update complete.
  is_public: true
  starting_gold: 15
  starting_level: 2
  max_level: 5
  leveling_curve: [0, 10, 30, 60, 100]
  combat_resolution_interval: 1.5
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
  non_ascii_names: true
  globals_enabled: false
  decay_glory: true
  built_by: Manifest Team
  small_background: https://assets.example/card.png
  large_background: https://assets.example/banner.png
  name_exclusions: |
    admin
    system
  combat:
    variance:
      enabled: false
      percent: 0
    profiles:
      basic_physical:
        power_scale: 0.5
        mitigation:
          resilience: false
  stats:
    labels:
      resources:
        energy: Focus
      derived:
        ability_power: Ability Power
    input_attributes:
      - key: grit
        label: Grit
      - key: brawn
        label: Brawn
      - key: grace
        label: Grace
      - key: willpower
        label: Willpower
      - key: insight
        label: Awareness
    class_profiles:
      warrior:
        label: Vanguard
        main_attribute: brawn
        base_attribute_weights:
          grit: 3
          brawn: 4
          grace: 1
          willpower: 1
          insight: 2
    formulas:
      global_rules:
        - source: grit
          target: health_max
          multiplier: 2
        - source: grit
          target: resilience
          multiplier: 1
        - source: brawn
          target: attack_power
          multiplier: 1
        - source: brawn
          target: health_max
          multiplier: 1
        - source: grace
          target: dodge
          multiplier: 1
        - source: grace
          target: crit
          multiplier: 1
        - source: willpower
          target: ability_power
          multiplier: 2
        - source: insight
          target: energy_max
          multiplier: 2
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["kind"], "world")
        self.assertEqual(resp.data["operation"], "updated")

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
        self.assertEqual(config.starting_level, 2)
        self.assertEqual(config.max_level, 5)
        self.assertEqual(config.leveling_curve, [0, 10, 30, 60, 100])
        self.assertEqual(config.combat_resolution_interval, 1.5)
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
        self.assertFalse(config.is_classless)
        self.assertTrue(config.non_ascii_names)
        self.assertFalse(config.globals_enabled)
        self.assertTrue(config.decay_glory)
        self.assertEqual(config.built_by, "Manifest Team")
        self.assertEqual(config.small_background, "https://assets.example/card.png")
        self.assertEqual(config.large_background, "https://assets.example/banner.png")
        self.assertEqual(config.name_exclusions.strip().splitlines(), ["admin", "system"])
        self.assertEqual(config.stat_system["labels"]["resources"]["energy"], "Focus")
        self.assertEqual(
            config.stat_system["labels"]["derived"]["ability_power"],
            "Ability Power",
        )
        self.assertFalse(config.combat_system["variance"]["enabled"])
        self.assertEqual(
            config.combat_system["profiles"]["basic_physical"]["power_scale"],
            0.5,
        )
        self.assertEqual(
            config.stat_system["labels"]["classes"]["warrior"],
            "Vanguard",
        )
        primary_keys = [
            entry["key"] for entry in config.stat_system["input_attributes"]
        ]
        self.assertIn("insight", primary_keys)

    def test_apply_world_config_manifest_accepts_async_combat_pacing_sentinel(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  combat_resolution_interval: -1
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.combat_resolution_interval, -1)

    def test_apply_world_config_manifest_rejects_invalid_leveling_curve(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  max_level: 4
  leveling_curve: [0, 30, 30]
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("strictly increase", str(resp.data))

    def test_apply_world_config_manifest_rejects_max_above_curve_length(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  max_level: 4
  leveling_curve: [0, 30, 100]
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("max_level", str(resp.data))

    def test_empty_stats_create_clean_world_without_class_profiles(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  stats:
    default_profile:
      label: ""
      main_attribute: ''
      base_attribute_weights: {{}}
      derived_rules: []
    class_profiles: {{}}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.world.config.refresh_from_db()
        self.assertTrue(self.world.config.is_classless)
        self.assertEqual(self.world.config.stat_system["class_profiles"], {})
        self.assertEqual(self.world.config.stat_system["labels"]["classes"], {})

        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)
        exported = yaml.safe_load(config_resp.data["yaml"])
        self.assertEqual(exported["spec"]["stats"]["class_profiles"], {})
        self.assertEqual(exported["spec"]["stats"]["labels"]["classes"], {})

    def test_apply_exported_world_config_yaml_round_trips_unchanged(self):
        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)

        manifest_yaml = config_resp.data["yaml"]

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest_yaml},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "world")
        self.assertEqual(resp.data["operation"], "updated")

    def test_apply_world_config_manifest_rejects_legacy_kind(self):
        manifest = f"""
kind: worldconfig
metadata:
  world: world.{self.world.id}
spec:
  combat_resolution_interval: 1
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported manifest kind", str(resp.data))

    def test_rank_2_builder_cannot_apply_world_config_manifest(self):
        builder_user = self.create_user("rank2-builder@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        manifest = f"""
kind: world
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
