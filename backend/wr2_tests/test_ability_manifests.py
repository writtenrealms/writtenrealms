import yaml

from rest_framework.reverse import reverse

from builders.models import AbilityDefinition, MobDefinition, WorldBuilder
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig


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
            "consumes_primary_action_on_resolve": True,
            "consumes_primary_action_while_casting": True,
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

    def _create_instance_world(self):
        return World.objects.new_world(
            name="Trial Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
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
  cast_time:
    rounds: 1
  cooldown:
    rounds: 2
    trigger: on_hit
  help:
    text: 1 round cast, 2 round cooldown, inflicts 1.5x physical damage on the target.
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
        self.assertTrue(ability.consumes_primary_action_on_resolve)
        self.assertTrue(ability.consumes_primary_action_while_casting)
        self.assertEqual(ability.cast_time["rounds"], 1)
        self.assertEqual(ability.cooldown, {"rounds": 2, "trigger": "on_hit"})
        self.assertEqual(
            ability.help,
            {
                "text": "1 round cast, 2 round cooldown, inflicts 1.5x physical damage on the target.",
            },
        )
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

    def test_apply_ability_manifest_accepts_phase_specific_primary_action_consumption(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: bleeding-cut
  name: Bleeding Cut
spec:
  command:
    verbs: [bleed]
  consumes_primary_action_on_resolve: false
  consumes_primary_action_while_casting: true
  target:
    type: hostile
    default: current_target
  components:
    - type: effect
      effect: dot
      duration:
        rounds: 2
      tick:
        every_rounds: 1
        component:
          type: damage
          profile: basic_physical
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="bleeding-cut")
        self.assertFalse(ability.consumes_primary_action_on_resolve)
        self.assertTrue(ability.consumes_primary_action_while_casting)
        self.assertEqual(ability.components[0]["scope"], "character")
        self.assertFalse(
            resp.data["ability"]["manifest"]["spec"][
                "consumes_primary_action_on_resolve"
            ]
        )
        self.assertTrue(
            resp.data["ability"]["manifest"]["spec"][
                "consumes_primary_action_while_casting"
            ]
        )

    def test_apply_ability_manifest_rejects_removed_primary_action_field(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: bleeding-cut
  name: Bleeding Cut
spec:
  command:
    verbs: [bleed]
  consumes_primary_action: false
  components:
    - type: effect
      effect: dot
      duration:
        rounds: 2
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("consumes_primary_action", str(resp.data))

    def test_mob_definition_manifest_accepts_combat_ability_loadout(self):
        self._create_ability(
            slug="shadow-bolt",
            name="Shadow Bolt",
            command_verbs=["shadowbolt"],
        )
        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: cave-shaman
  name: a cave shaman
spec:
  type: humanoid
  health_max: 40
  ability_power: 8
  combat:
    attackable: true
    abilities:
      - ability: shadow-bolt
        weight: 3
        chance: 25
        when:
          lte:
            - actor.health_percent
            - 50
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        mob_definition = MobDefinition.objects.get(world=self.world, slug="cave-shaman")
        expected = [
            {
                "ability": "shadow-bolt",
                "weight": 3,
                "chance": 25,
                "when": {"lte": ["actor.health_percent", 50]},
            }
        ]
        self.assertEqual(mob_definition.combat_abilities, expected)
        self.assertEqual(
            resp.data["mob_definition"]["manifest"]["spec"]["combat"]["abilities"],
            expected,
        )

    def test_mob_definition_manifest_rejects_invalid_combat_ability_chance(self):
        self._create_ability(
            slug="shadow-bolt",
            name="Shadow Bolt",
            command_verbs=["shadowbolt"],
        )

        for chance in (-1, 101):
            with self.subTest(chance=chance):
                manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: cave-shaman
  name: a cave shaman
spec:
  type: humanoid
  combat:
    abilities:
      - ability: shadow-bolt
        chance: {chance}
"""
                resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

                self.assertEqual(resp.status_code, 400)
                self.assertIn("chance must be 0-100", str(resp.data))

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

    def test_apply_ability_manifest_accepts_current_or_adjacent_room_openers(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: charge
  name: Charge
spec:
  command:
    verbs: [charge]
  target:
    type: hostile
    default: current_target
    allow_out_of_combat: true
    range: current_or_adjacent_room
    move_actor: true
    opener_priority: true
  cooldown:
    rounds: 10
  components:
    - type: damage
      profile: basic_physical
      overrides:
        multiplier: 1.5
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="charge")
        self.assertEqual(
            ability.target,
            {
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": True,
                "range": "current_or_adjacent_room",
                "move_actor": True,
                "opener_priority": True,
            },
        )
        self.assertEqual(ability.cooldown, {"rounds": 10})
        self.assertEqual(ability.components[0]["overrides"]["multiplier"], 1.5)

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

    def test_apply_ability_manifest_accepts_resource_proc_effects(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: energized-strikes
  name: Energized Strikes
spec:
  command:
    verbs: [energize]
  target:
    type: self
    default: self
    allow_out_of_combat: false
  components:
    - type: effect
      effect: energized-strikes
      category: buff
      target: self
      duration:
        rounds: 10
      primitives:
        - type: proc
          phase: after_damage
          conditions:
            all:
              - eq: [event.actor, "{{effect.target}}"]
              - eq: [event.attack, attack]
              - eq: [event.damage_type, physical]
              - gte: [event.damage_taken, 1]
          actions:
            - type: resource_change
              resource: energy
              amount: 5
              calc: fixed
              target: effect.target
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="energized-strikes")
        self.assertEqual(
            ability.components[0],
            {
                "type": "effect",
                "effect": "energized-strikes",
                "scope": "encounter",
                "category": "buff",
                "target": "self",
                "duration": {"rounds": 10},
                "apply": "on_resolve",
                "text": {"label": "Energized Strikes"},
                "primitives": [
                    {
                        "type": "proc",
                        "phase": "after_damage",
                        "conditions": {
                            "all": [
                                {"eq": ["event.actor", "{effect.target}"]},
                                {"eq": ["event.attack", "attack"]},
                                {"eq": ["event.damage_type", "physical"]},
                                {"gte": ["event.damage_taken", 1]},
                            ]
                        },
                        "actions": [
                            {
                                "type": "resource_change",
                                "resource": "energy",
                                "amount": 5.0,
                                "calc": "fixed",
                                "target": "effect.target",
                            }
                        ],
                    }
                ],
            },
        )

    def test_apply_ability_manifest_accepts_attack_routine_modifier(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: battle-trance
  name: Battle Trance
spec:
  command:
    verbs: [trance]
  action_type: utility
  target:
    type: self
    default: self
  components:
    - type: effect
      effect: battle-trance
      category: buff
      target: self
      duration:
        rounds: 6
      stacking: refresh
      stack_key: battle-trance
      primitives:
        - type: combat_modifier
          phase: attack_routine
          attack_routine:
            extra_mainhand_strikes: 1
            strike:
              source: battle-trance
              target: room.secondary_hostile
              weapon_slot: weapon
              damage_multiplier: 1
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="battle-trance")
        self.assertEqual(
            ability.components[0]["primitives"][0],
            {
                "type": "combat_modifier",
                "phase": "attack_routine",
                "attack_routine": {
                    "extra_mainhand_strikes": 1,
                    "strike": {
                        "source": "battle-trance",
                        "target": "room.secondary_hostile",
                        "weapon_slot": "weapon",
                        "damage_multiplier": 1.0,
                    },
                },
            },
        )

    def test_apply_ability_manifest_accepts_damage_absorb_effects(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: ward
  name: Ward
spec:
  command:
    verbs: [ward]
  target:
    type: self
    default: self
    allow_out_of_combat: false
  components:
    - type: effect
      effect: ward
      category: buff
      target: self
      duration:
        rounds: 3
      primitives:
        - type: damage_absorb
          amount: 25
          calc: fixed
          damage_types: [physical, ability]
          scaling:
            - source: ability_power
              multiplier: 0.1
            - source: health_max
              multiplier: 0.3
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="ward")
        self.assertEqual(
            ability.components[0],
            {
                "type": "effect",
                "effect": "ward",
                "scope": "encounter",
                "category": "buff",
                "target": "self",
                "duration": {"rounds": 3},
                "apply": "on_resolve",
                "text": {"label": "Ward"},
                "primitives": [
                    {
                        "type": "damage_absorb",
                        "amount": 25.0,
                        "calc": "fixed",
                        "damage_types": ["physical", "ability"],
                        "scaling": [
                            {"source": "ability_power", "multiplier": 0.1},
                            {"source": "health_max", "multiplier": 0.3},
                        ],
                    }
                ],
            },
        )

    def test_apply_ability_manifest_accepts_combat_modifier_effects(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: shout
  name: Shout
spec:
  command:
    verbs: [shout]
  target:
    type: self
    default: self
    allow_out_of_combat: true
  cooldown:
    rounds: 12
  components:
    - type: effect
      effect: shout
      category: buff
      target: room.allies
      stack_key: shout-damage-output
      stacking: refresh
      duration:
        rounds: 4
      primitives:
        - type: combat_modifier
          phase: outgoing_damage
          multiplier: 1.2
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="shout")
        self.assertEqual(ability.cooldown, {"rounds": 12})
        self.assertEqual(
            ability.components[0],
            {
                "type": "effect",
                "effect": "shout",
                "scope": "character",
                "category": "buff",
                "target": "room.allies",
                "duration": {"rounds": 4},
                "apply": "on_resolve",
                "text": {"label": "Shout"},
                "stack_key": "shout-damage-output",
                "stacking": "refresh",
                "primitives": [
                    {
                        "type": "combat_modifier",
                        "phase": "outgoing_damage",
                        "multiplier": 1.2,
                    }
                ],
            },
        )

    def test_apply_ability_manifest_accepts_stat_modifier_effects(self):
        manifest = f"""
kind: ability
metadata:
  world: world.{self.world.id}
  slug: shield-wall
  name: Shield Wall
spec:
  command:
    verbs: [shieldwall]
  target:
    type: self
    default: self
    allow_out_of_combat: true
  cooldown:
    rounds: 12
  components:
    - type: effect
      effect: shield-wall
      category: buff
      target: self
      stack_key: shield-wall-armor
      stacking: refresh
      duration:
        rounds: 3
      primitives:
        - type: stat_modifier
          stat: armor
          op: add
          amount: 12
        - type: stat_modifier
          stat: armor
          op: multiply
          multiplier: 3
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        ability = AbilityDefinition.objects.get(world=self.world, slug="shield-wall")
        self.assertEqual(
            ability.components[0],
            {
                "type": "effect",
                "effect": "shield-wall",
                "scope": "character",
                "category": "buff",
                "target": "self",
                "duration": {"rounds": 3},
                "apply": "on_resolve",
                "text": {"label": "Shield Wall"},
                "stack_key": "shield-wall-armor",
                "stacking": "refresh",
                "primitives": [
                    {
                        "type": "stat_modifier",
                        "stat": "armor",
                        "op": "add",
                        "amount": 12.0,
                    },
                    {
                        "type": "stat_modifier",
                        "stat": "armor",
                        "op": "multiply",
                        "multiplier": 3.0,
                    },
                ],
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
    starting_abilities:
      - first-aid
      - ability: bash
        conditions:
          eq: [actor.archetype, hoplite]
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.world.config.refresh_from_db()
        self.assertEqual(
            self.world.config.ability_progression,
            {
                "max_known": "uncapped",
                "starting_abilities": [
                    {"ability": "first-aid"},
                    {
                        "ability": "bash",
                        "conditions": {
                            "eq": ["actor.archetype", "hoplite"],
                        },
                    },
                ],
            },
        )

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
        self.assertTrue(
            ability_docs[0]["spec"]["consumes_primary_action_on_resolve"]
        )
        self.assertTrue(
            ability_docs[0]["spec"]["consumes_primary_action_while_casting"]
        )
        self.assertNotIn("consumes_primary_action", ability_docs[0]["spec"])
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
        self.assertTrue(ability_data["consumes_primary_action_on_resolve"])
        self.assertTrue(ability_data["consumes_primary_action_while_casting"])
        self.assertNotIn("consumes_primary_action", ability_data)
        self.assertEqual(ability_data["target"]["type"], "hostile")
        self.assertEqual(ability_data["help"], {})
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
        self.assertTrue(
            detail_resp.data["manifest"]["spec"][
                "consumes_primary_action_on_resolve"
            ]
        )
        self.assertTrue(
            detail_resp.data["manifest"]["spec"][
                "consumes_primary_action_while_casting"
            ]
        )

    def test_instance_ability_list_reads_base_world_definitions(self):
        ability = self._create_ability()
        instance_world = self._create_instance_world()

        list_resp = self.client.get(
            reverse(
                "builder-world-ability-list",
                args=[instance_world.pk],
            )
        )
        detail_resp = self.client.get(
            reverse(
                "builder-world-ability-detail",
                args=[instance_world.pk, ability.pk],
            )
        )

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.data["count"], 1)
        self.assertEqual(list_resp.data["results"][0]["id"], ability.id)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.data["id"], ability.id)

    def test_instance_world_cannot_apply_ability_manifest(self):
        instance_world = self._create_instance_world()
        instance_apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[instance_world.pk],
        )
        manifest = f"""
kind: ability
metadata:
  world: world.{instance_world.id}
  slug: instance-strike
  name: Instance Strike
spec:
  command:
    verbs: [instancestrike]
  target:
    type: self
    default: self
  components: []
"""

        resp = self.client.post(instance_apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Abilities are inherited from the base world", str(resp.data))
        self.assertFalse(AbilityDefinition.objects.filter(world=instance_world).exists())

    def test_instance_world_cannot_apply_abilities_bundle_manifest(self):
        instance_world = self._create_instance_world()
        instance_apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[instance_world.pk],
        )
        manifest = f"""
kind: abilities
metadata:
  world: world.{instance_world.id}
spec:
  abilities:
    - slug: instance-mend
      name: Instance Mend
      command:
        verbs: [instancemend]
      target:
        type: self
        default: self
      components: []
"""

        resp = self.client.post(instance_apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Abilities are inherited from the base world", str(resp.data))
        self.assertFalse(AbilityDefinition.objects.filter(world=instance_world).exists())

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
