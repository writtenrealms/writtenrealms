from datetime import timedelta
import logging

from celery import shared_task
from django.utils import timezone

from config import constants as api_consts
from fastapi_app.forge_ws import complete_job
from system.models import SiteControl
from system.services import update_staff_panel
from worlds.models import World
from worlds.services import WorldSmith

logger = logging.getLogger('lifecycle')


@shared_task
def toggle_maintenance_mode(client_id=None):
    site_control = SiteControl.objects.get(name='prod')
    site_control.maintenance_mode = not site_control.maintenance_mode
    site_control.save()
    update_staff_panel()

    if client_id:
        complete_job(
            client_id=client_id,
            job="toggle_maintenance_mode",
            data={
                'maintenance_mode': site_control.maintenance_mode
            })


@shared_task
def broadcast(message, client_id=None):
    return

    running_spawn_worlds = World.objects.filter(
        context__isnull=False,
        lifecycle=api_consts.WORLD_STATE_RUNNING)

    for world in running_spawn_worlds:
        print('adding broadcast timing for %s' % world)
        add_timing(
            type='timing.broadcast',
            expires=expiration_ts(0),
            db=world.rdb,
            world=world.key,
            data={'message': message})

    if client_id:
        complete_job(
            client_id=client_id,
            job="broadcast",
            data={
                'message': message,
            })
