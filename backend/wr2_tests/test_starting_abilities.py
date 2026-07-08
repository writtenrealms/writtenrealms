from builders.models import AbilityDefinition
from tests.base import WorldTestCase


class TestStartingAbilities(WorldTestCase):
    def _ability(self, slug, name=None, availability=None):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name or slug.replace("-", " ").title(),
            command_verbs=[slug.replace("-", "_")],
            action_type="primary",
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability=availability or {"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={"rounds": 0},
            cooldown={"rounds": 0},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {},
                    "text": {"label": name or slug.replace("-", " ").title()},
                },
            ],
        )

    def test_classless_world_grants_global_starting_abilities(self):
        self._ability("first-aid", "First Aid")
        self.world.config.ability_progression = {
            "max_known": 8,
            "starting_abilities": ["first-aid"],
        }
        self.world.config.save(update_fields=["ability_progression"])

        player = self.create_player("Classless")
        player.initialize(include_starting_equipment=False)

        self.assertEqual(player.known_abilities, ["first-aid"])
        self.assertEqual(player.ability_hotkeys, {"1": "first-aid"})

    def test_starting_ability_conditions_can_target_class(self):
        self._ability(
            "bash",
            "Bash",
            availability={"classes": ["hoplite"], "min_level": 1},
        )
        self.world.config.ability_progression = {
            "max_known": 8,
            "starting_abilities": [
                {
                    "ability": "bash",
                    "conditions": {
                        "eq": ["actor.archetype", "hoplite"],
                    },
                },
            ],
        }
        self.world.config.save(update_fields=["ability_progression"])

        hoplite = self.create_player("Hoplite")
        hoplite.archetype = "hoplite"
        hoplite.initialize(include_starting_equipment=False)

        mystic = self.create_player("Mystic")
        mystic.archetype = "mystic"
        mystic.initialize(include_starting_equipment=False)

        self.assertEqual(hoplite.known_abilities, ["bash"])
        self.assertEqual(hoplite.ability_hotkeys, {"1": "bash"})
        self.assertEqual(mystic.known_abilities, [])
        self.assertEqual(mystic.ability_hotkeys, {})

    def test_starting_abilities_respect_known_ability_cap(self):
        self._ability("bash", "Bash")
        self._ability("guard", "Guard")
        self.world.config.ability_progression = {
            "max_known": 1,
            "starting_abilities": ["bash", "guard"],
        }
        self.world.config.save(update_fields=["ability_progression"])

        player = self.create_player("Limited")
        player.initialize(include_starting_equipment=False)

        self.assertEqual(player.known_abilities, ["bash"])
        self.assertEqual(player.ability_hotkeys, {"1": "bash"})
