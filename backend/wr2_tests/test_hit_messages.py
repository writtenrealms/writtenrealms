import yaml

from rest_framework.reverse import reverse

from builders.models import ItemDefinition, MobDefinition
from config import constants as adv_consts
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from tests.base import WorldTestCase
from wr2_tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestCombatHitMessages(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
        )
        self.player.name = "Thibaud"
        self.player.health = self.stats["health_max"]
        self.player.energy = self.stats["energy_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(
            update_fields=["name", "health", "energy", "stamina", "in_game"]
        )
        self.watcher = self.create_player("Watcher", room=self.room)
        self.watcher.in_game = True
        self.watcher.save(update_fields=["in_game"])

        self.world.config.combat_resolution_interval = -1
        self.world.config.combat_system = normalize_combat_system({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 1,
                    "use_weapon_damage": False,
                    "can_dodge": False,
                    "can_crit": False,
                    "mitigation": {
                        "armor": False,
                        "resilience": False,
                    },
                    "minimum": 0,
                },
            },
        })
        self.world.config.save(
            update_fields=["combat_resolution_interval", "combat_system"]
        )

    def _equip_weapon(self, definition):
        weapon = definition.spawn(self.player.equipment, self.spawn_world)
        self.player.equipment.weapon = weapon
        self.player.equipment.save(update_fields=["weapon"])
        return weapon

    def _spawn_dog(self, *, base_properties):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="dog",
            name="a dog",
            keywords="dog",
            base_properties={
                "health_max": max(100, self.stats["attack_power"] * 10),
                "attack_power": 4,
                **base_properties,
            },
        )
        return definition.spawn(self.room, self.spawn_world)

    @staticmethod
    def _attack_messages(messages, player_key):
        return [
            entry["message"]
            for entry in messages
            if entry["player_key"] == player_key
            and entry["message"].get("type") == "notification.combat.attack"
        ]

    @staticmethod
    def _attack_by_actor(messages, actor_key):
        return next(
            message
            for message in messages
            if message["data"]["actor"]["key"] == actor_key
        )

    def test_definition_hit_messages_are_used_for_attacker_target_and_observer(self):
        weapon_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="curved-sword",
            name="a curved sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "slash",
                "hit_msg_third": "slashes",
            },
        )
        weapon = self._equip_weapon(weapon_definition)
        dog = self._spawn_dog(base_properties={
            "hit_msg_first": "bite",
            "hit_msg_third": "bites",
        })

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill dog")

        player_attacks = self._attack_messages(messages, self.player.key)
        watcher_attacks = self._attack_messages(messages, self.watcher.key)
        player_strike = self._attack_by_actor(player_attacks, self.player.key)
        dog_strike = self._attack_by_actor(player_attacks, dog.key)
        observed_player_strike = self._attack_by_actor(
            watcher_attacks,
            self.player.key,
        )
        observed_dog_strike = self._attack_by_actor(watcher_attacks, dog.key)

        self.assertEqual(weapon.hit_msg_first, "slash")
        self.assertEqual(weapon.hit_msg_third, "slashes")
        self.assertEqual(dog.hit_msg_first, "bite")
        self.assertEqual(dog.hit_msg_third, "bites")
        self.assertEqual(
            player_strike["text"],
            f"You slash a dog for {player_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            dog_strike["text"],
            f"A dog bites you for {dog_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            observed_player_strike["text"],
            (
                "Thibaud slashes a dog for "
                f"{observed_player_strike['data']['damage_taken']} damage."
            ),
        )
        self.assertEqual(
            observed_dog_strike["text"],
            (
                "A dog bites Thibaud for "
                f"{observed_dog_strike['data']['damage_taken']} damage."
            ),
        )

    def test_multiword_hit_messages_are_preserved_in_combat_text(self):
        weapon_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="braided-whip",
            name="a braided whip",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "lash at",
                "hit_msg_third": "lashes at",
            },
        )
        self._equip_weapon(weapon_definition)
        self._spawn_dog(base_properties={})

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill dog")

        player_strike = self._attack_by_actor(
            self._attack_messages(messages, self.player.key),
            self.player.key,
        )
        observed_player_strike = self._attack_by_actor(
            self._attack_messages(messages, self.watcher.key),
            self.player.key,
        )
        self.assertEqual(
            player_strike["text"],
            f"You lash at a dog for {player_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            observed_player_strike["text"],
            (
                "Thibaud lashes at a dog for "
                f"{observed_player_strike['data']['damage_taken']} damage."
            ),
        )

    def test_mainhand_and_offhand_strikes_use_their_selected_weapon_messages(self):
        mainhand_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="mainhand-sword",
            name="a mainhand sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "slash",
                "hit_msg_third": "slashes",
            },
        )
        offhand_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="offhand-hammer",
            name="an offhand hammer",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "pound",
                "hit_msg_third": "pounds",
            },
        )
        mainhand = mainhand_definition.spawn(self.player.equipment, self.spawn_world)
        offhand = offhand_definition.spawn(self.player.equipment, self.spawn_world)
        self.player.equipment.weapon = mainhand
        self.player.equipment.offhand = offhand
        self.player.equipment.save(update_fields=["weapon", "offhand"])
        self.world.config.equipment_system = {
            "offhand_weapons": {
                "default_allowed": True,
                "allowed_grips": [adv_consts.WEAPON_GRIP_ONE_HAND],
            },
        }
        self.world.config.combat_system = normalize_combat_system({
            **self.world.config.combat_system,
            "attack_routine": {
                "dual_wield": {
                    "enabled": True,
                    "grants_offhand_strike": True,
                    "offhand_damage_multiplier": 0.5,
                },
            },
        })
        self.world.config.save(
            update_fields=["equipment_system", "combat_system"],
        )
        self._spawn_dog(base_properties={})

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill dog")

        player_strikes = [
            message
            for message in self._attack_messages(messages, self.player.key)
            if message["data"]["actor"]["key"] == self.player.key
        ]
        observed_strikes = [
            message
            for message in self._attack_messages(messages, self.watcher.key)
            if message["data"]["actor"]["key"] == self.player.key
        ]
        mainhand_strike = next(
            message
            for message in player_strikes
            if message["data"]["attack"] == "attack"
        )
        offhand_strike = next(
            message
            for message in player_strikes
            if message["data"]["attack"] == "dual_wield_offhand"
        )
        observed_mainhand_strike = next(
            message
            for message in observed_strikes
            if message["data"]["attack"] == "attack"
        )
        observed_offhand_strike = next(
            message
            for message in observed_strikes
            if message["data"]["attack"] == "dual_wield_offhand"
        )

        self.assertEqual(
            mainhand_strike["text"],
            f"You slash a dog for {mainhand_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            offhand_strike["text"],
            f"You pound a dog for {offhand_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            observed_mainhand_strike["text"],
            (
                "Thibaud slashes a dog for "
                f"{observed_mainhand_strike['data']['damage_taken']} damage."
            ),
        )
        self.assertEqual(
            observed_offhand_strike["text"],
            (
                "Thibaud pounds a dog for "
                f"{observed_offhand_strike['data']['damage_taken']} damage."
            ),
        )

    def test_missing_and_blank_hit_messages_fall_back_to_hit_and_hits(self):
        weapon_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="blank-blade",
            name="a blank blade",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "",
                "hit_msg_third": "   ",
            },
        )
        weapon = self._equip_weapon(weapon_definition)
        dog = self._spawn_dog(base_properties={})

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill dog")

        player_attacks = self._attack_messages(messages, self.player.key)
        watcher_attacks = self._attack_messages(messages, self.watcher.key)
        player_strike = self._attack_by_actor(player_attacks, self.player.key)
        dog_strike = self._attack_by_actor(player_attacks, dog.key)
        observed_player_strike = self._attack_by_actor(
            watcher_attacks,
            self.player.key,
        )

        self.assertEqual(weapon.hit_msg_first, "")
        self.assertEqual(weapon.hit_msg_third, "   ")
        self.assertEqual(dog.hit_msg_first, "hit")
        self.assertEqual(dog.hit_msg_third, "hits")
        self.assertEqual(
            player_strike["text"],
            f"You hit a dog for {player_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            dog_strike["text"],
            f"A dog hits you for {dog_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            observed_player_strike["text"],
            (
                "Thibaud hits a dog for "
                f"{observed_player_strike['data']['damage_taken']} damage."
            ),
        )

    def test_inventory_weapon_does_not_change_unarmed_hit_messages(self):
        inventory_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="inventory-spear",
            name="an inventory spear",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "hit_msg_first": "stab",
                "hit_msg_third": "stabs",
            },
        )
        inventory_definition.spawn(self.player, self.spawn_world)
        self._spawn_dog(base_properties={})

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill dog")

        player_strike = self._attack_by_actor(
            self._attack_messages(messages, self.player.key),
            self.player.key,
        )
        observed_player_strike = self._attack_by_actor(
            self._attack_messages(messages, self.watcher.key),
            self.player.key,
        )
        self.assertEqual(
            player_strike["text"],
            f"You hit a dog for {player_strike['data']['damage_taken']} damage.",
        )
        self.assertEqual(
            observed_player_strike["text"],
            (
                "Thibaud hits a dog for "
                f"{observed_player_strike['data']['damage_taken']} damage."
            ),
        )


class TestHitMessageDefinitionManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])

    def test_item_and_mob_hit_messages_apply_spawn_and_export(self):
        item_manifest = f"""
kind: itemdefinition
metadata:
  world: world.{self.world.id}
  slug: curved-sword
  name: a curved sword
spec:
  type: equippable
  equipment_type: weapon_1h
  hit_msg_first: slash
  hit_msg_third: slashes
"""
        item_resp = self.client.post(
            self.apply_ep,
            {"manifest": item_manifest},
            format="json",
        )
        self.assertEqual(item_resp.status_code, 201, item_resp.data)

        mob_manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: dog
  name: a dog
spec:
  type: beast
  keywords: dog
  health_max: 20
  hit_msg_first: bite
  hit_msg_third: bites
"""
        mob_resp = self.client.post(
            self.apply_ep,
            {"manifest": mob_manifest},
            format="json",
        )
        self.assertEqual(mob_resp.status_code, 201, mob_resp.data)

        item_definition = ItemDefinition.objects.get(
            world=self.world,
            slug="curved-sword",
        )
        mob_definition = MobDefinition.objects.get(world=self.world, slug="dog")
        item = item_definition.spawn(self.player, self.spawn_world)
        mob = mob_definition.spawn(self.room, self.spawn_world)

        self.assertEqual(item_definition.base_properties["hit_msg_first"], "slash")
        self.assertEqual(item_definition.base_properties["hit_msg_third"], "slashes")
        self.assertEqual(mob_definition.base_properties["hit_msg_first"], "bite")
        self.assertEqual(mob_definition.base_properties["hit_msg_third"], "bites")
        self.assertEqual(item.hit_msg_first, "slash")
        self.assertEqual(item.hit_msg_third, "slashes")
        self.assertEqual(mob.hit_msg_first, "bite")
        self.assertEqual(mob.hit_msg_third, "bites")

        export_resp = self.client.get(self.export_ep)
        self.assertEqual(export_resp.status_code, 200, export_resp.data)
        documents = [
            document
            for document in yaml.safe_load_all(export_resp.data["yaml"])
            if document
        ]
        item_document = next(
            document
            for document in documents
            if document["kind"] == "itemdefinition"
            and document["metadata"]["slug"] == "curved-sword"
        )
        mob_document = next(
            document
            for document in documents
            if document["kind"] == "mobdefinition"
            and document["metadata"]["slug"] == "dog"
        )

        self.assertEqual(item_document["spec"]["hit_msg_first"], "slash")
        self.assertEqual(item_document["spec"]["hit_msg_third"], "slashes")
        self.assertEqual(mob_document["spec"]["hit_msg_first"], "bite")
        self.assertEqual(mob_document["spec"]["hit_msg_third"], "bites")

    def test_null_hit_messages_are_stored_as_blank_runtime_fallbacks(self):
        manifests = f"""
kind: itemdefinition
metadata:
  world: world.{self.world.id}
  slug: unmarked-blade
  name: an unmarked blade
spec:
  type: equippable
  equipment_type: weapon_1h
  hit_msg_first:
  hit_msg_third: null
---
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: silent-dog
  name: a silent dog
spec:
  type: beast
  hit_msg_first:
  hit_msg_third: null
"""
        response = self.client.post(
            self.apply_ep,
            {"manifest": manifests},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        item_definition = ItemDefinition.objects.get(
            world=self.world,
            slug="unmarked-blade",
        )
        mob_definition = MobDefinition.objects.get(
            world=self.world,
            slug="silent-dog",
        )
        item = item_definition.spawn(self.player, self.spawn_world)
        mob = mob_definition.spawn(self.room, self.spawn_world)

        self.assertEqual(item_definition.base_properties["hit_msg_first"], "")
        self.assertEqual(item_definition.base_properties["hit_msg_third"], "")
        self.assertEqual(mob_definition.base_properties["hit_msg_first"], "")
        self.assertEqual(mob_definition.base_properties["hit_msg_third"], "")
        self.assertEqual(item.hit_msg_first, "")
        self.assertEqual(item.hit_msg_third, "")
        self.assertEqual(mob.hit_msg_first, "")
        self.assertEqual(mob.hit_msg_third, "")
