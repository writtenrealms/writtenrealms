import json

from builders.currencies import create_currency
from builders.models import ItemDefinition, MobDefinition
from core.condition_dsl import (
    ConditionContext,
    evaluate_condition,
    resolve_path,
    validate_condition_payload,
)
from core.conditions import evaluate_conditions
from core.scoped_state import STATE_SCOPE_WORLD, set_state_value
from quests.services.predicates import evaluate_condition as evaluate_quest_condition
from spawns.models import Item, Mob, PlayerCurrencyBalance
from tests.base import WorldTestCase


class TestSharedConditionDsl(WorldTestCase):
    def test_actor_balance_does_not_read_a_player_with_the_same_numeric_id(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        PlayerCurrencyBalance.objects.create(
            player=self.player,
            currency=obol,
            amount=19,
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Coinless Guard",
        )
        context = ConditionContext(actor=mob, player=self.player)

        self.assertEqual(resolve_path("actor.balances.obol", context), 0)
        self.assertEqual(resolve_path("player.balances.obol", context), 19)

    def test_shared_context_evaluates_actor_event_and_state_paths(self):
        set_state_value(STATE_SCOPE_WORLD, self.player.world, "weather", "rainy")

        context = ConditionContext(
            actor=self.player,
            player=self.player,
            room=self.player.room,
            world=self.player.world,
            event_data={"command": "pledge"},
        )

        self.assertEqual(resolve_path("actor.archetype", context), self.player.archetype)
        self.assertTrue(
            evaluate_condition(
                {
                    "all": [
                        {"eq": ["actor.archetype", self.player.archetype]},
                        {"eq": ["state.world.weather", "rainy"]},
                        {"eq": ["event.command", "pledge"]},
                    ]
                },
                context=context,
            )
        )

    def test_trigger_structured_conditions_use_shared_context(self):
        payload = json.dumps({
            "not": {
                "eq": ["actor.archetype", "not-a-real-class"],
            }
        })

        result = evaluate_conditions(self.player, payload)

        self.assertTrue(result["result"])

    def test_legacy_room_conditions_isolate_parallel_runtime_world_characters(self):
        self.player.in_game = False
        self.player.save(update_fields=["in_game"])
        evaluator = self.player
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="parallel-sentinel",
            name="Parallel Sentinel",
        )
        other_runtime = self.world.create_spawn_world()
        foreign_player = self.create_player(
            "Foreign Player",
            world=other_runtime,
            room=self.room,
        )
        foreign_player.in_game = True
        foreign_player.save(update_fields=["in_game"])
        Mob.objects.create(
            world=other_runtime,
            room=self.room,
            definition=definition,
            name="Foreign Sentinel",
        )

        self.assertFalse(evaluate_conditions(evaluator, "player_in_room")["result"])
        self.assertFalse(
            evaluate_conditions(
                evaluator,
                f"mob_in_room {definition.id}",
            )["result"]
        )

        local_player = self.create_player(
            "Local Player",
            world=self.spawn_world,
            room=self.room,
        )
        local_player.in_game = True
        local_player.save(update_fields=["in_game"])
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="Local Sentinel",
        )

        self.assertTrue(evaluate_conditions(evaluator, "player_in_room")["result"])
        self.assertTrue(
            evaluate_conditions(
                evaluator,
                f"mob_in_room {definition.id}",
            )["result"]
        )

    def test_quest_predicate_wrapper_uses_shared_context(self):
        self.assertTrue(
            evaluate_quest_condition(
                {"eq": ["event.target.id", 42]},
                player=self.player,
                event_data={"target": {"id": 42}},
            )
        )

    def test_mob_present_resolves_portable_ref_in_actor_runtime_world(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="East Gate Guard",
        )
        condition = {
            "mob_present": "mobdefinition.east-gate-guard",
        }
        context = ConditionContext(actor=self.player)

        with self.assertNumQueries(1):
            self.assertTrue(evaluate_condition(condition, context=context))
        self.assertTrue(
            evaluate_condition(
                {"mob_present": {"ref": "east-gate-guard"}},
                context=context,
            )
        )
        self.assertFalse(
            evaluate_condition(
                {"mob_present": {"ref": "itemdefinition.east-gate-guard"}},
                context=context,
            )
        )

    def test_mob_present_filters_pending_and_other_runtime_world_mobs_and_counts(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="bridge-sentinel",
            name="Bridge Sentinel",
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="Active Sentinel",
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="Pending Sentinel",
            is_pending_deletion=True,
        )
        other_runtime_world = self.world.create_spawn_world()
        Mob.objects.create(
            world=other_runtime_world,
            room=self.room,
            definition=definition,
            name="Other World Sentinel",
        )
        condition = {
            "mob_present": {
                "ref": "mobdefinition.bridge-sentinel",
                "count": 2,
            },
        }
        context = ConditionContext(
            actor=self.player,
            room=self.room,
            world=self.spawn_world,
        )

        self.assertFalse(evaluate_condition(condition, context=context))

        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="Second Active Sentinel",
        )

        self.assertTrue(evaluate_condition(condition, context=context))

    def test_mob_present_validation_requires_ref_and_positive_integer_count(self):
        validate_condition_payload({
            "mob_present": "mobdefinition.east-gate-guard",
        })
        validate_condition_payload({
            "mob_present": {
                "ref": "mobdefinition.east-gate-guard",
                "count": 2,
            }
        })

        for payload in (
            {"mob_present": ""},
            {"mob_present": "itemdefinition.east-gate-guard"},
            {"mob_present": {}},
            {"mob_present": {"ref": "mobdefinition.east-gate-guard", "count": 0}},
            {"mob_present": {"ref": "mobdefinition.east-gate-guard", "count": True}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_condition_payload(payload)

    def test_item_present_resolves_portable_actor_inventory_ref(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        definition.spawn(self.player, self.spawn_world)
        condition = {
            "item_present": {
                "location": "actor_inventory",
                "item": "itemdefinition.barley-seed",
            },
        }

        with self.assertNumQueries(1):
            self.assertTrue(
                evaluate_condition(condition, context=ConditionContext(actor=self.player))
            )

        with self.assertNumQueries(1):
            self.assertTrue(
                evaluate_conditions(
                    self.player,
                    json.dumps(condition),
                    room=self.room,
                    world=self.spawn_world,
                )["result"]
            )

    def test_item_present_distinguishes_typed_numeric_slugs_from_bare_ids(self):
        legacy_id_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="legacy-id-definition",
            name="a legacy ID definition",
        )
        numeric_slug_definition = ItemDefinition.objects.create(
            world=self.world,
            slug=str(legacy_id_definition.id),
            name="a numbered seed",
        )
        numeric_slug_definition.spawn(self.player, self.spawn_world)
        context = ConditionContext(actor=self.player)

        self.assertTrue(
            evaluate_condition(
                {
                    "item_present": {
                        "location": "actor_inventory",
                        "item": f"itemdefinition.{legacy_id_definition.id}",
                    },
                },
                context=context,
            )
        )
        self.assertFalse(
            evaluate_condition(
                {
                    "item_present": {
                        "location": "actor_inventory",
                        "item": legacy_id_definition.id,
                    },
                },
                context=context,
            )
        )

        legacy_id_definition.spawn(self.player, self.spawn_world)
        self.assertTrue(
            evaluate_condition(
                {
                    "item_present": {
                        "location": "actor_inventory",
                        "item": legacy_id_definition.id,
                    },
                },
                context=context,
            )
        )

    def test_item_present_isolates_room_items_by_runtime_world_and_live_state(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        other_runtime_world = self.world.create_spawn_world()
        definition.spawn(self.room, other_runtime_world)
        Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            definition=definition,
            name="a pending barley seedling",
            is_pending_deletion=True,
        )
        condition = {
            "item_present": {
                "location": "room",
                "item": "itemdefinition.barley-seedling",
                "count": 2,
            },
        }
        context = ConditionContext(
            actor=self.player,
            room=self.room,
            world=self.spawn_world,
        )

        self.assertFalse(evaluate_condition(condition, context=context))
        definition.spawn(self.room, self.spawn_world)
        self.assertFalse(evaluate_condition(condition, context=context))
        definition.spawn(self.room, self.spawn_world)
        self.assertTrue(evaluate_condition(condition, context=context))

    def test_item_present_validation_requires_location_item_and_positive_count(self):
        validate_condition_payload({
            "item_present": {
                "location": "room",
                "item": "itemdefinition.barley-seedling",
                "count": 2,
            },
        })

        for payload in (
            {"item_present": "itemdefinition.barley-seedling"},
            {"item_present": {"location": "room"}},
            {
                "item_present": {
                    "location": "equipment",
                    "item": "itemdefinition.barley-seedling",
                },
            },
            {
                "item_present": {
                    "location": "room",
                    "item": "mobdefinition.barley-seedling",
                },
            },
            {
                "item_present": {
                    "location": "room",
                    "item": "itemdefinition.barley-seedling",
                    "count": 0,
                },
            },
            {
                "item_present": {
                    "location": "room",
                    "item": "itemdefinition.barley-seedling",
                    "count": True,
                },
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_condition_payload(payload)
