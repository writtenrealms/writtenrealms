"""Payload loading stays bounded across world inheritance and room occupants."""

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import Faction, ItemDefinition
from config import constants as adv_consts
from core.world_config import inherited_system_config
from spawns.actions.movement import BuildMoveEventsAction, MoveContext, ResolveMoveAction
from spawns.models import Item
from spawns.state_payloads import (
    get_player_with_related,
    room_payload_key_for,
    serialize_actor,
    serialize_room,
)
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig


class MovementQueryLoadingTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.faction = Faction.objects.create(
            world=self.world,
            name="Humans",
            code="payload-human",
            type="core",
            playable=True,
        )
        self.definition = ItemDefinition.objects.create(
            world=self.world,
            slug="payload-blade",
            name="Payload blade",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )

    def _equip(self, player):
        augment = Item.objects.create(world=player.world, name="Strong gem", health_max=3)
        item = Item.objects.create(
            world=player.world,
            name="Payload blade",
            definition=self.definition,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            augment=augment,
            health_max=7,
        )
        player.equipment.weapon = item
        player.equipment.save(update_fields=["weapon"])
        player.core_faction = self.faction
        player.in_game = True
        player.save(update_fields=["core_faction", "in_game"])
        return item

    def test_player_payload_loads_inheritance_and_equipment_without_wide_joins(self):
        item = self._equip(self.player)
        template = World.objects.new_world(
            name="Payload instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        runtime = template.create_spawn_world(instance_ref="payload-instance")
        self.player.world = runtime
        self.player.room = template.config.starting_room
        self.player.save(update_fields=["world", "room"])

        with CaptureQueriesContext(connection) as captured:
            player = get_player_with_related(self.player.pk)

        # PostgreSQL's planner was spending >200 ms on the old 20-join read.
        # Keep every individual read small, including nested inheritance.
        self.assertLessEqual(max(query["sql"].count(" JOIN ") for query in captured), 4)
        with self.assertNumQueries(0):
            self.assertEqual(inherited_system_config(player.world).pk, self.world.config_id)
            self.assertEqual(player.world.context.config.pk, template.config_id)
            self.assertEqual(player.room.pk, template.config.starting_room_id)
            self.assertEqual(player.equipment.weapon.pk, item.pk)
            self.assertEqual(player.equipment.weapon.definition.name, "Payload blade")
            self.assertEqual(player.equipment.weapon.augment.health_max, 3)

        payload = serialize_actor(player, player.room)
        self.assertEqual(payload.equipment.weapon.name, "Payload blade")
        self.assertEqual(payload.factions["core"], self.faction.code)

    def test_equipment_loading_cost_does_not_grow_with_occupied_slots(self):
        item = self._equip(self.player)
        get_player_with_related(self.player.pk)  # Warm ContentType's process cache.
        with CaptureQueriesContext(connection) as one_slot:
            get_player_with_related(self.player.pk)

        equipment = self.player.equipment
        for slot in adv_consts.EQUIPMENT_SLOTS:
            if slot != "weapon":
                setattr(equipment, slot, Item.objects.create(
                    world=self.player.world,
                    name=f"Payload {slot}",
                    definition=self.definition,
                    augment=item.augment,
                ))
        equipment.save()
        with CaptureQueriesContext(connection) as all_slots:
            loaded = get_player_with_related(self.player.pk)

        self.assertEqual(len(all_slots), len(one_slot))
        with self.assertNumQueries(0):
            for slot in adv_consts.EQUIPMENT_SLOTS:
                equipped = getattr(loaded.equipment, slot)
                self.assertEqual(equipped.definition.pk, self.definition.pk)
                self.assertEqual(equipped.augment.health_max, 3)

    def test_occupied_room_payload_batches_player_worlds_factions_and_equipment(self):
        self._equip(self.player)
        viewer = get_player_with_related(self.player.pk)

        def capture_room():
            with CaptureQueriesContext(connection) as queries:
                payload = serialize_room(
                    self.room,
                    {self.room.pk: self.room.key},
                    {},
                    viewer=viewer,
                    runtime_world=self.spawn_world,
                )
            return payload, len(queries)

        capture_room()  # Warm the existing trigger/content-type caches.
        initial, initial_count = capture_room()
        others = [self.create_player(f"Guest{i}") for i in range(4)]
        for player in others:
            self._equip(player)
        payload, crowded_count = capture_room()

        self.assertEqual({char.key for char in initial.chars}, {self.player.key})
        self.assertEqual({char.key for char in payload.chars}, {self.player.key, *(p.key for p in others)})
        # Character effects still have an authoritative per-player read. World,
        # faction and equipment relations must not add further per-player reads.
        self.assertLessEqual(crowded_count - initial_count, len(others))
        self.assertTrue(all(char.core_faction == self.faction.code for char in payload.chars))

    def test_resolution_loads_only_the_requested_exit(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.room.create_at(adv_consts.DIRECTION_NORTH)
        self.player.stamina = 100
        with CaptureQueriesContext(connection) as captured:
            result = ResolveMoveAction().execute(self.player, adv_consts.DIRECTION_EAST)
        self.assertEqual(result.data["context"].dest_room_id, destination.pk)
        room_reads = [query["sql"] for query in captured if 'FROM "worlds_room"' in query["sql"]]
        self.assertTrue(room_reads)
        self.assertTrue(all(sql.count('JOIN "worlds_room"') <= 1 for sql in room_reads))

    def test_room_reuses_supplied_runtime_without_changing_payload(self):
        self._equip(self.player)
        self._equip(self.create_player("Observer"))
        viewer = get_player_with_related(self.player.pk)

        def read_room(runtime_world):
            with CaptureQueriesContext(connection) as queries:
                payload = serialize_room(
                    self.room,
                    {self.room.pk: self.room.key},
                    {},
                    viewer=viewer,
                    runtime_world=runtime_world,
                ).model_dump()
            return payload, queries

        read_room(None)  # Warm trigger, ContentType, and room relation caches.
        read_room(viewer.world)
        standalone_payload, standalone_queries = read_room(None)
        shared_payload, shared_queries = read_room(viewer.world)

        self.assertEqual(shared_payload, standalone_payload)
        self.assertLess(len(shared_queries), len(standalone_queries))
        world_reads = lambda queries: [
            query["sql"] for query in queries
            if 'FROM "worlds_world"' in query["sql"]
        ]
        self.assertTrue(world_reads(standalone_queries))
        self.assertEqual(world_reads(shared_queries), [])

    def test_move_payload_reuses_loaded_destination(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.player.room = destination
        self.player.save(update_fields=["room"])
        context = MoveContext(
            player_id=self.player.pk, direction="east",
            origin_room_id=self.room.pk, dest_room_id=destination.pk,
            trigger_world_id=self.world.pk, movement_cost=1,
        )
        with patch(
            "spawns.actions.movement._movement_room",
            side_effect=AssertionError("Destination should already be loaded"),
        ):
            result = BuildMoveEventsAction().execute(context)
        self.assertEqual(result.events[0].data["room"]["id"], destination.pk)
        self.assertEqual(result.events[0].data["room"]["zone"]["key"], destination.zone.key)

    def test_move_payload_keeps_destination_when_post_commit_action_relocated_player(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        relocated_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        self.player.room = relocated_room
        self.player.save(update_fields=["room"])
        context = MoveContext(
            player_id=self.player.pk, direction="east",
            origin_room_id=self.room.pk, dest_room_id=destination.pk,
            trigger_world_id=self.world.pk, movement_cost=1,
        )
        result = BuildMoveEventsAction().execute(context)
        move_data = result.events[0].data
        self.assertEqual(move_data["room"]["id"], destination.pk)
        self.assertEqual(move_data["actor"]["room"]["key"], room_payload_key_for(destination))
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, relocated_room.pk)
