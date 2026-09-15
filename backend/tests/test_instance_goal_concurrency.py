from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from builders.models import Faction
from spawns.combat_encounters import locked_combat
from spawns.models import Mob
from worlds.instance_goals import start_instance_goal, record_instance_mob_defeat, process_instance_mob_defeat
from worlds.instances import create_fresh_instance_run
from worlds.models import World, WorldConfig, InstanceClearRecord
from tests.test_instance_goals import PERSIAN_GOAL


class TestInstanceGoalConcurrency(TransactionTestCase):
    def test_simultaneous_kills_and_duplicate_delivery_complete_once(self):
        user = get_user_model().objects.create_user('goal-race@example.com', 'p')
        base = World.objects.new_world(name='Base', author=user, config=WorldConfig.objects.create(), is_multiplayer=True)
        template = World.objects.new_world(name='Outpost', author=user, instance_of=base, is_multiplayer=True,
                                         config=WorldConfig.objects.create(instance_goal=PERSIAN_GOAL))
        faction = Faction.objects.create(world=base, code='persian', name='Persian', type='core')
        run = create_fresh_instance_run(template, leader=None)
        rooms = [template.config.starting_room, template.config.starting_room.create_at('north')]
        mobs = [Mob.objects.create(name='Guard', world=run.spawned_world, room=room, health=10) for room in rooms]
        for mob in mobs:
            mob.faction_assignments.create(faction=faction)
        with transaction.atomic():
            start_instance_goal(run)
        barrier = Barrier(2)

        def kill(mob_id):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                with locked_combat(keys=[f'mob.{mob_id}']) as context:
                    barrier.wait(timeout=5)
                    mob = context.actors[f'mob.{mob_id}']
                    record_instance_mob_defeat(mob)
                    mob.delete()
                data = {'world_id': run.spawned_world_id, 'mob_id': mob_id}
                process_instance_mob_defeat(data)
                process_instance_mob_defeat(data)
            finally:
                close_old_connections()

        with patch('spawns.tasks.reconcile_combat_room.apply_async'), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(kill, mob.pk) for mob in mobs]
            for future in futures:
                future.result(timeout=20)
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.progress['remaining'], 0)
        self.assertEqual(InstanceClearRecord.objects.count(), 1)
