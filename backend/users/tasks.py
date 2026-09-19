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
