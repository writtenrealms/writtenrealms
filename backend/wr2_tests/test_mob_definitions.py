import random
import yaml

from django.contrib.contenttypes.models import ContentType
from rest_framework.reverse import reverse

from builders.models import AbilityDefinition, Loader, MobDefinition, Rule
from config import constants as adv_consts
from spawns.loading import LoaderRun
from spawns.models import Mob
from spawns.serializers import LoadTemplateSerializer
from tests.base import WorldTestCase
from worlds.models import Room
from wr2_tests.utils import apply_basic_stat_system


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
                "health_max": 45,
                "attack_power": 6,
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
        self.assertIsNone(mob.template)
        self.assertEqual(mob.name, "a bandit")
        self.assertEqual(mob.type, adv_consts.MOB_TYPE_HUMANOID)
        self.assertEqual(mob.level, 4)
        self.assertEqual(mob.health_max, 45)
        self.assertEqual(mob.health, 45)
        self.assertEqual(mob.attack_power, 18)
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

    def test_loader_rule_can_spawn_mob_definition(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="wild-boar",
            name="a wild boar",
            mob_type=adv_consts.MOB_TYPE_BEAST,
            base_properties={"health_max": 18},
        )
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
        )
        rule = Rule.objects.create(
            loader=loader,
            template_type=ContentType.objects.get_for_model(MobDefinition),
            template_id=definition.id,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            num_copies=1,
        )

        output = LoaderRun(loader, self.spawn_world, check=False).execute(force=True)

        spawned = output[rule.id][0]
        self.assertIsInstance(spawned, Mob)
        self.assertEqual(spawned.definition, definition)
        self.assertIsNone(spawned.template)
        self.assertEqual(spawned.rule, rule)

    def test_load_template_serializer_resolves_mob_definition(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="shade",
            name="a shade",
        )

        serializer = LoadTemplateSerializer(
            data={
                "world_id": self.spawn_world.id,
                "actor_type": "player",
                "actor_id": self.player.id,
                "template_type": "mob",
                "template_id": "shade",
                "room": self.room.id,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["template"], definition)


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
        self.assertEqual(definition.attributes, {"brawn": 2})
        self.assertEqual(definition.randomization["attributes"][0]["mode"], "favor_high")

    def test_apply_mob_definition_manifest_can_create_ability_trainer(self):
        AbilityDefinition.objects.create(
            world=self.world,
            slug="power-strike",
            name="Power Strike",
            command_verbs=["strike"],
            action_type="primary",
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
            action_type="primary",
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
        self.assertEqual(resp.data["manifest"]["spec"]["attributes"], {})
        self.assertGreater(resp.data["suggested_stats"]["health_max"], 0)
        self.assertGreater(resp.data["suggested_stats"]["attack_power"], 0)
        self.assertIn("kind: mobdefinition", resp.data["yaml"])

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 201, apply_resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="cave-wolf")
        self.assertEqual(definition.name, "a cave wolf")
        self.assertEqual(definition.base_properties["level"], 4)
        self.assertEqual(
            definition.base_properties["health_max"],
            resp.data["suggested_stats"]["health_max"],
        )

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
