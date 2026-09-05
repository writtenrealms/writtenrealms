from django.apps import AppConfig


class SpawnsConfig(AppConfig):
    name = 'spawns'

    def ready(self):
        from spawns import combat_signals  # noqa: F401
