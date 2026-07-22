from unittest.mock import PropertyMock, patch

from rest_framework.reverse import reverse

from spawns.models import Player
from tests.base import WorldTestCase


class TestLobbyCharacterDeletion(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_character_delete_marks_player_pending_without_old_game_lookup(self):
        endpoint = reverse("lobby-world-char", args=[self.world.pk, self.player.pk])

        with patch.object(
            Player,
            "game_player",
            new_callable=PropertyMock,
            side_effect=AssertionError("old game lookup should not be used"),
        ):
            resp = self.client.delete(endpoint)

        self.assertEqual(resp.status_code, 204)
        self.player.refresh_from_db()
        self.assertIsNotNone(self.player.pending_deletion_ts)
        self.assertEqual(self.player.name, f"Joe{self.player.id}")

    def test_character_delete_rejects_players_currently_in_game(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        endpoint = reverse("lobby-world-char", args=[self.world.pk, self.player.pk])

        resp = self.client.delete(endpoint)

        self.assertEqual(resp.status_code, 400)
        self.player.refresh_from_db()
        self.assertIsNone(self.player.pending_deletion_ts)
