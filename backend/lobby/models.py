from django.db import models

from core.db import BaseModel


class FeaturedWorld(BaseModel):

    world = models.ForeignKey('worlds.World', on_delete=models.CASCADE)
    order = models.IntegerField(default=1, unique=True, blank=False)


class DiscoverWorld(BaseModel):

    world = models.ForeignKey('worlds.World', on_delete=models.CASCADE)
    order = models.IntegerField(default=1, unique=True, blank=False)


class InDevelopmentWorld(BaseModel):

    world = models.ForeignKey('worlds.World', on_delete=models.CASCADE)
    order = models.IntegerField(default=1, unique=True, blank=False)