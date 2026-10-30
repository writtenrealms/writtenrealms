import os

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator
from django.utils import timezone

from config import game_settings as adv_config

from config import constants as api_consts
from core.db import (
  AdventBaseModel,
  BaseModel,
  list_to_choice,
  optional)


class IntroConfig(models.Model):
    "Should only ever be one of those"

    world = models.ForeignKey('worlds.World', on_delete=models.CASCADE)


class EdeusUniques(AdventBaseModel):

    run_ts = models.DateTimeField()
    warrior = models.ForeignKey('spawns.player',
                                on_delete=models.SET_NULL,
                                related_name='warrior_uniques',
                                **optional)
    mage = models.ForeignKey('spawns.player',
                             on_delete=models.SET_NULL,
                             related_name='mage_uniques',
                             **optional)
    cleric = models.ForeignKey('spawns.player',
                               on_delete=models.SET_NULL,
                               related_name='cleric_uniques',
                               **optional)
    assassin = models.ForeignKey('spawns.player',
                                 on_delete=models.SET_NULL,
                                 related_name='assassin_uniques',
                                 **optional)


class SiteControl(AdventBaseModel):

    name = models.TextField()
    founders_enabled = models.BooleanField(default=False)
    access_mode = models.CharField(max_length=16, default='open', choices=(
        ('open', 'Open'), ('invite_only', 'Invite only'), ('closed', 'Closed'),
    ))
    maintenance_mode = models.BooleanField(default=False)
    building_enabled = models.BooleanField(
        default=True,
        help_text=(
            'Allow any signed-up user to create worlds and see the Build '
            'menu. Staff can always build, and builders keep access to '
            'worlds they already author or were added to.'
        ),
    )
    main_world_id = models.PositiveIntegerField(
        default=1, null=True, blank=True, validators=[MinValueValidator(1)],
        help_text=(
            'Root world opened by the homepage and lobby. Defaults to world 1. '
            'Leave blank to show the multi-world homepage and lobby. '
            'This does not change world visibility or access permissions.'
        ),
    )


class AccessAllowlistEntry(BaseModel):
    email = models.EmailField(unique=True)
    note = models.CharField(max_length=500, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['email']
        constraints = [models.UniqueConstraint(
            Lower('email'), name='system_allowlist_email_ci_unique')]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().casefold()
        return super().save(*args, **kwargs)


class StaffAuditEvent(models.Model):
    created_ts = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    actor_email = models.EmailField(blank=True, default='')
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=255, blank=True, default='')
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ['-id']


class SupportEvent(models.Model):
    """Small historical snapshots, retained independently of runtime objects."""

    created_ts = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=48)
    reason = models.CharField(max_length=64, blank=True)
    # Deliberately not foreign keys: instance/character deletion must not erase
    # the evidence or add locks to the gameplay objects being diagnosed.
    player_id = models.PositiveBigIntegerField(null=True)
    user_id = models.PositiveBigIntegerField(null=True)
    player_name = models.CharField(max_length=100, blank=True)
    world_id = models.PositiveBigIntegerField(null=True)
    world_name = models.CharField(max_length=100, blank=True)
    base_world_id = models.PositiveBigIntegerField(null=True)
    template_world_id = models.PositiveBigIntegerField(null=True)
    run_id = models.PositiveBigIntegerField(null=True)
    room_id = models.PositiveBigIntegerField(null=True)
    details = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=['player_id', 'created_ts'], name='support_player_time'),
            models.Index(fields=['base_world_id', 'created_ts'], name='support_world_time'),
            models.Index(fields=['run_id', 'created_ts'], name='support_run_time'),
        ]


class Nexus(BaseModel):
    "Maps to a Kubernetes pod / service / ingress package."

    name = models.TextField()
    state = models.TextField(choices=list_to_choice(api_consts.NEXUS_STATES),
                             default=api_consts.NEXUS_STATE_ABSENT)
    last_activity_ts = models.DateTimeField(**optional)

    maintenance_mode = models.BooleanField(default=False)

    def mark_activity(self):
        self.last_activity_ts = timezone.now()
        self.save()

    @property
    def rdb(self):
        return None


class IPBan(BaseModel):

    ip = models.TextField()
    reason = models.TextField(**optional)
