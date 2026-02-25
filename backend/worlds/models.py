from datetime import datetime
import json
import logging
import traceback
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from config import constants as adv_consts
from core.utils import CamelCase__to__camel_case

from config import constants as api_consts
from config import game_settings as adv_config
from core.db import (
    BaseModel,
    AdventBaseModel,
    AdventWorldBaseModel,
    optional,
    list_to_choice,
    batch_deletion)
from worlds.managers import (
    WorldManager,
    RoomManager)


lifecycle_logger = logging.getLogger('lifecycle')


class World(AdventBaseModel):

    objects = WorldManager()

    name = models.TextField()
    short_description = models.TextField(blank=True)
    description = models.TextField(blank=True)
    motd = models.TextField(**optional) # message of the day

    lifecycle = models.TextField(choices=list_to_choice(
                                        api_consts.WORLD_LIFECYCLES),
                                 default=api_consts.WORLD_LIFECYCLE_NEW,
                                 db_index=True)
    lifecycle_change_ts = models.DateTimeField(db_index=True, **optional)

    change_state_ts = models.DateTimeField(db_index=True, **optional)
    is_clean = models.BooleanField(default=False)
    clean_start_ts = models.DateTimeField(**optional)

    last_played_ts = models.DateTimeField(**optional)

    # Whether the world has been deployed on Kubernetes
    # is_k8s_deployed = models.BooleanField(default=False)

    is_multiplayer = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)

    # Builder maintenance mode
    maintenance_mode = models.BooleanField(default=False)
    maintenance_msg = models.TextField(**optional)

    tier = models.IntegerField(default=1)

    # For multiplayer only
    auto_start = models.BooleanField(default=False)

    # If a world has been deemed problematic, this will prevent it from
    # being spun up again.
    no_start = models.BooleanField(default=False)

    last_loader_run_ts = models.DateTimeField(**optional)
    last_extraction_ts = models.DateTimeField(**optional)
    last_entered_ts = models.DateTimeField(**optional)

    full_map = models.TextField(**optional)

    facts = models.TextField(**optional)

    # References

    author = models.ForeignKey(settings.AUTH_USER_MODEL,
                               on_delete=models.SET_NULL,
                               related_name='worlds',
                               **optional)

    # Root worlds have no context. Spawn worlds refer to their template world
    # as the context.
    context = models.ForeignKey('worlds.World',
                                on_delete=models.CASCADE,
                                related_name='spawned_worlds',
                                **optional)

    # Instance world
    instance_of = models.ForeignKey('worlds.World',
                                    on_delete=models.CASCADE,
                                    related_name='instances',
                                    **optional)
    # Instance ref
    instance_ref = models.TextField(db_index=True, **optional)
    # Instance leader
    leader = models.ForeignKey('spawns.Player',
                               on_delete=models.SET_NULL,
                               related_name='leader_for',
                               **optional)

    # This is really required, but since the config depends on certain
    # world elements like rooms, there has to exist a time where a world
    # exists without a config as it's being created (at least until it has
    # a room).
    config = models.ForeignKey('worlds.WorldConfig',
                               related_name='configured_worlds',
                               on_delete=models.SET_NULL,
                               **optional)

    nexus = models.ForeignKey('system.Nexus',
                              related_name='worlds',
                              on_delete=models.SET_NULL,
                              **optional)

    save_start_ts = models.DateTimeField(**optional)

    # M2M

    # Could this be in WorldConfig perhaps?
    builders = models.ManyToManyField(settings.AUTH_USER_MODEL,
                                      related_name='builder_for',
                                      through='builders.WorldBuilder')

    class Meta:
        ordering = ('-created_ts',)

    def __str__(self):
        return "%s - %s" % (self.id, self.name)

    @property
    def review_status(self):
        latest_review = self.world_reviews.order_by('-created_ts').first()
        if latest_review:
            return latest_review.status
        return api_consts.WORLD_REVIEW_STATUS_UNSUBMITTED

    @property
    def config_source_world(self):
        return self.context or self

    @property
    def effective_config(self):
        source_world = self.config_source_world
        return source_world.config if source_world else None

    # ==== Utility functions that change state ====

    def set_state(self, state, rdb=None):
        "Function that should be invoked whenever there's a lifecycle transition."
        if not self.context:
            raise RuntimeError("Root worlds are stateless.")

        with transaction.atomic():
            world = World.objects.select_for_update().get(pk=self.pk)
            world.lifecycle = state
            world.change_state_ts = timezone.now()
            world.save()

        # Refresh the current instance from the database
        self.refresh_from_db()

        rdb = rdb or self.rdb
        world.update_builder_admin(rdb=rdb)
        return world

    def set_lifecycle(self, lifecycle):
        "Function that should be invoked whenever there's a lifecycle transition."
        if not self.context:
            raise RuntimeError("Root worlds have no lifecycle.")

        with transaction.atomic():
            world = World.objects.select_for_update().get(pk=self.pk)
            world.lifecycle = lifecycle
            world.lifecycle_change_ts = timezone.now()
            world.save()

        # Refresh the current instance from the database
        self.refresh_from_db()

        #world.update_builder_admin()
        return world

    def save_data(self, game_world=None):

        if not self.context:
            raise RuntimeError("Can only save spawn worlds.")

        try:
            with transaction.atomic():
                world = World.objects.select_for_update().get(pk=self.pk)
                if world.save_start_ts:
                    return
                world.save_start_ts = timezone.now()
                world.save(update_fields=['save_start_ts'])

            # Facts
            facts = game_world.facts or {}
            fact_schedules = self.context.fact_schedules.filter(
                Q(next_run_ts__isnull=True)
                | (Q(next_run_ts__isnull=False) &
                Q(next_run_ts__lt=timezone.now())))
            updated_facts = []
            for fact_schedule in fact_schedules:
                updated_facts.append(fact_schedule.run(facts))
                try:
                    fact_schedule.set_next_run()
                except:
                    print("Error updating fact schedule for %s:" % self.id)
                    traceback.print_exc()
            for fact_change in updated_facts:
                facts[fact_change['fact']] = fact_change['new_value']
                if (fact_change['msg']
                    and fact_change['old_value'] != fact_change['new_value']):
                    # add_timing(
                    #     world=self.key,
                    #     type='timing.game_write',
                    #     data={'text': fact_change['msg']},
                    #     db=self.rdb)
                    pass
            game_world.facts = facts

        finally:
            with transaction.atomic():
                world = World.objects.select_for_update().get(pk=self.pk)
                world.save_start_ts = None
                world.save(update_fields=['save_start_ts'])

    def track_event(self, type, start):
        # TrackedEvent.objects.create(
        #     type=type,
        #     world=self,
        #     speed=time.time() - start)
        pass

    def cleanup(self, spw=False):
        """
        Rid a world of all of its mobs, and all of its items on the ground.
        This is meant to be done before an initial loader run in a multiplayer
        world.
        """

        if not self.context: raise TypeError("Cannot clean root world")

        if self.lifecycle in ([api_consts.WORLD_STATE_RUNNING,
                               api_consts.WORLD_STATE_STARTING,
                               api_consts.WORLD_STATE_STOPPING]):
            raise ValueError(
                "World cannot be cleaned up in state '%s'."
                % self.lifecycle)

        lifecycle_logger.info("Starting full Cleanup for %s (%s)" % (self.name, self.id))

        # We don't invoke self.start_cleanup because this cleanup happens as
        # we're shutting down and the deletions are transactional, so even
        # if there's another cleanup going at the same time there's no harm
        # in going through this code.

        # Remove all mobs
        lifecycle_logger.debug("Deleting mobs...")
        if spw:
            batch_deletion(self.mobs.filter(is_pending_deletion=True))
        else:
            batch_deletion(self.mobs.all())

        items_qs = self.items.all()

        # Exlcude persistent items
        items_qs = items_qs.exclude(
            is_persistent=True,
            container_type__model='room')

        if not spw:
            # Remove all items in rooms
            lifecycle_logger.debug("Deleting items in rooms...")
            batch_deletion(items_qs.filter(container_type__model='room'))

        # Remove all pending deletion items older than 1 month
        lifecycle_logger.debug("Deleting items pending deletion...")
        #batch_deletion(items_qs.filter(is_pending_deletion=True))
        one_week_ago = timezone.now() - timezone.timedelta(days=7)
        batch_deletion(items_qs.filter(
            is_pending_deletion=True,
            pending_deletion_ts__lt=one_week_ago))

        # Remove all items that don't have a container
        lifecycle_logger.debug("Deleting items that don't have a container...")
        batch_deletion(items_qs.filter(container_id__isnull=True))

        # Remove all player extraction data entries older than 1 week old
        from spawns.models import PlayerData
        lifecycle_logger.debug("Deleting player extraction data...")
        PlayerData.objects.filter(
            player__world=self,
            created_ts__lt=timezone.now() - timezone.timedelta(days=7)).delete()

        # If instance, move all players back to the base world
        if self.context.instance_of:
            for player in self.players.all():
                self.leave_instance(player=player)

        self.players.update(in_game=False)

        # Delete all pending deletion players
        self.players.filter(pending_deletion_ts__isnull=False).delete()

        self.is_clean = True
        self.save(update_fields=['is_clean'])

        WorldLocks.end_cleanup(self)
        lifecycle_logger.info("Full cleanup complete for %s (%s)" % (self.name, self.id))

    mpw_cleanup = cleanup

    def live_cleanup(self):
        """
        Clean up objects in a running world that we are confident are gone
        for good.
        """
        if not self.context: raise TypeError("Cannot clean root world")

        if self.lifecycle != api_consts.WORLD_STATE_RUNNING:
            raise ValueError(
                "World cannot be live cleaned up in state '%s'."
                % self.lifecycle)

        WorldLocks.start_cleanup(self)

        try:
            lifecycle_logger.info("Live cleaning %s (%s)" % (self.name, self.id))

            one_hour_ago = timezone.now() - timezone.timedelta(hours=1)

            # Remove all pending deletion mobs older than 1 hour
            lifecycle_logger.debug("Deleting mobs...")
            batch_deletion(self.mobs.filter(
                is_pending_deletion=True,
                pending_deletion_ts__lt=one_hour_ago))
            #batch_deletion(self.mobs.filter(is_pending_deletion=True))

            """
            # Remove all pending deletion items older than 1 hour
            lifecycle_logger.debug("Deleting items pending deletion...")
            threshold = timezone.now() - timezone.timedelta(hours=1)
            batch_deletion(self.items.filter(
                is_pending_deletion=True,
                pending_deletion_ts__lt=threshold))
            """
        except:
            print("Error in live cleanup:")
            traceback.print_exc()
        finally:
            WorldLocks.end_cleanup(self)
            lifecycle_logger.info("Live cleanup complete for %s (%s)" % (self.name, self.id))

    @property
    def rdb(self):
        return None

    @property
    def game_world(self):
        return None

    # Kubernetes propeties

    @property
    def pod_name(self):
        return f"{self.nexus_name}-pod"

    @property
    def service_name(self):
        import os
        release = os.getenv('HELM_RELEASE', '')
        service = self.nexus_name
        if release:
            service = release + '-'  + service
        return service

    @property
    def ingress_name(self):
        return f"{self.nexus_name}-ingress"

    @property
    def ingress_path(self):
        return f"/websocket/{self.nexus_name}/"

    @property
    def cluster_id(self):
        return self.context.id if self.context else self.id

    @property
    def nexus_name(self):
        root_world = self.context if self.context else self
        root_world = root_world.instance_of or root_world
        if root_world.tier == 3:
            return f"nexus-{root_world.id}"
        if root_world.tier == 2:
            return "nexus-sanctum"
        return "nexus-sandbox"



    def update_builder_admin(self, rdb=None):
        """
        When changes have been made to a root world or any of
        its spawns that would update the builder's admin page,
        we trigger that update here.
        """
        from fastapi_app.forge_ws import publish
        from builders.serializers import WorldAdminSerializer
        rdb = rdb or self.rdb
        root_world = self.context if self.context else self
        world_data = WorldAdminSerializer(
            root_world,
            context={'rdb': rdb}).data
        publish(
            pub='builder.admin',
            data=world_data,
            world_id=root_world.id,)

    def start(self, rdb=None):
        """
        Boot up a multiplayer world and set it ready for playing. This is a
        destructive action and works a lot like a reset.

        We remove all items which are on the ground, and all mobs. Then we
        run the loaders in initial mode.

        For multiplayer worlds, the initial animation should only occur right after initialization, and then it should be partial animations.
        """

        if (self.no_start or self.context.no_start):
            raise RuntimeError("World is disabled.")

        # We only start spawn worlds
        if not self.context:
            raise TypeError("Cannot initialize root world")

        rdb = rdb or self.rdb
        if self.is_multiplayer:
            return self.start_mpw(rdb=rdb)
        else:
            return self.start_spw(rdb=rdb)
    # Backwards compatibility
    initialize = start

    def start_mpw(self, rdb=None):
        if self.lifecycle not in [
            api_consts.WORLD_STATE_STORED,
            api_consts.WORLD_STATE_STOPPED,
            api_consts.WORLD_STATE_NEW,
            api_consts.WORLD_STATE_BUILT,
            api_consts.WORLD_STATE_KILLED,
            api_consts.WORLD_STATE_CLEAN]:
            raise RuntimeError(
                "Cannot start in %s state." % self.lifecycle)

        self.set_state(api_consts.WORLD_STATE_STARTING)

        # Run the loaders
        from spawns.loading import run_loaders
        run_loaders(world=self, initial=True)

        # Mark the world as running
        self.set_state(api_consts.WORLD_STATE_RUNNING)

    def start_spw(self, rdb=None):
        """
        Unlike Multiplayer Worlds that boot up on their own and won't accept
        enter requests unless they're in a valid state, SPWs are prone to
        often receive enter requests while still being storing themselves
        away, for example if a user hits the reload button on their browser
        and quickly clicks join.
        """
        if self.lifecycle not in [api_consts.WORLD_STATE_NEW,
                                  api_consts.WORLD_STATE_STORED,
                                  api_consts.WORLD_STATE_BUILT]:
            raise RuntimeError(
                "World cannot be started in '%s' state." % self.lifecycle)

        self.set_state(api_consts.WORLD_STATE_STARTING)

        from spawns.models import Item, Mob
        # There used to be a bug (possibly still is?) where the contents of
        # mobs in pending deletion state in SPWs did not get marked as
        # pending deletion, meaning they would get injected into the world
        # with a stale container reference.
        # Hopefully no longer a thing, we nevertheless need to clean up
        # instances of this otherwise we get errors trying to extract that
        # SPW data from the game side.
        #
        # Get all mobs that are pending deletion for the world
        mob_ids = Mob.objects.filter(
            is_pending_deletion=True,
            world=self).values_list('id', flat=True)
        if mob_ids:
            # Mark all of the contents of pending deletion mobs as pending
            # deletion.
            stale_items_qs = Item.objects.filter(
                container_type=ContentType.objects.get_for_model(Mob),
                container_id__in=mob_ids)
            if stale_items_qs:
                print("@@@@@ Marking %s items as pending deletion"
                      % stale_items_qs.count())
                stale_items_qs.update(is_pending_deletion=True)

        if not self.last_loader_run_ts:
            from spawns.loading import run_loaders
            run_loaders(world=self, initial=True)

        # Mark the world as running
        self.set_state(api_consts.WORLD_STATE_RUNNING)

    # Utility functions

    def create_spawn_world(self, **kwargs):
        if (self.is_multiplayer and
            not self.instance_of and
            self.spawned_worlds.filter(
                is_multiplayer=True).exists()):
            raise TypeError(
            "Cannot create more than one spawn world for a multiplayer "
            "world.")

        effective_config = self.effective_config
        if not effective_config:
            raise ValueError("Cannot create a spawn world without a world config.")

        spawn_world = World.objects.create(
            name=self.name,
            config=effective_config,
            description=self.description,
            is_multiplayer=self.is_multiplayer,
            context=self,
            is_clean=True,
            **kwargs)
        WorldLocks.objects.create(world=spawn_world)
        return spawn_world

    def instance_for(self, player, transfer_from=None, ref=None, member_ids=None, **kwargs):
        """
        Get or create the appropriate instance of a world for a player.
        """
        if self.context:
            raise TypeError("Cannot create an instance of a spawn world.")
        if not self.instance_of:
            raise TypeError("Cannot create an instance of a base world.")

        # If an instance ref is passed, we join that instance, creating
        # an instance assignment first if need be.
        if ref:
            ref_instance = World.objects.filter(instance_ref=ref).first()
            if not ref_instance:
                raise RuntimeError("Invalid instance reference %s" % ref)

            player_assignment = InstanceAssignment.objects.filter(
                instance=ref_instance,
                player=player).first()
            if not player_assignment:
                player_assignment = InstanceAssignment.objects.create(
                    instance=ref_instance,
                    player=player,
                    transfer_from=transfer_from)

            if member_ids and not player_assignment.member_ids:
                player_assignment.member_ids = ' '.join(member_ids)
                player_assignment.save(update_fields=['member_ids'])

            return ref_instance

        # No instance ref is passed, we either fetch or create an instance
        # with the player as its leader (and create an instance assignment
        # if need be).
        instance = self.spawned_worlds.filter(leader=player).first()
        if instance:
            player_assignment = InstanceAssignment.objects.filter(
                instance=instance,
                player=player).first()
            if not player_assignment:
                player_assignment = InstanceAssignment.objects.create(
                    instance=instance,
                    player=player,
                    transfer_from=transfer_from,
                    member_ids=' '.join(member_ids or []),
                )

            if member_ids and not player_assignment.member_ids:
                player_assignment.member_ids = ' '.join(member_ids)
                player_assignment.save(update_fields=['member_ids'])

            return instance

        instance = self.create_spawn_world(
            instance_ref=uuid.uuid4().hex,
            leader=player,
            **kwargs)
        InstanceAssignment.objects.create(
            player=player,
            instance=instance,
            transfer_from=transfer_from,
            member_ids=' '.join(member_ids or []))
        return instance


    def can_edit(self, user, builder=None):

        from builders.models import WorldBuilder

        if self.author == user:
            return True

        if user.is_staff:
            return True

        if not user.is_authenticated:
            return False

        if not builder:
            builder = WorldBuilder.objects.filter(
                world=self,
                user=user).first()

        # This really answers the question of whether the builder COULD
        # edit. Further permission checks will be performed for specific
        # resources / actions (for example mobs, items).
        if builder and builder.builder_rank >= 1:
            return True
        # if world_builder:
        #     if not world_builder[0].read_only:
        #         return True
        return False

    @classmethod
    def enter_instance(cls, player, transfer_to_id, transfer_from_id, ref=None, member_ids=None):
        transfer_to = Room.objects.get(pk=transfer_to_id)
        transfer_from = Room.objects.get(pk=transfer_from_id)
        instance = transfer_to.world.instance_for(
            player,
            transfer_from=transfer_from,
            ref=ref,
            member_ids=member_ids)
        player.world = instance
        player.room = transfer_to
        player.save()
        return instance

    @classmethod
    def leave_instance(cls, player):
        # Leave instance means that the player is going back to the main
        # world, for example after invoking the 'leave' command.
        base_world_context = player.world.context.instance_of
        if not base_world_context:
            raise ValueError("Player is not in an instance.")

        base_spawn_world = base_world_context.spawned_worlds.filter(
            is_multiplayer=True).get()

        room = None
        try:
            instance_assignment = InstanceAssignment.objects.get(
                player=player,
                instance=player.world)
            room = instance_assignment.transfer_from
        except InstanceAssignment.DoesNotExist:
            pass

        if not room:
            room = base_world_context.config.starting_room

        player.world = base_spawn_world
        player.room = room
        player.save(update_fields=['world', 'room'])

        # If leaving an instance, make sure that all items in the
        # player's inventory or equipment are set to the base world.
        inv_ids = list(player.inventory.values_list('id', flat=True))
        eq_ids = list(player.equipment.inventory.values_list('id', flat=True))
        from spawns.models import Item
        Item.objects.filter(
            Q(id__in=inv_ids) | Q(id__in=eq_ids)
        ).update(world_id=base_spawn_world.id)
        Item.objects.filter(
            container_type=ContentType.objects.get_for_model(Item),
            container_id__in=inv_ids + eq_ids
        ).update(world_id=base_spawn_world.id)

        return player

    def exit_instance(self, player):
        template_world = self.context
        if not template_world:
            raise ValueError("Not a spawned world.")

        root_world = template_world.instance_of
        if not root_world:
            raise ValueError("Not an instance world.")

        if not self.context or not self.context.instance_of:
            raise ValueError("Not a spawned instance.")

        spawned_instance = self

        spawned_root = root_world.spawned_worlds.get(is_multiplayer=True)

        from spawns import instances
        exit_room = (
            player.room.exits_to
            or spawned_instance.config.exits_to)
        instances.prepare_entry(
            player=player,
            spawned_world=spawned_root,
            room=exit_room)

        # See if any players are left on this instance, and if not
        # clean it up.
        if not spawned_instance.players.count():
            game_db = spawned_instance.rdb

            if game_db.exists(spawned_instance.key):
                game_db.fetch(spawned_instance.key).delete()
            spawned_instance.delete()

    @property
    def factions(self):
        factions = {}
        template_world = self.context or self
        for faction in template_world.world_factions.all():
            factions[faction.code] = {
                'code': faction.code,
                'name': faction.name,
            }
        return factions

    # Redis-requiring methods

    def load_player(self, player, rdb=None):
        "Inject a player into a multiplayer world"
        player.last_action_ts = timezone.now()
        player.save(update_fields=['last_action_ts'])

    def animate(self, redis_db=None, animation_data=None):
        from spawns.animation import animate
        redis_db = redis_db or self.rdb
        return animate(self, redis_db=redis_db, animation_data=animation_data)

    def extract_data(self, redis_db=None):
        if not self.context:
            raise TypeError("Can only extra data for spawn worlds.")
        redis_db = redis_db or self.rdb
        game_world = redis_db.fetch(self.key)

        data = game_world.extract_data()
        self.last_extraction_ts = timezone.now()
        self.save(update_fields=['last_extraction_ts'])
        return data

    def get_running_worlds(self, rdb=None):
        return self.spawned_worlds.filter(
            lifecycle=api_consts.WORLD_STATE_RUNNING)

    # Model creators

    def add_builder(self, builder, builder_rank=1, read_only=True):
        from builders.models import WorldBuilder
        world_builder, created = WorldBuilder.objects.get_or_create(
            world=self,
            user=builder,
            builder_rank=builder_rank,
            read_only=read_only)
        return world_builder

    def create_item_template(self, **kwargs):
        from builders.models import ItemTemplate
        kwargs.pop('world', None)
        return ItemTemplate.objects.create(world=self, **kwargs)

    # Optimize world map getter
    def get_map(self, rooms_qs=None):
        from worlds.models import World, RoomFlag
        from core.serializers import ReferenceField

        rooms = {}
        room_refs = {}

        # After this block, rooms looks like
        # { 2340: {'id': 2340,
        #          'key': 'room.1765',
        #          'name': 'Untitled Room',
        #          'model_type': 'room',
        #          'type': 'water',
        #          'note': '',
        #          'description': '',
        #          'north_id': 2339,
        #          'east_id': None,
        #          'south_id': None,
        #          'west_id': None,
        #          'up_id': None,
        #          'down_id': None,
        #          'zone_id': 76}}
        rooms_qs = rooms_qs or self.rooms.all()
        for room in rooms_qs:
            rooms[room.id] = room.data
            rooms[room.id]['flags'] = []
            room_refs[room.id] = ReferenceField().to_representation(room)

        # Gather room flags
        flags_qs = RoomFlag.objects.filter(
            room__world_id=self.id)

        # Add room flags to rooms
        for flag in flags_qs:
            #print(rooms[flag_room_id])
            rooms[flag.room_id]['flags'].append(flag.code)

        # Gather zones
        zones_qs = self.zones.all()
        zone_refs = {}
        for zone in zones_qs:
            zone_refs[zone.id] = ReferenceField().to_representation(zone)

        # now go through all the rooms again and add the directions + zone
        # references
        rooms_by_key = {}
        for room_id, room_data in rooms.items():
            for direction in adv_consts.DIRECTIONS:
                if room_data.get(direction + '_id'):
                    exit_room_id = room_data[direction + '_id']
                    room_data[direction] = room_refs[exit_room_id]
                else:
                    room_data[direction] = None
                del room_data[direction + '_id']
            try:
                room_data['zone'] = zone_refs[room_data['zone_id']]
            except KeyError:
                print("room has bad zone: %s (%s)" % (
                    room_data['id'],
                    room_data['name']))
            del room_data['zone_id']

            rooms_by_key[room_data['key']] = room_data

        # Now we do a second room pass, resolving all the references

        return rooms_by_key


class InstanceAssignment(BaseModel):
    player = models.ForeignKey('spawns.Player',
                               related_name='player_instances',
                               on_delete=models.CASCADE)
    instance = models.ForeignKey('worlds.World',
                                 related_name='world_instances',
                                 on_delete=models.CASCADE)
    transfer_from = models.ForeignKey('worlds.Room',
                                      related_name='transfer_from_instances',
                                      on_delete=models.SET_NULL,
                                      **optional)
    # When a group leader enters an instance, this a list of comma-seperated
    # IDs of the players who were in that group initially. It will then be
    # used to invite those players into the new instance once it is formed.
    member_ids = models.TextField(**optional)
    # Obsolete
    leader = models.ForeignKey('spawns.Player',
                               related_name='leader_instances',
                               on_delete=models.SET_NULL,
                               **optional)


class WorldLocks(BaseModel):

    world = models.OneToOneField(World, on_delete=models.CASCADE)
    clean_start_ts = models.DateTimeField(**optional)

    @classmethod
    def check_ongoing_cleanup(cls, world):
        "If a world is currently being cleaned up, return the timestamp. Otherwise None."
        with transaction.atomic():
            try:
                lock = cls.objects.select_for_update().get(world=world)
            except WorldLocks.DoesNotExist:
                return None
            return lock.clean_start_ts

    @classmethod
    def start_cleanup(cls, world):
        if not world.context:
            raise TypeError("Cannot lock root world.")
        with transaction.atomic():
            # Lock the row for the duration of the transaction
            try:
                lock = cls.objects.select_for_update().get(world=world)
            except WorldLocks.DoesNotExist:
                lock = cls.objects.create(world=world)
                lock = cls.objects.select_for_update().get(world=world)
            if lock.clean_start_ts is not None:
                raise Exception("Cleanup is already in progress from %s" % lock.clean_start_ts)
            lock.clean_start_ts = timezone.now()
            lock.save()

    @classmethod
    def end_cleanup(cls, world):
        with transaction.atomic():
            # Lock the row for the duration of the transaction
            try:
                lock = cls.objects.select_for_update().get(world=world)
            except WorldLocks.DoesNotExist:
                return
            lock.clean_start_ts = None
            lock.save()


class WorldURL(models.Model):
    world = models.ForeignKey('World', on_delete=models.CASCADE)
    url = models.TextField(unique=True)
    is_private = models.BooleanField(default=False)


class StartingEq(models.Model):
    worldconfig = models.ForeignKey('WorldConfig',
                                    on_delete=models.CASCADE)
    itemtemplate = models.ForeignKey('builders.Itemtemplate',
                                     on_delete=models.CASCADE)
    archetype = models.TextField(
        choices=list_to_choice(adv_consts.ARCHETYPES),
        **optional)
    num = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'worlds_worldconfig_starting_eq'
        unique_together = (('worldconfig', 'itemtemplate'),)


class WorldConfig(BaseModel):

    # Fields not exposed to builders

    can_create_chars = models.BooleanField(default=True)
    has_corpse_decay = models.BooleanField(default=True)
    never_reload = models.BooleanField(default=False)
    autoflee = models.IntegerField(default=0)
    flee_to_unknown_rooms = models.BooleanField(default=True)

    # Builder fields

    # Refs

    starting_room = models.ForeignKey('worlds.Room',
                                      related_name='start_room_for',
                                      on_delete=models.CASCADE,
                                      **optional)
    death_room = models.ForeignKey('worlds.Room',
                                   related_name='death_room_for',
                                   on_delete=models.CASCADE,
                                   **optional)

    exits_to = models.ForeignKey('worlds.Room',
                                 related_name='exits_for',
                                 on_delete=models.SET_NULL,
                                 **optional)

    # Booleans
    can_select_faction = models.BooleanField(default=True)
    auto_equip = models.BooleanField(default=True)
    allow_combat = models.BooleanField(default=True)
    players_can_set_title = models.BooleanField(default=True)
    allow_pvp = models.BooleanField(default=True)
    is_narrative = models.BooleanField(default=False)
    non_ascii_names = models.BooleanField(default=False)
    is_classless = models.BooleanField(default=False)
    globals_enabled = models.BooleanField(default=True)

    # If false, all chars will be default_gender gender
    can_select_gender = models.BooleanField(default=True)

    # Choices
    death_mode = models.TextField(
        choices=list_to_choice(adv_consts.DEATH_MODES),
        default=adv_consts.DEATH_MODE_LOSE_NONE)
    death_route = models.TextField(
        choices=list_to_choice(adv_consts.DEATH_ROUTES),
        default=adv_consts.DEATH_ROUTE_TOP_FACTION)
    pvp_mode = models.TextField(
        choices=list_to_choice(adv_consts.PVP_MODES),
        default=adv_consts.PVP_MODE_FFA)
    default_gender = models.TextField(
        choices=list_to_choice(adv_consts.GENDERS),
        default=adv_consts.GENDER_FEMALE)

    # Values
    built_by = models.TextField(**optional)
    name_exclusions = models.TextField(**optional)
    starting_gold = models.PositiveIntegerField(default=0)
    death_gold_penalty = models.FloatField(default=0.2)
    clan_registration_cost = models.PositiveIntegerField(default=1000)
    # URLs for the frontend to use for world backgrounds.
    # 740 x 332
    small_background = models.TextField(**optional)
    # 2300 x 598
    large_background  = models.TextField(**optional)

    decay_glory = models.BooleanField(default=False)

    cross_race_cooldown = models.PositiveIntegerField(default=0)

    # M2M
    starting_eq = models.ManyToManyField(
        'builders.ItemTemplate',
        related_name='starter_for',
        through='StartingEq')

    def __str__(self):
        return "WorldConfig %s" % self.pk


class Zone(AdventWorldBaseModel):

    world = models.ForeignKey(World,
                              on_delete=models.CASCADE,
                              related_name='zones')

    center = models.ForeignKey('worlds.Room',
                               on_delete=models.CASCADE,
                               related_name='centers_for',
                               **optional)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    notes = models.TextField(**optional)

    is_warzone = models.BooleanField(default=False)
    zone_data = models.TextField(default="{}", blank=True)

    respawn_wait = models.IntegerField(default=300)
    last_respawn_ts = models.DateTimeField(**optional)

    # Applicable for 'zone' pvp mode
    pvp_zone = models.BooleanField(default=False)

    @property
    def key(self):
        return '%s.%s' % (
            CamelCase__to__camel_case(self.__class__.__name__),
            self.id)

    def get_game_key(self, spawn_world):
        return '@{world_id}:{model}.{relative_id}'.format(
            world_id=spawn_world.pk,
            model=self.get_class_name(),
            relative_id=self.id)

    def update_live_instances(self):
        return
        zone = self

        # See if any worlds with this room are currently running
        running_worlds = zone.world.get_running_worlds()

        # If no work is needed, we are done
        if not running_worlds.count():
            return

        # Update all rooms
        for spawn_world in running_worlds:
            pass

Zone.connect_relative_id_post_save_signal()


class RoomFlag(BaseModel):
    code = models.TextField(choices=list_to_choice(
                                adv_consts.ROOM_FLAGS))
    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='flags')

    class Meta:
        unique_together = ('code', 'room')


class Room(AdventWorldBaseModel):

    objects = RoomManager()

    world = models.ForeignKey(World,
                              on_delete=models.CASCADE,
                              related_name='rooms')
    zone = models.ForeignKey(Zone,
                             on_delete=models.SET_NULL,
                             related_name='rooms',
                             **optional)

    name = models.TextField()
    description = models.TextField(**optional)
    note = models.TextField(**optional)

    type = models.TextField(choices=list_to_choice(adv_consts.ROOM_TYPES),
                            default=adv_consts.ROOM_TYPE_INDOOR)

    color = models.TextField(**optional)

    x = models.IntegerField()
    y = models.IntegerField()
    z = models.IntegerField()

    is_landmark = models.BooleanField(default=False)

    north = models.OneToOneField('worlds.Room', related_name='north_exits',
                                 on_delete=models.SET_NULL, **optional)
    east = models.OneToOneField('worlds.Room', related_name='east_exits',
                                on_delete=models.SET_NULL, **optional)
    south = models.OneToOneField('worlds.Room', related_name='south_exits',
                                 on_delete=models.SET_NULL, **optional)
    west = models.OneToOneField('worlds.Room', related_name='west_exits',
                                on_delete=models.SET_NULL, **optional)
    up = models.OneToOneField('worlds.Room', related_name='up_exits',
                                on_delete=models.SET_NULL, **optional)
    down = models.OneToOneField('worlds.Room', related_name='down_exits',
                                on_delete=models.SET_NULL, **optional)

    # Which instance the room grants access to
    enters_instance = models.ForeignKey('worlds.World',
                                        on_delete=models.SET_NULL,
                                        related_name='entrances',
                                        **optional)

    # Room that can be transfered to in another world by being in this room
    transfer_to = models.ForeignKey('worlds.Room',
                                    on_delete=models.SET_NULL,
                                    related_name='transfer_from',
                                    **optional)

    inventory = GenericRelation(
        'spawns.Item',
        content_type_field='container_type',
        object_id_field='container_id')

    # Housing
    ownership_type = models.TextField(
                    choices=list_to_choice(adv_consts.ROOM_OWNERSHIP_TYPES),
                    default=adv_consts.ROOM_OWNERSHIP_TYPE_PRIVATE)
    housing_block = models.ForeignKey('builders.HousingBlock',
                                      related_name='block_rooms',
                                      on_delete=models.SET_NULL,
                                      **optional)

    # Instance exit overwriting the world's default exit if defined
    exits_to = models.ForeignKey('worlds.Room',
                                 related_name='room_exits_for',
                                 on_delete=models.SET_NULL,
                                 **optional)

    class Meta:
        unique_together = [
            AdventWorldBaseModel.Meta.unique_together,
            ['world', 'x', 'y', 'z'],
        ]

    @property
    def key(self):
        return '%s.%s' % (
            CamelCase__to__camel_case(self.__class__.__name__),
            self.id)

    def get_game_key(self, spawn_world):
        return '@{world_id}:{model}.{relative_id}'.format(
            world_id=spawn_world.pk,
            model=self.get_class_name(),
            relative_id=self.id)

    @property
    def data(self):
        "Returns core room data serialization"
        simple_fields = [
            'id', 'key', 'name', 'model_type', 'type', 'note', 'description',
            'x', 'y', 'z', 'color',
        ]
        ref_fields = [
            'north', 'east', 'south', 'west', 'up', 'down', 'zone',
        ]
        data = {}
        for field in simple_fields:
            data[field] = getattr(self, field)
        for field in ref_fields:
            data[field + '_id'] = getattr(self, field + '_id')
        return data

    def get_neighbor(self, direction):
        diff = adv_consts.DIR_COORD_DIFF[direction]
        x = self.x + diff[0]
        y = self.y + diff[1]
        z = self.z + diff[2]
        try:
            return Room.objects.get(
                world=self.world,
                x=x, y=y, z=z)
        except Room.DoesNotExist:
            return None

    def get_inbound_exit_room(self, direction):
        rev_dir = adv_consts.REVERSE_DIRECTIONS[direction]
        qkwargs = {'%s_id' % rev_dir: self.pk}
        try:
            return Room.objects.filter(world=self.world).get(**qkwargs)
        except Room.DoesNotExist:
            return None

    # Operations

    def create_at(self, direction, connect=True):
        room = self
        diff = adv_consts.DIR_COORD_DIFF[direction]
        x = room.x + diff[0]
        y = room.y + diff[1]
        z = room.z + diff[2]

        # Make sure there isn't already a room there
        try:
            room = Room.objects.get(world=room.world, x=x, y=y, z=z)
            raise ValueError("A room already exists %s." % direction)
        except Room.DoesNotExist:
            pass

        new_room = Room.objects.create(
            world=room.world,
            type=room.type,
            zone=room.zone,
            name='Untitled Room',
            x=x, y=y, z=z)
        if connect:
            setattr(room, direction, new_room)
            room.save()
            setattr(new_room, adv_consts.REVERSE_DIRECTIONS[direction], room)
            new_room.save()
        return new_room

    def update_live_instances(self):
        # Currently a no-op until we get a better idea of where live room
        # data will reside.
        return

        room = self

        # See if any worlds with this room are currently running
        running_worlds = room.world.get_running_worlds()

        # If no work is needed, we are done
        if not running_worlds.count():
            return room

        # Update all rooms
        for spawn_world in running_worlds:
            pass

Room.connect_relative_id_post_save_signal()

# On room save, empty out the world's full map
def post_room_save(sender, **kwargs):
    room = kwargs['instance']
    room.world.full_map = None
    room.world.save(update_fields=['full_map'])
models.signals.post_save.connect(post_room_save, Room)


class RoomDetail(AdventBaseModel):

    room = models.ForeignKey('worlds.Room',
                             on_delete=models.CASCADE,
                             related_name='details')

    keywords = models.TextField()
    description = models.TextField()
    is_hidden = models.BooleanField(default=False)


class Door(AdventBaseModel):

    direction = models.TextField(
        choices=list_to_choice(adv_consts.DIRECTIONS))

    from_room = models.ForeignKey('worlds.Room',
                                  on_delete=models.CASCADE,
                                  related_name='doors_from')
    to_room = models.ForeignKey('worlds.Room',
                                on_delete=models.CASCADE,
                                related_name='doors_to')
    name = models.TextField(default='door')
    key = models.ForeignKey('builders.ItemTemplate',
                            on_delete=models.CASCADE,
                            related_name='key_doors',
                            **optional)
    destroy_key = models.BooleanField(default=False)
    default_state = models.TextField(
        choices=list_to_choice(adv_consts.DOOR_STATES),
        default=adv_consts.DOOR_STATE_CLOSED)
    # SPWs only
    #current_state = models.TextField(default=adv_consts.DOOR_STATE_CLOSED)
