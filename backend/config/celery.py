from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.schedules import crontab
from config import game_settings as adv_config

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.config.settings.container')

app = Celery('backend')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


def _heartbeat_interval_seconds() -> float:
    raw_interval = getattr(adv_config, "GAME_HEARTBEAT_INTERVAL_SECONDS", 2)
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        return 2.0
    return max(interval, 1.0)


def _spawn_plan_interval_seconds() -> float:
    raw_interval = getattr(adv_config, "GAME_SPAWN_PLAN_INTERVAL_SECONDS", 15)
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        return 15.0
    return max(interval, 1.0)


app.conf.beat_schedule = {
    'game-heartbeat': {
        'task': 'spawns.tasks.game_heartbeat',
        'schedule': _heartbeat_interval_seconds(),
    },
    'run-world-spawn-plans': {
        'task': 'worlds.tasks.run_world_spawn_plans',
        'schedule': _spawn_plan_interval_seconds(),
    },
    'cleanup-stale-connections': {
        'task': 'users.tasks.cleanup_stale_connections',
        'schedule': crontab(minute='*/5'),  # Runs every 5 minutes
    },
    'prune-crafting-action-receipts': {
        'task': 'spawns.tasks.prune_crafting_action_receipts',
        'schedule': crontab(hour='4', minute='20'),
    },
    'monitor-worlds': {
      'task': 'worlds.tasks.monitor_worlds',
        'schedule': crontab(minute='*/1'),  # Runs every minute
    },
}
