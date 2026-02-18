# backend/config/asgi.py
"""
ASGI config for the Django backend.

This serves the Django REST API over HTTP.
WebSocket connections are handled by the FastAPI service (fastapi_app).
"""
import os

from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.config.settings.container')

application = get_asgi_application()

if settings.DEBUG:
    application = ASGIStaticFilesHandler(application)
