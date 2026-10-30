from django.apps import AppConfig


class FoundersConfig(AppConfig):
    name = 'founders'

    def ready(self):
        from django.db.models.signals import post_delete, post_save
        from system.access_signals import changed
        from founders.models import FounderPass
        for signal in (post_save, post_delete):
            signal.connect(changed, sender=FounderPass,
                           dispatch_uid=f'founders-access-{signal}')
