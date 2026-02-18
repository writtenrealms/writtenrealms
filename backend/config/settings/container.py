from backend.config.settings.base import *

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'wrealms'),
        'USER': os.environ.get('POSTGRES_USER', 'django'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
        'HOST': 'db',
        'PORT': '5432',
    }
}

ALLOWED_HOSTS = ['localhost', 'api']

REDIS_HOST = 'redis'

AUTO_START_MPWS = True

USE_INJECTION = True
INJECTION_TIMESTAMPS = True

