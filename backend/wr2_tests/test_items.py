from config import constants as adv_consts
from builders.models import ItemTemplate
from spawns.models import Item
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestInventoryCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_inventory_lists_items_and_text(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="Apple",
        )
        Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "inv")

        message = self._message_by_type(messages, "cmd.inventory.success")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))
        self.assertIn("Apple", message["text"])

    def test_inventory_prefers_template_name_when_instance_name_is_default(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="Steel Dagger",
        )
        Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "inv")

        message = self._message_by_type(messages, "cmd.inventory.success")
        self.assertIsNotNone(message)
        self.assertIn("Steel Dagger", message["text"])
        self.assertNotIn("Unnamed Item", message["text"])


class TestDropCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_drop_moves_item_and_notifies_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        template = ItemTemplate.objects.create(
            world=self.world,
            name="Lantern",
        )
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "drop lantern")

        item.refresh_from_db()
        self.assertEqual(item.container_id, self.room.id)
        self.assertFalse(self.player.inventory.filter(pk=item.id).exists())

        self.assertIsNotNone(self._message_by_type(messages, "cmd.drop.success"))
        self.assertIsNotNone(self._message_by_type(messages, "notification.cmd.drop.success"))

    def test_drop_success_room_key_matches_actor_room_key(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.room.relative_id = self.room.id + 3000
        self.room.save(update_fields=["relative_id"])

        template = ItemTemplate.objects.create(
            world=self.world,
            name="Compass",
        )
        Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "drop compass")

        message = self._message_by_type(messages, "cmd.drop.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["room"]["key"], message["data"]["actor"]["room"]["key"])
        self.assertEqual(message["data"]["room"]["key"], f"room.{self.room.relative_id}")

    def test_drop_matches_template_name_when_instance_name_is_default(self):
        template = ItemTemplate.objects.create(
            world=self.world,
            name="Bronze Ring",
        )
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "drop ring")

        item.refresh_from_db()
        self.assertEqual(item.container_id, self.room.id)
        message = self._message_by_type(messages, "cmd.drop.success")
        self.assertIsNotNone(message)


class TestGetCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_get_moves_item_to_inventory_and_notifies_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        template = ItemTemplate.objects.create(world=self.world, name="Lantern")
        item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=template,
            name=template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "get lantern")

        item.refresh_from_db()
        self.assertEqual(item.container_id, self.player.id)
        self.assertTrue(self.player.inventory.filter(pk=item.id).exists())

        message = self._message_by_type(messages, "cmd.get.success")
        self.assertIsNotNone(message)
        self.assertIsNotNone(self._message_by_type(messages, "notification.cmd.get.success"))
        self.assertEqual(message["data"]["room"]["key"], message["data"]["actor"]["room"]["key"])

    def test_get_from_room_container(self):
        container_template = ItemTemplate.objects.create(
            world=self.world,
            name="Chest",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        chest = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=container_template,
            name=container_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
            is_pickable=False,
        )

        item_template = ItemTemplate.objects.create(world=self.world, name="Apple")
        item = Item.objects.create(
            world=self.spawn_world,
            container=chest,
            template=item_template,
            name=item_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "get apple chest")

        item.refresh_from_db()
        self.assertEqual(item.container_id, self.player.id)

        message = self._message_by_type(messages, "cmd.get.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["source"]["key"], chest.key)


class TestPutCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_put_moves_item_to_room_container_and_notifies_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name="Bag",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        bag = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            template=bag_template,
            name=bag_template.name,
            type=adv_consts.ITEM_TYPE_CONTAINER,
            is_pickable=False,
        )

        item_template = ItemTemplate.objects.create(world=self.world, name="Coin")
        coin = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=item_template,
            name=item_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "put coin bag")

        coin.refresh_from_db()
        self.assertEqual(coin.container_id, bag.id)

        message = self._message_by_type(messages, "cmd.put.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], bag.key)
        self.assertIsNotNone(self._message_by_type(messages, "notification.cmd.put.success"))

    def test_put_requires_container_argument(self):
        item_template = ItemTemplate.objects.create(world=self.world, name="Coin")
        Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=item_template,
            name=item_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "put coin")

        error_message = self._message_by_type(messages, "cmd.put.error")
        self.assertIsNotNone(error_message)
        self.assertIn("Put where?", error_message.get("text", ""))
