from datetime import timedelta
import hashlib
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from core.access_policy import environment_allowlist
from users.models import LoginLinkRequest
from users.tokens import build_token_response

User = get_user_model()


@override_settings(ACCESS_EMAIL_ALLOWLIST=frozenset({'invited@example.com'}))
class AdmissionTests(APITestCase):
    def setUp(self):
        self.invited = User.objects.create_user('invited@example.com')
        self.outsider = User.objects.create_user('outsider@example.com')

    @patch('core.mail.send_login_link')
    def test_uninvited_registration_and_login_do_not_create_accounts_or_send_mail(self, mail):
        for url in ['email-login-request', 'signup']:
            response = self.client.post(reverse(url), {'email': 'new@example.com'})
            self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())
        mail.assert_not_called()

    @patch('core.mail.send_login_link')
    def test_invited_email_is_case_insensitive(self, mail):
        response = self.client.post(reverse('email-login-request'), {'email': 'INVITED@example.com'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(LoginLinkRequest.objects.get().user_id, self.invited.pk)
        mail.assert_called_once()

    def test_revoked_login_link_cannot_issue_tokens(self):
        link = LoginLinkRequest.objects.create(user=self.outsider,
            code_hash=hashlib.sha256(b'old-link').hexdigest(),
            expires_ts=timezone.now() + timedelta(minutes=10))
        response = self.client.post(reverse('email-login-confirm'), {'token': 'old-link'})
        self.assertEqual(response.status_code, 403)
        link.refresh_from_db()
        self.assertIsNone(link.used_ts)

    def test_previously_issued_access_and_refresh_tokens_are_denied(self):
        refresh = RefreshToken.for_user(self.outsider)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(refresh.access_token))
        self.assertEqual(self.client.get(reverse('logged-in-user')).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.post(reverse('jwt-refresh-token'),
            {'refresh': str(refresh)}).status_code, 403)

    def test_existing_session_obeys_allowlist(self):
        self.client.force_login(self.outsider)
        self.assertIn(self.client.get(reverse('logged-in-user')).status_code, (401, 403))

    def test_admin_authentication_obeys_allowlist(self):
        from django.contrib.auth import authenticate
        self.outsider.set_password('test-password')
        self.outsider.save()
        self.assertIsNone(authenticate(email=self.outsider.email, password='test-password'))

    def test_tokens_include_email_and_refresh_uses_current_account(self):
        tokens = build_token_response(self.invited)
        self.assertEqual(AccessToken(tokens['access'])['email'], self.invited.email)
        legacy = RefreshToken.for_user(self.invited)
        response = self.client.post(reverse('jwt-refresh-token'), {'refresh': str(legacy)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccessToken(response.data['access'])['email'], self.invited.email)

    @override_settings(GOOGLE_CLIENT_ID='test-google-client')
    @patch('users.serializers.id_token.verify_oauth2_token')
    def test_google_cannot_create_an_uninvited_account(self, verify):
        verify.return_value = {'iss': 'accounts.google.com', 'email_verified': True,
                               'email': 'new@example.com', 'sub': 'test-subject'}
        response = self.client.post(reverse('google-login'), {'credential': 'verified-by-mock'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())

    def test_guest_entry_is_rejected_before_world_or_account_creation(self):
        count = User.objects.count()
        self.assertEqual(self.client.post(reverse('game-play'), {}).status_code, 403)
        self.assertEqual(User.objects.count(), count)

    @override_settings(ACCESS_EMAIL_ALLOWLIST=frozenset())
    def test_empty_enabled_allowlist_denies_everyone(self):
        self.assertEqual(self.client.post(reverse('email-login-request'),
            {'email': self.invited.email}).status_code, 403)

    def test_api_admission_adds_no_database_query(self):
        from users.access import AllowedEmailJWTAuthentication
        token = RefreshToken.for_user(self.invited).access_token
        with self.assertNumQueries(1):
            self.assertEqual(AllowedEmailJWTAuthentication().get_user(token).pk, self.invited.pk)


class GatewayAdmissionTests(SimpleTestCase):
    def test_gateway_checks_signed_email_and_rejects_refresh_tokens(self):
        import jwt
        from fastapi_app import main, game_ws
        allowlist = frozenset({'invited@example.com'})
        for module, method in [(main, main._decode_token), (game_ws, game_ws._verify_token)]:
            with patch.object(module, 'ACCESS_EMAIL_ALLOWLIST', allowlist), \
                    patch.object(module, 'JWT_SECRET', 'test-signing-key'):
                for claims, allowed in [
                    ({'email': 'invited@example.com', 'token_type': 'access'}, True),
                    ({'email': 'outsider@example.com', 'token_type': 'access'}, False),
                    ({'email': 'invited@example.com', 'token_type': 'refresh'}, False),
                    ({'token_type': 'access'}, False),
                ]:
                    token = jwt.encode({'user_id': 1, **claims}, 'test-signing-key', algorithm='HS256')
                    self.assertEqual(method(token) is not None, allowed)

    def test_disabled_and_enabled_empty_environment_are_distinct(self):
        with patch.dict(os.environ, {'WR_ACCESS_RESTRICTED': '0', 'WR_ALLOWED_EMAILS': ''}):
            self.assertIsNone(environment_allowlist())
        with patch.dict(os.environ, {'WR_ACCESS_RESTRICTED': '1', 'WR_ALLOWED_EMAILS': ''}):
            self.assertEqual(environment_allowlist(), frozenset())
