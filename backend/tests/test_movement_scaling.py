"""Larger maps and inventories retain full movement data without per-row reads."""

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import ItemDefinition, Trigger
from config import constants as adv_consts
from spawns.handlers import dispatch_command
from spawns.models import Item
from spawns.state_payloads import room_payload_key_for
from tests.base import WorldTestCase
from tests.utils import capture_game_messages


class MovementScalingTests(WorldTestCase):
    def test_large_explored_map_and_inventory_do_not_add_per_row_queries(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        first_unseen = destination.create_at(adv_consts.DIRECTION_EAST)
        self.player.stamina = 100
        self.player.in_game = True
        self.player.save(update_fields=["stamina", "in_game"])
        self.player.viewed_rooms.add(self.room, destination)
        definition = ItemDefinition.objects.create(
            world=self.world, slug="travel-marker", name="Travel marker",
            description="A marker for a long journey.",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        Trigger.objects.create(
            world=self.world, kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(ItemDefinition),
            target_id=definition.pk, match="inspect marker",
            script="/echo -- The marker is intact.", display_action_in_room=True,
        )

        def create_item(index):
            return Item.objects.create(
                world=self.spawn_world, container=self.player,
                definition=definition, name=f"Travel marker {index}",
                type=adv_consts.ITEM_TYPE_INERT,
            )

        def move(direction):
            with capture_game_messages() as messages, CaptureQueriesContext(connection) as queries:
                dispatch_command(
                    command_type="move", player_id=self.player.pk,
                    payload={"direction": direction},
                )
            success = next(
                envelope["message"]["data"] for envelope in messages
                if envelope["message"]["type"] == "cmd.move.success"
            )
            return success, len(queries)

        items = [create_item(0)]
        move("east")
        move("west")  # Warm both directions and existing trigger/content-type caches.
        small, small_count = move("east")
        self.assertEqual(len(small["map"]), 2)
        self.assertEqual(len(small["actor"]["inventory"]), 1)
        move("west")

        explored = [self.room, destination, first_unseen]
        while len(explored) < 100:
            explored.append(explored[-1].create_at(adv_consts.DIRECTION_EAST))
        last_unseen = explored[-1].create_at(adv_consts.DIRECTION_EAST)
        self.player.viewed_rooms.add(*explored)
        items.extend(create_item(index) for index in range(1, 50))

        large, large_count = move("east")

        self.assertLessEqual(large_count, small_count)
        self.assertEqual(large["room"]["id"], destination.pk)
        self.assertEqual(large["actor"]["room"]["key"], room_payload_key_for(destination))
        inventory = large["actor"]["inventory"]
        self.assertEqual({item["key"] for item in inventory}, {item.key for item in items})
        self.assertTrue(all(item["description"] == definition.description for item in inventory))
        self.assertTrue(all(item["actions"] == ["inspect marker"] for item in inventory))
        map_rooms = {room["key"]: room for room in large["map"]}
        self.assertEqual(set(map_rooms), {room_payload_key_for(room) for room in explored})
        self.assertNotIn(room_payload_key_for(last_unseen), map_rooms)
        self.assertEqual(
            map_rooms[room_payload_key_for(explored[-1])]["east"],
            room_payload_key_for(last_unseen),
        )
