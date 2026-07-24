from builders.models import Faction, ItemDefinition
from config import constants as adv_consts
from core.computations import compute_stats
from spawns.handlers import dispatch_command
from spawns.models import CombatEncounter, Item, Mob
from spawns.state_payloads import build_map_payload
from django.utils import timezone
from tests.base import WorldTestCase
from tests.utils import (
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

    def test_look_uses_item_room_description(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="iron-ration",
            name="an iron ration",
            room_description="An iron ration lies here.",
            keywords="ration",
        )
        item = definition.spawn(self.room, self.spawn_world)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        room_inventory = message["data"]["target"]["inventory"]
        item_payload = next(entry for entry in room_inventory if entry["key"] == item.key)
        self.assertEqual(
            item_payload["room_description"],
            "An iron ration lies here.",
        )
        self.assertNotIn("ground_description", item_payload)
        self.assertIn("An iron ration lies here.", message["text"])

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
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="lantern",
            name="Lantern",
            description="A brass lantern with a warm flame.",
            keywords="lantern",
        )
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            definition=definition,
            definition_slug_snapshot=definition.slug,
            name=definition.name,
            description=definition.description,
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
        bag_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bag",
            name="Bag",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
            keywords="bag",
        )
        bag = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            definition=bag_definition,
            definition_slug_snapshot=bag_definition.slug,
            name=bag_definition.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        apple_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="apple",
            name="Apple",
            keywords="apple",
        )
        apple = Item.objects.create(
            world=self.spawn_world,
            container=bag,
            definition=apple_definition,
            definition_slug_snapshot=apple_definition.slug,
            name=apple_definition.name,
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


class TestScanCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def setUp(self):
        super().setUp()
        self.exit_room = self.room.create_at("east")
        self.soldier = Mob.objects.create(
            name="a soldier",
            world=self.world,
            room=self.exit_room,
            keywords="soldier",
        )
        self.priest = Mob.objects.create(
            name="a priest",
            world=self.world,
            room=self.exit_room,
            keywords="priest",
        )

    def test_scan_no_args(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan")

        message = self._message_by_type(messages, "cmd.scan.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "Scan in which direction?")

    def test_scan_with_invalid_arg(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan something")

        message = self._message_by_type(messages, "cmd.scan.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "Something is not a valid direction.")

    def test_scan_direction_with_no_exit(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan north")

        message = self._message_by_type(messages, "cmd.scan.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "There is no exit north.")

    def test_scan_direction(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan east")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "A priest is here.\nA soldier is here.")
        self.assertEqual(len(message["data"]["chars"]), 2)
        self.assertEqual(message["data"]["chars"][0]["name"], "a priest")
        self.assertEqual(message["data"]["chars"][1]["name"], "a soldier")

    def test_scan_abbreviated_direction(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan e")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["direction"], "east")
        self.assertEqual(len(message["data"]["chars"]), 2)

    def test_scan_in_empty_exit_room(self):
        self.room.create_at("north")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan north")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message["text"],
            "There doesn't seem to be anything there.",
        )

    def test_scan_to_unscannable_room_type(self):
        for room_type in adv_consts.UNSCANNABLE_ROOM_TYPES:
            self.room.type = room_type
            self.room.save(update_fields=["type"])

            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "scan east")

            message = self._message_by_type(messages, "cmd.scan.error")
            self.assertIsNotNone(message)
            self.assertEqual(message["text"], f"Cannot scan in {room_type}s.")

    def test_scan_hides_invisible_chars_from_non_builder(self):
        self.priest.is_invisible = True
        self.priest.save(update_fields=["is_invisible"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan east")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            [char["name"] for char in message["data"]["chars"]],
            ["a soldier"],
        )
        self.assertEqual(message["text"], "A soldier is here.")

    def test_scan_hides_invisible_chars_from_builder(self):
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])
        self.priest.is_invisible = True
        self.priest.save(update_fields=["is_invisible"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan east")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            [char["name"] for char in message["data"]["chars"]],
            ["a soldier"],
        )

    def test_scan_includes_combat_target_text(self):
        target = self.create_player("Target", room=self.exit_room)
        target.in_game = True
        target.save(update_fields=["in_game"])
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.exit_room,
            player=target,
            mob=self.priest,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan east")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertIn("Target is here, fighting a priest.", message["text"])
        self.assertIn("A priest is here, fighting Target.", message["text"])
        priest = next(
            char for char in message["data"]["chars"]
            if char["key"] == self.priest.key
        )
        self.assertEqual(priest["target"]["name"], "Target")

    def test_scan_ignores_stale_combat_when_participants_are_split(self):
        target = self.create_player("Target", room=self.room)
        target.in_game = True
        target.save(update_fields=["in_game"])
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.exit_room,
            player=target,
            mob=self.priest,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan east")

        message = self._message_by_type(messages, "cmd.scan.success")
        self.assertIsNotNone(message)
        self.assertIn("A priest is here.", message["text"])
        self.assertNotIn("fighting Target", message["text"])
        priest = next(
            char for char in message["data"]["chars"]
            if char["key"] == self.priest.key
        )
        self.assertIsNone(priest["target"])


class TestWhoCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _set_online(self, player, *, last_action_ts=None):
        player.in_game = True
        player.last_action_ts = last_action_ts or timezone.now()
        player.save(update_fields=["in_game", "last_action_ts"])

    def _assign_core_faction(self, player, code):
        faction = Faction.objects.create(
            world=self.world,
            code=code,
            name=code.title(),
            is_core=True,
        )
        player.faction_assignments.create(faction=faction)
        return faction

    def test_who_text_command_returns_wr1_payload_and_text(self):
        self.player.title = "the Tester"
        self.player.level = 3
        self.player.save(update_fields=["title", "level"])
        self._set_online(self.player)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "who")

        message = self._message_by_type(messages, "cmd.who.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["grapevine"], {})
        self.assertEqual(len(message["data"]["players"]), 1)
        self.assertEqual(message["data"]["players"][0]["key"], self.player.key)
        self.assertEqual(message["data"]["players"][0]["title"], "the Tester")
        self.assertIn("Players online:", message["text"])
        self.assertIn("Joe the Tester (3)", message["text"])

    def test_who_filters_invisible_offline_and_cross_faction_players(self):
        self._assign_core_faction(self.player, "human")
        self._set_online(self.player)

        human = self.create_player("Human")
        self._assign_core_faction(human, "human")
        self._set_online(human)

        orc = self.create_player("Orc")
        self._assign_core_faction(orc, "orc")
        self._set_online(orc)

        invisible = self.create_player("Invisible")
        self._assign_core_faction(invisible, "human")
        invisible.is_invisible = True
        invisible.save(update_fields=["is_invisible"])
        self._set_online(invisible)

        offline = self.create_player("Offline")
        self._assign_core_faction(offline, "human")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "who")

        message = self._message_by_type(messages, "cmd.who.success")
        self.assertIsNotNone(message)
        player_names = [player["name"] for player in message["data"]["players"]]
        self.assertEqual(player_names, ["Joe", "Human"])

    def test_builder_who_sees_invisible_players_and_room_ids(self):
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])
        self._set_online(self.player)

        invisible = self.create_player("Invisible")
        invisible.is_invisible = True
        invisible.save(update_fields=["is_invisible"])
        self._set_online(invisible)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "who")

        message = self._message_by_type(messages, "cmd.who.success")
        self.assertIsNotNone(message)
        players_by_name = {
            player["name"]: player
            for player in message["data"]["players"]
        }
        self.assertIn("Invisible", players_by_name)
        self.assertTrue(players_by_name["Invisible"]["is_invisible"])
        self.assertEqual(players_by_name["Invisible"]["room_id"], invisible.room_id)
        self.assertTrue(players_by_name["Joe"]["is_immortal"])
        self.assertIn("~ Joe", message["text"])
        self.assertIn("Invisible  (1) [invisible]", message["text"])


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

    def test_state_sync_map_room_keeps_exit_to_unvisited_room(self):
        west_room = self.room.create_at("west")
        unvisited_room = west_room.create_at("west")
        unvisited_room.relative_id = unvisited_room.id + 7000
        unvisited_room.save(update_fields=["relative_id"])
        self.player.viewed_rooms.add(west_room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        map_by_key = {room["key"]: room for room in message["data"]["map"]}
        west_room_key = f"room.{west_room.relative_id or west_room.id}"
        unvisited_room_key = f"room.{unvisited_room.relative_id}"
        self.assertIn(west_room_key, map_by_key)
        self.assertNotIn(unvisited_room_key, map_by_key)
        self.assertEqual(map_by_key[west_room_key]["west"], unvisited_room_key)

    def test_map_payload_bulk_resolves_unvisited_exit_keys(self):
        exit_rooms = {
            direction: self.room.create_at(direction)
            for direction in ("north", "east", "south", "west")
        }

        with self.assertNumQueries(2):
            map_rooms, room_key_lookup = build_map_payload(
                self.world,
                [self.room.id],
                {},
            )

        self.assertEqual(len(map_rooms), 1)
        payload = map_rooms[0].model_dump()
        for direction, exit_room in exit_rooms.items():
            exit_key = f"room.{exit_room.relative_id or exit_room.id}"
            self.assertEqual(payload[direction], exit_key)
            self.assertEqual(room_key_lookup[exit_room.id], exit_key)

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

    def test_state_sync_world_pvp_fields_derive_legacy_boolean_from_mode(self):
        from spawns.state_payloads import build_state_sync

        config = self.spawn_world.config
        expectations = (
            (adv_consts.PVP_MODE_DISABLED, False),
            (adv_consts.PVP_MODE_ZONE, True),
            (adv_consts.PVP_MODE_FFA, True),
        )

        for pvp_mode, allow_pvp in expectations:
            with self.subTest(pvp_mode=pvp_mode):
                config.pvp_mode = pvp_mode
                config.save(update_fields=["pvp_mode"])

                world_data = build_state_sync(self.player).model_dump()["world"]

                self.assertEqual(world_data["pvp_mode"], pvp_mode)
                self.assertIs(world_data["allow_pvp"], allow_pvp)


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
