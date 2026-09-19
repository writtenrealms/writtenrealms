from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase


User = get_user_model()


class ProfilePermissionsTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user('profile@example.com', 'password')
        self.client.force_authenticate(self.user)
        self.endpoints = [
            reverse('logged-in-user'),
            reverse('user-detail', args=[self.user.pk]),
        ]

    def test_profile_cannot_change_identity_or_server_owned_fields(self):
        protected_flags = (
            'is_staff', 'is_temporary', 'is_confirmed', 'is_invalid',
            'cod_accepted', 'name_recognition', 'multiplayer_worlds',
        )
        joined = self.user.date_joined
        for endpoint in self.endpoints:
            for original in (False, True):
                with self.subTest(endpoint=endpoint, original=original):
                    User.objects.filter(pk=self.user.pk).update(**{
                        field: original for field in protected_flags
                    })
                    self.user.refresh_from_db()
                    response = self.client.put(endpoint, {
                        **{field: not original for field in protected_flags},
                        'is_admin': not original,
                        'email': 'another@example.com',
                        'date_joined': '2000-01-01T00:00:00Z',
                    }, format='json')
                    self.assertEqual(response.status_code, 200)
                    self.user.refresh_from_db()
                    for field in protected_flags:
                        self.assertEqual(getattr(self.user, field), original)
                        self.assertEqual(response.data[field], original)
                    self.assertEqual(self.user.email, 'profile@example.com')
                    self.assertEqual(self.user.date_joined, joined)

    def test_profile_preferences_still_save_with_echoed_user_fields(self):
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                payload = self.client.get(reverse('logged-in-user')).data
                payload.update({
                    'name': 'Player', 'first_name': 'First', 'last_name': 'Last',
                    'send_newsletter': True, 'use_grapevine': True,
                    'accessibility_mode': True,
                })
                response = self.client.put(endpoint, payload, format='json')
                self.assertEqual(response.status_code, 200)
                self.user.refresh_from_db()
                self.assertEqual(self.user.username, 'Player')
                self.assertEqual(self.user.first_name, 'First')
                self.assertEqual(self.user.last_name, 'Last')
                self.assertTrue(self.user.send_newsletter)
                self.assertTrue(self.user.use_grapevine)
                self.assertTrue(self.user.accessibility_mode)
                self.assertFalse(self.user.is_confirmed)
