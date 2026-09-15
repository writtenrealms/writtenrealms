from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from config import constants as adv_consts
from builders.models import Trigger
from quests.services.room_items import _player_owned_item_ids
from spawns.actions import items as item_actions
from spawns.actions.base import ActionError
from spawns.actions.items import GetAction
from spawns.handlers import dispatch_command
from spawns.models import Item
from tests.base import WorldTestCase
from tests.test_items import create_test_item
from tests.utils import capture_game_messages, dispatch_text_command


class TestMultiContainerGet(WorldTestCase):
    def container(self, name, *, owner=None, item_type=adv_consts.ITEM_TYPE_CORPSE, **fields):
        return create_test_item(
            self.world, self.spawn_world, owner or self.room, name,
            item_type=item_type, is_pickable=False, **fields,
        )

    def item(self, source, name, **fields):
        return create_test_item(self.world, self.spawn_world, source, name, **fields)

    def assert_owned_by(self, item, owner):
        self.assertTrue(owner.inventory.filter(pk=item.pk).exists())

    def assert_loots_all_corpses(self, command=None):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        watcher = self.create_player("Watcher")
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        self.container("Empty Raider")
        first = self.container("Fallen Raider")
        second = self.container("Fallen Wolf")
        coin = self.item(first, "Silver Coin")
        pelt = self.item(second, "Wolf Pelt")
        fixed = self.item(second, "Fixed Stone", is_pickable=False)
        deleted = self.item(second, "Old Coin", is_pending_deletion=True)
        ground_item = self.item(self.room, "Lantern")
        chest = self.container("Chest", item_type=adv_consts.ITEM_TYPE_CONTAINER)
        chest_item = self.item(chest, "Ruby")

        with capture_game_messages() as messages:
            if command:
                dispatch_text_command(self.player.id, command)
            else:
                dispatch_command(
                    command_type="get", player_id=self.player.id,
                    payload={"item": {"name": "all"}, "from": {"name": "all.corpse"}},
                )

        for item in [coin, pelt]:
            self.assert_owned_by(item, self.player)
        for item, owner in [(fixed, second), (deleted, second), (ground_item, self.room), (chest_item, chest)]:
            self.assert_owned_by(item, owner)
        successes = [entry["message"] for entry in messages if entry["message"]["type"] == "cmd.get.success"]
        notifications = [entry["message"] for entry in messages if entry["message"]["type"] == "notification.cmd.get.success"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(notifications), 1)
        for message in [*successes, *notifications]:
            self.assertEqual({item["key"] for item in message["data"]["items"]}, {coin.key, pelt.key})
            self.assertIn("Silver Coin from Fallen Raider.", message["text"])
            self.assertIn("Wolf Pelt from Fallen Wolf.", message["text"])
            self.assertEqual(
                {group["source"]["key"]: group["item_keys"] for group in message["data"]["sources"]},
                {first.key: [coin.key], second.key: [pelt.key]},
            )

    def test_loot_all(self):
        self.assert_loots_all_corpses("loot all")

    def test_get_all_all_corpse(self):
        self.assert_loots_all_corpses("get all all.corpse")

    def test_loot_all_corpse(self):
        self.assert_loots_all_corpses("loot all.corpse")

    def test_structured_get_all_corpses(self):
        self.assert_loots_all_corpses()

    def test_item_selectors_apply_within_each_matching_container(self):
        for selector, expected_indexes in [("coin", [0]), ("2.coin", [1]), ("all.coin", [0, 1])]:
            with self.subTest(selector=selector):
                sources = [self.container(f"Pouch {i}", item_type=adv_consts.ITEM_TYPE_CONTAINER) for i in range(2)]
                coins = [[self.item(source, f"Coin {i}") for i in range(2)] for source in sources]
                stones = [self.item(source, "Stone") for source in sources]
                empty = self.container("Empty Pouch", item_type=adv_consts.ITEM_TYPE_CONTAINER)
                result = GetAction().execute(self.player.id, selector, "all.pouch")
                expected = [row[i] for row in coins for i in expected_indexes]
                self.assertEqual({item["key"] for item in result.events[0].data["items"]}, {item.key for item in expected})
                for row, source in zip(coins, sources):
                    for i, item in enumerate(row):
                        self.assert_owned_by(item, self.player if i in expected_indexes else source)
                for stone, source in zip(stones, sources):
                    self.assert_owned_by(stone, source)
                Item.objects.filter(pk__in=[source.pk for source in [*sources, empty]]).delete()

    def test_mixed_room_and_carried_sources_only_notify_visible_transfers(self):
        watcher = self.create_player("Watcher")
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        room_bag = self.container("Public Bag", item_type=adv_consts.ITEM_TYPE_CONTAINER)
        carried_bag = self.container("Private Bag", owner=self.player, item_type=adv_consts.ITEM_TYPE_CONTAINER)
        visible = self.item(room_bag, "Apple")
        private = self.item(carried_bag, "Secret Note")
        result = GetAction().execute(self.player.id, "all", "all.bag")
        success, notification = result.events
        self.assertEqual({item["key"] for item in success.data["items"]}, {visible.key, private.key})
        self.assertEqual([item["key"] for item in notification.data["items"]], [visible.key])
        self.assertEqual(notification.data["source"]["key"], room_bag.key)
        self.assertNotIn("Secret", notification.text)
        self.assertNotIn("Private", notification.text)

    def test_carried_sources_and_invisible_actor_do_not_notify_room(self):
        watcher = self.create_player("Watcher")
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        for invisible in [False, True]:
            with self.subTest(invisible=invisible):
                self.player.is_invisible = invisible
                self.player.save(update_fields=["is_invisible"])
                source = self.container("Pouch", owner=self.room if invisible else self.player, item_type=adv_consts.ITEM_TYPE_CONTAINER)
                self.item(source, "Coin")
                result = GetAction().execute(self.player.id, "all", "all.pouch")
                self.assertEqual([event.type for event in result.events], ["cmd.get.success"])

    def test_all_sources_are_directly_accessible_and_in_the_runtime_world(self):
        visible = self.container("Corpse")
        coin = self.item(visible, "Coin")
        other_world = self.world.create_spawn_world()
        other = self.container("Other Instance Corpse")
        other.world = other_world
        other.save(update_fields=["world"])
        private = self.container("Other Player Corpse", owner=self.create_player("Other"))
        pending = self.container("Decaying Corpse", is_pending_deletion=True)
        nested = self.container("Nested Corpse", owner=visible)
        for source in [other, private, pending, nested]:
            self.item(source, "Hidden Coin")
        result = GetAction().execute(self.player.id, "all", "all.corpse")
        self.assertEqual([item["key"] for item in result.events[0].data["items"]], [coin.key])

    def test_bare_all_selects_all_container_types(self):
        sources = [self.container("Corpse"), self.container("Bag", item_type=adv_consts.ITEM_TYPE_CONTAINER)]
        items = [self.item(source, "Coin") for source in sources]
        result = GetAction().execute(self.player.id, "all", "all")
        self.assertEqual({item["key"] for item in result.events[0].data["items"]}, {item.key for item in items})

    def test_loot_refresh_keeps_each_items_trigger_actions(self):
        source = self.container("Corpse")
        first = self.item(source, "First Relic")
        second = self.item(source, "Second Relic")
        second.definition = first.definition
        second.save(update_fields=["definition"])
        unrelated = self.item(source, "Stone")
        for target, match, order in [(first.definition, "inspect relic", 2), (first, "touch relic", 1)]:
            Trigger.objects.create(
                world=self.world,
                kind=adv_consts.TRIGGER_KIND_COMMAND,
                scope=adv_consts.TRIGGER_SCOPE_ROOM,
                target_type=ContentType.objects.get_for_model(target),
                target_id=target.id,
                match=match,
                script="/echo -- It glows.",
                display_action_in_room=True,
                order=order,
            )
        result = GetAction().execute(self.player.id, "all", "all.corpse")
        actions = {item["key"]: item["actions"] for item in result.events[0].data["actor"]["inventory"]}
        self.assertEqual(actions[first.key], ["touch relic", "inspect relic"])
        self.assertEqual(actions[second.key], ["inspect relic"])
        self.assertEqual(actions[unrelated.key], [])

    def test_quest_ownership_scan_keeps_nested_and_equipped_items(self):
        bag = self.container("Bag", owner=self.player, item_type=adv_consts.ITEM_TYPE_CONTAINER)
        pouch = self.container("Pouch", owner=bag, item_type=adv_consts.ITEM_TYPE_CONTAINER)
        nested = self.item(pouch, "Quest Token", item_type=adv_consts.ITEM_TYPE_QUEST)
        equipped = self.item(self.player.equipment, "Ring")
        self.item(self.room, "Unowned Coin")
        self.assertEqual(_player_owned_item_ids(self.player), {bag.id, pouch.id, nested.id, equipped.id})

    def test_no_corpses_empty_corpses_and_no_matching_items(self):
        with self.assertRaises(ActionError) as error:
            GetAction().execute(self.player.id, "all", "all.corpse")
        self.assertEqual(error.exception.code, "no_containers")
        source = self.container("Corpse")
        with self.assertRaises(ActionError) as error:
            GetAction().execute(self.player.id, "all", "all.corpse")
        self.assertEqual(error.exception.code, "empty_container")
        self.item(source, "Stone")
        with self.assertRaises(ActionError) as error:
            GetAction().execute(self.player.id, "coin", "all.corpse")
        self.assertEqual(error.exception.code, "item_not_found")

    def test_container_taken_after_selection_is_not_looted(self):
        source = self.container("Corpse")
        coin = self.item(source, "Coin")
        other = self.create_player("Other")
        original = item_actions._resolve_accessible_containers

        def move_after_selection(*args):
            sources = original(*args)
            source.container = other
            source.save(update_fields=["container_type", "container_id"])
            return sources

        with patch.object(item_actions, "_resolve_accessible_containers", side_effect=move_after_selection):
            with self.assertRaises(ActionError):
                GetAction().execute(self.player.id, "all", "all.corpse")
        self.assert_owned_by(coin, source)

    def test_query_count_is_bounded_as_corpses_and_items_increase(self):
        first = self.container("Corpse")
        self.item(first, "Warmup Coin")
        GetAction().execute(self.player.id, "all", "all.corpse")
        self.item(first, "Coin")
        with CaptureQueriesContext(connection) as small:
            GetAction().execute(self.player.id, "all", "all.corpse")
        for i in range(20):
            source = self.container(f"Corpse {i}")
            for j in range(3):
                self.item(source, f"Coin {i} {j}")
        with CaptureQueriesContext(connection) as large:
            result = GetAction().execute(self.player.id, "all", "all.corpse")
        self.assertEqual(len(result.events[0].data["items"]), 60)
        self.assertLessEqual(len(large), len(small) + 2)
        writes = [query["sql"] for query in large if query["sql"].startswith('UPDATE "spawns_item"')]
        self.assertEqual(len(writes), 1)
