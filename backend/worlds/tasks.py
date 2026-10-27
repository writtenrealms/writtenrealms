import logging

from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Q, Subquery
from django.utils import timezone
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from celery import shared_task

from config import constants as api_consts, constants
from config import game_settings as adv_config
from core.forge_permissions import reject_job, require_world_admin
from fastapi_app.forge_ws import complete_job, exit_world as notify_exit_world
from spawns.models import Player
from spawns.loading import run_spawn_plans_for_world
from spawns.instance_clock import is_time_controlled, live_worlds
from spawns.services import WorldGate
from worlds.models import InstanceParticipant, InstanceRun, World
from worlds.services import WorldSmith


logger = logging.getLogger('lifecycle')
SPAWN_PLANS_TASK_LOCK_KEY = 'run_world_spawn_plans_lock'
SINGLE_PLAYER_INSTANCE_OFFLINE_GRACE_SECONDS = 3 * 60 * 60


def _spawn_plan_interval_seconds() -> float:
    raw_interval = getattr(adv_config, 'GAME_SPAWN_PLAN_INTERVAL_SECONDS', 15)
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        return 15.0
    return max(interval, 1.0)


def _spawn_plan_lock_timeout_seconds() -> int:
    return max(int(_spawn_plan_interval_seconds() * 4), 30)


def _player_idle_timeout_seconds(player) -> int:
    if player.is_builder:
        return api_consts.BUILDER_IDLE_TIMEOUT
    return api_consts.IDLE_TIMEOUT


def _should_idle_logout_player(player, spawn_world) -> bool:
    if not spawn_world.is_multiplayer:
        return True
    if not player.config:
        return True
    return bool(player.config.idle_logout)


def _disconnect_idle_player(player, spawn_world):
    idle_seconds = int((timezone.now() - player.last_action_ts).total_seconds()) if player.last_action_ts else None
    WorldGate(player=player, world=spawn_world).exit(
        reason='idle_timeout', idle_seconds=idle_seconds,
    )
    notify_exit_world(
        player_id=player.id,
        world_id=spawn_world.id,
        exit_to=spawn_world.context.instance_of_id or spawn_world.context_id,
    )


def _disconnect_idle_players(spawn_world) -> int:
    # Choosing when to advance may deliberately take arbitrarily long. An
    # online owner in a controlled run is not an abandoned instance.
    if is_time_controlled(spawn_world):
        return 0
    now = timezone.now()
    disconnected_players = 0
    players_in_world = Player.objects.filter(
        world=spawn_world,
        in_game=True,
    ).select_related('config')

    for player in players_in_world.iterator(chunk_size=100):
        if not _should_idle_logout_player(player, spawn_world):
            continue

        idle_timeout = _player_idle_timeout_seconds(player)
        if player.last_action_ts:
            delta = (now - player.last_action_ts).total_seconds()
        else:
            delta = idle_timeout + 1

        if delta <= idle_timeout:
            continue

        logger.info(
            "Player %s [ %s ] in world %s is idle for %s seconds, disconnecting...",
            player.name,
            player.id,
            spawn_world.key,
            delta,
        )
        _disconnect_idle_player(player, spawn_world)
        disconnected_players += 1

    return disconnected_players


def _instance_idle_reference_ts(spawn_world):
    if not getattr(spawn_world.context, 'instance_of_id', None):
        return None

    # Death exits already record this timestamp on the participant. Read the
    # latest exit through the (run, exited_at) index instead of adding a shared
    # run-row write/lock to combat deaths. Keep this in the existing run query.
    latest_exit = InstanceParticipant.objects.filter(
        run_id=OuterRef('pk'), exited_at__isnull=False,
    ).order_by('-exited_at').values('exited_at')[:1]
    run = InstanceRun.objects.filter(spawned_world=spawn_world).annotate(
        latest_exit_at=Subquery(latest_exit),
    ).first()
    if not run:
        return spawn_world.lifecycle_change_ts
    activity_at = run.last_active_at or run.started_at or spawn_world.lifecycle_change_ts
    if run.latest_exit_at and (activity_at is None or run.latest_exit_at > activity_at):
        return run.latest_exit_at
    return activity_at


def _world_idle_seconds(spawn_world, *, now):
    idle_reference_ts = _instance_idle_reference_ts(spawn_world)
    if idle_reference_ts is None:
        # A newly started/restarted base world gets its own grace period too.
        idle_reference_ts = max(ts for ts in (
            spawn_world.last_played_ts, spawn_world.lifecycle_change_ts,
        ) if ts is not None)

    # Logout already records the exact disconnect time on the player, in the
    # same transaction that clears in_game. Read it after checking occupancy so
    # even an idle logout in this monitor pass gets the complete grace period.
    # Include child runtimes for a base world: their online players keep it
    # alive, so their last disconnect must start its timer as well.
    scope = Q(pk=spawn_world.pk)
    if not spawn_world.context.instance_of_id:
        scope |= Q(context__instance_of_id=spawn_world.context_id)
    latest_disconnect = Player.objects.filter(
        world_id=OuterRef('pk'), last_disconnection_ts__isnull=False,
    ).order_by('-last_disconnection_ts').values('last_disconnection_ts')[:1]
    disconnected_at = World.objects.filter(scope).annotate(
        latest_disconnect=Subquery(latest_disconnect),
    ).aggregate(latest=Max('latest_disconnect'))['latest']
    # One indexed top-one lookup per runtime, without scanning its players or
    # adding shared World/InstanceRun writes to logout and combat lock paths.
    if disconnected_at and disconnected_at > idle_reference_ts:
        idle_reference_ts = disconnected_at
    return (now - idle_reference_ts).total_seconds()


@shared_task
def start_world(world_id, user_id=None, client_id=None):
    """
    Start a world so that players are able to enter it.

    Args:
        world_id (int): The ID of the world to start.
        user_id (int): The ID of the user who started the world.
    """
    try:
        world, user = require_world_admin(world_id, user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'start_world', exc)
        return
    print("Starting world %s [ %s ]..." % (world.name, world.id))

    try:
        WorldSmith(world).start(staff_request=user.is_staff)
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
def request_stop(world_id, client_id=None, user_id=None):
    try:
        world, user = require_world_admin(world_id, user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'stop_world', exc)
        return

    try:
        WorldSmith(world).request_stop(client_id=client_id, user_id=user.id)
    except Exception as e:
        if client_id:
            complete_job(
                client_id=client_id,
                job="stop_world",
                data={'error': str(e)},
                status='error')
        raise e


@shared_task
def stop_world(world_id, client_id=None, user_id=None):
    try:
        world, _ = require_world_admin(world_id, user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'stop_world', exc)
        return

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
def kill_world(world_id, client_id=None, user_id=None):
    try:
        world, _ = require_world_admin(world_id, user_id)
    except PermissionDenied as exc:
        reject_job(client_id, 'kill_world', exc)
        return

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
    # Admission is snapshotted on the run. Retain it only while its offline
    # owner is still inside, counting wall time from the actual disconnect.
    # Filter in SQL so retained runs add no per-world queries to the monitor.
    offline_owner_runs = InstanceRun.objects.filter(
        single_player=True,
        owner__world_id=F('spawned_world_id'),
        owner__in_game=False,
        owner__last_disconnection_ts__gt=timezone.now() - timezone.timedelta(
            seconds=SINGLE_PLAYER_INSTANCE_OFFLINE_GRACE_SECONDS,
        ),
    )
    has_offline_owner_grace = Exists(offline_owner_runs.filter(
        spawned_world_id=OuterRef('pk'),
    ))

    # Go through each world marked as running in the Forge and verify
    # that they still are, and that everything is in order in the game
    # data.
    running_worlds = World.objects.filter(
        context__isnull=False,
        lifecycle=constants.WORLD_LIFECYCLE_RUNNING,
        lifecycle_change_ts__isnull=False,
    ).exclude(has_offline_owner_grace).select_related('context', 'config')

    running_worlds = running_worlds.exclude(Exists(InstanceRun.objects.filter(
        spawned_world_id=OuterRef('pk'), time_control=True, time_paused=True,
        owner__in_game=True, owner__world_id=OuterRef('pk'),
    )))
    for spawn_world in running_worlds:
        logger.info("#### Examining world %s" % spawn_world.key)

        disconnected_players = _disconnect_idle_players(spawn_world)
        if disconnected_players:
            spawn_world.refresh_from_db()

        if spawn_world.lifecycle != constants.WORLD_LIFECYCLE_RUNNING:
            continue

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
            world__context__instance_of=spawn_world.context,
            in_game=True,
        ).exists():
            continue

        delta = _world_idle_seconds(spawn_world, now=timezone.now())
        if delta > spawn_world.config.idle_timeout_seconds:
            # An idle logout or concurrent disconnect after the initial query
            # starts the same grace period. Recheck only cleanup candidates.
            if (spawn_world.context.instance_of_id
                    and offline_owner_runs.filter(spawned_world=spawn_world).exists()):
                continue
            logger.info("World is idle for %s seconds, stopping..." % delta)
            # Start the stopping process
            WorldSmith(spawn_world).stop(reason='idle_expired')
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
        lifecycle_change_ts__lt=five_min_ago,
    ).exclude(has_offline_owner_grace)
    for instance in stored_instances:
        logger.info("Deleting idle instance %s..." % instance.id)
        from system.support_events import record_support_event
        record_support_event('instance.cleanup', world=instance, reason='idle_expired')
        for player in instance.players.all():
            World.leave_instance(
                player,
                force_active_duel=True,
                reason='idle_expired',
            )
        # Redundant but we want to be absolutely sure we don't delete
        # player data.
        if instance.players.count() == 0:
            instance.delete()


@shared_task(ignore_result=True)
def run_world_spawn_plans():
    lock_timeout = _spawn_plan_lock_timeout_seconds()
    if not cache.add(SPAWN_PLANS_TASK_LOCK_KEY, 1, timeout=lock_timeout):
        return {'skipped': True}

    try:
        spawn_worlds = live_worlds(World.objects.filter(
            context__isnull=False,
            lifecycle=constants.WORLD_LIFECYCLE_RUNNING,
        ).select_related('config'), world_field='id')

        processed = 0
        for spawn_world in spawn_worlds.iterator(chunk_size=100):
            if spawn_world.config and spawn_world.config.never_reload:
                continue

            try:
                run_spawn_plans_for_world(world=spawn_world, process_doors=False)
                processed += 1
            except Exception:
                logger.exception(
                    "Error running spawn plans for world %s (%s)",
                    spawn_world.key,
                    spawn_world.id,
                )

        return {'processed': processed}
    finally:
        cache.delete(SPAWN_PLANS_TASK_LOCK_KEY)


@shared_task(ignore_result=True)
def run_world_door_upkeep():
    """Fair, bounded wakeups, separate from population and item cleanup."""
    from datetime import timedelta
    from config import game_settings
    if not game_settings.DOOR_UPKEEP_ENABLED:
        return 0
    now = timezone.now()
    period = _spawn_plan_interval_seconds()
    with transaction.atomic():
        worlds = list(live_worlds(World.objects.filter(context__isnull=False,
            lifecycle=constants.WORLD_LIFECYCLE_RUNNING, config__never_reload=False)
            .filter(Q(door_upkeep_next_poll_ts__isnull=True) | Q(door_upkeep_next_poll_ts__lte=now)),
            world_field='id').order_by(F('door_upkeep_next_poll_ts').asc(nulls_first=True), 'id')
            .select_for_update(of=('self',), skip_locked=True)[:game_settings.DOOR_UPKEEP_WORLD_BATCH])
        for world in worlds:
            world.door_upkeep_next_poll_ts = now + timedelta(seconds=period)
        World.objects.bulk_update(worlds, ['door_upkeep_next_poll_ts'])
    for world in worlds:
        try:
            run_runtime_door_upkeep.apply_async(args=[world.pk], expires=period)
        except Exception:
            logger.exception('Could not enqueue door upkeep for runtime %s', world.pk)
    return len(worlds)


@shared_task(ignore_result=True)
def run_runtime_door_upkeep(world_id):
    from config import game_settings
    from spawns.door_upkeep import process_due_door_resets
    if not game_settings.DOOR_UPKEEP_ENABLED:
        return
    world = live_worlds(World.objects.filter(pk=world_id, context__isnull=False,
        lifecycle=constants.WORLD_LIFECYCLE_RUNNING).select_related('config', 'context'), world_field='id').first()
    if world:
        try:
            return process_due_door_resets(world=world, budget=game_settings.DOOR_UPKEEP_ZONE_BUDGET)
        except Exception:
            logger.exception('Door upkeep failed for runtime %s', world.pk)
            raise
