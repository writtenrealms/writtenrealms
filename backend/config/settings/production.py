"""Environment-configured production settings for independent Core servers."""
from django.core.exceptions import ImproperlyConfigured

from backend.config.settings.base import *

DEBUG = False
for name in ('DJANGO_SECRET_KEY', 'JWT_SECRET'):
    if len(os.environ.get(name, '')) < 50:
        raise ImproperlyConfigured(f'{name} must contain at least 50 characters.')

ALLOWED_HOSTS = [host.strip() for host in os.environ['ALLOWED_HOSTS'].split(',') if host.strip()]
SITE_BASE = os.environ['WR_SITE_BASE'].rstrip('/')
if not SITE_BASE.startswith('https://'):
    raise ImproperlyConfigured('WR_SITE_BASE must use HTTPS in production.')
CSRF_TRUSTED_ORIGINS = [SITE_BASE]
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [SITE_BASE]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# A deployment controls this exact hostname, not its future child hostnames or
# a browser preload submission. HTTPS, secure cookies, and host validation stay enforced.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ['POSTGRES_DB'],
        'USER': os.environ['POSTGRES_USER'],
        'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        'HOST': os.environ['POSTGRES_HOST'],
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 0,
        'DISABLE_SERVER_SIDE_CURSORS': True,
        'OPTIONS': {
            'sslmode': 'verify-full',
            'sslrootcert': os.environ['POSTGRES_SSLROOTCERT'],
            'connect_timeout': 10,
        },
    }
}
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.environ.get('DJANGO_CACHE_URL', 'redis://redis:6379/1'),
        'TIMEOUT': 300,
    }
}
CELERY_BROKER_URL = os.environ['CELERY_BROKER_URL']
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis-celery:6379/0')
CELERY_RESULT_EXPIRES = 3600
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
STATIC_ROOT = '/code/backend/static'
SEND_EMAIL = os.environ.get('WR_SEND_EMAIL', '0') == '1'
PRINT_UNSENT_EMAIL = False
AUTO_START_MPWS = False
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
