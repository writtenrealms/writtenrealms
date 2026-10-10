from django.db import transaction
from django.db.models.signals import post_delete, post_save

from system.access import clear_access_cache
from system.models import AccessAllowlistEntry, SiteControl


def changed(sender, **kwargs):
    clear_access_cache()
    # Discard any value another thread read before the transaction committed.
    transaction.on_commit(clear_access_cache)


for model in (AccessAllowlistEntry, SiteControl):
    post_save.connect(changed, sender=model, dispatch_uid=f'access-save-{model._meta.label}')
    post_delete.connect(changed, sender=model, dispatch_uid=f'access-delete-{model._meta.label}')
