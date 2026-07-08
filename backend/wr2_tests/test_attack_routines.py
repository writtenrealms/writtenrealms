from copy import deepcopy

from builders.models import ItemDefinition
from config import constants as adv_consts
from core.attack_routines import resolve_attack_routine
from core.combat_formulas import normalize_combat_system, resolve_attack
from spawns.models import Item, Mob
from tests.base import WorldTestCase
from wr2_tests.utils import (
    BASIC_TEST_STAT_SYSTEM,
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestAttackRoutines(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = -1
        self.world.config.combat_system = normalize_combat_system({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
                    "use_weapon_damage": True,
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
        self.world.config.save(update_fields=["combat_resolution_interval", "combat_system"])

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type):
        return [
            msg["message"]
            for msg in messages
            if msg["message"].get("type") == message_type
            and msg["player_key"] == self.player.key
        ]

    def _weapon(self, name, *, weapon_damage=5, container=None):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug=name.lower().replace(" ", "-"),
            name=name,
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "weapon_damage": weapon_damage,
            },
        )
        return Item.objects.create(
            world=self.spawn_world,
            container=container or self.player,
            definition=definition,
            definition_slug_snapshot=definition.slug,
            name=definition.name,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            weapon_damage=weapon_damage,
        )

    def _configure_assassin_dual_wield(self):
        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system["class_profiles"]["assassin"] = {
            "label": "Assassin",
            "main_attribute": "brawn",
            "attribute_weights": {
                "brawn": 4,
                "grit": 2,
                "focus": 1,
            },
            "features": {
                "equipment": {
                    "can_equip_offhand_weapon": True,
                    "allowed_offhand_weapon_grips": ["one_hand"],
                },
            },
        }
        self.world.config.stat_system = stat_system
        self.world.config.equipment_system = {
            "offhand_weapons": {
                "default_allowed": False,
                "allowed_grips": ["one_hand"],
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
        self.world.config.save(update_fields=["stat_system", "equipment_system", "combat_system"])

    def test_class_feature_allows_offhand_weapon_equipping(self):
        self._configure_assassin_dual_wield()
        self.player.archetype = "assassin"
        self.player.save(update_fields=["archetype"])
        mainhand = self._weapon("Steel Dagger")
        offhand = self._weapon("Bone Dagger")
        self.player.equipment.equip(mainhand, adv_consts.EQUIPMENT_SLOT_WEAPON)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "wield bone")

        self.player.equipment.refresh_from_db()
        offhand.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, mainhand.id)
        self.assertEqual(self.player.equipment.offhand_id, offhand.id)
        self.assertEqual(offhand.container_id, self.player.equipment.id)
        self.assertIsNotNone(self._message_by_type(messages, "cmd.wield.success"))

    def test_offhand_weapon_policy_blocks_class_without_feature(self):
        self._configure_assassin_dual_wield()
        self.player.archetype = "warrior"
        self.player.save(update_fields=["archetype"])
        mainhand = self._weapon("Steel Sword")
        replacement = self._weapon("War Axe")
        self.player.equipment.equip(mainhand, adv_consts.EQUIPMENT_SLOT_WEAPON)

        with capture_game_messages():
            dispatch_text_command(self.player.id, "wield axe")

        self.player.equipment.refresh_from_db()
        mainhand.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, replacement.id)
        self.assertIsNone(self.player.equipment.offhand_id)
        self.assertEqual(mainhand.container_id, self.player.id)

    def test_resolve_attack_can_use_offhand_weapon_damage(self):
        mainhand = self._weapon("Steel Sword", weapon_damage=10, container=self.player.equipment)
        offhand = self._weapon("Parrying Dagger", weapon_damage=4, container=self.player.equipment)
        self.player.equipment.equip(mainhand, adv_consts.EQUIPMENT_SLOT_WEAPON)
        self.player.equipment.equip(offhand, adv_consts.EQUIPMENT_SLOT_OFFHAND)
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target",
            keywords="target",
            health=100,
            health_max=100,
        )

        result = resolve_attack(
            actor=self.player,
            target=target,
            world=self.spawn_world,
            weapon_slot=adv_consts.EQUIPMENT_SLOT_OFFHAND,
            damage_multiplier=0.5,
        )

        self.assertEqual(result.damage_base, 4)
        self.assertEqual(result.damage_taken, 2)

    def test_active_effect_adds_extra_mainhand_strike(self):
        weapon = self._weapon("Steel Sword", weapon_damage=5, container=self.player.equipment)
        self.player.equipment.equip(weapon, adv_consts.EQUIPMENT_SLOT_WEAPON)
        self.player.active_effects = [
            {
                "effect": "battle-trance",
                "remaining_rounds": 3,
                "primitives": [
                    {
                        "type": "combat_modifier",
                        "phase": "attack_routine",
                        "attack_routine": {
                            "extra_mainhand_strikes": 1,
                            "strike": {
                                "source": "battle-trance",
                                "weapon_slot": "weapon",
                                "damage_multiplier": 1,
                            },
                        },
                    }
                ],
            }
        ]
        self.player.save(update_fields=["active_effects"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=20,
            health_max=20,
            fights_back=False,
        )
        mob.create_corpse()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        mob.refresh_from_db()
        attack_messages = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(mob.health, 10)
        self.assertEqual(len(attack_messages), 2)
        self.assertEqual(attack_messages[0]["data"]["attack"], "attack")
        self.assertEqual(attack_messages[1]["data"]["attack"], "battle-trance")

    def test_dual_wield_policy_adds_offhand_strike_to_routine(self):
        self._configure_assassin_dual_wield()
        self.player.archetype = "assassin"
        self.player.save(update_fields=["archetype"])
        mainhand = self._weapon("Steel Dagger", weapon_damage=8, container=self.player.equipment)
        offhand = self._weapon("Bone Dagger", weapon_damage=4, container=self.player.equipment)
        self.player.equipment.equip(mainhand, adv_consts.EQUIPMENT_SLOT_WEAPON)
        self.player.equipment.equip(offhand, adv_consts.EQUIPMENT_SLOT_OFFHAND)

        strikes = resolve_attack_routine(actor=self.player, world=self.spawn_world)

        self.assertEqual([strike.attack for strike in strikes], ["attack", "dual_wield_offhand"])
        self.assertEqual(strikes[1].weapon_slot, adv_consts.EQUIPMENT_SLOT_OFFHAND)
        self.assertEqual(strikes[1].damage_multiplier, 0.5)

    def test_mob_trait_adds_virtual_offhand_strike(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Blade Dancer",
            keywords="blade dancer",
            health=50,
            health_max=50,
            weapon_damage=10,
            trait_instances=[
                {
                    "key": "dual-wielder",
                    "params": {
                        "attack_routine": {
                            "extra_offhand_strikes": 1,
                            "offhand_damage_multiplier": 0.5,
                        }
                    },
                }
            ],
        )

        strikes = resolve_attack_routine(actor=mob, target=self.player, world=self.spawn_world)

        self.assertEqual([strike.attack for strike in strikes], ["attack", "dual-wielder"])
        self.assertEqual(strikes[1].weapon_slot, adv_consts.EQUIPMENT_SLOT_OFFHAND)
        self.assertEqual(strikes[1].damage_multiplier, 0.5)
