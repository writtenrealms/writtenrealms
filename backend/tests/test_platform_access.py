"""Platform admission and staff settings must also cover existing identities."""
from datetime import timedelta
import os
from unittest.mock import patch
import hashlib

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from system.access import clear_access_cache, email_admitted, player_admitted
from system.models import AccessAllowlistEntry, SiteControl, StaffAuditEvent
from users.models import LoginLinkRequest
from tests.base import WorldTestCase

User = get_user_model()


class PlatformAdmissionTests(APITestCase):
    def setUp(self):
        clear_access_cache()
        self.addCleanup(clear_access_cache)
        self.user = User.objects.create_user('player@example.com')
        self.staff = User.objects.create_user('staff@example.com', is_staff=True)
        self.control, _ = SiteControl.objects.update_or_create(
            name='prod', defaults={'access_mode': 'open'})

    def set_mode(self, mode):
        self.control.access_mode = mode
        self.control.save(update_fields=['access_mode'])

    @patch('core.mail.send_login_link')
    def test_invite_only_blocks_account_creation_and_allows_exact_normalized_email(self, mail):
        self.set_mode('invite_only')
        AccessAllowlistEntry.objects.create(email=' INVITED@Example.com ')
        for name in ('signup', 'email-login-request'):
            rejected = self.client.post(reverse(name), {'email': 'intruder@example.com'})
            self.assertEqual(rejected.status_code, 403)
            self.assertEqual(rejected.data['code'], 'invite_only')
            self.assertEqual(rejected.data['detail'],
                'This site is currently invite-only. Please sign in with an invited '
                'email address, or contact the administrator to request an invitation.')
        self.assertFalse(User.objects.filter(email='intruder@example.com').exists())
        mail.assert_not_called()
        accepted = self.client.post(reverse('email-login-request'), {'email': 'INVITED@example.com'})
        self.assertEqual(accepted.status_code, 201)
        self.assertFalse(email_admitted('not-invited@example.com'))

    @patch('core.mail.send_login_link')
    def test_closed_site_explains_closure_without_creating_accounts_or_sending_mail(self, mail):
        self.set_mode('closed')
        for name in ('signup', 'email-login-request'):
            rejected = self.client.post(reverse(name), {'email': 'new@example.com'})
            self.assertEqual(rejected.status_code, 403)
            self.assertEqual(rejected.data['code'], 'site_closed')
            self.assertEqual(rejected.data['detail'],
                'This site is currently closed. Please check back later.')
        self.assertFalse(User.objects.filter(email='new@example.com').exists())
        mail.assert_not_called()

    def test_warm_denial_message_adds_no_database_queries(self):
        from rest_framework.exceptions import PermissionDenied
        from system.access import require_platform_access
        for mode in ('invite_only', 'closed'):
            self.set_mode(mode)
            self.assertFalse(email_admitted(self.user.email))
            with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                require_platform_access(user=self.user)

    def test_closure_blocks_existing_access_refresh_and_session(self):
        refresh = RefreshToken.for_user(self.user)
        self.set_mode('closed')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(refresh.access_token))
        self.assertEqual(self.client.get(reverse('logged-in-user')).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.post(reverse('jwt-refresh-token'), {'refresh': str(refresh)}).status_code, 403)
        self.client.force_login(self.user)
        self.assertIn(self.client.get(reverse('logged-in-user')).status_code, (401, 403))

    @patch('core.mail.send_login_link')
    def test_staff_can_recover_closed_installation(self, mail):
        self.set_mode('closed')
        response = self.client.post(reverse('email-login-request'), {'email': self.staff.email})
        self.assertEqual(response.status_code, 201)
        token = RefreshToken.for_user(self.staff).access_token
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(token))
        self.assertEqual(self.client.get(reverse('logged-in-user')).status_code, 200)

    def test_database_policy_is_authoritative_even_with_obsolete_environment_values(self):
        with patch.dict(os.environ, {'WR_ACCESS_RESTRICTED': '1', 'WR_ALLOWED_EMAILS': ''}):
            self.assertTrue(email_admitted(self.user.email))
        self.set_mode('invite_only')
        with patch.dict(os.environ, {'WR_ACCESS_RESTRICTED': '0', 'WR_ALLOWED_EMAILS': self.user.email}):
            self.assertFalse(email_admitted(self.user.email))
            AccessAllowlistEntry.objects.create(email=self.user.email)
            self.assertTrue(email_admitted(self.user.email))
            self.set_mode('closed')
            self.assertFalse(email_admitted(self.user.email))

    def test_previous_login_link_does_not_consume_link_after_removal(self):
        self.set_mode('invite_only')
        entry = AccessAllowlistEntry.objects.create(email=self.user.email)
        self.assertTrue(email_admitted(self.user.email))
        link = LoginLinkRequest.objects.create(user=self.user,
            code_hash=hashlib.sha256(b'existing-link').hexdigest(),
            expires_ts=timezone.now() + timedelta(minutes=10))
        entry.delete()
        response = self.client.post(reverse('email-login-confirm'), {'token': 'existing-link'})
        self.assertEqual(response.status_code, 403)
        link.refresh_from_db()
        self.assertIsNone(link.used_ts)

    def test_other_process_policy_change_is_observed_within_five_seconds(self):
        with patch('system.access.monotonic', return_value=0):
            self.assertTrue(email_admitted(self.user.email))
        # Queryset.update emulates an update in another process (no signals).
        SiteControl.objects.filter(pk=self.control.pk).update(access_mode='closed')
        with patch('system.access.monotonic', return_value=5.01):
            self.assertFalse(email_admitted(self.user.email))

    def test_warm_admission_requires_no_database_queries_and_cache_is_bounded(self):
        from system import access
        self.set_mode('invite_only')
        AccessAllowlistEntry.objects.create(email=self.user.email)
        self.assertTrue(email_admitted(self.user.email))
        with self.assertNumQueries(0):
            for _ in range(20):
                self.assertTrue(email_admitted(self.user.email))
        with patch.object(access, '_CACHE_LIMIT', 4):
            for i in range(8):
                access._cached(('test', i), lambda: True)
            self.assertLessEqual(len(access._cache), 4)


class StaffAccessSettingsTests(APITestCase):
    def setUp(self):
        clear_access_cache()
        self.addCleanup(clear_access_cache)
        self.staff = User.objects.create_user('staff@example.com', is_staff=True)
        self.regular = User.objects.create_user('player@example.com')
        self.client.force_authenticate(self.staff)
        self.settings_url = reverse('staff-site-settings')
        self.list_url = reverse('staff-allowlist')

    def test_settings_report_only_the_database_policy(self):
        SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'invite_only'})
        response = self.client.get(self.settings_url)
        self.assertEqual(response.data['access_mode'], 'invite_only')
        self.assertNotIn('deployment_restricted', response.data)
        self.assertNotIn('deployment_allowlist_count', response.data)

    def test_all_operations_require_staff(self):
        for user in (None, self.regular):
            self.client.force_authenticate(user)
            for method, path, body in (
                ('get', self.settings_url, None), ('patch', self.settings_url, {'access_mode': 'closed'}),
                ('get', self.list_url, None), ('post', self.list_url, {'email': 'a@example.com'}),
                ('patch', self.list_url + '1/', {'note': 'secret'}), ('delete', self.list_url + '1/', None),
            ):
                response = getattr(self.client, method)(path, body, format='json')
                self.assertIn(response.status_code, (401, 403))

    def test_settings_validate_and_audit_only_real_changes(self):
        SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'open'})
        for body in ({'access_mode': 'bad'}, {'building_enabled': 'no'},
                     {'maintenance_mode': 1}, {'main_world_id': True},
                     {'main_world_id': 9999999}, {'secret': 'value'}):
            self.assertEqual(self.client.patch(self.settings_url, body, format='json').status_code, 400)
        self.assertEqual(StaffAuditEvent.objects.count(), 0)
        response = self.client.patch(self.settings_url, {'access_mode': 'closed', 'main_world_id': None}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['access_mode'], 'closed')
        event = StaffAuditEvent.objects.get()
        self.assertEqual(event.actor_id, self.staff.pk)
        self.assertEqual(event.details['access_mode'], {'before': 'open', 'after': 'closed'})
        self.client.patch(self.settings_url, {'access_mode': 'closed'}, format='json')
        self.assertEqual(StaffAuditEvent.objects.count(), 1)

    def test_fresh_installation_can_save_access_before_default_world_exists(self):
        SiteControl.objects.all().delete()
        response = self.client.patch(self.settings_url, {
            'access_mode': 'invite_only', 'main_world_id': 1,
            'building_enabled': True, 'maintenance_mode': False,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['access_mode'], 'invite_only')

    @patch('system.tasks.update_staff_panel')
    def test_legacy_maintenance_toggle_is_also_audited(self, update_panel):
        from system.tasks import toggle_maintenance_mode
        SiteControl.objects.update_or_create(name='prod', defaults={'maintenance_mode': False})
        toggle_maintenance_mode(user_id=self.staff.pk)
        event = StaffAuditEvent.objects.get()
        self.assertEqual(event.actor_id, self.staff.pk)
        self.assertEqual(event.details['maintenance_mode'], {'before': False, 'after': True})
        self.assertTrue(SiteControl.objects.get(name='prod').maintenance_mode)

    def test_allowlist_crud_normalizes_unique_emails_and_audits(self):
        response = self.client.post(self.list_url, {'email': '  INVITED@Example.com  ', 'note': 'Beta tester'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['email'], 'invited@example.com')
        self.assertEqual(response.data['created_by_email'], self.staff.email)
        self.assertEqual(self.client.post(self.list_url, {'email': 'Invited@example.com'}, format='json').status_code, 400)
        row_url = f"{self.list_url}{response.data['id']}/"
        self.assertEqual(self.client.patch(row_url, {'note': 'Updated'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(row_url).status_code, 204)
        self.assertFalse(AccessAllowlistEntry.objects.exists())
        self.assertEqual(set(StaffAuditEvent.objects.values_list('action', flat=True)),
                         {'allowlist.added', 'allowlist.updated', 'allowlist.removed'})

    def test_allowlist_is_paginated_and_searchable(self):
        AccessAllowlistEntry.objects.bulk_create([
            AccessAllowlistEntry(email=f'person{i:02d}@example.com') for i in range(30)])
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['count'], 30)
        self.assertEqual(len(response.data['results']), 25)
        self.assertEqual(len(self.client.get(self.list_url, {'page': 2}).data['results']), 5)
        self.assertEqual(self.client.get(self.list_url, {'q': 'person01'}).data['count'], 1)


class GameplayAdmissionTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        clear_access_cache()
        self.addCleanup(clear_access_cache)

    @patch('spawns.tasks.publish_to_player')
    @patch('spawns.tasks.dispatch_command')
    def test_closed_system_blocks_already_connected_commands(self, dispatch, publish):
        from spawns.tasks import handle_game_command
        SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'closed'})
        handle_game_command.run('text', player_id=self.player.pk, user_id=self.user.pk, payload={'text': 'look'})
        dispatch.assert_not_called()
        self.assertEqual(publish.call_args.args[1]['data']['code'], 'platform_access_denied')

    @patch('spawns.tasks.publish_to_player')
    @patch('spawns.tasks.dispatch_command')
    def test_socket_cannot_bypass_closure_by_using_staff_character(self, dispatch, publish):
        from spawns.tasks import handle_game_command
        staff = self.create_user('staff@example.com', is_staff=True)
        self.player.user = staff
        self.player.save(update_fields=['user'])
        SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'closed'})
        handle_game_command.run('text', player_id=self.player.pk, user_id=self.user.pk)
        dispatch.assert_not_called()
        handle_game_command.run('text', player_id=self.player.pk, user_id=staff.pk)
        dispatch.assert_called_once()

    def test_player_policy_is_cached_and_observes_cross_process_closure(self):
        control, _ = SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'open'})
        with patch('system.access.monotonic', return_value=0):
            self.assertTrue(player_admitted(self.player.pk, self.user.pk))
        with patch('system.access.monotonic', return_value=4.9):
            with self.assertNumQueries(0):
                self.assertTrue(player_admitted(self.player.pk, self.user.pk))
        SiteControl.objects.filter(pk=control.pk).update(access_mode='closed')
        with patch('system.access.monotonic', return_value=5.01):
            self.assertFalse(player_admitted(self.player.pk, self.user.pk))
