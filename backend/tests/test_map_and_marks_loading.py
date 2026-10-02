"""Map selection is scoped and state payloads read legacy marks only as fallback."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from spawns.models import CharacterState, Mark, Player
from spawns.state_payloads import (
    build_map_payload,
    collect_map_room_ids,
    get_player_with_related,
    room_payload_key_for,
    serialize_actor,
)
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig


class MapAndMarksLoadingTests(WorldTestCase):
    def test_actor_marks_preserve_canonical_empty_and_legacy_snapshots(self):
        Mark.objects.create(player=self.player, name="legacy", value="old")
        for canonical, expected in (
            ({"quest": {"stage": 2}, "legacy": "new"}, {"quest": {"stage": 2}, "legacy": "new"}),
            ({}, {}),
            (None, {"legacy": "old"}),
        ):
            with self.subTest(canonical=canonical):
                CharacterState.objects.filter(player=self.player).delete()
                if canonical is not None:
                    CharacterState.objects.create(player=self.player, data=canonical)
                lazy = serialize_actor(Player.objects.get(pk=self.player.pk), self.room)
                with CaptureQueriesContext(connection) as queries:
                    player = get_player_with_related(self.player.pk)
                    payload = serialize_actor(player, player.room)
                self.assertEqual(payload.model_dump(), lazy.model_dump())
                self.assertEqual(payload.marks, expected)
                mark_reads = [
                    query for query in queries
                    if 'FROM "spawns_mark"' in query["sql"]
                ]
                self.assertEqual(len(mark_reads), int(canonical is None))

    def test_map_selection_unions_scoped_sources_in_one_query(self):
        visited = self.room.create_at("east")
        unseen = visited.create_at("east")
        landmark = self.room.create_at("north")
        landmark.is_landmark = True
        landmark.save(update_fields=["is_landmark"])
        foreign_world = World.objects.new_world(
            name="Other map", author=self.user, config=WorldConfig.objects.create(),
        )
        foreign_room = foreign_world.config.starting_room
        foreign_room.is_landmark = True
        foreign_room.save(update_fields=["is_landmark"])
        self.player.viewed_rooms.add(self.room, visited, landmark, foreign_room)
        player = get_player_with_related(self.player.pk)

        with self.assertNumQueries(1):
            room_ids = collect_map_room_ids(player, self.world, visited)
        self.assertEqual(room_ids, {self.room.pk, visited.pk, landmark.pk})

        map_rooms, lookup = build_map_payload(self.world, room_ids, {})
        payloads = {room.key: room for room in map_rooms}
        self.assertEqual(set(payloads), {room_payload_key_for(room) for room in (self.room, visited, landmark)})
        self.assertEqual(payloads[room_payload_key_for(visited)].east, room_payload_key_for(unseen))
        self.assertNotIn(room_payload_key_for(unseen), payloads)
        self.assertEqual(lookup[unseen.pk], room_payload_key_for(unseen))
        self.assertNotIn(foreign_room.pk, lookup)

        # A starting-room reference in a different authored world stays excluded.
        player.world.config.starting_room_id = foreign_room.pk
        with self.assertNumQueries(1):
            self.assertEqual(collect_map_room_ids(player, self.world, None), room_ids)

    def test_map_selection_preserves_start_current_and_empty_cases(self):
        player = get_player_with_related(self.player.pk)
        player.viewed_rooms.clear()
        with self.assertNumQueries(1):
            self.assertEqual(collect_map_room_ids(player, self.world, None), {self.room.pk})
        player.world.config.starting_room_id = None
        with self.assertNumQueries(1):
            self.assertEqual(collect_map_room_ids(player, self.world, None), set())
        with self.assertNumQueries(1):
            self.assertEqual(collect_map_room_ids(player, self.world, self.room), {self.room.pk})

        # Current-room inclusion is unconditional, as before; map assembly owns
        # the final authored-world filter.
        foreign_world = World.objects.new_world(
            name="Current room elsewhere", author=self.user, config=WorldConfig.objects.create(),
        )
        foreign_room = foreign_world.config.starting_room
        with self.assertNumQueries(1):
            self.assertEqual(collect_map_room_ids(player, self.world, foreign_room), {foreign_room.pk})
        self.assertEqual(build_map_payload(self.world, {foreign_room.pk}, {}), ([], {}))
