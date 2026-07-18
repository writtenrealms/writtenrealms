from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import AbilityDefinition, ItemDefinition
from config import constants as adv_consts
from core.computations import compute_stats
from tests.base import WorldTestCase


class TestStartingLoadout(WorldTestCase):
    def _ability(self, slug):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=slug.replace("-", " ").title(),
            command_verbs=[slug.replace("-", "_")],
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability={"classes": ["hoplite"], "min_level": 1},
            requirements={},
            cost={},
            cast_time={"rounds": 0},
            cooldown={"rounds": 0},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {},
                    "text": {"label": slug.replace("-", " ").title()},
                },
            ],
        )

    def _item_definition(self, slug, equipment_type, **properties):
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=slug.replace("-", " "),
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": equipment_type,
                **properties,
            },
        )

    def test_level_twenty_hoplite_starts_with_abilities_and_full_loadout(self):
        ability_slugs = ["bash", "charge", "cleave", "wound", "guard", "shout"]
        for slug in ability_slugs:
            self._ability(slug)

        equipped_definitions = {
            adv_consts.EQUIPMENT_SLOT_WEAPON: self._item_definition(
                "starter-hoplite-sword",
                adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                weapon_damage=44,
            ),
            adv_consts.EQUIPMENT_SLOT_OFFHAND: self._item_definition(
                "starter-hoplite-shield",
                adv_consts.EQUIPMENT_TYPE_SHIELD,
            ),
            adv_consts.EQUIPMENT_SLOT_HEAD: self._item_definition(
                "starter-hoplite-head",
                adv_consts.EQUIPMENT_TYPE_HEAD,
            ),
            adv_consts.EQUIPMENT_SLOT_BODY: self._item_definition(
                "starter-hoplite-body",
                adv_consts.EQUIPMENT_TYPE_BODY,
                health_max=37,
            ),
            adv_consts.EQUIPMENT_SLOT_ARMS: self._item_definition(
                "starter-hoplite-arms",
                adv_consts.EQUIPMENT_TYPE_ARMS,
            ),
            adv_consts.EQUIPMENT_SLOT_HANDS: self._item_definition(
                "starter-hoplite-hands",
                adv_consts.EQUIPMENT_TYPE_HANDS,
            ),
            adv_consts.EQUIPMENT_SLOT_WAIST: self._item_definition(
                "starter-hoplite-waist",
                adv_consts.EQUIPMENT_TYPE_WAIST,
            ),
            adv_consts.EQUIPMENT_SLOT_LEGS: self._item_definition(
                "starter-hoplite-legs",
                adv_consts.EQUIPMENT_TYPE_LEGS,
            ),
            adv_consts.EQUIPMENT_SLOT_FEET: self._item_definition(
                "starter-hoplite-feet",
                adv_consts.EQUIPMENT_TYPE_FEET,
            ),
            adv_consts.EQUIPMENT_SLOT_ACCESSORY: self._item_definition(
                "starter-hoplite-accessory",
                adv_consts.EQUIPMENT_TYPE_ACCESSORY,
            ),
        }
        spare_spear = self._item_definition(
            "starter-hoplite-spare-spear",
            adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
            weapon_damage=62,
        )

        leveling_curve = [0] + [level * 100 for level in range(1, 20)]
        self.world.config.starting_level = 20
        self.world.config.max_level = 20
        self.world.config.leveling_curve = leveling_curve
        self.world.config.ability_progression = {
            "max_known": 6,
            "starting_abilities": [
                {
                    "ability": slug,
                    "conditions": {"eq": ["actor.archetype", "hoplite"]},
                }
                for slug in ability_slugs
            ],
        }
        self.world.config.starting_equipment = [
            {
                "item_definition": f"itemdefinition.{definition.slug}",
                "count": 1,
                "archetype": "hoplite",
            }
            for definition in equipped_definitions.values()
        ] + [
            {
                "item_definition": f"itemdefinition.{spare_spear.slug}",
                "count": 1,
                "archetype": "hoplite",
                "equip": False,
            },
        ]
        self.world.config.save(update_fields=[
            "starting_level",
            "max_level",
            "leveling_curve",
            "ability_progression",
            "starting_equipment",
        ])

        player = self.create_player("Hoplite")
        player.archetype = "hoplite"
        with CaptureQueriesContext(connection) as queries:
            player.initialize()

        definition_selects = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].lstrip().lower().startswith("select")
            and "builders_itemdefinition" in query["sql"].lower()
        ]
        self.assertEqual(len(definition_selects), 1)

        player.refresh_from_db()
        player.equipment.refresh_from_db()
        self.assertEqual(player.level, 20)
        self.assertEqual(player.experience, leveling_curve[19])
        self.assertEqual(player.known_abilities, ability_slugs)
        self.assertEqual(
            player.ability_hotkeys,
            {str(index): slug for index, slug in enumerate(ability_slugs, start=1)},
        )
        for slot, definition in equipped_definitions.items():
            self.assertEqual(getattr(player.equipment, slot).definition, definition)

        carried_items = list(player.inventory.select_related("definition"))
        self.assertEqual([item.definition for item in carried_items], [spare_spear])
        self.assertEqual(
            player.health,
            compute_stats(
                player.level,
                player.archetype,
                char=player,
                world=player.world,
            )["health_max"],
        )

    def test_conflicting_starter_items_remain_in_inventory(self):
        spear = self._item_definition(
            "starter-spear",
            adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
        )
        shield = self._item_definition(
            "starter-shield",
            adv_consts.EQUIPMENT_TYPE_SHIELD,
        )
        sword = self._item_definition(
            "starter-sword",
            adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        )
        first_helmet = self._item_definition(
            "starter-first-helmet",
            adv_consts.EQUIPMENT_TYPE_HEAD,
        )
        second_helmet = self._item_definition(
            "starter-second-helmet",
            adv_consts.EQUIPMENT_TYPE_HEAD,
        )
        definitions = [spear, shield, sword, first_helmet, second_helmet]
        self.world.config.starting_equipment = [
            {
                "item_definition": f"itemdefinition.{definition.slug}",
                "count": 1,
                "archetype": "hoplite",
            }
            for definition in definitions
        ]
        self.world.config.save(update_fields=["starting_equipment"])

        player = self.create_player("Conflict")
        player.archetype = "hoplite"
        player.initialize()
        player.equipment.refresh_from_db()

        self.assertEqual(player.equipment.weapon.definition, spear)
        self.assertIsNone(player.equipment.offhand)
        self.assertEqual(player.equipment.head.definition, first_helmet)
        carried_definition_ids = set(
            player.inventory.values_list("definition_id", flat=True)
        )
        self.assertEqual(
            carried_definition_ids,
            {shield.id, sword.id, second_helmet.id},
        )
