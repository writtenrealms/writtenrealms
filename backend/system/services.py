from django.utils import timezone

from config import constants as api_consts
from builders.models import WorldReview
from spawns.models import Player
from system.models import Nexus, SiteControl
from users.models import User
from worlds.models import World

def get_staff_panel():
    """
    Get the staff panel data.
    """

    site_control = SiteControl.objects.get(name='prod')
    panel_data = {
        'maintenance_mode': site_control.maintenance_mode
    }

    # Unreviewed publication submissions
    panel_data['unreviewed'] = WorldReview.objects.filter(
        status=api_consts.WORLD_REVIEW_STATUS_SUBMITTED
    ).count()

    # User signups in the last 24 hours
    one_day_ago = timezone.now() - timezone.timedelta(days=1)
    users_qs = User.objects.filter(
        date_joined__gte=one_day_ago,
        is_temporary=False,
    )
    panel_data['user_signups'] = {
        'total': users_qs.count(),
        'confirmed': users_qs.filter(is_confirmed=True).count(),
    }

    # Unique users entering worlds over the last 24 hours
    panel_data['user_connections'] = Player.objects.filter(
        last_connection_ts__gte=one_day_ago,
        user__is_temporary=False,
    ).values('user').distinct().count()

    # Nexues
    panel_data['nexuses'] = []
    for nexus in Nexus.objects.all():
        panel_data['nexuses'].append({
            'id': nexus.id,
            'name': nexus.name,
            'state': nexus.state,
            'worlds': [
                {
                    'id': world.id,
                    'key': world.key,
                    'name': world.name,
                    'state': world.lifecycle,
                    'context_id': world.context_id,
                    'change_state_ts': world.change_state_ts.strftime('%Y-%m-%d %H:%M:%S') if world.change_state_ts else None,
                } for world in nexus.worlds.exclude(lifecycle=api_consts.WORLD_STATE_STORED).order_by('-change_state_ts')
            ]
        })

    def serialize_world(world):
        nexus = world.nexus
        delta = timezone.now() - world.change_state_ts
        time_since_last_change = int((delta).total_seconds())

        return {
            'id': world.id,
            'key': world.key,
            'name': world.name,
            'state': world.lifecycle,
            'context_id': world.context_id,
            #'nexus_data': k8s.get_nexus_data(world.nexus_name),
            'nexus_data': {
                'name': nexus.name,
                'state': nexus.state,
            } if nexus else None,
            'change_state_ts': world.change_state_ts.strftime('%Y-%m-%d %H:%M:%S'),
            'time_since_last_change': time_since_last_change,
            'playing_count': world.players.filter(in_game=True).count(),
        }

    # running worlds
    running_worlds = World.objects.filter(
        context__isnull=False,
        lifecycle=api_consts.WORLD_STATE_RUNNING,
        change_state_ts__isnull=False,
    )
    panel_data['running_worlds_count'] = running_worlds.count()
    panel_data['running_worlds'] = [
        serialize_world(world)
        for world in running_worlds.order_by('-change_state_ts')[0:10]
    ]

    # wip worlds (not running, new or stored)
    wip_worlds = World.objects.filter(
        context__isnull=False,
        change_state_ts__isnull=False,
    ).exclude(
        lifecycle__in=[
            api_consts.WORLD_STATE_RUNNING,
            api_consts.WORLD_STATE_NEW,
            api_consts.WORLD_STATE_STORED])
    panel_data['wip_worlds_count'] = wip_worlds.count()
    panel_data['wip_worlds'] = [
        serialize_world(world)
        for world in wip_worlds.order_by('-change_state_ts')[0:5]
    ]

    return panel_data

def update_staff_panel():
    from fastapi_app.forge_ws import publish
    publish(
        pub='staff.panel',
        data=get_staff_panel())
