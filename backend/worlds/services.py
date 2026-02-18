import logging

from rest_framework import serializers

from config import constants as api_consts
from config.exceptions import ServiceError
from system.models import SiteControl
from worlds.models import World, WorldLocks


logger = logging.getLogger('lifecycle')


class WorldSmith:

    def __init__(self, world):
        if not world.context:
            raise ServiceError("Can only act on spawn worlds.")
        self.world = world

    def start_preflight(self, staff_request=False):
        world = self.world

        if not staff_request:
            if (world.no_start or world.context.no_start):
                raise RuntimeError("World is disabled.")

            # Site control mechanism to prevent all worlds from starting
            try:
                site_control = SiteControl.objects.get(name='prod')
                if site_control.maintenance_mode:
                    raise ServiceError(
                        "Unable to build world: Written Realms is undergoing "
                        "maintenance. Please try again later.")
            except SiteControl.DoesNotExist:
                pass

        # don't start a world being actively cleaned up
        if WorldLocks.check_ongoing_cleanup(world):
            raise ServiceError("World is being cleaned up. Please wait.")

        if self.world.lifecycle not in [
            api_consts.WORLD_LIFECYCLE_NEW,
            api_consts.WORLD_LIFECYCLE_STOPPED]:
            raise ServiceError(
                "World cannot be started in '%s' state."
                % self.world.lifecycle)

        return True

    def start(self, staff_request=False):
        self.start_preflight(staff_request=staff_request)
        self.world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STARTING)
        # Placeholder for startup work
        self.world.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)
        # Update the admin page
        self.world.update_builder_admin()
        return self.world

    def request_stop(self, client_id=None):
        """
        Request to stop a MPW, which will give players a 30 seconds warning
        before actually doing anything.
        """
        spawn_world = self.world

        if spawn_world.lifecycle != api_consts.WORLD_STATE_RUNNING:
            raise serializers.ValidationError("Can only stop running worlds.")

        self.world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPING)

        from worlds.tasks import stop_world
        stop_world.apply_async(
            args=[spawn_world.id, client_id],
            countdown=60)

    def stop(self):
        # if self.world.lifecycle != api_consts.WORLD_LIFECYCLE_STOPPING:
        #     raise serializers.ValidationError("Can only stop stopping worlds.")

        # Placeholder for stop work
        self.world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPED)

        # Actually delete instances on stop
        if self.world.context.instance_of:
            self.world.delete()

    def stop_spw(self, player=None, player_data_id=None):
        spawn_world = self.world
        spawn_world.set_state(api_consts.WORLD_STATE_STOPPING)

        game_world = spawn_world.game_world

        if not player:
            player = spawn_world.players.first()


        if player:
            player.in_game = False
            player.save(update_fields=['in_game'])

        spawn_world.set_state(api_consts.WORLD_STATE_STOPPED)

        # Clean up the world
        spawn_world.cleanup(spw=True)
        spawn_world.set_state(api_consts.WORLD_STATE_STORED)

    def stop_mpw(self):
        """
        Actually stop the MPW after the 30 seconds warning notice has elapsed.
        """
        spawn_world = self.world
        spawn_world.set_state(api_consts.WORLD_STATE_STOPPING)
        spawn_world.set_state(api_consts.WORLD_STATE_STOPPED)

        # Stop any instances from this world
        if spawn_world.is_multiplayer:
            instances = World.objects.filter(
                context__instance_of=spawn_world.context)
            for instance in instances:
                print('--- stopping %s' % instance)
                WorldSmith(instance).stop_mpw()

        # Clean up the world
        spawn_world.cleanup()
        spawn_world.set_state(api_consts.WORLD_STATE_STORED)

    def kill(self):
        logger.info("Killing %s..." % self.world.name)
        spawn_world = self.world
        spawn_world.set_state(api_consts.WORLD_STATE_KILLING)

        # Kill any instances from this world
        if spawn_world.is_multiplayer:
            instances = World.objects.filter(
                context__instance_of=spawn_world.context)
            for instance in instances:
                print('--- killing %s' % instance)
                WorldSmith(instance).kill()

        spawn_world.set_state(api_consts.WORLD_STATE_KILLED)

        if spawn_world.is_multiplayer:
            # Clean up the world
            spawn_world.cleanup()
        else:
            spawn_world.cleanup(spw=True)

        if self.world.nexus:
            self.world.nexus.mark_activity()

        spawn_world.players.update(in_game=False)
        spawn_world.set_state(api_consts.WORLD_STATE_STORED)

        # Actually delete instances on kill
        if spawn_world.context.instance_of:
            spawn_world.delete()

        logger.info("Done.")
