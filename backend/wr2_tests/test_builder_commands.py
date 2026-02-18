from builders.models import ItemTemplate, MobTemplate
from spawns.models import Item, Mob
from tests.base import WorldTestCase
from wr2_tests.utils import (
    capture_game_messages,
    dispatch_text_command,
    dispatch_text_command_as_mob,
)


class TestBuilderLoad(WorldTestCase):
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


class TestBuilderPurge(WorldTestCase):
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


class TestBuilderJump(WorldTestCase):
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


class TestBuilderResync(WorldTestCase):
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


class TestBuilderEcho(WorldTestCase):
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


class TestBuilderCmd(WorldTestCase):
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

    def test_builder_can_cmd_mob_to_run_cmd(self):
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
        self.assertFalse(builder_success[0]["message"].get("text"))

        mob_success = self._messages_for_key_and_type(messages, target.key, "cmd./cmd.success")
        self.assertEqual(len(mob_success), 1)
        self.assertIn("unknown command", mob_success[0]["message"].get("text", "").lower())

    def test_mob_can_use_cmd_without_builder_permissions(self):
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
        self.assertEqual(len(mob_success), 1)
        self.assertIn("unknown command", mob_success[0]["message"].get("text", "").lower())

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
