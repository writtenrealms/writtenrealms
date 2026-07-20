import random
import yaml

from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    ItemBundle,
    ItemDefinition,
)
from config import constants as adv_consts
from tests.base import WorldTestCase
from wr2_tests.utils import apply_basic_stat_system
from spawns.models import Item
from spawns.state_payloads import serialize_item


class TestItemDefinitions(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.default_currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )

    def test_spawn_rolls_declared_attributes_and_ignores_stale_keys(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "weapon_damage": 8,
            },
            attributes={"brawn": 2},
            randomization={
                "version": 1,
                "attributes": [
                    {"key": "brawn", "min": 10, "max": 10, "mode": "uniform"},
                    {"key": "luck", "min": 5, "max": 5, "mode": "uniform"},
                ],
            },
        )

        item = definition.spawn(
            self.player,
            self.spawn_world,
            rng=random.Random(7),
        )

        self.assertEqual(item.definition, definition)
        self.assertEqual(item.name, "a bronze sword")
        self.assertEqual(item.type, adv_consts.ITEM_TYPE_EQUIPPABLE)
        self.assertEqual(item.equipment_type, adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        self.assertEqual(item.weapon_damage, 8)
        self.assertEqual(item.attributes, {"brawn": 12.0})
        self.assertEqual(item.roll_metadata["ignored_attributes"], ["luck"])
        self.assertTrue(item.roll_metadata["randomized"])

    def test_payload_marks_stable_definitions_stackable_and_randomized_items_unique(self):
        stable_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="ration",
            name="a ration",
            item_type=adv_consts.ITEM_TYPE_FOOD,
        )
        random_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="chipped-sword",
            name="a chipped sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            randomization={
                "attributes": [
                    {"key": "brawn", "min": 1, "max": 1, "mode": "uniform"},
                ],
            },
        )
        stable_item = stable_definition.spawn(self.player, self.spawn_world)
        random_item = random_definition.spawn(self.player, self.spawn_world)

        stable_payload = serialize_item(stable_item).model_dump()
        random_payload = serialize_item(random_item).model_dump()

        self.assertEqual(stable_payload["definition_slug"], "ration")
        self.assertTrue(stable_payload["is_stackable"])
        self.assertTrue(stable_payload["stack_key"].startswith("definition:ration:"))

        self.assertEqual(random_payload["definition_slug"], "chipped-sword")
        self.assertFalse(random_payload["is_stackable"])
        self.assertIsNone(random_payload["stack_key"])

    def test_stable_definition_edits_sync_existing_unmodified_items(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="training-sword",
            name="a training sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "weapon_damage": 2,
            },
            attributes={"brawn": 1},
        )
        item = definition.spawn(self.player, self.spawn_world)

        definition.name = "a sharpened training sword"
        definition.base_properties = {
            "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            "weapon_damage": 5,
        }
        definition.attributes = {"brawn": 4}
        definition.save()

        item.refresh_from_db()
        later_item = definition.spawn(self.player, self.spawn_world)

        self.assertEqual(item.name, "a sharpened training sword")
        self.assertEqual(item.weapon_damage, 5)
        self.assertEqual(item.attributes, {"brawn": 4})
        self.assertEqual(item.roll_metadata["randomized"], False)
        self.assertEqual(
            item.roll_metadata["rolled_at_definition_modified_ts"],
            definition.modified_ts.isoformat(),
        )
        self.assertEqual(
            serialize_item(item).stack_key,
            serialize_item(later_item).stack_key,
        )

    def test_definition_repricing_does_not_reprice_existing_items(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            cost=25,
            currency=self.default_currency,
        )
        item = definition.spawn(self.player, self.spawn_world)
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )

        definition.cost = 90
        definition.currency = drachma
        definition.save()

        item.refresh_from_db()
        later_item = definition.spawn(self.player, self.spawn_world)
        self.assertEqual(item.cost, 25)
        self.assertEqual(item.currency, self.default_currency)
        self.assertEqual(later_item.cost, 90)
        self.assertEqual(later_item.currency, drachma)

    def test_augmented_definition_items_do_not_stack_or_get_resynced(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="heirloom-blade",
            name="an heirloom blade",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={"weapon_damage": 2},
        )
        item = definition.spawn(self.player, self.spawn_world)
        augment = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name="a sharpening rune",
            type=adv_consts.ITEM_TYPE_INERT,
        )
        item.augment = augment
        item.save(update_fields=["augment"])
        original_damage = item.weapon_damage

        definition.base_properties = {"weapon_damage": 9}
        definition.save()

        item.refresh_from_db()
        self.assertEqual(item.weapon_damage, original_damage)
        self.assertIsNone(serialize_item(item).stack_key)


class TestItemDefinitionManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.default_currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])

    def test_apply_item_definition_manifest_can_create_definition(self):
        manifest = f"""
kind: itemdefinition
metadata:
  world: world.{self.world.id}
  slug: bronze-sword
  name: a bronze sword
spec:
  description: A practical blade.
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 8
  cost: 12
  currency: obol
  attributes:
    brawn: 2
  randomization:
    attributes:
      - key: brawn
        min: 10
        max: 20
        mode: favor_high
        curve: 1.5
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "itemdefinition")
        self.assertEqual(resp.data["operation"], "created")

        definition = ItemDefinition.objects.get(world=self.world, slug="bronze-sword")
        self.assertEqual(definition.name, "a bronze sword")
        self.assertEqual(definition.item_type, adv_consts.ITEM_TYPE_EQUIPPABLE)
        self.assertEqual(definition.base_properties["equipment_type"], "weapon_1h")
        self.assertEqual(definition.base_properties["weapon_damage"], 8)
        self.assertNotIn("currency", definition.base_properties)
        self.assertEqual(definition.cost, 12)
        self.assertEqual(definition.currency, self.default_currency)
        self.assertEqual(definition.attributes, {"brawn": 2})
        self.assertEqual(definition.randomization["attributes"][0]["mode"], "favor_high")

    def test_item_money_patch_rejects_currency_without_cost(self):
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            cost=12,
            currency=self.default_currency,
        )

        for cost_line in ("", "  cost: null\n"):
            with self.subTest(cost_line=cost_line or "omitted"):
                manifest = f"""
kind: itemdefinition
metadata:
  slug: bronze-sword
spec:
{cost_line}  currency: drachma
"""
                response = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)

        definition.refresh_from_db()
        self.assertEqual(definition.cost, 12)
        self.assertEqual(definition.currency, self.default_currency)
        self.assertNotEqual(definition.currency, drachma)

    def test_apply_item_definition_manifest_accepts_armor_rating_and_class(self):
        self.world.config.equipment_system = {
            "armor_classes": [
                {"key": "light", "label": "Light Armor"},
                {"key": "heavy", "label": "Heavy Armor"},
            ],
            "default_armor_class": "light",
        }
        self.world.config.save(update_fields=["equipment_system"])

        manifest = f"""
kind: itemdefinition
metadata:
  world: world.{self.world.id}
  slug: bronze-helmet
  name: a bronze helmet
spec:
  type: equippable
  equipment_type: head
  armor_class: heavy
  armor: 7
  resilience: 2
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        definition = ItemDefinition.objects.get(world=self.world, slug="bronze-helmet")
        self.assertEqual(definition.base_properties["armor_class"], "heavy")
        self.assertEqual(definition.base_properties["armor"], 7)
        self.assertEqual(definition.base_properties["resilience"], 2)

        item = definition.spawn(self.player, self.spawn_world)
        self.assertEqual(item.armor_class, "heavy")
        self.assertEqual(item.armor, 7)

    def test_apply_item_definition_manifest_rejects_unknown_authored_armor_class(self):
        self.world.config.equipment_system = {
            "armor_classes": [
                {"key": "light", "label": "Light Armor"},
            ],
            "default_armor_class": "light",
        }
        self.world.config.save(update_fields=["equipment_system"])

        manifest = f"""
kind: itemdefinition
metadata:
  world: world.{self.world.id}
  slug: bronze-helmet
  name: a bronze helmet
spec:
  type: equippable
  equipment_type: head
  armor_class: heavy
  armor: 7
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("declared armor class", str(resp.data))

    def test_apply_item_bundle_manifest_can_create_weighted_bundle(self):
        sword = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="rusty-dagger",
            name="a rusty dagger",
        )
        manifest = f"""
kind: itembundle
metadata:
  world: world.{self.world.id}
  slug: bandit-weapon-drop
  name: Bandit weapon drop
spec:
  entries:
    - item_definition: itemdefinition.{sword.slug}
      weight: 5
      min_quantity: 1
      max_quantity: 2
      probability: 100
    - item_definition: rusty-dagger
      weight: 3
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "itembundle")

        bundle = ItemBundle.objects.get(world=self.world, slug="bandit-weapon-drop")
        self.assertEqual(bundle.entries.count(), 2)
        self.assertEqual(
            list(bundle.entries.order_by("id").values_list("weight", flat=True)),
            [5, 3],
        )

    def test_world_export_includes_item_definition_and_bundle_documents(self):
        sword = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={"weapon_damage": 8},
            randomization={
                "attributes": [
                    {"key": "brawn", "min": 10, "max": 20, "mode": "uniform"},
                ],
            },
        )
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="bandit-weapon-drop",
            name="Bandit weapon drop",
        )
        bundle.entries.create(item_definition=sword, weight=5)

        resp = self.client.get(self.export_ep)
        self.assertEqual(resp.status_code, 200, resp.data)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc]
        kinds = [doc["kind"] for doc in docs]
        self.assertIn("itemdefinition", kinds)
        self.assertIn("itembundle", kinds)

        item_doc = next(doc for doc in docs if doc["kind"] == "itemdefinition")
        self.assertEqual(item_doc["metadata"]["slug"], "bronze-sword")
        self.assertEqual(item_doc["spec"]["weapon_damage"], 8)

        bundle_doc = next(doc for doc in docs if doc["kind"] == "itembundle")
        self.assertEqual(
            bundle_doc["spec"]["entries"][0]["item_definition"],
            "itemdefinition.bronze-sword",
        )

class TestItemDefinitionBuilderEndpoints(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.list_ep = reverse("builder-item-definition-list", args=[self.world.pk])

    def test_list_item_definitions_for_builder_ui(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            randomization={
                "attributes": [
                    {"key": "brawn", "min": 1, "max": 3, "mode": "uniform"},
                ],
            },
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="ration",
            name="a ration",
            item_type=adv_consts.ITEM_TYPE_FOOD,
        )

        resp = self.client.get(self.list_ep, {"sort_by": "slug"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 2)
        self.assertEqual(
            [entry["slug"] for entry in resp.data["results"]],
            ["bronze-sword", "ration"],
        )
        self.assertTrue(resp.data["results"][0]["randomized"])
        self.assertEqual(resp.data["results"][0]["type"], adv_consts.ITEM_TYPE_EQUIPPABLE)

    def test_list_filters_by_item_and_equipment_type(self):
        sword = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            },
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="greatsword",
            name="a greatsword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
            },
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="ration",
            name="a ration",
            item_type=adv_consts.ITEM_TYPE_FOOD,
        )

        resp = self.client.get(
            self.list_ep,
            {
                "item_type": adv_consts.ITEM_TYPE_EQUIPPABLE,
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "sort_by": "slug",
            },
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["slug"], sword.slug)

    def test_retrieve_item_definition_includes_yaml(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            attributes={"brawn": 2},
        )

        resp = self.client.get(
            reverse("builder-item-definition-detail", args=[self.world.pk, definition.pk])
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["slug"], "bronze-sword")
        self.assertEqual(resp.data["attributes"], {"brawn": 2})
        self.assertIn("kind: itemdefinition", resp.data["yaml"])
        self.assertEqual(resp.data["manifest"]["kind"], "itemdefinition")


class TestItemBundleBuilderEndpoints(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.list_ep = reverse("builder-item-bundle-list", args=[self.world.pk])

    def test_list_item_bundles_for_builder_ui(self):
        sword = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
        )
        dagger = ItemDefinition.objects.create(
            world=self.world,
            slug="rusty-dagger",
            name="a rusty dagger",
        )
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="bandit-weapon-drop",
            name="Bandit weapon drop",
        )
        bundle.entries.create(item_definition=sword, weight=5)
        bundle.entries.create(item_definition=dagger, weight=3)

        resp = self.client.get(self.list_ep, {"sort_by": "slug"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["slug"], "bandit-weapon-drop")
        self.assertEqual(resp.data["results"][0]["entry_count"], 2)

    def test_retrieve_item_bundle_includes_yaml(self):
        sword = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
        )
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="bandit-weapon-drop",
            name="Bandit weapon drop",
        )
        bundle.entries.create(item_definition=sword, weight=5)

        resp = self.client.get(
            reverse("builder-item-bundle-detail", args=[self.world.pk, bundle.pk])
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["slug"], "bandit-weapon-drop")
        self.assertEqual(resp.data["entry_count"], 1)
        self.assertIn("kind: itembundle", resp.data["yaml"])
        self.assertEqual(resp.data["manifest"]["kind"], "itembundle")
