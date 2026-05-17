import random
import yaml

from rest_framework.reverse import reverse

from builders.models import (
    Currency,
    ItemBundle,
    ItemDefinition,
    ItemTemplate,
    MerchantInventory,
    MobTemplate,
    MobTemplateInventory,
)
from config import constants as adv_consts
from system.serializers import UpdateMerchantsSerializer
from tests.base import WorldTestCase
from wr2_tests.utils import apply_basic_stat_system
from spawns.state_payloads import serialize_item


class TestItemDefinitions(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.default_currency = Currency.objects.create(
            world=self.world,
            code="gold",
            name="Gold",
            is_default=True,
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
        self.assertIsNone(item.template)
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
        template = ItemTemplate.objects.create(
            world=self.world,
            name="a torch",
        )

        stable_item = stable_definition.spawn(self.player, self.spawn_world)
        random_item = random_definition.spawn(self.player, self.spawn_world)
        template_item = template.spawn(self.player, self.spawn_world)

        stable_payload = serialize_item(stable_item).model_dump()
        random_payload = serialize_item(random_item).model_dump()
        template_payload = serialize_item(template_item).model_dump()

        self.assertEqual(stable_payload["definition_slug"], "ration")
        self.assertTrue(stable_payload["is_stackable"])
        self.assertTrue(stable_payload["stack_key"].startswith("definition:ration:"))

        self.assertEqual(random_payload["definition_slug"], "chipped-sword")
        self.assertFalse(random_payload["is_stackable"])
        self.assertIsNone(random_payload["stack_key"])

        self.assertTrue(template_payload["is_stackable"])
        self.assertEqual(template_payload["stack_key"], f"template:{template.id}")

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

    def test_upgraded_definition_items_do_not_stack_or_get_resynced(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="heirloom-blade",
            name="an heirloom blade",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={"weapon_damage": 2},
        )
        item = definition.spawn(self.player, self.spawn_world)
        item.boost()
        boosted_damage = item.weapon_damage

        definition.base_properties = {"weapon_damage": 9}
        definition.save()

        item.refresh_from_db()
        self.assertEqual(item.weapon_damage, boosted_damage)
        self.assertIsNone(serialize_item(item).stack_key)

    def test_mob_template_inventory_can_spawn_item_definition(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bandit-sword",
            name="a bandit sword",
        )
        mob_template = MobTemplate.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
        )
        MobTemplateInventory.objects.create(
            container=mob_template,
            item_definition=definition,
            probability=100,
            num_copies=1,
        )

        mob = mob_template.spawn(self.room, self.spawn_world)

        spawned_item = mob.inventory.get(definition=definition)
        self.assertEqual(spawned_item.name, "a bandit sword")
        self.assertIsNone(spawned_item.template)

    def test_mob_template_inventory_can_spawn_item_bundle(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bandit-sword",
            name="a bandit sword",
        )
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="bandit-drop",
            name="Bandit drop",
        )
        bundle.entries.create(item_definition=definition, weight=1)
        mob_template = MobTemplate.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
        )
        MobTemplateInventory.objects.create(
            container=mob_template,
            item_bundle=bundle,
            probability=100,
            num_copies=1,
        )

        mob = mob_template.spawn(self.room, self.spawn_world)

        spawned_item = mob.inventory.get(definition=definition)
        self.assertEqual(spawned_item.roll_metadata["source_bundle_slug"], "bandit-drop")
        self.assertEqual(spawned_item.roll_metadata["source_bundle_id"], bundle.id)

    def test_merchant_inventory_restocks_item_definition_and_bundle_slots(self):
        direct_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="merchant-ration",
            name="a merchant ration",
        )
        bundle_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="merchant-sword",
            name="a merchant sword",
        )
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="merchant-random-stock",
            name="Merchant random stock",
        )
        bundle.entries.create(item_definition=bundle_definition, weight=1)
        merchant_template = MobTemplate.objects.create(
            world=self.world,
            slug="quartermaster",
            name="a quartermaster",
        )
        MerchantInventory.objects.create(
            mob=merchant_template,
            item_definition=direct_definition,
            num=2,
        )
        MerchantInventory.objects.create(
            mob=merchant_template,
            item_bundle=bundle,
            num=1,
        )
        merchant = merchant_template.spawn(self.room, self.spawn_world)
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])

        first_update = UpdateMerchantsSerializer(
            data={
                "world_id": self.spawn_world.id,
                "data": [
                    {
                        "id": merchant.id,
                        "inventory": [],
                        "player_in_room": False,
                    }
                ],
            }
        )
        self.assertTrue(first_update.is_valid(), first_update.errors)
        first_update.save()

        current_items = list(merchant.inventory.all())
        self.assertEqual(
            sum(1 for item in current_items if item.definition_id == direct_definition.id),
            2,
        )
        bundle_items = [
            item
            for item in current_items
            if (item.roll_metadata or {}).get("source_bundle_id") == bundle.id
        ]
        self.assertEqual(len(bundle_items), 1)

        second_update = UpdateMerchantsSerializer(
            data={
                "world_id": self.spawn_world.id,
                "data": [
                    {
                        "id": merchant.id,
                        "inventory": [{"id": item.id} for item in merchant.inventory.all()],
                        "player_in_room": False,
                    }
                ],
            }
        )
        self.assertTrue(second_update.is_valid(), second_update.errors)
        second_update.save()

        self.assertEqual(merchant.inventory.filter(definition__isnull=False).count(), 3)


class TestItemDefinitionManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        Currency.objects.create(
            world=self.world,
            code="gold",
            name="Gold",
            is_default=True,
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
  currency: gold
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
        self.assertEqual(definition.base_properties["currency"], "gold")
        self.assertEqual(definition.attributes, {"brawn": 2})
        self.assertEqual(definition.randomization["attributes"][0]["mode"], "favor_high")

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

    def test_apply_mob_template_manifest_accepts_item_definition_and_bundle_inventory(self):
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
        bundle.entries.create(item_definition=dagger, weight=1)
        manifest = f"""
kind: mobtemplate
metadata:
  world: world.{self.world.id}
  slug: bandit
  name: a bandit
spec:
  inventory:
    - item_definition: itemdefinition.{sword.slug}
      probability: 100
      num_copies: 1
    - item_bundle: itembundle.{bundle.slug}
      probability: 50
      num_copies: 2
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        mob_template = MobTemplate.objects.get(world=self.world, slug="bandit")
        inventory = list(mob_template.template_inventories.order_by("id"))
        self.assertEqual(len(inventory), 2)
        self.assertEqual(inventory[0].item_definition, sword)
        self.assertIsNone(inventory[0].item_template)
        self.assertEqual(inventory[1].item_bundle, bundle)
        self.assertEqual(inventory[1].probability, 50)
        self.assertEqual(inventory[1].num_copies, 2)

    def test_apply_mob_template_manifest_rejects_unknown_item_definition_inventory(self):
        manifest = f"""
kind: mobtemplate
metadata:
  world: world.{self.world.id}
  slug: bandit
  name: a bandit
spec:
  inventory:
    - item_definition: itemdefinition.missing-sword
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertFalse(
            ItemDefinition.objects.filter(world=self.world, slug="missing-sword").exists()
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
