import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from builders.currencies import create_currency
from builders.models import (
    CraftMaterial,
    CraftingIngredient,
    CraftingProfile,
    CraftingProfileRecipe,
    CraftingRecipe,
    Currency,
    ItemDefinition,
    ItemSalvageYield,
    MerchantProfile,
    MobDefinition,
    Trigger,
)
from config import constants as adv_consts
from spawns.actions.crafting import (
    CraftItemAction,
    InspectRecipeAction,
    ListMaterialsAction,
    ListRecipesAction,
    ListSalvageItemsAction,
    MAX_SALVAGE_LIST_ITEMS,
    SalvageItemAction,
    _resolve_salvage_item,
)
from spawns.actions.base import ActionError
from spawns.handlers import dispatch_command
from spawns.models import (
    Alias,
    CraftingActionReceipt,
    GameEventOutbox,
    Item,
    Player,
    PlayerMaterialBalance,
)
from spawns.request_segments import append_request_segment
from spawns.state_payloads import serialize_actor
from spawns.wallet import balance_map, mutate_balances
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
from tests.utils import apply_basic_stat_system, capture_game_messages


class CraftingRuntimeTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        apply_basic_stat_system(self.world)
        self.bronze = CraftMaterial.objects.create(
            world=self.world,
            slug="bronze",
            name="Bronze",
            description="Recovered bronze.",
            order=10,
        )
        self.leather = CraftMaterial.objects.create(
            world=self.world,
            slug="leather",
            name="Leather",
            description="Cleaned leather.",
            order=20,
        )
        self.helm_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="blue-crested-helm",
            name="a blue-crested helm",
            keywords="blue crested bronze helm head hoplite",
            description="A close bronze helm with a blue crest.",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "level": 20,
                "quality": adv_consts.ITEM_QUALITY_NORMAL,
                "equipment_type": adv_consts.EQUIPMENT_TYPE_HEAD,
                "armor_class": adv_consts.ARMOR_CLASS_HEAVY,
                "armor": 163,
            },
            attributes={"brawn": 2},
            randomization={
                "version": 1,
                "attributes": [
                    {"key": "grit", "min": 0, "max": 1, "mode": "uniform"},
                ],
            },
        )
        self.recipe = CraftingRecipe.objects.create(
            world=self.world,
            slug="t2-hoplite-head",
            output_item_definition=self.helm_definition,
            group="hoplite",
            order=10,
        )
        CraftingIngredient.objects.create(
            recipe=self.recipe,
            material=self.bronze,
            quantity=8,
        )
        CraftingIngredient.objects.create(
            recipe=self.recipe,
            material=self.leather,
            quantity=2,
        )
        self.profile = CraftingProfile.objects.create(
            world=self.world,
            slug="camp-workshop",
            name="Camp Workshop",
            keywords="camp workshop forge armory",
        )
        CraftingProfileRecipe.objects.create(
            profile=self.profile,
            recipe=self.recipe,
            order=10,
        )
        self.room.crafting_profile = self.profile
        self.room.save(update_fields=["crafting_profile"])

    def _balance(self, material, quantity):
        return PlayerMaterialBalance.objects.create(
            player=self.player,
            material=material,
            quantity=quantity,
        )

    def _price_recipe(self, amount=150):
        self.recipe.cost = amount
        self.recipe.currency = self.currency
        self.recipe.save(update_fields=["cost", "currency", "modified_ts"])

    def _fund(self, amount):
        return mutate_balances(
            self.player,
            {self.currency: amount},
            reason="crafting test setup",
            emit_event=False,
        )

    def _dispatch_text(self, text):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": text},
            )
        return [entry["message"] for entry in messages]

    def _dispatch_text_with_request(self, text, request_id):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": text, "_request_id": request_id},
            )
        return [entry["message"] for entry in messages]

    @staticmethod
    def _message(messages, message_type):
        return next((message for message in messages if message.get("type") == message_type), None)

    def _persian_definition(self, *, slug="persian-scale-coat"):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name="a Persian scale coat",
            keywords="persian scale coat armor spoils",
            item_type=adv_consts.ITEM_TYPE_INERT,
            salvage_only=True,
        )
        ItemSalvageYield.objects.create(
            item_definition=definition,
            material=self.bronze,
            quantity=4,
        )
        ItemSalvageYield.objects.create(
            item_definition=definition,
            material=self.leather,
            quantity=2,
        )
        return definition

    def _add_recipe(
        self,
        *,
        slug: str,
        name: str,
        membership_order: int,
        group: str = "warlord",
        leather_cost: int = 1,
    ):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug=f"{slug}-item",
            name=name,
            keywords=name,
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_HANDS,
                "armor_class": adv_consts.ARMOR_CLASS_LIGHT,
                "armor": 10,
            },
        )
        recipe = CraftingRecipe.objects.create(
            world=self.world,
            slug=slug,
            output_item_definition=definition,
            group=group,
            order=999,
        )
        CraftingIngredient.objects.create(
            recipe=recipe,
            material=self.leather,
            quantity=leather_cost,
        )
        membership = CraftingProfileRecipe.objects.create(
            profile=self.profile,
            recipe=recipe,
            order=membership_order,
        )
        return recipe, definition, membership


class TestCraftingReadCommands(CraftingRuntimeTestCase):
    def test_workshop_room_look_exposes_craft_action(self):
        messages = self._dispatch_text("look")

        message = self._message(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertIn("craft", message["data"]["target"]["actions"])
        self.assertIn("Action available: craft", message["text"])

    def test_workshop_craft_action_deduplicates_trigger_label(self):
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            match="Craft",
            script="/echo -- The forge is ready.",
            display_action_in_room=True,
        )

        messages = self._dispatch_text("look")

        message = self._message(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        actions = message["data"]["target"]["actions"]
        self.assertEqual(
            sum(action.casefold() == "craft" for action in actions),
            1,
        )

    def test_room_without_crafting_profile_has_no_automatic_craft_action(self):
        self.room.crafting_profile = None
        self.room.save(update_fields=["crafting_profile"])

        messages = self._dispatch_text("look")

        message = self._message(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertNotIn("craft", message["data"]["target"]["actions"])

    def test_materials_lists_positive_balances_in_authored_order(self):
        self._balance(self.leather, 6)
        self._balance(self.bronze, 18)
        zero = CraftMaterial.objects.create(
            world=self.world,
            slug="linen",
            name="Linen",
            order=5,
        )
        self._balance(zero, 0)

        result = ListMaterialsAction().execute(self.player.id)

        self.assertEqual(
            [(entry["slug"], entry["quantity"]) for entry in result.data["materials"]],
            [("bronze", 18), ("leather", 6)],
        )
        self.assertIn("Bronze", result.events[0].text)
        self.assertNotIn("Linen", result.events[0].text)

    def test_materials_text_command_has_explicit_empty_state(self):
        messages = self._dispatch_text("materials")
        message = self._message(messages, "cmd.materials.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["materials"], [])
        self.assertEqual(message["text"], "You have no crafting materials.")

    def test_unprefetched_actor_materials_use_one_joined_world_filtered_query(self):
        self._balance(self.bronze, 3)
        self._balance(self.leather, 4)
        other_world = World.objects.create(name="Other Material World")
        foreign_material = CraftMaterial.objects.create(
            world=other_world,
            slug="foreign-iron",
            name="Foreign Iron",
        )
        self._balance(foreign_material, 99)
        player = Player.objects.select_related(
            "world",
            "world__config",
            "world__context",
            "world__context__instance_of",
            "room",
            "equipment",
            "user",
            "config",
        ).get(pk=self.player.id)
        self.assertNotIn(
            "material_balances",
            getattr(player, "_prefetched_objects_cache", {}),
        )

        with CaptureQueriesContext(connection) as queries:
            actor = serialize_actor(player, player.room)

        sql = [query["sql"] for query in queries]
        balance_queries = [
            query for query in sql if '"spawns_playermaterialbalance"' in query
        ]
        standalone_material_queries = [
            query for query in sql if 'FROM "builders_craftmaterial"' in query
        ]
        self.assertEqual(actor.materials, {"bronze": 3, "leather": 4})
        self.assertEqual(len(balance_queries), 1)
        self.assertIn('JOIN "builders_craftmaterial"', balance_queries[0])
        self.assertIn('"builders_craftmaterial"."world_id"', balance_queries[0])
        self.assertEqual(standalone_material_queries, [])

    def test_recipe_preview_merges_fixed_and_random_attribute_ranges(self):
        self._balance(self.bronze, 8)
        self._balance(self.leather, 1)

        result = InspectRecipeAction().execute(self.player.id, "blue crested helm")
        recipe = result.data["recipe"]

        self.assertEqual(recipe["output"]["armor"], 163)
        self.assertEqual(recipe["output"]["attributes"]["brawn"], {"min": 2, "max": 2})
        self.assertEqual(recipe["output"]["attributes"]["grit"]["min"], 0)
        self.assertEqual(recipe["output"]["attributes"]["grit"]["max"], 1)
        self.assertEqual(recipe["missing"], 1)
        self.assertFalse(recipe["ready"])

    def test_recipe_filters_and_ready_status_use_one_balance_snapshot(self):
        self._balance(self.bronze, 8)
        self._balance(self.leather, 2)

        result = ListRecipesAction().execute(self.player.id, "ready")

        self.assertEqual(len(result.data["recipes"]), 1)
        self.assertTrue(result.data["recipes"][0]["ready"])
        self.assertEqual(result.data["recipes"][0]["group"], "hoplite")

    def test_priced_recipe_list_and_inspect_include_wallet_readiness(self):
        self._price_recipe(150)
        self._balance(self.bronze, 8)
        self._balance(self.leather, 2)
        self._fund(149)

        listed = ListRecipesAction().execute(self.player.id)
        inspected = InspectRecipeAction().execute(self.player.id, "1")

        expected_cost = {
            "amount": 150,
            "currency": "obol",
            "display": "150 Obols",
        }
        for recipe in (listed.data["recipes"][0], inspected.data["recipe"]):
            self.assertEqual(recipe["cost"], expected_cost)
            self.assertEqual(recipe["currency_owned"], 149)
            self.assertEqual(recipe["currency_missing"], 1)
            self.assertEqual(recipe["currency_missing_display"], "1 Obol")
            self.assertFalse(recipe["ready"])
        self.assertIn("need 1 Obol", listed.events[0].text)
        self.assertIn("Cost: 150 Obols", inspected.events[0].text)
        self.assertIn("Obols: 149 / 150", inspected.events[0].text)

        self._fund(1)
        ready = ListRecipesAction().execute(self.player.id, "ready")
        self.assertEqual(len(ready.data["recipes"]), 1)
        self.assertTrue(ready.data["recipes"][0]["ready"])

    def test_authored_recipe_groups_are_available_as_filters(self):
        custom_recipe, _definition, _membership = self._add_recipe(
            slug="field-armorer-gloves",
            name="a pair of field armorer gloves",
            membership_order=20,
            group="Field-Armorer",
        )

        result = ListRecipesAction().execute(self.player.id, "field-armorer")

        self.assertIn("field-armorer", result.data["filters"])
        self.assertEqual(
            [(recipe["number"], recipe["slug"]) for recipe in result.data["recipes"]],
            [(2, custom_recipe.slug)],
        )

    def test_recipes_follow_profile_order_instead_of_recipe_metadata(self):
        later_membership = CraftingProfileRecipe.objects.get(
            profile=self.profile,
            recipe=self.recipe,
        )
        later_membership.order = 20
        later_membership.save(update_fields=["order"])
        first_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="profile-first-helm",
            name="a profile-first helm",
            keywords="profile first helm",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_HEAD,
                "armor_class": adv_consts.ARMOR_CLASS_HEAVY,
                "armor": 150,
            },
        )
        first_recipe = CraftingRecipe.objects.create(
            world=self.world,
            slug="profile-first-recipe",
            output_item_definition=first_definition,
            group="warlord",
            order=999,
        )
        CraftingProfileRecipe.objects.create(
            profile=self.profile,
            recipe=first_recipe,
            order=10,
        )

        result = ListRecipesAction().execute(self.player.id)

        self.assertEqual(
            [recipe["slug"] for recipe in result.data["recipes"]],
            ["profile-first-recipe", "t2-hoplite-head"],
        )
        self.assertEqual(
            [recipe["number"] for recipe in result.data["recipes"]],
            [1, 2],
        )

        filtered = ListRecipesAction().execute(self.player.id, "hoplite")
        self.assertEqual(
            [(recipe["number"], recipe["slug"]) for recipe in filtered.data["recipes"]],
            [(2, "t2-hoplite-head")],
        )
        inspected = InspectRecipeAction().execute(self.player.id, "2")
        self.assertEqual(inspected.data["recipe"]["slug"], "t2-hoplite-head")
        explicit = InspectRecipeAction().execute(
            self.player.id,
            f"craftingrecipe.{self.recipe.id}",
        )
        self.assertEqual(explicit.data["recipe"]["slug"], "t2-hoplite-head")

    def test_full_t2_sized_catalog_is_loaded_with_bounded_queries(self):
        self._price_recipe(90)
        self._fund(90)
        for index in range(44):
            definition = ItemDefinition.objects.create(
                world=self.world,
                slug=f"catalog-item-{index}",
                name=f"a catalog item {index}",
                keywords=f"catalog item {index}",
                item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
                base_properties={
                    "equipment_type": adv_consts.EQUIPMENT_TYPE_HANDS,
                    "armor_class": adv_consts.ARMOR_CLASS_LIGHT,
                    "armor": 10,
                },
            )
            recipe = CraftingRecipe.objects.create(
                world=self.world,
                slug=f"catalog-recipe-{index}",
                output_item_definition=definition,
                group="assassin",
                order=index,
                cost=90,
                currency=self.currency,
            )
            CraftingIngredient.objects.create(
                recipe=recipe,
                material=self.leather,
                quantity=1,
            )
            CraftingProfileRecipe.objects.create(
                profile=self.profile,
                recipe=recipe,
                order=index + 20,
            )

        with CaptureQueriesContext(connection) as queries:
            result = ListRecipesAction().execute(self.player.id)

        self.assertEqual(len(result.data["recipes"]), 45)
        self.assertEqual(
            [recipe["number"] for recipe in result.data["recipes"]],
            list(range(1, 46)),
        )
        self.assertLessEqual(
            len(queries),
            11,
            "A 45-recipe catalog should not query once per recipe or ingredient.",
        )
        wallet_queries = [
            query["sql"]
            for query in queries
            if 'FROM "spawns_playercurrencybalance"' in query["sql"]
        ]
        self.assertEqual(
            len(wallet_queries),
            1,
            "A priced catalog should load the wallet once, not once per recipe.",
        )

    def test_unknown_recipe_filter_returns_structured_command_error(self):
        messages = self._dispatch_text("recipes sandals")
        message = self._message(messages, "cmd.recipes.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "unknown_recipe_filter")
        self.assertIn("ready", message["data"]["filters"])

    def test_bare_recipe_and_craft_list_the_same_numbered_catalog(self):
        catalogs = []
        for command in ("recipes", "recipe", "craft"):
            messages = self._dispatch_text(command)
            message = self._message(messages, "cmd.recipes.success")
            self.assertIsNotNone(message)
            self.assertEqual(message["data"]["operation"], "list")
            self.assertIn("1. a blue-crested helm", message["text"])
            catalogs.append(
                [(recipe["number"], recipe["slug"]) for recipe in message["data"]["recipes"]]
            )
        self.assertEqual(catalogs, [[(1, "t2-hoplite-head")]] * 3)
        self.assertFalse(self.player.inventory.exists())
        self.assertFalse(PlayerMaterialBalance.objects.filter(player=self.player).exists())

    def test_structured_zero_is_an_index_error_not_a_bare_recipe_list(self):
        for command in ("recipe", "craft"):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type=command,
                    player_id=self.player.id,
                    payload={"recipe": 0},
                )
            error = self._message(
                [entry["message"] for entry in messages],
                f"cmd.{command}.error",
            )
            self.assertEqual(error["data"]["code"], "recipe_index_not_found")

    def test_number_selects_between_recipes_with_the_same_output_name(self):
        duplicate, _definition, _membership = self._add_recipe(
            slug="duplicate-blue-helm",
            name="a blue-crested helm",
            membership_order=20,
        )

        with self.assertRaises(ActionError) as error:
            InspectRecipeAction().execute(self.player.id, "blue crested helm")
        self.assertEqual(error.exception.code, "ambiguous_recipe")

        inspected = InspectRecipeAction().execute(self.player.id, "2")
        self.assertEqual(inspected.data["recipe"]["slug"], duplicate.slug)

    def test_recipe_names_may_contain_provider_suffix_words(self):
        with_recipe, _definition, _membership = self._add_recipe(
            slug="bronze-trim-helm",
            name="a helm with bronze trim",
            membership_order=20,
        )
        at_recipe, _definition, _membership = self._add_recipe(
            slug="dawn-blade",
            name="a blade at dawn",
            membership_order=30,
        )

        messages = self._dispatch_text("recipe helm with bronze trim")
        inspected = self._message(messages, "cmd.recipe.success")
        self.assertEqual(inspected["data"]["recipe"]["slug"], with_recipe.slug)

        messages = self._dispatch_text("recipe blade at dawn")
        inspected = self._message(messages, "cmd.recipe.success")
        self.assertEqual(inspected["data"]["recipe"]["slug"], at_recipe.slug)

    def test_recipe_index_rejects_negative_out_of_range_and_oversized_values(self):
        for selector in ("-1", "2", "9" * 5000):
            with self.assertRaises(ActionError) as error:
                InspectRecipeAction().execute(self.player.id, selector)
            self.assertEqual(error.exception.code, "recipe_index_not_found")

        with self.assertRaises(ActionError) as error:
            InspectRecipeAction().execute(
                self.player.id,
                "craftingrecipe." + ("9" * 5000),
            )
        self.assertEqual(error.exception.code, "recipe_not_found")

    def test_no_workshop_is_a_clear_error(self):
        self.room.crafting_profile = None
        self.room.save(update_fields=["crafting_profile"])

        messages = self._dispatch_text("recipes")

        message = self._message(messages, "cmd.recipes.error")
        self.assertEqual(message["data"]["code"], "no_crafting_provider")

    def test_npc_provider_is_isolated_to_players_runtime_world(self):
        self.room.crafting_profile = None
        self.room.save(update_fields=["crafting_profile"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="damon-armorer",
            name="Damon",
            keywords="damon armorer smith",
            attackable=False,
            crafting_profile=self.profile,
            crafting_availability="alive_and_present",
            base_properties={"health_max": 20},
        )
        definition.spawn(self.room, self.spawn_world)
        other_runtime = self.world.create_spawn_world()
        definition.spawn(self.room, other_runtime)

        result = ListRecipesAction().execute(self.player.id)

        self.assertEqual(len(result.data["providers"]), 1)
        self.assertEqual(result.data["providers"][0]["type"], "mob")
        self.assertEqual(result.data["providers"][0]["name"], "Damon")

    def test_dead_alive_and_present_npc_is_not_a_provider(self):
        self.room.crafting_profile = None
        self.room.save(update_fields=["crafting_profile"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="wounded-armorer",
            name="Damon",
            keywords="damon armorer smith",
            attackable=False,
            crafting_profile=self.profile,
            crafting_availability="alive_and_present",
            base_properties={"health_max": 20},
        )
        mob = definition.spawn(self.room, self.spawn_world)
        mob.health = 0
        mob.save(update_fields=["health"])

        messages = self._dispatch_text("recipes")

        error = self._message(messages, "cmd.recipes.error")
        self.assertEqual(error["data"]["code"], "no_crafting_provider")

    def test_duplicate_room_and_npc_provider_requires_explicit_provider(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="damon-armorer",
            name="Damon",
            keywords="damon armorer smith",
            attackable=False,
            crafting_profile=self.profile,
            base_properties={"health_max": 20},
        )
        definition.spawn(self.room, self.spawn_world)
        self._balance(self.bronze, 8)
        self._balance(self.leather, 2)

        messages = self._dispatch_text("craft 1")
        error = self._message(messages, "cmd.craft.error")
        self.assertEqual(error["data"]["code"], "ambiguous_crafting_provider")

        inspected = InspectRecipeAction().execute(self.player.id, "1")
        self.assertEqual(len(inspected.data["recipe"]["providers"]), 2)

        messages = self._dispatch_text("craft 1 with damon")
        self.assertIsNotNone(self._message(messages, "cmd.craft.success"))

    def test_provider_scoped_catalog_keeps_global_recipe_numbers(self):
        specialist_profile = CraftingProfile.objects.create(
            world=self.world,
            slug="specialist-workshop",
            name="Specialist Workshop",
            keywords="specialist damon",
        )
        specialist_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="specialist-gloves",
            name="a pair of specialist gloves",
            keywords="specialist gloves",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_HANDS,
                "armor_class": adv_consts.ARMOR_CLASS_LIGHT,
                "armor": 10,
            },
        )
        specialist_recipe = CraftingRecipe.objects.create(
            world=self.world,
            slug="specialist-gloves",
            output_item_definition=specialist_definition,
            group="assassin",
        )
        CraftingIngredient.objects.create(
            recipe=specialist_recipe,
            material=self.leather,
            quantity=1,
        )
        CraftingProfileRecipe.objects.create(
            profile=specialist_profile,
            recipe=specialist_recipe,
            order=10,
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="damon-specialist",
            name="Damon",
            keywords="damon specialist",
            attackable=False,
            crafting_profile=specialist_profile,
            base_properties={"health_max": 20},
        )
        mob_definition.spawn(self.room, self.spawn_world)

        global_catalog = ListRecipesAction().execute(self.player.id)
        scoped_catalog = ListRecipesAction().execute(
            self.player.id,
            provider_selector="damon",
        )
        self.assertEqual(
            [(recipe["number"], recipe["slug"]) for recipe in global_catalog.data["recipes"]],
            [(1, "t2-hoplite-head"), (2, "specialist-gloves")],
        )
        self.assertEqual(
            [(recipe["number"], recipe["slug"]) for recipe in scoped_catalog.data["recipes"]],
            [(2, "specialist-gloves")],
        )
        with self.assertRaises(ActionError) as error:
            InspectRecipeAction().execute(self.player.id, "1", "damon")
        self.assertEqual(error.exception.code, "recipe_index_not_found")

        self._balance(self.leather, 1)
        crafted = CraftItemAction().execute(self.player.id, "2", "damon")
        self.assertEqual(crafted.data["recipe"]["slug"], "specialist-gloves")
        self.assertTrue(
            self.player.inventory.filter(definition=specialist_definition).exists()
        )


class TestCraftingMutation(CraftingRuntimeTestCase):
    def test_numeric_craft_uses_profile_order_instead_of_database_id(self):
        base_membership = CraftingProfileRecipe.objects.get(
            profile=self.profile,
            recipe=self.recipe,
        )
        base_membership.order = 20
        base_membership.save(update_fields=["order"])
        decoy_recipe, decoy_definition, _membership = self._add_recipe(
            slug="catalog-first-gloves",
            name="a pair of catalog-first gloves",
            membership_order=10,
        )
        self.assertGreater(decoy_recipe.id, self.recipe.id)
        self._balance(self.bronze, 8)
        self._balance(self.leather, 3)

        result = CraftItemAction().execute(self.player.id, "2")

        self.assertEqual(result.data["recipe"]["slug"], self.recipe.slug)
        self.assertTrue(
            self.player.inventory.filter(definition=self.helm_definition).exists()
        )
        self.assertFalse(
            self.player.inventory.filter(definition=decoy_definition).exists()
        )

    def test_craft_atomically_deducts_and_spawns_one_persisted_roll(self):
        bronze = self._balance(self.bronze, 10)
        leather = self._balance(self.leather, 3)

        result = CraftItemAction().execute(self.player.id, "blue crested helm")

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual(bronze.quantity, 2)
        self.assertEqual(leather.quantity, 1)
        crafted = self.player.inventory.get(definition=self.helm_definition)
        self.assertEqual(crafted.attributes["brawn"], 2)
        self.assertIn(crafted.attributes["grit"], (0, 1))
        self.assertTrue(crafted.roll_metadata["randomized"])
        self.assertEqual(crafted.roll_metadata["source_recipe_slug"], self.recipe.slug)
        self.assertEqual(result.data["item"]["key"], crafted.key)
        self.assertEqual(result.events[0].type, "cmd.craft.success")
        self.assertEqual(
            list(GameEventOutbox.objects.order_by("sequence").values_list("event_type", flat=True)),
            ["crafting.item.crafted", "crafting.material.changed"],
        )

    def test_priced_craft_atomically_debits_wallet_and_records_cost(self):
        self._price_recipe(150)
        self._fund(200)
        bronze = self._balance(self.bronze, 10)
        leather = self._balance(self.leather, 3)

        result = CraftItemAction().execute(self.player.id, "blue crested helm")

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.player.refresh_from_db()
        expected_cost = {
            "amount": 150,
            "currency": "obol",
            "display": "150 Obols",
        }
        self.assertEqual(balance_map(self.player), {"obol": 50})
        self.assertEqual((bronze.quantity, leather.quantity), (2, 1))
        self.assertEqual(result.data["cost"], expected_cost)
        self.assertEqual(result.data["recipe"]["cost"], expected_cost)
        self.assertEqual(result.data["actor"]["economy"]["balances"]["obol"], 50)
        self.assertIn("You pay 150 Obols.", result.events[0].text)
        outbox = list(GameEventOutbox.objects.order_by("sequence"))
        self.assertEqual(
            [event.event_type for event in outbox],
            [
                "currency.balances_changed",
                "crafting.item.crafted",
                "crafting.material.changed",
            ],
        )
        self.assertEqual(outbox[0].data["changes"][0]["delta"], -150)
        self.assertEqual(outbox[1].data["cost"], expected_cost)

    def test_insufficient_currency_consumes_nothing(self):
        self._price_recipe(150)
        self._fund(149)
        bronze = self._balance(self.bronze, 8)
        leather = self._balance(self.leather, 2)
        self.player.refresh_from_db()
        revision_before = self.player.wallet_revision

        messages = self._dispatch_text("craft blue crested helm")

        error = self._message(messages, "cmd.craft.error")
        self.assertEqual(error["data"]["code"], "insufficient_currency")
        self.assertEqual(error["data"]["cost"]["amount"], 150)
        self.assertEqual(error["data"]["owned"], 149)
        self.assertEqual(error["data"]["missing"], 1)
        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(balance_map(self.player), {"obol": 149})
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertFalse(self.player.inventory.filter(definition=self.helm_definition).exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_spawn_failure_rolls_back_currency_and_material_debits(self):
        self._price_recipe(150)
        self._fund(150)
        bronze = self._balance(self.bronze, 8)
        leather = self._balance(self.leather, 2)
        self.player.refresh_from_db()
        revision_before = self.player.wallet_revision

        with patch(
            "spawns.actions.crafting.spawn_item_from_definition",
            side_effect=RuntimeError("simulated spawn failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated spawn failure"):
                CraftItemAction().execute(self.player.id, "blue crested helm")

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(balance_map(self.player), {"obol": 150})
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertFalse(self.player.inventory.filter(definition=self.helm_definition).exists())
        self.assertFalse(CraftingActionReceipt.objects.exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_insufficient_materials_change_nothing(self):
        bronze = self._balance(self.bronze, 7)
        leather = self._balance(self.leather, 2)

        messages = self._dispatch_text("craft blue crested helm")

        error = self._message(messages, "cmd.craft.error")
        self.assertEqual(error["data"]["code"], "insufficient_materials")
        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual(bronze.quantity, 7)
        self.assertEqual(leather.quantity, 2)
        self.assertFalse(self.player.inventory.filter(definition=self.helm_definition).exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_condition_failure_changes_nothing(self):
        self.recipe.conditions = {"gte": ["player.level", 20]}
        self.recipe.failure_message = "You are not ready for this armor."
        self.recipe.save(update_fields=["conditions", "failure_message"])
        bronze = self._balance(self.bronze, 8)
        leather = self._balance(self.leather, 2)

        messages = self._dispatch_text("craft blue crested helm")

        error = self._message(messages, "cmd.craft.error")
        self.assertEqual(error["data"]["code"], "recipe_conditions_not_met")
        self.assertEqual(error["text"], "You are not ready for this armor.")
        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))

    def test_request_receipt_replays_success_without_spending_or_enqueuing_again(self):
        request_id = uuid.uuid4()
        bronze = self._balance(self.bronze, 16)
        leather = self._balance(self.leather, 4)

        first = CraftItemAction().execute(
            self.player.id,
            "blue crested helm",
            request_id=request_id,
        )
        receipt = CraftingActionReceipt.objects.get()
        self.assertNotIn("actor", receipt.result)
        self.assertNotIn("materials", receipt.result)

        # A retry must replay the operation snapshot without rolling current
        # player state backward to the moment of the first response.
        bronze.quantity = 9
        bronze.save(update_fields=["quantity"])
        mutate_balances(
            self.player,
            {self.currency: 77},
            reason="receipt replay test setup",
            emit_event=False,
        )
        replay = CraftItemAction().execute(
            self.player.id,
            "blue crested helm",
            request_id=request_id,
        )

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (9, 2))
        self.assertEqual(self.player.inventory.filter(definition=self.helm_definition).count(), 1)
        self.assertFalse(first.data["replayed"])
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(replay.data["actor"]["economy"]["balances"]["obol"], 77)
        replay_bronze = next(
            entry for entry in replay.data["materials"] if entry["slug"] == "bronze"
        )
        self.assertEqual(replay_bronze["quantity"], 9)
        self.assertEqual(first.data["item"]["key"], replay.data["item"]["key"])
        self.assertIn("Craft already completed", replay.events[0].text)
        self.assertNotIn("You spend", replay.events[0].text)
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_priced_craft_receipt_replay_does_not_debit_wallet_twice(self):
        self._price_recipe(150)
        self._fund(300)
        self._balance(self.bronze, 16)
        self._balance(self.leather, 4)
        request_id = uuid.uuid4()

        first = CraftItemAction().execute(
            self.player.id,
            "blue crested helm",
            request_id=request_id,
        )
        replay = CraftItemAction().execute(
            self.player.id,
            "blue crested helm",
            request_id=request_id,
        )

        self.player.refresh_from_db()
        self.assertEqual(balance_map(self.player), {"obol": 150})
        self.assertFalse(first.data["replayed"])
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(first.data["cost"], replay.data["cost"])
        self.assertEqual(replay.data["cost"]["amount"], 150)
        self.assertEqual(self.player.inventory.filter(definition=self.helm_definition).count(), 1)
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(GameEventOutbox.objects.count(), 3)

    def test_numeric_craft_replay_does_not_retarget_a_reordered_catalog(self):
        base_membership = CraftingProfileRecipe.objects.get(
            profile=self.profile,
            recipe=self.recipe,
        )
        base_membership.order = 20
        base_membership.save(update_fields=["order"])
        _decoy_recipe, decoy_definition, decoy_membership = self._add_recipe(
            slug="replay-first-gloves",
            name="a pair of replay-first gloves",
            membership_order=10,
        )
        bronze = self._balance(self.bronze, 16)
        leather = self._balance(self.leather, 4)
        request_id = uuid.uuid4()

        initial = CraftItemAction().execute(
            self.player.id,
            "2",
            request_id=request_id,
        )
        base_membership.order = 5
        base_membership.save(update_fields=["order"])
        decoy_membership.order = 30
        decoy_membership.save(update_fields=["order"])
        replay = CraftItemAction().execute(
            self.player.id,
            "2",
            request_id=request_id,
        )

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual(initial.data["recipe"]["slug"], self.recipe.slug)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(
            self.player.inventory.filter(definition=self.helm_definition).count(),
            1,
        )
        self.assertFalse(
            self.player.inventory.filter(definition=decoy_definition).exists()
        )

    def test_alias_retry_preserves_request_identity(self):
        Alias.objects.create(
            player=self.player,
            match="forge",
            replacement="craft blue crested helm",
        )
        bronze = self._balance(self.bronze, 16)
        leather = self._balance(self.leather, 4)
        request_id = uuid.uuid4()

        responses = []
        for _attempt in range(2):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    player_id=self.player.id,
                    payload={
                        "text": "forge",
                        "_request_id": request_id,
                        "_request_segment": 7,
                    },
                )
            responses.append(
                self._message(
                    [entry["message"] for entry in messages],
                    "cmd.craft.success",
                )
            )

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(self.player.inventory.filter(definition=self.helm_definition).count(), 1)
        self.assertFalse(responses[0]["data"]["replayed"])
        self.assertTrue(responses[1]["data"]["replayed"])
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(CraftingActionReceipt.objects.get().segment, "r.7")
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_changed_alias_cannot_reuse_request_path_for_another_mutation(self):
        ItemSalvageYield.objects.create(
            item_definition=self.helm_definition,
            material=self.bronze,
            quantity=1,
        )
        alias = Alias.objects.create(
            player=self.player,
            match="forge",
            replacement="craft blue crested helm",
        )
        bronze = self._balance(self.bronze, 8)
        leather = self._balance(self.leather, 2)
        request_id = uuid.uuid4()

        self._dispatch_text_with_request("forge", request_id)
        crafted = self.player.inventory.get(definition=self.helm_definition)
        alias.replacement = f"salvage {crafted.key}"
        alias.save(update_fields=["replacement"])
        messages = self._dispatch_text_with_request("forge", request_id)

        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "idempotency_conflict")
        self.assertEqual(error["data"]["original_action"], "craft")
        self.assertEqual(error["data"]["requested_action"], "salvage")
        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (0, 0))
        self.assertTrue(Item.objects.filter(pk=crafted.id).exists())
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(CraftingActionReceipt.objects.get().action, "craft")
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_history_retry_preserves_request_identity(self):
        bronze = self._balance(self.bronze, 16)
        leather = self._balance(self.leather, 4)
        request_id = uuid.uuid4()
        self.player.command_history = ["craft blue crested helm"]
        self.player.save(update_fields=["command_history"])

        responses = []
        for _attempt in range(2):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    player_id=self.player.id,
                    payload={
                        "text": "!1",
                        "_request_id": request_id,
                        "_request_segment": 9,
                    },
                )
            responses.append(
                self._message(
                    [entry["message"] for entry in messages],
                    "cmd.craft.success",
                )
            )

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(self.player.inventory.filter(definition=self.helm_definition).count(), 1)
        self.assertFalse(responses[0]["data"]["replayed"])
        self.assertTrue(responses[1]["data"]["replayed"])
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(CraftingActionReceipt.objects.get().segment, "r.9")
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_history_replayed_chain_retry_uses_distinct_hierarchical_receipts(self):
        bronze = self._balance(self.bronze, 32)
        leather = self._balance(self.leather, 8)
        request_id = uuid.uuid4()
        self.player.command_history = [
            "craft blue crested helm ; craft blue crested helm"
        ]
        self.player.save(update_fields=["command_history"])

        attempts = []
        for _attempt in range(2):
            with capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    player_id=self.player.id,
                    payload={
                        "text": "!1",
                        "_request_id": request_id,
                    },
                )
            attempts.append(
                [
                    entry["message"]
                    for entry in messages
                    if entry["message"].get("type") == "cmd.craft.success"
                ]
            )

        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (16, 4))
        self.assertEqual(self.player.inventory.filter(definition=self.helm_definition).count(), 2)
        self.assertEqual([len(attempt) for attempt in attempts], [2, 2])
        self.assertTrue(all(not message["data"]["replayed"] for message in attempts[0]))
        self.assertTrue(all(message["data"]["replayed"] for message in attempts[1]))
        self.assertEqual(
            list(CraftingActionReceipt.objects.order_by("segment").values_list(
                "segment", flat=True
            )),
            ["r.0", "r.1"],
        )
        self.assertEqual(GameEventOutbox.objects.count(), 4)

    def _assert_history_chain_is_rejected(self, command):
        bronze = self._balance(self.bronze, 8)
        leather = self._balance(self.leather, 2)
        self.player.command_history = ["craft blue crested helm"]
        self.player.save(update_fields=["command_history"])

        messages = self._dispatch_text(command)

        error = self._message(messages, "cmd.history.error")
        self.assertEqual(
            error["text"],
            "History references cannot be used inside command chains.",
        )
        self.assertEqual(error["data"]["code"], "history_reference_in_chain")
        self.assertEqual(error["data"]["references"], ["!1"])
        bronze.refresh_from_db()
        leather.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual((bronze.quantity, leather.quantity), (8, 2))
        self.assertEqual(self.player.command_history, ["craft blue crested helm"])
        self.assertFalse(self.player.inventory.filter(definition=self.helm_definition).exists())
        self.assertFalse(CraftingActionReceipt.objects.exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_history_reference_cannot_appear_in_a_command_chain(self):
        commands = (
            "!1 ; craft blue crested helm",
            "craft blue crested helm ; !1",
        )
        for command in commands:
            with self.subTest(command=command):
                self._assert_history_chain_is_rejected(command)
                self.player.material_balances.all().delete()

    def test_deep_request_segment_paths_stay_bounded_and_distinct(self):
        left = "r"
        right = "r"
        for _depth in range(100):
            left = append_request_segment(left, 0)
            right = append_request_segment(right, 1)

        self.assertLessEqual(len(left), 128)
        self.assertLessEqual(len(right), 128)
        self.assertNotEqual(left, right)


class TestSalvageRuntime(CraftingRuntimeTestCase):
    def test_bare_salvage_lists_only_eligible_items_with_one_based_numbers(self):
        definition = self._persian_definition()
        first = definition.spawn(self.player, self.spawn_world)
        unsalvageable = self.helm_definition.spawn(self.player, self.spawn_world)
        second = definition.spawn(self.player, self.spawn_world)

        messages = self._dispatch_text("salvage")

        message = self._message(messages, "cmd.salvage.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["operation"], "list")
        self.assertEqual(
            [item["number"] for item in message["data"]["items"]],
            [1, 2],
        )
        self.assertEqual(
            [item["key"] for item in message["data"]["items"]],
            [first.key, second.key],
        )
        self.assertIn("1. a Persian scale coat", message["text"])
        self.assertIn("2. a Persian scale coat", message["text"])
        self.assertTrue(Item.objects.filter(pk=unsalvageable.id).exists())
        self.assertEqual(self.player.inventory.count(), 3)
        self.assertFalse(
            PlayerMaterialBalance.objects.filter(player=self.player).exists()
        )

    def test_bare_salvage_has_an_explicit_empty_state(self):
        messages = self._dispatch_text("salvage")

        message = self._message(messages, "cmd.salvage.success")
        self.assertEqual(message["data"]["operation"], "list")
        self.assertEqual(message["data"]["items"], [])
        self.assertEqual(message["text"], "You have nothing you can salvage.")

    def test_numeric_salvage_uses_the_current_eligible_list_order(self):
        definition = self._persian_definition()
        first = definition.spawn(self.player, self.spawn_world)
        self.helm_definition.spawn(self.player, self.spawn_world)
        second = definition.spawn(self.player, self.spawn_world)

        messages = self._dispatch_text("salvage 2")

        message = self._message(messages, "cmd.salvage.success")
        self.assertEqual(message["data"]["items"][0]["key"], second.key)
        self.assertTrue(Item.objects.filter(pk=first.id).exists())
        self.assertFalse(Item.objects.filter(pk=second.id).exists())

    def test_numbered_list_excludes_equipped_quest_and_nonempty_items(self):
        ItemSalvageYield.objects.create(
            item_definition=self.helm_definition,
            material=self.bronze,
            quantity=2,
        )
        equipped = self.helm_definition.spawn(self.player, self.spawn_world)
        self.player.equipment.head = equipped
        self.player.equipment.save(update_fields=["head"])
        equipped.container = self.player.equipment
        equipped.save(update_fields=["container_type", "container_id"])

        quest_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="salvage-quest-token",
            name="a bound campaign token",
            keywords="bound campaign token",
            item_type=adv_consts.ITEM_TYPE_QUEST,
        )
        ItemSalvageYield.objects.create(
            item_definition=quest_definition,
            material=self.bronze,
            quantity=1,
        )
        quest_definition.spawn(self.player, self.spawn_world)

        bag_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="numbered-salvage-bag",
            name="a salvage bag",
            keywords="salvage bag",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        ItemSalvageYield.objects.create(
            item_definition=bag_definition,
            material=self.leather,
            quantity=1,
        )
        bag = bag_definition.spawn(self.player, self.spawn_world)
        self.helm_definition.spawn(bag, self.spawn_world)

        eligible = self._persian_definition().spawn(self.player, self.spawn_world)

        result = ListSalvageItemsAction().execute(self.player.id)

        self.assertEqual(
            [item["key"] for item in result.data["items"]],
            [eligible.key],
        )

    def test_numeric_selector_rejects_zero_and_out_of_range_without_mutation(self):
        item = self._persian_definition().spawn(self.player, self.spawn_world)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="salvage",
                player_id=self.player.id,
                payload={"item": 0},
            )
        error = self._message(
            [entry["message"] for entry in messages],
            "cmd.salvage.error",
        )
        self.assertEqual(error["data"]["code"], "salvage_index_not_found")

        messages = self._dispatch_text("salvage 2")
        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "salvage_index_not_found")

        messages = self._dispatch_text("salvage -1")
        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "salvage_index_not_found")
        self.assertTrue(Item.objects.filter(pk=item.id).exists())
        self.assertFalse(
            PlayerMaterialBalance.objects.filter(player=self.player).exists()
        )

        with self.assertRaises(ActionError) as error:
            _resolve_salvage_item(self.player, "9" * 5000)
        self.assertEqual(error.exception.code, "salvage_index_not_found")

        with self.assertRaises(ActionError) as error:
            _resolve_salvage_item(self.player, "item." + ("9" * 5000))
        self.assertEqual(error.exception.code, "item_not_found")

        with self.assertRaises(ActionError) as error:
            _resolve_salvage_item(self.player, ("9" * 5000) + ".coat")
        self.assertEqual(error.exception.code, "item_not_found")

    def test_salvage_list_is_bounded_without_per_item_queries(self):
        definition = self._persian_definition()
        for _index in range(MAX_SALVAGE_LIST_ITEMS + 1):
            definition.spawn(self.player, self.spawn_world)

        with CaptureQueriesContext(connection) as queries:
            result = ListSalvageItemsAction().execute(self.player.id)

        self.assertEqual(len(result.data["items"]), MAX_SALVAGE_LIST_ITEMS)
        self.assertTrue(result.data["truncated"])
        self.assertIn(
            f"Only the first {MAX_SALVAGE_LIST_ITEMS} items are shown.",
            result.events[0].text,
        )
        self.assertLessEqual(
            len(queries),
            4,
            "The salvage list must not query once per carried item.",
        )

    def test_exact_item_key_resolution_does_not_scan_unrelated_inventory(self):
        for _index in range(75):
            self.helm_definition.spawn(self.player, self.spawn_world)
        definition = self._persian_definition()
        target = definition.spawn(self.player, self.spawn_world)

        with CaptureQueriesContext(connection) as queries:
            resolved = _resolve_salvage_item(self.player, target.key)

        item_queries = [
            query["sql"]
            for query in queries
            if 'FROM "spawns_item"' in query["sql"]
        ]
        self.assertEqual(resolved.id, target.id)
        self.assertEqual(len(item_queries), 1)
        self.assertIn(f'"spawns_item"."id" = {target.id}', item_queries[0])
        self.assertIn("LIMIT 1", item_queries[0])

    def test_single_salvage_physically_deletes_item_and_credits_fixed_yield(self):
        definition = self._persian_definition()
        item = definition.spawn(self.player, self.spawn_world)
        item.attributes = {"brawn": 999}
        item.save(update_fields=["attributes"])

        result = SalvageItemAction().execute(self.player.id, "scale coat")

        self.assertFalse(Item.objects.filter(pk=item.id).exists())
        self.assertEqual(
            PlayerMaterialBalance.objects.get(player=self.player, material=self.bronze).quantity,
            4,
        )
        self.assertEqual(
            PlayerMaterialBalance.objects.get(player=self.player, material=self.leather).quantity,
            2,
        )
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(
            set(result.data["items"][0]),
            {
                "id",
                "key",
                "name",
                "definition_id",
                "definition_slug",
                "salvage_only",
            },
        )
        self.assertEqual(result.data["items"][0]["name"], "a Persian scale coat")
        self.assertNotIn("999", result.events[0].text)
        self.assertEqual(
            list(GameEventOutbox.objects.order_by("sequence").values_list("event_type", flat=True)),
            ["crafting.item.salvaged", "crafting.material.changed"],
        )

    def test_salvage_rejects_ambiguous_inventory_selector(self):
        definition = self._persian_definition()
        definition.spawn(self.player, self.spawn_world)
        second = definition.spawn(self.player, self.spawn_world)

        messages = self._dispatch_text("salvage coat")
        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "ambiguous_item")
        self.assertEqual(len(error["data"]["items"]), 2)

        result = SalvageItemAction().execute(self.player.id, "2.coat")
        self.assertFalse(Item.objects.filter(pk=second.id).exists())

    def test_equipped_items_are_not_salvage_candidates(self):
        ItemSalvageYield.objects.create(
            item_definition=self.helm_definition,
            material=self.bronze,
            quantity=2,
        )
        item = self.helm_definition.spawn(self.player, self.spawn_world)
        self.player.equipment.head = item
        self.player.equipment.save(update_fields=["head"])
        item.container = self.player.equipment
        item.save(update_fields=["container_type", "container_id"])

        messages = self._dispatch_text("salvage helm")

        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "item_not_found")
        self.assertTrue(Item.objects.filter(pk=item.id).exists())

    def test_nonempty_container_cannot_be_salvaged(self):
        bag_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="salvage-bag",
            name="a salvage bag",
            keywords="salvage bag",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        ItemSalvageYield.objects.create(
            item_definition=bag_definition,
            material=self.leather,
            quantity=1,
        )
        bag = bag_definition.spawn(self.player, self.spawn_world)
        self.helm_definition.spawn(bag, self.spawn_world)

        messages = self._dispatch_text("salvage bag")

        error = self._message(messages, "cmd.salvage.error")
        self.assertEqual(error["data"]["code"], "container_not_empty")
        self.assertTrue(Item.objects.filter(pk=bag.id).exists())

    def test_salvage_spoils_is_capped_and_never_selects_ordinary_gear(self):
        persian = self._persian_definition()
        # Keep this bulk fixture to one material per item so expected economy
        # totals stay obvious while still exercising the hard batch cap.
        ItemSalvageYield.objects.filter(
            item_definition=persian,
            material=self.leather,
        ).delete()
        ItemSalvageYield.objects.filter(
            item_definition=persian,
            material=self.bronze,
        ).update(quantity=1)
        for _index in range(101):
            persian.spawn(self.player, self.spawn_world)
        ItemSalvageYield.objects.create(
            item_definition=self.helm_definition,
            material=self.bronze,
            quantity=1,
        )
        ordinary = self.helm_definition.spawn(self.player, self.spawn_world)

        result = SalvageItemAction().execute(self.player.id, spoils=True)

        self.assertEqual(result.data["count"], 100)
        self.assertEqual(result.data["remaining_spoils"], 1)
        self.assertTrue(all(len(item) == 6 for item in result.data["items"]))
        self.assertEqual(self.player.inventory.filter(definition=persian).count(), 1)
        self.assertTrue(self.player.inventory.filter(pk=ordinary.id).exists())
        self.assertEqual(
            PlayerMaterialBalance.objects.get(player=self.player, material=self.bronze).quantity,
            100,
        )
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_structured_string_false_does_not_enable_bulk_salvage(self):
        definition = self._persian_definition()
        first = definition.spawn(self.player, self.spawn_world)
        second = definition.spawn(self.player, self.spawn_world)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="salvage",
                player_id=self.player.id,
                payload={"spoils": "false"},
            )

        message = self._message(
            [entry["message"] for entry in messages],
            "cmd.salvage.success",
        )
        self.assertEqual(message["data"]["operation"], "list")
        self.assertEqual(len(message["data"]["items"]), 2)
        self.assertTrue(Item.objects.filter(pk=first.id).exists())
        self.assertTrue(Item.objects.filter(pk=second.id).exists())
        self.assertFalse(PlayerMaterialBalance.objects.filter(player=self.player).exists())

    def test_action_string_zero_does_not_enable_bulk_salvage(self):
        definition = self._persian_definition()
        selected = definition.spawn(self.player, self.spawn_world)
        untouched = definition.spawn(self.player, self.spawn_world)

        result = SalvageItemAction().execute(
            self.player.id,
            selected.key,
            spoils="0",
        )

        self.assertEqual(result.data["count"], 1)
        self.assertFalse(Item.objects.filter(pk=selected.id).exists())
        self.assertTrue(Item.objects.filter(pk=untouched.id).exists())

    def test_salvage_receipt_replay_does_not_credit_twice(self):
        definition = self._persian_definition()
        item = definition.spawn(self.player, self.spawn_world)
        request_id = uuid.uuid4()

        first = SalvageItemAction().execute(
            self.player.id,
            item.key,
            request_id=request_id,
        )
        replay = SalvageItemAction().execute(
            self.player.id,
            item.key,
            request_id=request_id,
        )

        self.assertFalse(first.data["replayed"])
        self.assertTrue(replay.data["replayed"])
        self.assertIn("Salvage already completed", replay.events[0].text)
        self.assertNotIn("You recover", replay.events[0].text)
        self.assertEqual(
            PlayerMaterialBalance.objects.get(player=self.player, material=self.bronze).quantity,
            4,
        )
        self.assertEqual(GameEventOutbox.objects.count(), 2)

    def test_numeric_salvage_receipt_replay_does_not_retarget_shifted_list(self):
        definition = self._persian_definition()
        first = definition.spawn(self.player, self.spawn_world)
        second = definition.spawn(self.player, self.spawn_world)
        request_id = uuid.uuid4()

        initial = SalvageItemAction().execute(
            self.player.id,
            "1",
            request_id=request_id,
        )
        replay = SalvageItemAction().execute(
            self.player.id,
            "1",
            request_id=request_id,
        )

        self.assertEqual(initial.data["items"][0]["key"], first.key)
        self.assertTrue(replay.data["replayed"])
        self.assertTrue(Item.objects.filter(pk=second.id).exists())
        self.assertEqual(
            PlayerMaterialBalance.objects.get(
                player=self.player,
                material=self.bronze,
            ).quantity,
            4,
        )

    def test_salvage_only_spoils_cannot_be_sold_to_merchants(self):
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="camp-broker",
            name="Camp Broker",
            settlement_currency=self.currency,
        )
        merchant_definition = MobDefinition.objects.create(
            world=self.world,
            slug="camp-broker",
            name="a camp broker",
            keywords="camp broker merchant",
            attackable=False,
            merchant_profile=profile,
            base_properties={"health_max": 20},
        )
        merchant_definition.spawn(self.room, self.spawn_world)
        persian = self._persian_definition()
        item = persian.spawn(self.player, self.spawn_world)

        messages = self._dispatch_text("sell coat to broker")

        error = self._message(messages, "cmd.sell.error")
        self.assertEqual(error["data"]["code"], "salvage_only")
        self.assertTrue(self.player.inventory.filter(pk=item.id).exists())


class TestConcurrentCrafting(TransactionTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user("craft-race@example.com", "p")
        config = WorldConfig.objects.create()
        authored_world = World.objects.new_world(
            name="Craft Race World",
            author=user,
            config=config,
        )
        apply_basic_stat_system(authored_world)
        spawn_world = authored_world.create_spawn_world()
        room = authored_world.zones.first().rooms.first()
        self.player = Player.objects.create(
            name="Smith",
            room=room,
            user=user,
            world=spawn_world,
        )
        bronze = CraftMaterial.objects.create(
            world=authored_world,
            slug="race-bronze",
            name="Bronze",
            order=10,
        )
        definition = ItemDefinition.objects.create(
            world=authored_world,
            slug="race-helm",
            name="a race helm",
            keywords="race helm",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_HEAD,
                "armor_class": adv_consts.ARMOR_CLASS_HEAVY,
                "armor": 10,
            },
        )
        recipe = CraftingRecipe.objects.create(
            world=authored_world,
            slug="race-helm",
            output_item_definition=definition,
            group="hoplite",
            order=10,
        )
        CraftingIngredient.objects.create(
            recipe=recipe,
            material=bronze,
            quantity=8,
        )
        profile = CraftingProfile.objects.create(
            world=authored_world,
            slug="race-forge",
            name="Race Forge",
        )
        CraftingProfileRecipe.objects.create(
            profile=profile,
            recipe=recipe,
            order=10,
        )
        room.crafting_profile = profile
        room.save(update_fields=["crafting_profile"])
        PlayerMaterialBalance.objects.create(
            player=self.player,
            material=bronze,
            quantity=8,
        )
        self.bronze = bronze
        self.definition = definition

    def test_two_simultaneous_crafts_cannot_overspend_one_balance(self):
        barrier = Barrier(2)

        def craft_once():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                CraftItemAction().execute(
                    self.player.id,
                    "race helm",
                    request_id=uuid.uuid4(),
                )
                return "success"
            except ActionError as err:
                return err.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: craft_once(), range(2)))

        balance = PlayerMaterialBalance.objects.get(
            player=self.player,
            material=self.bronze,
        )
        self.assertEqual(sorted(outcomes), ["insufficient_materials", "success"])
        self.assertEqual(balance.quantity, 0)
        self.assertEqual(
            self.player.inventory.filter(definition=self.definition).count(),
            1,
        )
        self.assertEqual(CraftingActionReceipt.objects.count(), 1)
        self.assertEqual(GameEventOutbox.objects.count(), 2)
