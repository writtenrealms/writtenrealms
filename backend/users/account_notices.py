"""After-commit email delivery with a bounded, durable retry path."""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from users.models import AccountNotice

logger = logging.getLogger(__name__)


def queue_notice(recipient, subject, body, *, founder_pass=None, expires_at=None):
    now = timezone.now()
    notice = AccountNotice.objects.create(
        recipient=recipient, subject=subject, body=body, founder_pass=founder_pass,
        expires_at=expires_at or now + timedelta(days=7), next_attempt_at=now)

    def dispatch():
        from users.tasks import deliver_account_notice
        try:
            deliver_account_notice.delay(notice.pk)
        except Exception:
            # Never turn a committed grant into an apparent failure. Beat recovers.
            logger.warning('Account notice %s queued for recovery', notice.pk)
    transaction.on_commit(dispatch)
    return notice


def deliver_notice(notice_id):
    from core.mail import send_email
    now = timezone.now()
    with transaction.atomic():
        notice = AccountNotice.objects.select_for_update(skip_locked=True).filter(
            pk=notice_id, status='pending', next_attempt_at__lte=now).first()
        if not notice:
            return
        if notice.expires_at <= now:
            notice.status, notice.body = 'expired', ''
            notice.save(update_fields=['status', 'body'])
            return
        notice.attempts += 1
        notice.next_attempt_at = now + timedelta(minutes=5)
        notice.save(update_fields=['attempts', 'next_attempt_at'])
    try:
        sent = send_email(notice.subject, notice.body, [notice.recipient])
        status = 'sent' if sent else 'suppressed'
    except Exception:
        status = 'failed' if notice.attempts >= 5 else 'pending'
        logger.warning('Account notice %s delivery attempt %s failed', notice.pk, notice.attempts)
    updates = {'status': status}
    if status != 'pending':
        updates['body'] = ''
    else:
        updates['next_attempt_at'] = timezone.now() + timedelta(minutes=2 ** notice.attempts)
    AccountNotice.objects.filter(pk=notice.pk, status='pending').update(**updates)
