import yaml

from rest_framework.reverse import reverse

from builders.models import AbilityDefinition
from tests.base import WorldTestCase


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestAbilityManifests(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )
        self.export_ep = reverse(
            "builder-world-export",
            args=[self.world.pk],
        )

    def test_apply_ability_manifest_can_create_ability(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: power-strike
  name: Power Strike
spec:
  command:
    verbs: [strike]
  action_type: primary
  target:
    type: hostile
    default: current_target
  cooldown:
    rounds: 2
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.5
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["kind"], "ability")
        self.assertEqual(resp.data["operation"], "created")

        ability = AbilityDefinition.objects.get(world=self.world, slug="power-strike")
        self.assertEqual(ability.name, "Power Strike")
        self.assertEqual(ability.command_verbs, ["strike"])
        self.assertEqual(ability.cooldown["rounds"], 2)
        self.assertEqual(ability.components[0]["overrides"]["multiplier"], 1.5)

    def test_apply_abilities_manifest_can_create_bundle(self):
        manifest = f"""
kind: abilities
metadata:
  world: world.{self.world.id}
spec:
  abilities:
    - slug: mend
      name: Mend
      command:
        verbs: [mend]
      target:
        type: self
        default: self
      components:
        - type: healing
          profile: basic_heal
    - slug: stun-bash
      name: Stun Bash
      command:
        verbs: [bash]
      components:
        - type: effect
          effect: stun
          duration:
            rounds: 1
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "abilities")
        self.assertEqual(len(resp.data["abilities"]), 2)
        self.assertTrue(AbilityDefinition.objects.filter(world=self.world, slug="mend").exists())
        self.assertTrue(AbilityDefinition.objects.filter(world=self.world, slug="stun-bash").exists())

    def test_apply_ability_manifest_accepts_condition_requirements(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: shield-slam
  name: Shield Slam
spec:
  command:
    verbs: [slam]
  target:
    type: hostile
    default: current_target
  requirements:
    eq:
      - actor.equipment.offhand.equipment_type
      - shield
  components:
    - type: damage
      profile: basic_physical
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201)
        ability = AbilityDefinition.objects.get(world=self.world, slug="shield-slam")
        self.assertEqual(
            ability.requirements,
            {"eq": ["actor.equipment.offhand.equipment_type", "shield"]},
        )

    def test_world_manifest_accepts_ability_progression(self):
        manifest = f"""
kind: world
metadata:
  world: world.{self.world.id}
spec:
  ability_progression:
    max_known: uncapped
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.ability_progression["max_known"], "uncapped")

    def test_world_export_includes_ability_documents(self):
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
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {}, "text": {"label": "Power Strike"}}],
        )

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        ability_docs = [doc for doc in docs if doc["kind"] == "ability"]
        self.assertEqual(len(ability_docs), 1)
        self.assertEqual(ability_docs[0]["metadata"]["slug"], "power-strike")
        self.assertEqual(resp.data["summary"]["abilities"], 1)
