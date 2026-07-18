import yaml

from rest_framework.reverse import reverse

from builders.models import (
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingRecipe,
    Currency,
    ItemDefinition,
    ItemSalvageYield,
    MobDefinition,
)
from config import constants as adv_consts
from tests.base import WorldTestCase
from worlds.models import WorldConfig


class TestCraftingManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])
        self.output = ItemDefinition.objects.create(
            world=self.world,
            slug="t2-hoplite-head",
            name="a blue-crested helm",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={"equipment_type": adv_consts.EQUIPMENT_TYPE_HEAD, "armor": 12},
            randomization={
                "version": 1,
                "attributes": [
                    {"key": "constitution", "min": 2, "max": 3, "mode": "uniform"},
                ],
            },
        )

    def apply(self, manifest, expected_status=201):
        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def create_materials(self):
        response = self.apply(
            """
---
kind: craftmaterial
metadata:
  slug: bronze
  name: Bronze
spec:
  description: Usable bronze recovered from captured equipment.
  order: 10
---
kind: craftmaterial
metadata:
  slug: leather
  name: Leather
spec:
  description: Clean hide for straps and armor.
  order: 20
""",
            expected_status=200,
        )
        self.assertEqual(response.data["kind"], "batch")
        return (
            CraftMaterial.objects.get(world=self.world, slug="bronze"),
            CraftMaterial.objects.get(world=self.world, slug="leather"),
        )

    def create_recipe(self):
        return self.apply(
            """
kind: craftingrecipe
metadata:
  slug: t2-hoplite-head
spec:
  group: hoplite
  order: 10
  output:
    item_definition: itemdefinition.t2-hoplite-head
  inputs:
    - material: craftmaterial.bronze
      quantity: 8
    - material: craftmaterial.leather
      quantity: 2
  conditions:
    gte: [actor.level, 20]
  failure_message: You are not yet ready to craft this armor.
"""
        )

    def test_apply_complete_authored_crafting_catalog_and_provider_attachments(self):
        obol = Currency.objects.create(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.world.default_currency = obol
        self.world.save(update_fields=["default_currency", "modified_ts"])

        bronze, leather = self.create_materials()

        self.apply(
            """
kind: itemdefinition
metadata:
  slug: t2-hoplite-head
spec:
  salvage:
    only: false
    yields:
      - material: craftmaterial.bronze
        quantity: 2
      - material: craftmaterial.leather
        quantity: 1
""",
            expected_status=200,
        )
        self.output.refresh_from_db()
        self.assertFalse(self.output.salvage_only)
        self.assertEqual(
            list(
                self.output.salvage_yields.order_by("material__order").values_list(
                    "material__slug", "quantity"
                )
            ),
            [("bronze", 2), ("leather", 1)],
        )

        response = self.create_recipe()
        self.assertEqual(response.data["crafting_recipe"]["name"], "a blue-crested helm")
        recipe = CraftingRecipe.objects.get(world=self.world, slug="t2-hoplite-head")
        self.assertEqual(recipe.output_item_definition, self.output)
        self.assertEqual(recipe.conditions, {"gte": ["actor.level", 20]})
        self.assertEqual(
            list(recipe.ingredients.values_list("material_id", "quantity")),
            [(bronze.id, 8), (leather.id, 2)],
        )

        self.apply(
            """
kind: craftingprofile
metadata:
  slug: camp-workshop
  name: Camp Workshop
spec:
  keywords: camp workshop forge armory
  recipes:
    - craftingrecipe.t2-hoplite-head
"""
        )
        profile = CraftingProfile.objects.get(world=self.world, slug="camp-workshop")
        self.assertEqual(list(profile.recipes.all()), [recipe])

        room_ref = f"room@{self.room.x},{self.room.y},{self.room.z}"
        self.apply(
            f"""
kind: room
metadata:
  ref: {room_ref}
  name: {self.room.name}
spec:
  crafting:
    profile: craftingprofile.camp-workshop
""",
            expected_status=200,
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.crafting_profile, profile)

        self.apply(
            """
kind: mobdefinition
metadata:
  slug: damon-armorer
  name: Damon
spec:
  type: humanoid
  room_description: Damon directs the work around a smoking forge.
  keywords: damon armorer smith crafter
  combat:
    attackable: false
  crafting:
    profile: craftingprofile.camp-workshop
    availability: alive_and_present
"""
        )
        mob = MobDefinition.objects.get(world=self.world, slug="damon-armorer")
        self.assertEqual(mob.crafting_profile, profile)
        self.assertEqual(mob.crafting_availability, "alive_and_present")

        export = self.client.get(self.export_ep)
        self.assertEqual(export.status_code, 200, export.data)
        documents = [doc for doc in yaml.safe_load_all(export.data["yaml"]) if doc]
        kinds = [doc["kind"] for doc in documents]
        self.assertLess(kinds.index("craftmaterial"), kinds.index("itemdefinition"))
        self.assertLess(kinds.index("itemdefinition"), kinds.index("craftingrecipe"))
        self.assertLess(kinds.index("craftingrecipe"), kinds.index("craftingprofile"))
        self.assertLess(kinds.index("craftingprofile"), kinds.index("room"))
        self.assertEqual(export.data["summary"]["craft_materials"], 2)
        self.assertEqual(export.data["summary"]["crafting_recipes"], 1)
        self.assertEqual(export.data["summary"]["crafting_profiles"], 1)

        room_doc = next(
            doc for doc in documents
            if doc["kind"] == "room" and doc["metadata"]["ref"] == room_ref
        )
        self.assertEqual(
            room_doc["spec"]["crafting"]["profile"],
            "craftingprofile.camp-workshop",
        )
        item_doc = next(
            doc for doc in documents
            if doc["kind"] == "itemdefinition"
            and doc["metadata"]["slug"] == "t2-hoplite-head"
        )
        self.assertEqual(
            item_doc["spec"]["salvage"]["yields"][0],
            {"material": "craftmaterial.bronze", "quantity": 2},
        )

        target_world = self.world.__class__.objects.new_world(
            name="Crafting Import Target",
            author=self.user,
            config=WorldConfig.objects.create(),
        )
        import_response = self.client.post(
            reverse("builder-world-manifest-apply", args=[target_world.pk]),
            {"manifest": export.data["yaml"]},
            format="json",
        )
        self.assertEqual(import_response.status_code, 200, import_response.data)
        target_recipe = CraftingRecipe.objects.get(
            world=target_world,
            slug="t2-hoplite-head",
        )
        self.assertEqual(target_recipe.ingredients.count(), 2)
        target_profile = CraftingProfile.objects.get(
            world=target_world,
            slug="camp-workshop",
        )
        target_room = target_world.rooms.get(
            x=self.room.x,
            y=self.room.y,
            z=self.room.z,
        )
        self.assertEqual(target_room.crafting_profile, target_profile)
        target_item = ItemDefinition.objects.get(
            world=target_world,
            slug="t2-hoplite-head",
        )
        self.assertEqual(target_item.salvage_yields.count(), 2)

    def test_patch_omission_preserves_inputs_and_salvage(self):
        self.create_materials()
        self.apply(
            """
kind: itemdefinition
metadata:
  slug: t2-hoplite-head
spec:
  salvage:
    yields:
      - material: craftmaterial.bronze
        quantity: 2
""",
            expected_status=200,
        )
        self.create_recipe()

        self.apply(
            """
kind: craftingrecipe
metadata:
  slug: t2-hoplite-head
spec:
  order: 99
""",
            expected_status=200,
        )
        self.apply(
            """
kind: itemdefinition
metadata:
  slug: t2-hoplite-head
spec:
  description: A newly polished helm.
""",
            expected_status=200,
        )

        recipe = CraftingRecipe.objects.get(world=self.world, slug="t2-hoplite-head")
        self.assertEqual(recipe.order, 99)
        self.assertEqual(recipe.ingredients.count(), 2)
        self.assertEqual(self.output.salvage_yields.count(), 1)

    def test_validation_rejects_duplicate_cross_world_and_invalid_specs(self):
        self.create_materials()

        duplicate_inputs = """
kind: craftingrecipe
metadata:
  slug: duplicate-inputs
spec:
  group: hoplite
  output:
    item_definition: itemdefinition.t2-hoplite-head
  inputs:
    - material: craftmaterial.bronze
      quantity: 2
    - material: craftmaterial.bronze
      quantity: 1
"""
        response = self.apply(duplicate_inputs, expected_status=400)
        self.assertIn("duplicates", str(response.data))

        fractional_input = """
kind: craftingrecipe
metadata:
  slug: fractional-input
spec:
  group: hoplite
  output:
    item_definition: itemdefinition.t2-hoplite-head
  inputs:
    - material: craftmaterial.bronze
      quantity: 1.5
"""
        response = self.apply(fractional_input, expected_status=400)
        self.assertIn("must be an integer", str(response.data))

        invalid_conditions = """
kind: craftingrecipe
metadata:
  slug: invalid-conditions
spec:
  group: hoplite
  output:
    item_definition: itemdefinition.t2-hoplite-head
  inputs:
    - material: craftmaterial.bronze
      quantity: 2
  conditions:
    unsupported: true
"""
        response = self.apply(invalid_conditions, expected_status=400)
        self.assertIn("supported condition operator", str(response.data))

        salvage_only_equipment = """
kind: itemdefinition
metadata:
  slug: salvage-only-helm
  name: a salvage-only helm
spec:
  type: equippable
  equipment_type: head
  salvage:
    only: true
    yields:
      - material: craftmaterial.bronze
        quantity: 1
"""
        response = self.apply(salvage_only_equipment, expected_status=400)
        self.assertIn("cannot be equippable", str(response.data))

        fractional_salvage = """
kind: itemdefinition
metadata:
  slug: fractional-salvage
  name: some captured scraps
spec:
  type: inert
  salvage:
    only: true
    yields:
      - material: craftmaterial.bronze
        quantity: 2.5
"""
        response = self.apply(fractional_salvage, expected_status=400)
        self.assertIn("must be an integer", str(response.data))

        other_config = WorldConfig.objects.create()
        other_world = self.world.__class__.objects.new_world(
            name="Other Crafting World",
            author=self.user,
            config=other_config,
        )
        CraftMaterial.objects.create(
            world=other_world,
            slug="foreign-bronze",
            name="Foreign Bronze",
        )
        cross_world = """
kind: craftingrecipe
metadata:
  slug: cross-world
spec:
  group: hoplite
  output:
    item_definition: itemdefinition.t2-hoplite-head
  inputs:
    - material: craftmaterial.foreign-bronze
      quantity: 1
"""
        response = self.apply(cross_world, expected_status=400)
        self.assertIn("unknown", str(response.data))

    def test_read_only_builder_endpoints_include_canonical_yaml(self):
        self.create_materials()
        self.create_recipe()
        self.apply(
            """
kind: craftingprofile
metadata:
  slug: camp-workshop
  name: Camp Workshop
spec:
  recipes: [craftingrecipe.t2-hoplite-head]
"""
        )

        material_list = self.client.get(
            reverse("builder-craft-material-list", args=[self.world.pk])
        )
        self.assertEqual(material_list.status_code, 200, material_list.data)
        self.assertEqual(material_list.data["count"], 2)

        recipe = CraftingRecipe.objects.get(world=self.world, slug="t2-hoplite-head")
        recipe_detail = self.client.get(
            reverse("builder-crafting-recipe-detail", args=[self.world.pk, recipe.pk])
        )
        self.assertEqual(recipe_detail.status_code, 200, recipe_detail.data)
        self.assertIn("kind: craftingrecipe", recipe_detail.data["yaml"])
        self.assertEqual(recipe_detail.data["inputs"][0]["material"], "craftmaterial.bronze")

        profile = CraftingProfile.objects.get(world=self.world, slug="camp-workshop")
        profile_detail = self.client.get(
            reverse("builder-crafting-profile-detail", args=[self.world.pk, profile.pk])
        )
        self.assertEqual(profile_detail.status_code, 200, profile_detail.data)
        self.assertEqual(profile_detail.data["recipe_count"], 1)
        self.assertIn("craftingrecipe.t2-hoplite-head", profile_detail.data["yaml"])

    def test_explicit_empty_salvage_clears_yields(self):
        bronze, _ = self.create_materials()
        ItemSalvageYield.objects.create(
            item_definition=self.output,
            material=bronze,
            quantity=2,
        )
        self.apply(
            """
kind: itemdefinition
metadata:
  slug: t2-hoplite-head
spec:
  salvage: {}
""",
            expected_status=200,
        )
        self.assertFalse(ItemSalvageYield.objects.filter(item_definition=self.output).exists())

    def test_delete_referenced_material_returns_validation_error(self):
        bronze, _ = self.create_materials()
        CraftingIngredient.objects.create(
            recipe=CraftingRecipe.objects.create(
                world=self.world,
                slug="manual-recipe",
                group="hoplite",
                output_item_definition=self.output,
            ),
            material=bronze,
            quantity=1,
        )
        response = self.apply(
            """
kind: craftmaterial
operation: delete
metadata:
  slug: bronze
""",
            expected_status=400,
        )
        self.assertIn("still referenced", str(response.data))
