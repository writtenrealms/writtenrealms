from datetime import datetime
from decimal import Decimal
import json
import logging
import traceback

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

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
from core.equipment_system import default_equipment_system
from core.leveling import default_leveling_curve
from core.stat_system import default_stat_system
from core.abilities import default_ability_progression
from worlds.managers import (
    WorldManager,
    RoomManager)


lifecycle_logger = logging.getLogger('lifecycle')

BIGINT_MAX = 9_223_372_036_854_775_807
MAX_ALLOCATABLE_ROOM_RELATIVE_ID = BIGINT_MAX - 1
INSTANCE_SLUG_DB_PATTERN = (
    r'^([a-z0-9]|[a-z0-9][a-z0-9_-]*[a-z0-9])$'
)


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

    last_spawn_plan_run_ts = models.DateTimeField(**optional)
    last_extraction_ts = models.DateTimeField(**optional)
    last_entered_ts = models.DateTimeField(**optional)

    # Persistent high-water mark for authored room identities. Unlike MAX + 1,
    # this does not reuse a room reference after the highest-numbered room is
    # deleted. It is touched only on builder/import room creation paths.
    next_room_relative_id = models.PositiveBigIntegerField(
        default=1,
        editable=False,
    )

    full_map = models.TextField(**optional)

    facts = models.TextField(**optional)
    # Authored defaults copied into each spawned runtime world. Live state is
    # stored in WorldState and never written back to this definition field.
    initial_state = models.JSONField(default=dict, blank=True)

    @classmethod
    def from_db(cls, db, field_names, values):
        world = super().from_db(db, field_names, values)
        loaded_values = dict(zip(field_names, values))
        identity_fields = (
            'instance_of_id',
            'context_id',
            'instance_slug',
        )
        if all(field in loaded_values for field in identity_fields):
            world._loaded_manifest_identity = tuple(
                loaded_values[field]
                for field in identity_fields
            )
        return world

    def _assert_manifest_identity_unchanged(self, *, using):
        loaded_identity = getattr(
            self,
            '_loaded_manifest_identity',
            None,
        )
        if loaded_identity is None:
            loaded_identity = (
                World.objects.using(using)
                .filter(pk=self.pk)
                .values_list(
                    'instance_of_id',
                    'context_id',
                    'instance_slug',
                )
                .first()
            )
        if loaded_identity is None:
            return
        current_identity = (
            self.instance_of_id,
            self.context_id,
            self.instance_slug,
        )
        if current_identity != loaded_identity:
            raise ValidationError(
                'A world manifest scope is immutable after creation.')

    def _allocate_instance_slug(self, *, using):
        if not self.instance_of_id or self.context_id:
            if self.instance_slug not in (None, ''):
                raise ValidationError(
                    'Only authored instance templates may have an '
                    'instance slug.')
            self.instance_slug = None
            return

        parent = World.objects.using(using).select_for_update().only(
            'id',
            'context_id',
            'instance_of_id',
        ).get(
            pk=self.instance_of_id,
        )
        if parent.context_id or parent.instance_of_id:
            raise ValidationError(
                'Authored instance templates must belong directly to a '
                'base world.')
        raw_slug = str(self.instance_slug or '').strip()
        if raw_slug:
            if len(raw_slug) > 120:
                raise ValidationError(
                    'Instance slugs cannot exceed 120 characters.')
            normalized = slugify(raw_slug)
            if not normalized or normalized != raw_slug:
                raise ValidationError(
                    'Instance slugs must already be lowercase slug values.')
            self.instance_slug = normalized
            return

        base_slug = slugify(self.name or '')[:100] or 'instance'
        sibling_slugs = set(
            World.objects.using(using)
            .filter(
                instance_of_id=self.instance_of_id,
                context__isnull=True,
            )
            .exclude(instance_slug__isnull=True)
            .values_list('instance_slug', flat=True)
        )
        candidate = base_slug
        suffix = 2
        while candidate in sibling_slugs:
            suffix_text = f'-{suffix}'
            candidate = (
                f'{base_slug[:120 - len(suffix_text)]}{suffix_text}'
            )
            suffix += 1
        self.instance_slug = candidate

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or router.db_for_write(
            self.__class__,
            instance=self,
        )
        if self._state.adding:
            with transaction.atomic(using=using):
                self._allocate_instance_slug(using=using)
                result = super().save(*args, **kwargs)
            self._loaded_manifest_identity = (
                self.instance_of_id,
                self.context_id,
                self.instance_slug,
            )
            return result

        self._assert_manifest_identity_unchanged(using=using)
        # Room allocation advances this field with an atomic queryset update.
        # A previously loaded World instance must never write an older value
        # back during an unrelated full save. Lock the same world row used by
        # Room.save() so an allocation cannot commit between this refresh and
        # the subsequent UPDATE.
        update_fields = kwargs.get('update_fields')
        writes_allocator = update_fields is None or (
            'next_room_relative_id' in update_fields
        )
        if not self._state.adding and self.pk and writes_allocator:
            with transaction.atomic(using=using):
                persisted_value = (
                    World.objects.using(using)
                    .select_for_update()
                    .filter(pk=self.pk)
                    .values_list('next_room_relative_id', flat=True)
                    .first()
                )
                if (
                    persisted_value is not None
                    and self.next_room_relative_id < persisted_value
                ):
                    self.next_room_relative_id = persisted_value
                result = super().save(*args, **kwargs)
        else:
            result = super().save(*args, **kwargs)
        self._loaded_manifest_identity = (
            self.instance_of_id,
            self.context_id,
            self.instance_slug,
        )
        return result

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
    # Stable authored identity within one base world's instance-template
    # family. Runtime spawned worlds use instance_ref instead.
    instance_slug = models.SlugField(
        max_length=120,
        editable=False,
        **optional,
    )
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

    default_currency = models.ForeignKey(
        'builders.Currency',
        related_name='default_for_worlds',
        on_delete=models.RESTRICT,
        **optional)
    economy_revision = models.BigIntegerField(default=0)

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
        base_manager_name = 'objects'
        ordering = ('-created_ts',)
        constraints = [
            models.UniqueConstraint(
                fields=['instance_of', 'instance_slug'],
                condition=Q(
                    context__isnull=True,
                    instance_of__isnull=False,
                    instance_slug__isnull=False,
                ),
                name='worlds_instance_template_slug_unique',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        context__isnull=True,
                        instance_of__isnull=False,
                        instance_slug__isnull=False,
                        instance_slug__regex=INSTANCE_SLUG_DB_PATTERN,
                    )
                    & ~Q(instance_slug__contains='--')
                    | Q(
                        instance_slug__isnull=True,
                        instance_of__isnull=True,
                    )
                    | Q(
                        instance_slug__isnull=True,
                        context__isnull=False,
                    )
                ),
                name='worlds_instance_slug_authored_template',
            ),
        ]

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
        from core.scoped_state import (
            STATE_SCOPE_WORLD,
            get_state_snapshot,
            replace_state_snapshot,
        )

        if not self.context:
            raise RuntimeError("Can only save spawn worlds.")

        try:
            with transaction.atomic():
                world = World.objects.select_for_update().get(pk=self.pk)
                if world.save_start_ts:
                    return
                world.save_start_ts = timezone.now()
                world.save(update_fields=['save_start_ts'])

            state_snapshot = get_state_snapshot(STATE_SCOPE_WORLD, self)
            if hasattr(game_world, 'facts'):
                state_snapshot = dict(getattr(game_world, 'facts', {}) or {})
            fact_schedules = self.context.fact_schedules.filter(
                Q(next_run_ts__isnull=True)
                | (Q(next_run_ts__isnull=False) &
                Q(next_run_ts__lt=timezone.now())))
            updated_facts = []
            for fact_schedule in fact_schedules:
                updated_facts.append(fact_schedule.run(state_snapshot))
                try:
                    fact_schedule.set_next_run()
                except:
                    print("Error updating fact schedule for %s:" % self.id)
                    traceback.print_exc()
            for fact_change in updated_facts:
                state_snapshot[fact_change['fact']] = fact_change['new_value']
                if (fact_change['msg']
                    and fact_change['old_value'] != fact_change['new_value']):
                    # add_timing(
                    #     world=self.key,
                    #     type='timing.game_write',
                    #     data={'text': fact_change['msg']},
                    #     db=self.rdb)
                    pass
            replace_state_snapshot(STATE_SCOPE_WORLD, self, state_snapshot)
            if hasattr(game_world, 'facts'):
                game_world.facts = state_snapshot

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
        This is meant to be done before an initial spawn-plan run in a multiplayer
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
        mobs_qs = self.mobs.filter(is_pending_deletion=True) if spw else self.mobs.all()
        batch_deletion(mobs_qs)

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
                self.leave_instance(
                    player=player,
                    force_active_duel=True,
                )

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
        run spawn plans in initial mode.

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

        from spawns.loading import run_spawn_plans_for_world
        run_spawn_plans_for_world(world=self, initial=True)

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

        if not self.last_spawn_plan_run_ts:
            from spawns.loading import run_spawn_plans_for_world
            run_spawn_plans_for_world(world=self, initial=True)

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
        from core.scoped_state import initialize_runtime_state

        initialize_runtime_state(spawn_world)
        return spawn_world

    def instance_for(self, player, transfer_from=None, ref=None, member_ids=None, **kwargs):
        """
        Get or create the appropriate instance of a world for a player.
        """
        from worlds.instances import get_or_create_instance_run
        run = get_or_create_instance_run(
            self,
            player=player,
            transfer_from=transfer_from,
            ref=ref,
            member_ids=member_ids,
            **kwargs)
        return run.spawned_world


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
        from worlds.instances import enter_instance
        transfer_to = Room.objects.get(pk=transfer_to_id)
        transfer_from = Room.objects.get(pk=transfer_from_id)
        run = enter_instance(
            player=player,
            transfer_to=transfer_to,
            transfer_from=transfer_from,
            ref=ref,
            member_ids=member_ids)
        return run.spawned_world

    @classmethod
    def leave_instance(cls, player, force_active_duel=False):
        from worlds.instances import leave_instance
        return leave_instance(
            player=player,
            force_active_duel=force_active_duel,
        )

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

    def create_item_definition(self, **kwargs):
        from builders.models import ItemDefinition
        kwargs.pop('world', None)
        return ItemDefinition.objects.create(world=self, **kwargs)

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


class InstanceRun(BaseModel):
    STATUS_CREATED = 'created'
    STATUS_ACTIVE = 'active'
    STATUS_RESOLVING = 'resolving'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_EXPIRED = 'expired'
    STATUS_ABANDONED = 'abandoned'
    STATUS_CLOSED = 'closed'
    STATUS_CLEANED = 'cleaned'
    STATUS_CHOICES = (
        STATUS_CREATED,
        STATUS_ACTIVE,
        STATUS_RESOLVING,
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_EXPIRED,
        STATUS_ABANDONED,
        STATUS_CLOSED,
        STATUS_CLEANED,
    )
    ACTIVE_STATUSES = (
        STATUS_CREATED,
        STATUS_ACTIVE,
        STATUS_RESOLVING,
    )

    base_world = models.ForeignKey(
        'worlds.World',
        related_name='instance_runs_as_base',
        on_delete=models.CASCADE)
    template_world = models.ForeignKey(
        'worlds.World',
        related_name='instance_runs_as_template',
        on_delete=models.CASCADE)
    spawned_world = models.OneToOneField(
        'worlds.World',
        related_name='instance_run',
        on_delete=models.CASCADE)
    ref = models.TextField(db_index=True)
    leader = models.ForeignKey(
        'spawns.Player',
        related_name='led_instance_runs',
        on_delete=models.SET_NULL,
        **optional)
    status = models.TextField(
        choices=list_to_choice(STATUS_CHOICES),
        default=STATUS_ACTIVE,
        db_index=True)
    started_at = models.DateTimeField(**optional)
    last_active_at = models.DateTimeField(**optional)
    completed_at = models.DateTimeField(**optional)
    failed_at = models.DateTimeField(**optional)
    expires_at = models.DateTimeField(**optional)
    closed_at = models.DateTimeField(**optional)
    cleanup_after = models.DateTimeField(**optional)
    goal_spec = models.JSONField(default=dict, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    outcome = models.JSONField(default=dict, blank=True)
    seed = models.TextField(blank=True)
    initial_member_ids = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['ref']),
            models.Index(fields=['base_world', 'status']),
            models.Index(fields=['template_world', 'status']),
        ]

    def __str__(self):
        return "InstanceRun %s for %s" % (self.id, self.template_world)


class InstanceParticipant(BaseModel):
    ROLE_LEADER = 'leader'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = (
        ROLE_LEADER,
        ROLE_MEMBER,
    )
    EXIT_REASON_LEFT = 'left'
    EXIT_REASON_FORCED = 'forced'
    EXIT_REASON_REPLACED = 'replaced'
    EXIT_REASON_DEATH_DELEGATED = 'death_delegated'
    EXIT_REASON_CHOICES = (
        EXIT_REASON_LEFT,
        EXIT_REASON_FORCED,
        EXIT_REASON_REPLACED,
        EXIT_REASON_DEATH_DELEGATED,
    )

    run = models.ForeignKey(
        'worlds.InstanceRun',
        related_name='participants',
        on_delete=models.CASCADE)
    player = models.ForeignKey(
        'spawns.Player',
        related_name='instance_participations',
        on_delete=models.CASCADE)
    role = models.TextField(
        choices=list_to_choice(ROLE_CHOICES),
        default=ROLE_MEMBER,
        db_index=True)
    transfer_from = models.ForeignKey(
        'worlds.Room',
        related_name='instance_participants_from',
        on_delete=models.SET_NULL,
        **optional)
    return_runtime_world = models.ForeignKey(
        'worlds.World',
        related_name='returning_instance_participants',
        on_delete=models.RESTRICT,
        **optional)
    joined_at = models.DateTimeField(default=timezone.now)
    exited_at = models.DateTimeField(**optional)
    exit_reason = models.TextField(
        choices=list_to_choice(EXIT_REASON_CHOICES),
        **optional)

    class Meta(BaseModel.Meta):
        unique_together = ('run', 'player')
        indexes = [
            models.Index(fields=['player', 'exited_at']),
            models.Index(fields=['run', 'exited_at']),
            models.Index(fields=['role']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['player'],
                condition=models.Q(exited_at__isnull=True),
                name='worlds_instance_one_active_player'),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        exited_at__isnull=True,
                        exit_reason__isnull=True,
                        return_runtime_world__isnull=False,
                    )
                    | models.Q(
                        exited_at__isnull=False,
                        exit_reason__isnull=False,
                        return_runtime_world__isnull=True,
                    )
                ),
                name='worlds_instance_participant_exit_shape',
            ),
        ]

    def __str__(self):
        return "%s in %s" % (self.player, self.run)


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


class WorldState(BaseModel):

    world = models.OneToOneField(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='scoped_state',
    )
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['world_id']


class WorldConfig(BaseModel):

    DEATH_ROUTING_SOURCE_LOCAL = 'local'
    DEATH_ROUTING_SOURCE_BASE_WORLD = 'base_world'
    DEATH_ROUTING_SOURCES = (
        DEATH_ROUTING_SOURCE_LOCAL,
        DEATH_ROUTING_SOURCE_BASE_WORLD,
    )

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
                                   on_delete=models.RESTRICT,
                                   **optional)
    death_currency = models.ForeignKey(
        'builders.Currency',
        related_name='death_policies',
        on_delete=models.RESTRICT,
        **optional)
    clan_registration_currency = models.ForeignKey(
        'builders.Currency',
        related_name='clan_registration_policies',
        on_delete=models.RESTRICT,
        **optional)

    exits_to = models.ForeignKey('worlds.Room',
                                 related_name='exits_for',
                                 on_delete=models.SET_NULL,
                                 **optional)

    # Booleans
    can_select_faction = models.BooleanField(default=True)
    auto_equip = models.BooleanField(default=True)
    allow_combat = models.BooleanField(default=True)
    # Encounter pacing in seconds:
    #   > 0 => auto-advance combat encounters on this cadence
    #   0   => resolve immediately
    #   -1  => never auto-advance (manual / fully async progression)
    combat_resolution_interval = models.FloatField(default=0)
    default_roam_chance = models.PositiveIntegerField(default=10)
    combat_system = models.JSONField(default=dict)
    ability_progression = models.JSONField(default=default_ability_progression)
    player_creation = models.JSONField(default=dict, blank=True)
    players_can_set_title = models.BooleanField(default=True)
    is_narrative = models.BooleanField(default=False)
    non_ascii_names = models.BooleanField(default=False)
    is_classless = models.BooleanField(default=True)
    globals_enabled = models.BooleanField(default=True)
    announce_duel_results = models.BooleanField(default=False)
    equipment_system = models.JSONField(default=default_equipment_system)
    stat_system = models.JSONField(default=default_stat_system)

    # If false, all chars will be default_gender gender
    can_select_gender = models.BooleanField(default=True)

    # Choices
    death_mode = models.TextField(
        choices=list_to_choice(adv_consts.DEATH_MODES),
        default=adv_consts.DEATH_MODE_LOSE_NONE)
    death_route = models.TextField(
        choices=list_to_choice(adv_consts.DEATH_ROUTES),
        default=adv_consts.DEATH_ROUTE_TOP_FACTION)
    death_routing_source = models.TextField(
        choices=list_to_choice(DEATH_ROUTING_SOURCES),
        default=DEATH_ROUTING_SOURCE_LOCAL,
    )
    pvp_mode = models.TextField(
        choices=list_to_choice(adv_consts.PVP_MODES),
        default=adv_consts.PVP_MODE_FFA)
    default_gender = models.TextField(
        choices=list_to_choice(adv_consts.GENDERS),
        default=adv_consts.GENDER_MALE)

    # Values
    built_by = models.TextField(**optional)
    name_exclusions = models.TextField(**optional)
    starting_equipment = models.JSONField(default=list, blank=True)
    starting_level = models.PositiveIntegerField(default=1)
    leveling_curve = models.JSONField(default=default_leveling_curve)
    max_level = models.PositiveIntegerField(default=20)
    # Monotonic routing identities belong to the config rather than the
    # replaceable policy row, so rebuilt plans can never reuse a cache key.
    death_routing_generation = models.BigIntegerField(default=0)
    death_routing_source_generation = models.BigIntegerField(default=0)
    death_currency_penalty = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        default=Decimal('0.2'),
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('1')),
        ])
    clan_registration_cost = models.BigIntegerField(
        default=0,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(9007199254740991),
        ])
    # URLs for the frontend to use for world backgrounds.
    # 740 x 332
    small_background = models.TextField(**optional)
    # 2300 x 598
    large_background  = models.TextField(**optional)

    decay_glory = models.BooleanField(default=False)

    cross_race_cooldown = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    death_currency_penalty__gte=0,
                    death_currency_penalty__lte=1,
                ),
                name='worlds_death_currency_penalty_rate'),
            models.CheckConstraint(
                condition=models.Q(
                    clan_registration_cost__gte=0,
                    clan_registration_cost__lte=9007199254740991,
                ),
                name='worlds_clan_registration_cost_safe'),
        ]

    @property
    def allow_pvp(self):
        """Legacy runtime alias; ``pvp_mode`` is the canonical setting."""
        return self.pvp_mode != adv_consts.PVP_MODE_DISABLED

    def __str__(self):
        return "WorldConfig %s" % self.pk


class DeathRoutingPolicy(BaseModel):
    """Canonical selector policy for one world or instance config."""

    config = models.OneToOneField(
        'worlds.WorldConfig',
        related_name='death_routing_policy',
        on_delete=models.CASCADE,
    )
    enabled = models.BooleanField(default=False)


class DeathRoutingRoute(BaseModel):
    """One canonical ordered first-match route."""

    policy = models.ForeignKey(
        'worlds.DeathRoutingPolicy',
        related_name='routes',
        on_delete=models.CASCADE,
    )
    position = models.PositiveSmallIntegerField()
    # ``condition`` is normalized portable DSL for exact manifest round trips.
    # ``compiled_condition`` contains the bounded, query-free identifier IR.
    condition = models.JSONField(default=dict)
    compiled_version = models.PositiveSmallIntegerField(default=2)
    compiled_condition = models.JSONField(default=dict)
    destination_room = models.ForeignKey(
        'worlds.Room',
        related_name='death_routing_routes',
        on_delete=models.RESTRICT,
    )

    class Meta(BaseModel.Meta):
        ordering = ['position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['policy', 'position'],
                name='worlds_death_route_position_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(position__lt=32),
                name='worlds_death_route_position_bound',
            ),
        ]


class DeathRoutingCompiledSnapshot(BaseModel):
    """Immutable, rebuildable ordered plan for one policy generation."""

    CACHE_VERSION = 2

    config = models.ForeignKey(
        'worlds.WorldConfig',
        related_name='death_routing_snapshots',
        on_delete=models.CASCADE,
    )
    plan_generation = models.BigIntegerField()
    cache_version = models.PositiveSmallIntegerField(default=CACHE_VERSION)
    data = models.JSONField(default=dict)
    retirement_pending = models.BooleanField(default=False)
    retired_at = models.DateTimeField(**optional)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['config', 'plan_generation', 'cache_version'],
                name='worlds_death_route_snapshot_unique',
            ),
        ]


class DeathRoutingSnapshotReference(BaseModel):
    """
    Relational retention for identifiers embedded in an immutable snapshot.

    Runtime plans use compact integer maps. These RESTRICT references protect
    the current generation's rooms, faction keys, and zones. Publication takes
    an exclusive config lock, so references are released only after all
    shared in-flight death resolutions have drained.
    """

    snapshot = models.ForeignKey(
        'worlds.DeathRoutingCompiledSnapshot',
        related_name='references',
        on_delete=models.CASCADE,
    )
    destination_room = models.ForeignKey(
        'worlds.Room',
        related_name='death_routing_snapshot_references',
        on_delete=models.RESTRICT,
        **optional,
    )
    core_faction = models.ForeignKey(
        'builders.Faction',
        related_name='death_routing_snapshot_references',
        on_delete=models.RESTRICT,
        **optional,
    )
    origin_zone = models.ForeignKey(
        'worlds.Zone',
        related_name='death_routing_snapshot_references',
        on_delete=models.RESTRICT,
        **optional,
    )

    class Meta(BaseModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        destination_room__isnull=False,
                        core_faction__isnull=True,
                        origin_zone__isnull=True,
                    )
                    | models.Q(
                        destination_room__isnull=True,
                        core_faction__isnull=False,
                        origin_zone__isnull=True,
                    )
                    | models.Q(
                        destination_room__isnull=True,
                        core_faction__isnull=True,
                        origin_zone__isnull=False,
                    )
                ),
                name='worlds_death_snapshot_ref_one_target',
            ),
            models.UniqueConstraint(
                fields=['snapshot', 'destination_room'],
                condition=models.Q(destination_room__isnull=False),
                name='worlds_death_snapshot_room_unique',
            ),
            models.UniqueConstraint(
                fields=['snapshot', 'core_faction'],
                condition=models.Q(core_faction__isnull=False),
                name='worlds_death_snapshot_faction_unique',
            ),
            models.UniqueConstraint(
                fields=['snapshot', 'origin_zone'],
                condition=models.Q(origin_zone__isnull=False),
                name='worlds_death_snapshot_zone_unique',
            ),
        ]


class Zone(AdventWorldBaseModel):

    world = models.ForeignKey(World,
                              on_delete=models.CASCADE,
                              related_name='zones')

    center = models.ForeignKey('worlds.Room',
                               on_delete=models.SET_NULL,
                               related_name='centers_for',
                               **optional)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    notes = models.TextField(**optional)

    zone_data = models.TextField(default="{}", blank=True)
    # Authored defaults copied into per-runtime ZoneState rows.
    initial_state = models.JSONField(default=dict, blank=True)

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

class ZoneState(BaseModel):

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='zone_state_records',
    )
    zone = models.ForeignKey(
        'worlds.Zone',
        on_delete=models.CASCADE,
        related_name='runtime_state_records',
    )
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['world_id', 'zone_id']
        constraints = [
            models.UniqueConstraint(
                fields=['world', 'zone'],
                name='worlds_zone_state_runtime_owner',
            ),
        ]


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

    # Coordinates are mutable placement. This world-scoped number is the
    # room's permanent authored identity and portable manifest reference.
    relative_id = models.PositiveBigIntegerField(editable=False)

    world = models.ForeignKey(World,
                              on_delete=models.CASCADE,
                              related_name='rooms')
    zone = models.ForeignKey(Zone,
                             on_delete=models.SET_NULL,
                             related_name='rooms',
                             **optional)
    crafting_profile = models.ForeignKey(
        'builders.CraftingProfile',
        on_delete=models.SET_NULL,
        related_name='rooms',
        **optional)
    merchant_profile = models.ForeignKey(
        'builders.MerchantProfile',
        on_delete=models.SET_NULL,
        related_name='rooms',
        **optional)

    name = models.TextField()
    description = models.TextField(**optional)
    note = models.TextField(**optional)

    type = models.TextField(choices=list_to_choice(adv_consts.ROOM_TYPES),
                            default=adv_consts.ROOM_TYPE_INDOOR)

    color = models.TextField(**optional)
    # Authored defaults copied into per-runtime RoomState rows.
    initial_state = models.JSONField(default=dict, blank=True)

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
        base_manager_name = 'objects'
        unique_together = [
            AdventWorldBaseModel.Meta.unique_together,
            ['world', 'x', 'y', 'z'],
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(relative_id__gt=0),
                name='worlds_room_relative_id_positive',
            ),
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        room = super().from_db(db, field_names, values)
        loaded_values = dict(zip(field_names, values))
        if 'world_id' in loaded_values and 'relative_id' in loaded_values:
            room._loaded_room_identity = (
                loaded_values['world_id'],
                loaded_values['relative_id'],
            )
        return room

    @staticmethod
    def _coerce_relative_id(value):
        if isinstance(value, bool):
            raise ValidationError(
                'Room relative IDs must be positive integers.')
        try:
            relative_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                'Room relative IDs must be positive integers.') from exc
        if relative_id <= 0:
            raise ValidationError(
                'Room relative IDs must be positive integers.')
        return relative_id

    def _assert_identity_unchanged(self, *, using):
        loaded_identity = getattr(self, '_loaded_room_identity', None)
        if loaded_identity is None:
            loaded_identity = (
                Room.objects.using(using)
                .filter(pk=self.pk)
                .values_list('world_id', 'relative_id')
                .first()
            )
        if loaded_identity is None:
            return
        current_identity = (self.world_id, self.relative_id)
        if current_identity != loaded_identity:
            raise ValidationError(
                'A room world and relative ID are immutable after creation.')

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or router.db_for_write(
            self.__class__,
            instance=self,
        )

        if not self._state.adding:
            self._assert_identity_unchanged(using=using)
            result = super().save(*args, **kwargs)
            self._loaded_room_identity = (self.world_id, self.relative_id)
            return result

        if self.world_id is None:
            raise ValidationError(
                'A room must belong to a world before it can be created.')

        with transaction.atomic(using=using):
            world = (
                World.objects.using(using)
                .select_for_update()
                .only('id', 'next_room_relative_id')
                .get(pk=self.world_id)
            )
            next_relative_id = int(world.next_room_relative_id)
            if next_relative_id > MAX_ALLOCATABLE_ROOM_RELATIVE_ID:
                raise ValidationError(
                    'Room relative ID space is exhausted for this world.')
            if self.relative_id is None:
                self.relative_id = next_relative_id
            else:
                self.relative_id = self._coerce_relative_id(self.relative_id)
                if self.relative_id > MAX_ALLOCATABLE_ROOM_RELATIVE_ID:
                    raise ValidationError(
                        'Room relative ID space is exhausted for this world.')
                if self.relative_id < next_relative_id:
                    raise ValidationError(
                        'Room relative ID '
                        f'{self.relative_id} was already allocated or retired.')

            result = super().save(*args, **kwargs)
            # PostgreSQL also enforces this invariant in a BEFORE INSERT
            # trigger so raw SQL and alternate managers cannot reuse retired
            # identities. Keep the model update for other database backends.
            World.objects.db_manager(using).advance_room_identity_allocator(
                world_id=self.world_id,
                next_relative_id=self.relative_id + 1,
            )
            cached_world = self._state.fields_cache.get('world')
            if cached_world is not None:
                cached_world.next_room_relative_id = self.relative_id + 1

        self._loaded_room_identity = (self.world_id, self.relative_id)
        return result

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
            'id', 'key', 'relative_id', 'name', 'model_type', 'type', 'note',
            'description', 'x', 'y', 'z', 'color',
        ]
        ref_fields = [
            'north', 'east', 'south', 'west', 'up', 'down', 'zone',
        ]
        data = {}
        for field in simple_fields:
            data[field] = getattr(self, field)
        data['manifest_ref'] = f'room@{self.relative_id}'
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


class RoomState(BaseModel):

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='room_state_records',
    )
    room = models.ForeignKey(
        'worlds.Room',
        on_delete=models.CASCADE,
        related_name='runtime_state_records',
    )
    data = models.JSONField(default=dict)
    version = models.BigIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['world_id', 'room_id']
        constraints = [
            models.UniqueConstraint(
                fields=['world', 'room'],
                name='worlds_room_state_runtime_owner',
            ),
        ]


# On room save, empty out the world's full map
def post_room_save(sender, **kwargs):
    room = kwargs['instance']
    if kwargs.get('raw') and room.relative_id is not None:
        # Fixture loading bypasses Room.save(). Keep the allocator coherent for
        # any rooms created after a fixture has been installed.
        World.objects.db_manager(
            kwargs.get('using'),
        ).advance_room_identity_allocator(
            world_id=room.world_id,
            next_relative_id=room.relative_id + 1,
        )
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


class Doorway(AdventBaseModel):
    """One authored doorway shared by one or two directional door faces."""

    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='doorways',
    )
    key = models.ForeignKey(
        'builders.ItemDefinition',
        on_delete=models.RESTRICT,
        related_name='key_doorways',
        **optional,
    )
    destroy_key = models.BooleanField(default=False)
    default_state = models.TextField(
        choices=list_to_choice(adv_consts.DOOR_STATES),
        default=adv_consts.DOOR_STATE_CLOSED,
    )

class Door(AdventBaseModel):
    """A directional face of a logical doorway."""

    doorway = models.ForeignKey(
        'worlds.Doorway',
        on_delete=models.CASCADE,
        related_name='faces',
    )

    direction = models.TextField(
        choices=list_to_choice(adv_consts.DIRECTIONS))

    from_room = models.ForeignKey('worlds.Room',
                                  on_delete=models.CASCADE,
                                  related_name='doors_from')
    to_room = models.ForeignKey('worlds.Room',
                                on_delete=models.CASCADE,
                                related_name='doors_to')
    name = models.TextField(default='door')

    class Meta(AdventBaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['from_room', 'direction'],
                name='worlds_door_unique_room_direction',
            ),
            models.UniqueConstraint(
                fields=['doorway', 'from_room'],
                name='worlds_door_unique_doorway_room',
            ),
            models.CheckConstraint(
                condition=~models.Q(from_room=models.F('to_room')),
                name='worlds_door_distinct_rooms',
            ),
        ]
    @property
    def key(self):
        return self.doorway.key

    @property
    def key_id(self):
        return self.doorway.key_id

    @property
    def destroy_key(self):
        return self.doorway.destroy_key

    @property
    def default_state(self):
        return self.doorway.default_state
