"""
Tests for builder world creation via the API.

These tests verify the world creation endpoint at /api/v1/builder/worlds/
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from worlds.models import World

User = get_user_model()


class TestBuilderWorldCreation(APITestCase):
    """Tests for creating worlds via the builder API."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('builder@example.com', 'password123')
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('builder-world-list')

    def test_create_multiplayer_world(self):
        """Test creating a new multiplayer world via POST."""
        resp = self.client.post(self.endpoint, {
            'name': 'A New World',
            'is_multiplayer': True,
        })

        # Verify the response
        self.assertEqual(resp.status_code, 201)
        self.assertIn('id', resp.data)
        self.assertEqual(resp.data['name'], 'A New World')
        self.assertTrue(resp.data['is_multiplayer'])

        # Verify the world was created in the database
        world = World.objects.get(pk=resp.data['id'])
        self.assertEqual(world.name, 'A New World')
        self.assertTrue(world.is_multiplayer)
        self.assertEqual(world.author, self.user)

        # Verify a spawned world was created
        spawned_world = world.spawned_worlds.get()
        self.assertTrue(spawned_world.is_multiplayer)

    def test_builder_creates_world_with_multiplayer_flag(self):
        """Test builder creates a world with the multiplayer flag set."""
        payload = {
            'name': 'A New World',
            'is_multiplayer': True,
        }
        resp = self.client.post(self.endpoint, payload)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], payload['name'])
        self.assertTrue(resp.data['is_multiplayer'])

        world = World.objects.get(pk=resp.data['id'])
        self.assertTrue(world.is_multiplayer)
