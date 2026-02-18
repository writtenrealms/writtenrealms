import logging

from django.db import transaction
from django.utils import timezone

from celery import shared_task

from config import constants as api_consts, constants
from fastapi_app.forge_ws import complete_job
from spawns.models import Player
from users.models import User
from worlds.models import World
from worlds.services import WorldSmith


logger = logging.getLogger('lifecycle')


@shared_task
def start_world(world_id, user_id=None, client_id=None):
    """
    Start a world so that players are able to enter it.

    Args:
        world_id (int): The ID of the world to start.
        user_id (int): The ID of the user who started the world.
    """
    world = World.objects.get(pk=world_id)
    print("Starting world %s [ %s ]..." % (world.name, world.id))

    try:
        user = None
        if user_id:
            user = User.objects.filter(id=user_id).first()
        staff_request = user.is_staff if user else False
        WorldSmith(world).start(staff_request=staff_request)
    except Exception as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="start_world",
                data={'error': str(e)},
                status='error')
        raise e

    # Notify the user who requested the job
    #ForgeConsumer.notify_user(user_id=user_id, message="World started.")
    if client_id:
        complete_job(
                    client_id=client_id,
                    job="start_world",)


@shared_task
def request_stop(world_id, client_id=None):
    world = World.objects.get(pk=world_id)

    try:
        WorldSmith(world).request_stop(client_id=client_id)
    except Exception as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="stop_world",
                data={'error': str(e)},
                status='error')
        raise e


@shared_task
def stop_world(world_id, client_id=None):
    world = World.objects.get(pk=world_id)

    try:
        WorldSmith(world).stop()
    except Exception as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="stop_world",
                data={'error': str(e)},
                status='error')
        raise e

    # Notify the user who requested the job that it is complete
    if client_id:
        complete_job(
            client_id=client_id,
            job="stop_world",)

@shared_task
def kill_world(world_id, client_id=None):
    world = World.objects.get(pk=world_id)

    try:
        WorldSmith(world).kill()
    except Exception as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="kill_world",
                data={'error': str(e)},
                status='error')
        raise e

    # Notify the user who requested the job
    if client_id:
        complete_job(
                    client_id=client_id,
                    job="kill_world",)


@shared_task
def monitor_worlds():
    # Go through each world marked as running in the Forge and verify
    # that they still are, and that everything is in order in the game
    # data.
    running_worlds = World.objects.filter(
        context__isnull=False,
        lifecycle=constants.WORLD_LIFECYCLE_RUNNING,
        lifecycle_change_ts__isnull=False,)

    for spawn_world in running_worlds:
        logger.info("Examining world %s" % spawn_world.key)

        # The last checks have to do with idling worlds (MPWs with no
        # connected players). We exclude tier 3 MPWs, which always run.
        if (spawn_world.is_multiplayer and
           spawn_world.context.tier == 3):
            continue

        # If the world has players in it, we don't consider it idle.
        if Player.objects.filter(world=spawn_world, in_game=True).exists():
            continue

        # If the world still has player in instasnces, we don't consider it
        # idle since they could come back out anytime.
        if Player.objects.filter(
            world__context__instance_of=spawn_world.context).exists():
            continue

        # 5 minutes, might be worth making this configurable
        MAX_WORLD_IDLE = 5 * 60
        last_played_on_ts = spawn_world.last_played_ts
        if last_played_on_ts:
            delta = (timezone.now() - last_played_on_ts).total_seconds()
        else: # Set it to a high mark
            delta = MAX_WORLD_IDLE + 100
        if delta > MAX_WORLD_IDLE:
            logger.info("World is idle for %s seconds, stopping..." % delta)
            # Start the stopping process
            WorldSmith(spawn_world).stop()
            continue

    # Look for stuck worlds
    wip_worlds = World.objects.filter(
        context__isnull=False,
        lifecycle_change_ts__isnull=False
    ).exclude(lifecycle__in=[
        constants.WORLD_LIFECYCLE_RUNNING,
        constants.WORLD_LIFECYCLE_NEW,
        constants.WORLD_LIFECYCLE_STOPPED])
    for world in wip_worlds:
        # Calculate how long it's been since the world has been in this state
        delta = (timezone.now() - world.lifecycle_change_ts).total_seconds()
        # If it's been stuck for more than 5 minutes, kill it
        if delta > 300:
            logger.info(
                "World %s has been stuck in %s for %s seconds, killing..." % (
                world.key, world.lifecycle, delta))
            WorldSmith(world).kill()

    # Look for instances that have been stored for more than 5 minutes
    five_min_ago = timezone.now() - timezone.timedelta(seconds=300)
    stored_instances = World.objects.filter(
        context__isnull=False,
        context__instance_of__isnull=False,
        lifecycle=constants.WORLD_LIFECYCLE_STOPPED,
        lifecycle_change_ts__lt=five_min_ago)
    for instance in stored_instances:
        logger.info("Deleting idle instance %s..." % instance.id)
        for player in instance.players.all():
            World.leave_instance(player)
        # Redundant but we want to be absolutely sure we don't delete
        # player data.
        if instance.players.count() == 0:
            instance.delete()
