from unittest.mock import patch

from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from builders.models import Faction, Trigger
from config import constants as adv_consts
from spawns.actions.combat import apply_player_death, mob_should_aggro_player
from spawns.models import CombatEncounter, Item, Mob, Player
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from worlds.models import Room
from wr2_tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestKillCommand(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
        )
        self.player.health = self.stats["health_max"]
        self.player.energy = self.stats["energy_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(update_fields=["health", "energy", "stamina", "in_game"])
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
        self.world.config.save(update_fields=["combat_system"])

    def _message_by_type(self, messages, message_type, player_key=None):
        for msg in messages:
            if player_key and msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type, player_key=None):
        return [
            msg["message"]
            for msg in messages
            if msg["message"].get("type") == message_type
            and (player_key is None or msg["player_key"] == player_key)
        ]

    def _death_event_by_type(self, events, event_type):
        for event in events:
            if event.type == event_type:
                return event
        return None

    def _set_death_mode(self, death_mode, *, gold_penalty=None):
        self.world.config.death_mode = death_mode
        update_fields = ["death_mode"]
        if gold_penalty is not None:
            self.world.config.death_gold_penalty = gold_penalty
            update_fields.append("death_gold_penalty")
        self.world.config.save(update_fields=update_fields)

    def _equipped_item(self, *, name="Bronze Sword", slot=adv_consts.EQUIPMENT_SLOT_WEAPON, cost=100):
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.player.equipment,
            name=name,
            cost=cost,
        )
        setattr(self.player.equipment, slot, item)
        self.player.equipment.save(update_fields=[slot])
        return item

    def _inventory_item(self, *, name="Travel Ration"):
        return Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name=name,
        )

    def test_player_death_destroy_eq_destroys_equipment(self):
        self._set_death_mode(adv_consts.DEATH_MODE_DESTROY_EQ)
        sword = self._equipped_item()
        ration = self._inventory_item()
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
        )

        updated_player.refresh_from_db()
        updated_player.equipment.refresh_from_db()
        self.assertIsNone(updated_player.equipment.weapon)
        self.assertFalse(Item.objects.filter(pk=sword.pk).exists())
        self.assertEqual(Item.objects.get(pk=ration.pk).container, updated_player)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "Your equipment is destroyed.")
        self.assertEqual(death_affect.data["actor"]["equipment"]["weapon"], None)

    def test_player_death_destroy_all_destroys_equipment_and_inventory(self):
        self._set_death_mode(adv_consts.DEATH_MODE_DESTROY_ALL)
        sword = self._equipped_item()
        shield = self._equipped_item(
            name="Iron Shield",
            slot=adv_consts.EQUIPMENT_SLOT_OFFHAND,
        )
        ration = self._inventory_item()
        token = self._inventory_item(name="Copper Token")
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
        )

        updated_player.refresh_from_db()
        updated_player.equipment.refresh_from_db()
        self.assertIsNone(updated_player.equipment.weapon)
        self.assertIsNone(updated_player.equipment.offhand)
        self.assertFalse(
            Item.objects.filter(pk__in=[sword.pk, shield.pk, ration.pk, token.pk]).exists()
        )

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(
            death_affect.data["penalty"],
            "Your equipment and inventory are destroyed.",
        )
        self.assertEqual(death_affect.data["actor"]["equipment"]["weapon"], None)
        self.assertEqual(death_affect.data["actor"]["equipment"]["offhand"], None)
        self.assertEqual(death_affect.data["actor"]["inventory"], [])

    def test_player_death_lose_all_drops_equipment_and_inventory_in_origin_room_corpse(self):
        self._set_death_mode(adv_consts.DEATH_MODE_LOSE_ALL)
        sword = self._equipped_item()
        ration = self._inventory_item()
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
            room_text="Ogre kills Joe.",
        )

        updated_player.refresh_from_db()
        updated_player.equipment.refresh_from_db()
        sword.refresh_from_db()
        ration.refresh_from_db()
        self.assertIsNone(updated_player.equipment.weapon)
        corpse = self.room.inventory.get(type=adv_consts.ITEM_TYPE_CORPSE)
        self.assertEqual(corpse.name, "the corpse of Joe")
        self.assertEqual(sword.container, corpse)
        self.assertEqual(ration.container, corpse)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "Your equipment is left behind.")
        self.assertEqual(death_affect.data["actor"]["inventory"], [])

        death_notification = self._death_event_by_type(events, "notification.death")
        self.assertIsNotNone(death_notification)
        self.assertEqual(death_notification.data["corpse"]["key"], corpse.key)
        self.assertEqual(
            {item["key"] for item in death_notification.data["corpse"]["inventory"]},
            {sword.key, ration.key},
        )

    def test_player_death_lose_inv_drops_inventory_and_keeps_equipment(self):
        self._set_death_mode(adv_consts.DEATH_MODE_LOSE_INV)
        sword = self._equipped_item()
        ration = self._inventory_item()
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
        )

        updated_player.refresh_from_db()
        updated_player.equipment.refresh_from_db()
        sword.refresh_from_db()
        ration.refresh_from_db()
        corpse = self.room.inventory.get(type=adv_consts.ITEM_TYPE_CORPSE)
        self.assertEqual(updated_player.equipment.weapon_id, sword.id)
        self.assertEqual(sword.container, updated_player.equipment)
        self.assertEqual(ration.container, corpse)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "Your inventory is left behind.")

    def test_player_death_lose_gold_charges_repairs_for_non_pvp_death(self):
        self._set_death_mode(adv_consts.DEATH_MODE_LOSE_GOLD, gold_penalty=0.25)
        self._equipped_item(cost=100)
        self._equipped_item(name="Iron Shield", slot=adv_consts.EQUIPMENT_SLOT_OFFHAND, cost=60)
        self.player.gold = 30
        self.player.save(update_fields=["gold"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
        )

        updated_player.refresh_from_db()
        self.assertEqual(updated_player.gold, 0)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "You pay 30 gold for repairs.")
        self.assertEqual(death_affect.data["actor"]["gold"], 0)

    def test_player_death_lose_gold_does_not_charge_repairs_for_pvp_death(self):
        self._set_death_mode(adv_consts.DEATH_MODE_LOSE_GOLD, gold_penalty=0.25)
        self._equipped_item(cost=100)
        self.player.gold = 30
        self.player.save(update_fields=["gold"])
        killer = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Killer",
            user=self.create_user("killer@example.com"),
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=killer,
        )

        updated_player.refresh_from_db()
        self.assertEqual(updated_player.gold, 30)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "")

    def test_player_death_lose_eq_drops_equipment_and_keeps_inventory(self):
        self._set_death_mode(adv_consts.DEATH_MODE_LOSE_EQ)
        sword = self._equipped_item()
        ration = self._inventory_item()
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
        )

        updated_player, events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            killer=mob,
        )

        updated_player.refresh_from_db()
        updated_player.equipment.refresh_from_db()
        sword.refresh_from_db()
        ration.refresh_from_db()
        corpse = self.room.inventory.get(type=adv_consts.ITEM_TYPE_CORPSE)
        self.assertIsNone(updated_player.equipment.weapon)
        self.assertEqual(sword.container, corpse)
        self.assertEqual(ration.container, updated_player)

        death_affect = self._death_event_by_type(events, "affect.death")
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect.data["penalty"], "Your equipment is left behind.")

    def test_kill_auto_resolves_until_mob_dies_and_awards_rewards(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] + 5,
            health_max=self.stats["attack_power"] + 5,
            attack_power=4,
            exp_worth=17,
            gold=300,
        )
        mob.create_corpse()
        exp_before = self.player.experience
        gold_before = self.player.gold
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.experience, exp_before + 17)
        self.assertEqual(self.player.gold, gold_before + 300)

        actor_attacks = self._messages_by_type(
            messages,
            "notification.combat.attack",
            self.player.key,
        )
        self.assertGreaterEqual(len(actor_attacks), 3)
        self.assertEqual(actor_attacks[0]["data"]["damage_taken"], self.stats["attack_power"])

        death_message = self._message_by_type(
            messages,
            "notification.death",
            self.player.key,
        )
        self.assertIsNotNone(death_message)
        self.assertEqual(death_message["text"], "Rat is dead! R.I.P.")
        self.assertEqual(death_message["data"]["actor"]["experience"], exp_before + 17)
        self.assertEqual(death_message["data"]["actor"]["gold"], gold_before + 300)
        self.assertEqual(death_message["data"]["gold_gained"], 300)
        self.assertTrue(
            any(
                item["type"] == "corpse" and "rat" in item["name"].lower()
                for item in death_message["data"]["room"]["inventory"]
            )
        )

        reward_message = self._message_by_type(
            messages,
            "notification.reward",
            self.player.key,
        )
        self.assertIsNotNone(reward_message)
        self.assertEqual(
            reward_message["text"],
            "You gain 17 experience.\nYou receive 300 gold.",
        )
        self.assertEqual(reward_message["data"]["actor"]["experience"], exp_before + 17)
        self.assertEqual(reward_message["data"]["actor"]["gold"], gold_before + 300)

        watcher_death = self._message_by_type(
            messages,
            "notification.death",
            watcher.key,
        )
        self.assertIsNotNone(watcher_death)
        self.assertEqual(watcher_death["text"], "Rat is dead! R.I.P.")
        self.assertIsNone(
            self._message_by_type(messages, "notification.reward", watcher.key)
        )

    def test_kill_reward_levels_player_when_xp_crosses_threshold(self):
        self.player.experience = 29
        self.player.level = 1
        self.player.save(update_fields=["experience", "level"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"],
            health_max=self.stats["attack_power"],
            attack_power=0,
            exp_worth=1,
        )
        mob.create_corpse()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.experience, 30)
        self.assertEqual(self.player.level, 2)

        reward_message = self._message_by_type(
            messages,
            "notification.reward",
            self.player.key,
        )
        self.assertIsNotNone(reward_message)
        self.assertEqual(
            reward_message["text"],
            "You gain 1 experience.\nYou are now level 2!",
        )
        self.assertEqual(reward_message["data"]["previous_level"], 1)
        self.assertEqual(reward_message["data"]["new_level"], 2)
        self.assertEqual(reward_message["data"]["levels_gained"], 1)
        self.assertEqual(reward_message["data"]["actor"]["level"], 2)
        self.assertEqual(reward_message["data"]["experience_progress"], 0)
        self.assertEqual(reward_message["data"]["experience_needed"], 70)

    def test_kill_can_get_player_killed_and_moves_them_to_death_room(self):
        graveyard = self.room.create_at("east")
        self.world.config.death_room = graveyard
        self.world.config.save(update_fields=["death_room"])
        room_ct = ContentType.objects.get_for_model(Room)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_ct,
            target_id=graveyard.id,
            event=adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
            script="/cmd room -- /echo -- Death room trigger fired.",
            display_action_in_room=False,
            gate_delay=0,
        )

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=self.stats["health_max"],
            exp_worth=99,
        )
        mob.create_corpse()

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill ogre")

        self.player.refresh_from_db()
        self.assertTrue(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.room_id, graveyard.id)
        self.assertEqual(self.player.health, self.stats["health_max"])

        death_affect = self._message_by_type(messages, "affect.death", self.player.key)
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect["data"]["room"]["id"], graveyard.id)
        self.assertEqual(death_affect["data"]["origin_room"]["id"], self.room.id)

        death_room_echo = self._message_by_type(
            messages,
            "cmd./echo.success",
        )
        self.assertIsNotNone(death_room_echo, [msg["message"] for msg in messages])
        self.assertIn("Death room trigger fired", death_room_echo["text"])

        watcher_death = self._message_by_type(messages, "notification.death", watcher.key)
        self.assertIsNotNone(watcher_death)
        self.assertEqual(watcher_death["data"]["deceased"]["key"], self.player.key)
        self.assertEqual(watcher_death["text"], "Ogre kills Joe.")

    def test_invisible_player_is_not_eligible_for_mob_aggro(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Sentinel",
            keywords="sentinel",
            aggression=adv_consts.MOB_AGGRESSION_ALL,
        )

        self.assertTrue(mob_should_aggro_player(mob, self.player))

        self.player.is_builder = True
        self.player.is_invisible = True
        self.player.save(update_fields=["is_builder", "is_invisible"])

        self.assertFalse(mob_should_aggro_player(mob, self.player))

    def test_mob_aggression_modes_match_wr1_faction_rules(self):
        human = Faction.objects.create(
            world=self.world,
            code="aggro_human",
            name="Aggro Human",
            is_core=True,
        )
        orc = Faction.objects.create(
            world=self.world,
            code="aggro_orc",
            name="Aggro Orc",
            is_core=True,
        )
        player = self.player
        player.faction_assignments.create(faction=human)

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Sentinel",
            keywords="sentinel",
            aggression=adv_consts.MOB_AGGRESSION_NORMAL,
        )
        self.assertFalse(mob_should_aggro_player(mob, player))

        mob.faction_assignments.create(faction=orc)
        self.assertTrue(mob_should_aggro_player(mob, player))

        mob.aggression = adv_consts.MOB_AGGRESSION_FRIENDLY
        self.assertTrue(mob_should_aggro_player(mob, player))

        mob.aggression = adv_consts.MOB_AGGRESSION_PASSIVE
        self.assertFalse(mob_should_aggro_player(mob, player))

        mob.aggression = adv_consts.MOB_AGGRESSION_ALL
        self.assertTrue(mob_should_aggro_player(mob, player))

        mob.aggression = adv_consts.MOB_AGGRESSION_PLAYERS
        self.assertTrue(mob_should_aggro_player(mob, player))

        mob.aggression = "aggressive"
        self.assertTrue(mob_should_aggro_player(mob, player))

    def test_mob_aggro_starts_combat_when_player_enters_room(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        destination = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Gatehouse",
            x=self.room.x + 1,
            y=self.room.y,
            z=self.room.z,
        )
        self.room.east = destination
        self.room.save(update_fields=["east"])
        destination.west = self.room
        destination.save(update_fields=["west"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=destination,
            name="Sentinel",
            keywords="sentinel",
            health=20,
            health_max=20,
            attack_power=4,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "east")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.round_number, 0)
        engage_message = self._message_by_type(
            messages,
            "cmd.kill.success",
            self.player.key,
        )
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["text"], "Sentinel attacks you!")
        self.assertEqual(engage_message["data"]["actor"]["state"], "combat")
        self.assertEqual(engage_message["data"]["actor"]["target"]["key"], mob.key)
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(schedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_passive_mob_does_not_aggro_when_player_enters_room(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        destination = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Quiet Hall",
            x=self.room.x + 1,
            y=self.room.y,
            z=self.room.z,
        )
        self.room.east = destination
        self.room.save(update_fields=["east"])
        Mob.objects.create(
            world=self.spawn_world,
            room=destination,
            name="Guard",
            keywords="guard",
            aggression=adv_consts.MOB_AGGRESSION_PASSIVE,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "east")

        self.assertIsNotNone(
            self._message_by_type(messages, "cmd.move.success", self.player.key)
        )
        self.assertIsNone(
            self._message_by_type(messages, "cmd.kill.success", self.player.key)
        )
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        schedule_mock.assert_not_called()

    def test_kill_with_positive_interval_starts_encounter_without_immediate_resolution(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] + 5,
            health_max=self.stats["attack_power"] + 5,
            attack_power=4,
            exp_worth=17,
        )
        mob.create_corpse()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "kill rat")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        mob.refresh_from_db()

        self.assertEqual(encounter.round_number, 0)
        self.assertEqual(mob.health, self.stats["attack_power"] + 5)
        self.assertIsNone(self._message_by_type(messages, "notification.combat.attack", self.player.key))
        engage_message = self._message_by_type(messages, "cmd.kill.success", self.player.key)
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["text"], "You engage Rat.")
        self.assertEqual(engage_message["data"]["actor"]["state"], "combat")
        self.assertEqual(engage_message["data"]["actor"]["target"]["key"], mob.key)
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(schedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_bare_k_targets_single_attackable_room_mob(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Persian Guard",
            keywords="persian guard",
            health=self.stats["attack_power"] + 5,
            health_max=self.stats["attack_power"] + 5,
            attack_power=4,
            exp_worth=17,
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Blacksmith",
            keywords="blacksmith",
            attackable=False,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "k")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        engage_message = self._message_by_type(messages, "cmd.kill.success", self.player.key)
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["text"], "You engage Persian Guard.")
        self.assertEqual(engage_message["data"]["actor"]["target"]["key"], mob.key)
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )

    def test_bare_k_targets_first_attackable_room_mob(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        first_soldier = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Soldier",
            keywords="soldier",
            health=self.stats["attack_power"],
            health_max=self.stats["attack_power"],
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Soldier",
            keywords="soldier",
            health=self.stats["attack_power"],
            health_max=self.stats["attack_power"],
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "k")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.mob_id, first_soldier.id)
        engage_message = self._message_by_type(messages, "cmd.kill.success", self.player.key)
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["data"]["actor"]["target"]["key"], first_soldier.key)

    def test_bare_k_does_not_target_single_non_attackable_mob(self):
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Blacksmith",
            keywords="blacksmith",
            health=self.stats["attack_power"],
            health_max=self.stats["attack_power"],
            attackable=False,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "k")

        error = self._message_by_type(messages, "cmd.kill.error", self.player.key)
        self.assertIsNotNone(error)
        self.assertEqual(error["text"], "Kill what?")
        self.assertEqual(error["data"]["code"], "missing_target")
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )

    def test_scheduled_combat_round_advances_one_step_and_reschedules(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=4,
            fights_back=True,
            exp_worth=17,
        )
        mob.create_corpse()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as reschedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()

        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(mob.health, self.stats["attack_power"] * 2)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        actor_attacks = self._messages_by_type(
            messages,
            "notification.combat.attack",
            self.player.key,
        )
        self.assertEqual(len(actor_attacks), 2)
        self.assertEqual(actor_attacks[0]["data"]["actor"]["state"], "combat")
        self.assertEqual(actor_attacks[1]["data"]["target"]["key"], self.player.key)
        reschedule_mock.assert_called_once()
        self.assertEqual(
            reschedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(reschedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_manual_combat_interval_advances_one_round_per_kill_command(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 2,
            health_max=self.stats["attack_power"] * 2,
            attack_power=4,
            fights_back=True,
            exp_worth=9,
            gold=25,
        )
        mob.create_corpse()
        exp_before = self.player.experience
        gold_before = self.player.gold

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with capture_game_messages() as first_messages:
                dispatch_text_command(self.player.id, "kill rat")

            encounter = CombatEncounter.objects.get(
                player=self.player,
                mob=mob,
                status=CombatEncounter.STATUS_ACTIVE,
            )
            mob.refresh_from_db()
            self.player.refresh_from_db()

            self.assertEqual(encounter.round_number, 1)
            self.assertEqual(mob.health, self.stats["attack_power"])
            self.assertEqual(self.player.health, self.stats["health_max"] - 4)
            self.assertIsNotNone(self._message_by_type(first_messages, "cmd.kill.success", self.player.key))

            with capture_game_messages() as second_messages:
                dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        schedule_mock.assert_not_called()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(
            CombatEncounter.objects.get(pk=encounter.id).status,
            CombatEncounter.STATUS_FINISHED,
        )
        self.assertEqual(self.player.experience, exp_before + 9)
        self.assertEqual(self.player.gold, gold_before + 25)
        death_message = self._message_by_type(
            second_messages,
            "notification.death",
            self.player.key,
        )
        self.assertIsNotNone(death_message)
        self.assertEqual(death_message["text"], "Rat is dead! R.I.P.")
        reward_message = self._message_by_type(
            second_messages,
            "notification.reward",
            self.player.key,
        )
        self.assertIsNotNone(reward_message)
        self.assertEqual(
            reward_message["text"],
            "You gain 9 experience.\nYou receive 25 gold.",
        )
