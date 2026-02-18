import os

from django.db import models
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
    maintenance_mode = models.BooleanField(default=False)
    platform_policy = models.JSONField(default=dict, blank=True)


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
