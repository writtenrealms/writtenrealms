from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from builders.instance_templates import create_instance_template
from spawns.models import Player
from system.models import SupportEvent
from worlds.models import InstanceRun, PlayerOnboarding, World, WorldConfig
from worlds.onboarding import prepare_onboarding_login


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks.')
class OnboardingConcurrencyTests(TransactionTestCase):
    def test_simultaneous_logins_reserve_only_one_replacement_run(self):
        user = get_user_model().objects.create_user('onboarding@example.com', 'p')
        base = World.objects.new_world(
            name='Home', author=user, config=WorldConfig.objects.create(), is_multiplayer=True,
        )
        runtime = base.create_spawn_world()
        template = create_instance_template(base_world=base, author=user, name='Introduction')
        template.config.instance_single_player = True
        template.config.save(update_fields=['instance_single_player'])
        player = Player.objects.create(
            name='Newcomer', user=user, world=runtime, room=base.config.starting_room,
        )
        PlayerOnboarding.objects.create(player=player, template=template)
        barrier = Barrier(2)

        def login():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '15s'")
                stale_player = Player.objects.select_related('world__context').get(pk=player.pk)
                barrier.wait(timeout=5)
                return prepare_onboarding_login(stale_player).world_id
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(login) for _ in range(2)]
            world_ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(world_ids[0], world_ids[1])
        run = InstanceRun.objects.get()
        self.assertEqual(run.spawned_world_id, world_ids[0])
        self.assertEqual(run.participants.filter(player=player, exited_at=None).count(), 1)
        self.assertEqual(SupportEvent.objects.filter(kind='instance.reserved').count(), 1)
