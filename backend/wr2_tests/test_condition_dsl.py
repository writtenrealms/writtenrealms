import json

from core.condition_dsl import ConditionContext, evaluate_condition, resolve_path
from core.conditions import evaluate_conditions
from core.scoped_state import STATE_SCOPE_WORLD, set_state_value
from quests.services.predicates import evaluate_condition as evaluate_quest_condition
from tests.base import WorldTestCase


class TestSharedConditionDsl(WorldTestCase):
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

    def test_quest_predicate_wrapper_uses_shared_context(self):
        self.assertTrue(
            evaluate_quest_condition(
                {"eq": ["event.target.id", 42]},
                player=self.player,
                event_data={"target": {"id": 42}},
            )
        )
