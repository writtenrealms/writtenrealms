from django.core.cache import cache
from rest_framework.reverse import reverse

from builders.models import LastViewedRoom
from config import constants as api_consts
from lobby.cache import LOBBY_FIXED_SECTIONS_CACHE_KEY
from lobby.models import FeaturedWorld
from tests.base import WorldTestCase


class TestArchivedWorldLobby(WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_lobby_excludes_archived_worlds_and_their_recent_characters(self):
        LastViewedRoom.objects.create(
            world=self.world,
            user=self.user,
            room=self.room,
        )
        FeaturedWorld.objects.create(world=self.world, order=1)

        self.world.lifecycle = api_consts.WORLD_STATE_ARCHIVED
        self.world.save(update_fields=["lifecycle"])

        resp = self.client.get(reverse("lobby"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(
            self.player.id,
            {player["id"] for player in resp.data["recent_characters"]},
        )
        for section in ("featured", "playing", "building"):
            self.assertNotIn(
                self.world.id,
                {world["id"] for world in resp.data[section]},
            )

    def test_deleting_world_clears_lobby_fixed_sections_cache(self):
        cache.set(
            LOBBY_FIXED_SECTIONS_CACHE_KEY,
            {
                "featured": [{"id": self.world.id, "name": self.world.name}],
                "staff_picks": [],
                "in_development": [],
                "intro": [],
            },
            900,
        )

        self.world.lifecycle = api_consts.WORLD_STATE_STORED
        self.world.save(update_fields=["lifecycle"])

        resp = self.client.delete(
            reverse("builder-world-detail", args=[self.world.id])
        )

        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(cache.get(LOBBY_FIXED_SECTIONS_CACHE_KEY))
