from spawns.models import Mob
from tests.base import WorldTestCase
from wr2_tests.utils import (
    capture_game_messages,
    dispatch_text_command,
    dispatch_text_command_as_mob,
)


class TestSayCommands(WorldTestCase):
    def _message_entry(self, messages, message_type, player_key):
        for msg in messages:
            if msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg
        return None

    def test_say_sends_actor_and_room_notifications(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "say hello there")

        actor_msg = self._message_entry(messages, "cmd.say.success", self.player.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["data"]["text"], "hello there")
        self.assertEqual(actor_msg["message"]["text"], "You say 'hello there'")

        notify_msg = self._message_entry(messages, "notification.cmd.say.success", watcher.key)
        self.assertIsNotNone(notify_msg)
        self.assertEqual(notify_msg["message"]["data"]["text"], "hello there")
        self.assertEqual(notify_msg["message"]["text"], "Joe says 'hello there'")

    def test_say_requires_message(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "say")

        error_msg = self._message_entry(messages, "cmd.say.error", self.player.key)
        self.assertIsNotNone(error_msg)
        self.assertIn("say what", error_msg["message"].get("text", "").lower())

    def test_mob_say_notifies_players_in_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(mob.id, "say halt")

        actor_msg = self._message_entry(messages, "cmd.say.success", mob.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["text"], "You say 'halt'")

        notify_player = self._message_entry(messages, "notification.cmd.say.success", self.player.key)
        notify_watcher = self._message_entry(messages, "notification.cmd.say.success", watcher.key)
        self.assertIsNotNone(notify_player)
        self.assertIsNotNone(notify_watcher)
        self.assertEqual(notify_player["message"]["text"], "Guard says 'halt'")
        self.assertEqual(notify_watcher["message"]["text"], "Guard says 'halt'")


class TestYellCommands(WorldTestCase):
    def _message_entry(self, messages, message_type, player_key):
        for msg in messages:
            if msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg
        return None

    def test_yell_sends_actor_and_zone_notifications(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        room_watcher = self.create_player("Room Watcher", room=self.room)
        room_watcher.in_game = True
        room_watcher.save(update_fields=["in_game"])

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
            dispatch_text_command(self.player.id, "yell hello there")

        actor_msg = self._message_entry(messages, "cmd.yell.success", self.player.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["data"]["text"], "hello there")
        self.assertEqual(actor_msg["message"]["text"], "You yell 'hello there'")

        room_notify = self._message_entry(messages, "notification.cmd.yell.success", room_watcher.key)
        self.assertIsNotNone(room_notify)
        self.assertEqual(room_notify["message"]["text"], "Joe yells 'hello there'")

        zone_notify = self._message_entry(messages, "notification.cmd.yell.success", zone_watcher.key)
        self.assertIsNotNone(zone_notify)
        self.assertEqual(zone_notify["message"]["text"], "Joe yells 'hello there'")

        outside_notify = self._message_entry(
            messages,
            "notification.cmd.yell.success",
            outside_watcher.key,
        )
        self.assertIsNone(outside_notify)

    def test_yell_requires_message(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "yell")

        error_msg = self._message_entry(messages, "cmd.yell.error", self.player.key)
        self.assertIsNotNone(error_msg)
        self.assertIn("yell", error_msg["message"].get("text", "").lower())

    def test_mob_yell_notifies_players_in_zone(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        room_watcher = self.create_player("Room Watcher", room=self.room)
        room_watcher.in_game = True
        room_watcher.save(update_fields=["in_game"])

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

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(mob.id, "yell halt")

        actor_msg = self._message_entry(messages, "cmd.yell.success", mob.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["text"], "You yell 'halt'")

        player_notify = self._message_entry(messages, "notification.cmd.yell.success", self.player.key)
        room_notify = self._message_entry(messages, "notification.cmd.yell.success", room_watcher.key)
        zone_notify = self._message_entry(messages, "notification.cmd.yell.success", zone_watcher.key)
        self.assertIsNotNone(player_notify)
        self.assertIsNotNone(room_notify)
        self.assertIsNotNone(zone_notify)
        self.assertEqual(player_notify["message"]["text"], "Guard yells 'halt'")
        self.assertEqual(room_notify["message"]["text"], "Guard yells 'halt'")
        self.assertEqual(zone_notify["message"]["text"], "Guard yells 'halt'")

        outside_notify = self._message_entry(
            messages,
            "notification.cmd.yell.success",
            outside_watcher.key,
        )
        self.assertIsNone(outside_notify)


class TestEmoteCommands(WorldTestCase):
    def _message_entry(self, messages, message_type, player_key):
        for msg in messages:
            if msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg
        return None

    def test_emote_sends_actor_and_room_notifications(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "emote smiles warmly.")

        actor_msg = self._message_entry(messages, "cmd.emote.success", self.player.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["data"]["text"], "smiles warmly.")
        self.assertEqual(actor_msg["message"]["text"], "Joe smiles warmly.")

        notify_msg = self._message_entry(messages, "notification.cmd.emote.success", watcher.key)
        self.assertIsNotNone(notify_msg)
        self.assertEqual(notify_msg["message"]["data"]["text"], "smiles warmly.")
        self.assertEqual(notify_msg["message"]["text"], "Joe smiles warmly.")

    def test_emote_requires_message(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "emote")

        error_msg = self._message_entry(messages, "cmd.emote.error", self.player.key)
        self.assertIsNotNone(error_msg)
        self.assertIn("express", error_msg["message"].get("text", "").lower())

    def test_mob_emote_notifies_players_in_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Scout",
            keywords="scout",
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(mob.id, "emote nods.")

        actor_msg = self._message_entry(messages, "cmd.emote.success", mob.key)
        self.assertIsNotNone(actor_msg)
        self.assertEqual(actor_msg["message"]["text"], "Scout nods.")

        notify_player = self._message_entry(messages, "notification.cmd.emote.success", self.player.key)
        notify_watcher = self._message_entry(messages, "notification.cmd.emote.success", watcher.key)
        self.assertIsNotNone(notify_player)
        self.assertIsNotNone(notify_watcher)
        self.assertEqual(notify_player["message"]["text"], "Scout nods.")
        self.assertEqual(notify_watcher["message"]["text"], "Scout nods.")
