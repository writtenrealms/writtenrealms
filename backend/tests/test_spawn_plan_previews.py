from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from builders.models import ItemDefinition, MobDefinition, Path, SpawnEntry, SpawnPlan
from tests.base import WorldTestCase
from worlds.models import World


class TestSpawnPlanPreviews(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.mob = MobDefinition.objects.create(
            world=self.world, slug='spearman', name='a spearman',
            description='A soldier holds an ash spear.',
            base_properties={'level': 5, 'health_max': 80},
            attributes={'strength': 12},
        )
        self.plan = SpawnPlan.objects.create(world=self.world, zone=self.zone, slug='field')
        self.endpoint = reverse('builder-zone-spawn-plan-detail', args=[self.world.id, self.zone.id, self.plan.id])

    def entry(self, slug, **kwargs):
        values = {'source': f'mobdefinition.{self.mob.slug}', 'count': 2, 'target_room': self.room}
        values.update(kwargs)
        return SpawnEntry.objects.create(plan=self.plan, slug=slug, **values)

    def preview(self):
        response = self.client.get(self.endpoint, {'preview': 'true'})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_details_include_mobs_counts_pool_weights_and_all_targets(self):
        first = self.entry('direct')
        item = ItemDefinition.objects.create(world=self.world, slug='spear', name='a spear')
        path = Path.objects.create(world=self.world, zone=self.zone, name='Patrol')
        self.entry('patrol', target_room=None, target_path=path, count={'min': 2, 'max': 4}, source={'pool': [
            {'ref': f'mob_definition.{self.mob.id}', 'weight': 70},
            {'source': f'itemdefinition.{item.slug}', 'weight': 30},
        ]})
        self.entry('zone', target_room=None, target_zone=self.zone, is_active=False)
        self.entry('follower', target_room=None, target_entry=first, conditions={'all': []})

        data = self.preview()
        entries = {entry['slug']: entry for entry in data['entries']}
        self.assertEqual(entries['direct']['sources'][0]['name'], 'a spearman')
        self.assertEqual(entries['direct']['sources'][0]['mob_id'], self.mob.id)
        self.assertEqual(entries['direct']['target'], f'room@{self.room.relative_id}')
        self.assertEqual(entries['patrol']['target'], f'path@{path.relative_id}')
        self.assertEqual(entries['patrol']['count'], {'min': 2, 'max': 4})
        self.assertTrue(entries['patrol']['pooled'])
        self.assertEqual([source['weight'] for source in entries['patrol']['sources']], [70, 30])
        self.assertEqual(entries['patrol']['sources'][1]['name'], 'a spear')
        self.assertEqual(entries['zone']['target'], f'zone@{self.zone.relative_id}')
        self.assertFalse(entries['zone']['is_active'])
        self.assertEqual(entries['follower']['target'], 'entry.direct')
        self.assertTrue(entries['follower']['conditional'])
        self.assertEqual(len(data['mobs']), 1)
        self.assertEqual(data['mobs'][self.mob.id]['description'], self.mob.description)
        self.assertEqual(data['mobs'][self.mob.id]['attributes'], {'strength': 12})
        self.assertNotIn('yaml', data)

    def test_query_count_does_not_grow_with_entries_or_mobs(self):
        self.entry('first')
        with CaptureQueriesContext(connection) as first_queries:
            self.preview()
        for index in range(30):
            mob = MobDefinition.objects.create(world=self.world, slug=f'mob-{index}', name=f'Mob {index}')
            self.entry(f'entry-{index}', source=f'mobdefinition.{mob.slug}')
        with CaptureQueriesContext(connection) as many_queries:
            data = self.preview()
        self.assertEqual(len(data['entries']), 31)
        self.assertEqual(len(data['mobs']), 31)
        self.assertEqual(len(many_queries), len(first_queries))

    def test_instance_uses_base_world_definitions(self):
        instance = World.objects.new_world(name='Instance', author=self.user, instance_of=self.world)
        zone = instance.zones.get()
        plan = SpawnPlan.objects.create(world=instance, zone=zone, slug='instance-plan')
        SpawnEntry.objects.create(plan=plan, slug='guard', source=f'mobdefinition.{self.mob.slug}', target_zone=zone)
        endpoint = reverse('builder-zone-spawn-plan-detail', args=[instance.id, zone.id, plan.id])
        response = self.client.get(endpoint, {'preview': 'true'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['entries'][0]['sources'][0]['mob_id'], self.mob.id)

    def test_unresolved_sources_are_visible_without_leaking_other_worlds(self):
        other_world = World.objects.new_world(name='Other world', author=self.user)
        foreign = MobDefinition.objects.create(world=other_world, slug='private', name='Private mob')
        self.entry('missing', source=f'mobdefinition.{foreign.id}')
        data = self.preview()
        self.assertEqual(data['mobs'], {})
        self.assertEqual(data['entries'][0]['sources'], [{'ref': f'mobdefinition.{foreign.id}', 'weight': 1}])
        wrong_route = reverse('builder-zone-spawn-plan-detail', args=[other_world.id, other_world.zones.get().id, self.plan.id])
        self.assertEqual(self.client.get(wrong_route, {'preview': 'true'}).status_code, 404)

    def test_empty_plan_and_builder_permission(self):
        self.assertEqual(self.preview(), {'entries': [], 'mobs': {}})
        self.client.force_authenticate(self.create_user('visitor@example.com'))
        self.assertEqual(self.client.get(self.endpoint, {'preview': 'true'}).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.endpoint, {'preview': 'true'}).status_code, (401, 403))
