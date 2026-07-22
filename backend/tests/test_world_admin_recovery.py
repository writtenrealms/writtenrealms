from django.urls import reverse

from config import constants as api_consts
from spawns.models import Item, Mob
from tests.base import WorldTestCase


class TestWorldAdminRecovery(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse(
            "builder-world-admin-instance-recover",
            args=[self.world.pk, self.spawn_world.pk],
        )

    def test_admin_recovery_moves_transient_spawn_world_to_stopped(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STARTING)
        mob = Mob.objects.create(
            name="Half Loaded Guard",
            world=self.spawn_world,
            room=self.room,
        )
        ground_item = Item.objects.create(
            name="Half Loaded Rock",
            world=self.spawn_world,
            container=self.room,
        )
        carried_item = Item.objects.create(
            name="Carried Apple",
            world=self.spawn_world,
            container=self.player,
        )

        resp = self.client.post(self.endpoint)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.spawn_world.refresh_from_db()
        self.assertEqual(
            self.spawn_world.lifecycle,
            api_consts.WORLD_LIFECYCLE_STOPPED,
        )
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertFalse(Item.objects.filter(pk=ground_item.pk).exists())
        self.assertTrue(Item.objects.filter(pk=carried_item.pk).exists())
        self.assertEqual(
            resp.data["lifecycle_details"]["current"],
            api_consts.WORLD_LIFECYCLE_STOPPED,
        )
        self.assertFalse(resp.data["recovery_actions"]["recover_to_stopped"])

    def test_admin_recovery_rejects_running_world(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)

        resp = self.client.post(self.endpoint)

        self.assertEqual(resp.status_code, 400)

    def test_admin_recovery_accepts_legacy_stored_world(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_STATE_STORED)

        resp = self.client.post(self.endpoint)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.spawn_world.refresh_from_db()
        self.assertEqual(
            self.spawn_world.lifecycle,
            api_consts.WORLD_LIFECYCLE_STOPPED,
        )

    def test_admin_payload_exposes_recovery_action_for_transient_world(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STARTING)

        resp = self.client.get(reverse("builder-world-admin", args=[self.world.pk]))

        self.assertEqual(resp.status_code, 200, resp.data)
        spawn_payload = next(
            item
            for item in resp.data["spawned_worlds"]
            if item["id"] == self.spawn_world.id
        )
        self.assertTrue(
            spawn_payload["recovery_actions"]["recover_to_stopped"]
        )
