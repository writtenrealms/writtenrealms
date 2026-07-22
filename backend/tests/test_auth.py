from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase

from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from users.models import LoginLinkRequest
from django.utils import timezone
from datetime import timedelta
import hashlib

User = get_user_model()


class TestCustomUser(TestCase):

    def test_optional_username(self):
        """
        Tests that the username on a user model is optional, but if it is
        specified it must be unique.
        """
        user1 = User.objects.create_user('u1@example.com', password='p')
        self.assertEqual(user1.username, None)
        user2 = User.objects.create_user('u2@example.com', password='p')
        self.assertEqual(user2.username, None)
        user3 = User.objects.create_user('u3@example.com', password='p',
                                         username='joe')
        self.assertEqual(user3.username, 'joe')
        with self.assertRaises(IntegrityError):
            user4 = User.objects.create_user('u4@example.com', password='p',
                                             username='joe')


class TestLoginLink(APITestCase):

    def setUp(self):
        super().setUp()
        self.email = 'joe@example.com'
        self.user = User.objects.create_user(self.email)
        self.request_endpoint = reverse('email-login-request')
        self.confirm_endpoint = reverse('email-login-confirm')

    def test_login_link_request(self):
        resp = self.client.post(self.request_endpoint, {
            'email': self.user.email,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            LoginLinkRequest.objects.filter(user=self.user).count(),
            1)

    def test_login_link_confirm(self):
        token = 'token123'
        LoginLinkRequest.objects.create(
            user=self.user,
            code_hash=hashlib.sha256(token.encode('utf-8')).hexdigest(),
            expires_ts=timezone.now() + timedelta(minutes=10))
        resp = self.client.post(self.confirm_endpoint, {
            'token': token,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_email_case_insensitivity(self):
        resp = self.client.post(self.request_endpoint, {
            'email': self.user.email.upper(),
        })
        self.assertEqual(resp.status_code, 201)
