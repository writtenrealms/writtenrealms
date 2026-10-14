"""Google's verified identity must pass the same database admission policy."""
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from system.access import clear_access_cache
from system.models import AccessAllowlistEntry, SiteControl
from tests.base import WorldTestCase
from users.models import User


class GoogleAdmissionMixin:
    def setUp(self):
        super().setUp()
        clear_access_cache()
        self.addCleanup(clear_access_cache)
        self.control, _ = SiteControl.objects.update_or_create(
            name='prod', defaults={'access_mode': 'invite_only'})
        self.invitation = AccessAllowlistEntry.objects.create(email='invited@example.com')
        verifier = patch('users.serializers.id_token.verify_oauth2_token')
        self.verify = verifier.start()
        self.addCleanup(verifier.stop)
        self.verify.return_value = {
            'iss': 'https://accounts.google.com', 'email_verified': True,
            'email': 'invited@example.com', 'sub': 'google-subject',
            'given_name': 'Invited', 'family_name': 'Player',
        }

    def set_mode(self, mode):
        self.control.access_mode = mode
        self.control.save(update_fields=['access_mode'])

    def login(self):
        return self.client.post(reverse('google-login'), {'credential': 'google-token'})

    def assert_denied(self, response, code='invite_only'):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], code)
        for field in ('access', 'refresh', 'token'):
            self.assertNotIn(field, response.data)


@override_settings(GOOGLE_CLIENT_ID='expected-google-client')
class GoogleLoginAdmissionTests(GoogleAdmissionMixin, APITestCase):
    def test_invited_new_account_can_sign_in_without_staff_privileges(self):
        response = self.login()
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='invited@example.com')
        self.assertEqual(user.google_id, 'google-subject')
        self.assertTrue(user.is_confirmed)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(self.verify.call_args.args[0], 'google-token')
        self.assertEqual(self.verify.call_args.args[2], 'expected-google-client')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(self.client.get(reverse('logged-in-user')).status_code, 200)

    def test_invited_existing_email_links_case_insensitively(self):
        user = User.objects.create_user('invited@example.com')
        self.verify.return_value['email'] = 'INVITED@example.com'
        before = User.objects.count()
        response = self.login()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user']['id'], user.pk)
        self.assertEqual(User.objects.count(), before)
        user.refresh_from_db()
        self.assertEqual(user.google_id, 'google-subject')

    def test_uninvited_new_account_is_not_created(self):
        self.verify.return_value['email'] = 'outsider@example.com'
        before = User.objects.count()
        self.assert_denied(self.login())
        self.assertEqual(User.objects.count(), before)

    def test_existing_uninvited_account_is_not_linked_or_modified(self):
        user = User.objects.create_user('outsider@example.com')
        self.verify.return_value['email'] = user.email
        self.assert_denied(self.login())
        user.refresh_from_db()
        self.assertIsNone(user.google_id)
        self.assertIsNone(user.last_login)
        self.assertFalse(user.is_confirmed)

    def test_revoked_invitation_blocks_linked_google_login_and_existing_tokens(self):
        response = self.login()
        self.assertEqual(response.status_code, 201)
        self.invitation.delete()
        self.assert_denied(self.login())
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assert_denied(self.client.get(reverse('logged-in-user')))
        self.client.credentials()
        self.assert_denied(self.client.post(reverse('jwt-refresh-token'),
            {'refresh': response.data['refresh']}))

    def test_changed_google_email_must_be_invited_before_account_mutation(self):
        user = User.objects.create_user('invited@example.com', google_id='google-subject')
        self.verify.return_value['email'] = 'changed@example.com'
        self.assert_denied(self.login())
        user.refresh_from_db()
        self.assertEqual(user.email, 'invited@example.com')
        self.assertIsNone(user.last_login)

    def test_closed_site_blocks_even_invited_google_accounts(self):
        user = User.objects.create_user('invited@example.com', google_id='google-subject')
        self.set_mode('closed')
        self.assert_denied(self.login(), 'site_closed')
        user.refresh_from_db()
        self.assertIsNone(user.last_login)

    def test_existing_active_staff_retain_closed_site_recovery_access(self):
        staff = User.objects.create_user('staff@example.com', is_staff=True)
        self.verify.return_value['email'] = staff.email
        self.set_mode('closed')
        response = self.login()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user']['id'], staff.pk)

    def test_inactive_staff_do_not_receive_closed_site_exception(self):
        staff = User.objects.create_user('staff@example.com', is_staff=True, is_active=False)
        self.verify.return_value['email'] = staff.email
        self.set_mode('closed')
        self.assert_denied(self.login(), 'site_closed')

    def test_open_site_allows_noninvited_google_account(self):
        self.set_mode('open')
        self.verify.return_value['email'] = 'new@example.com'
        self.assertEqual(self.login().status_code, 201)

    def test_invalid_google_credential_does_not_create_account(self):
        self.verify.side_effect = ValueError('Invalid signature or audience')
        self.assertEqual(self.login().status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_unverified_email_and_wrong_issuer_are_rejected(self):
        claims = self.verify.return_value.copy()
        for changes in ({'email_verified': False}, {'iss': 'https://not-google.example'}):
            with self.subTest(changes=changes):
                self.verify.return_value = {**claims, **changes}
                self.assertEqual(self.login().status_code, 400)
                self.assertFalse(User.objects.exists())

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_disabled_google_login_does_not_verify_or_create_accounts(self):
        self.assertEqual(self.login().status_code, 400)
        self.verify.assert_not_called()
        self.assertFalse(User.objects.exists())


@override_settings(GOOGLE_CLIENT_ID='expected-google-client')
class GoogleGuestSaveAdmissionTests(GoogleAdmissionMixin, WorldTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_temporary = True
        self.user.save(update_fields=['is_temporary'])
        # Exercise the endpoint's own Google admission check independently
        # of the platform check already applied by normal JWT authentication.
        self.client.force_authenticate(self.user)

    def save_with_google(self):
        return self.client.post(reverse('google-save'), {'credential': 'google-token'})

    def test_guest_cannot_link_an_uninvited_google_email(self):
        self.verify.return_value['email'] = 'outsider@example.com'
        before = User.objects.count()
        self.assert_denied(self.save_with_google())
        self.assertEqual(User.objects.count(), before)
        self.user.refresh_from_db()
        self.player.refresh_from_db()
        self.assertTrue(self.user.is_temporary)
        self.assertIsNone(self.user.google_id)
        self.assertEqual(self.player.user_id, self.user.pk)

    def test_closed_site_blocks_guest_conversion_even_for_invited_email(self):
        self.set_mode('closed')
        self.assert_denied(self.save_with_google(), 'site_closed')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_temporary)

    def test_invited_guest_conversion_preserves_character_and_uses_google_identity(self):
        response = self.save_with_google()
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.player.refresh_from_db()
        self.assertFalse(self.user.is_temporary)
        self.assertEqual(self.user.email, 'invited@example.com')
        self.assertEqual(self.user.google_id, 'google-subject')
        self.assertEqual(self.player.user_id, self.user.pk)
