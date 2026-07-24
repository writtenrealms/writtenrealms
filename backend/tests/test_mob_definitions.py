import random
import yaml

from rest_framework.reverse import reverse

from builders.models import (
    AbilityDefinition,
    FACTION_TYPE_CORE,
    FACTION_TYPE_REPUTATION,
    Faction,
    ItemDefinition,
    MobDefinition,
)
from config import constants as adv_consts
from spawns.serializers import LoadDefinitionSerializer
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system


class TestMobDefinitions(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)

    def test_spawn_rolls_declared_attributes_and_ignores_stale_keys(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            room_description="A bandit watches the road.",
            base_properties={
                "level": 4,
                "aggression": "aggressive",
                "health_max": 45,
                "attack_power": 6,
                "weapon_damage": 12,
                "target_priority": 9,
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

        mob = definition.spawn(
            self.room,
            self.spawn_world,
            rng=random.Random(7),
        )

        self.assertEqual(mob.definition, definition)
        self.assertEqual(mob.name, "a bandit")
        self.assertEqual(mob.type, adv_consts.MOB_TYPE_HUMANOID)
        self.assertEqual(mob.level, 4)
        self.assertEqual(mob.aggression, adv_consts.MOB_AGGRESSION_ALL)
        self.assertEqual(mob.health_max, 45)
        self.assertEqual(mob.health, 45)
        self.assertEqual(mob.attack_power, 18)
        self.assertEqual(mob.weapon_damage, 12)
        self.assertEqual(mob.target_priority, 9)
        self.assertEqual(mob.attributes, {"brawn": 12.0})
        self.assertEqual(mob.roll_metadata["ignored_attributes"], ["luck"])
        self.assertTrue(mob.roll_metadata["randomized"])

        self.assertEqual(mob.definition_slug_snapshot, "bandit")
        self.assertEqual(mob.room_description, "A bandit watches the road.")

    def test_stable_definition_edits_sync_existing_unmodified_mobs(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="training-dummy",
            name="a training dummy",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={
                "health_max": 20,
                "attack_power": 1,
            },
            attributes={"brawn": 1},
        )
        mob = definition.spawn(self.room, self.spawn_world)

        definition.name = "a reinforced training dummy"
        definition.base_properties = {
            "health_max": 35,
            "attack_power": 3,
        }
        definition.attributes = {"brawn": 4}
        definition.save()

        mob.refresh_from_db()

        self.assertEqual(mob.name, "a reinforced training dummy")
        self.assertEqual(mob.health_max, 35)
        self.assertEqual(mob.health, 35)
        self.assertEqual(mob.attack_power, 7)
        self.assertEqual(mob.attributes, {"brawn": 4})
        self.assertEqual(mob.roll_metadata["randomized"], False)

    def test_load_definition_serializer_resolves_mob_definition(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="shade",
            name="a shade",
        )

        serializer = LoadDefinitionSerializer(
            data={
                "world_id": self.spawn_world.id,
                "actor_type": "player",
                "actor_id": self.player.id,
                "definition_type": "mob",
                "definition_id": "shade",
                "room": self.room.id,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["definition"], definition)


class TestMobDefinitionManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])

    def test_apply_mob_definition_manifest_can_create_definition(self):
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: bandit
  name: a bandit
spec:
  description: A wary roadside thief.
  room_description: A bandit watches the road.
  keywords: bandit thief
  type: humanoid
  aggression: all
  level: 5
  health_max: 42
  attack_power: 7
  weapon_damage: 13
  target_priority: -1
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
        self.assertEqual(resp.data["kind"], "mobdefinition")
        self.assertEqual(resp.data["operation"], "created")

        definition = MobDefinition.objects.get(world=self.world, slug="bandit")
        self.assertEqual(definition.name, "a bandit")
        self.assertEqual(definition.mob_type, adv_consts.MOB_TYPE_HUMANOID)
        self.assertEqual(definition.base_properties["aggression"], adv_consts.MOB_AGGRESSION_ALL)
        self.assertEqual(definition.base_properties["level"], 5)
        self.assertEqual(definition.base_properties["health_max"], 42)
        self.assertEqual(definition.base_properties["weapon_damage"], 13)
        self.assertEqual(definition.base_properties["target_priority"], -1)
        self.assertEqual(definition.attributes, {"brawn": 2})
        self.assertEqual(definition.randomization["attributes"][0]["mode"], "favor_high")

    def test_apply_mob_definition_manifest_accepts_structured_traits(self):
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: volatile-sentry
  name: a volatile sentry
spec:
  type: construct
  health_max: 10
  attack_power: 6
  traits:
    - key: colossal
      modifiers:
        health_max_multiplier: 2
    - key: enraged
      modifiers:
        attack_power_multiplier: 1.5
    - key: exploder
      visibility: hidden_until_death
      params:
        delay_rounds:
          min: 1
          max: 2
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="volatile-sentry")
        self.assertEqual(
            [trait["key"] for trait in definition.traits],
            ["colossal", "enraged", "exploder"],
        )
        self.assertNotIn("traits", definition.base_properties)

        mob = definition.spawn(self.room, self.spawn_world)

        self.assertEqual(mob.health_max, 20)
        self.assertEqual(mob.health, 20)
        self.assertEqual(mob.attack_power, 9)
        self.assertEqual(
            [trait["key"] for trait in mob.trait_instances],
            ["colossal", "enraged", "exploder"],
        )
        self.assertEqual(
            {trait["key"]: trait["source"] for trait in mob.trait_instances},
            {
                "colossal": "mob_definition",
                "enraged": "mob_definition",
                "exploder": "mob_definition",
            },
        )

    def test_apply_mob_definition_manifest_accepts_loot_source_pools(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="rusty-sword",
            name="a rusty sword",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="chipped-axe",
            name="a chipped axe",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="bone-charm",
            name="a bone charm",
        )
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: scavenger
  name: a scavenger
spec:
  type: humanoid
  health_max: 10
  loot:
    entries:
      - slug: weapon-drop
        probability: 100
        source_pool:
          - ref: itemdefinition.rusty-sword
            weight: 5
          - ref: itemdefinition.chipped-axe
            weight: 1
      - slug: charm-drop
        source: itemdefinition.bone-charm
        quantity:
          min: 1
          max: 1
        conditions:
          always: true
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="scavenger")
        self.assertEqual(
            definition.loot,
            {
                "entries": [
                    {
                        "slug": "weapon-drop",
                        "probability": 100,
                        "quantity": 1,
                        "source_pool": [
                            {"ref": "itemdefinition.rusty-sword", "weight": 5},
                            {"ref": "itemdefinition.chipped-axe", "weight": 1},
                        ],
                    },
                    {
                        "slug": "charm-drop",
                        "probability": 100,
                        "quantity": {"min": 1, "max": 1},
                        "conditions": {"always": True},
                        "source": "itemdefinition.bone-charm",
                    },
                ],
            },
        )
        self.assertEqual(resp.data["mob_definition"]["manifest"]["spec"]["loot"], definition.loot)

    def test_apply_mob_definition_manifest_normalizes_aggressive_alias(self):
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: guard
  name: a guard
spec:
  type: humanoid
  aggression: aggressive
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="guard")
        self.assertEqual(
            definition.base_properties["aggression"],
            adv_consts.MOB_AGGRESSION_ALL,
        )
        detail_resp = self.client.get(
            reverse(
                "builder-mob-definition-detail",
                args=[self.world.pk, definition.pk],
            )
        )
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)
        self.assertEqual(
            detail_resp.data["manifest"]["spec"]["aggression"],
            adv_consts.MOB_AGGRESSION_ALL,
        )

    def test_apply_mob_definition_manifest_rejects_unknown_aggression(self):
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: guard
  name: a guard
spec:
  type: humanoid
  aggression: berserk
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("aggression", str(resp.data).lower())

    def test_apply_mob_definition_manifest_can_create_ability_trainer(self):
        AbilityDefinition.objects.create(
            world=self.world,
            slug="power-strike",
            name="Power Strike",
            command_verbs=["strike"],
            target={"type": "hostile", "default": "current_target", "allow_out_of_combat": False},
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cooldown={"rounds": 0},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: arms-trainer
  name: an arms trainer
spec:
  type: humanoid
  keywords: trainer arms
  trainer:
    availability: alive_and_present
    abilities:
      - power-strike
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="arms-trainer")
        self.assertEqual(
            definition.trainer,
            {
                "abilities": ["power-strike"],
                "availability": "alive_and_present",
            },
        )
        self.assertEqual(
            resp.data["mob_definition"]["trainer"],
            {
                "abilities": ["power-strike"],
                "availability": "alive_and_present",
            },
        )

    def test_world_export_includes_mob_definition_documents(self):
        AbilityDefinition.objects.create(
            world=self.world,
            slug="power-strike",
            name="Power Strike",
            command_verbs=["strike"],
            target={"type": "hostile", "default": "current_target", "allow_out_of_combat": False},
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cooldown={"rounds": 0},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        MobDefinition.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"attack_power": 7},
            trainer={
                "abilities": ["power-strike"],
                "availability": "present",
            },
            randomization={
                "attributes": [
                    {"key": "brawn", "min": 10, "max": 20, "mode": "uniform"},
                ],
            },
        )

        resp = self.client.get(self.export_ep)
        self.assertEqual(resp.status_code, 200, resp.data)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc]
        kinds = [doc["kind"] for doc in docs]
        self.assertIn("mobdefinition", kinds)
        self.assertEqual(resp.data["summary"]["mob_definitions"], 1)

        mob_doc = next(doc for doc in docs if doc["kind"] == "mobdefinition")
        self.assertEqual(mob_doc["metadata"]["slug"], "bandit")
        self.assertEqual(mob_doc["spec"]["attack_power"], 7)
        self.assertEqual(mob_doc["spec"]["trainer"]["abilities"], ["power-strike"])


class TestMobDefinitionBuilderEndpoints(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.list_ep = reverse("builder-mob-definition-list", args=[self.world.pk])
        self.suggestion_ep = reverse("builder-mob-definition-suggestion", args=[self.world.pk])
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])

    def test_list_mob_definitions_for_builder_ui(self):
        MobDefinition.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            randomization={
                "attributes": [
                    {"key": "brawn", "min": 1, "max": 3, "mode": "uniform"},
                ],
            },
        )
        MobDefinition.objects.create(
            world=self.world,
            slug="boar",
            name="a boar",
            mob_type=adv_consts.MOB_TYPE_BEAST,
        )

        resp = self.client.get(self.list_ep, {"sort_by": "slug"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 2)
        self.assertEqual(
            [entry["slug"] for entry in resp.data["results"]],
            ["bandit", "boar"],
        )
        self.assertTrue(resp.data["results"][0]["randomized"])
        self.assertEqual(resp.data["results"][0]["type"], adv_consts.MOB_TYPE_HUMANOID)

    def test_list_filters_by_type_and_core_faction(self):
        human = Faction.objects.create(
            world=self.world,
            code="human",
            name="Human",
            type=FACTION_TYPE_CORE,
        )
        orc = Faction.objects.create(
            world=self.world,
            code="orc",
            name="Orc",
            type=FACTION_TYPE_CORE,
        )
        town_watch = Faction.objects.create(
            world=self.world,
            code="town-watch",
            name="Town Watch",
            type=FACTION_TYPE_REPUTATION,
        )
        human_bandit = MobDefinition.objects.create(
            world=self.world,
            slug="human-bandit",
            name="a human bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
        )
        human_boar = MobDefinition.objects.create(
            world=self.world,
            slug="human-boar",
            name="a human boar",
            mob_type=adv_consts.MOB_TYPE_BEAST,
        )
        orc_bandit = MobDefinition.objects.create(
            world=self.world,
            slug="orc-bandit",
            name="an orc bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
        )
        human_bandit.faction_assignments.create(faction=human)
        human_bandit.faction_assignments.create(faction=town_watch, value=50)
        human_boar.faction_assignments.create(faction=human)
        orc_bandit.faction_assignments.create(faction=orc)

        resp = self.client.get(
            self.list_ep,
            {
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "faction": human.code,
                "sort_by": "slug",
            },
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["slug"], human_bandit.slug)

        reputation_resp = self.client.get(
            self.list_ep,
            {"faction": town_watch.code},
        )
        self.assertEqual(reputation_resp.status_code, 200, reputation_resp.data)
        self.assertEqual(reputation_resp.data["count"], 0)

    def test_retrieve_mob_definition_includes_yaml(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            attributes={"brawn": 2},
        )

        resp = self.client.get(
            reverse("builder-mob-definition-detail", args=[self.world.pk, definition.pk])
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["slug"], "bandit")
        self.assertEqual(resp.data["aggression"], adv_consts.MOB_AGGRESSION_PASSIVE)
        self.assertEqual(resp.data["attributes"], {"brawn": 2})
        self.assertIn("kind: mobdefinition", resp.data["yaml"])
        self.assertIn("aggression: passive", resp.data["yaml"])
        self.assertEqual(resp.data["manifest"]["kind"], "mobdefinition")

    def test_suggest_mob_definition_returns_applyable_yaml(self):
        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a cave wolf",
                "slug": "cave-wolf",
                "type": adv_consts.MOB_TYPE_BEAST,
                "level": 4,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(MobDefinition.objects.filter(slug="cave-wolf").exists())
        self.assertEqual(resp.data["manifest"]["kind"], "mobdefinition")
        self.assertEqual(resp.data["manifest"]["metadata"]["slug"], "cave-wolf")
        self.assertEqual(resp.data["manifest"]["spec"]["level"], 4)
        self.assertEqual(resp.data["manifest"]["spec"]["type"], adv_consts.MOB_TYPE_BEAST)
        self.assertEqual(
            resp.data["manifest"]["spec"]["aggression"],
            adv_consts.MOB_AGGRESSION_NORMAL,
        )
        self.assertEqual(resp.data["manifest"]["spec"]["attributes"], {})
        self.assertGreater(resp.data["suggested_stats"]["health_max"], 0)
        self.assertGreater(resp.data["suggested_stats"]["attack_power"], 0)
        self.assertGreater(resp.data["suggested_stats"]["weapon_damage"], 0)
        self.assertIn("kind: mobdefinition", resp.data["yaml"])
        self.assertIn("aggression: normal", resp.data["yaml"])

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 201, apply_resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="cave-wolf")
        self.assertEqual(definition.name, "a cave wolf")
        self.assertEqual(
            definition.base_properties["aggression"],
            adv_consts.MOB_AGGRESSION_NORMAL,
        )
        self.assertEqual(definition.base_properties["level"], 4)
        self.assertEqual(
            definition.base_properties["health_max"],
            resp.data["suggested_stats"]["health_max"],
        )

    def test_suggest_mob_definition_uses_standard_rating_percent_defaults(self):
        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a guard",
                "slug": "guard",
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "level": 4,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        stats = resp.data["suggested_stats"]
        for rating_key in ("crit", "resilience", "armor", "dodge"):
            self.assertEqual(resp.data["manifest"]["spec"][rating_key], stats[rating_key])
            self.assertGreater(stats[rating_key], 0)

        preview = resp.data["combat_preview"]
        self.assertAlmostEqual(preview["same_level_armor_mitigation"], 0.08, delta=0.01)
        self.assertAlmostEqual(preview["same_level_dodge_chance"], 0.07, delta=0.01)
        self.assertAlmostEqual(preview["same_level_crit_chance"], 0.05, delta=0.01)
        self.assertAlmostEqual(preview["same_level_resilience_mitigation"], 0.03, delta=0.01)

    def test_suggest_mob_definition_includes_selected_core_faction(self):
        faction = Faction.objects.create(
            world=self.world,
            code="town_watch",
            name="Town Watch",
            type=FACTION_TYPE_CORE,
        )

        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a guard",
                "slug": "guard",
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "level": 4,
                "faction": faction.code,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            resp.data["manifest"]["spec"]["factions"],
            {"core": faction.code},
        )

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 201, apply_resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="guard")
        self.assertTrue(
            definition.faction_assignments.filter(faction=faction).exists()
        )

    def test_suggest_mob_definition_rejects_non_core_faction(self):
        faction = Faction.objects.create(
            world=self.world,
            code="town_watch",
            name="Town Watch",
            type=FACTION_TYPE_REPUTATION,
        )

        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a guard",
                "slug": "guard",
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "level": 4,
                "faction": faction.code,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("faction", resp.data)

    def test_suggest_mob_definition_supports_hyphenated_faction_code(self):
        faction = Faction.objects.create(
            world=self.world,
            code="town-watch",
            name="Town Watch",
            type=FACTION_TYPE_CORE,
        )

        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a guard",
                "slug": "guard",
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "level": 4,
                "faction": faction.code,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            resp.data["manifest"]["spec"]["factions"],
            {"core": f"faction.{faction.pk}"},
        )

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 201, apply_resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="guard")
        self.assertTrue(
            definition.faction_assignments.filter(faction=faction).exists()
        )

    def test_suggest_mob_definition_gives_beasts_more_armor_than_humanoids(self):
        humanoid_resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a guard",
                "slug": "guard",
                "type": adv_consts.MOB_TYPE_HUMANOID,
                "level": 4,
            },
            format="json",
        )
        beast_resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a wolf",
                "slug": "wolf",
                "type": adv_consts.MOB_TYPE_BEAST,
                "level": 4,
            },
            format="json",
        )

        self.assertEqual(humanoid_resp.status_code, 200, humanoid_resp.data)
        self.assertEqual(beast_resp.status_code, 200, beast_resp.data)
        self.assertGreater(
            beast_resp.data["combat_preview"]["same_level_armor_mitigation"],
            humanoid_resp.data["combat_preview"]["same_level_armor_mitigation"],
        )

    def test_suggest_mob_definition_converts_rating_percentages(self):
        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a cave wolf",
                "slug": "cave-wolf",
                "type": adv_consts.MOB_TYPE_BEAST,
                "level": 4,
                "crit_percent": 10,
                "resilience_percent": 20,
                "armor_percent": 25,
                "dodge_percent": 15,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        stats = resp.data["suggested_stats"]
        spec = resp.data["manifest"]["spec"]
        for rating_key in ("crit", "resilience", "armor", "dodge"):
            self.assertEqual(spec[rating_key], stats[rating_key])
            self.assertGreater(stats[rating_key], 0)

        preview = resp.data["combat_preview"]
        self.assertAlmostEqual(preview["same_level_crit_chance"], 0.10, delta=0.01)
        self.assertAlmostEqual(preview["same_level_resilience_mitigation"], 0.20, delta=0.01)
        self.assertAlmostEqual(preview["same_level_armor_mitigation"], 0.25, delta=0.01)
        self.assertAlmostEqual(preview["same_level_dodge_chance"], 0.15, delta=0.01)

    def test_suggest_mob_definition_validates_level(self):
        resp = self.client.post(
            self.suggestion_ep,
            {
                "name": "a cave wolf",
                "slug": "cave-wolf",
                "type": adv_consts.MOB_TYPE_BEAST,
                "level": 999,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("level", resp.data)
