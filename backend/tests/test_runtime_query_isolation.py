from types import SimpleNamespace

from builders.models import (
    AbilityDefinition,
    MobDefinition,
    TrainerProfile,
    TrainerProfileAbility,
)
from quests.services.discovery import _source_matches_player
from quests.services.effects import _resolve_effect_mob
from tests.utils import dispatch_text_command  # Initializes the handler registry.
from spawns.actions.abilities import trainer_for_ability_change
from spawns.actions.base import ActionError
from spawns.merchants import resolve_merchant_runtime
from tests.base import WorldTestCase


class RuntimeWorldQueryIsolationTests(WorldTestCase):
    """Runtime lookups must not cross parallel copies of an authored room."""

    def setUp(self):
        super().setUp()
        self.parallel_world = self.world.create_spawn_world(
            instance_ref="parallel-copy",
        )
        self.ability = AbilityDefinition.objects.create(
            world=self.world,
            slug="arena-strike",
            name="Arena Strike",
        )
        profile = TrainerProfile.objects.create(
            world=self.world,
            slug="arena-training",
            name="Arena Training",
        )
        TrainerProfileAbility.objects.create(
            profile=profile,
            ability=self.ability,
            order=0,
        )
        self.mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="arena-guide",
            name="an arena guide",
            keywords="guide arena",
            base_properties={"health_max": 10},
            trainer_profile=profile,
        )
        self.foreign_mob = self.mob_definition.spawn(
            self.room,
            self.parallel_world,
        )

    def test_npc_dialogue_discovery_ignores_mob_in_parallel_runtime(self):
        template = SimpleNamespace(world=self.world)
        source = {
            "type": "npc_dialogue",
            "mob_definition": self.mob_definition.slug,
        }

        self.assertFalse(
            _source_matches_player(
                self.player,
                template,
                source,
            )
        )

        self.mob_definition.spawn(self.room, self.spawn_world)
        self.assertTrue(
            _source_matches_player(
                self.player,
                template,
                source,
            )
        )

    def test_quest_mob_effect_resolution_ignores_parallel_runtime(self):
        template = SimpleNamespace(world=self.world)
        foreign_target = {
            "target": {
                "key": self.foreign_mob.key,
                "definition_id": self.mob_definition.id,
            }
        }
        cases = (
            ({"mob": self.foreign_mob.key}, None),
            ({"mob_definition": self.mob_definition.slug}, None),
            ({"selector": "guide"}, None),
            ({}, foreign_target),
        )

        for effect, event_data in cases:
            with self.subTest(effect=effect, event_data=event_data):
                self.assertIsNone(
                    _resolve_effect_mob(
                        effect,
                        player=self.player,
                        template=template,
                        event_data=event_data,
                    )
                )

        own_mob = self.mob_definition.spawn(self.room, self.spawn_world)
        self.assertEqual(
            _resolve_effect_mob(
                {"mob_definition": self.mob_definition.slug},
                player=self.player,
                template=template,
            ),
            own_mob,
        )
        self.assertEqual(
            _resolve_effect_mob(
                {"selector": "guide"},
                player=self.player,
                template=template,
            ),
            own_mob,
        )

    def test_ability_trainer_lookup_ignores_parallel_runtime(self):
        self.assertIsNone(
            trainer_for_ability_change(self.player, self.ability)
        )

        own_trainer = self.mob_definition.spawn(
            self.room,
            self.spawn_world,
        )
        self.assertEqual(
            trainer_for_ability_change(self.player, self.ability),
            own_trainer,
        )

    def test_merchant_lookup_ignores_parallel_runtime(self):
        with self.assertRaises(ActionError) as raised:
            resolve_merchant_runtime(self.player, "guide")

        self.assertEqual(raised.exception.code, "target_not_found")
