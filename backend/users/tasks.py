from datetime import datetime, timedelta
from django.core.cache import cache
from celery import shared_task
from fastapi_app.forge_ws import check_heartbeats


@shared_task
def cleanup_stale_connections():
    check_heartbeats()
