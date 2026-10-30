"""Operator-only Founder’s Pass administration; no world-builder endpoint."""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException

from founders.models import FounderPass, FounderProgram
from founders.services import event, grant, normalize_email, notify_grant
from system.models import SiteControl, StaffAuditEvent
from worlds.models import World


class Command(BaseCommand):
    help = 'Configure, grant, inspect, and resend world-specific Founder’s Passes.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['configure', 'grant', 'status', 'resend'])
        parser.add_argument('emails', nargs='*')
        parser.add_argument('--world-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--enable', action='store_true')
        parser.add_argument('--disable', action='store_true')
        parser.add_argument('--claim-deadline', help='ISO timestamp with timezone, or none')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        actor = None
        if options['actor_id']:
            actor = get_user_model().objects.filter(pk=options['actor_id'], is_staff=True, is_active=True).first()
            if actor is None:
                raise CommandError('The actor must be an active site administrator.')
        world = World.objects.filter(pk=options['world_id'], instance_of__isnull=True, context__isnull=True).first()
        if world is None:
            raise CommandError('Select an authored root world, not a runtime or instance.')
        action = options['action']
        if action == 'configure':
            return self.configure(world, actor, options)
        if options['enable'] or options['disable'] or options['claim_deadline'] is not None:
            raise CommandError('Configuration options require the configure action.')
        program = FounderProgram.objects.filter(world=world).first()
        if program is None:
            raise CommandError('No founder program exists for this world. Run configure --enable first.')
        if action == 'status':
            self.stdout.write(json.dumps({
                'world_id': world.pk, 'world': world.name,
                'installation_enabled': SiteControl.objects.filter(name='prod', founders_enabled=True).exists(),
                'world_enabled': program.enabled,
                'claim_deadline': program.claim_deadline.isoformat() if program.claim_deadline else None,
                'direct': program.passes.filter(kind='direct').count(),
                'gifted': program.passes.filter(kind='gift').count(),
                'unclaimed': program.passes.filter(claimed_at__isnull=True).count(),
            }))
            if not options['emails']:
                return
        if not options['emails']:
            raise CommandError('Provide one or more email addresses.')
        # Validate the whole input before creating anything.
        try:
            emails = list(dict.fromkeys(normalize_email(email) for email in options['emails']))
        except APIException as exc:
            raise CommandError(str(exc.detail)) from None
        failures = 0
        for email in emails:
            try:
                if options['dry_run']:
                    self.stdout.write(f'{email}: would {action}; no changes or email sent')
                    continue
                if action == 'grant':
                    founder_pass, created = grant(program.pk, email, actor=actor)
                    outcome = 'granted; notification queued' if created else 'already issued; unchanged'
                else:
                    from django.db.models import Q
                    founder_pass = FounderPass.objects.select_related('program__world', 'user').filter(
                        program=program).filter(Q(issued_email__iexact=email) | Q(user__email__iexact=email)).first()
                    if founder_pass is None:
                        raise CommandError('No pass found.')
                    if action == 'resend':
                        with transaction.atomic():
                            notify_grant(founder_pass)
                            event(founder_pass, 'notification_resent', actor)
                        outcome = 'notification queued to current owner'
                    else:
                        outcome = 'status'
                notice = founder_pass.notices.order_by('-pk').values_list('status', flat=True).first()
                self.stdout.write(json.dumps({'email': email, 'outcome': outcome,
                    'pass_id': founder_pass.pk, 'owner_email': founder_pass.user.email,
                    'kind': founder_pass.kind, 'claimed': founder_pass.claimed_at is not None,
                    'notification': notice}))
            except (APIException, CommandError, IntegrityError) as exc:
                failures += 1
                detail = str(exc.detail) if isinstance(exc, APIException) else (
                    'Destination changed concurrently; retry safely.' if isinstance(exc, IntegrityError) else str(exc))
                self.stderr.write(f'{email}: {detail}')
        if failures:
            raise CommandError(f'{failures} address(es) failed. Successful grants are retained; rerunning is safe.')

    def configure(self, world, actor, options):
        if options['enable'] and options['disable']:
            raise CommandError('Choose either --enable or --disable.')
        deadline = options['claim_deadline']
        if deadline is not None and deadline != 'none':
            try:
                deadline = parse_datetime(deadline)
            except ValueError:
                deadline = None
            if deadline is None or timezone.is_naive(deadline):
                raise CommandError('Use an ISO timestamp with an explicit timezone, or none.')
        if options['dry_run']:
            self.stdout.write(f'Would configure the founder program for {world.name}; no changes.')
            return
        with transaction.atomic():
            control, _ = SiteControl.objects.get_or_create(name='prod')
            program, _ = FounderProgram.objects.get_or_create(world=world)
            program = FounderProgram.objects.select_for_update().get(pk=program.pk)
            if options['enable']:
                control.founders_enabled = True
                control.save(update_fields=['founders_enabled'])
                program.enabled = True
            if options['disable']:
                program.enabled = False
            if options['claim_deadline'] is not None:
                program.claim_deadline = None if deadline == 'none' else deadline
            program.save()
            StaffAuditEvent.objects.create(actor=actor, actor_email=actor.email if actor else '',
                action='founders.configured', target=str(world.pk), details={
                    'enabled': program.enabled,
                    'claim_deadline': program.claim_deadline.isoformat() if program.claim_deadline else None})
        self.stdout.write(f'Founder program configured for {world.name}: enabled={program.enabled}; deadline={program.claim_deadline}')
