from django.db import connection
from django.test.utils import CaptureQueriesContext

from config import constants as adv_consts
from spawns.handlers import dispatch_command
from spawns.handlers.registry import resolve_text_handler
from tests.base import WorldTestCase
from tests.test_items import create_test_item
from tests.utils import capture_game_messages, dispatch_text_command


class TestItemCommandPhrasing(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.pouch = self.item(
            self.player, "a leather pouch", keywords="pouch bag",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        self.spears = [
            self.item(self.pouch, "a shieldguard spear", keywords="spear")
            for _ in range(2)
        ]

    def item(self, container, name, **fields):
        return create_test_item(self.world, self.spawn_world, container, name, **fields)

    def command(self, text, message_type):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, text)
        matches = [m["message"] for m in messages if m["message"]["type"] == message_type]
        self.assertEqual(len(matches), 1, messages)
        return matches[0]

    def move(self, item, container):
        item.container = container
        item.save(update_fields=["container_type", "container_id"])

    def test_look_phrases_show_both_spears(self):
        for text in (
            "l pouch", "l in pouch", "look inside pouch", "look into the pouch",
            "look inside of the leather pouch", "look at a leather pouch",
            "examine the pouch", "L IN THE POUCH", f"l in {self.pouch.key}",
        ):
            with self.subTest(text=text):
                message = self.command(text, "cmd.look.success")
                self.assertEqual(message["data"]["target"]["key"], self.pouch.key)
                self.assertEqual(
                    [item["key"] for item in message["data"]["target"]["inventory"]],
                    [item.key for item in self.spears],
                )

    def test_get_phrases_preserve_numbered_selection(self):
        for text in (
            "get 2.spear pouch", "get 2.spear from pouch",
            "take the 2.spear from the pouch", "get 2.spear out of pouch",
            "pick up 2.spear from pouch", "pickup 2.spear from pouch",
            "GET 2.shieldguard spear FROM THE leather pouch",
            f"get {self.spears[1].key} from {self.pouch.key}",
        ):
            with self.subTest(text=text):
                message = self.command(text, "cmd.get.success")
                self.assertEqual([i["key"] for i in message["data"]["items"]], [self.spears[1].key])
                self.assertTrue(self.pouch.inventory.filter(pk=self.spears[0].pk).exists())
                self.assertTrue(self.player.inventory.filter(pk=self.spears[1].pk).exists())
                self.move(self.spears[1], self.pouch)

    def test_ground_pickup_synonyms(self):
        lantern = self.item(self.room, "a lantern")
        for text in ("take lantern", "pick up the lantern", "pickup lantern", "get a lantern"):
            with self.subTest(text=text):
                self.command(text, "cmd.get.success")
                self.assertTrue(self.player.inventory.filter(pk=lantern.pk).exists())
                self.move(lantern, self.room)

    def test_put_phrases_and_multiword_names(self):
        for text in (
            "put spear pouch", "put spear in pouch", "put spear into pouch",
            "put the spear inside the pouch", "put spear inside of pouch",
            "put a shieldguard spear into the leather pouch",
        ):
            with self.subTest(text=text):
                self.move(self.spears[1], self.player)
                self.command(text, "cmd.put.success")
                self.assertTrue(self.pouch.inventory.filter(pk=self.spears[1].pk).exists())

    def test_give_to_and_multiword_recipient(self):
        guard = self.create_mob("a palace guard", keywords="guard")
        for text in ("give spear guard", "give the shieldguard spear to the palace guard"):
            with self.subTest(text=text):
                self.move(self.spears[1], self.player)
                self.command(text, "cmd.give.success")
                self.assertTrue(guard.inventory.filter(pk=self.spears[1].pk).exists())

    def test_equipment_phrases_and_articles(self):
        helmet = self.item(
            self.player, "an iron helmet", keywords="helmet",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD,
        )
        for wear, remove in (
            ("put on the iron helmet", "take off the iron helmet"),
            ("put helmet on", "take helmet off"),
            ("PUT THE IRON HELMET ON", "TAKE THE IRON HELMET OFF"),
            (f"put {helmet.key} on", f"take {helmet.key} off"),
            ("put all.armor on", "take all.armor off"),
        ):
            with self.subTest(wear=wear, remove=remove):
                self.command(wear, "cmd.wear.success")
                self.player.refresh_from_db()
                self.assertTrue(self.player.equipment.inventory.filter(pk=helmet.pk).exists())
                self.command(remove, "cmd.remove.success")
                self.assertTrue(self.player.inventory.filter(pk=helmet.pk).exists())
                self.player.refresh_from_db()
                self.assertEqual(self.player.command_history[:2], [remove, wear])
        self.command("drop the iron helmet", "cmd.drop.success")
        self.assertTrue(self.room.inventory.filter(pk=helmet.pk).exists())

    def test_trailing_equipment_phrases_preserve_numbered_selection(self):
        helmets = [
            self.item(
                self.player, "an iron helmet", keywords="helmet",
                item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
                equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD,
            )
            for _ in range(2)
        ]
        self.command("put the 2.helmet on", "cmd.wear.success")
        self.player.refresh_from_db()
        self.assertTrue(self.player.equipment.inventory.filter(pk=helmets[1].pk).exists())
        self.assertTrue(self.player.inventory.filter(pk=helmets[0].pk).exists())

        # Equipment selectors count equipped matches, not carried matches.
        self.command("take 2.helmet off", "cmd.remove.error")
        self.assertTrue(self.player.equipment.inventory.filter(pk=helmets[1].pk).exists())
        self.command("take 1.helmet off", "cmd.remove.success")
        self.assertTrue(self.player.inventory.filter(pk=helmets[1].pk).exists())

    def test_explicit_container_relations_take_precedence_over_trailing_particles(self):
        for name in ("a chest with its lid on", "a chest with its lid off"):
            with self.subTest(name=name):
                chest = self.item(self.room, name, item_type=adv_consts.ITEM_TYPE_CONTAINER)
                self.move(self.spears[0], self.player)
                self.command(f"put spear into {name}", "cmd.put.success")
                self.assertTrue(chest.inventory.filter(pk=self.spears[0].pk).exists())
                self.command(f"take spear from {name}", "cmd.get.success")
                self.assertTrue(self.player.inventory.filter(pk=self.spears[0].pk).exists())

    def test_bulk_sources_keep_other_items_and_ground_items(self):
        other_pouch = self.item(self.room, "a cloth pouch", item_type=adv_consts.ITEM_TYPE_CONTAINER)
        other_spear = self.item(other_pouch, "a shieldguard spear")
        stone = self.item(other_pouch, "a stone")
        ground_spear = self.item(self.room, "a shieldguard spear")
        self.command("get all.shieldguard spear from all.pouch", "cmd.get.success")
        for spear in [*self.spears, other_spear]:
            self.assertTrue(self.player.inventory.filter(pk=spear.pk).exists())
        self.assertTrue(other_pouch.inventory.filter(pk=stone.pk).exists())
        self.assertTrue(self.room.inventory.filter(pk=ground_spear.pk).exists())

    def test_loot_from_container(self):
        self.command("loot from the pouch", "cmd.get.success")
        self.assertEqual(self.pouch.inventory.count(), 0)

    def test_incomplete_phrases_never_move_items(self):
        ground_spear = self.item(self.room, "a spear")
        carried_spear = self.item(self.player, "a spear")
        for text, command, code in (
            ("get spear from", "get", "missing_container"),
            ("take spear out of the", "get", "missing_container"),
            ("get from pouch", "get", "missing_item"),
            ("loot from", "get", "missing_container"),
            ("put spear into", "put", "missing_container"),
            ("put into pouch", "put", "missing_item"),
            ("give spear to", "give", "missing_target"),
            ("give to guard", "give", "missing_item"),
            ("take off", "remove", "missing_item"),
            ("take the off", "remove", "missing_item"),
            ("put on", "wear", "missing_item"),
            ("put the on", "wear", "missing_item"),
            ("pick up", "get", "missing_item"),
        ):
            with self.subTest(text=text):
                message = self.command(text, f"cmd.{command}.error")
                self.assertEqual(message["data"]["code"], code)
                self.assertTrue(self.room.inventory.filter(pk=ground_spear.pk).exists())
                self.assertTrue(self.player.inventory.filter(pk=carried_spear.pk).exists())
                self.assertEqual(self.pouch.inventory.count(), 2)
        self.command("l in", "cmd.look.error")

    def test_phrase_cannot_get_items_from_someone_elses_pouch(self):
        other_player = self.create_player("Other")
        self.move(self.pouch, other_player)
        self.command(f"take all from {self.pouch.key}", "cmd.get.error")
        self.assertEqual(self.pouch.inventory.count(), 2)

    def test_structured_selectors_are_not_parsed_as_phrases(self):
        item = self.item(self.room, "In", keywords="in")
        with capture_game_messages() as messages:
            dispatch_command(command_type="look", player_id=self.player.id, payload={"target": "in"})
        message = next(m["message"] for m in messages if m["message"]["type"] == "cmd.look.success")
        self.assertEqual(message["data"]["target"]["key"], item.key)

    def test_natural_phrasing_adds_no_queries(self):
        # Warm Django/command caches, then compare the same successful transfer.
        counts = []
        for text in ["get spear pouch", "get spear from pouch"] * 2:
            with CaptureQueriesContext(connection) as queries:
                self.command(text, "cmd.get.success")
            counts.append(len(queries))
            self.move(self.spears[0], self.pouch)
        self.assertEqual(counts[-2], counts[-1])

    def test_new_synonyms_preserve_shortcuts(self):
        for shortcut, command in (("l", "look"), ("ta", "talk"), ("p", "put"), ("g", "gossip"), ("ge", "get"), ("i", "inventory")):
            with self.subTest(shortcut=shortcut):
                self.assertEqual(resolve_text_handler(shortcut)[1].command_type, command)
