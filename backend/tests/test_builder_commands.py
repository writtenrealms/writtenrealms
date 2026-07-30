import json
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import (
    AbilityDefinition,
    CraftMaterial,
    Currency,
    ItemDefinition,
    ItemSalvageYield,
    MobDefinition,
    SpawnEntry,
    SpawnPlan,
    Trigger,
)
from config import constants as api_consts
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    get_state_snapshot,
)
# Register handlers before importing builder Actions; the handler package
# imports this Action module during registration.
from spawns.handlers import dispatch_command, get_registered_handlers
from spawns.actions.builder import GrantItemAction, SendExceptAction
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    DoorState,
    GameEventOutbox,
    Item,
    Mob,
    PlayerCurrencyBalance,
)
from spawns.loading import run_spawn_plans_for_world
from spawns.wallet import balance_map
from tests.base import WorldTestCase
from worlds.models import Door, Doorway, Room, World, WorldConfig, Zone
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    create_active_effect,
    dispatch_text_command,
    dispatch_text_command_as_mob,
)


def create_definition_item(world, spawn_world, container, name, **item_fields):
    definition = ItemDefinition.objects.create(
        world=world,
        name=name,
        keywords=item_fields.pop("keywords", ""),
    )
    return Item.objects.create(
        world=spawn_world,
        container=container,
        definition=definition,
        definition_slug_snapshot=definition.slug,
        name=definition.name,
        keywords=definition.keywords,
        **item_fields,
    )


class TestBuilderCommandPermissions(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _builder_command_handlers(self):
        return {
            command_type: handler
            for command_type, handler in get_registered_handlers().items()
            if getattr(handler, "builder_only", False)
        }

    def test_text_builder_commands_require_builder_character(self):
        # self.player belongs to the world author, but is not the builder character.
        self.assertFalse(self.player.is_builder)

        for command_type, handler in self._builder_command_handlers().items():
            text_commands = getattr(handler, "text_commands", ()) or ()
            if not text_commands:
                continue

            with self.subTest(command=command_type):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, text_commands[0])

                message = self._message_by_type(messages, f"cmd.{command_type}.error")
                self.assertIsNotNone(message)
                self.assertIn("permission", message.get("text", "").lower())

    def test_structured_builder_commands_require_builder_character(self):
        self.assertFalse(self.player.is_builder)

        for command_type in self._builder_command_handlers():
            with self.subTest(command=command_type):
                with capture_game_messages() as messages:
                    dispatch_command(
                        command_type=command_type,
                        player_id=self.player.id,
                        payload={},
                    )

                message = self._message_by_type(messages, f"cmd.{command_type}.error")
                self.assertIsNotNone(message)
                self.assertIn("permission", message.get("text", "").lower())

    def test_payload_cannot_spoof_script_source(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "/echo -- spoofed",
                    "__trigger_source": True,
                },
            )

        self.assertIsNone(self._message_by_type(messages, "cmd./echo.success"))
        message = self._message_by_type(messages, "cmd./echo.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_script_source_allows_echo_but_not_level_changes(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/echo -- scripted"},
                script_source=True,
            )

        self.assertIsNotNone(self._message_by_type(messages, "cmd./echo.success"))

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/setlevel 20"},
                script_source=True,
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.level, 1)
        message = self._message_by_type(messages, "cmd./setlevel.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_script_source_requires_room_issuer_for_set(self):
        original_glory = self.player.glory

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/set self glory 7"},
                script_source=True,
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.glory, original_glory)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_script_source_does_not_allow_player_state_commands(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/state set character self pull_lever true"},
                script_source=True,
            )

        self.assertNotIn(
            "pull_lever",
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player),
        )
        message = self._message_by_type(messages, "cmd./state.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_script_source_does_not_allow_player_kill_commands(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": f"/kill {self.player.key}"},
                script_source=True,
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        message = self._message_by_type(messages, "cmd./kill.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_script_source_does_not_allow_currency_assignments(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/setcurrency obol 10"},
                script_source=True,
            )

        message = self._message_by_type(
            messages,
            "cmd./setcurrency.error",
        )
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())
        self.assertFalse(
            PlayerCurrencyBalance.objects.filter(player=self.player).exists()
        )

    def test_script_source_does_not_allow_player_repop_commands(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/repop"},
                script_source=True,
            )

        message = self._message_by_type(messages, "cmd./repop.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())


class BuilderCommandTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])


class TestBuilderInvisible(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_invisible_toggles_builder_visibility(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/invisible")

        self.player.refresh_from_db()
        self.assertTrue(self.player.is_invisible)
        message = self._message_by_type(messages, "cmd./invisible.success")
        self.assertIsNotNone(message)
        self.assertTrue(message["data"]["is_invisible"])
        self.assertEqual(message["text"], "You are now invisible.")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/inv")

        self.player.refresh_from_db()
        self.assertFalse(self.player.is_invisible)
        message = self._message_by_type(messages, "cmd./invisible.success")
        self.assertIsNotNone(message)
        self.assertFalse(message["data"]["is_invisible"])
        self.assertEqual(message["text"], "You are now visible.")

    def test_invisible_builder_is_omitted_from_who_list(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher_user = self.create_user("who-watcher@example.com")
        watcher = self.create_player("Watcher", user=watcher_user)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages():
            dispatch_text_command(self.player.id, "/invisible")

        with capture_game_messages() as messages:
            dispatch_text_command(watcher.id, "who")

        message = self._message_by_type(messages, "cmd.who.success")
        self.assertIsNotNone(message)
        player_keys = {entry["key"] for entry in message["data"]["players"]}
        self.assertIn(watcher.key, player_keys)
        self.assertNotIn(self.player.key, player_keys)


class TestBuilderLoad(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="test-item",
            name="Test Item",
        )
        self.mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="test-mob",
            name="Test Mob",
        )

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_load_item_adds_inventory(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/lo item {self.item_definition.id}")

        loaded_item = self.player.inventory.get(definition=self.item_definition)
        self.assertTrue(
            self.player.inventory.filter(
                definition=self.item_definition,
            ).exists()
        )
        self.assertEqual(loaded_item.name, self.item_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.item_definition.name,
        )
        self.assertTrue(message.get("text"))

    def test_load_mob_adds_room_mob(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/lo mob {self.mob_definition.id}")

        loaded_mob = Mob.objects.get(
            definition=self.mob_definition,
            room=self.room,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_mob.name, self.mob_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.mob_definition.name,
        )
        self.assertTrue(message.get("text"))

    def test_load_accepts_unambiguous_definition_type_prefix(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load i {self.item_definition.id}")

        loaded_item = self.player.inventory.get(definition=self.item_definition)
        self.assertEqual(loaded_item.name, self.item_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("data", {}).get("loaded", {}).get("name"), self.item_definition.name)

    def test_load_item_accepts_definition_slug(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load item {self.item_definition.slug}")

        loaded_item = self.player.inventory.get(definition=self.item_definition)
        self.assertEqual(loaded_item.name, self.item_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("data", {}).get("loaded", {}).get("name"), self.item_definition.name)

    def test_room_actor_load_item_adds_room_inventory(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="room-definition-sword",
            name="a room definition sword",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/load item room-definition-sword",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        loaded_item = self.room.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        self.assertFalse(self.player.inventory.filter(definition=item_definition).exists())
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "room")
        self.assertEqual(message["data"]["loaded"]["name"], item_definition.name)

    def test_cmd_room_load_item_adds_room_inventory(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="cmd-room-definition-sword",
            name="a command room definition sword",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/cmd room -- /load item cmd-room-definition-sword"},
                script_source=True,
            )

        loaded_item = self.room.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        self.assertFalse(self.player.inventory.filter(definition=item_definition).exists())
        cmd_message = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["target"]["type"], "scope")
        self.assertEqual(cmd_message["data"]["target"]["scope"], "room")
        self.assertEqual(cmd_message["data"]["errors"], [])

    def test_scripted_mob_load_item_adds_mob_inventory(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="mob-definition-sword",
            name="a mob definition sword",
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=mob.id,
                payload={"text": "/load item mob-definition-sword"},
                script_source=True,
            )

        loaded_item = mob.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        self.assertFalse(self.player.inventory.filter(definition=item_definition).exists())
        self.assertFalse(self.room.inventory.filter(definition=item_definition).exists())
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "mob")
        self.assertEqual(message["data"]["loaded"]["name"], item_definition.name)

    def test_mob_load_requires_script_source(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="mob-restricted-definition-sword",
            name="a restricted mob definition sword",
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=mob.id,
                payload={"text": "/load item mob-restricted-definition-sword"},
            )

        self.assertFalse(mob.inventory.filter(definition=item_definition).exists())
        message = self._message_by_type(messages, "cmd./load.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_load_mob_accepts_definition_slug(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load mob {self.mob_definition.slug}")

        loaded_mob = Mob.objects.get(
            definition=self.mob_definition,
            room=self.room,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_mob.name, self.mob_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.mob_definition.name,
        )


class TestBuilderGrantItem(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _message_for_key_and_type(self, messages, player_key, message_type):
        for msg in messages:
            if msg["player_key"] == player_key and msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _item_definition(self, slug="grant-definition-sword", name="a grant definition sword"):
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
        )

    def test_builder_grantitem_adds_player_inventory_and_notifies_target(self):
        item_definition = self._item_definition()
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/grantitem {target.key} grant-definition-sword")

        loaded_item = target.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        self.assertFalse(self.player.inventory.filter(definition=item_definition).exists())

        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertEqual(message["data"]["loaded"]["name"], item_definition.name)

        notification = self._message_for_key_and_type(messages, target.key, "notification./grantitem")
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["actor"]["key"], target.key)
        self.assertIn(
            loaded_item.key,
            [item["key"] for item in notification["data"]["actor"]["inventory"]],
        )

    def test_builder_grantitem_adds_mob_inventory(self):
        item_definition = self._item_definition(
            slug="grant-mob-definition-sword",
            name="a grant mob definition sword",
        )
        target_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/grantitem quartermaster grant-mob-definition-sword")

        loaded_item = target_mob.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target_mob.key)
        self.assertEqual(message["data"]["target_type"], "mob")

    def test_room_actor_grantitem_adds_player_inventory(self):
        item_definition = self._item_definition(
            slug="room-grant-definition-sword",
            name="a room grant definition sword",
        )
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/grantitem {target.key} room-grant-definition-sword",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        loaded_item = target.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "room")
        self.assertEqual(message["data"]["target"]["key"], target.key)

        notification = self._message_for_key_and_type(messages, target.key, "notification./grantitem")
        self.assertIsNotNone(notification)
        self.assertIn(
            loaded_item.key,
            [item["key"] for item in notification["data"]["actor"]["inventory"]],
        )

    def test_cmd_room_grantitem_adds_player_inventory(self):
        item_definition = self._item_definition(
            slug="cmd-room-grant-definition-sword",
            name="a command room grant definition sword",
        )
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": f"/cmd room -- /grantitem {target.key} cmd-room-grant-definition-sword"},
                script_source=True,
            )

        loaded_item = target.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        cmd_message = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["errors"], [])
        self.assertEqual(cmd_message["data"]["target"]["scope"], "room")

    def test_builder_grantitem_adds_multiple_player_items(self):
        sword = self._item_definition(
            slug="batch-grant-sword",
            name="a batch grant sword",
        )
        helm = self._item_definition(
            slug="batch-grant-helm",
            name="a batch grant helm",
        )
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/grantitem {target.key} -- batch-grant-sword batch-grant-helm",
            )

        loaded_sword = target.inventory.get(definition=sword, world=self.spawn_world)
        loaded_helm = target.inventory.get(definition=helm, world=self.spawn_world)
        self.assertEqual(loaded_sword.name, sword.name)
        self.assertEqual(loaded_helm.name, helm.name)

        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertEqual(message["data"]["loaded"]["type"], "items")
        self.assertEqual(message["data"]["loaded_count"], 2)
        self.assertEqual(
            [item["name"] for item in message["data"]["loaded_items"]],
            [sword.name, helm.name],
        )
        self.assertEqual(message["text"], "Granted 2 items to Target.")

        notification = self._message_for_key_and_type(messages, target.key, "notification./grantitem")
        self.assertIsNotNone(notification)
        self.assertEqual(notification["text"], "You receive 2 items.")
        self.assertEqual(notification["data"]["loaded_count"], 2)
        self.assertIn(
            loaded_sword.key,
            [item["key"] for item in notification["data"]["actor"]["inventory"]],
        )
        self.assertIn(
            loaded_helm.key,
            [item["key"] for item in notification["data"]["actor"]["inventory"]],
        )

    def test_builder_grantitem_salvageability_queries_are_bounded(self):
        definitions = [
            self._item_definition(
                slug=f"query-grant-{index}",
                name=f"a query grant item {index}",
            )
            for index in range(12)
        ]
        material = CraftMaterial.objects.create(
            world=self.world,
            slug="query-grant-material",
            name="Query Grant Material",
        )
        ItemSalvageYield.objects.create(
            item_definition=definitions[0],
            material=material,
            quantity=1,
        )
        target = self.create_player("Target", room=self.room)

        with CaptureQueriesContext(connection) as queries:
            result = GrantItemAction().execute_many(
                actor=self.player,
                target_selector=target.key,
                item_ids=[definition.slug for definition in definitions],
            )

        loaded_items = result.events[0].data["loaded_items"]
        self.assertEqual(len(loaded_items), len(definitions))
        self.assertEqual(
            [
                loaded_item["item"]["is_salvageable"]
                for loaded_item in loaded_items
            ],
            [True] + [False] * (len(definitions) - 1),
        )
        salvage_table = ItemSalvageYield._meta.db_table.lower()
        salvage_queries = [
            query["sql"]
            for query in queries
            if salvage_table in query["sql"].lower()
        ]
        self.assertLessEqual(
            len(salvage_queries),
            3,
            "Bulk grants must not query salvage yields once per item.",
        )

    def test_builder_grantitem_batch_supports_multi_word_target(self):
        sword = self._item_definition(
            slug="batch-name-sword",
            name="a batch name sword",
        )
        helm = self._item_definition(
            slug="batch-name-helm",
            name="a batch name helm",
        )
        target = self.create_player("Target Friend", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                "/grantitem Target Friend -- batch-name-sword batch-name-helm",
            )

        self.assertTrue(target.inventory.filter(definition=sword, world=self.spawn_world).exists())
        self.assertTrue(target.inventory.filter(definition=helm, world=self.spawn_world).exists())
        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)

    def test_builder_grantitem_batch_rolls_back_when_any_item_is_invalid(self):
        sword = self._item_definition(
            slug="batch-rollback-sword",
            name="a batch rollback sword",
        )
        helm = self._item_definition(
            slug="batch-rollback-helm",
            name="a batch rollback helm",
        )
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/grantitem {target.key} -- batch-rollback-sword missing-batch-item batch-rollback-helm",
            )

        self.assertFalse(target.inventory.filter(definition=sword, world=self.spawn_world).exists())
        self.assertFalse(target.inventory.filter(definition=helm, world=self.spawn_world).exists())
        message = self._message_by_type(messages, "cmd./grantitem.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["text"], "Item definition does not belong to this world")
        self.assertEqual(message["data"]["item"], "missing-batch-item")

    def test_cmd_room_grantitem_adds_multiple_player_items(self):
        sword = self._item_definition(
            slug="cmd-room-batch-grant-sword",
            name="a command room batch grant sword",
        )
        helm = self._item_definition(
            slug="cmd-room-batch-grant-helm",
            name="a command room batch grant helm",
        )
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": f"/cmd room -- /grantitem {target.key} -- cmd-room-batch-grant-sword cmd-room-batch-grant-helm"
                },
                script_source=True,
            )

        self.assertTrue(target.inventory.filter(definition=sword, world=self.spawn_world).exists())
        self.assertTrue(target.inventory.filter(definition=helm, world=self.spawn_world).exists())
        cmd_message = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["errors"], [])
        self.assertEqual(cmd_message["data"]["target"]["scope"], "room")

    def test_mob_actor_grantitem_adds_player_inventory(self):
        item_definition = self._item_definition(
            slug="mob-grant-definition-sword",
            name="a mob grant definition sword",
        )
        target = self.create_player("Target", room=self.room)
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=issuer_mob.id,
                payload={"text": f"/grantitem {target.key} mob-grant-definition-sword"},
                script_source=True,
            )

        loaded_item = target.inventory.get(
            definition=item_definition,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_item.name, item_definition.name)
        message = self._message_by_type(messages, "cmd./grantitem.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "mob")
        self.assertEqual(message["data"]["target"]["key"], target.key)

    def test_mob_grantitem_requires_script_source(self):
        item_definition = self._item_definition(
            slug="mob-restricted-grant-definition-sword",
            name="a restricted mob grant definition sword",
        )
        target = self.create_player("Target", room=self.room)
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=issuer_mob.id,
                payload={"text": f"/grantitem {target.key} mob-restricted-grant-definition-sword"},
            )

        self.assertFalse(target.inventory.filter(definition=item_definition).exists())
        message = self._message_by_type(messages, "cmd./grantitem.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())


class TestBuilderSetCurrency(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.obol = Currency.objects.create(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.drachma = Currency.objects.create(
            world=self.world,
            code="drachma",
            name="Drachma",
            plural_name="Drachmas",
        )

    @staticmethod
    def _message_by_type(messages, message_type):
        for entry in messages:
            if entry["message"].get("type") == message_type:
                return entry["message"]
        return None

    @staticmethod
    def _message_for_key_and_type(messages, player_key, message_type):
        for entry in messages:
            if (
                entry["player_key"] == player_key
                and entry["message"].get("type") == message_type
            ):
                return entry["message"]
        return None

    def test_builder_sets_own_exact_currency_balance(self):
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setcurrency obol 25")

        self.player.refresh_from_db()
        self.assertEqual(balance_map(self.player)["obol"], 25)
        self.assertEqual(self.player.wallet_revision, revision_before + 1)

        message = self._message_by_type(messages, "cmd./setcurrency.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], self.player.key)
        self.assertEqual(message["data"]["currency"]["code"], "obol")
        self.assertEqual(message["data"]["before"], 0)
        self.assertEqual(message["data"]["after"], 25)
        self.assertEqual(message["data"]["delta"], 25)
        self.assertTrue(message["data"]["changed"])
        self.assertEqual(
            message["text"],
            "Set Joe's Obol balance to 25 Obols.",
        )

        wallet_event = GameEventOutbox.objects.get(
            event_type="currency.balances_changed",
        )
        self.assertEqual(wallet_event.recipients, [self.player.key])
        self.assertEqual(wallet_event.data["reason"], "builder.set_currency")
        self.assertEqual(wallet_event.data["changes"][0]["after"], 25)

    def test_builder_sets_keyed_player_outside_room_and_preserves_other_balances(self):
        far_room = self.room.create_at("north")
        target = self.create_player("Target Friend", room=far_room)
        PlayerCurrencyBalance.objects.bulk_create(
            [
                PlayerCurrencyBalance(
                    player=target,
                    currency=self.obol,
                    amount=40,
                ),
                PlayerCurrencyBalance(
                    player=target,
                    currency=self.drachma,
                    amount=3,
                ),
            ]
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/setcurrency {target.key} obol 7",
            )

        target.refresh_from_db()
        self.assertEqual(
            balance_map(target),
            {"obol": 7, "drachma": 3},
        )
        message = self._message_by_type(messages, "cmd./setcurrency.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertEqual(message["data"]["before"], 40)
        self.assertEqual(message["data"]["after"], 7)
        self.assertEqual(message["data"]["delta"], -33)
        notification = self._message_for_key_and_type(
            messages,
            target.key,
            "notification./setcurrency",
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["currency"]["code"], "obol")
        self.assertEqual(notification["data"]["after"], 7)
        self.assertEqual(
            notification["text"],
            "Your Obol balance was set to 7 Obols.",
        )
        self.assertEqual(
            GameEventOutbox.objects.get(
                event_type="currency.balances_changed",
            ).recipients,
            [target.key],
        )

    def test_builder_can_use_multiword_local_player_name(self):
        target = self.create_player("Target Friend", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                "/setcurrency Target Friend drachma 11",
            )

        self.assertEqual(balance_map(target)["drachma"], 11)
        message = self._message_by_type(messages, "cmd./setcurrency.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)

    def test_structured_setcurrency_defaults_to_self(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="/setcurrency",
                player_id=self.player.id,
                payload={"currency": "obol", "amount": 8},
            )

        self.assertEqual(balance_map(self.player)["obol"], 8)
        self.assertIsNotNone(
            self._message_by_type(messages, "cmd./setcurrency.success")
        )

    def test_setting_same_amount_is_a_noop(self):
        PlayerCurrencyBalance.objects.create(
            player=self.player,
            currency=self.obol,
            amount=9,
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setcurrency obol 9")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertFalse(GameEventOutbox.objects.exists())
        message = self._message_by_type(messages, "cmd./setcurrency.success")
        self.assertIsNotNone(message)
        self.assertFalse(message["data"]["changed"])
        self.assertEqual(message["data"]["delta"], 0)

    def test_invalid_amounts_and_unknown_currency_do_not_mutate_wallet(self):
        invalid_commands = [
            ("/setcurrency obol -1", "invalid_amount"),
            ("/setcurrency obol 1.5", "invalid_amount"),
            ("/setcurrency obol 9007199254740992", "invalid_amount"),
            ("/setcurrency missing 1", "invalid_currency"),
        ]

        for command, error_code in invalid_commands:
            with self.subTest(command=command):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, command)

                message = self._message_by_type(
                    messages,
                    "cmd./setcurrency.error",
                )
                self.assertIsNotNone(message)
                self.assertEqual(message["data"]["code"], error_code)
                self.assertEqual(balance_map(self.player)["obol"], 0)
                self.assertFalse(GameEventOutbox.objects.exists())

    def test_mob_target_is_rejected(self):
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Treasurer",
            keywords="treasurer",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/setcurrency {target.key} obol 10",
            )

        message = self._message_by_type(messages, "cmd./setcurrency.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "invalid_target")
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_player_in_parallel_runtime_is_rejected(self):
        parallel_world = self.world.create_spawn_world()
        target = self.create_player(
            "Parallel Target",
            world=parallel_world,
            room=parallel_world.effective_config.starting_room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/setcurrency {target.key} obol 10",
            )

        message = self._message_by_type(messages, "cmd./setcurrency.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "invalid_target")
        self.assertFalse(
            PlayerCurrencyBalance.objects.filter(player=target).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_command_registers_help(self):
        help_data = get_registered_handlers()["/setcurrency"].get_help_data(
            command_name="/setcurrency",
        )

        self.assertIn("<currency_code>", help_data["format"])
        self.assertIn("exact balance", help_data["description"])


class TestBuilderSend(BuilderCommandTestCase):
    def _message_for_key_and_type(self, messages, player_key, message_type):
        for msg in messages:
            if msg["player_key"] == player_key and msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _set_online(self, *players):
        for player in players:
            player.in_game = True
            player.save(update_fields=["in_game"])

    def test_builder_send_private_message_to_online_player(self):
        far_room = self.room.create_at("north")
        target = self.create_player("Aria", room=far_room)
        watcher = self.create_player("Watcher", room=far_room)
        self._set_online(target, watcher)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/send ari You feel watched.")

        success = self._message_for_key_and_type(messages, self.player.key, "cmd./send.success")
        self.assertIsNotNone(success)
        self.assertEqual(success["data"]["target"]["key"], target.key)
        self.assertEqual(success["text"], "You feel watched.")

        notification = self._message_for_key_and_type(messages, target.key, "notification./send")
        self.assertIsNotNone(notification)
        self.assertEqual(notification["text"], "You feel watched.")
        self.assertEqual(notification["data"]["actor"]["key"], self.player.key)

        watcher_notification = self._message_for_key_and_type(messages, watcher.key, "notification./send")
        self.assertIsNone(watcher_notification)

    def test_cmd_room_send_private_message_to_triggering_player(self):
        target = self.create_player("TriggerTarget", room=self.room)
        self._set_online(target)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=target.id,
                payload={
                    "text": f"/cmd room -- /send {target.key} -- The altar answers you alone."
                },
                script_source=True,
            )

        notification = self._message_for_key_and_type(messages, target.key, "notification./send")
        self.assertIsNotNone(notification)
        self.assertEqual(notification["text"], "The altar answers you alone.")
        self.assertEqual(notification["data"]["actor"]["char_type"], "room")

        cmd_message = self._message_for_key_and_type(messages, target.key, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["errors"], [])

    def test_mob_actor_send_private_message(self):
        target = self.create_player("Target", room=self.room)
        self._set_online(target)
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Oracle",
            keywords="oracle",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=issuer_mob.id,
                payload={"text": f"/send {target.key} -- The oracle sees you."},
                script_source=True,
            )

        notification = self._message_for_key_and_type(messages, target.key, "notification./send")
        self.assertIsNotNone(notification)
        self.assertEqual(notification["text"], "The oracle sees you.")
        self.assertEqual(notification["data"]["actor"]["char_type"], "mob")

    def test_player_script_source_cannot_send_without_ambient_actor(self):
        other_user = self.create_user("other-send@example.com")
        trigger_actor = self.create_player("Triggerer", user=other_user)
        target = self.create_player("Target", room=self.room)
        self._set_online(trigger_actor, target)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=trigger_actor.id,
                payload={"text": f"/send {target.key} This should not send."},
                script_source=True,
            )

        notification = self._message_for_key_and_type(messages, target.key, "notification./send")
        self.assertIsNone(notification)
        error = self._message_for_key_and_type(messages, trigger_actor.key, "cmd./send.error")
        self.assertIsNotNone(error)
        self.assertIn("permission", error.get("text", "").lower())

    def test_send_requires_online_player(self):
        target = self.create_player("OfflineTarget", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/send {target.key} Are you there?")

        error = self._message_for_key_and_type(messages, self.player.key, "cmd./send.error")
        self.assertIsNotNone(error)
        self.assertIn("recipient not found", error.get("text", "").lower())

    def test_builder_sendexcept_uses_target_room_and_excludes_target(self):
        far_room = self.room.create_at("north")
        target = self.create_player("Aria", room=far_room)
        watcher = self.create_player("Watcher", room=far_room)
        issuer_room_watcher = self.create_player("Near Watcher", room=self.room)
        offline_watcher = self.create_player("Sleeping Watcher", room=far_room)
        self._set_online(target, watcher, issuer_room_watcher)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                "/sendexcept ari -- Aria studies the inscription.",
            )

        success = self._message_for_key_and_type(
            messages,
            self.player.key,
            "cmd./sendexcept.success",
        )
        self.assertIsNotNone(success)
        self.assertEqual(success["data"]["target"]["key"], target.key)
        self.assertEqual(success["text"], "Aria studies the inscription.")

        watcher_notification = self._message_for_key_and_type(
            messages,
            watcher.key,
            "notification./sendexcept",
        )
        self.assertIsNotNone(watcher_notification)
        self.assertEqual(
            watcher_notification["text"],
            "Aria studies the inscription.",
        )
        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                target.key,
                "notification./sendexcept",
            )
        )
        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                issuer_room_watcher.key,
                "notification./sendexcept",
            )
        )
        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                offline_watcher.key,
                "notification./sendexcept",
            )
        )

    def test_sendexcept_supports_every_ambient_send_actor_type(self):
        target = self.create_player("Target", room=self.room)
        watcher = self.create_player("Watcher", room=self.room)
        self._set_online(target, watcher)
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Oracle",
            keywords="oracle",
        )
        actors = (
            ("mob", issuer_mob.id),
            ("room", self.room.id),
            ("zone", self.zone.id),
            ("world", self.spawn_world.id),
        )

        for actor_type, actor_id in actors:
            with self.subTest(actor_type=actor_type):
                with capture_game_messages() as messages:
                    dispatch_command(
                        command_type="text",
                        actor_type=actor_type,
                        actor_id=actor_id,
                        payload={
                            "text": (
                                f"/sendexcept {target.key} -- "
                                f"{actor_type} message"
                            ),
                            "runtime_world_id": self.spawn_world.id,
                        },
                        script_source=True,
                    )

                notification = self._message_for_key_and_type(
                    messages,
                    watcher.key,
                    "notification./sendexcept",
                )
                self.assertIsNotNone(notification)
                self.assertEqual(
                    notification["text"],
                    f"{actor_type} message",
                )
                self.assertIsNone(
                    self._message_for_key_and_type(
                        messages,
                        target.key,
                        "notification./sendexcept",
                    )
                )

    def test_player_script_source_cannot_sendexcept_without_ambient_actor(self):
        trigger_actor = self.create_player(
            "Triggerer",
            user=self.create_user("sendexcept-triggerer@example.com"),
        )
        target = self.create_player("Target", room=self.room)
        self._set_online(trigger_actor, target)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=trigger_actor.id,
                payload={
                    "text": (
                        f"/sendexcept {target.key} -- "
                        "This should not send."
                    )
                },
                script_source=True,
            )

        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                target.key,
                "notification./sendexcept",
            )
        )
        error = self._message_for_key_and_type(
            messages,
            trigger_actor.key,
            "cmd./sendexcept.error",
        )
        self.assertIsNotNone(error)
        self.assertIn("permission", error.get("text", "").lower())

    def test_sendexcept_isolated_to_target_runtime_world(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        self._set_online(watcher)
        other_runtime = self.world.create_spawn_world(
            instance_ref="sendexcept-other",
        )
        other_watcher = self.create_player(
            "Other Watcher",
            world=other_runtime,
            room=self.room,
        )
        self._set_online(other_watcher)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                "/sendexcept self -- Joe turns the dial.",
            )

        success = self._message_for_key_and_type(
            messages,
            self.player.key,
            "cmd./sendexcept.success",
        )
        self.assertIsNotNone(success)
        self.assertEqual(success["text"], "Message sent.")
        self.assertNotEqual(success["text"], "Joe turns the dial.")
        self.assertIsNotNone(
            self._message_for_key_and_type(
                messages,
                watcher.key,
                "notification./sendexcept",
            )
        )
        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                self.player.key,
                "notification./sendexcept",
            )
        )
        self.assertIsNone(
            self._message_for_key_and_type(
                messages,
                other_watcher.key,
                "notification./sendexcept",
            )
        )

    def test_sendexcept_rejects_roomless_online_target(self):
        target = self.create_player("Nowhere", room=self.room)
        target.in_game = True
        target.room = None
        target.save(update_fields=["in_game", "room"])

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/sendexcept {target.key} -- A distant bell rings.",
            )

        error = self._message_for_key_and_type(
            messages,
            self.player.key,
            "cmd./sendexcept.error",
        )
        self.assertIsNotNone(error)
        self.assertEqual(error["data"]["code"], "no_room")

    def test_sendexcept_registers_help(self):
        help_data = get_registered_handlers()["/sendexcept"].get_help_data(
            command_name="/sendexcept",
        )

        self.assertEqual(
            help_data["format"],
            "/sendexcept <player> <message>",
        )
        self.assertIn("every other connected player", help_data["description"])

    def test_sendexcept_recipient_queries_do_not_scale_with_room_population(self):
        target = self.create_player("Target", room=self.room)
        self._set_online(target)
        action = SendExceptAction()

        with CaptureQueriesContext(connection) as empty_room_queries:
            action.execute(
                actor=self.player,
                target_selector=target.key,
                message="Target turns the dial.",
                runtime_world=self.spawn_world,
            )

        observers = [
            self.create_player(f"Observer {index}", room=self.room)
            for index in range(20)
        ]
        self._set_online(*observers)
        with CaptureQueriesContext(connection) as populated_room_queries:
            result = action.execute(
                actor=self.player,
                target_selector=target.key,
                message="Target turns the dial.",
                runtime_world=self.spawn_world,
            )

        self.assertEqual(
            len(populated_room_queries),
            len(empty_room_queries),
        )
        notification = next(
            event
            for event in result.events
            if event.type == "notification./sendexcept"
        )
        self.assertEqual(len(notification.recipients), len(observers))


class TestBuilderWizKill(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.death_room = self.room.create_at("east")
        self.world.config.death_room = self.death_room
        self.world.config.save(update_fields=["death_room"])
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def _message_by_type(self, messages, message_type, player_key=None):
        for msg in messages:
            if player_key and msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_builder_kill_moves_player_to_death_room_with_message(self):
        target = self.create_player("Target", room=self.room)
        target.in_game = True
        target.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/kill {target.key} -- The pit swallows you whole.")

        target.refresh_from_db()
        self.assertEqual(target.room_id, self.death_room.id)
        self.assertEqual(
            (target.health, target.energy, target.stamina),
            (1, 1, 1),
        )

        success = self._message_by_type(messages, "cmd./kill.success", self.player.key)
        self.assertIsNotNone(success)
        self.assertEqual(success["data"]["target"]["key"], target.key)

        affect = self._message_by_type(messages, "affect.death", target.key)
        self.assertIsNotNone(affect)
        self.assertEqual(affect["text"], "The pit swallows you whole.")
        self.assertEqual(
            (
                affect["data"]["actor"]["health"],
                affect["data"]["actor"]["energy"],
                affect["data"]["actor"]["stamina"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(affect["data"]["room"]["id"], self.death_room.id)
        self.assertEqual(affect["data"]["origin_room"]["id"], self.room.id)

        watcher_death = self._message_by_type(messages, "notification.death", watcher.key)
        self.assertIsNotNone(watcher_death)
        self.assertEqual(watcher_death["text"], "Joe snaps Target out of existence.")
        self.assertEqual(watcher_death["data"]["deceased"]["key"], target.key)

    def test_cmd_room_kill_moves_triggering_player_to_death_room(self):
        target = self.create_player("TriggerTarget", room=self.room)
        target.in_game = True
        target.save(update_fields=["in_game"])
        Trigger.objects.create(
            world=self.world,
            scope=api_consts.TRIGGER_SCOPE_ROOM,
            kind=api_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(self.death_room.__class__),
            target_id=self.death_room.id,
            event=api_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
            script="/cmd room -- /echo -- Death room trigger fired.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=target.id,
                payload={
                    "text": f"/cmd room -- /kill {target.key} -- The floor vanishes beneath you."
                },
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, self.death_room.id)
        affect = self._message_by_type(messages, "affect.death", target.key)
        self.assertIsNotNone(affect)
        self.assertEqual(affect["text"], "The floor vanishes beneath you.")
        cmd_message = self._message_by_type(messages, "cmd./cmd.success", target.key)
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["errors"], [])
        death_room_echo = self._message_by_type(messages, "cmd./echo.success")
        self.assertIsNotNone(death_room_echo, [msg["message"] for msg in messages])
        self.assertIn("Death room trigger fired", death_room_echo["text"])

    def test_mob_actor_kill_moves_player_to_death_room(self):
        target = self.create_player("Victim", room=self.room)
        target.in_game = True
        target.save(update_fields=["in_game"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Stone Sentinel",
            keywords="sentinel",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=mob.id,
                payload={"text": f"/kill {target.key} -- The sentinel crushes you."},
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, self.death_room.id)
        affect = self._message_by_type(messages, "affect.death", target.key)
        self.assertIsNotNone(affect)
        self.assertEqual(affect["text"], "The sentinel crushes you.")

    def test_kill_rejects_mob_targets(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Dummy",
            keywords="dummy",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/kill {mob.key}")

        self.assertTrue(Mob.objects.filter(pk=mob.id).exists())
        message = self._message_by_type(messages, "cmd./kill.error", self.player.key)
        self.assertIsNotNone(message)
        self.assertIn("player targets", message.get("text", ""))


class TestBuilderPurge(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_purge_all_removes_room_items_and_mobs(self):
        item = create_definition_item(self.world, self.spawn_world, self.room, "Trash")
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target mob",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/pu")

        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())

        message = self._message_by_type(messages, "cmd./purge.success")
        self.assertIsNotNone(message)
        self.assertIn("cleaner", message.get("text", "").lower())
        self.assertEqual(message["data"]["room"]["inventory"], [])
        self.assertEqual(message["data"]["room"]["chars"], [])

    def test_purge_items_only_keeps_mobs(self):
        item = create_definition_item(self.world, self.spawn_world, self.room, "Pebble")
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/purge items")

        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        self.assertTrue(Mob.objects.filter(pk=mob.pk).exists())

        message = self._message_by_type(messages, "cmd./purge.success")
        self.assertIsNotNone(message)
        self.assertIn("all items", message.get("text", "").lower())

    def test_purge_mobs_removes_without_creating_corpses(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            pending_player_ability={
                "ability": "power-strike",
                "status": "casting",
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/purge mobs")

        encounter.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertIsNone(encounter.mob_id)
        self.assertFalse(
            self.room.inventory.filter(
                world=self.spawn_world,
                type=api_consts.ITEM_TYPE_CORPSE,
            ).exists()
        )
        preparation_state = self._message_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertIsNotNone(preparation_state)
        self.assertEqual(preparation_state["data"]["abilities"], [])

    def test_purge_target_can_remove_inventory_item(self):
        item = create_definition_item(
            self.world,
            self.spawn_world,
            self.player,
            "Relic",
            keywords="relic",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/purge relic")

        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        message = self._message_by_type(messages, "cmd./purge.success")
        self.assertIsNotNone(message)
        self.assertIn("You purge Relic from this world.", message.get("text", ""))


class TestBuilderRepop(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.other_zone = Zone.objects.create(
            world=self.world,
            name="Far Annex",
        )
        self.other_room = Room.objects.create(
            world=self.world,
            zone=self.other_zone,
            name="Far Annex Room",
            x=1,
            y=0,
            z=0,
        )
        self.none_definition = self._create_mob_definition("none-dummy")
        self.wait_definition = self._create_mob_definition("waiting-dummy")
        self.other_definition = self._create_mob_definition("other-zone-dummy")
        self.none_plan = self._create_spawn_plan(
            slug="none-plan",
            room=self.room,
            respawn_policy={"mode": "none"},
            definition=self.none_definition,
        )
        self.wait_plan = self._create_spawn_plan(
            slug="waiting-plan",
            room=self.room,
            respawn_policy={"mode": "fixed", "seconds": 3600},
            definition=self.wait_definition,
        )
        self.other_plan = self._create_spawn_plan(
            slug="other-zone-plan",
            room=self.other_room,
            respawn_policy={"mode": "fixed", "seconds": 3600},
            definition=self.other_definition,
        )
        run_spawn_plans_for_world(
            world=self.spawn_world,
            initial=True,
        )

    def _create_mob_definition(self, slug):
        return MobDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=slug.replace("-", " ").title(),
            mob_type=api_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )

    def _create_spawn_plan(
        self,
        *,
        slug,
        room,
        respawn_policy,
        definition,
    ):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=room.zone,
            slug=slug,
            name=slug.replace("-", " ").title(),
            respawn_policy=respawn_policy,
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug=f"{slug}-entry",
            source=f"mobdefinition.{definition.slug}",
            target={"room": f"room@{room.x},{room.y},{room.z}"},
            count=1,
        )
        return plan

    def _create_doorway(
        self,
        *,
        from_room,
        to_room,
        direction="east",
    ):
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=api_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction=direction,
            from_room=from_room,
            to_room=to_room,
            name="test gate",
        )
        return doorway

    def _create_runtime_door_state(
        self,
        *,
        from_room,
        to_room,
        runtime_world=None,
        direction="east",
        revision=7,
    ):
        doorway = self._create_doorway(
            from_room=from_room,
            to_room=to_room,
            direction=direction,
        )
        state = DoorState.objects.create(
            world=runtime_world or self.spawn_world,
            doorway=doorway,
            state=api_consts.DOOR_STATE_CLOSED,
            revision=revision,
        )
        return doorway, state

    @staticmethod
    def _message_by_type(messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_repop_forces_current_zone_waiting_and_none_plans_without_duplicates(self):
        _, door_state = self._create_runtime_door_state(
            from_room=self.room,
            to_room=self.other_room,
        )
        Mob.objects.filter(world=self.spawn_world).delete()

        ordinary_output = run_spawn_plans_for_world(world=self.spawn_world)

        self.assertEqual(
            sum(result["spawned"] for result in ordinary_output["spawn_plans"]),
            0,
        )
        self.assertFalse(Mob.objects.filter(world=self.spawn_world).exists())
        self.zone.last_respawn_ts = None
        self.zone.save(update_fields=["last_respawn_ts"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/repop")

        self.assertTrue(
            Mob.objects.filter(
                world=self.spawn_world,
                definition=self.none_definition,
            ).exists()
        )
        self.assertTrue(
            Mob.objects.filter(
                world=self.spawn_world,
                definition=self.wait_definition,
            ).exists()
        )
        self.assertFalse(
            Mob.objects.filter(
                world=self.spawn_world,
                definition=self.other_definition,
            ).exists()
        )
        message = self._message_by_type(messages, "cmd./repop.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["zone"]["id"], self.zone.id)
        self.assertEqual(message["data"]["spawn_plans_checked"], 2)
        self.assertEqual(message["data"]["spawn_plans_reconciled"], 2)
        self.assertEqual(message["data"]["placements_checked"], 2)
        self.assertEqual(message["data"]["spawned"], 2)
        self.assertEqual(
            message["data"]["doors"],
            {
                "requested": False,
                "doorways_checked": 0,
                "door_states_reset": 0,
            },
        )
        door_state.refresh_from_db()
        self.assertEqual(door_state.state, api_consts.DOOR_STATE_CLOSED)
        self.assertEqual(door_state.revision, 7)
        self.zone.refresh_from_db()
        self.assertIsNone(self.zone.last_respawn_ts)

        with capture_game_messages() as second_messages:
            dispatch_text_command(self.player.id, "/repop")

        self.assertEqual(
            Mob.objects.filter(
                world=self.spawn_world,
                definition__in=[
                    self.none_definition,
                    self.wait_definition,
                ],
            ).count(),
            2,
        )
        second_message = self._message_by_type(
            second_messages,
            "cmd./repop.success",
        )
        self.assertIsNotNone(second_message)
        self.assertEqual(second_message["data"]["spawned"], 0)

    def test_repop_doors_resets_only_selected_zone_and_runtime(self):
        current_doorway, current_state = self._create_runtime_door_state(
            from_room=self.room,
            to_room=self.other_room,
        )
        _, other_zone_state = self._create_runtime_door_state(
            from_room=self.other_room,
            to_room=self.room,
            direction="south",
        )
        sparse_doorway = self._create_doorway(
            from_room=self.room,
            to_room=self.other_room,
            direction="north",
        )
        other_runtime = self.world.create_spawn_world()
        other_runtime_state = DoorState.objects.create(
            world=other_runtime,
            doorway=current_doorway,
            state=api_consts.DOOR_STATE_LOCKED,
            revision=3,
        )
        self.zone.last_respawn_ts = None
        self.zone.save(update_fields=["last_respawn_ts"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/repop --doors")

        current_state.refresh_from_db()
        other_zone_state.refresh_from_db()
        other_runtime_state.refresh_from_db()
        self.assertEqual(current_state.state, api_consts.DOOR_STATE_OPEN)
        self.assertEqual(current_state.revision, 8)
        self.assertEqual(other_zone_state.state, api_consts.DOOR_STATE_CLOSED)
        self.assertEqual(other_zone_state.revision, 7)
        self.assertEqual(other_runtime_state.state, api_consts.DOOR_STATE_LOCKED)
        self.assertEqual(other_runtime_state.revision, 3)
        self.assertFalse(
            DoorState.objects.filter(
                world=self.spawn_world,
                doorway=sparse_doorway,
            ).exists()
        )
        self.zone.refresh_from_db()
        self.assertIsNone(self.zone.last_respawn_ts)

        message = self._message_by_type(messages, "cmd./repop.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message["data"]["doors"],
            {
                "requested": True,
                "doorways_checked": 2,
                "door_states_reset": 1,
            },
        )
        self.assertIn("Reset 1 runtime door state", message["text"])

    def test_repop_rejects_unknown_or_duplicate_options(self):
        for command in (
            "/repop --unknown",
            "/repop --doors --doors",
        ):
            with self.subTest(command=command):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, command)

                message = self._message_by_type(
                    messages,
                    "cmd./repop.error",
                )
                self.assertIsNotNone(message)
                self.assertEqual(message["data"]["code"], "invalid_args")
                self.assertIn("/repop [--doors]", message["text"])

    def test_room_script_repop_uses_issuer_rooms_zone(self):
        _, door_state = self._create_runtime_door_state(
            from_room=self.other_room,
            to_room=self.room,
            direction="south",
        )
        Mob.objects.filter(
            world=self.spawn_world,
            definition=self.other_definition,
        ).delete()
        script_user = self.create_user("repop-script@example.com")
        script_player = self.create_player(
            "Repop Script Actor",
            user=script_user,
            room=self.other_room,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=script_player.id,
                payload={"text": "/cmd room -- /repop --doors"},
                script_source=True,
            )

        door_state.refresh_from_db()
        self.assertEqual(door_state.state, api_consts.DOOR_STATE_OPEN)
        self.assertEqual(door_state.revision, 8)
        self.assertTrue(
            Mob.objects.filter(
                world=self.spawn_world,
                definition=self.other_definition,
            ).exists()
        )
        repop_message = self._message_by_type(messages, "cmd./repop.success")
        self.assertIsNotNone(repop_message)
        self.assertEqual(repop_message["data"]["actor"]["char_type"], "room")
        self.assertEqual(repop_message["data"]["zone"]["id"], self.other_zone.id)
        self.assertEqual(repop_message["data"]["spawn_plans_checked"], 1)
        self.assertEqual(repop_message["data"]["spawned"], 1)
        self.assertEqual(repop_message["data"]["doors"]["requested"], True)
        self.assertEqual(
            repop_message["data"]["doors"]["door_states_reset"],
            1,
        )
        cmd_message = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["errors"], [])


class TestBuilderTransfer(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self._set_online(self.player)

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type):
        return [
            msg
            for msg in messages
            if msg["message"].get("type") == message_type
        ]

    @staticmethod
    def _set_online(*players):
        for player in players:
            player.in_game = True
            player.save(update_fields=["in_game"])

    @staticmethod
    def _room_ref(room):
        return f"room@{room.x},{room.y},{room.z}"

    def test_room_enter_event_fires_for_transfer_but_not_same_room_transfer(self):
        destination = self.room.create_at("east")
        Trigger.objects.create(
            world=self.world,
            scope=api_consts.TRIGGER_SCOPE_ROOM,
            kind=api_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=destination.id,
            event=api_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The transfer sigil flashes.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as moved_messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(
                    self.player.id,
                    f"/transfer self {self._room_ref(destination)}",
                )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        moved_echoes = [
            entry
            for entry in self._messages_by_type(
                moved_messages,
                "cmd./echo.success",
            )
            if "transfer sigil" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(moved_echoes), 1)

        with capture_game_messages() as same_room_messages:
            dispatch_text_command(self.player.id, "/transfer self here")

        same_room_echoes = [
            entry
            for entry in self._messages_by_type(
                same_room_messages,
                "cmd./echo.success",
            )
            if "transfer sigil" in entry["message"].get("text", "")
        ]
        self.assertEqual(same_room_echoes, [])

    def test_builder_transfers_remote_player_and_refreshes_target_room(self):
        origin = self.room.create_at("west")
        destination = self.room.create_at("east")
        target_user = self.create_user("transfer-target@example.com")
        target = self.create_player(
            "Target",
            user=target_user,
            room=origin,
        )
        self._set_online(target)
        idle_timestamp = timezone.now() - timedelta(minutes=10)
        target.last_action_ts = idle_timestamp
        target.save(update_fields=["last_action_ts"])

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer target {self._room_ref(destination)}",
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, destination.id)
        self.assertEqual(target.world_id, self.spawn_world.id)
        self.assertEqual(target.last_action_ts, idle_timestamp)
        self.assertTrue(target.viewed_rooms.filter(pk=destination.id).exists())

        success = self._message_by_type(messages, "cmd./transfer.success")
        self.assertIsNotNone(success)
        self.assertEqual(success["data"]["transferred"]["key"], target.key)
        self.assertEqual(success["data"]["target"]["id"], destination.id)
        self.assertIn("You transfer Target", success["text"])

        transfer_messages = self._messages_by_type(messages, "affect.transfer")
        self.assertEqual(len(transfer_messages), 1)
        self.assertEqual(transfer_messages[0]["player_key"], target.key)
        self.assertEqual(
            transfer_messages[0]["message"]["data"]["room"]["id"],
            destination.id,
        )

    def test_transfer_supports_portable_absolute_typed_and_direction_rooms(self):
        destination = self.room.create_at("east")
        selectors = (
            self._room_ref(destination),
            str(destination.relative_id),
            f"room.{destination.id}",
            "east",
        )

        for selector in selectors:
            with self.subTest(selector=selector):
                self.player.room = self.room
                self.player.save(update_fields=["room"])

                with capture_game_messages() as messages:
                    dispatch_text_command(
                        self.player.id,
                        f"/transfer self {selector}",
                    )

                self.player.refresh_from_db()
                self.assertEqual(self.player.room_id, destination.id)
                self.assertIsNotNone(
                    self._message_by_type(messages, "cmd./transfer.success")
                )

    def test_slashless_transfer_alias_moves_player(self):
        destination = self.room.create_at("east")

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"transfer self {self._room_ref(destination)}",
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertIsNotNone(
            self._message_by_type(messages, "cmd./transfer.success")
        )

    def test_builder_transfers_local_mob(self):
        destination = self.room.create_at("east")
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a gate guard",
            keywords="gate guard",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer guard {self._room_ref(destination)}",
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, destination.id)
        success = self._message_by_type(messages, "cmd./transfer.success")
        self.assertEqual(success["data"]["transferred_type"], "mob")
        self.assertIsNone(self._message_by_type(messages, "affect.transfer"))

    def test_scripted_room_and_mob_issuers_can_transfer(self):
        destination = self.room.create_at("east")
        room_target_user = self.create_user("room-transfer-target@example.com")
        room_target = self.create_player(
            "RoomTarget",
            user=room_target_user,
            room=self.room,
        )
        mob_target_user = self.create_user("mob-transfer-target@example.com")
        mob_target = self.create_player(
            "MobTarget",
            user=mob_target_user,
            room=self.room,
        )
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="an usher",
            keywords="usher",
        )
        self._set_online(room_target, mob_target)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/transfer {room_target.key} {self._room_ref(destination)}",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=issuer_mob.id,
                payload={
                    "text": f"/transfer {mob_target.key} {self._room_ref(destination)}",
                },
                script_source=True,
            )

        room_target.refresh_from_db()
        mob_target.refresh_from_db()
        self.assertEqual(room_target.room_id, destination.id)
        self.assertEqual(mob_target.room_id, destination.id)
        successes = self._messages_by_type(messages, "cmd./transfer.success")
        self.assertEqual(len(successes), 2)
        self.assertEqual(
            {message["player_key"] for message in successes},
            {self.room.key, issuer_mob.key},
        )

    def test_cmd_room_transfer_preserves_runtime_world_context(self):
        destination = self.room.create_at("east")
        target_user = self.create_user("cmd-room-transfer@example.com")
        target = self.create_player(
            "CmdTarget",
            user=target_user,
            room=self.room,
        )
        self._set_online(target)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": (
                        f"/cmd room -- /transfer {target.key} "
                        f"{self._room_ref(destination)}"
                    ),
                },
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, destination.id)
        cmd_success = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_success)
        self.assertEqual(cmd_success["data"]["errors"], [])

    def test_untrusted_player_mob_and_room_scripts_cannot_transfer(self):
        destination = self.room.create_at("east")
        other_user = self.create_user("untrusted-transfer@example.com")
        other_player = self.create_player(
            "Other",
            user=other_user,
            room=self.room,
        )
        issuer_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="an untrusted usher",
            keywords="usher",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=other_player.id,
                payload={"text": f"/transfer self {self._room_ref(destination)}"},
                script_source=True,
            )
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=issuer_mob.id,
                payload={"text": f"/transfer self {self._room_ref(destination)}"},
            )
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/transfer {other_player.key} {self._room_ref(destination)}",
                    "world_id": self.spawn_world.id,
                },
            )

        other_player.refresh_from_db()
        issuer_mob.refresh_from_db()
        self.assertEqual(other_player.room_id, self.room.id)
        self.assertEqual(issuer_mob.room_id, self.room.id)
        errors = self._messages_by_type(messages, "cmd./transfer.error")
        self.assertEqual(len(errors), 3)
        for error in errors:
            self.assertIn("permission", error["message"].get("text", "").lower())

    def test_transfer_rejects_target_from_parallel_runtime_world(self):
        destination = self.room.create_at("east")
        other_runtime = self.world.create_spawn_world()
        target_user = self.create_user("parallel-transfer-target@example.com")
        target = self.create_player(
            "ParallelTarget",
            user=target_user,
            world=other_runtime,
            room=self.room,
        )
        self._set_online(target)

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer {target.key} {self._room_ref(destination)}",
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, self.room.id)
        self.assertEqual(target.world_id, other_runtime.id)
        error = self._message_by_type(messages, "cmd./transfer.error")
        self.assertEqual(error["data"]["code"], "invalid_target")

    def test_transfer_emits_runtime_isolated_exit_look_and_enter_events(self):
        destination = self.room.create_at("east")
        target_user = self.create_user("visible-transfer-target@example.com")
        target = self.create_player(
            "VisibleTarget",
            user=target_user,
            room=self.room,
        )
        origin_user = self.create_user("transfer-origin-watcher@example.com")
        origin_watcher = self.create_player(
            "OriginWatcher",
            user=origin_user,
            room=self.room,
        )
        destination_user = self.create_user("transfer-destination-watcher@example.com")
        destination_watcher = self.create_player(
            "DestinationWatcher",
            user=destination_user,
            room=destination,
        )
        other_runtime = self.world.create_spawn_world()
        isolated_user = self.create_user("transfer-isolated-watcher@example.com")
        isolated_watcher = self.create_player(
            "IsolatedWatcher",
            user=isolated_user,
            world=other_runtime,
            room=destination,
        )
        self._set_online(
            target,
            origin_watcher,
            destination_watcher,
            isolated_watcher,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer {target.key} {self._room_ref(destination)}",
            )

        exit_messages = self._messages_by_type(
            messages,
            "notification./transfer.exit",
        )
        enter_messages = self._messages_by_type(
            messages,
            "notification./transfer.enter",
        )
        self.assertEqual(
            {message["player_key"] for message in exit_messages},
            {self.player.key, origin_watcher.key},
        )
        self.assertEqual(
            {message["player_key"] for message in enter_messages},
            {destination_watcher.key},
        )
        self.assertNotIn(
            isolated_watcher.key,
            {message["player_key"] for message in enter_messages},
        )
        transfer_state = self._messages_by_type(messages, "affect.transfer")
        self.assertEqual(
            [message["player_key"] for message in transfer_state],
            [target.key],
        )
        look_char_keys = {
            char["key"]
            for char in transfer_state[0]["message"]["data"]["room"]["chars"]
        }
        self.assertNotIn(isolated_watcher.key, look_char_keys)
        event_types = [
            message["message"]["type"]
            for message in messages
            if message["message"]["type"] in {
                "notification./transfer.exit",
                "affect.transfer",
                "notification./transfer.enter",
            }
        ]
        exit_indices = [
            index
            for index, event_type in enumerate(event_types)
            if event_type == "notification./transfer.exit"
        ]
        enter_indices = [
            index
            for index, event_type in enumerate(event_types)
            if event_type == "notification./transfer.enter"
        ]
        transfer_index = event_types.index("affect.transfer")
        self.assertLess(max(exit_indices), transfer_index)
        self.assertLess(transfer_index, min(enter_indices))

    def test_invisible_and_same_room_transfers_do_not_notify_watchers(self):
        destination = self.room.create_at("east")
        origin_watcher_user = self.create_user("transfer-origin-no-notify@example.com")
        origin_watcher = self.create_player(
            "OriginWatcher",
            user=origin_watcher_user,
            room=self.room,
        )
        watcher_user = self.create_user("transfer-no-notify@example.com")
        watcher = self.create_player(
            "Watcher",
            user=watcher_user,
            room=destination,
        )
        self._set_online(origin_watcher, watcher)
        self.player.is_invisible = True
        self.player.save(update_fields=["is_invisible"])

        with capture_game_messages() as invisible_messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer self {self._room_ref(destination)}",
            )

        self.assertEqual(
            self._messages_by_type(
                invisible_messages,
                "notification./transfer.enter",
            ),
            [],
        )
        self.assertEqual(
            self._messages_by_type(
                invisible_messages,
                "notification./transfer.exit",
            ),
            [],
        )

        opponent = Mob.objects.create(
            world=self.spawn_world,
            room=destination,
            name="a waiting opponent",
            fights_back=False,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=destination,
            player=self.player,
            mob=opponent,
        )
        effect = create_active_effect(
            target=self.player,
            source=opponent,
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            payload={
                "effect": "dot",
                "label": "Waiting",
                "remaining_rounds": 2,
                "duration_rounds": 2,
            },
        )

        with capture_game_messages() as same_room_messages:
            dispatch_text_command(self.player.id, "/transfer self here")

        encounter.refresh_from_db()
        success = self._message_by_type(
            same_room_messages,
            "cmd./transfer.success",
        )
        self.assertFalse(success["data"]["moved"])
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertTrue(ActiveEffect.objects.filter(pk=effect.pk).exists())
        self.assertIsNone(
            self._message_by_type(same_room_messages, "affect.transfer")
        )
        self.assertIsNotNone(
            self._message_by_type(same_room_messages, "cmd.look.success")
        )
        self.assertEqual(
            self._messages_by_type(
                same_room_messages,
                "notification./transfer.exit",
            ),
            [],
        )

    def test_transfer_finishes_active_combat_in_one_relocation(self):
        destination = self.room.create_at("east")
        target_user = self.create_user("combat-transfer-target@example.com")
        target = self.create_player(
            "CombatTarget",
            user=target_user,
            room=self.room,
        )
        self._set_online(target)
        opponent = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="an opponent",
            keywords="opponent",
            fights_back=False,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=target,
            mob=opponent,
            pending_player_ability={
                "ability": "strike",
                "status": "casting",
            },
            pending_mob_ability={"ability": "bite"},
            pending_flee={"direction": "east"},
        )
        effect = create_active_effect(
            target=target,
            source=opponent,
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            payload={
                "effect": "dot",
                "label": "Burning",
                "remaining_rounds": 2,
                "duration_rounds": 2,
            },
        )

        encounter.next_resolution_ts = timezone.now() + timedelta(minutes=1)
        encounter.save(update_fields=["next_resolution_ts"])

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer {target.key} {self._room_ref(destination)}",
            )

        encounter.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(target.room_id, destination.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertIsNone(encounter.next_resolution_ts)
        self.assertEqual(encounter.pending_player_ability, {})
        self.assertEqual(encounter.pending_mob_ability, {})
        self.assertEqual(encounter.pending_flee, {})
        self.assertFalse(ActiveEffect.objects.filter(pk=effect.pk).exists())
        effect_state = self._message_by_type(
            messages,
            "player.combat_effects.update",
        )
        self.assertIsNotNone(effect_state)
        self.assertEqual(effect_state["data"]["active_effects"], [])
        preparation_state = self._message_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertIsNotNone(preparation_state)
        self.assertEqual(preparation_state["data"]["abilities"], [])

    def test_transfer_finishes_mob_combat_and_refreshes_opponent_effects(self):
        destination = self.room.create_at("east")
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a bound sentinel",
            keywords="sentinel",
            fights_back=False,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=target,
            next_resolution_ts=timezone.now() + timedelta(minutes=1),
        )
        effect = create_active_effect(
            target=self.player,
            source=target,
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            payload={
                "effect": "dot",
                "label": "Marked",
                "remaining_rounds": 2,
                "duration_rounds": 2,
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer {target.key} {self._room_ref(destination)}",
            )

        target.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(target.room_id, destination.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertFalse(ActiveEffect.objects.filter(pk=effect.pk).exists())
        effect_state = self._message_by_type(
            messages,
            "player.combat_effects.update",
        )
        self.assertIsNotNone(effect_state)
        self.assertEqual(effect_state["data"]["active_effects"], [])

    def test_transfer_fires_runtime_isolated_mob_enter_reactions(self):
        destination = self.room.create_at("east")
        onward_destination = destination.create_at("east")
        definition = MobDefinition.objects.create(
            world=self.world,
            name="Threshold Watcher",
        )
        watcher = Mob.objects.create(
            world=self.spawn_world,
            room=destination,
            definition=definition,
            name="a threshold watcher",
        )
        other_runtime = self.world.create_spawn_world()
        isolated_watcher = Mob.objects.create(
            world=other_runtime,
            room=destination,
            definition=definition,
            name="an isolated threshold watcher",
        )
        Trigger.objects.create(
            world=self.world,
            kind=api_consts.TRIGGER_KIND_EVENT,
            scope=api_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=definition.id,
            event=api_consts.MOB_REACTION_EVENT_ENTERING,
            script=(
                "say The threshold stirs. && "
                f"/transfer {{{{ actor_key }}}} "
                f"{self._room_ref(onward_destination)}"
            ),
            display_action_in_room=False,
            gate_delay=0,
        )
        self.player.is_invisible = True
        self.player.save(update_fields=["is_invisible"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(
                    self.player.id,
                    f"/transfer self {self._room_ref(destination)}",
                )

        reaction_actor_keys = {
            message["message"].get("data", {}).get("actor", {}).get("key")
            for message in messages
            if message["message"].get("type") == "notification.cmd.say.success"
        }
        self.assertIn(watcher.key, reaction_actor_keys)
        self.assertNotIn(isolated_watcher.key, reaction_actor_keys)
        self.player.refresh_from_db()
        watcher.refresh_from_db()
        self.assertEqual(self.player.room_id, onward_destination.id)
        self.assertEqual(watcher.room_id, destination.id)

        self.player.room = self.room
        self.player.save(update_fields=["room"])
        transferred_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name="a threshold courier",
            keywords="courier",
        )
        with capture_game_messages() as mob_messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(
                    self.player.id,
                    (
                        f"/transfer {transferred_mob.key} "
                        f"{self._room_ref(destination)}"
                    ),
                )

        transferred_mob.refresh_from_db()
        watcher.refresh_from_db()
        isolated_watcher.refresh_from_db()
        self.assertEqual(transferred_mob.room_id, onward_destination.id)
        self.assertEqual(watcher.room_id, destination.id)
        self.assertEqual(isolated_watcher.room_id, destination.id)
        self.assertTrue(
            any(
                message["player_key"] == watcher.key
                for message in mob_messages
                if message["message"].get("type") == "cmd.say.success"
            )
        )
        self.assertFalse(
            any(
                message["player_key"] == transferred_mob.key
                for message in mob_messages
                if message["message"].get("type") == "cmd.say.success"
            )
        )

    def test_transfer_rejects_offline_player_keys(self):
        destination = self.room.create_at("east")
        target_user = self.create_user("offline-transfer-target@example.com")
        target = self.create_player(
            "OfflineTarget",
            user=target_user,
            room=self.room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer {target.key} {self._room_ref(destination)}",
            )

        target.refresh_from_db()
        self.assertEqual(target.room_id, self.room.id)
        error = self._message_by_type(messages, "cmd./transfer.error")
        self.assertEqual(error["data"]["code"], "invalid_target")
        self.assertIsNone(self._message_by_type(messages, "affect.transfer"))

    def test_transfer_prefers_exact_players_then_counted_local_characters(self):
        destination = self.room.create_at("east")
        remote_room = self.room.create_at("west")
        remote_user = self.create_user("guardian-transfer-target@example.com")
        remote_player = self.create_player(
            "Guardian",
            user=remote_user,
            room=remote_room,
        )
        local_user = self.create_user("local-guard-transfer-target@example.com")
        local_player = self.create_player(
            "Guard Captain",
            user=local_user,
            room=self.room,
        )
        self._set_online(remote_player, local_player)
        first_guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a first guard",
            keywords="guard",
        )
        second_guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a second guard",
            keywords="guard",
        )

        with capture_game_messages():
            dispatch_text_command(
                self.player.id,
                f"/transfer 2.guard {self._room_ref(destination)}",
            )
        first_guard.refresh_from_db()
        self.assertEqual(first_guard.room_id, destination.id)

        with capture_game_messages():
            dispatch_text_command(
                self.player.id,
                f"/transfer guard {self._room_ref(destination)}",
            )

        local_player.refresh_from_db()
        remote_player.refresh_from_db()
        second_guard.refresh_from_db()
        self.assertEqual(local_player.room_id, destination.id)
        self.assertEqual(second_guard.room_id, self.room.id)
        self.assertEqual(remote_player.room_id, remote_room.id)

    def test_bare_numeric_room_selector_prefers_legacy_relative_id(self):
        absolute_room = self.room.create_at("east")
        absolute_room.relative_id = 900000 + absolute_room.id
        absolute_room.save(update_fields=["relative_id"])
        legacy_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Legacy numbered room",
            x=self.room.x,
            y=self.room.y + 1,
            z=self.room.z,
            relative_id=absolute_room.id,
        )

        with capture_game_messages():
            dispatch_text_command(
                self.player.id,
                f"/transfer self {absolute_room.id}",
            )
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, legacy_room.id)

        with capture_game_messages():
            dispatch_text_command(
                self.player.id,
                f"/transfer self room.{absolute_room.id}",
            )
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, absolute_room.id)

    def test_transfer_enforces_instance_authored_room_boundary(self):
        instance_config = WorldConfig.objects.create()
        instance_template = World.objects.new_world(
            name="Transfer instance",
            author=self.user,
            config=instance_config,
            instance_of=self.world,
        )
        instance_room = instance_template.rooms.first()
        instance_runtime = instance_template.create_spawn_world()
        target_user = self.create_user("instance-transfer-target@example.com")
        target = self.create_player(
            "InstanceTarget",
            user=target_user,
            world=instance_runtime,
            room=instance_room,
        )
        self._set_online(target)

        with capture_game_messages() as base_room_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/transfer {target.key} here",
                    "world_id": instance_runtime.id,
                },
                script_source=True,
            )
        base_error = self._message_by_type(
            base_room_messages,
            "cmd./transfer.error",
        )
        self.assertEqual(base_error["data"]["code"], "invalid_world_context")

        instance_room.east = self.room
        instance_room.save(update_fields=["east"])
        with capture_game_messages() as cross_exit_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=instance_room.id,
                payload={
                    "text": f"/transfer {target.key} east",
                    "world_id": instance_runtime.id,
                },
                script_source=True,
            )
        cross_exit_error = self._message_by_type(
            cross_exit_messages,
            "cmd./transfer.error",
        )
        self.assertEqual(
            cross_exit_error["data"]["code"],
            "invalid_world_context",
        )
        target.refresh_from_db()
        self.assertEqual(target.world_id, instance_runtime.id)
        self.assertEqual(target.room_id, instance_room.id)

    def test_transfer_rejects_player_prefixes_and_legacy_trailing_commands(self):
        destination = self.room.create_at("east")
        anna_user = self.create_user("transfer-anna@example.com")
        anna = self.create_player("Anna", user=anna_user, room=self.room)
        annabel_user = self.create_user("transfer-annabel@example.com")
        annabel = self.create_player("Annabel", user=annabel_user, room=self.room)
        self._set_online(anna, annabel)

        with capture_game_messages() as prefix_messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer ann {self._room_ref(destination)}",
            )

        prefix_error = self._message_by_type(
            prefix_messages,
            "cmd./transfer.error",
        )
        self.assertEqual(prefix_error["data"]["code"], "invalid_target")

        with capture_game_messages() as trailing_messages:
            dispatch_text_command(
                self.player.id,
                f"/transfer self {self._room_ref(destination)} /echo gone",
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        trailing = self._message_by_type(
            trailing_messages,
            "cmd./transfer.error",
        )
        self.assertIn("same-line", trailing["text"])


class TestBuilderJump(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type):
        return [msg for msg in messages if msg["message"].get("type") == message_type]

    def test_room_enter_event_fires_for_jump_but_not_same_room_jump(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        target_room = self.room.create_at("east")
        Trigger.objects.create(
            world=self.world,
            scope=api_consts.TRIGGER_SCOPE_ROOM,
            kind=api_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=target_room.id,
            event=api_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The landing rune glows.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as moved_messages:
            dispatch_text_command(
                self.player.id,
                f"/jump {target_room.id}",
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, target_room.id)
        moved_echoes = [
            entry
            for entry in self._messages_by_type(
                moved_messages,
                "cmd./echo.success",
            )
            if "landing rune" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(moved_echoes), 1)

        with capture_game_messages() as same_room_messages:
            dispatch_text_command(
                self.player.id,
                f"/jump {target_room.id}",
            )

        same_room_echoes = [
            entry
            for entry in self._messages_by_type(
                same_room_messages,
                "cmd./echo.success",
            )
            if "landing rune" in entry["message"].get("text", "")
        ]
        self.assertEqual(same_room_echoes, [])

    def test_jump_moves_player_to_target_room(self):
        target_room = self.room.create_at("east")
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            pending_player_ability={
                "ability": "power-strike",
                "status": "casting",
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/jump {target_room.id}")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, target_room.id)

        message = self._message_by_type(messages, "cmd./jump.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "room")
        self.assertEqual(message["data"]["target"]["id"], target_room.id)
        self.assertEqual(message["data"]["target"]["key"], f"room.{target_room.relative_id}")
        self.assertIn("satisfying thump", message.get("text", "").lower())
        preparation_state = self._message_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertIsNotNone(preparation_state)
        self.assertEqual(preparation_state["data"]["abilities"], [])

    def test_jump_moves_player_by_direction(self):
        target_room = self.room.create_at(api_consts.DIRECTION_EAST)

        for selector in ("east", "e"):
            with self.subTest(selector=selector):
                self.player.room = self.room
                self.player.stamina = 10
                self.player.save(update_fields=["room", "stamina"])

                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, f"/jump {selector}")

                self.player.refresh_from_db()
                self.assertEqual(self.player.room_id, target_room.id)
                self.assertEqual(self.player.stamina, 10)

                message = self._message_by_type(messages, "cmd./jump.success")
                self.assertIsNotNone(message)
                self.assertEqual(message["data"]["target"]["id"], target_room.id)

    def test_jump_direction_bypasses_move_policy(self):
        target_room = self.room.create_at(api_consts.DIRECTION_EAST)
        room_ct = ContentType.objects.get_for_model(target_room.__class__)
        Trigger.objects.create(
            world=self.world,
            scope=api_consts.TRIGGER_SCOPE_ROOM,
            kind=api_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=target_room.id,
            event=api_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
            conditions=json.dumps({"always": False}),
            failure_message="Only warlords may enter.",
            display_action_in_room=False,
            gate_delay=0,
        )
        self.player.stamina = 10
        self.player.save(update_fields=["stamina"])

        with capture_game_messages() as move_messages:
            dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        move_error = self._message_by_type(move_messages, "cmd.move.error")
        self.assertIsNotNone(move_error)
        self.assertEqual(move_error["text"], "Only warlords may enter.")

        with capture_game_messages() as jump_messages:
            dispatch_text_command(self.player.id, "/jump e")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, target_room.id)
        self.assertEqual(self.player.stamina, 10)
        self.assertIsNotNone(self._message_by_type(jump_messages, "cmd./jump.success"))
        self.assertIsNone(self._message_by_type(jump_messages, "cmd.move.error"))

    def test_jump_rejects_invalid_room_id(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/jump nope")

        message = self._message_by_type(messages, "cmd./jump.error")
        self.assertIsNotNone(message)
        self.assertIn("must be a number", message.get("text", "").lower())

    def test_jump_rejects_unknown_room_id(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/jump 999999")

        message = self._message_by_type(messages, "cmd./jump.error")
        self.assertIsNotNone(message)
        self.assertIn("invalid room id", message.get("text", "").lower())

    def test_jump_prefers_template_room_id_over_relative_id_collision(self):
        target_room = self.room.create_at("east")
        colliding_room = self.room.create_at("west")

        colliding_room.relative_id = target_room.id
        colliding_room.save(update_fields=["relative_id"])

        with capture_game_messages():
            dispatch_text_command(self.player.id, f"/jump {target_room.id}")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, target_room.id)

    def test_jump_sends_origin_and_destination_notifications(self):
        target_room = self.room.create_at("east")
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        origin_user = self.create_user("origin-watcher@example.com")
        origin_watcher = self.create_player("Origin Watcher", user=origin_user, room=self.room)
        origin_watcher.in_game = True
        origin_watcher.save(update_fields=["in_game"])

        destination_user = self.create_user("destination-watcher@example.com")
        destination_watcher = self.create_player(
            "Destination Watcher",
            user=destination_user,
            room=target_room,
        )
        destination_watcher.in_game = True
        destination_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/jump {target_room.relative_id}")

        exit_messages = self._messages_by_type(messages, "notification./jump.exit")
        enter_messages = self._messages_by_type(messages, "notification./jump.enter")

        self.assertEqual(len(exit_messages), 1)
        self.assertEqual(exit_messages[0]["player_key"], origin_watcher.key)
        self.assertIn("disappears", exit_messages[0]["message"].get("text", "").lower())

        self.assertEqual(len(enter_messages), 1)
        self.assertEqual(enter_messages[0]["player_key"], destination_watcher.key)
        self.assertIn("appears", enter_messages[0]["message"].get("text", "").lower())

    def test_jump_does_not_notify_players_in_another_runtime_world(self):
        target_room = self.room.create_at("east")
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        other_runtime = self.world.create_spawn_world(
            instance_ref="other-jump-runtime",
        )

        origin_watcher = self.create_player(
            "Other Origin Watcher",
            user=self.create_user("other-origin-watcher@example.com"),
            world=other_runtime,
            room=self.room,
        )
        destination_watcher = self.create_player(
            "Other Destination Watcher",
            user=self.create_user("other-destination-watcher@example.com"),
            world=other_runtime,
            room=target_room,
        )
        origin_watcher.in_game = True
        destination_watcher.in_game = True
        origin_watcher.save(update_fields=["in_game"])
        destination_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/jump {target_room.relative_id}")

        notified_player_keys = {
            message["player_key"]
            for message in messages
            if message["message"].get("type")
            in {"notification./jump.exit", "notification./jump.enter"}
        }
        self.assertNotIn(origin_watcher.key, notified_player_keys)
        self.assertNotIn(destination_watcher.key, notified_player_keys)

    def test_jump_omits_notifications_when_invisible(self):
        target_room = self.room.create_at("east")
        self.player.is_invisible = True
        self.player.save(update_fields=["is_invisible"])

        origin_user = self.create_user("origin-no-notify@example.com")
        origin_watcher = self.create_player("Origin Watcher", user=origin_user, room=self.room)
        origin_watcher.in_game = True
        origin_watcher.save(update_fields=["in_game"])

        destination_user = self.create_user("destination-no-notify@example.com")
        destination_watcher = self.create_player(
            "Destination Watcher",
            user=destination_user,
            room=target_room,
        )
        destination_watcher.in_game = True
        destination_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/jump {target_room.relative_id}")

        self.assertEqual(
            self._messages_by_type(messages, "notification./jump.exit"),
            [],
        )
        self.assertEqual(
            self._messages_by_type(messages, "notification./jump.enter"),
            [],
        )


class TestBuilderSetLevel(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_setlevel_updates_builder_player_level_and_experience(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setlevel 3")

        self.player.refresh_from_db()
        self.assertEqual(self.player.level, 3)
        self.assertEqual(self.player.experience, 100)

        message = self._message_by_type(messages, "cmd./setlevel.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "player")
        self.assertEqual(message["data"]["previous_level"], 1)
        self.assertEqual(message["data"]["new_level"], 3)
        self.assertEqual(message["data"]["experience"], 100)
        self.assertEqual(message["data"]["experience_progress"], 0)
        self.assertEqual(message["data"]["experience_needed"], 300)

    def test_setlevel_updates_room_player_target(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setlevel target 2")

        target.refresh_from_db()
        self.assertEqual(target.level, 2)
        self.assertEqual(target.experience, 30)

        message = self._message_by_type(messages, "cmd./setlevel.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertIn("Target", message["text"])

    def test_setlevel_updates_room_mob_target(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
            level=1,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setlevel guard 4")

        mob.refresh_from_db()
        self.assertEqual(mob.level, 4)

        message = self._message_by_type(messages, "cmd./setlevel.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "mob")
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["new_level"], 4)

    def test_setlevel_rejects_levels_above_world_max(self):
        self.world.config.max_level = 2
        self.world.config.save(update_fields=["max_level"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setlevel 3")

        self.player.refresh_from_db()
        self.assertEqual(self.player.level, 1)
        message = self._message_by_type(messages, "cmd./setlevel.error")
        self.assertIsNotNone(message)
        self.assertIn("between 1 and 2", message.get("text", ""))


class TestBuilderSetClass(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.world.config.stat_system = {
            "attributes": [
                {"key": "constitution", "label": "Constitution"},
                {"key": "intelligence", "label": "Intelligence"},
            ],
            "labels": {
                "classes": {
                    "hoplite": "Hoplite",
                    "warlord": "Warlord",
                },
            },
            "class_profiles": {
                "hoplite": {
                    "label": "Hoplite",
                    "main_attribute": "constitution",
                    "attribute_weights": {
                        "constitution": 4,
                        "intelligence": 0,
                    },
                },
                "warlord": {
                    "label": "Warlord",
                    "main_attribute": "constitution",
                    "attribute_weights": {
                        "constitution": 2,
                        "intelligence": 2,
                    },
                },
            },
            "class_selection": {
                "enabled": False,
                "default": "hoplite",
            },
            "formulas": {
                "base_resources": {
                    "energy": {"source": "intelligence", "multiplier": 2},
                    "stamina": {"flat": 100},
                    "health": {},
                },
                "global_rules": [
                    {"source": "constitution", "target": "health_max", "multiplier": 2},
                ],
            },
        }
        self.world.config.save(update_fields=["stat_system"])

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_setclass_updates_player_class_and_recomputed_resources(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setclass hoplite")

        self.player.refresh_from_db()
        self.assertEqual(self.player.archetype, "hoplite")
        self.assertEqual(self.player.energy, 0)

        message = self._message_by_type(messages, "cmd./setclass.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["new_class"], "hoplite")
        self.assertEqual(message["data"]["target"]["energy_max"], 0)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setclass warlord")

        self.player.refresh_from_db()
        self.assertEqual(self.player.archetype, "warlord")
        self.assertGreater(self.player.energy, 0)
        message = self._message_by_type(messages, "cmd./setclass.success")
        self.assertEqual(message["data"]["class_label"], "Warlord")
        self.assertGreater(message["data"]["target"]["energy_max"], 0)

    def test_setclass_updates_room_player_target_by_class_label(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setclass target Warlord")

        target.refresh_from_db()
        self.assertEqual(target.archetype, "warlord")
        self.assertGreater(target.energy, 0)
        message = self._message_by_type(messages, "cmd./setclass.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertIn("Target", message["text"])

    def test_setclass_unlearns_target_abilities(self):
        AbilityDefinition.objects.create(
            world=self.world,
            slug="power-strike",
            name="Power Strike",
            command_verbs=["strike"],
            target={"type": "hostile", "default": "current_target", "allow_out_of_combat": False},
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cooldown={"rounds": 0},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        target = self.create_player("Target", room=self.room)
        target.known_abilities = ["power-strike"]
        target.ability_hotkeys = {"1": "power-strike"}
        target.ability_cooldowns = {"power-strike": 2}
        target.save(update_fields=["known_abilities", "ability_hotkeys", "ability_cooldowns"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=target,
            pending_player_ability={
                "ability": "power-strike",
                "status": "casting",
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/setclass target Warlord")

        target.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(target.archetype, "warlord")
        self.assertEqual(target.known_abilities, [])
        self.assertEqual(target.ability_hotkeys, {})
        self.assertEqual(target.ability_cooldowns, {})
        self.assertEqual(encounter.pending_player_ability, {})
        message = self._message_by_type(messages, "cmd./setclass.success")
        self.assertEqual(message["data"]["unlearned_abilities"], ["power-strike"])
        self.assertEqual(message["data"]["target"]["known_abilities"], [])
        self.assertEqual(message["data"]["target"]["ability_hotkeys"], {})
        preparation_state = self._message_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertIsNotNone(preparation_state)
        self.assertEqual(preparation_state["data"]["abilities"], [])

    def test_setclass_rejects_spoofed_trigger_source(self):
        other_user = self.create_user("trigger-setclass@example.com")
        other_player = self.create_player("TriggerTarget", user=other_user)
        original_class = other_player.archetype

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=other_player.id,
                payload={
                    "text": "/setclass hoplite",
                    "__trigger_source": True,
                },
            )

        other_player.refresh_from_db()
        self.assertEqual(other_player.archetype, original_class)
        message = self._message_by_type(messages, "cmd./setclass.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_setclass_allows_internal_script_source(self):
        other_user = self.create_user("script-setclass@example.com")
        other_player = self.create_player("ScriptTarget", user=other_user)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=other_player.id,
                payload={"text": "/setclass hoplite"},
                script_source=True,
            )

        other_player.refresh_from_db()
        self.assertEqual(other_player.archetype, "hoplite")
        message = self._message_by_type(messages, "cmd./setclass.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["new_class"], "hoplite")

    def test_cmd_room_setclass_updates_target_player(self):
        target = self.create_player("Pledge Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=target.id,
                payload={"text": f"/cmd room -- /setclass {target.key} Warlord"},
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.archetype, "warlord")
        cmd_message = self._message_by_type(messages, "cmd./cmd.success")
        self.assertIsNotNone(cmd_message)
        self.assertEqual(cmd_message["data"]["target"]["scope"], "room")
        self.assertEqual(cmd_message["data"]["errors"], [])


class TestBuilderStatsAndSet(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_builder_stats_reads_room_mob_by_selector(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Iron Guard",
            keywords="iron guard",
            health=12,
            health_max=40,
            attack_power=7,
            aggression=api_consts.MOB_AGGRESSION_PLAYERS,
            attributes={"strength": 3},
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/stats guard")

        message = self._message_by_type(messages, "cmd./stats.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "mob")
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["target"]["aggression"], api_consts.MOB_AGGRESSION_PLAYERS)
        self.assertEqual(message["data"]["target"]["stats"]["attack_power"], 7)
        self.assertEqual(message["data"]["target"]["attributes"]["strength"], 3)
        self.assertIn("Iron Guard", message.get("text", ""))
        self.assertIn("Aggression: players", message.get("text", ""))
        self.assertIn("Health: 12 / 40", message.get("text", ""))
        self.assertIn("Health Regen: 0", message.get("text", ""))
        self.assertNotIn("(regen", message.get("text", ""))
        self.assertNotIn("Stats:", message.get("text", ""))

    def test_builder_stats_reads_global_mob_key_outside_room(self):
        far_room = self.room.create_at("east")
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=far_room,
            name="Remote Guard",
            keywords="remote guard",
            health=20,
            health_max=55,
            ability_power=9,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/stats {mob.key}")

        message = self._message_by_type(messages, "cmd./stats.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["target"]["stats"]["ability_power"], 9)

    def test_builder_stats_reads_global_player_key_outside_room(self):
        far_room = self.room.create_at("east")
        target = self.create_player("RemotePlayer", room=far_room)
        target.health = 18
        target.stamina = 22
        target.save(update_fields=["health", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/stats {target.key}")

        message = self._message_by_type(messages, "cmd./stats.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "player")
        self.assertEqual(message["data"]["target"]["key"], target.key)
        self.assertEqual(message["data"]["target"]["health"], 18)
        self.assertIn("RemotePlayer", message.get("text", ""))

    def test_builder_set_updates_room_mob_stat(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            attack_power=2,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard attack_power 11")

        mob.refresh_from_db()
        self.assertEqual(mob.attack_power, 11)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["field"], "attack_power")
        self.assertEqual(message["data"]["previous_value"], 2)
        self.assertEqual(message["data"]["new_value"], 11)
        self.assertEqual(message["data"]["room"]["id"], self.room.id)

    def test_builder_set_updates_room_mob_attackable(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            attackable=True,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard attackable false")

        mob.refresh_from_db()
        self.assertFalse(mob.attackable)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["field"], "attackable")
        self.assertIs(message["data"]["previous_value"], True)
        self.assertIs(message["data"]["new_value"], False)

    def test_room_actor_set_updates_room_mob_attackable(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            attackable=False,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard attackable true",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        mob.refresh_from_db()
        self.assertTrue(mob.attackable)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "room")
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(message["data"]["field"], "attackable")
        self.assertIs(message["data"]["previous_value"], False)
        self.assertIs(message["data"]["new_value"], True)

    def test_builder_set_rejects_mob_current_resource_above_max(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            health=100,
            health_max=200,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard health 500")

        mob.refresh_from_db()
        self.assertEqual(mob.health, 100)
        self.assertEqual(mob.health_max, 200)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("health cannot exceed health_max (200)", message.get("text", ""))
        self.assertEqual(message["data"]["field"], "health")
        self.assertEqual(message["data"]["max_field"], "health_max")
        self.assertEqual(message["data"]["max_value"], 200)

    def test_builder_set_clamps_mob_current_resource_when_lowering_max(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            health=150,
            health_max=200,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard health_max 100")

        mob.refresh_from_db()
        self.assertEqual(mob.health, 100)
        self.assertEqual(mob.health_max, 100)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["field"], "health_max")
        self.assertEqual(message["data"]["previous_value"], 200)
        self.assertEqual(message["data"]["new_value"], 100)
        self.assertEqual(message["data"]["target"]["health"], 100)
        self.assertEqual(message["data"]["target"]["health_max"], 100)

    def test_builder_set_rejects_player_current_resource_above_computed_max(self):
        target = self.create_player("Target", room=self.room)
        target.health = 1
        target.save(update_fields=["health"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/set {target.key} health 999999")

        target.refresh_from_db()
        self.assertEqual(target.health, 1)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("health cannot exceed health_max", message.get("text", ""))
        self.assertEqual(message["data"]["field"], "health")
        self.assertEqual(message["data"]["max_field"], "health_max")

    def test_builder_set_updates_room_mob_aggression_case_insensitively(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard aggression Aggressive")

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_ALL)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["field"], "aggression")
        self.assertEqual(
            message["data"]["previous_value"],
            api_consts.MOB_AGGRESSION_PASSIVE,
        )
        self.assertEqual(message["data"]["new_value"], api_consts.MOB_AGGRESSION_ALL)
        self.assertEqual(
            message["data"]["target"]["aggression"],
            api_consts.MOB_AGGRESSION_ALL,
        )

    def test_room_actor_set_updates_room_mob_aggression(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard aggression normal",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_NORMAL)
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["char_type"], "room")
        self.assertEqual(message["data"]["room"]["id"], self.room.id)
        self.assertEqual(message["data"]["target"]["key"], mob.key)
        self.assertEqual(
            message["data"]["previous_value"],
            api_consts.MOB_AGGRESSION_PASSIVE,
        )
        self.assertEqual(
            message["data"]["new_value"],
            api_consts.MOB_AGGRESSION_NORMAL,
        )

    def test_room_actor_set_updates_room_mob_text_fields(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            room_description="A training guard waits here.",
            description="The guard's uniform is immaculate.",
        )
        updates = (
            ("name", "The Awakened Guard"),
            (
                "room_description",
                "The awakened guard watches from beneath the archway.",
            ),
            (
                "description",
                "Old scars cross the awakened guard's weathered face.",
            ),
        )

        for field_name, value in updates:
            with self.subTest(field=field_name), capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    actor_type="room",
                    actor_id=self.room.id,
                    payload={
                        "text": f"/set guard {field_name} -- {value}",
                        "world_id": self.spawn_world.id,
                    },
                    script_source=True,
                )

            mob.refresh_from_db()
            self.assertEqual(getattr(mob, field_name), value)
            message = self._message_by_type(messages, "cmd./set.success")
            self.assertIsNotNone(message)
            self.assertEqual(message["data"]["target"]["key"], mob.key)
            self.assertEqual(message["data"]["field"], field_name)
            self.assertEqual(message["data"]["new_value"], value)

    def test_room_actor_set_rejects_invalid_mob_names(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
        )
        invalid_names = ("", "x" * 256)

        for invalid_name in invalid_names:
            with self.subTest(length=len(invalid_name)), capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    actor_type="room",
                    actor_id=self.room.id,
                    payload={
                        "text": f"/set guard name -- {invalid_name}",
                        "world_id": self.spawn_world.id,
                    },
                    script_source=True,
                )

            mob.refresh_from_db()
            self.assertEqual(mob.name, "Training Guard")
            message = self._message_by_type(messages, "cmd./set.error")
            self.assertIsNotNone(message)
            self.assertEqual(message["data"]["code"], "invalid_value")

    def test_room_actor_set_preserves_scalar_looking_description_text(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
        )
        descriptions = (
            "true",
            "null",
            '{"state":"watchful"}',
        )

        for description in descriptions:
            with self.subTest(description=description), capture_game_messages() as messages:
                dispatch_command(
                    command_type="text",
                    actor_type="room",
                    actor_id=self.room.id,
                    payload={
                        "text": f"/set guard description -- {description}",
                        "world_id": self.spawn_world.id,
                    },
                    script_source=True,
                )

            mob.refresh_from_db()
            self.assertEqual(mob.description, description)
            message = self._message_by_type(messages, "cmd./set.success")
            self.assertIsNotNone(message)
            self.assertEqual(message["data"]["new_value"], description)

    def test_room_actor_set_cannot_rename_player(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/set {target.key} name -- Renamed Player",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.name, "Target")
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "invalid_field")

    def test_room_actor_set_requires_local_unambiguous_target(self):
        first_guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="First Guard",
            keywords="guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )
        second_guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Second Guard",
            keywords="guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )
        far_room = self.room.create_at("east")
        far_guard = Mob.objects.create(
            world=self.spawn_world,
            room=far_room,
            name="Far Guard",
            keywords="far guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages() as ambiguous_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard aggression normal",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        ambiguous_error = self._message_by_type(ambiguous_messages, "cmd./set.error")
        self.assertIsNotNone(ambiguous_error)
        self.assertEqual(ambiguous_error["data"]["code"], "ambiguous_target")

        with capture_game_messages() as self_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set self aggression normal",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        self_error = self._message_by_type(self_messages, "cmd./set.error")
        self.assertIsNotNone(self_error)
        self.assertEqual(self_error["data"]["code"], "invalid_target")

        with capture_game_messages() as remote_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/set {far_guard.key} aggression normal",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        remote_error = self._message_by_type(remote_messages, "cmd./set.error")
        self.assertIsNotNone(remote_error)
        self.assertEqual(remote_error["data"]["code"], "invalid_target")

        for guard in (first_guard, second_guard, far_guard):
            guard.refresh_from_db()
            self.assertEqual(guard.aggression, api_consts.MOB_AGGRESSION_PASSIVE)

    def test_room_actor_set_requires_script_source(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard aggression normal",
                    "world_id": self.spawn_world.id,
                },
            )

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_PASSIVE)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_room_actor_set_rejects_unrelated_runtime_context(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )
        unrelated_config = WorldConfig.objects.create()
        unrelated_world = World.objects.new_world(
            name="Unrelated set world",
            author=self.user,
            config=unrelated_config,
        )
        unrelated_runtime = unrelated_world.create_spawn_world()

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard aggression normal",
                    "world_id": unrelated_runtime.id,
                },
                script_source=True,
            )

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_PASSIVE)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "invalid_world_context")

    def test_room_actor_set_notifies_player_target(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": f"/set {target.key} glory 7",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        target.refresh_from_db()
        self.assertEqual(target.glory, 7)
        notification = next(
            (
                msg["message"]
                for msg in messages
                if (
                    msg["player_key"] == target.key
                    and msg["message"].get("type") == "notification./set"
                )
            ),
            None,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["actor"]["key"], target.key)
        self.assertEqual(notification["data"]["issuer"]["char_type"], "room")
        self.assertEqual(notification["data"]["field"], "glory")
        self.assertEqual(notification["data"]["new_value"], 7)

    def test_room_actor_set_aggression_does_not_start_combat(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages():
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/set guard aggression players",
                    "world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_PLAYERS)
        self.assertFalse(
            CombatEncounter.objects.filter(mob=mob).exists(),
        )

    def test_builder_set_rejects_unknown_mob_aggression(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            aggression=api_consts.MOB_AGGRESSION_PASSIVE,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/set guard aggression Berserk")

        mob.refresh_from_db()
        self.assertEqual(mob.aggression, api_consts.MOB_AGGRESSION_PASSIVE)
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("aggression must be one of", message.get("text", ""))

    def test_builder_set_updates_global_player_attribute_key(self):
        far_room = self.room.create_at("east")
        target = self.create_player("RemotePlayer", room=far_room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/set {target.key} attribute.brawn 5")

        target.refresh_from_db()
        self.assertEqual(target.attributes, {"brawn": 5})
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "player")
        self.assertEqual(message["data"]["field"], "attributes.brawn")
        self.assertEqual(message["data"]["new_value"], 5)

    def test_builder_set_replaces_mob_attributes_with_delimited_json(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Training Guard",
            keywords="training guard",
            attributes={"old": 1},
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, '/set guard attributes -- {"strength": 4, "wit": 2}')

        mob.refresh_from_db()
        self.assertEqual(mob.attributes, {"strength": 4, "wit": 2})
        message = self._message_by_type(messages, "cmd./set.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["field"], "attributes")
        self.assertEqual(message["data"]["new_value"], {"strength": 4, "wit": 2})

    def test_builder_set_rejects_computed_player_stat(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/set {target.key} attack_power 12")

        target.refresh_from_db()
        message = self._message_by_type(messages, "cmd./set.error")
        self.assertIsNotNone(message)
        self.assertIn("computed", message.get("text", "").lower())


class TestBuilderEcho(BuilderCommandTestCase):
    def _messages_by_type(self, messages, message_type):
        return [msg for msg in messages if msg["message"].get("type") == message_type]

    def _messages_for_key_and_type(self, messages, player_key, message_type):
        return [
            msg
            for msg in messages
            if msg["player_key"] == player_key and msg["message"].get("type") == message_type
        ]

    def test_echo_room_scope_broadcasts_to_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/echo room -- A lantern flickers.")

        actor_success = self._messages_for_key_and_type(messages, self.player.key, "cmd./echo.success")
        self.assertEqual(len(actor_success), 1)
        self.assertEqual(actor_success[0]["message"].get("text"), "A lantern flickers.")
        self.assertEqual(actor_success[0]["message"].get("data", {}).get("scope"), "room")

        watcher_notify = self._messages_for_key_and_type(messages, watcher.key, "notification./echo")
        self.assertEqual(len(watcher_notify), 1)
        self.assertEqual(watcher_notify[0]["message"].get("text"), "A lantern flickers.")

    def test_echo_defaults_to_room_without_scope_or_delimiter(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/echo A stone falls.")

        actor_success = self._messages_for_key_and_type(messages, self.player.key, "cmd./echo.success")
        self.assertEqual(len(actor_success), 1)
        self.assertEqual(actor_success[0]["message"].get("data", {}).get("scope"), "room")
        self.assertEqual(actor_success[0]["message"].get("text"), "A stone falls.")

        watcher_notify = self._messages_for_key_and_type(messages, watcher.key, "notification./echo")
        self.assertEqual(len(watcher_notify), 1)
        self.assertEqual(watcher_notify[0]["message"].get("text"), "A stone falls.")

    def test_echo_supports_explicit_scope_without_delimiter(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        zone_room = self.room.create_at("east")
        zone_watcher = self.create_player("Zone Watcher", room=zone_room)
        zone_watcher.in_game = True
        zone_watcher.save(update_fields=["in_game"])

        outside_room = self.room.create_at("north")
        outside_room.zone = None
        outside_room.save(update_fields=["zone"])
        outside_watcher = self.create_player("Outside Watcher", room=outside_room)
        outside_watcher.in_game = True
        outside_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/echo zone The bells ring.")

        zone_notify = self._messages_for_key_and_type(messages, zone_watcher.key, "notification./echo")
        self.assertEqual(len(zone_notify), 1)
        self.assertEqual(zone_notify[0]["message"].get("data", {}).get("scope"), "zone")
        self.assertEqual(zone_notify[0]["message"].get("text"), "The bells ring.")

        outside_notify = self._messages_for_key_and_type(messages, outside_watcher.key, "notification./echo")
        self.assertEqual(outside_notify, [])

    def test_echo_accepts_unambiguous_scope_prefix(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        zone_room = self.room.create_at("east")
        zone_watcher = self.create_player("Zone Watcher", room=zone_room)
        zone_watcher.in_game = True
        zone_watcher.save(update_fields=["in_game"])

        outside_room = self.room.create_at("north")
        outside_room.zone = None
        outside_room.save(update_fields=["zone"])
        outside_watcher = self.create_player("Outside Watcher", room=outside_room)
        outside_watcher.in_game = True
        outside_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/echo z The bells ring.")

        zone_notify = self._messages_for_key_and_type(messages, zone_watcher.key, "notification./echo")
        self.assertEqual(len(zone_notify), 1)
        self.assertEqual(zone_notify[0]["message"].get("data", {}).get("scope"), "zone")

        outside_notify = self._messages_for_key_and_type(messages, outside_watcher.key, "notification./echo")
        self.assertEqual(outside_notify, [])

    def test_zecho_alias_targets_zone(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        zone_room = self.room.create_at("east")
        zone_watcher = self.create_player("Zone Watcher", room=zone_room)
        zone_watcher.in_game = True
        zone_watcher.save(update_fields=["in_game"])

        outside_room = self.room.create_at("north")
        outside_room.zone = None
        outside_room.save(update_fields=["zone"])
        outside_watcher = self.create_player("Outside Watcher", room=outside_room)
        outside_watcher.in_game = True
        outside_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/zecho -- The zone trembles.")

        zone_notify = self._messages_for_key_and_type(messages, zone_watcher.key, "notification./echo")
        self.assertEqual(len(zone_notify), 1)
        self.assertEqual(zone_notify[0]["message"].get("data", {}).get("scope"), "zone")

        outside_notify = self._messages_for_key_and_type(messages, outside_watcher.key, "notification./echo")
        self.assertEqual(outside_notify, [])

    def test_wecho_alias_targets_world(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        room_watcher = self.create_player("Room Watcher", room=self.room)
        room_watcher.in_game = True
        room_watcher.save(update_fields=["in_game"])

        far_room = self.room.create_at("south")
        far_room.zone = None
        far_room.save(update_fields=["zone"])
        far_watcher = self.create_player("Far Watcher", room=far_room)
        far_watcher.in_game = True
        far_watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/wecho The world trembles.")

        room_notify = self._messages_for_key_and_type(messages, room_watcher.key, "notification./echo")
        far_notify = self._messages_for_key_and_type(messages, far_watcher.key, "notification./echo")
        self.assertEqual(len(room_notify), 1)
        self.assertEqual(len(far_notify), 1)
        self.assertEqual(room_notify[0]["message"].get("data", {}).get("scope"), "world")
        self.assertEqual(far_notify[0]["message"].get("data", {}).get("scope"), "world")


class TestBuilderState(BuilderCommandTestCase):
    def _messages_by_type(self, messages, message_type):
        return [msg for msg in messages if msg["message"].get("type") == message_type]

    def test_state_set_get_add_and_clear_updates_scoped_state(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/state set world weather -- rainy")
            dispatch_text_command(self.player.id, "/state set room lever_pulled true")
            dispatch_text_command(self.player.id, "/state add character self rumor_count 2")
            dispatch_text_command(self.player.id, "/state get world weather")
            dispatch_text_command(self.player.id, "/state clear room lever_pulled")

        success_messages = self._messages_by_type(messages, "cmd./state.success")
        self.assertEqual(len(success_messages), 5)
        self.assertIn("world.weather = rainy", success_messages[3]["message"].get("text", ""))

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, self.spawn_world).get("weather"),
            "rainy",
        )
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player).get("rumor_count"),
            2,
        )
        self.assertNotIn(
            "lever_pulled",
            get_state_snapshot(STATE_SCOPE_ROOM, self.room),
        )

    def test_state_target_sets_room_player_character_state(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/state set character {target.key} pull_lever true")
            dispatch_text_command(self.player.id, f"/state get character {target.key} pull_lever")
            dispatch_text_command(self.player.id, f"/state clear character {target.key} pull_lever")

        success_messages = self._messages_by_type(messages, "cmd./state.success")
        self.assertEqual(len(success_messages), 3)
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, target).get("pull_lever"),
            None,
        )
        self.assertNotIn(
            "pull_lever",
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player),
        )
        self.assertIn("character.pull_lever = true", success_messages[1]["message"].get("text", ""))

    def test_state_target_set_supports_value_delimiter(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages():
            dispatch_text_command(
                self.player.id,
                f"/state set character {target.key} lever_note -- pulled at the west altar",
            )

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, target).get("lever_note"),
            "pulled at the west altar",
        )

    def test_cmd_room_state_target_sets_triggering_player_character_state(self):
        target = self.create_player("TriggerTarget", room=self.room)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=target.id,
                payload={
                    "text": f"/cmd room -- /state set character {target.key} pull_lever true"
                },
                script_source=True,
            )

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, target).get("pull_lever"),
            True,
        )
        cmd_messages = self._messages_by_type(messages, "cmd./cmd.success")
        self.assertEqual(len(cmd_messages), 1)
        self.assertEqual(cmd_messages[0]["message"]["data"]["errors"], [])

    def test_state_rejects_legacy_target_option(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/state set room --target {target.key} pull_lever true")

        error_messages = self._messages_by_type(messages, "cmd./state.error")
        self.assertEqual(len(error_messages), 1)
        self.assertIn("Usage:", error_messages[0]["message"].get("text", ""))
        self.assertNotIn("pull_lever", get_state_snapshot(STATE_SCOPE_ROOM, self.room))

    def test_state_character_requires_target(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/state set character pull_lever true")

        error_messages = self._messages_by_type(messages, "cmd./state.error")
        self.assertEqual(len(error_messages), 1)
        self.assertIn("character <target>", error_messages[0]["message"].get("text", ""))
        self.assertNotIn("pull_lever", get_state_snapshot(STATE_SCOPE_CHARACTER, self.player))

    def test_state_target_supports_mob_character_state(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/state set character {mob.key} captive true",
            )
            dispatch_text_command(
                self.player.id,
                f"/state add character {mob.key} escape_attempts 2",
            )
            dispatch_text_command(
                self.player.id,
                f"/state get character {mob.key} captive",
            )

        success_messages = self._messages_by_type(messages, "cmd./state.success")
        self.assertEqual(len(success_messages), 3)
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, mob),
            {"captive": True, "escape_attempts": 2},
        )
        self.assertIn(
            "character.captive = true",
            success_messages[-1]["message"].get("text", ""),
        )

    def test_echo_renders_state_template(self):
        dispatch_text_command(self.player.id, "/state set world weather -- windy")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/echo The weather is {{ state.world.weather }}.")

        success_messages = self._messages_by_type(messages, "cmd./echo.success")
        self.assertEqual(len(success_messages), 1)
        self.assertEqual(
            success_messages[0]["message"].get("text"),
            "The weather is windy.",
        )


class TestBuilderCmd(BuilderCommandTestCase):
    def _messages_by_type(self, messages, message_type):
        return [msg for msg in messages if msg["message"].get("type") == message_type]

    def _messages_for_key_and_type(self, messages, player_key, message_type):
        return [
            msg
            for msg in messages
            if msg["player_key"] == player_key and msg["message"].get("type") == message_type
        ]

    def test_builder_cannot_cmd_mob_to_run_builder_cmd(self):
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target",
        )
        victim = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Victim Mob",
            keywords="victim",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/cmd {target.key} -- /cmd {victim.key} -- dance",
            )

        builder_success = self._messages_for_key_and_type(messages, self.player.key, "cmd./cmd.success")
        self.assertEqual(len(builder_success), 1)
        self.assertIn("permission", builder_success[0]["message"].get("text", "").lower())

        mob_success = self._messages_for_key_and_type(messages, target.key, "cmd./cmd.success")
        self.assertEqual(mob_success, [])

    def test_mob_cannot_use_cmd_without_builder_permissions(self):
        first_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="First Mob",
            keywords="first",
        )
        second_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Second Mob",
            keywords="second",
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(first_mob.id, f"/cmd {second_mob.key} -- dance")

        mob_success = self._messages_for_key_and_type(messages, first_mob.key, "cmd./cmd.success")
        self.assertEqual(mob_success, [])
        mob_error = self._messages_for_key_and_type(messages, first_mob.key, "cmd./cmd.error")
        self.assertEqual(len(mob_error), 1)
        self.assertIn("permission", mob_error[0]["message"].get("text", "").lower())

    def test_cmd_can_trigger_mob_say_and_emote(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"/cmd {target.key} -- say hello there && emote salutes.",
            )

        cmd_success = self._messages_for_key_and_type(messages, self.player.key, "cmd./cmd.success")
        self.assertEqual(len(cmd_success), 1)
        self.assertFalse(cmd_success[0]["message"].get("text"))

        say_notify = self._messages_for_key_and_type(
            messages,
            watcher.key,
            "notification.cmd.say.success",
        )
        emote_notify = self._messages_for_key_and_type(
            messages,
            watcher.key,
            "notification.cmd.emote.success",
        )
        self.assertEqual(len(say_notify), 1)
        self.assertEqual(len(emote_notify), 1)
        self.assertEqual(say_notify[0]["message"].get("text"), "Target Mob says 'hello there'")
        self.assertEqual(emote_notify[0]["message"].get("text"), "Target Mob salutes.")

    def test_cmd_requires_delimiter(self):
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/cmd {target.key} say hello")

        cmd_errors = self._messages_for_key_and_type(messages, self.player.key, "cmd./cmd.error")
        self.assertEqual(len(cmd_errors), 1)
        self.assertIn("usage", cmd_errors[0]["message"].get("text", "").lower())

    def test_force_alias_uses_cmd_routing(self):
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/force {target.key} -- dance")

        cmd_success = self._messages_for_key_and_type(messages, self.player.key, "cmd./cmd.success")
        self.assertEqual(len(cmd_success), 1)
        self.assertIn("unknown command", cmd_success[0]["message"].get("text", "").lower())
