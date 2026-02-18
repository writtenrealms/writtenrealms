from backend.config.settings.base import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'wrealms'),
        'USER': os.environ.get('POSTGRES_USER', 'django'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}

ALLOWED_HOSTS = ['writtenrealms.com', 'localhost', 'api.writtenrealms.com']

STATIC_ROOT = '/code/backend/static'
LOG_DIR = '/code/logs'

SITE_BASE = 'https://writtenrealms.com'
SEND_EMAIL = True

sentry_dsn = os.environ.get('SENTRY_DSN')
if sentry_dsn:
    RAVEN_CONFIG = {
        'dsn': sentry_dsn,
        # If you are using git, you can also automatically configure the
        # release based on the git info.
    }
else:
    RAVEN_CONFIG = {}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
        'console_log': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'console.log'),
        },
        'error_log': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'error.log'),
        }
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'console_log'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
        },
        'django.request': {
            'handlers': ['error_log'],
            'level': 'ERROR',
            'propagate': False,
        },
    },

}

USE_INJECTION = True

AUTO_START_MPWS = True
