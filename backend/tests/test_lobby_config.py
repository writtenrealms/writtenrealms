from django.core.exceptions import ValidationError
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from system.models import SiteControl
from tests.base import WorldTestCase


class TestLobbyConfig(APITestCase):
    def setUp(self):
        self.endpoint = reverse('lobby-config')
        SiteControl.objects.all().delete()

    def test_missing_site_control_defaults_to_world_one_with_one_query(self):
        with self.assertNumQueries(1):
            response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'main_world_id': 1})

    def test_new_site_control_defaults_to_world_one(self):
        site = SiteControl.objects.create(name='prod')
        self.assertEqual(site.main_world_id, 1)
        self.assertEqual(self.client.get(self.endpoint).data, {'main_world_id': 1})

    def test_override_is_public_and_does_not_expose_other_settings(self):
        SiteControl.objects.create(
            name='prod', main_world_id=12, maintenance_mode=True,
            platform_policy={'world_creation': 'whitelist'},
        )
        # Public configuration must also work with stale browser credentials.
        self.client.credentials(HTTP_AUTHORIZATION='Bearer expired')
        with self.assertNumQueries(1):
            response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'main_world_id': 12})

    def test_blank_restores_multi_world_mode_and_changes_are_immediate(self):
        site = SiteControl.objects.create(name='prod', main_world_id=12)
        self.assertEqual(self.client.get(self.endpoint).data, {'main_world_id': 12})
        site.main_world_id = None
        site.save(update_fields=['main_world_id'])
        self.assertEqual(self.client.get(self.endpoint).data, {'main_world_id': None})

    def test_admin_field_rejects_nonpositive_ids_and_accepts_blank(self):
        field = SiteControl._meta.get_field('main_world_id')
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                field.clean(value, None)
        self.assertIsNone(field.clean(None, None))
        self.assertEqual(field.clean(12, None), 12)


class TestMainWorldPermissions(WorldTestCase):
    def test_main_world_does_not_grant_access_to_a_private_world(self):
        SiteControl.objects.update_or_create(
            name='prod', defaults={'main_world_id': self.world.pk},
        )
        self.world.is_public = False
        self.world.save(update_fields=['is_public'])
        self.client.force_authenticate(user=None)
        self.assertEqual(
            self.client.get(reverse('lobby-config')).data,
            {'main_world_id': self.world.pk},
        )
        response = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertIn(response.status_code, (401, 403))
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertEqual(response.status_code, 200)
