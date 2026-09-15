from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from config import constants as adv_consts
from spawns.actions import items as item_actions
from spawns.actions.base import ActionError
from spawns.actions.items import GetAction
from spawns.models import Player
from tests.test_items import create_test_item
from worlds.models import World, WorldConfig


class TestConcurrentLooting(TransactionTestCase):
    def test_two_players_cannot_claim_the_same_corpse_contents(self):
        user = get_user_model().objects.create_user("looter@example.com", "p")
        world = World.objects.new_world(
            name="Concurrent Loot World", author=user, config=WorldConfig.objects.create(),
        )
        runtime = world.create_spawn_world()
        room = world.zones.first().rooms.first()
        players = [
            Player.objects.create(user=user, world=runtime, room=room, name=f"Looter {i}")
            for i in range(2)
        ]
        loot = []
        for i in range(3):
            corpse = create_test_item(
                world, runtime, room, f"Corpse {i}",
                item_type=adv_consts.ITEM_TYPE_CORPSE, is_pickable=False,
            )
            loot.append(create_test_item(world, runtime, corpse, f"Coin {i}"))

        selected = Barrier(2)
        original = item_actions._resolve_accessible_containers

        def select_together(*args):
            sources = original(*args)
            selected.wait(timeout=10)
            return sources

        def loot_once(player_id):
            close_old_connections()
            try:
                try:
                    result = GetAction().execute(player_id, "all", "all.corpse")
                except ActionError as error:
                    return player_id, [], error.code
                return player_id, [item["key"] for item in result.events[0].data["items"]], None
            finally:
                close_old_connections()

        with patch.object(item_actions, "_resolve_accessible_containers", side_effect=select_together):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(loot_once, [player.id for player in players]))

        successes = [outcome for outcome in outcomes if outcome[2] is None]
        failures = [outcome for outcome in outcomes if outcome[2] is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][2], "empty_container")
        self.assertEqual(set(successes[0][1]), {item.key for item in loot})
        winner = Player.objects.get(pk=successes[0][0])
        self.assertEqual(set(winner.inventory.values_list("pk", flat=True)), {item.pk for item in loot})
        loser = Player.objects.get(pk=failures[0][0])
        self.assertFalse(loser.inventory.exists())
