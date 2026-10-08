from celery import shared_task
from django.core.exceptions import PermissionDenied

from core.forge_permissions import reject_job, require_staff, require_world_admin
from fastapi_app.forge_ws import complete_job, complete_subscription
from system.models import SiteControl
from system.services import update_staff_panel


@shared_task
def toggle_maintenance_mode(client_id=None, user_id=None):
    try:
        require_staff(user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'toggle_maintenance_mode', exc)
        return

    site_control = SiteControl.objects.get(name='prod')
    site_control.maintenance_mode = not site_control.maintenance_mode
    site_control.save()
    update_staff_panel()

    if client_id:
        complete_job(
            client_id=client_id,
            job='toggle_maintenance_mode',
            data={'maintenance_mode': site_control.maintenance_mode},
        )


@shared_task
def broadcast(message, client_id=None, user_id=None):
    try:
        require_staff(user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'broadcast', exc)
        return

    # Delivery was already disabled. Do not restore the obsolete WR1 timing
    # implementation as part of authorization; report the unavailable feature.
    reject_job(client_id, 'broadcast', 'Broadcasting is currently unavailable.')


@shared_task(ignore_result=True)
def authorize_forge_subscription(client_id, request_id, user_id, sub, world_id=None):
    # FastAPI owns the connection but cannot query Django models. Return a
    # correlated approval over Redis; only the requesting live connection may
    # consume it. No client joins a protected group until this check succeeds.
    error = None
    try:
        if sub == 'staff.panel':
            require_staff(user_id)
        elif sub == 'builder.admin':
            require_world_admin(world_id, user_id)
        else:
            raise PermissionDenied('Unknown subscription.')
    except PermissionDenied as exc:
        error = str(exc)
    complete_subscription(client_id, request_id, error=error)
