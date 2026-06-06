import yaml

from rest_framework.reverse import reverse

from builders.models import AbilityDefinition, WorldBuilder
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
        self.list_ep = reverse(
            "builder-world-ability-list",
            args=[self.world.pk],
        )

    def _create_ability(self, **overrides):
        fields = {
            "world": self.world,
            "slug": "power-strike",
            "name": "Power Strike",
            "command_verbs": ["strike"],
            "action_type": "primary",
            "target": {"type": "hostile", "default": "current_target"},
            "availability": {"classes": [], "min_level": 1},
            "requirements": {},
            "cost": {},
            "cast_time": {"rounds": 1},
            "cooldown": {"rounds": 2},
            "components": [
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {},
                    "text": {"label": "Power Strike"},
                },
            ],
            "is_active": True,
        }
        fields.update(overrides)
        return AbilityDefinition.objects.create(**fields)

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
  cast_time:
    rounds: 1
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
        self.assertEqual(ability.cast_time["rounds"], 1)
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

    def test_apply_ability_manifest_accepts_percent_base_cost(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: arcane-bolt
  name: Arcane Bolt
spec:
  command:
    verbs: [bolt]
  cost:
    resource: energy
    amount: 5
    calc: percent_base
  components:
    - type: damage
      profile: basic_ability
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="arcane-bolt")
        self.assertEqual(
            ability.cost,
            {"resource": "energy", "amount": 5.0, "calc": "percent_base"},
        )

    def test_apply_ability_manifest_accepts_state_components_and_scaling(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: quick-jab
  name: Quick Jab
spec:
  command:
    verbs: [jab]
  target:
    type: hostile
    default: current_target
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1
      scaling:
        from: state.character.combo_points
        multiplier_per_point: 0.5
        max_points: 5
    - type: state
      scope: character
      key: combo_points
      op: increment
      amount: 1
      max: 5
      apply: on_hit
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="quick-jab")
        self.assertEqual(
            ability.components[0]["scaling"],
            {
                "from": "state.character.combo_points",
                "multiplier_per_point": 0.5,
                "max_points": 5.0,
            },
        )
        self.assertEqual(
            ability.components[1],
            {
                "type": "state",
                "scope": "character",
                "key": "combo_points",
                "op": "increment",
                "apply": "on_hit",
                "text": {"label": "Quick Jab"},
                "amount": 1.0,
                "max": 5.0,
            },
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
        self._create_ability(
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            cooldown={"rounds": 0},
        )

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        ability_docs = [doc for doc in docs if doc["kind"] == "ability"]
        self.assertEqual(len(ability_docs), 1)
        self.assertEqual(ability_docs[0]["metadata"]["slug"], "power-strike")
        self.assertEqual(ability_docs[0]["spec"]["cast_time"], {"rounds": 1})
        self.assertEqual(resp.data["summary"]["abilities"], 1)

    def test_world_ability_list_includes_yaml_manifest(self):
        ability = self._create_ability()

        resp = self.client.get(self.list_ep)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        ability_data = resp.data["results"][0]
        self.assertEqual(ability_data["id"], ability.id)
        self.assertEqual(ability_data["key"], f"ability.{ability.id}")
        self.assertEqual(ability_data["slug"], "power-strike")
        self.assertEqual(ability_data["command_verbs"], ["strike"])
        self.assertEqual(ability_data["action_type"], "primary")
        self.assertEqual(ability_data["target"]["type"], "hostile")
        self.assertTrue(ability_data["is_active"])
        self.assertIn("kind: ability", ability_data["yaml"])
        self.assertIn("slug: power-strike", ability_data["yaml"])
        self.assertIn("operation: delete", ability_data["delete_yaml"])

        detail_ep = reverse(
            "builder-world-ability-detail",
            args=[self.world.pk, ability.pk],
        )
        detail_resp = self.client.get(detail_ep)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.data["id"], ability.id)
        self.assertEqual(detail_resp.data["manifest"]["spec"]["cast_time"], {"rounds": 1})

    def test_world_ability_list_supports_filters_search_and_sort(self):
        power_strike = self._create_ability()
        mend = self._create_ability(
            slug="mend",
            name="Mend",
            command_verbs=["mend"],
            action_type="utility",
            target={"type": "self", "default": "self"},
            is_active=False,
            components=[
                {
                    "type": "healing",
                    "profile": "basic_heal",
                    "overrides": {},
                    "text": {"label": "Mend"},
                },
            ],
        )

        resp = self.client.get(self.list_ep, {"action_type": "utility"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([ability["id"] for ability in resp.data["results"]], [mend.id])

        resp = self.client.get(self.list_ep, {"is_active": "false"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([ability["id"] for ability in resp.data["results"]], [mend.id])

        resp = self.client.get(self.list_ep, {"query": "power"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([ability["id"] for ability in resp.data["results"]], [power_strike.id])

        resp = self.client.get(self.list_ep, {"sort_by": "-slug"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [ability["slug"] for ability in resp.data["results"]],
            ["power-strike", "mend"],
        )

    def test_rank_2_builder_cannot_view_world_ability_list(self):
        builder_user = self.create_user("ability-list-builder@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        resp = self.client.get(self.list_ep)
        self.assertEqual(resp.status_code, 403)
