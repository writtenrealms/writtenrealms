import io
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from founders import services
from founders.models import FounderEvent, FounderPass, FounderProgram, PassTransfer
from system.access import clear_access_cache, email_admitted
from system.models import AccessAllowlistEntry, SiteControl
from users.account_notices import deliver_notice
from users.email_changes import confirm_email_change, request_email_change
from users.models import AccountEmailChange, AccountNotice, ExternalIdentity, LoginLinkRequest
from worlds.models import World, WorldConfig

User = get_user_model()


class FounderSetup:
    def setUp(self):
        super().setUp()
        clear_access_cache()
        cache.clear()
        self.addCleanup(clear_access_cache)
        self.delivery = patch('users.tasks.deliver_account_notice.delay').start()
        self.addCleanup(patch.stopall)
        self.staff = User.objects.create_user('operator@example.com', is_staff=True, is_confirmed=True)
        self.user = User.objects.create_user('founder@example.com', is_confirmed=True)
        self.world = World.objects.create(name='Test realm', author=self.staff,
                                           config=WorldConfig.objects.create())
        self.program = FounderProgram.objects.create(world=self.world, enabled=True)
        self.control, _ = SiteControl.objects.update_or_create(name='prod', defaults={
            'founders_enabled': True, 'access_mode': 'invite_only'})

    def grant(self, email=None):
        return services.grant(self.program.pk, email or self.user.email, actor=self.staff)[0]

    def token(self, recipient, subject):
        notice = AccountNotice.objects.filter(recipient=recipient, subject__startswith=subject).latest('pk')
        return re.search(r'#token=([^\s]+)', notice.body).group(1)


class FounderPassTests(FounderSetup, APITestCase):
    def test_program_and_installation_default_off(self):
        self.assertFalse(FounderProgram().enabled)
        self.assertFalse(SiteControl().founders_enabled)
        for target in (self.program, self.control):
            attr = 'enabled' if target == self.program else 'founders_enabled'
            setattr(target, attr, False)
            target.save()
            with self.assertRaises(PermissionDenied):
                self.grant()
            setattr(target, attr, True)
            target.save()
        self.assertFalse(FounderPass.objects.exists())

    def test_grant_is_case_insensitive_idempotent_and_creates_only_reserved_user(self):
        founder_pass = self.grant('NEW@Example.com')
        again = self.grant(' new@example.com ')
        self.assertEqual(founder_pass.pk, again.pk)
        self.assertEqual(FounderPass.objects.count(), 1)
        self.assertEqual(AccountNotice.objects.count(), 1)
        self.assertEqual(FounderEvent.objects.count(), 1)
        user = founder_pass.user
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.invited_signup_pending)
        self.assertFalse(user.is_confirmed)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.cod_accepted)
        self.assertTrue(email_admitted('NEW@example.com'))

    def test_existing_user_and_ordinary_invitation_are_preserved(self):
        self.user.username = 'existing'
        self.user.save()
        AccessAllowlistEntry.objects.create(email=self.user.email)
        founder_pass = self.grant()
        self.assertEqual(founder_pass.user_id, self.user.pk)
        self.assertEqual(founder_pass.user.username, 'existing')
        self.assertTrue(AccessAllowlistEntry.objects.filter(email=self.user.email).exists())

    def test_regular_builder_cannot_issue_direct_pass(self):
        with self.assertRaises(PermissionDenied):
            services.grant(self.program.pk, 'new@example.com', actor=self.user)

    def test_claim_is_idempotent_locks_transfer_and_enables_one_invitation(self):
        founder_pass = self.grant()
        with self.assertRaises(ValidationError):
            services.invite(founder_pass.pk, self.user, 'friend@example.com')
        services.claim(founder_pass.pk, self.user)
        services.claim(founder_pass.pk, self.user)
        self.assertEqual(founder_pass.events.filter(action='claimed').count(), 1)
        with self.assertRaises(ValidationError):
            services.start_transfer(founder_pass.pk, self.user, 'other@example.com')
        gift, created = services.invite(founder_pass.pk, self.user, 'friend@example.com')
        self.assertTrue(created)
        self.assertEqual(gift.claim_reason, 'gift')
        self.assertTrue(email_admitted(gift.user.email))
        again, created = services.invite(founder_pass.pk, self.user, 'FRIEND@example.com')
        self.assertEqual(gift.pk, again.pk)
        self.assertFalse(created)
        with self.assertRaises(ValidationError):
            services.invite(founder_pass.pk, self.user, 'second@example.com')
        gift.user.is_confirmed = True
        gift.user.save()
        for operation in (services.invite, services.start_transfer):
            with self.assertRaises(ValidationError):
                operation(gift.pk, gift.user, 'chain@example.com')
        self.assertEqual(FounderPass.objects.count(), 2)

    def test_duplicate_recipient_does_not_spend_invitation_or_upgrade_gift(self):
        founder_pass = services.claim(self.grant().pk, self.user)
        self.grant('existing@example.com')
        with self.assertRaises(ValidationError):
            services.invite(founder_pass.pk, self.user, 'existing@example.com')
        self.assertFalse(FounderPass.objects.filter(parent=founder_pass).exists())
        gift, _ = services.invite(founder_pass.pk, self.user, 'friend@example.com')
        self.assertEqual(self.grant('friend@example.com').pk, gift.pk)
        gift.refresh_from_db()
        self.assertEqual(gift.kind, 'gift')

    def test_transfer_verifies_destination_moves_unused_invitation_and_never_duplicates(self):
        founder_pass = self.grant()
        services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        self.assertFalse(email_admitted('new@example.com'))
        founder_pass.refresh_from_db()
        self.assertEqual(founder_pass.user_id, self.user.pk)
        token = self.token('new@example.com', 'Confirm your')
        destination = services.complete_transfer(token)
        self.assertTrue(destination.is_confirmed)
        founder_pass.refresh_from_db()
        self.assertEqual(founder_pass.user_id, destination.pk)
        self.assertEqual(founder_pass.claim_reason, 'transfer')
        self.assertTrue(email_admitted('new@example.com'))
        self.assertFalse(email_admitted(self.user.email))
        self.assertEqual(self.grant().pk, founder_pass.pk)
        self.assertEqual(self.grant('new@example.com').pk, founder_pass.pk)
        with self.assertRaises(ValidationError):
            services.complete_transfer(token)
        with self.assertRaises(ValidationError):
            services.start_transfer(founder_pass.pk, destination, 'third@example.com')
        services.invite(founder_pass.pk, destination, 'friend@example.com')
        with self.assertRaises(NotFound):
            services.invite(founder_pass.pk, self.user, 'old-owner-friend@example.com')

    def test_transfer_preserves_independent_platform_invitation(self):
        AccessAllowlistEntry.objects.create(email=self.user.email)
        founder_pass = self.grant()
        services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        services.complete_transfer(self.token('new@example.com', 'Confirm your'))
        self.assertTrue(email_admitted(self.user.email))

    def test_claim_cancel_and_new_transfer_invalidate_old_link(self):
        for action in ('claim', 'cancel', 'replace'):
            with self.subTest(action=action):
                owner = User.objects.create_user(f'{action}@example.com', is_confirmed=True)
                founder_pass = self.grant(owner.email)
                target = f'{action}-target@example.com'
                services.start_transfer(founder_pass.pk, owner, target)
                token = self.token(target, 'Confirm your')
                if action == 'claim':
                    services.claim(founder_pass.pk, owner)
                elif action == 'cancel':
                    services.cancel_transfer(founder_pass.pk, owner)
                else:
                    services.start_transfer(founder_pass.pk, owner, f'{action}-new@example.com')
                with self.assertRaises(ValidationError):
                    services.complete_transfer(token)
                founder_pass.refresh_from_db()
                self.assertEqual(founder_pass.user_id, owner.pk)

    def test_expired_and_conflicting_transfer_keeps_original_pass(self):
        founder_pass = self.grant()
        transfer = services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your')
        PassTransfer.objects.filter(pk=transfer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            services.complete_transfer(token)
        services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your')
        self.grant('new@example.com')
        with self.assertRaises(ValidationError):
            services.complete_transfer(token)
        founder_pass.refresh_from_db()
        self.assertEqual(founder_pass.user_id, self.user.pk)

    def test_cutoff_autoclaims_absent_recipients_and_preserves_plus_one(self):
        founder_pass = self.grant('absent@example.com')
        self.program.claim_deadline = timezone.now() - timedelta(seconds=1)
        self.program.save()
        self.assertEqual(services.finalize_due_passes(), 1)
        founder_pass.refresh_from_db()
        self.assertEqual(founder_pass.claim_reason, 'deadline')
        self.assertFalse(founder_pass.user.is_confirmed)
        self.assertEqual(services.finalize_due_passes(), 0)
        founder_pass.user.is_confirmed = True
        founder_pass.user.save()
        services.invite(founder_pass.pk, founder_pass.user, 'late-friend@example.com')

    def test_cutoff_enforced_without_scheduler_and_pending_transfer_cannot_win(self):
        founder_pass = self.grant()
        services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your')
        self.program.claim_deadline = timezone.now() - timedelta(seconds=1)
        self.program.save()
        with self.assertRaises(ValidationError):
            services.complete_transfer(token)
        self.client.force_authenticate(self.user)
        data = self.client.get(reverse('founder-passes')).data['passes'][0]
        self.assertEqual(data['claim_reason'], 'deadline')
        self.assertTrue(data['can_invite'])
        self.assertFalse(data['can_transfer'])

    def test_disabled_enrollment_preserves_permanent_access_and_existing_claim(self):
        founder_pass = self.grant()
        self.program.enabled = False
        self.program.save()
        services.claim(founder_pass.pk, self.user)
        self.assertTrue(services.has_founder_access(self.user.pk, self.world.pk))
        self.assertFalse(services.has_founder_access(self.user.pk, self.world.pk + 1))
        self.assertTrue(email_admitted(self.user.email))
        with self.assertRaises(PermissionDenied):
            services.invite(founder_pass.pk, self.user, 'friend@example.com')

    def test_pass_does_not_bypass_closed_installation_or_inactive_account(self):
        self.grant()
        self.control.access_mode = 'closed'
        self.control.save()
        self.assertFalse(email_admitted(self.user.email))
        self.assertEqual(self.client.post(reverse('email-login-request'), {'email': self.user.email}).status_code, 403)
        self.control.access_mode = 'invite_only'
        self.control.save()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.post(reverse('email-login-request'), {'email': self.user.email}).status_code, 400)

    def test_only_owner_can_read_or_mutate_pass(self):
        founder_pass = self.grant()
        intruder = User.objects.create_user('intruder@example.com', is_confirmed=True)
        self.client.force_authenticate(intruder)
        self.assertEqual(self.client.get(reverse('founder-passes')).data['passes'], [])
        response = self.client.post(reverse('founder-action', args=[founder_pass.pk, 'claim']))
        self.assertEqual(response.status_code, 404)

    def test_reserved_signup_does_not_overwrite_account_and_does_not_claim_pass(self):
        founder_pass = self.grant('new@example.com')
        with patch('core.mail.send_login_link') as send:
            response = self.client.post(reverse('signup'), {
                'email': 'NEW@example.com', 'username': 'unverified', 'send_newsletter': True})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(User.objects.filter(email__iexact='new@example.com').count(), 1)
        user = founder_pass.user
        user.refresh_from_db()
        self.assertIsNone(user.username)
        self.assertFalse(user.send_newsletter)
        token = send.call_args.args[1]
        response = self.client.post(reverse('email-login-confirm'), {'token': token})
        self.assertEqual(response.status_code, 201)
        user.refresh_from_db()
        self.assertFalse(user.invited_signup_pending)
        self.assertTrue(user.is_confirmed)
        founder_pass.refresh_from_db()
        self.assertIsNone(founder_pass.claimed_at)

    def test_normal_existing_account_still_rejects_duplicate_signup(self):
        self.grant()
        self.assertEqual(self.client.post(reverse('signup'), {'email': self.user.email}).status_code, 400)

    def test_reserved_signup_does_not_revert_concurrent_email_verification(self):
        founder_pass = self.grant('new@example.com')
        def verify_during_signup(*args):
            User.objects.filter(pk=founder_pass.user_id).update(is_confirmed=True, invited_signup_pending=False)
        with patch('core.mail.send_login_link', side_effect=verify_during_signup):
            response = self.client.post(reverse('signup'), {'email': 'new@example.com'})
        self.assertEqual(response.status_code, 201)
        founder_pass.user.refresh_from_db()
        self.assertTrue(founder_pass.user.is_confirmed)
        self.assertFalse(founder_pass.user.invited_signup_pending)

    def test_warm_admission_is_query_free_and_grant_clears_cached_denial(self):
        self.assertFalse(email_admitted(self.user.email))
        self.grant()
        self.assertTrue(email_admitted(self.user.email))
        with self.assertNumQueries(0):
            for _ in range(20):
                self.assertTrue(email_admitted(self.user.email))

    def test_list_does_not_add_queries_per_pass(self):
        self.grant()
        self.client.force_authenticate(self.user)
        with self.assertNumQueries(3):
            self.client.get(reverse('founder-passes'))
        for index in range(3):
            world = World.objects.create(name=f'Realm {index}', author=self.staff, config=WorldConfig.objects.create())
            program = FounderProgram.objects.create(world=world, enabled=True)
            services.grant(program.pk, self.user.email, actor=self.staff)
        with self.assertNumQueries(3):
            self.assertEqual(len(self.client.get(reverse('founder-passes')).data['passes']), 4)

    def test_email_change_preserves_claim_invitation_identity_and_invalidates_old_login(self):
        founder_pass = services.claim(self.grant().pk, self.user)
        old_link = LoginLinkRequest.objects.create(user=self.user, code_hash='old', expires_ts=timezone.now()+timedelta(hours=1))
        self.user.google_id = 'old-google'
        self.user.save()
        ExternalIdentity.objects.create(user=self.user, issuer='test', subject='old')
        request_email_change(self.user, 'NEW@example.com')
        user = confirm_email_change(self.token('new@example.com', 'Confirm your Written'))
        self.assertEqual(user.pk, self.user.pk)
        self.assertEqual(user.email, 'new@example.com')
        self.assertIsNone(user.google_id)
        self.assertFalse(user.external_identities.exists())
        founder_pass.refresh_from_db()
        self.assertEqual(founder_pass.user_id, user.pk)
        self.assertEqual(founder_pass.claim_reason, 'self')
        self.assertTrue(email_admitted(user.email))
        self.assertFalse(email_admitted('founder@example.com'))
        old_link.refresh_from_db()
        self.assertIsNotNone(old_link.used_ts)
        services.invite(founder_pass.pk, user, 'friend@example.com')
        self.assertEqual(self.grant('founder@example.com').pk, founder_pass.pk)

    def test_email_change_preserves_normal_allowlist_and_never_merges_accounts(self):
        AccessAllowlistEntry.objects.create(email=self.user.email)
        with self.assertRaises(ValidationError):
            request_email_change(self.user, self.staff.email)
        request_email_change(self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your Written')
        user = confirm_email_change(token)
        self.assertTrue(email_admitted(user.email))
        self.assertFalse(email_admitted('founder@example.com'))
        with self.assertRaises(ValidationError):
            confirm_email_change(token)

    def test_email_change_conflict_and_expiry_do_not_change_original_account(self):
        self.grant()
        request_email_change(self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your Written')
        User.objects.create_user('new@example.com')
        with self.assertRaises(ValidationError):
            confirm_email_change(token)
        request_email_change(self.user, 'other@example.com')
        token = self.token('other@example.com', 'Confirm your Written')
        AccountEmailChange.objects.filter(user=self.user, used_at__isnull=True).update(expires_at=timezone.now())
        with self.assertRaises(ValidationError):
            confirm_email_change(token)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'founder@example.com')

    def test_transfer_link_dies_if_destination_changes_email(self):
        founder_pass = self.grant()
        transfer = services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your')
        User.objects.filter(pk=transfer.destination_id).update(email='other@example.com')
        with self.assertRaises(ValidationError):
            services.complete_transfer(token)

    def test_delivery_failure_does_not_duplicate_grant_and_can_retry(self):
        founder_pass = self.grant()
        notice = founder_pass.notices.get()
        with patch('core.mail.send_email', side_effect=RuntimeError('offline')):
            deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'pending')
        self.assertTrue(notice.body)
        self.assertEqual(self.grant().pk, founder_pass.pk)
        self.assertEqual(AccountNotice.objects.count(), 1)
        AccountNotice.objects.filter(pk=notice.pk).update(next_attempt_at=timezone.now())
        with patch('core.mail.send_email', return_value=True) as send:
            deliver_notice(notice.pk)
            deliver_notice(notice.pk)
        self.assertEqual(send.call_count, 1)
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'sent')
        self.assertEqual(notice.body, '')

    def test_notice_dispatch_happens_after_commit_and_broker_failure_is_recoverable(self):
        self.delivery.side_effect = RuntimeError('broker unavailable')
        with self.captureOnCommitCallbacks(execute=True):
            self.grant()
            self.delivery.assert_not_called()
        self.assertTrue(FounderPass.objects.exists())
        self.assertEqual(AccountNotice.objects.get().status, 'pending')

    def test_command_validates_whole_batch_and_dry_run_has_no_writes(self):
        options = {'world_id': self.world.pk, 'actor_id': self.staff.pk, 'stdout': io.StringIO()}
        with self.assertRaises(CommandError):
            call_command('founders', 'grant', 'valid@example.com', 'bad', **options)
        self.assertFalse(FounderPass.objects.exists())
        call_command('founders', 'grant', 'valid@example.com', dry_run=True, **options)
        self.assertFalse(FounderPass.objects.exists())
        call_command('founders', 'grant', 'one@example.com', 'two@example.com', **options)
        self.assertEqual(FounderPass.objects.count(), 2)
        call_command('founders', 'grant', 'ONE@example.com', 'two@example.com', **options)
        self.assertEqual(FounderPass.objects.count(), 2)


class FounderConcurrencyTests(FounderSetup, TransactionTestCase):
    def race(self, *operations):
        barrier = Barrier(len(operations))
        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except APIException as exc:
                return exc
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_two_grants_issue_one_pass_and_one_notice(self):
        results = self.race(lambda: self.grant(), lambda: self.grant())
        self.assertTrue(all(isinstance(result, FounderPass) for result in results))
        self.assertEqual(FounderPass.objects.count(), 1)
        self.assertEqual(AccountNotice.objects.count(), 1)

    def test_two_invites_issue_only_one_gift(self):
        founder_pass = services.claim(self.grant().pk, self.user)
        results = self.race(
            lambda: services.invite(founder_pass.pk, self.user, 'one@example.com'),
            lambda: services.invite(founder_pass.pk, self.user, 'two@example.com'))
        self.assertEqual(sum(isinstance(result, APIException) for result in results), 1)
        self.assertEqual(FounderPass.objects.filter(parent=founder_pass).count(), 1)

    def test_claim_racing_transfer_produces_one_owner(self):
        founder_pass = self.grant()
        services.start_transfer(founder_pass.pk, self.user, 'new@example.com')
        token = self.token('new@example.com', 'Confirm your')
        results = self.race(lambda: services.claim(founder_pass.pk, self.user),
                            lambda: services.complete_transfer(token))
        self.assertEqual(sum(isinstance(result, APIException) for result in results), 1)
        self.assertEqual(FounderPass.objects.count(), 1)
        founder_pass.refresh_from_db()
        self.assertIsNotNone(founder_pass.claimed_at)
        self.assertEqual(FounderEvent.objects.filter(action__in=['claimed', 'transferred']).count(), 1)
