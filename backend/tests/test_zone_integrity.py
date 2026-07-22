from rest_framework.reverse import reverse

from tests.base import WorldTestCase
from worlds.models import Room, Zone


class TestZoneIntegrity(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_deleting_zone_center_room_preserves_zone_and_room_memberships(self):
        center_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Zone Center",
            x=1,
            y=0,
            z=0,
        )
        survivor_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Zone Survivor",
            x=2,
            y=0,
            z=0,
        )
        self.zone.center = center_room
        self.zone.save(update_fields=["center"])

        resp = self.client.delete(
            reverse("builder-room-detail", args=[self.world.pk, center_room.pk])
        )

        self.assertEqual(resp.status_code, 204, resp.data)
        self.zone.refresh_from_db()
        self.assertIsNone(self.zone.center)

        survivor_room.refresh_from_db()
        self.assertEqual(survivor_room.zone, self.zone)
        self.assertTrue(Zone.objects.filter(pk=self.zone.pk).exists())
