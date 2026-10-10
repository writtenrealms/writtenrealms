from django.apps import AppConfig


class SystemConfig(AppConfig):
    name = 'system'

    def ready(self):
        from system import access_signals  # noqa: F401
