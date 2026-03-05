import json
from unittest.mock import patch

from django.test import override_settings

from builders.models import ItemTemplate, Loader, MobTemplate, Rule
from config import constants as adv_consts
from spawns.loading import LoaderRun, run_loaders
from spawns.models import Item
from tests.base import WorldTestCase


class TestLoaderRuntimeSafety(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()

    def test_nested_rule_target_missing_output_is_ignored(self):
        rock_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name='a bag',
            type=adv_consts.ITEM_TYPE_CONTAINER)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)

        parent_rule = Rule.objects.create(
            loader=loader,
            template=bag_template,
            target=self.room)
        nested_rule = Rule.objects.create(
            loader=loader,
            template=rock_template,
            target=parent_rule)

        nested_rule.order = 0
        nested_rule.save(update_fields=['order'])

        output = loader.run(self.spawn_world, check=False)
        self.assertEqual(output[nested_rule.id], [])
        self.assertEqual(len(output[parent_rule.id]), 1)

    def test_loader_condition_invalid_expression_marks_run_executed(self):
        self.zone.is_warzone = True
        self.zone.zone_data = json.dumps({
            'north_control': 'orc',
        })
        self.zone.save()

        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            loader_condition="this is not valid python ???")
        Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False)
        output = loader_run.execute()
        self.assertTrue(loader_run.executed)
        self.assertEqual(len(output.keys()), 0)

    def test_run_loaders_does_not_require_rdb(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        self.assertIsNone(self.spawn_world.rdb)

        output = run_loaders(world=self.spawn_world)
        self.assertEqual(len(output['rules']), 1)
        self.assertEqual(len(output['rules'][0]), 1)
        loaded = next(iter(output['rules'][0].values()))
        self.assertEqual(len(loaded), 1)

        self.spawn_world.refresh_from_db()
        self.assertIsNotNone(self.spawn_world.last_loader_run_ts)

    def test_reload_counts_from_database_without_population_data(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a stone')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=2)

        first_output = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False,
        ).execute()
        self.assertEqual(len(first_output[rule.id]), 2)
        self.assertEqual(
            Item.objects.filter(world=self.spawn_world, rule=rule).count(),
            2,
        )

    @override_settings(
        WR_AI_EVENT_FORWARD_URL="http://localhost:8071/v1/events",
        WR_AI_EVENT_TYPES="mob.spawned",
    )
    @patch("spawns.tasks.forward_event_to_ai_sidecar.delay")
    def test_loader_enqueues_sidecar_spawn_signal(self, mock_forward_delay):
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name="a sentinel",
        )
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0,
        )
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room,
            num_copies=1,
        )

        output = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False,
        ).execute()
        spawned_mob = output[rule.id][0]

        mock_forward_delay.assert_called_once()
        kwargs = mock_forward_delay.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "mob.spawned")
        self.assertEqual(kwargs["actor_key"], spawned_mob.key)
        self.assertEqual(kwargs["event_data"]["source"], "loader")
        self.assertEqual(kwargs["event_data"]["loader_id"], loader.id)
        self.assertEqual(kwargs["event_data"]["rule_id"], rule.id)
        self.assertEqual(kwargs["event_data"]["mob"]["key"], spawned_mob.key)

        # Delete one copy and ensure reload adds exactly one replacement
        first_output[rule.id][0].delete()
        self.assertEqual(
            Item.objects.filter(world=self.spawn_world, rule=rule).count(),
            1,
        )

        second_output = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=True,
        ).execute()
        self.assertEqual(len(second_output[rule.id]), 1)
        self.assertEqual(
            Item.objects.filter(world=self.spawn_world, rule=rule).count(),
            2,
        )
