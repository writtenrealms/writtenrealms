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

ALLOWED_HOSTS = ['dev.writtenrealms.com', 'ptr.writtenrealms.com', 'localhost']

STATIC_ROOT = '/code/backend/static'
LOG_DIR = '/code/logs'

SITE_BASE = 'https://devwrittenrealms.com'
SEND_EMAIL = False

RAVEN_CONFIG = {
}

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
