from builders.models import ItemTemplate
from config import constants as adv_consts
from core.computations import compute_stats
from spawns.handlers import dispatch_command
from spawns.models import Item
from django.utils import timezone
from tests.base import WorldTestCase
from wr2_tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestLookCommandText(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_look_success_includes_text(self):
        self.room.description = "A test room."
        self.room.save(update_fields=["description"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))
        self.assertIn(self.room.name, message["text"])
        self.assertIn("A test room.", message["text"])

    def test_look_capitalizes_generated_mob_room_description(self):
        mob = self.create_mob("a rat", keywords="rat")

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        room_chars = message["data"]["target"]["chars"]
        mob_payload = next(char for char in room_chars if char["key"] == mob.key)
        self.assertEqual(mob_payload["room_description"], "A rat is here.")
        self.assertIn("A rat is here.", message["text"])

    def test_look_target_mob_returns_char_payload(self):
        mob = self.create_mob(
            "Sam",
            description="A watchful scout studies the room.",
            keywords="sam scout",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look sam")

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "char")
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["target"]["char_type"], "mob")
        self.assertEqual(
            message["data"]["target"]["description"],
            "A watchful scout studies the room.",
        )
        self.assertIn("Sam", message["text"])
        self.assertIn("watchful scout", message["text"].lower())

    def test_look_target_room_item_returns_item_payload(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="Lantern",
            description="A brass lantern with a warm flame.",
            keywords="lantern",
        )
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=template,
            name=template.name,
            description=template.description,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look lantern")

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "item")
        self.assertEqual(message["data"]["target"]["key"], item.key)
        self.assertEqual(
            message["data"]["target"]["description"],
            "A brass lantern with a warm flame.",
        )
        self.assertIn("Lantern", message["text"])

    def test_look_target_inventory_container_includes_contents(self):
        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name="Bag",
            type=adv_consts.ITEM_TYPE_CONTAINER,
            keywords="bag",
        )
        bag = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=bag_template,
            name=bag_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        apple_template = ItemTemplate.objects.create(
            world=self.world,
            name="Apple",
            keywords="apple",
        )
        apple = Item.objects.create(
            world=self.spawn_world,
            container=bag,
            template=apple_template,
            name=apple_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look bag")

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "item")
        self.assertEqual(message["data"]["target"]["key"], bag.key)
        self.assertEqual(len(message["data"]["target"]["inventory"]), 1)
        self.assertEqual(message["data"]["target"]["inventory"][0]["key"], apple.key)
        self.assertIn("Apple", message["text"])

    def test_look_target_not_found_returns_error(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look sam")

        message = self._message_by_type(messages, "cmd.look.error")
        self.assertIsNotNone(message)
        self.assertIn("don't see that here", message["text"].lower())


class TestStateSyncText(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_state_sync_includes_full_room_text(self):
        self.room.description = "A sync room."
        self.room.save(update_fields=["description"])

        self.player.refresh_from_db()
        if self.player.config:
            self.player.config.room_brief = True
            self.player.config.save(update_fields=["room_brief"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))
        self.assertIn(self.room.name, message["text"])
        self.assertIn("A sync room.", message["text"])

    def test_state_sync_who_list_includes_only_in_game_players(self):
        self.player.in_game = True
        self.player.last_action_ts = timezone.now()
        self.player.save(update_fields=["in_game", "last_action_ts"])

        online_user = self.create_user("online@example.com")
        online_player = self.create_player("Online", user=online_user)
        online_player.in_game = True
        online_player.last_action_ts = timezone.now()
        online_player.save(update_fields=["in_game", "last_action_ts"])

        offline_user = self.create_user("offline@example.com")
        offline_player = self.create_player("Offline", user=offline_user)
        offline_player.in_game = False
        offline_player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        who_keys = {entry["key"] for entry in message["data"]["who_list"]}
        self.assertIn(self.player.key, who_keys)
        self.assertIn(online_player.key, who_keys)
        self.assertNotIn(offline_player.key, who_keys)

    def test_state_sync_actor_includes_computed_vital_caps(self):
        apply_basic_stat_system(self.world)
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.health = max(stats["health_max"] - 10, 1)
        self.player.energy = max(stats["energy_max"] - 1, 0)
        self.player.stamina = max(stats["stamina_max"] - 1, 0)
        self.player.save(update_fields=["health", "energy", "stamina"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        actor = message["data"]["actor"]

        self.assertEqual(actor["health_max"], stats["health_max"])
        self.assertEqual(actor["energy_max"], stats["energy_max"])
        self.assertEqual(actor["stamina_max"], stats["stamina_max"])

    def test_state_sync_includes_world_stat_labels_and_aliases(self):
        self.world.config.stat_system = {
            "labels": {
                "resources": {
                    "energy": "Focus",
                },
                "stats": {
                    "ability_power": "Ability Power",
                },
                "classes": {
                    "warrior": "Vanguard",
                },
            },
            "attributes": [
                {"key": "grit", "label": "Grit"},
                {"key": "brawn", "label": "Brawn"},
                {"key": "grace", "label": "Grace"},
                {"key": "willpower", "label": "Willpower"},
                {"key": "insight", "label": "Awareness"},
            ],
            "class_profiles": {
                "warrior": {
                    "label": "Vanguard",
                    "main_attribute": "brawn",
                    "attribute_weights": {
                        "grit": 3,
                        "brawn": 4,
                        "grace": 1,
                        "willpower": 1,
                        "insight": 2,
                    },
                },
            },
            "formulas": {
                "global_rules": [
                    {"source": "grit", "target": "health_max", "multiplier": 2},
                    {"source": "grit", "target": "resilience", "multiplier": 1},
                    {"source": "brawn", "target": "attack_power", "multiplier": 1},
                    {"source": "brawn", "target": "health_max", "multiplier": 1},
                    {"source": "grace", "target": "dodge", "multiplier": 1},
                    {"source": "grace", "target": "crit", "multiplier": 1},
                    {"source": "willpower", "target": "ability_power", "multiplier": 2},
                    {"source": "insight", "target": "energy_max", "multiplier": 2},
                ],
            },
        }
        self.world.config.save(update_fields=["stat_system"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        actor = message["data"]["actor"]
        world_data = message["data"]["world"]

        self.assertEqual(world_data["labels"]["resources"]["energy"], "Focus")
        self.assertEqual(
            world_data["labels"]["stats"]["ability_power"],
            "Ability Power",
        )
        self.assertEqual(world_data["labels"]["classes"]["warrior"], "Vanguard")
        self.assertEqual(actor["energy"], actor["energy"])
        self.assertEqual(actor["energy_max"], actor["energy_max"])
        self.assertEqual(actor["ability_power"], actor["ability_power"])
        self.assertIn("insight", actor["attributes"])
        self.assertGreater(actor["attributes"]["insight"], 0)

    def test_state_sync_room_chars_include_primary_keyword(self):
        mob = self.create_mob("Gus Tone", keywords="gus tone")

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        room_chars = message["data"]["room"]["chars"]
        mob_payload = next(char for char in room_chars if char["key"] == mob.key)
        self.assertEqual(mob_payload["keywords"], "gus tone")
        self.assertEqual(mob_payload["keyword"], "gus")

    def test_state_sync_capitalizes_generated_mob_room_description(self):
        mob = self.create_mob("a rat", keywords="rat")

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        room_chars = message["data"]["room"]["chars"]
        mob_payload = next(char for char in room_chars if char["key"] == mob.key)
        self.assertEqual(mob_payload["room_description"], "A rat is here.")
        self.assertIn("A rat is here.", message["text"])


class TestStateSyncMapKeys(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_state_sync_actor_room_key_matches_map_when_relative_id_differs(self):
        self.room.relative_id = self.room.id + 5000
        self.room.save(update_fields=["relative_id"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        data = message["data"]
        actor_room_key = data["actor"]["room"]["key"]
        room_key = data["room"]["key"]
        map_keys = {room["key"] for room in data["map"]}

        self.assertEqual(actor_room_key, room_key)
        self.assertIn(actor_room_key, map_keys)
        self.assertEqual(actor_room_key, f"room.{self.room.relative_id}")

    def test_state_sync_world_room_refs_use_relative_key(self):
        self.room.relative_id = self.room.id + 9000
        self.room.save(update_fields=["relative_id"])

        self.world.config.starting_room = self.room
        self.world.config.death_room = self.room
        self.world.config.save(update_fields=["starting_room", "death_room"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        world_data = message["data"]["world"]
        expected_key = f"room.{self.room.relative_id}"
        self.assertEqual(world_data["starting_room"], expected_key)
        self.assertEqual(world_data["death_room"], expected_key)
        self.assertEqual(world_data["starting_level"], 1)
        self.assertEqual(world_data["max_level"], 20)
        self.assertEqual(world_data["leveling_curve"][1], 30)
        self.assertEqual(world_data["combat_resolution_interval"], 0.0)


class TestUnknownTextCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_unknown_text_command_returns_explicit_helpful_error(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flarble")

        error_message = self._message_by_type(messages, "cmd.text.error")
        self.assertIsNotNone(error_message)
        self.assertEqual(
            error_message["text"],
            "Unknown command: 'flarble'. Type 'help' for help.",
        )
        self.assertEqual(error_message["data"]["code"], "unknown_cmd")
        self.assertEqual(error_message["data"]["original_command"], "flarble")
