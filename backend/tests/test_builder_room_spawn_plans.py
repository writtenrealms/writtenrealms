from django.urls import reverse

from builders import serializers as builder_serializers
from builders.models import Path, PathRoom, SpawnEntry, SpawnPlan
from tests.base import WorldTestCase
from worlds.models import Room, World


class TestBuilderRoomSpawnPlans(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse(
            'builder-room-spawn-plans',
            args=[self.world.pk, self.room.pk],
        )

        self.path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name='Patrol Route',
        )
        PathRoom.objects.create(path=self.path, room=self.room)
        self.plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug='room-plan',
            name='Room Plan',
        )

    def _entry(self, slug, **target):
        return SpawnEntry.objects.create(
            plan=self.plan,
            slug=slug,
            name=slug,
            order=self.plan.entries.count() + 1,
            **target,
        )

    def test_list_matches_room_zone_and_path_foreign_keys(self):
        room_entry = self._entry('direct', target_room=self.room)
        self._entry('zone-wide', target_zone=self.zone)
        self._entry('patrol', target_path=self.path)
        self._entry('follower', target_entry=room_entry)

        other_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='Other Room',
            x=1,
            y=0,
            z=0,
        )
        other_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug='other-plan',
            name='Other Plan',
        )
        SpawnEntry.objects.create(
            plan=other_plan,
            slug='other',
            target_room=other_room,
        )

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['spawn_plans']), 1)
        payload = response.data['spawn_plans'][0]
        self.assertEqual(payload['id'], self.plan.id)
        self.assertEqual(payload['num_entries'], 4)
        self.assertEqual(
            payload['matching_entries'],
            ['direct', 'zone-wide', 'patrol'],
        )

    def test_room_entry_count_matches_list_target_semantics(self):
        room_entry = self._entry('direct', target_room=self.room)
        self._entry('zone-wide', target_zone=self.zone)
        self._entry('patrol', target_path=self.path)
        self._entry('follower', target_entry=room_entry)

        serializer = builder_serializers.RoomBuilderSerializer()
        with self.assertNumQueries(1):
            count = serializer.get_num_spawn_plan_entries(self.room)

        self.assertEqual(count, 3)

    def test_room_entry_counts_are_batched_for_many_rooms(self):
        self._entry('direct', target_room=self.room)
        self._entry('zone-wide', target_zone=self.zone)
        self._entry('patrol', target_path=self.path)
        other_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='Other Room',
            x=1,
            y=0,
            z=0,
        )

        with self.assertNumQueries(3):
            rooms = builder_serializers._set_spawn_plan_entry_counts(
                [self.room, other_room]
            )

        self.assertEqual(rooms[0]._num_spawn_plan_entries, 3)
        self.assertEqual(rooms[1]._num_spawn_plan_entries, 1)

    def test_path_entry_room_matches_without_path_membership(self):
        entry_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='Path Entry Room',
            x=2,
            y=0,
            z=0,
        )
        self.path.entry_room = entry_room
        self.path.save(update_fields=['entry_room'])
        other_path_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='Other Path Room',
            x=3,
            y=0,
            z=0,
        )
        PathRoom.objects.create(path=self.path, room=entry_room)
        PathRoom.objects.create(path=self.path, room=other_path_room)
        self._entry('patrol', target_path=self.path)

        response = self.client.get(reverse(
            'builder-room-spawn-plans',
            args=[self.world.pk, entry_room.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['spawn_plans']), 1)
        self.assertEqual(
            response.data['spawn_plans'][0]['matching_entries'],
            ['patrol'],
        )
        self.assertEqual(
            builder_serializers.RoomBuilderSerializer().get_num_spawn_plan_entries(
                entry_room,
            ),
            1,
        )

    def test_primary_key_lookup_is_scoped_to_route_world(self):
        other_world = World.objects.new_world(
            name='Another World',
            author=self.user,
        )
        foreign_room = other_world.rooms.get()

        response = self.client.get(reverse(
            'builder-room-spawn-plans',
            args=[self.world.pk, foreign_room.pk],
        ))

        self.assertEqual(response.status_code, 404)
