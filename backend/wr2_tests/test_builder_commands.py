import json

from django.contrib.contenttypes.models import ContentType

from builders.models import (
    AbilityDefinition,
    ItemDefinition,
    ItemTemplate,
    MobTemplate,
    Trigger,
)
from config import constants as api_consts
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    get_state_snapshot,
)
from spawns.handlers import dispatch_command, get_registered_handlers
from spawns.models import CombatEncounter, Item, Mob
from tests.base import WorldTestCase
from wr2_tests.utils import (
    capture_game_messages,
    dispatch_text_command,
    dispatch_text_command_as_mob,
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

    def test_script_source_does_not_allow_player_state_commands(self):
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/state set character pull_lever true"},
                script_source=True,
            )

        self.assertNotIn(
            "pull_lever",
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player),
        )
        message = self._message_by_type(messages, "cmd./state.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())


class BuilderCommandTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])


class TestBuilderLoad(BuilderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.item_template = ItemTemplate.objects.create(
            world=self.world,
            name="Test Item",
        )
        self.mob_template = MobTemplate.objects.create(
            world=self.world,
            name="Test Mob",
        )

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_load_item_adds_inventory(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/lo item {self.item_template.id}")

        loaded_item = self.player.inventory.get(template=self.item_template)
        self.assertTrue(
            self.player.inventory.filter(
                template=self.item_template,
            ).exists()
        )
        self.assertEqual(loaded_item.name, self.item_template.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.item_template.name,
        )
        self.assertTrue(message.get("text"))

    def test_load_mob_adds_room_mob(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/lo mob {self.mob_template.id}")

        loaded_mob = Mob.objects.get(
            template=self.mob_template,
            room=self.room,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_mob.name, self.mob_template.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.mob_template.name,
        )
        self.assertTrue(message.get("text"))

    def test_load_accepts_unambiguous_template_type_prefix(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load i {self.item_template.id}")

        loaded_item = self.player.inventory.get(template=self.item_template)
        self.assertEqual(loaded_item.name, self.item_template.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("data", {}).get("loaded", {}).get("name"), self.item_template.name)

    def test_load_item_accepts_template_slug(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load item {self.item_template.slug}")

        loaded_item = self.player.inventory.get(template=self.item_template)
        self.assertEqual(loaded_item.name, self.item_template.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("data", {}).get("loaded", {}).get("name"), self.item_template.name)

    def test_load_item_accepts_item_definition_slug(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="definition-sword",
            name="a definition sword",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/load item definition-sword")

        loaded_item = self.player.inventory.get(definition=item_definition)
        self.assertEqual(loaded_item.name, item_definition.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            item_definition.name,
        )

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

    def test_load_mob_accepts_template_slug(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/load mob {self.mob_template.slug}")

        loaded_mob = Mob.objects.get(
            template=self.mob_template,
            room=self.room,
            world=self.spawn_world,
        )
        self.assertEqual(loaded_mob.name, self.mob_template.name)
        message = self._message_by_type(messages, "cmd./load.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("data", {}).get("loaded", {}).get("name"),
            self.mob_template.name,
        )

    def test_load_requires_builder(self):
        other_user = self.create_user("other@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, f"/lo item {self.item_template.id}")

        self.assertFalse(
            other_player.inventory.filter(
                template=self.item_template,
            ).exists()
        )
        message = self._message_by_type(messages, "cmd./load.error")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))


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


class TestBuilderPurge(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_purge_all_removes_room_items_and_mobs(self):
        item_template = ItemTemplate.objects.create(world=self.world, name="Trash")
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=item_template,
            name=item_template.name,
        )
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
        item_template = ItemTemplate.objects.create(world=self.world, name="Pebble")
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=item_template,
            name=item_template.name,
        )
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

        with capture_game_messages():
            dispatch_text_command(self.player.id, "/purge mobs")

        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertFalse(
            self.room.inventory.filter(
                world=self.spawn_world,
                type=api_consts.ITEM_TYPE_CORPSE,
            ).exists()
        )

    def test_purge_target_can_remove_inventory_item(self):
        item_template = ItemTemplate.objects.create(world=self.world, name="Relic")
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=item_template,
            name=item_template.name,
            keywords="relic",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/purge relic")

        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        message = self._message_by_type(messages, "cmd./purge.success")
        self.assertIsNotNone(message)
        self.assertIn("You purge Relic from this world.", message.get("text", ""))

    def test_purge_requires_builder(self):
        other_user = self.create_user("other-builder@example.com")
        other_player = self.create_player("Other", user=other_user)

        item_template = ItemTemplate.objects.create(world=self.world, name="Crate")
        item = Item.objects.create(
            world=self.spawn_world,
            container=other_player.room,
            template=item_template,
            name=item_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "/purge")

        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        message = self._message_by_type(messages, "cmd./purge.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())


class TestBuilderJump(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type):
        return [msg for msg in messages if msg["message"].get("type") == message_type]

    def test_jump_moves_player_to_target_room(self):
        target_room = self.room.create_at("east")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/jump {target_room.relative_id}")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, target_room.id)

        message = self._message_by_type(messages, "cmd./jump.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "room")
        self.assertEqual(message["data"]["target"]["id"], target_room.id)
        self.assertEqual(message["data"]["target"]["key"], f"room.{target_room.relative_id}")
        self.assertIn("satisfying thump", message.get("text", "").lower())

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

    def test_jump_requires_builder(self):
        target_room = self.room.create_at("east")
        other_user = self.create_user("other-jump@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, f"/jump {target_room.relative_id}")

        other_player.refresh_from_db()
        self.assertEqual(other_player.room_id, self.room.id)
        message = self._message_by_type(messages, "cmd./jump.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

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

    def test_setlevel_requires_builder_permissions(self):
        other_user = self.create_user("other-setlevel@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "/setlevel 3")

        other_player.refresh_from_db()
        self.assertEqual(other_player.level, 1)
        message = self._message_by_type(messages, "cmd./setlevel.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())


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
            action_type="primary",
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
            pending_player_ability={"ability": "power-strike"},
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

    def test_setclass_requires_builder_permissions(self):
        other_user = self.create_user("other-setclass@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "/setclass hoplite")

        other_player.refresh_from_db()
        self.assertNotEqual(other_player.archetype, "hoplite")
        message = self._message_by_type(messages, "cmd./setclass.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

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


class TestBuilderResync(BuilderCommandTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_resync_item_template_updates_existing_instances(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="a sword",
            description="A plain blade.",
            keywords="sword",
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, f"/load item {template.id}")

        spawned_item = self.player.inventory.get(template=template)
        template.name = "a magic sword"
        template.description = "A blade humming with magic."
        template.keywords = "magic sword"
        template.save(update_fields=["name", "description", "keywords"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/resync item {template.id}")

        spawned_item.refresh_from_db()
        self.assertEqual(spawned_item.name, "a magic sword")
        self.assertEqual(spawned_item.description, "A blade humming with magic.")
        self.assertEqual(spawned_item.keywords, "magic sword")

        message = self._message_by_type(messages, "cmd./resync.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "item")
        self.assertEqual(message["data"]["template"]["id"], template.id)
        self.assertEqual(message["data"]["updated"], 1)

    def test_resync_accepts_unambiguous_template_type_prefix(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="a sword",
            description="A plain blade.",
            keywords="sword",
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, f"/load item {template.id}")

        spawned_item = self.player.inventory.get(template=template)
        template.name = "a magic sword"
        template.save(update_fields=["name"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/resync i {template.id}")

        spawned_item.refresh_from_db()
        self.assertEqual(spawned_item.name, "a magic sword")
        message = self._message_by_type(messages, "cmd./resync.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "item")

    def test_resync_mob_template_updates_existing_instances(self):
        template = MobTemplate.objects.create(
            world=self.world,
            name="a soldier",
            room_description="A soldier stands guard here.",
            keywords="soldier",
        )

        with capture_game_messages():
            dispatch_text_command(self.player.id, f"/load mob {template.id}")

        spawned_mob = Mob.objects.get(
            world=self.spawn_world,
            room=self.room,
            template=template,
        )

        template.name = "a knight"
        template.room_description = "A knight stands guard here."
        template.keywords = "knight"
        template.save(update_fields=["name", "room_description", "keywords"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/resync mob {template.id}")

        spawned_mob.refresh_from_db()
        self.assertEqual(spawned_mob.name, "a knight")
        self.assertEqual(spawned_mob.room_description, "A knight stands guard here.")
        self.assertEqual(spawned_mob.keywords, "knight")

        message = self._message_by_type(messages, "cmd./resync.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "mob")
        self.assertEqual(message["data"]["template"]["id"], template.id)
        self.assertEqual(message["data"]["updated"], 1)

    def test_resync_all_templates_updates_multiple_items(self):
        first_template = ItemTemplate.objects.create(world=self.world, name="a sword")
        second_template = ItemTemplate.objects.create(world=self.world, name="a shield")

        first_item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=first_template,
            name="old sword",
        )
        second_item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=second_template,
            name="old shield",
        )

        first_template.name = "a runed sword"
        first_template.save(update_fields=["name"])
        second_template.name = "a tower shield"
        second_template.save(update_fields=["name"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/resync item all")

        first_item.refresh_from_db()
        second_item.refresh_from_db()
        self.assertEqual(first_item.name, "a runed sword")
        self.assertEqual(second_item.name, "a tower shield")

        message = self._message_by_type(messages, "cmd./resync.success")
        self.assertIsNotNone(message)
        self.assertGreaterEqual(message["data"]["updated"], 2)

    def test_resync_all_mob_templates_updates_multiple_mobs(self):
        first_template = MobTemplate.objects.create(world=self.world, name="a soldier")
        second_template = MobTemplate.objects.create(world=self.world, name="a guard")

        first_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            template=first_template,
            name="old soldier",
            description="old desc",
        )
        second_mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            template=second_template,
            name="old guard",
            description="old desc",
        )

        first_template.name = "a veteran soldier"
        first_template.save(update_fields=["name"])
        second_template.name = "a royal guard"
        second_template.save(update_fields=["name"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/resync mob all")

        first_mob.refresh_from_db()
        second_mob.refresh_from_db()
        self.assertEqual(first_mob.name, "a veteran soldier")
        self.assertEqual(second_mob.name, "a royal guard")

        message = self._message_by_type(messages, "cmd./resync.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target_type"], "mob")
        self.assertGreaterEqual(message["data"]["updated"], 2)

    def test_resync_requires_builder_permissions(self):
        other_user = self.create_user("other-resync@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "/resync item all")

        message = self._message_by_type(messages, "cmd./resync.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_resync_rejects_invalid_template(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/resync item 999999")

        message = self._message_by_type(messages, "cmd./resync.error")
        self.assertIsNotNone(message)
        self.assertIn("template does not belong", message.get("text", "").lower())


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
            dispatch_text_command(self.player.id, "/state add character rumor_count 2")
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
            dispatch_text_command(self.player.id, f"/state set character --target {target.key} pull_lever true")
            dispatch_text_command(self.player.id, f"/state get character --target {target.key} pull_lever")
            dispatch_text_command(self.player.id, f"/state clear character --target {target.key} pull_lever")

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
                f"/state set character --target {target.key} lever_note -- pulled at the west altar",
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
                    "text": f"/cmd room -- /state set character --target {target.key} pull_lever true"
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

    def test_state_target_rejects_non_character_scope(self):
        target = self.create_player("Target", room=self.room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/state set room --target {target.key} pull_lever true")

        error_messages = self._messages_by_type(messages, "cmd./state.error")
        self.assertEqual(len(error_messages), 1)
        self.assertIn("--target", error_messages[0]["message"].get("text", ""))
        self.assertNotIn("pull_lever", get_state_snapshot(STATE_SCOPE_ROOM, self.room))

    def test_state_target_rejects_mob_character_state(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Quartermaster",
            keywords="quartermaster",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/state set character --target {mob.key} pull_lever true")

        error_messages = self._messages_by_type(messages, "cmd./state.error")
        self.assertEqual(len(error_messages), 1)
        self.assertIn("players", error_messages[0]["message"].get("text", ""))

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

    def test_cmd_requires_builder_permissions_for_players(self):
        target = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Target Mob",
            keywords="target",
        )
        other_user = self.create_user("other-force@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, f"/cmd {target.key} -- look")

        cmd_errors = self._messages_by_type(messages, "cmd./cmd.error")
        self.assertEqual(len(cmd_errors), 1)
        self.assertEqual(cmd_errors[0]["player_key"], other_player.key)
        self.assertIn("permission", cmd_errors[0]["message"].get("text", "").lower())

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
