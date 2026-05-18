from config import constants as adv_consts
from builders.models import ItemTemplate
from spawns.handlers import dispatch_command
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

    def test_inventory_short_alias_i_resolves_to_inventory_not_inspect(self):
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
            dispatch_text_command(self.player.id, "i")

        inventory_message = self._message_by_type(messages, "cmd.inventory.success")
        self.assertIsNotNone(inventory_message)
        self.assertIn("Apple", inventory_message["text"])
        self.assertIsNone(self._message_by_type(messages, "cmd.inspect.success"))
        self.assertIsNone(self._message_by_type(messages, "cmd.inspect.error"))


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


class TestGiveCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_give_moves_item_to_mob_and_notifies_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        guard = self.create_mob("Quartermaster", keywords="quartermaster guard")

        item_template = ItemTemplate.objects.create(world=self.world, name="Pelt")
        pelt = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=item_template,
            name=item_template.name,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "give pelt quartermaster")

        pelt.refresh_from_db()
        self.assertEqual(pelt.container_id, guard.id)

        message = self._message_by_type(messages, "cmd.give.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["id"], guard.id)
        self.assertIsNotNone(self._message_by_type(messages, "notification.cmd.give.success"))


class TestEquipmentCommands(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _make_equipment_item(self, name, equipment_type):
        template = ItemTemplate.objects.create(
            world=self.world,
            name=name,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=equipment_type,
        )
        return Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=equipment_type,
        )

    def test_equip_moves_inventory_item_to_equipment(self):
        helmet = self._make_equipment_item(
            "Iron Helmet",
            adv_consts.EQUIPMENT_TYPE_HEAD,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "equip helmet")

        helmet.refresh_from_db()
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.head_id, helmet.id)
        self.assertEqual(helmet.container_id, self.player.equipment.id)

        message = self._message_by_type(messages, "cmd.equip.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["actor"]["equipment"]["head"]["key"], helmet.key)
        self.assertIn("You wear Iron Helmet on your head.", message.get("text", ""))

    def test_wield_swaps_existing_weapon(self):
        sword = self._make_equipment_item(
            "Short Sword",
            adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        )
        axe = self._make_equipment_item(
            "War Axe",
            adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        )
        self.player.equipment.equip(sword, adv_consts.EQUIPMENT_SLOT_WEAPON)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "wield axe")

        sword.refresh_from_db()
        axe.refresh_from_db()
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, axe.id)
        self.assertEqual(axe.container_id, self.player.equipment.id)
        self.assertEqual(sword.container_id, self.player.id)

        message = self._message_by_type(messages, "cmd.wield.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message["data"]["swapped_items"][0]["removed"]["key"],
            sword.key,
        )
        self.assertIn("You swap Short Sword for War Axe.", message.get("text", ""))

    def test_remove_moves_equipped_item_to_inventory_and_notifies_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        boots = self._make_equipment_item(
            "Trail Boots",
            adv_consts.EQUIPMENT_TYPE_FEET,
        )
        self.player.equipment.equip(boots, adv_consts.EQUIPMENT_SLOT_FEET)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "remove boots")

        boots.refresh_from_db()
        self.player.equipment.refresh_from_db()
        self.assertIsNone(self.player.equipment.feet_id)
        self.assertEqual(boots.container_id, self.player.id)

        message = self._message_by_type(messages, "cmd.remove.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["items"][0]["key"], boots.key)
        self.assertIn("You stop using Trail Boots.", message.get("text", ""))
        self.assertIsNotNone(self._message_by_type(messages, "notification.cmd.remove.success"))

    def test_structured_wear_command_accepts_item_key_payload(self):
        gloves = self._make_equipment_item(
            "Work Gloves",
            adv_consts.EQUIPMENT_TYPE_HANDS,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                "wear",
                player_id=self.player.id,
                payload={"item": {"key": gloves.key}},
            )

        gloves.refresh_from_db()
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.hands_id, gloves.id)
        self.assertIsNotNone(self._message_by_type(messages, "cmd.wear.success"))
