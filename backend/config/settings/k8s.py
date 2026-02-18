import os

from backend.config.settings.base import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'wrealms',
        'USER': 'django',
        'HOST': os.getenv('WR_PG_HOST', 'localhost'),
        'PASSWORD': os.getenv('WR_PG_PASSWORD'),
        'PORT': os.getenv('WR_PG_PORT', 5432),
    }
}

ALLOWED_HOSTS = ['localhost', 'api', 'forge']

# Expand allowed hosts if ALLOWED_HOSTS env var is defined
# Support comma-separated list of hosts
hosts_list_str = os.getenv('ALLOWED_HOSTS', '')
hosts_list = hosts_list_str.split(',') if hosts_list_str else []
if hosts_list:
  ALLOWED_HOSTS.extend([ h.strip() for h in hosts_list])

REDIS_HOST = 'redis'

AUTO_START_MPWS = True

USE_INJECTION = True
INJECTION_TIMESTAMPS = True

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'amqp://rabbitmq:5672')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis-celery:6379/0')

STATIC_ROOT = '/code/backend/static'
FORCE_SCRIPT_NAME = '/forge'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'LOCATION': 'memcached:11211',
    }
}

SITE_BASE = 'https://writtenrealms.com'
SEND_EMAIL = os.getenv('WR_ENV') == 'prod'

# Whether to integrate with an LLM for puzzle solving.
PUZZLE_LLM = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'wr_json': {
            '()': 'backend.core.wr_logs.WRJsonFormatter',
        },
        'simple': {
            'format': '%(asctime)s %(levelname)s %(message)s',
            'datefmt': '%Y-%m-%dT%H:%M:%S',
        },
        'error_formatter': {
            'format': '%(asctime)s %(levelname)s %(message)s\n%(exc_info)s',
            'datefmt': '%Y-%m-%dT%H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'wr_json',
        },
        'stderr': {
            'level': 'ERROR',
            'class': 'logging.StreamHandler',
            'formatter': 'error_formatter',
            'stream': 'ext://sys.stderr',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'django.request': {  # Updated logger configuration
            'handlers': ['stderr'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['stderr'],
            'level': 'ERROR',
            'propagate': False,
        },
        'forge_ws': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'lifecycle': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'security': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
