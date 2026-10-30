"""Founder ownership changes are serialized per program, never on game paths."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from founders.models import FounderEvent, FounderPass, FounderProgram, PassTransfer
from system.models import SiteControl
from users.account_notices import queue_notice

User = get_user_model()


def normalize_email(value):
    email = value.strip().casefold() if isinstance(value, str) else ''
    try:
        validate_email(email)
    except DjangoValidationError:
        raise ValidationError({'email': 'Enter a valid email address.'})
    if len(email) > 255:
        raise ValidationError({'email': 'Email address is too long.'})
    return email


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def require_member(user):
    if not user.is_active or user.is_temporary or not user.is_confirmed or user.is_invalid:
        raise PermissionDenied('Sign in with a verified email address first.')


def require_enabled(program):
    if not program.enabled or not SiteControl.objects.filter(name='prod', founders_enabled=True).exists():
        raise PermissionDenied("Founder’s Pass enrollment is currently disabled.")


def reserve_user(email):
    user, _ = User.objects.get_or_create(email__iexact=email, defaults={
        'email': email, 'password': '!', 'invited_signup_pending': True})
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.email.casefold() != email:
        raise ValidationError({'email': 'This account changed its email. Refresh and try again.'})
    if not user.is_active or user.is_invalid or user.is_temporary:
        raise ValidationError({'email': 'This address cannot receive a pass.'})
    return user


def event(founder_pass, action, actor=None, **details):
    FounderEvent.objects.create(founder_pass=founder_pass, actor=actor,
                                action=action, details=details)


def notify_grant(founder_pass):
    world = founder_pass.program.world.name
    direct = founder_pass.kind == 'direct'
    body = (
        f"You’ve received a Founder’s Pass for {world}!\n\n"
        f"It grants your account free access to {world} for as long as the world operates. "
        "Normal conduct rules and usage limits still apply.\n\n"
        "You can now sign in with this email address, even while signup is invite-only.\n"
        f"{settings.SITE_BASE}/account/passes\n\n")
    if direct:
        if founder_pass.claimed_at:
            body += "This pass is already claimed on your account and cannot be transferred. "
        else:
            body += ("Claim it to keep it on your account, or transfer it once before claiming. "
                 "A completed transfer claims the pass on the destination account. "
                 )
        body += ("The account holding this claimed pass can invite one +1 to receive a Founder’s Pass. "
                 "The +1 cannot transfer their pass or invite another person.\n")
        deadline = founder_pass.program.claim_deadline
        body += (f"Unclaimed passes automatically lock to their accounts at {deadline.isoformat()}.\n"
                 if deadline else "Unclaimed passes will automatically lock to their accounts when beta ends; no deadline has been set yet.\n")
    else:
        body += "This gifted pass is already claimed on your account. It cannot be transferred and does not include another invitation.\n"
    return queue_notice(founder_pass.user.email, f"Your Founder’s Pass for {world}", body,
                        founder_pass=founder_pass)


def settle_pass(founder_pass, now=None):
    now = now or timezone.now()
    deadline = founder_pass.program.claim_deadline
    if founder_pass.claimed_at is None and deadline and deadline <= now:
        founder_pass.claimed_at = deadline
        founder_pass.claim_reason = 'deadline'
        founder_pass.save(update_fields=['claimed_at', 'claim_reason'])
        PassTransfer.objects.filter(founder_pass=founder_pass, active=True).update(active=False)
        event(founder_pass, 'claimed', reason='deadline')
    return founder_pass


@transaction.atomic
def grant(program_id, email, *, actor=None):
    if actor is not None and not (actor.is_staff and actor.is_active):
        raise PermissionDenied('Only site administrators can issue direct passes.')
    email = normalize_email(email)
    program = FounderProgram.objects.select_for_update(of=('self',)).select_related('world').get(pk=program_id)
    require_enabled(program)
    existing = FounderPass.objects.select_related('user').filter(program=program).filter(
        Q(issued_email__iexact=email) | Q(user__email__iexact=email)).first()
    if existing:
        return existing, False
    user = reserve_user(email)
    founder_pass = FounderPass.objects.create(program=program, user=user,
                                              issued_email=email, kind='direct')
    event(founder_pass, 'granted', actor, email=email)
    settle_pass(founder_pass)
    notify_grant(founder_pass)
    return founder_pass, True


def locked_pass(pass_id, user=None):
    # All pass mutations acquire the program before the pass. This also orders
    # deadline, grant, transfer and gift races without locking gameplay objects.
    identity = FounderPass.objects.filter(pk=pass_id)
    if user is not None:
        identity = identity.filter(user=user)
    program_id = identity.values_list('program_id', flat=True).first()
    if program_id is None:
        raise NotFound()
    program = FounderProgram.objects.select_for_update(of=('self',)).select_related('world').get(pk=program_id)
    founder_pass = FounderPass.objects.select_for_update(of=('self',)).select_related('user').get(pk=pass_id)
    if user is not None and founder_pass.user_id != user.pk:
        raise NotFound()
    founder_pass.program = program
    return founder_pass


@transaction.atomic
def claim(pass_id, user):
    require_member(user)
    founder_pass = locked_pass(pass_id, user)
    settle_pass(founder_pass)
    if founder_pass.claimed_at is None:
        founder_pass.claimed_at = timezone.now()
        founder_pass.claim_reason = 'self'
        founder_pass.save(update_fields=['claimed_at', 'claim_reason'])
        PassTransfer.objects.filter(founder_pass=founder_pass, active=True).update(active=False)
        event(founder_pass, 'claimed', user, reason='self')
    return founder_pass


@transaction.atomic
def invite(pass_id, user, email):
    require_member(user)
    email = normalize_email(email)
    founder_pass = locked_pass(pass_id, user)
    require_enabled(founder_pass.program)
    settle_pass(founder_pass)
    if founder_pass.kind != 'direct' or founder_pass.claimed_at is None:
        raise ValidationError('Claim your direct Founder’s Pass before inviting your +1.')
    existing = FounderPass.objects.filter(parent=founder_pass).first()
    if existing:
        if existing.issued_email == email:
            return existing, False
        raise ValidationError('Your +1 invitation has already been used.')
    if FounderPass.objects.filter(program=founder_pass.program).filter(
            Q(issued_email__iexact=email) | Q(user__email__iexact=email)).exists():
        raise ValidationError({'email': 'This address already has a pass or previously received one. Your invitation has not been used.'})
    recipient = reserve_user(email)
    if recipient.pk == user.pk:
        raise ValidationError({'email': 'Choose someone other than yourself.'})
    gift = FounderPass.objects.create(program=founder_pass.program, user=recipient,
        issued_email=email, kind='gift', parent=founder_pass,
        claimed_at=timezone.now(), claim_reason='gift')
    event(gift, 'gifted', user, parent_id=founder_pass.pk, email=email)
    event(founder_pass, 'invited', user, gift_id=gift.pk, email=email)
    notify_grant(gift)
    return gift, True


@transaction.atomic
def start_transfer(pass_id, user, email):
    require_member(user)
    email = normalize_email(email)
    founder_pass = locked_pass(pass_id, user)
    require_enabled(founder_pass.program)
    settle_pass(founder_pass)
    if founder_pass.kind != 'direct' or founder_pass.claimed_at is not None:
        raise ValidationError('Only an unclaimed direct pass can be transferred.')
    if FounderPass.objects.filter(program=founder_pass.program).filter(
            Q(issued_email__iexact=email) | Q(user__email__iexact=email)).exists():
        raise ValidationError({'email': 'This address already has a pass or previously received one.'})
    destination = reserve_user(email)
    if destination.pk == user.pk:
        raise ValidationError({'email': 'Choose a different account, or change your account email instead.'})
    PassTransfer.objects.filter(founder_pass=founder_pass, active=True).update(active=False)
    token = secrets.token_urlsafe(32)
    expires = timezone.now() + timedelta(hours=24)
    if founder_pass.program.claim_deadline:
        expires = min(expires, founder_pass.program.claim_deadline)
    transfer = PassTransfer.objects.create(founder_pass=founder_pass,
        source_email=founder_pass.user.email, destination=destination,
        destination_email=email, token_hash=token_hash(token), expires_at=expires)
    queue_notice(email, f"Confirm your Founder’s Pass transfer for {founder_pass.program.world.name}",
        "A Founder’s Pass has been offered to this address. Confirm to move the pass "
        "and its unused +1 invitation to your account. This permanently claims the pass; "
        "characters and progress stay with their original account.\n\n"
        f"{settings.SITE_BASE}/account/pass-transfer#token={token}\n\n"
        f"This link expires at {expires.isoformat()}. Ignore it if you do not want the transfer.",
        founder_pass=founder_pass, expires_at=expires)
    event(founder_pass, 'transfer_requested', user, destination_email=email)
    return transfer


@transaction.atomic
def cancel_transfer(pass_id, user):
    require_member(user)
    founder_pass = locked_pass(pass_id, user)
    count = PassTransfer.objects.filter(founder_pass=founder_pass, active=True).update(active=False)
    if count:
        event(founder_pass, 'transfer_canceled', user)


@transaction.atomic
def complete_transfer(token):
    transfer = PassTransfer.objects.filter(token_hash=token_hash(token)).first()
    if not transfer:
        raise ValidationError('This transfer link is invalid or expired.')
    founder_pass = locked_pass(transfer.founder_pass_id)
    require_enabled(founder_pass.program)
    transfer = PassTransfer.objects.select_for_update().get(pk=transfer.pk)
    now = timezone.now()
    if (not transfer.active or transfer.expires_at <= now or founder_pass.claimed_at or
            (founder_pass.program.claim_deadline and founder_pass.program.claim_deadline <= now)):
        raise ValidationError('This transfer link is invalid, expired, or the pass has already been claimed.')
    owners = {u.pk: u for u in User.objects.select_for_update().filter(
        pk__in=[founder_pass.user_id, transfer.destination_id]).order_by('pk')}
    source, destination = owners[founder_pass.user_id], owners[transfer.destination_id]
    if (source.email != transfer.source_email or destination.email != transfer.destination_email or
            not source.is_active or not destination.is_active or destination.is_invalid):
        raise ValidationError('An account changed since this transfer was requested. Request a new transfer.')
    if FounderPass.objects.filter(program=founder_pass.program, user=destination).exists():
        raise ValidationError('The destination account already has a pass. The original pass has not moved.')
    # A closed installation stays closed, including confirmation links.
    from system.access import access_mode
    if access_mode() == 'closed' and not destination.is_staff:
        raise PermissionDenied('This site is currently closed. Please check back later.')
    founder_pass.user = destination
    founder_pass.claimed_at, founder_pass.claim_reason = now, 'transfer'
    founder_pass.save(update_fields=['user', 'claimed_at', 'claim_reason'])
    destination.is_confirmed, destination.invited_signup_pending = True, False
    destination.save(update_fields=['is_confirmed', 'invited_signup_pending'])
    transfer.active, transfer.completed_at = False, now
    transfer.save(update_fields=['active', 'completed_at'])
    event(founder_pass, 'transferred', destination, source_user_id=source.pk,
          destination_user_id=destination.pk, source_email=source.email, destination_email=destination.email)
    for owner in (source, destination):
        queue_notice(owner.email, 'Founder’s Pass transfer completed',
            f"The Founder’s Pass for {founder_pass.program.world.name} has moved from "
            f"{source.email} to {destination.email} and is now claimed. "
            "Its unused +1 invitation follows the pass. Characters and progress have not moved.",
            founder_pass=founder_pass)
    return destination


def finalize_due_passes(limit=500):
    ids = list(FounderPass.objects.filter(claimed_at__isnull=True,
        program__claim_deadline__lte=timezone.now()).order_by('program_id', 'pk')
        .values_list('pk', flat=True)[:limit])
    for pass_id in ids:
        with transaction.atomic():
            settle_pass(locked_pass(pass_id))
    return len(ids)


def has_founder_access(user_id, world_id):
    """Permanent world entitlement; enrollment switches never revoke it.

    Future billing can combine this with subscriptions/earned access. Call at
    world admission, not per command, tick, or NPC inference request.
    """
    return FounderPass.objects.filter(user_id=user_id, program__world_id=world_id).exists()
