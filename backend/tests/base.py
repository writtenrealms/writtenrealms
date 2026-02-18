from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework.test import APITestCase

from spawns.models import Player, Mob
from worlds.models import World, Zone, Room, WorldConfig

User = get_user_model()


class WorldTestCase(APITestCase):

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('joe@example.com', 'p')
        self.world_config = WorldConfig.objects.create()
        self.world = World.objects.new_world(
            name='An Island',
            author=self.user,
            config=self.world_config)
        self.spawn_world = self.world.create_spawn_world()
        self.zone = self.world.zones.all()[0]
        self.room = self.zone.rooms.all()[0]
        self.player = self.create_player('Joe')

    def make_system_user(self):
        self.user.email = settings.SYSTEM_USER_EMAIL
        self.user.save()

    def create_user(self, email, is_staff=False):
        user = User.objects.create_user(email, 'p')
        if is_staff:
            user.is_staff = True
            user.save()
        return user

    def create_player(self, name, user=None, world=None, room=None):
        user = user or self.user
        world = world or self.spawn_world
        room = room or self.room
        return Player.objects.create(name=name, room=room, user=user, world=world)

    def create_instance(self):
        super().setUp()
        self.instance_config = WorldConfig.objects.create()
        self.instance_context = World.objects.new_world(
            name='An Island Instance',
            author=self.user,
            config=self.instance_config,
            instance_of=self.world)
        self.instance_room = self.instance_context.rooms.all().first()
        #self.instance_spawn_world = self.instance_context.create_spawn_world()
        return self.instance_context

    def create_mob(self, name, **kwargs):
        return Mob.objects.create(
            name=name,
            world=self.world,
            room=self.room,
            **kwargs)
