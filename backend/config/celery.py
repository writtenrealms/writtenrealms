from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.config.settings.container')

app = Celery('backend')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'cleanup-stale-connections': {
        'task': 'users.tasks.cleanup_stale_connections',
        'schedule': crontab(minute='*/5'),  # Runs every 5 minutes
    },
    'monitor-worlds': {
      'task': 'worlds.tasks.monitor_worlds',
        'schedule': crontab(minute='*/1'),  # Runs every minute
    },
}