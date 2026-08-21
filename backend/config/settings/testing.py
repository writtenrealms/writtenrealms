from backend.config.settings.base import *

DEBUG = True
TESTING = True

def _default_postgres_host():
    env_host = os.environ.get('POSTGRES_HOST')
    if env_host:
        return env_host
    if os.path.exists('/.dockerenv'):
        return 'db'
    try:
        with open('/proc/1/cgroup', 'r', encoding='utf-8') as handle:
            cgroup = handle.read()
            if 'docker' in cgroup or 'containerd' in cgroup:
                return 'db'
    except OSError:
        pass
    return '127.0.0.1'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'wrealms'),
        'USER': os.environ.get('POSTGRES_USER', 'django'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
        'HOST': _default_postgres_host(),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

PRINT_UNSENT_EMAIL = False

RAVEN_CONFIG = {}

# Keep successful test runs focused on the test runner's own output. Expected
# warnings and errors are captured explicitly with assertLogs in the tests that
# exercise those paths. Defining a root handler also prevents imported ASGI
# modules from installing a process-wide console handler via basicConfig.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'null': {
            'class': 'logging.NullHandler',
        },
    },
    'root': {
        'handlers': ['null'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['null'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['null'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Password hashing strength is irrelevant to test behavior and the production
# hashers make fixture creation dominate the backend suite runtime.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

MIGRATION_MODULES = {
    'admin': None,
    'auth': None,
    'contenttypes': None,
    'default': None,
    'sessions': None,

    'core': None,
    'builders': None,
    'quests': None,
    'worlds': None,
    'users': None,
    'lobby': None,
    'system': None,
    'spawns': None,
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

#ALLOWED_HOSTS = ['localhost', 'api', 'forge']

FORCE_SCRIPT_NAME = '/'

REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['email'] = '1000/min'
