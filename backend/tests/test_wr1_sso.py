import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
from threading import Barrier
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.db import close_old_connections, connection, IntegrityError, transaction
from django.test import override_settings, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.reverse import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from tests.base import WorldTestCase
from users.models import ExternalIdentity, User, WR1SignIn
from users.tasks import cleanup_wr1_sign_ins
from users.wr1_sso import digest, outcome
from worlds.models import World


SETTINGS = dict(
    WR1_SSO_ENABLED=True, WR1_SSO_ISSUER='https://alpha.example.test',
    WR1_SSO_AUTHORIZE_URL='https://alpha.example.test/auth/core/authorize',
    WR1_SSO_EXCHANGE_URL='https://alpha.example.test/forge/api/v1/auth/core/exchange/',
    WR1_SSO_CLIENT_ID='core-test', WR1_SSO_CLIENT_SECRET='local-test-secret-with-at-least-32-characters',
    WR1_SSO_REDIRECT_URI='https://core.example.test/auth/wr1/callback',
    WR1_SSO_ALLOW_INSECURE_LOCAL=False,
)
ORIGIN = 'https://core.example.test'
IDENTITY = {'iss': SETTINGS['WR1_SSO_ISSUER'], 'aud': 'core-test', 'sub': '987654',
            'email': 'alpha-player@example.test', 'email_verified': True, 'name': 'AlphaPlayer'}


def provider_response(identity=None, status=200):
    response = Mock(status_code=status)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [json.dumps(identity or IDENTITY).encode()]
    return response


class Flow:
    def start(self, client=None):
        client = client or self.client
        response = client.post(reverse('wr1-start'), {'world': self.world.pk}, format='json', HTTP_ORIGIN=ORIGIN)
        self.assertEqual(response.status_code, 201, response.data)
        params = parse_qs(urlsplit(response.data['authorization_url']).query)
        return params['state'][0]

    def post(self, name, state, **data):
        return self.client.post(reverse('wr1-' + name), {'state': state, **data}, format='json', HTTP_ORIGIN=ORIGIN)

    def callback(self, state, identity=None):
        with patch('users.wr1_sso.requests.post', return_value=provider_response(identity)):
            return self.post('callback', state, code='c' * 43)

    def ready(self, identity=None):
        state = self.start()
        response = self.callback(state, identity)
        self.assertEqual(response.status_code, 200, response.data)
        return state, response


@override_settings(**SETTINGS)
class WR1SignInTests(Flow, WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.world.is_public = True
        self.world.save(update_fields=['is_public'])

    def test_start_binds_cookie_and_pkce_without_leaking_verifier(self):
        state = self.start()
        attempt = WR1SignIn.objects.get()
        cookie = self.client.cookies['__Host-wr1-bridge']
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['domain'], '')
        self.assertEqual(attempt.state_hash, digest(state))
        self.assertNotEqual(attempt.browser_hash, cookie.value)
        depths = []
        depth_before = len(connection.atomic_blocks)
        def exchange(*args, **kwargs):
            depths.append(len(connection.atomic_blocks))
            self.assertEqual(kwargs['json']['code_verifier'], attempt.verifier)
            self.assertEqual(kwargs['auth'], ('core-test', SETTINGS['WR1_SSO_CLIENT_SECRET']))
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['timeout'], (3, 5))
            return provider_response()
        with patch('users.wr1_sso.requests.post', side_effect=exchange):
            response = self.post('callback', state, code='c' * 43)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(depths, [depth_before])
        self.assertNotIn('access', response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_confirmed_alpha_user_creates_core_identity_and_core_tokens(self):
        state, response = self.ready()
        self.assertEqual(response.data['status'], 'ready')
        response = self.post('finish', state)
        self.assertEqual(response.status_code, 200, response.data)
        user = User.objects.get(email=IDENTITY['email'])
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_confirmed)
        self.assertFalse(user.is_staff or user.is_superuser or user.cod_accepted)
        self.assertEqual(int(AccessToken(response.data['access'])['user_id']), user.pk)
        self.assertNotEqual(str(user.pk), IDENTITY['sub'])
        self.assertEqual(response.data['destination'], '/worlds/%s' % self.world.pk)
        self.assertEqual(ExternalIdentity.objects.get().user_id, user.pk)
        self.assertEqual(self.post('finish', state).status_code, 400)

    def test_repeat_identity_ignores_alpha_email_changes(self):
        linked = User.objects.create_user('core-owned@example.test', is_confirmed=True)
        ExternalIdentity.objects.create(issuer=IDENTITY['iss'], subject=IDENTITY['sub'], user=linked)
        state, response = self.ready({**IDENTITY, 'email': 'changed@example.test', 'email_verified': False})
        self.assertEqual(response.data['user_id'], linked.pk)
        response = self.post('finish', state)
        self.assertEqual(response.data['user']['id'], linked.pk)
        linked.refresh_from_db()
        self.assertEqual(linked.email, 'core-owned@example.test')

    def test_existing_email_needs_fresh_proof_even_with_ambient_core_token(self):
        existing = User.objects.create_user(IDENTITY['email'].upper(), is_confirmed=True)
        self.client.force_authenticate(existing)
        state, response = self.ready()
        self.assertEqual(response.data['status'], 'verification_required')
        self.assertEqual(response.data['user_id'], existing.pk)
        self.assertEqual(self.post('finish', state).data['status'], 'verification_required')
        self.assertFalse(ExternalIdentity.objects.exists())
        with patch('users.wr1_sso.mail.send_email') as send:
            self.assertEqual(self.post('email', state).status_code, 200)
        code = send.call_args.kwargs['body'].split('\n\n')[1]
        self.assertEqual(send.call_args.kwargs['to_addresses'], [IDENTITY['email']])
        response = self.post('finish', state, proof=code)
        self.assertEqual(response.data['user']['id'], existing.pk)
        self.assertEqual(ExternalIdentity.objects.get().user_id, existing.pk)

    def test_native_signup_between_resolution_and_linking_still_requires_proof(self):
        state, _ = self.ready()
        def signup_after_resolution(attempt):
            result = outcome(attempt)
            User.objects.create_user(IDENTITY['email'], is_confirmed=True)
            return result
        with patch('users.wr1_sso.outcome', side_effect=signup_after_resolution):
            response = self.post('finish', state)
        self.assertEqual(response.data['status'], 'verification_required')
        self.assertNotIn('access', response.data)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_unconfirmed_new_user_needs_email_code(self):
        state, response = self.ready({**IDENTITY, 'email_verified': False})
        self.assertEqual(response.data['status'], 'verification_required')
        self.assertFalse(User.objects.filter(email=IDENTITY['email']).exists())
        with patch('users.wr1_sso.mail.send_email') as send:
            self.post('email', state)
        code = send.call_args.kwargs['body'].split('\n\n')[1]
        self.assertEqual(self.post('finish', state, proof=code).data['status'], 'authenticated')

    def test_email_code_is_browser_bound_and_attempt_limited(self):
        state, _ = self.ready({**IDENTITY, 'email_verified': False})
        with patch('users.wr1_sso.mail.send_email'):
            self.post('email', state)
        WR1SignIn.objects.update(proof_hash=digest('12345678'))
        for _ in range(5):
            self.assertEqual(self.post('finish', state, proof='00000000').status_code, 400)
        self.assertEqual(self.post('finish', state, proof='12345678').status_code, 400)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_email_code_cannot_prove_another_pending_handoff(self):
        first, _ = self.ready({**IDENTITY, 'email_verified': False})
        second, _ = self.ready({**IDENTITY, 'sub': '987655', 'email_verified': False})
        with patch('users.wr1_sso.mail.send_email') as send:
            self.post('email', first)
        code = send.call_args.kwargs['body'].split('\n\n')[1]
        self.assertEqual(self.post('finish', second, proof=code).status_code, 400)
        other = APIClient()
        self.assertEqual(other.post(reverse('wr1-finish'), {'state': first, 'proof': code}, HTTP_ORIGIN=ORIGIN).status_code, 400)
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertEqual(self.post('finish', first, proof=code).data['status'], 'authenticated')

    def test_missing_swapped_and_expired_browser_state_never_exchanges(self):
        state = self.start()
        other = APIClient()
        other_state = self.start(other)
        with patch('users.wr1_sso.requests.post') as exchange:
            self.assertEqual(other.post(reverse('wr1-callback'), {'state': state, 'code': 'c'*43}, HTTP_ORIGIN=ORIGIN).status_code, 400)
            self.assertEqual(self.post('callback', other_state, code='c'*43).status_code, 400)
            self.client.cookies.clear()
            self.assertEqual(self.post('callback', state, code='c'*43).status_code, 400)
            exchange.assert_not_called()
        state = self.start()
        WR1SignIn.objects.filter(state_hash=digest(state)).update(expires_ts=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.callback(state).status_code, 400)

    def test_callback_replay_and_failed_exchange_require_new_start(self):
        state, _ = self.ready()
        with patch('users.wr1_sso.requests.post') as exchange:
            self.assertEqual(self.post('callback', state, code='c'*43).status_code, 400)
            exchange.assert_not_called()
        state = self.start()
        with patch('users.wr1_sso.requests.post', return_value=provider_response(status=400)):
            self.assertEqual(self.post('callback', state, code='c'*43).status_code, 400)
        self.assertEqual(self.callback(state).status_code, 400)

    def test_invalid_assertions_never_create_an_account(self):
        for changed in ({'iss': 'https://other.test'}, {'aud': 'other'},
                        {'email_verified': 'true'}, {'sub': 123}, {'email': 'invalid'}, {'sub': '0'}):
            with self.subTest(changed=changed):
                cache.clear()
                state = self.start()
                self.assertEqual(self.callback(state, {**IDENTITY, **changed}).status_code, 400)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_existing_core_disabled_accounts_are_rejected(self):
        linked = User.objects.create_user('blocked@example.test')
        ExternalIdentity.objects.create(issuer=IDENTITY['iss'], subject=IDENTITY['sub'], user=linked)
        for flag in ('is_active', 'is_invalid', 'is_temporary'):
            User.objects.filter(pk=linked.pk).update(is_active=True, is_invalid=False, is_temporary=False)
            state, _ = self.ready()
            User.objects.filter(pk=linked.pk).update(**{flag: flag != 'is_active'})
            self.assertEqual(self.post('finish', state).status_code, 403)

    def test_no_cross_origin_start_or_private_world(self):
        self.assertEqual(self.client.post(reverse('wr1-start'), {'world': self.world.pk}, HTTP_ORIGIN='https://evil.test').status_code, 403)
        self.assertEqual(self.client.post(reverse('wr1-start'), {'world': self.world.pk}).status_code, 403)
        self.world.is_public = False
        self.world.save(update_fields=['is_public'])
        self.assertEqual(self.client.post(reverse('wr1-start'), {'world': self.world.pk}, HTTP_ORIGIN=ORIGIN).status_code, 400)

    def test_disabled_bridge_and_expired_continuations(self):
        with self.settings(WR1_SSO_ENABLED=False):
            self.assertEqual(self.client.post(reverse('wr1-start'), {}, HTTP_ORIGIN=ORIGIN).status_code, 404)
        state, _ = self.ready()
        WR1SignIn.objects.update(expires_ts=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('finish', state).status_code, 400)
        cleanup_wr1_sign_ins()
        self.assertFalse(WR1SignIn.objects.exists())

    def test_configuration_change_invalidates_pending_attempt(self):
        state = self.start()
        with self.settings(WR1_SSO_CLIENT_ID='different'):
            self.assertEqual(self.callback(state).status_code, 400)

    def test_email_uniqueness_is_enforced_in_database(self):
        User.objects.create_user('unique@example.test')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('UNIQUE@example.test')

    def test_repeat_sign_in_query_count_does_not_grow_with_other_accounts(self):
        linked = User.objects.create_user(IDENTITY['email'], is_confirmed=True)
        ExternalIdentity.objects.create(issuer=IDENTITY['iss'], subject=IDENTITY['sub'], user=linked)
        counts = []
        for population in (0, 1000):
            if population:
                users = User.objects.bulk_create([User(email='load-%s@example.test' % n) for n in range(population)])
                ExternalIdentity.objects.bulk_create([
                    ExternalIdentity(issuer=IDENTITY['iss'], subject=str(n+1000000), user=user)
                    for n, user in enumerate(users)])
            state, _ = self.ready()
            with CaptureQueriesContext(connection) as queries:
                response = self.post('finish', state)
            self.assertEqual(response.status_code, 200, response.data)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[1], 15)


@override_settings(**SETTINGS)
class WR1ConcurrencyTests(Flow, TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.world = World.objects.create(name='Bridge destination', is_public=True)
        self.client = APIClient()

    def test_simultaneous_first_visits_create_one_user_and_identity(self):
        first, _ = self.ready()
        second, _ = self.ready()
        cookie = self.client.cookies['__Host-wr1-bridge'].value
        barrier = Barrier(2)
        def finish(state):
            close_old_connections()
            try:
                client = APIClient()
                client.cookies['__Host-wr1-bridge'] = cookie
                barrier.wait(timeout=10)
                response = client.post(reverse('wr1-finish'), {'state': state}, HTTP_ORIGIN=ORIGIN)
                return response.status_code, response.data.get('user', {}).get('id')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, (first, second)))
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(User.objects.filter(email=IDENTITY['email']).count(), 1)
        self.assertEqual(ExternalIdentity.objects.count(), 1)
