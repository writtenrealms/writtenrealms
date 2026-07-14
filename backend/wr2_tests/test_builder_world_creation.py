"""
Tests for builder world creation via the API.

These tests verify the world creation endpoint at /api/v1/builder/worlds/
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from worlds.models import World, Zone

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


class TestBuilderZoneCreation(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('zone-builder@example.com', 'password123')
        self.client.force_authenticate(self.user)
        self.world = World.objects.new_world(
            name='Zone Builder World',
            author=self.user,
        )
        self.endpoint = reverse('builder-zone-list', args=[self.world.pk])

    def test_create_zone_uses_highest_relative_id_not_newest_zone(self):
        older_zone = Zone.objects.create(world=self.world, name='Older High ID')
        newer_zone = Zone.objects.create(world=self.world, name='Newer Low ID')
        Zone.objects.filter(pk=older_zone.pk).update(relative_id=7)
        Zone.objects.filter(pk=newer_zone.pk).update(relative_id=6)

        resp = self.client.post(
            self.endpoint,
            {'name': 'A New Zone'},
            format='json',
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['relative_id'], 8)
        self.assertEqual(resp.data['manifest_ref'], 'zone@8')
        self.assertFalse(
            Zone.objects.filter(world=self.world, relative_id__isnull=True).exists()
        )


class TestConcurrentZoneCreation(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.world = World.objects.create(name='Concurrent Zone World')

    def test_concurrent_creates_get_distinct_relative_ids(self):
        create_count = 4
        barrier = Barrier(create_count)

        def create_zone(index):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                zone = Zone.objects.create(
                    world_id=self.world.pk,
                    name=f'Concurrent Zone {index}',
                )
                return zone.relative_id
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=create_count) as executor:
            relative_ids = list(executor.map(create_zone, range(create_count)))

        self.assertEqual(sorted(relative_ids), [1, 2, 3, 4])
        self.assertFalse(
            Zone.objects.filter(world=self.world, relative_id__isnull=True).exists()
        )
