from datetime import datetime, timedelta
from django.core.cache import cache
from celery import shared_task
from fastapi_app.forge_ws import check_heartbeats


@shared_task
def cleanup_stale_connections():
    check_heartbeats()


@shared_task
def cleanup_wr1_sign_ins():
    from django.utils import timezone
    from users.models import WR1SignIn
    ids = list(WR1SignIn.objects.filter(expires_ts__lte=timezone.now())
               .order_by('expires_ts').values_list('pk', flat=True)[:10000])
    WR1SignIn.objects.filter(pk__in=ids).delete()


@shared_task
def deliver_account_notice(notice_id):
    from users.account_notices import deliver_notice
    deliver_notice(notice_id)


@shared_task
def maintain_founder_passes():
    from django.utils import timezone
    from founders.services import finalize_due_passes
    from users.models import AccountNotice
    finalize_due_passes()
    ids = list(AccountNotice.objects.filter(status='pending', next_attempt_at__lte=timezone.now())
               .order_by('next_attempt_at').values_list('pk', flat=True)[:100])
    for notice_id in ids:
        deliver_account_notice.delay(notice_id)


@shared_task
def prune_account_challenges():
    from django.utils import timezone
    from founders.models import PassTransfer
    from users.models import AccountEmailChange, AccountNotice
    cutoff = timezone.now() - timedelta(days=30)
    for model, query in (
        (PassTransfer, {'expires_at__lt': cutoff}),
        (AccountEmailChange, {'expires_at__lt': cutoff}),
        (AccountNotice, {'expires_at__lt': cutoff}),
    ):
        ids = list(model.objects.filter(**query).order_by('pk').values_list('pk', flat=True)[:1000])
        model.objects.filter(pk__in=ids).delete()
