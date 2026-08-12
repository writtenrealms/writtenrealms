import yaml

from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    FACTION_TYPE_CORE,
    Faction,
    ItemDefinition,
    WorldBuilder,
    WorldStartingCurrencyBalance,
)
from config import constants as adv_consts
from config import game_settings as adv_config
from core.death_routing import (
    load_compiled_plan,
    resolve_death_destination,
)
from spawns.models import Player
from tests.base import WorldTestCase
from worlds.models import (
    DeathRoutingCompiledSnapshot,
    DeathRoutingPolicy,
    DeathRoutingRoute,
    DeathRoutingSnapshotReference,
    Room,
    World,
    WorldConfig,
)


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.world.config.death_currency = self.currency
        self.world.config.clan_registration_currency = self.currency
        self.world.config.save(update_fields=[
            "death_currency",
            "clan_registration_currency",
        ])
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

    def _instance_world(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        return World.objects.new_world(
            name="Trial Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=True,
            instance_of=self.world,
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
        self.assertEqual(config_resp.data["config"]["default_roam_chance"], 10)
        self.assertIs(config_resp.data["config"]["announce_duel_results"], False)
        self.assertNotIn("allow_pvp", config_resp.data["config"])
        stat_system = config_resp.data["config"]["stat_system"]
        self.assertEqual(
            stat_system["formulas"]["base_resources"]["stamina"]["flat"],
            adv_config.PLAYER_STARTING_MAX_STAMINA,
        )
        self.assertEqual(
            stat_system["formulas"]["base_stats"]["stamina_regen"],
            adv_config.PLAYER_STARTING_STAMINA_REGEN,
        )
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
        self.assertEqual(
            world_manifest["spec"]["starting_room"],
            f"room@{self.room.relative_id}",
        )
        self.assertEqual(
            world_manifest["spec"]["death_room"],
            f"room@{self.room.relative_id}",
        )
        self.assertEqual(world_manifest["spec"]["combat_resolution_interval"], 0)
        self.assertEqual(world_manifest["spec"]["default_roam_chance"], 10)
        self.assertEqual(
            world_manifest["spec"]["pvp_mode"],
            adv_consts.PVP_MODE_FFA,
        )
        self.assertIs(world_manifest["spec"]["announce_duel_results"], False)
        self.assertNotIn("allow_pvp", world_manifest["spec"])
        self.assertIn("player_creation", world_manifest["spec"])
        self.assertNotIn("can_select_faction", world_manifest["spec"])
        self.assertNotIn("is_classless", world_manifest["spec"])
        self.assertEqual(
            world_manifest["spec"]["stats"]["formulas"]["base_resources"]["stamina"]["flat"],
            adv_config.PLAYER_STARTING_MAX_STAMINA,
        )
        self.assertEqual(
            world_manifest["spec"]["stats"]["formulas"]["base_stats"]["stamina_regen"],
            adv_config.PLAYER_STARTING_STAMINA_REGEN,
        )
        self.assertNotIn("combat", world_manifest["spec"])

    def test_world_config_yaml_canonicalizes_nested_room_conditions(self):
        legacy_ref = f"room.{self.room.id}"
        canonical_ref = f"room@{self.room.relative_id}"
        self.world.config.ability_progression = {
            "max_known": 10,
            "starting_abilities": [
                {
                    "ability": "room-sense",
                    "conditions": {
                        "eq": ["actor.room_id", legacy_ref],
                    },
                },
            ],
        }
        self.world.config.save(update_fields=["ability_progression"])

        response = self.client.get(self.config_ep)

        self.assertEqual(response.status_code, 200, response.data)
        condition = response.data["manifest"]["spec"]["ability_progression"][
            "starting_abilities"
        ][0]["conditions"]
        self.assertEqual(condition["eq"][1], canonical_ref)
        self.assertEqual(
            yaml.safe_load(response.data["yaml"]),
            response.data["manifest"],
        )
        self.assertNotIn(legacy_ref, response.data["yaml"])

    def test_world_config_endpoint_rejects_foreign_world_currencies(self):
        other_world = World.objects.new_world(
            name="Other Economy",
            author=self.user,
        )
        foreign_currency = create_currency(
            world=other_world,
            code="foreign",
            name="Foreign Coin",
        )

        response = self.client.patch(
            self.config_ep,
            {"death_currency": foreign_currency.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("death_currency", response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_currency, self.currency)

    def test_world_config_endpoint_updates_duel_announcement_policy(self):
        response = self.client.patch(
            self.config_ep,
            {"announce_duel_results": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertTrue(self.world.config.announce_duel_results)

    def test_world_config_endpoint_compiles_death_routing(self):
        destination = self.room.create_at("east")
        response = self.client.patch(
            self.config_ep,
            {
                "death_routing": {
                    "routes": [
                        {
                            "when": {
                                "eq": [
                                    "state.character.afterlife_path",
                                    "ember",
                                ],
                            },
                            "destination": f"room@{destination.relative_id}",
                        },
                    ],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_routing_generation, 1)
        self.assertEqual(
            self.world.config.death_routing_policy.routes.get().destination_room,
            destination,
        )
        self.assertEqual(
            response.data["death_routing"]["routes"][0]["destination"]["id"],
            destination.id,
        )

    def test_world_config_endpoint_rejects_instance_source_on_base_world(self):
        response = self.client.patch(
            self.config_ep,
            {"death_routing_source": "base_world"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("death_routing_source", response.data)

    def test_world_config_endpoint_requires_currencies_for_monetary_policies(self):
        response = self.client.patch(
            self.config_ep,
            {
                "death_mode": adv_consts.DEATH_MODE_LOSE_CURRENCY,
                "death_currency": None,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("death_currency", response.data)

        response = self.client.patch(
            self.config_ep,
            {
                "clan_registration_cost": 1,
                "clan_registration_currency": None,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("clan_registration_currency", response.data)

    def test_world_manifest_rejects_fractional_clan_registration_cost(self):
        manifest = """
kind: world
spec:
  clan_registration_currency: obol
  clan_registration_cost: 1.9
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("must be an integer", str(response.data))

    def test_world_config_exports_match_pvp_mode_without_legacy_allow_pvp(self):
        self.world.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.world.config.save(update_fields=["pvp_mode"])

        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)

        export_resp = self.client.get(self.export_ep)
        self.assertEqual(export_resp.status_code, 200)
        export_docs = [
            doc
            for doc in yaml.safe_load_all(export_resp.data["yaml"])
            if doc is not None
        ]

        for manifest in (config_resp.data["manifest"], export_docs[-1]):
            with self.subTest(manifest=manifest):
                self.assertEqual(
                    manifest["spec"]["pvp_mode"],
                    adv_consts.PVP_MODE_MATCH,
                )
                self.assertNotIn("allow_pvp", manifest["spec"])

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
  default_currency: obol
  starting_balances:
    obol: 15
  starting_level: 2
  max_level: 5
  leveling_curve: [0, 10, 30, 60, 100]
  combat_resolution_interval: 1.5
  default_roam_chance: 25
  starting_room: room.{starting_room.id}
  death_room: room.{death_room.id}
  death_mode: lose_currency
  death_currency: obol
  death_currency_penalty: 0.35
  death_route: nearest_in_zone
  pvp_mode: zone
  announce_duel_results: true
  player_creation:
    core_faction:
      mode: none
  can_select_gender: false
  default_gender: male
  auto_equip: false
  is_narrative: true
  players_can_set_title: false
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
      stats:
        ability_power: Ability Power
    attributes:
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
        attribute_weights:
          grit: 3
          brawn: 4
          grace: 1
          willpower: 1
          insight: 2
    class_selection:
      enabled: false
      default: warrior
    formulas:
      base_stats:
        stamina_regen: 4
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
        self.assertEqual(
            WorldStartingCurrencyBalance.objects.get(
                world=self.world,
                currency=self.currency,
            ).amount,
            15,
        )
        self.assertEqual(config.starting_level, 2)
        self.assertEqual(config.max_level, 5)
        self.assertEqual(config.leveling_curve, [0, 10, 30, 60, 100])
        self.assertEqual(config.combat_resolution_interval, 1.5)
        self.assertEqual(config.default_roam_chance, 25)
        self.assertEqual(config.starting_room_id, starting_room.id)
        self.assertEqual(config.death_room_id, death_room.id)
        self.assertEqual(config.death_mode, "lose_currency")
        self.assertEqual(config.death_currency, self.currency)
        self.assertEqual(float(config.death_currency_penalty), 0.35)
        self.assertEqual(config.death_route, "nearest_in_zone")
        self.assertEqual(config.pvp_mode, "zone")
        self.assertTrue(config.announce_duel_results)
        self.assertFalse(config.can_select_faction)
        self.assertFalse(config.can_select_gender)
        self.assertEqual(config.default_gender, "male")
        self.assertFalse(config.auto_equip)
        self.assertTrue(config.is_narrative)
        self.assertFalse(config.allow_combat)
        self.assertFalse(config.players_can_set_title)
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
            config.stat_system["labels"]["stats"]["ability_power"],
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
        self.assertFalse(config.stat_system["class_selection"]["enabled"])
        self.assertEqual(config.stat_system["class_selection"]["default"], "warrior")
        self.assertEqual(config.stat_system["formulas"]["base_stats"]["stamina_regen"], 4.0)
        primary_keys = [
            entry["key"] for entry in config.stat_system["attributes"]
        ]
        self.assertIn("insight", primary_keys)

    def test_apply_world_config_manifest_normalizes_legacy_allow_pvp_false(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  allow_pvp: false
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.pvp_mode,
            adv_consts.PVP_MODE_DISABLED,
        )

    def test_apply_world_config_manifest_normalizes_legacy_allow_pvp_true(self):
        self.world.config.pvp_mode = adv_consts.PVP_MODE_DISABLED
        self.world.config.save(update_fields=["pvp_mode"])
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  allow_pvp: true
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.pvp_mode,
            adv_consts.PVP_MODE_FFA,
        )

    def test_apply_world_config_manifest_accepts_consistent_legacy_allow_pvp(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  pvp_mode: match
  allow_pvp: true
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.pvp_mode, adv_consts.PVP_MODE_MATCH)

    def test_apply_world_config_manifest_rejects_conflicting_pvp_fields(self):
        conflicts = (
            (adv_consts.PVP_MODE_DISABLED, True),
            (adv_consts.PVP_MODE_ZONE, False),
            (adv_consts.PVP_MODE_FFA, False),
            (adv_consts.PVP_MODE_MATCH, False),
        )

        for pvp_mode, allow_pvp in conflicts:
            with self.subTest(pvp_mode=pvp_mode, allow_pvp=allow_pvp):
                manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  pvp_mode: {pvp_mode}
  allow_pvp: {str(allow_pvp).lower()}
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400)
                self.assertIn("allow_pvp", str(resp.data))
                self.assertIn("pvp_mode", str(resp.data))

    def test_apply_world_config_manifest_accepts_starting_equipment(self):
        compass = ItemDefinition.objects.create(
            world=self.world,
            slug="simple-compass",
            name="a simple compass",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        assassin_token = ItemDefinition.objects.create(
            world=self.world,
            slug="assassin-token",
            name="an assassin token",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )

        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  starting_equipment:
    - item_definition: itemdefinition.{compass.slug}
      count: 1
    - item_definition: {assassin_token.slug}
      count: 2
      archetype: assassin
      equip: false
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.starting_equipment,
            [
                {
                    "item_definition": f"itemdefinition.{compass.slug}",
                    "count": 1,
                },
                {
                    "item_definition": f"itemdefinition.{assassin_token.slug}",
                    "count": 2,
                    "archetype": adv_consts.ARCHETYPE_ASSASSIN,
                    "equip": False,
                },
            ],
        )
        spawn_world = self.world.spawned_worlds.first()
        player = Player.objects.create(
            world=spawn_world,
            room=self.room,
            user=self.user,
            name="Warrior",
            archetype=adv_consts.ARCHETYPE_WARRIOR,
        )
        player.initialize()
        self.assertEqual(player.inventory.filter(definition=compass).count(), 1)
        self.assertEqual(player.inventory.filter(definition=assassin_token).count(), 0)

        assassin = Player.objects.create(
            world=spawn_world,
            room=self.room,
            user=self.user,
            name="Assassin",
            archetype=adv_consts.ARCHETYPE_ASSASSIN,
        )
        assassin.initialize()
        self.assertEqual(assassin.inventory.filter(definition=compass).count(), 1)
        self.assertEqual(assassin.inventory.filter(definition=assassin_token).count(), 2)

        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)
        self.assertEqual(
            config_resp.data["manifest"]["spec"]["starting_equipment"],
            self.world.config.starting_equipment,
        )

        export_resp = self.client.get(self.export_ep)
        self.assertEqual(export_resp.status_code, 200)
        export_docs = [
            doc
            for doc in yaml.safe_load_all(export_resp.data["yaml"])
            if doc is not None
        ]
        self.assertEqual(
            export_docs[-1]["spec"]["starting_equipment"],
            self.world.config.starting_equipment,
        )

    def test_apply_world_config_manifest_coerces_starting_equipment_equip(self):
        compass = ItemDefinition.objects.create(
            world=self.world,
            slug="simple-compass",
            name="a simple compass",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  starting_equipment:
    - item_definition: itemdefinition.{compass.slug}
      equip: "off"
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.starting_equipment,
            [
                {
                    "item_definition": f"itemdefinition.{compass.slug}",
                    "count": 1,
                    "equip": False,
                },
            ],
        )

    def test_apply_world_config_manifest_rejects_invalid_starting_equipment_equip(self):
        compass = ItemDefinition.objects.create(
            world=self.world,
            slug="simple-compass",
            name="a simple compass",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  starting_equipment:
    - item_definition: itemdefinition.{compass.slug}
      equip: sometimes
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn(
            "spec.starting_equipment[0].equip must be a boolean",
            str(resp.data),
        )

    def test_apply_world_config_manifest_accepts_destroy_all_death_mode(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  death_mode: destroy_all
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_mode, "destroy_all")

    def test_death_routing_manifest_compiles_first_death_and_four_choices(self):
        first_death = self.room.create_at("east")
        choice_rooms = [
            Room.objects.create(
                world=self.world,
                zone=self.zone,
                name=f"Choice {index}",
                x=10 + index,
                y=0,
                z=0,
            )
            for index in range(4)
        ]
        manifest = f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.afterlife_path, null]
        destination: room@{first_death.x},{first_death.y},{first_death.z}
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{choice_rooms[0].id}
      - when:
          eq: [state.character.afterlife_path, tide]
        destination: room.{choice_rooms[1].id}
      - when:
          eq: [state.character.afterlife_path, stone]
        destination: room.{choice_rooms[2].id}
      - when:
          eq: [state.character.afterlife_path, wind]
        destination: room.{choice_rooms[3].id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        policy = DeathRoutingPolicy.objects.get(config=self.world.config)
        self.assertTrue(policy.enabled)
        self.assertEqual(
            DeathRoutingRoute.objects.filter(policy=policy).count(),
            5,
        )
        self.assertEqual(
            list(policy.routes.values_list("position", flat=True)),
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(self.world.config.death_routing_generation, 1)
        snapshot = DeathRoutingCompiledSnapshot.objects.get(
            config=self.world.config,
            plan_generation=1,
        )
        self.assertEqual(
            set(
                DeathRoutingSnapshotReference.objects.filter(
                    snapshot=snapshot,
                    destination_room__isnull=False,
                ).values_list("destination_room_id", flat=True)
            ),
            {
                self.world.config.death_room_id,
                first_death.id,
                *(room.id for room in choice_rooms),
            },
        )

        self.assertFalse(
            snapshot.references.filter(core_faction__isnull=False).exists()
        )
        self.assertFalse(
            snapshot.references.filter(origin_zone__isnull=False).exists()
        )

        plan = load_compiled_plan(self.world.config)
        self.assertEqual(
            plan.required_state_paths,
            ("state.character.afterlife_path",),
        )
        first = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={},
            origin_zone_id=self.zone.id,
        )
        # A state-only route remains the clean, bounded way to converge every
        # faction on one destination.
        ember = resolve_death_destination(
            plan,
            core_faction_id=999,
            archetype=None,
            player_level=1,
            character_state={"afterlife_path": "ember"},
            origin_zone_id=self.zone.id,
        )
        unknown = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={"afterlife_path": "unknown"},
            origin_zone_id=self.zone.id,
        )
        self.assertEqual(first.room_id, first_death.id)
        self.assertEqual(first.reason, "ordered_route")
        self.assertEqual(first.matched_route_position, 0)
        self.assertEqual(ember.room_id, choice_rooms[0].id)
        self.assertEqual(ember.matched_route_position, 1)
        self.assertEqual(unknown.room_id, self.world.config.death_room_id)
        self.assertEqual(unknown.fallback_reason, "no_match")
        self.assertIsNone(unknown.matched_route_position)

        export_response = self.client.get(self.export_ep)
        export_documents = [
            document
            for document in yaml.safe_load_all(export_response.data["yaml"])
            if document is not None
        ]
        exported = export_documents[-1]["spec"]["death_routing"]
        self.assertEqual(set(exported), {"routes"})
        self.assertEqual(len(exported["routes"]), 5)
        self.assertEqual(
            exported["routes"][0]["when"],
            {"eq": ["state.character.afterlife_path", None]},
        )
        self.assertEqual(
            exported["routes"][0]["destination"],
            f"room@{first_death.relative_id}",
        )

    def test_death_routing_manifest_rejects_bare_numeric_destination(self):
        manifest = f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          always: true
        destination: {self.room.id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("canonical 'room@<relative_id>'", str(response.data))

    def test_death_routing_manifest_compiles_core_faction_ids_without_expansion(self):
        destination = self.room.create_at("east")
        factions = [
            Faction.objects.create(
                world=self.world,
                code=code,
                name=code.title(),
                type=FACTION_TYPE_CORE,
                playable=True,
            )
            for code in ("human", "orc")
        ]
        manifest = f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          all:
            - eq: [state.character.afterlife_path, ember]
            - in: [player.core_faction, [human, orc]]
        destination: room.{destination.id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        routes = DeathRoutingRoute.objects.filter(
            policy__config=self.world.config
        )
        self.assertEqual(routes.count(), 1)
        compiled_condition = routes.get().compiled_condition
        self.assertEqual(
            set(compiled_condition["children"][1]["values"]),
            {faction.id for faction in factions},
        )
        snapshot = DeathRoutingCompiledSnapshot.objects.get(
            config=self.world.config,
            plan_generation=self.world.config.death_routing_generation,
        )
        self.assertEqual(
            set(
                snapshot.references.filter(
                    core_faction__isnull=False,
                ).values_list("core_faction_id", flat=True)
            ),
            {faction.id for faction in factions},
        )
        plan = load_compiled_plan(self.world.config)
        for faction in factions:
            with self.subTest(faction=faction.code):
                resolution = resolve_death_destination(
                    plan,
                    core_faction_id=faction.id,
                    archetype=None,
                    player_level=1,
                    character_state={"afterlife_path": "ember"},
                    origin_zone_id=self.zone.id,
                )
                self.assertEqual(resolution.room_id, destination.id)
                self.assertEqual(resolution.reason, "ordered_route")
                self.assertEqual(resolution.matched_route_position, 0)

    def test_death_routing_manifest_uses_first_matching_authored_route(self):
        first_destination = self.room.create_at("east")
        second_destination = self.room.create_at("north")
        manifest = f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          in: [state.character.afterlife_path, [ember, tide]]
        destination: room.{first_destination.id}
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{second_destination.id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_routing_policy.routes.count(), 2)
        resolution = resolve_death_destination(
            load_compiled_plan(self.world.config),
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={"afterlife_path": "ember"},
            origin_zone_id=self.zone.id,
        )
        self.assertEqual(resolution.room_id, first_destination.id)
        self.assertEqual(resolution.matched_route_position, 0)

    def test_death_routing_manifest_round_trips_level_thresholds(self):
        lower_destination = self.room.create_at("east")
        upper_destination = self.room.create_at("north")
        manifest = f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          lte: [player.level, 9]
        destination: room.{lower_destination.id}
      - when:
          gte: [player.level, 10]
        destination: room.{upper_destination.id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        routes = list(
            DeathRoutingRoute.objects.filter(
                policy__config=self.world.config,
            ).order_by("position")
        )
        self.assertEqual(
            [route.condition for route in routes],
            [
                {"lte": ["player.level", 9]},
                {"gte": ["player.level", 10]},
            ],
        )
        self.assertEqual(
            [route.compiled_condition for route in routes],
            [
                {"op": "lte", "source": "level", "value": 9},
                {"op": "gte", "source": "level", "value": 10},
            ],
        )

        plan = load_compiled_plan(self.world.config)
        self.assertEqual(plan.required_state_paths, ())
        for level, expected_room, expected_position in [
            (9, lower_destination, 0),
            (10, upper_destination, 1),
        ]:
            with self.subTest(level=level):
                resolution = resolve_death_destination(
                    plan,
                    core_faction_id=None,
                    archetype=None,
                    player_level=level,
                    character_state={},
                    origin_zone_id=self.zone.id,
                )
                self.assertEqual(resolution.room_id, expected_room.id)
                self.assertEqual(
                    resolution.matched_route_position,
                    expected_position,
                )

        export_response = self.client.get(self.export_ep)
        exported_documents = [
            document
            for document in yaml.safe_load_all(export_response.data["yaml"])
            if document is not None
        ]
        self.assertEqual(
            exported_documents[-1]["spec"]["death_routing"]["routes"],
            [
                {
                    "when": {"lte": ["player.level", 9]},
                    "destination": f"room@{lower_destination.relative_id}",
                },
                {
                    "when": {"gte": ["player.level", 10]},
                    "destination": f"room@{upper_destination.relative_id}",
                },
            ],
        )

    def test_base_update_can_add_class_profile_and_class_route_together(self):
        destination = self.room.create_at("east")
        manifest = f"""
kind: world
spec:
  stats:
    attributes:
      - key: focus
        label: Focus
    class_profiles:
      psychopomp:
        label: Psychopomp
        main_attribute: focus
        attribute_weights:
          focus: 4
  death_routing:
    routes:
      - when:
          eq: [player.archetype, psychopomp]
        destination: room.{destination.id}
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertIn(
            "psychopomp",
            self.world.config.stat_system["class_profiles"],
        )
        route = DeathRoutingRoute.objects.get(
            policy__config=self.world.config,
        )
        self.assertEqual(
            route.condition,
            {"eq": ["player.archetype", "psychopomp"]},
        )
        self.assertEqual(route.compiled_condition["source"], "archetype")
        self.assertEqual(route.compiled_condition["value"], "psychopomp")
        resolution = resolve_death_destination(
            load_compiled_plan(self.world.config),
            core_faction_id=None,
            archetype="psychopomp",
            player_level=1,
            character_state={},
            origin_zone_id=self.zone.id,
        )
        self.assertEqual(resolution.room_id, destination.id)
        self.assertEqual(resolution.matched_route_position, 0)

    def test_base_class_removal_waits_for_instance_local_route_to_clear(self):
        add_class_response = self.client.post(
            self.apply_ep,
            {
                "manifest": """
kind: world
spec:
  stats:
    attributes:
      - key: brawn
        label: Brawn
    class_profiles:
      warlord:
        label: Warlord
        main_attribute: brawn
        attribute_weights:
          brawn: 4
""",
            },
            format="json",
        )
        self.assertEqual(
            add_class_response.status_code,
            200,
            add_class_response.data,
        )

        instance = self._instance_world()
        instance_destination = Room.objects.create(
            world=instance,
            zone=instance.config.starting_room.zone,
            name="The Warlord Afterlife",
            x=2,
            y=0,
            z=0,
        )
        instance_apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[instance.pk],
        )
        route_response = self.client.post(
            instance_apply_ep,
            {
                "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [player.archetype, warlord]
        destination: room.{instance_destination.id}
""",
            },
            format="json",
        )
        self.assertEqual(route_response.status_code, 200, route_response.data)

        remove_class_manifest = """
kind: world
spec:
  stats:
    default_profile:
      label: ""
      main_attribute: ""
      attribute_weights: {}
      stat_rules: []
    class_profiles: {}
"""
        blocked_response = self.client.post(
            self.apply_ep,
            {"manifest": remove_class_manifest},
            format="json",
        )

        self.assertEqual(blocked_response.status_code, 400)
        self.assertIn(
            "Cannot remove class profile(s) used by death routing: warlord.",
            str(blocked_response.data),
        )
        self.world.config.refresh_from_db()
        self.assertIn(
            "warlord",
            self.world.config.stat_system["class_profiles"],
        )
        instance.config.refresh_from_db()
        self.assertTrue(instance.config.death_routing_policy.enabled)

        clear_response = self.client.post(
            instance_apply_ep,
            {
                "manifest": """
kind: world
spec:
  death_routing: null
""",
            },
            format="json",
        )
        self.assertEqual(clear_response.status_code, 200, clear_response.data)

        removal_response = self.client.post(
            self.apply_ep,
            {"manifest": remove_class_manifest},
            format="json",
        )

        self.assertEqual(removal_response.status_code, 200, removal_response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.stat_system["class_profiles"],
            {},
        )
        instance.config.refresh_from_db()
        self.assertFalse(instance.config.death_routing_policy.enabled)
        self.assertFalse(instance.config.death_routing_policy.routes.exists())

    def test_clearing_death_routing_creates_disabled_empty_policy(self):
        destination = self.room.create_at("east")
        response = self.client.post(
            self.apply_ep,
            {
                "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{destination.id}
""",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        response = self.client.post(
            self.apply_ep,
            {
                "manifest": """
kind: world
spec:
  death_routing: null
""",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_routing_generation, 2)
        self.assertFalse(self.world.config.death_routing_policy.enabled)
        self.assertEqual(self.world.config.death_routing_policy.routes.count(), 0)
        config_response = self.client.get(self.config_ep)
        self.assertEqual(
            config_response.data["manifest"]["spec"]["death_routing"],
            {"routes": []},
        )

    def test_retired_snapshots_release_references_and_keep_eight_generations(self):
        destination = self.room.create_at("east")

        for generation in range(1, 11):
            response = self.client.post(
                self.apply_ep,
                {
                    "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.route_generation, {generation}]
        destination: room.{destination.id}
""",
                },
                format="json",
            )
            self.assertEqual(
                response.status_code,
                200,
                response.data,
            )

        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_routing_generation, 10)
        snapshots = DeathRoutingCompiledSnapshot.objects.filter(
            config=self.world.config,
        )
        self.assertEqual(snapshots.count(), 8)
        self.assertEqual(
            list(
                snapshots.order_by("plan_generation").values_list(
                    "plan_generation",
                    flat=True,
                )
            ),
            list(range(3, 11)),
        )

        current_snapshot = snapshots.get(plan_generation=10)
        retired_snapshots = snapshots.exclude(pk=current_snapshot.pk)
        self.assertEqual(
            retired_snapshots.filter(retired_at__isnull=False).count(),
            7,
        )
        self.assertFalse(
            DeathRoutingSnapshotReference.objects.filter(
                snapshot__in=retired_snapshots,
            ).exists()
        )
        self.assertEqual(
            set(
                current_snapshot.references.filter(
                    destination_room__isnull=False,
                ).values_list("destination_room_id", flat=True)
            ),
            {
                self.world.config.death_room_id,
                destination.id,
            },
        )
        self.assertEqual(
            DeathRoutingSnapshotReference.objects.filter(
                snapshot__config=self.world.config,
            ).count(),
            2,
        )

    def test_death_room_change_rebuilds_compiled_plan_generation(self):
        destination = self.room.create_at("east")
        response = self.client.post(
            self.apply_ep,
            {
                "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{destination.id}
""",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        new_fallback = self.room.create_at("north")

        response = self.client.post(
            self.apply_ep,
            {
                "manifest": f"""
kind: world
spec:
  death_room: room.{new_fallback.id}
""",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.death_routing_generation, 2)
        plan = load_compiled_plan(self.world.config)
        self.assertEqual(plan.fallback_room_id, new_fallback.id)
        self.assertEqual(
            resolve_death_destination(
                plan,
                core_faction_id=None,
                archetype=None,
                player_level=1,
                character_state={"afterlife_path": "ember"},
                origin_zone_id=self.zone.id,
            ).room_id,
            destination.id,
        )

    def test_compiled_plan_uses_current_fallback_if_snapshot_is_stale(self):
        destination = self.room.create_at("east")
        response = self.client.post(
            self.apply_ep,
            {
                "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{destination.id}
""",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        old_plan = load_compiled_plan(self.world.config)

        repaired_fallback = self.room.create_at("north")
        WorldConfig.objects.filter(pk=self.world.config_id).update(
            death_room=repaired_fallback
        )
        self.world.config.refresh_from_db()
        repaired_plan = load_compiled_plan(self.world.config)

        self.assertEqual(old_plan.fallback_room_id, self.room.id)
        self.assertEqual(repaired_plan.generation, old_plan.generation)
        self.assertEqual(repaired_plan.fallback_room_id, repaired_fallback.id)
        self.assertEqual(
            resolve_death_destination(
                repaired_plan,
                core_faction_id=None,
                archetype=None,
                player_level=1,
                character_state={"afterlife_path": "unknown"},
                origin_zone_id=self.zone.id,
            ).room_id,
            repaired_fallback.id,
        )

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

    def test_apply_world_config_manifest_rejects_invalid_default_roam_chance(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  default_roam_chance: 101
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("default_roam_chance", str(resp.data))

    def test_instance_config_payload_omits_inherited_core_system_fields(self):
        instance = self._instance_world()
        ep = reverse("builder-world-config", args=[instance.pk])

        resp = self.client.get(ep)

        self.assertEqual(resp.status_code, 200)
        spec = resp.data["manifest"]["spec"]
        for field_name in (
            "ability_progression",
            "allow_combat",
            "announce_duel_results",
            "combat",
            "combat_resolution_interval",
            "decay_glory",
            "default_roam_chance",
            "equipment",
            "globals_enabled",
            "is_narrative",
            "leveling_curve",
            "max_level",
            "name_exclusions",
            "players_can_set_title",
            "starting_level",
            "starting_balances",
            "stats",
        ):
            self.assertNotIn(field_name, spec)
            self.assertNotIn(field_name, resp.data["config"])
        self.assertIn("starting_room", spec)
        self.assertIn("death_room", spec)
        self.assertIn("death_mode", spec)
        self.assertIn("death_currency_penalty", spec)

    def test_instance_world_config_manifest_rejects_inherited_core_system_fields(self):
        instance = self._instance_world()
        ep = reverse("builder-world-manifest-apply", args=[instance.pk])
        manifest = f"""
kind: world
metadata:
  world: world.{instance.id}
spec:
  death_mode: destroy_eq
  announce_duel_results: true
  combat_resolution_interval: 1.5
  default_roam_chance: 25
  leveling_curve: [0, 10, 30]
  ability_progression:
    max_known: 4
  stats: {{}}
  combat: {{}}
  equipment: {{}}
"""

        resp = self.client.post(ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        text = str(resp.data)
        self.assertIn("Instance worlds inherit core systems", text)
        self.assertIn("stats", text)
        self.assertIn("announce_duel_results", text)
        self.assertIn("combat_resolution_interval", text)

    def test_instance_world_config_manifest_rejects_nonlocal_world_rules(self):
        instance = self._instance_world()
        ep = reverse("builder-world-manifest-apply", args=[instance.pk])
        manifest = f"""
kind: world
metadata:
  world: world.{instance.id}
spec:
  death_mode: destroy_eq
  starting_balances:
    obol: 123
  players_can_set_title: false
  globals_enabled: false
"""

        resp = self.client.post(ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        text = str(resp.data)
        self.assertIn("can only alter local instance fields", text)
        self.assertIn("starting_balances", text)
        self.assertIn("players_can_set_title", text)

    def test_instance_world_config_manifest_allows_local_death_settings(self):
        instance = self._instance_world()
        death_room = Room.objects.create(
            world=instance,
            zone=instance.config.starting_room.zone,
            name="Instance Infirmary",
            x=1,
            y=0,
            z=0,
        )
        ep = reverse("builder-world-manifest-apply", args=[instance.pk])
        manifest = f"""
kind: world
metadata:
  world: world.{instance.id}
spec:
  death_room: room.{death_room.id}
  death_mode: destroy_eq
  death_currency_penalty: 0.1
  death_route: near_room
  pvp_mode: match
"""

        resp = self.client.post(ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        instance.config.refresh_from_db()
        self.assertEqual(instance.config.death_room_id, death_room.id)
        self.assertEqual(instance.config.death_mode, "destroy_eq")
        self.assertEqual(float(instance.config.death_currency_penalty), 0.1)
        self.assertEqual(instance.config.death_route, "near_room")
        self.assertEqual(instance.config.pvp_mode, adv_consts.PVP_MODE_MATCH)

    def test_instance_death_routing_source_is_explicit_and_preserves_local_policy(self):
        instance = self._instance_world()
        destination = Room.objects.create(
            world=instance,
            zone=instance.config.starting_room.zone,
            name="Instance Afterlife",
            x=2,
            y=0,
            z=0,
        )
        endpoint = reverse("builder-world-manifest-apply", args=[instance.pk])
        response = self.client.post(
            endpoint,
            {
                "manifest": f"""
kind: world
spec:
  death_routing:
    routes:
      - when:
          eq: [state.character.afterlife_path, ember]
        destination: room.{destination.id}
  death_routing_source: base_world
""",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        instance.config.refresh_from_db()
        self.assertEqual(instance.config.death_routing_source, "base_world")
        self.assertEqual(instance.config.death_routing_source_generation, 1)
        self.assertTrue(instance.config.death_routing_policy.enabled)
        self.assertEqual(instance.config.death_routing_policy.routes.count(), 1)

        config_response = self.client.get(
            reverse("builder-world-config", args=[instance.pk])
        )
        self.assertEqual(config_response.status_code, 200)
        spec = config_response.data["manifest"]["spec"]
        self.assertEqual(spec["death_routing_source"], "base_world")
        self.assertEqual(
            spec["death_routing"]["routes"][0]["destination"],
            f"room@{destination.relative_id}",
        )

        response = self.client.post(
            endpoint,
            {
                "manifest": """
kind: world
spec:
  death_routing_source: local
""",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        instance.config.refresh_from_db()
        self.assertEqual(instance.config.death_routing_source, "local")
        self.assertEqual(instance.config.death_routing_source_generation, 2)
        self.assertEqual(instance.config.death_routing_policy.routes.count(), 1)

    def test_base_world_rejects_death_routing_source(self):
        response = self.client.post(
            self.apply_ep,
            {
                "manifest": """
kind: world
spec:
  death_routing_source: base_world
""",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("only valid for instance worlds", str(response.data))

    def test_instance_world_config_manifest_normalizes_legacy_allow_pvp(self):
        instance = self._instance_world()
        ep = reverse("builder-world-manifest-apply", args=[instance.pk])
        manifest = f"""
kind: world
metadata:
  world: world.{instance.id}
spec:
  allow_pvp: false
"""

        resp = self.client.post(ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        instance.config.refresh_from_db()
        self.assertEqual(
            instance.config.pvp_mode,
            adv_consts.PVP_MODE_DISABLED,
        )

    def test_instance_direct_config_patch_rejects_inherited_core_system_fields(self):
        instance = self._instance_world()
        ep = reverse("builder-world-config", args=[instance.pk])

        resp = self.client.patch(
            ep,
            {
                "announce_duel_results": True,
                "combat_system": {"profiles": {}},
                "leveling_curve": [0, 10, 30],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Instance worlds inherit core systems", str(resp.data))
        self.assertIn("announce_duel_results", str(resp.data))

    def test_instance_direct_config_patch_rejects_nonlocal_world_rules(self):
        instance = self._instance_world()
        ep = reverse("builder-world-config", args=[instance.pk])

        resp = self.client.patch(
            ep,
            {
                "clan_registration_cost": 123,
                "players_can_set_title": False,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        text = str(resp.data)
        self.assertIn("can only alter local instance config", text)
        self.assertIn("clan_registration_cost", text)

    def test_instance_direct_config_patch_updates_death_routing_source(self):
        instance = self._instance_world()
        ep = reverse("builder-world-config", args=[instance.pk])

        response = self.client.patch(
            ep,
            {"death_routing_source": "base_world"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        instance.config.refresh_from_db()
        self.assertEqual(instance.config.death_routing_source, "base_world")
        self.assertEqual(instance.config.death_routing_source_generation, 1)

    def test_instance_local_config_patch_does_not_rewrite_inherited_combat_flags(self):
        instance = self._instance_world()
        instance.config.is_narrative = True
        instance.config.allow_combat = True
        instance.config.save(update_fields=["is_narrative", "allow_combat"])
        ep = reverse("builder-world-config", args=[instance.pk])

        resp = self.client.patch(
            ep,
            {"death_mode": "destroy_eq"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        instance.config.refresh_from_db()
        self.assertTrue(instance.config.allow_combat)

    def test_apply_world_config_manifest_accepts_equipment_armor_proficiencies(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  equipment:
    armor_classes:
      - key: light
        label: Light Armor
        armor_multiplier: 1.0
      - key: heavy
        label: Heavy Armor
        armor_multiplier: 1.35
    default_armor_class: light
  stats:
    attributes:
      - key: constitution
        label: Constitution
      - key: strength
        label: Strength
    default_profile:
      armor_proficiencies: [light]
      attribute_weights:
        constitution: 1
        strength: 1
    class_profiles:
      hoplite:
        label: Hoplite
        main_attribute: constitution
        armor_proficiencies: [light, heavy]
        attribute_weights:
          constitution: 4
          strength: 2
      mystic:
        label: Mystic
        main_attribute: strength
        attribute_weights:
          constitution: 1
          strength: 4
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.equipment_system["armor_classes"][1]["key"],
            "heavy",
        )
        self.assertEqual(
            self.world.config.stat_system["class_profiles"]["hoplite"]["armor_proficiencies"],
            ["light", "heavy"],
        )
        self.assertEqual(
            self.world.config.stat_system["class_profiles"]["mystic"]["armor_proficiencies"],
            ["light"],
        )

        config_resp = self.client.get(self.config_ep)
        self.assertEqual(config_resp.status_code, 200)
        exported = yaml.safe_load(config_resp.data["yaml"])
        self.assertEqual(exported["spec"]["equipment"]["default_armor_class"], "light")
        self.assertEqual(
            exported["spec"]["stats"]["class_profiles"]["hoplite"]["armor_proficiencies"],
            ["light", "heavy"],
        )

    def test_apply_world_config_manifest_accepts_attack_routine_and_offhand_features(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  equipment:
    offhand_weapons:
      default_allowed: false
      allowed_grips: [one_hand]
  combat:
    attack_routine:
      base_mainhand_strikes: 1
      stacking:
        extra_mainhand_strikes: max
        max_primary_strikes: 2
      dual_wield:
        enabled: true
        grants_offhand_strike: true
        offhand_damage_multiplier: 0.5
  stats:
    attributes:
      - key: brawn
        label: Brawn
    class_profiles:
      assassin:
        label: Assassin
        main_attribute: brawn
        attribute_weights:
          brawn: 4
        features:
          equipment:
            can_equip_offhand_weapon: true
            allowed_offhand_weapon_grips: [one_hand]
          combat:
            extra_mainhand_strikes: 1
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.equipment_system["offhand_weapons"],
            {
                "default_allowed": False,
                "allowed_grips": ["one_hand"],
            },
        )
        self.assertTrue(
            self.world.config.combat_system["attack_routine"]["dual_wield"]["enabled"]
        )
        assassin = self.world.config.stat_system["class_profiles"]["assassin"]
        self.assertTrue(
            assassin["features"]["equipment"]["can_equip_offhand_weapon"]
        )
        self.assertEqual(assassin["features"]["combat"]["extra_mainhand_strikes"], 1)

        export_resp = self.client.get(self.export_ep)
        export_docs = [doc for doc in yaml.safe_load_all(export_resp.data["yaml"]) if doc is not None]
        world_manifest = export_docs[-1]
        self.assertEqual(
            world_manifest["spec"]["equipment"]["offhand_weapons"]["allowed_grips"],
            ["one_hand"],
        )
        self.assertTrue(
            world_manifest["spec"]["combat"]["attack_routine"]["dual_wield"]["enabled"]
        )

    def test_apply_world_config_manifest_rejects_unknown_armor_proficiency(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  equipment:
    armor_classes:
      - key: light
        label: Light Armor
  stats:
    attributes:
      - key: constitution
        label: Constitution
    class_profiles:
      hoplite:
        armor_proficiencies: [heavy]
        attribute_weights:
          constitution: 1
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("declared armor class", str(resp.data))

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

    def test_apply_world_config_manifest_rejects_legacy_derived_stat_labels(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  stats:
    labels:
      derived:
        ability_power: Spell Power
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported stats.labels field(s): derived", str(resp.data))

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
      attribute_weights: {{}}
      stat_rules: []
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
