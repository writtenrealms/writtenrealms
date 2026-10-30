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
    'maintain-founder-passes': {
        'task': 'users.tasks.maintain_founder_passes',
        'schedule': 60.0,
        'options': {'expires': 55},
    },
    'prune-account-challenges': {
        'task': 'users.tasks.prune_account_challenges',
        'schedule': crontab(minute='45'),
    },
    'prune-mob-conversations': {
        'task': 'spawns.conversations.tasks.prune_conversations',
        'schedule': 60.0,
        'options': {'expires': 60},
    },
    'finish-mob-conversations': {
        # Recover missed completion notices and handle idle/hard expiry.
        'task': 'spawns.conversations.tasks.finish_conversations',
        'schedule': 1.0,
        'options': {'expires': 1},
    },
    'dispatch-inference': {
        # Recover missed after-commit notices and expired worker leases.
        'task': 'inference.tasks.dispatch_requests',
        'schedule': 2.0,
        'options': {'expires': 2},
    },
    'prune-inference': {
        'task': 'inference.tasks.prune_requests',
        'schedule': crontab(minute='35'),
        'options': {'expires': 3300},
    },
    'prune-support-events': {
        'task': 'system.tasks.prune_support_events',
        'schedule': crontab(minute='40'),
        'options': {'expires': 3300},
    },
    'dispatch-item-upkeep': {
        'task': 'spawns.tasks.dispatch_item_upkeep',
        'schedule': 5.0,
        'options': {'expires': 5},
    },
    'run-world-door-upkeep': {
        'task': 'worlds.tasks.run_world_door_upkeep',
        'schedule': 5.0,
        'options': {'expires': 5},
    },
    'cleanup-wr1-sign-ins': {
        'task': 'users.tasks.cleanup_wr1_sign_ins',
        'schedule': crontab(minute='*/10'),
    },
    'recover-instance-time': {
        'task': 'spawns.tasks.recover_instance_time',
        'schedule': _heartbeat_interval_seconds(),
        'options': {'expires': _heartbeat_interval_seconds()},
    },
    'game-heartbeat': {
        'task': 'spawns.tasks.game_heartbeat',
        'schedule': _heartbeat_interval_seconds(),
    },
    'run-scheduled-trigger-steps': {
        'task': 'spawns.tasks.run_scheduled_trigger_steps',
        'schedule': _heartbeat_interval_seconds(),
        # A later poll sees every still-due row, so stale poll messages add no
        # value after broker/worker backpressure and should not pile up.
        'options': {'expires': _heartbeat_interval_seconds()},
    },
    'run-due-prepared-game-actions': {
        'task': 'spawns.tasks.run_due_prepared_game_actions',
        'schedule': _heartbeat_interval_seconds(),
        # ETA tasks normally resolve these at 2.5s. This bounded poll is the
        # durable recovery path if an ETA delivery is lost.
        'options': {'expires': _heartbeat_interval_seconds()},
    },
    'run-due-combat-encounters': {
        'task': 'spawns.tasks.run_due_combat_encounters',
        'schedule': _heartbeat_interval_seconds(),
        # Scheduled per-encounter tasks are the fast path. This indexed,
        # bounded poll repairs an ETA lost to a worker or broker failure.
        'options': {'expires': _heartbeat_interval_seconds()},
    },
    'run-world-spawn-plans': {
        'task': 'worlds.tasks.run_world_spawn_plans',
        'schedule': _spawn_plan_interval_seconds(),
    },
    'cleanup-stale-connections': {
        'task': 'users.tasks.cleanup_stale_connections',
        'schedule': crontab(minute='*/5'),  # Runs every 5 minutes
    },
    'prune-communication-messages': {
        'task': 'spawns.tasks.prune_communication_messages',
        'schedule': crontab(minute='*'),
        'options': {'expires': 55},
    },
    'prune-crafting-action-receipts': {
        'task': 'spawns.tasks.prune_crafting_action_receipts',
        'schedule': crontab(hour='4', minute='20'),
    },
    'prune-death-resolution-receipts': {
        'task': 'spawns.tasks.prune_death_resolution_receipts',
        'schedule': crontab(minute='25'),
        'options': {'expires': 3300},
    },
    'prune-scheduled-trigger-runs': {
        'task': 'spawns.tasks.prune_scheduled_trigger_runs',
        'schedule': crontab(minute='25'),
        'options': {'expires': 3300},
    },
    'prune-prepared-game-actions': {
        'task': 'spawns.tasks.prune_prepared_game_actions',
        'schedule': crontab(hour='4', minute='25'),
        'options': {'expires': 3300},
    },
    'monitor-worlds': {
      'task': 'worlds.tasks.monitor_worlds',
        'schedule': crontab(minute='*/1'),  # Runs every minute
    },
}
