import yaml

from rest_framework.reverse import reverse

from builders.models import ItemDefinition, MobDefinition
from tests.base import WorldTestCase


API_VERSION = "writtenrealms.com/v1alpha3"


class WR1ManifestExportContractTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_default_wr1_export_shape_imports_as_one_batch(self):
        documents = [
            {
                "apiVersion": API_VERSION,
                "kind": "currency",
                "metadata": {"code": "gold"},
                "spec": {"name": "◍ gold"},
            },
            {
                "apiVersion": API_VERSION,
                "kind": "itemdefinition",
                "metadata": {
                    "slug": "malformed-jawbone",
                    "name": "a malformed 𝐣𝐚𝐰bone",
                },
                "spec": {
                    "type": "equippable",
                    "description": "A strange piece of ossification.",
                    "room_description": "A jawbone lies here.",
                    "equipment_type": "weapon_1h",
                    "weapon_damage": 3.63,
                    "energy_max": 5,
                    "ability_power": 16,
                    "attack_power": -122,
                    "attributes": {"strength": 4},
                    "cost": 5,
                    "currency": "gold",
                },
            },
            {
                "apiVersion": API_VERSION,
                "kind": "zone",
                "metadata": {"ref": "zone@1", "name": "Old Quarter"},
                "spec": {
                    "description": "The original quarter.",
                    "notes": "Converted from WR1.",
                    "initial_state": {},
                    "respawn_wait": 300,
                    "pvp_zone": False,
                    "center": "room@1",
                },
            },
            {
                "apiVersion": API_VERSION,
                "kind": "room",
                "metadata": {"ref": "room@1", "name": "Old Gate"},
                "spec": {
                    "coordinates": {"x": 0, "y": 0, "z": 0},
                    "zone": "zone@1",
                    "description": "An old gate opens east.",
                    "note": "",
                    "initial_state": {},
                    "type": "road",
                    "color": "#998877",
                    "is_landmark": True,
                    "exits": {"east": "room@2"},
                    "flags": ["no_roam"],
                    "details": [],
                    "doors": [
                        {
                            "direction": "east",
                            "name": "gate",
                            "to_room": "room@2",
                            "key": "itemdefinition.malformed-jawbone",
                            "destroy_key": False,
                            "default_state": "closed",
                        }
                    ],
                },
            },
            {
                "apiVersion": API_VERSION,
                "kind": "room",
                "metadata": {"ref": "room@2", "name": "Market Road"},
                "spec": {
                    "coordinates": {"x": 1, "y": 0, "z": 0},
                    "zone": "zone@1",
                    "description": "The road continues west.",
                    "note": "",
                    "initial_state": {},
                    "type": "road",
                    "color": "",
                    "is_landmark": False,
                    "exits": {"west": "room@1"},
                    "flags": [],
                    "details": [],
                    "doors": [
                        {
                            "direction": "west",
                            "name": "gate",
                            "to_room": "room@1",
                            "key": "itemdefinition.malformed-jawbone",
                            "destroy_key": False,
                            "default_state": "closed",
                        }
                    ],
                },
            },
            {
                "apiVersion": API_VERSION,
                "kind": "mobdefinition",
                "metadata": {"slug": "tracker", "name": "a tracker"},
                "spec": {
                    "type": "humanoid",
                    "description": "A watchful tracker.",
                    "room_description": "A tracker watches the road.",
                    "level": 3,
                    "energy_max": 9,
                    "energy_regen": 3,
                    "ability_power": 7,
                    "traits": ["tracker"],
                    "rewards": {"currencies": {"gold": 4}},
                },
            },
            {
                "apiVersion": API_VERSION,
                "kind": "world",
                "spec": {
                    "name": "Converted World",
                    "description": "Literal astral Unicode: 𝐓𝐡𝐞 𝐘'𝐚𝐚𝐧𝐝",
                    "is_public": False,
                    "default_currency": "gold",
                    "starting_balances": {"gold": 3},
                    "starting_room": "room@1",
                    "death_room": "room@2",
                    "death_mode": "lose_inv",
                    "pvp_mode": "zone",
                    "auto_equip": True,
                    "starting_equipment": [
                        {
                            "item_definition": "itemdefinition.malformed-jawbone",
                            "count": 1,
                        }
                    ],
                },
            },
        ]
        manifest = yaml.safe_dump_all(
            documents,
            allow_unicode=True,
            sort_keys=False,
        )

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["kind"], "batch")
        self.assertEqual(response.data["summary"]["documents"], len(documents))

        item = ItemDefinition.objects.get(
            world=self.world,
            slug="malformed-jawbone",
        )
        self.assertEqual(item.name, "a malformed 𝐣𝐚𝐰bone")
        self.assertEqual(item.base_properties["energy_max"], 5)
        self.assertEqual(item.base_properties["ability_power"], 16)
        self.assertEqual(item.base_properties["attack_power"], -122)
        self.assertEqual(item.attributes, {"strength": 4})

        mob = MobDefinition.objects.get(world=self.world, slug="tracker")
        self.assertEqual(mob.base_properties["energy_max"], 9)
        self.assertEqual(mob.currency_rewards.get(currency__code="gold").amount, 4)
        self.assertEqual(mob.traits, [{"key": "tracker"}])

        self.world.refresh_from_db()
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.default_currency.code, "gold")
        self.assertEqual(self.world.config.starting_room.relative_id, 1)
        self.assertEqual(self.world.config.death_room.relative_id, 2)
