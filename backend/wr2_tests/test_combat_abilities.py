from unittest.mock import patch

from builders.models import AbilityDefinition
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from django.utils import timezone
from spawns.models import CombatEncounter, Mob
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestCombatAbilities(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.stats = compute_stats(self.player.level, self.player.archetype)
        self.player.health = self.stats["health_max"]
        self.player.mana = self.stats["mana_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(update_fields=["health", "mana", "stamina", "in_game"])
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
                "basic_heal": {
                    "power_stat": "health_max",
                    "power_scale": 0.5,
                    "can_crit": False,
                    "variance": "none",
                    "minimum": 0,
                },
            },
        })
        self.world.config.save(update_fields=["combat_resolution_interval", "combat_system"])

    def _ability(self, *, slug, name, verbs, components, target=None, cost=None, cooldown=None):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=verbs,
            action_type="primary",
            target=target or {
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost=cost or {},
            cooldown=cooldown or {"rounds": 0},
            components=components,
        )

    def _mob(self, *, health=None, attack_power=0, fights_back=False):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=health or self.stats["attack_power"] * 10,
            health_max=health or self.stats["attack_power"] * 10,
            attack_power=attack_power,
            fights_back=fights_back,
            exp_worth=1,
        )
        mob.create_corpse()
        return mob

    def _messages_by_type(self, messages, message_type):
        return [
            msg["message"]
            for msg in messages
            if msg["player_key"] == self.player.key
            and msg["message"].get("type") == message_type
        ]

    def test_learning_ability_assigns_next_available_hotkey(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, ["power-strike"])
        self.assertEqual(self.player.ability_hotkeys, {"1": "power-strike"})
        success = self._messages_by_type(messages, "cmd.ability.learn.success")[0]
        self.assertEqual(success["data"]["ability"]["hotkey"], "1")
        self.assertEqual(success["data"]["actor"]["ability_hotkeys"], {"1": "power-strike"})

    def test_hotkey_command_reassigns_known_ability(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self.player.known_abilities = ["power-strike", "quick-jab"]
        self.player.ability_hotkeys = {"1": "power-strike", "2": "quick-jab"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "hotkey 1 quick jab")

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_hotkeys, {"1": "quick-jab"})
        success = self._messages_by_type(messages, "cmd.ability.hotkey.success")[0]
        self.assertEqual(success["data"]["hotkey"], "1")
        self.assertEqual(success["data"]["replaced_ability"]["slug"], "power-strike")
        self.assertEqual(success["data"]["actor"]["ability_hotkeys"], {"1": "quick-jab"})

    def test_numbered_hotkey_uses_assigned_ability_and_reports_round_cooldown(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1},
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])
        mob = self._mob(health=self.stats["attack_power"] * 10)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "1 rat")

        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 9)
        self.assertEqual(self.player.ability_cooldowns, {"power-strike": 2})
        updates = self._messages_by_type(messages, "player.abilities.update")
        self.assertEqual(updates[-1]["data"]["actor"]["ability_cooldowns"], {"power-strike": 2})

    def test_state_sync_includes_ability_hotkeys_cooldowns_and_definitions(self):
        from spawns.state_payloads import build_state_sync

        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            cooldown={"rounds": 2},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.ability_cooldowns = {"power-strike": 2}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys", "ability_cooldowns"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(payload["actor"]["known_abilities"], ["power-strike"])
        self.assertEqual(payload["actor"]["ability_hotkeys"], {"1": "power-strike"})
        self.assertEqual(payload["actor"]["ability_cooldowns"], {"power-strike": 2})
        self.assertEqual(
            payload["world"]["abilities"]["definitions"]["power-strike"]["cooldown"],
            {"rounds": 2},
        )

    def test_queued_ability_replaces_auto_attack_for_the_round(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "strike rat")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 3)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(len(attacks), 1)
        self.assertEqual(attacks[0]["data"]["attack"], "power-strike")
        self.assertEqual(attacks[0]["data"]["damage_taken"], self.stats["attack_power"] * 2)

    def test_queued_ability_can_be_replaced_before_scheduled_resolution(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 2}, "text": {"label": "Power Strike"}}],
        )
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 1}, "text": {"label": "Quick Jab"}}],
        )
        self.player.known_abilities = ["power-strike", "quick-jab"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "strike rat")
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "jab rat")

        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_player_ability["ability"], "quick-jab")
        self.assertEqual(self._messages_by_type(messages, "cmd.ability.success")[0]["text"], "You switch to Quick Jab.")
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            resolve_combat_encounter(encounter.id)

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)

    def test_invalid_queued_ability_falls_back_to_auto_attack(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._ability(
            slug="mana-strike",
            name="Mana Strike",
            verbs=["mstrike"],
            cost={"resource": "mana", "amount": 1, "calc": "fixed"},
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 2}, "text": {"label": "Mana Strike"}}],
        )
        self.player.known_abilities = ["mana-strike"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "mstrike rat")

        self.player.mana = 0
        self.player.save(update_fields=["mana"])
        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)
        failures = self._messages_by_type(messages, "notification.combat.ability_failed")
        self.assertEqual(len(failures), 1)

    def test_stun_prevents_mob_counterattack(self):
        self._ability(
            slug="stun-bash",
            name="Stun Bash",
            verbs=["bash"],
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "duration": {"rounds": 1},
                    "apply": "on_resolve",
                    "text": {"label": "Stun Bash"},
                }
            ],
        )
        self.player.known_abilities = ["stun-bash"]
        self.player.save(update_fields=["known_abilities"])
        self._mob(attack_power=9, fights_back=True)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "bash rat")

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, self.stats["health_max"])
        effect_messages = self._messages_by_type(messages, "notification.combat.effect")
        self.assertTrue(any("stunned" in msg["text"] for msg in effect_messages))

    def test_dot_ticks_during_following_encounter_rounds(self):
        self._ability(
            slug="bleeding-cut",
            name="Bleeding Cut",
            verbs=["bleed"],
            components=[
                {
                    "type": "effect",
                    "effect": "dot",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "component": {
                            "type": "damage",
                            "profile": "basic_physical",
                            "overrides": {"multiplier": 1},
                            "text": {"label": "Bleed"},
                        },
                    },
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["bleeding-cut"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 6)

        dispatch_text_command(self.player.id, "bleed rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 6)

        dispatch_text_command(self.player.id, "kill rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)

    def test_self_heal_uses_same_ability_schema_outside_combat(self):
        self._ability(
            slug="mend",
            name="Mend",
            verbs=["mend"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[
                {
                    "type": "healing",
                    "profile": "basic_heal",
                    "overrides": {},
                    "text": {"label": "Mend"},
                }
            ],
        )
        self.player.known_abilities = ["mend"]
        self.player.health = 1
        self.player.save(update_fields=["known_abilities", "health"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "mend")

        self.player.refresh_from_db()
        self.assertGreater(self.player.health, 1)
        self.assertFalse(CombatEncounter.objects.filter(player=self.player, status=CombatEncounter.STATUS_ACTIVE).exists())
        self.assertEqual(len(self._messages_by_type(messages, "notification.ability.heal")), 1)
