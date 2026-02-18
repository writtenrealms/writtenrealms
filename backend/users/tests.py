import mock

from config import constants as adv_consts

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from spawns import serializers as spawn_serializers
from spawns.models import Player
from tests.base import WorldTestCase

from users.models import LoginLinkRequest

User = get_user_model()

# class CreationTests(APITestCase):
#     "Make sure that the auth protected resources can't be created"

#     def test_unauthenticated_creation(self):
#         resp = self.client.post('/worlds/', json={
#             'name': 'A Test World'
#         })
#         self.assertEqual(resp.status_code, 401)


class LoggedInUserTests(APITestCase):

    def test_view_unauthenticated(self):
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 401)

    def test_view_authenticated(self):
        user = User.objects.create(username='john')
        self.client.force_authenticate(user=user)
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], user.id)

    def test_edit_username(self):
        user = User.objects.create(username='john', email='j@example.com')
        self.client.force_authenticate(user=user)

        # Change via logged-in-user endpoint
        resp = self.client.put(
            reverse('logged-in-user'), {
                'name': 'jack'
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['name'], 'jack')
        user.refresh_from_db()
        self.assertEqual(user.username, 'jack')

        # Change via user detail endpoint
        resp = self.client.put(
            reverse('user-detail', args=[user.pk]), {
                'name': 'joe'
            })
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.username, 'joe')

        # Tests that can't edit someone else's username
        user2 = User.objects.create(username='will', email='w@example.com')
        resp = self.client.put(
            reverse('user-detail', args=[user2.pk]), {
                'name': 'bill'
            })
        self.assertEqual(resp.status_code, 404)


class SignupTests(APITestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse('signup')
        self.email = 'user@example.com'
        self.username = 'John'

    def test_signup_when_already_authenticated(self):
        user = User.objects.create_user('joe@example.com', 'p')
        self.client.force_authenticate(user)
        resp = self.client.post(self.endpoint, {
            'email': self.email,
            'username': self.username,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['user']['id'], user.id)

    def test_successful_signup(self):
        self.assertEqual(
            self.client.get(reverse('logged-in-user')).status_code, 401)

        resp = self.client.post(self.endpoint, {
            'email': self.email,
            'username': self.username,
            'first_name': 'John',
            'last_name': 'Doe',
        })

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['user']['email'], self.email)
        self.assertEqual(resp.data['user']['name'], self.username)
        self.assertTrue(resp.data['login_link_sent'])
        user = User.objects.get(pk=resp.data['user']['id'])
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name, 'John')
        self.assertEqual(user.last_name, 'Doe')
        self.assertEqual(LoginLinkRequest.objects.filter(user=user).count(), 1)

    def test_signup_duplicate(self):
        user = User.objects.create(email='john2@example.com',
                                   username=self.username)

        resp = self.client.post(self.endpoint, {
            'email': self.email,
            'username': self.username,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['username'][0], 'This field must be unique.')

        resp = self.client.post(self.endpoint, {
            'email': user.email,
            'username': 'John2',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['email'][0], 'This field must be unique.')

    def test_signup_without_names(self):
        resp = self.client.post(self.endpoint, {
            'email': self.email,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['user']['name'], None)

        resp = self.client.post(self.endpoint, {
            'email': 'new_email@example.com',
            'username': '',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['user']['name'], None)

        resp = self.client.post(self.endpoint, {
            'email': 'new_email2@example.com',
            'username': '',
        }, format='json')
        self.assertEqual(resp.data['user']['name'], None)

    def test_validation(self):
        resp = self.client.post(self.endpoint, {
            'email': self.email,
            'username': self.username,
        })
        self.assertEqual(resp.status_code, 201)

    def test_email_case_insensitivity(self):
        User.objects.create(email='joe@example.com', username='Joe')
        resp = self.client.post(self.endpoint, {
            'email': 'Joe@example.com',
            'username': 'Joe2',
        })
        self.assertEqual(resp.status_code, 400)

class SaveTemporaryUserTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse('save-user')
        self.username = 'John'
        self.email = 'john@example.com'

        self.user.is_temporary = True
        self.user.save()
        self.client.force_authenticate(self.user)

        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            name='Lana',
            gender=adv_consts.GENDER_FEMALE,
            room=self.room,
            user=self.user)

    def test_save_intro_character(self):
        self.assertTrue(self.user.is_temporary)
        resp = self.client.post(self.endpoint, {
            'username': self.username,
            'email': self.email,
            'send_newsletter': True,
            'first_name': 'John',
            'last_name': 'Doe',
        })
        self.assertEqual(resp.status_code, 201)

        self.user.refresh_from_db()

        self.assertFalse(self.user.is_temporary)
        self.assertEqual(self.user.username, self.username)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(self.user.email, self.email)
        self.assertTrue(self.user.send_newsletter)
        self.assertEqual(self.user.first_name, 'John')
        self.assertEqual(self.user.last_name, 'Doe')
        self.assertTrue(resp.data['login_link_sent'])
        self.assertEqual(
            LoginLinkRequest.objects.filter(user=self.user).count(),
            1)

        self.assertEqual(resp.data['user']['name'], self.username)
        self.assertEqual(resp.data['user']['email'], self.email)

    def test_failure_save_as_non_temporary_character(self):
        "This should only work when invoked by a temp user"
        self.user.is_temporary = False
        self.user.save()
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.endpoint, {})
        self.assertEqual(resp.status_code, 403)


class ForgotPasswordTests(WorldTestCase):
    "Tests for initiating a login link request"

    def setUp(self):
        super().setUp()

    @mock.patch('users.serializers.mail.send_login_link')
    def test_request_login_link(self, mock_send):
        # Email does not exist, we return a 201 response despite the fact
        # that no request was initiated, so as not to give away whether the
        # account actually exists / does not exist
        resp = self.client.post(reverse('forgot-password'), {
            'email': 'nobody@example.com'
        })
        self.assertEqual(resp.status_code, 201)
        mock_send.assert_not_called()

        # If we use a working email, the endpoint tries to send an email
        resp = self.client.post(reverse('forgot-password'), {
            'email': self.user.email,
        })
        self.assertEqual(resp.status_code, 201)
        mock_send.assert_called()

        # Make sure a login link record got created
        self.assertEqual(
            LoginLinkRequest.objects.filter(user=self.user).count(),
            1)

    @mock.patch('users.serializers.mail.send_login_link')
    def test_request_password_with_unconfirmed_user(self, mock_send):
        "Login links should still be sent even if the email isn't confirmed yet"
        self.user.is_confirmed = False
        self.user.save()
        resp = self.client.post(reverse('forgot-password'), {
            'email': self.user.email
        })
        self.assertEqual(resp.status_code, 201)
        mock_send.assert_called()


class ResetPasswordTests(WorldTestCase):
    "Tests for doing the password change with a code"

    def setUp(self):
        super().setUp()
        self.ep = reverse('reset-password')

    def test_reset_invalid_code(self):
        resp = self.client.post(self.ep, {
            'code': 'invalid',
            'password': 'p',
        })
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(
            resp.data['detail'],
            'Password reset is disabled. Use a login link instead.')

    def test_reset_valid_code(self):
        "Password reset is disabled"
        resp = self.client.post(self.ep, {
            'code': 'code',
            'password': 'newpass',
        })
        self.assertEqual(resp.status_code, 410)


class LoginLinkTests(WorldTestCase):

    def setUp(self):
        super().setUp()

    @mock.patch('users.serializers.mail.send_login_link')
    def test_signup_sends_email(self, mock_send_confirmation):
        # Make sure signing up sends a login link
        resp = self.client.post(reverse('signup'), {
            'email': 'user@example.com',
        })
        self.assertEqual(resp.status_code, 201)
        mock_send_confirmation.assert_called()

    @mock.patch('users.serializers.mail.send_login_link')
    def test_save_sends_email(self, mock_send_confirmation):
        self.client.force_authenticate(self.user)
        self.user.is_temporary = True
        self.user.save()
        resp = self.client.post(reverse('save-user'), {
            'email': 'user@example.com',
        })
        self.assertEqual(resp.status_code, 201)
        mock_send_confirmation.assert_called()
    @mock.patch('users.serializers.mail.send_login_link')
    def test_resend_confirmation_code(self, mock_send_confirmation):
        endpoint = reverse('resend-confirmation')

        resp = self.client.post(endpoint)
        self.assertEqual(resp.status_code, 401)

        self.client.force_authenticate(self.user)
        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 201)
        mock_send_confirmation.assert_called()

        self.user.is_temporary = False
        self.user.save()

        self.user.is_confirmed = True
        self.user.save()

        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 201)
