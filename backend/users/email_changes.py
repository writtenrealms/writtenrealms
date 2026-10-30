import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from founders.services import normalize_email, require_member, token_hash
from system.access import clear_access_cache, require_platform_access
from system.models import AccessAllowlistEntry, StaffAuditEvent
from users.account_notices import queue_notice
from users.models import AccountEmailChange, ExternalIdentity, LoginLinkRequest

User = get_user_model()


@transaction.atomic
def request_email_change(user, email):
    require_member(user)
    email = normalize_email(email)
    user = User.objects.select_for_update().get(pk=user.pk)
    require_member(user)
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({'email': 'This email already belongs to an account. Accounts cannot be merged here.'})
    now = timezone.now()
    AccountEmailChange.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=1)
    AccountEmailChange.objects.create(user=user, source_email=user.email,
        destination_email=email, token_hash=token_hash(token), expires_at=expires)
    queue_notice(email, 'Confirm your Written Realms email change',
        f"Confirm replacing {user.email} with this address on your Written Realms account. "
        "Your characters and Founder’s Passes stay with your account. "
        "Afterward, use your new email to sign in; previous linked sign-ins will be disconnected.\n\n"
        f"{settings.SITE_BASE}/account/email-confirm#token={token}\n\n"
        "This link expires in one hour. Ignore it if you did not request this change.", expires_at=expires)
    queue_notice(user.email, 'Written Realms email change requested',
        f"An email change to {email} was requested from your signed-in account. "
        "Your address has not changed yet. Contact the site administrator if this was not you.")


@transaction.atomic
def confirm_email_change(token):
    change = AccountEmailChange.objects.filter(token_hash=token_hash(token)).first()
    if change is None:
        raise ValidationError('This email change link is invalid or expired.')
    user = User.objects.select_for_update().get(pk=change.user_id)
    change = AccountEmailChange.objects.select_for_update().get(pk=change.pk)
    now = timezone.now()
    require_member(user)
    require_platform_access(user=user)
    if change.used_at or change.expires_at <= now or user.email != change.source_email:
        raise ValidationError('This email change link is invalid or expired.')
    if User.objects.filter(email__iexact=change.destination_email).exists():
        raise ValidationError('This address now belongs to an account. Your email has not changed.')
    old_email = user.email
    try:
        with transaction.atomic():
            user.email = change.destination_email
            user.google_id = None
            user.invited_signup_pending = False
            user.save(update_fields=['email', 'google_id', 'invited_signup_pending'])
    except IntegrityError:
        raise ValidationError('This address now belongs to an account. Your email has not changed.')
    # Preserve ordinary admission. Founder admission follows the User FK.
    invitations = AccessAllowlistEntry.objects.select_for_update().filter(email__iexact=old_email)
    if invitations.exists():
        AccessAllowlistEntry.objects.get_or_create(email=user.email, defaults={
            'note': 'Preserved through a verified account email change.'})
        invitations.delete()
    ExternalIdentity.objects.filter(user=user).delete()
    LoginLinkRequest.objects.filter(user=user, used_ts__isnull=True).update(used_ts=now)
    change.used_at = now
    change.save(update_fields=['used_at'])
    clear_access_cache()
    transaction.on_commit(clear_access_cache)
    StaffAuditEvent.objects.create(actor=user, actor_email=user.email,
        action='account.email_changed', target=str(user.pk),
        details={'previous_email': old_email, 'email': user.email})
    for email in (old_email, user.email):
        queue_notice(email, 'Written Realms email changed',
            f"Your account email changed from {old_email} to {user.email}. "
            "Your characters and Founder’s Passes remain on the same account. "
            "Use the new email address to sign in.")
    return user
