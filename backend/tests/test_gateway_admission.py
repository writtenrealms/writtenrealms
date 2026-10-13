"""Socket admission uses current database accounts and the shared policy."""
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TransactionTestCase
import jwt

from fastapi_app import access, main, game_ws
from system.access import clear_access_cache, user_admitted
from system.models import AccessAllowlistEntry, SiteControl
from tests.test_fastapi_game_ws import FakeGameWebSocket


class GatewayDatabaseAdmissionTests(TransactionTestCase):
    def setUp(self):
        clear_access_cache()
        self.addCleanup(clear_access_cache)
        self.user = get_user_model().objects.create_user('invited@example.com')
        self.control = SiteControl.objects.create(name='prod', access_mode='invite_only')
        self.invitation = AccessAllowlistEntry.objects.create(email=self.user.email)

    def token(self, **claims):
        return jwt.encode({'user_id': self.user.pk, 'token_type': 'access', **claims},
                          'gateway-test-secret', algorithm='HS256')

    def forge_identity(self, **claims):
        with patch.object(main, 'JWT_SECRET', 'gateway-test-secret'):
            return async_to_sync(main._get_user_from_token)(self.token(**claims))[0]

    def test_forge_checks_database_invitation_even_when_claims_are_stale(self):
        self.assertEqual(self.forge_identity(email='old@example.com'), self.user.pk)
        self.invitation.delete()
        self.assertIsNone(self.forge_identity(email=self.user.email, is_staff=True))

    def test_open_closed_inactive_missing_and_staff_accounts(self):
        self.invitation.delete()
        self.control.access_mode = 'open'
        self.control.save()
        self.assertEqual(self.forge_identity(), self.user.pk)
        self.control.access_mode = 'closed'
        self.control.save()
        self.assertIsNone(self.forge_identity(is_staff=True))
        self.user.is_staff = True
        self.user.save()
        clear_access_cache()
        self.assertEqual(self.forge_identity(is_staff=False), self.user.pk)
        self.user.is_active = False
        self.user.save()
        clear_access_cache()
        self.assertIsNone(self.forge_identity())
        self.user.delete()
        clear_access_cache()
        self.assertIsNone(self.forge_identity())

    def test_game_rejects_uninvited_account_before_registering_or_dispatching(self):
        self.invitation.delete()
        socket = FakeGameWebSocket([{'type': 'system.connect', 'token': self.token(is_staff=True),
                                    'data': {'player_key': 'player.42'}}])
        manager = game_ws.GameConnectionManager()
        manager.start_pubsub_listener = AsyncMock()
        with patch.object(game_ws, 'game_manager', manager), \
                patch.object(game_ws, 'JWT_SECRET', 'gateway-test-secret'), \
                patch.object(game_ws, 'get_celery_app') as celery:
            async_to_sync(game_ws.handle_game_websocket)(socket)
        self.assertEqual(socket.sent_json[0]['data']['code'], 'platform_access_denied')
        self.assertFalse(manager.active_connections)
        celery.assert_not_called()

    def test_database_failure_denies_socket_admission(self):
        with patch('system.access.user_admitted', side_effect=DatabaseError('private connection detail')):
            self.assertFalse(async_to_sync(access.user_admitted)(self.user.pk))

    def test_gateway_policy_is_bounded_cached_and_observes_changes(self):
        with patch('system.access.monotonic', return_value=0):
            with self.assertNumQueries(3):
                self.assertTrue(user_admitted(self.user.pk))
            with self.assertNumQueries(0):
                for _ in range(20):
                    self.assertTrue(user_admitted(self.user.pk))
        # Simulate another process changing both identity and policy.
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with patch('system.access.monotonic', return_value=5.01):
            self.assertFalse(user_admitted(self.user.pk))
