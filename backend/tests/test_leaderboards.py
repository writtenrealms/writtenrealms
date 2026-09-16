from datetime import timedelta
from unittest.mock import patch
import uuid
import time

import yaml
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.reverse import reverse

from builders.manifests import apply_world_config_manifest, parse_world_config_manifest, world_config_to_manifest
from builders.serializers import WorldConfigSerializer
from core.leaderboards import normalize_leaderboards
from lobby.leaderboards import LeaderboardsRefreshing, world_leaderboard_panels
from spawns.models import DuelMatch, DuelParticipant
from tests.base import WorldTestCase
from tests.test_instance_goals import PERSIAN_GOAL
from worlds.models import InstanceClearRecord, World, WorldConfig


class TestLeaderboards(WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.template = World.objects.new_world(
            name='A Persian Outpost', instance_slug='a-persian-outpost', author=self.user,
            instance_of=self.world, is_multiplayer=True,
            config=WorldConfig.objects.create(instance_goal=PERSIAN_GOAL, instance_single_player=True, instance_time_control=True),
        )
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('lobby-world-leaderboards', args=[self.world.pk])

    def configure(self, panels):
        parsed = parse_world_config_manifest(world=self.world, manifest={'kind': 'world', 'spec': {'leaderboards': panels}})
        apply_world_config_manifest(parsed)
        self.world.config.refresh_from_db()

    def clear(self, player=None, milliseconds=1000, **overrides):
        player = player or self.player
        values = dict(
            template_world=self.template, template_name=self.template.name, template_slug=self.template.instance_slug,
            attempt_id=uuid.uuid4(), started_at=timezone.now() - timedelta(seconds=30), completed_at=timezone.now(),
            clear_time_ms=milliseconds, participants=[{'player_id': player.pk, 'name': player.name}],
            ranking_player=player, ranking_eligible=True, single_player=True, time_control=True,
        )
        values.update(overrides)
        return InstanceClearRecord.objects.create(**values)

    def duels(self, player, wins, losses=0, **overrides):
        for won in [True] * wins + [False] * losses:
            values = dict(base_world=self.world, template_world=self.template,
                          expires_at=timezone.now(), status='completed', completed_at=timezone.now())
            values.update(overrides)
            match = DuelMatch.objects.create(**values)
            DuelParticipant.objects.create(match=match, player=player, result='won' if won else 'lost')

    def panels(self):
        return world_leaderboard_panels(self.world)

    def test_manifest_config_endpoint_and_export_preserve_panel_order_and_options(self):
        panels = [{'type': 'instance_clear_time', 'instance': self.template.instance_slug, 'title': 'Outpost', 'limit': 5},
                  {'type': 'dueling', 'minimum_matches': 3}]
        response = self.client.post(reverse('builder-world-manifest-apply', args=[self.world.pk]),
                                    {'manifest': yaml.safe_dump({'kind': 'world', 'spec': {'leaderboards': panels}})}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.leaderboards, panels)
        self.assertEqual(world_config_to_manifest(world=self.world)['spec']['leaderboards'], panels)
        response = self.client.get(reverse('builder-world-config', args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('leaderboards', str(response.data))
        payload = self.client.get(self.endpoint).data['panels']
        self.assertEqual([p['type'] for p in payload], ['instance_clear_time', 'dueling'])
        self.assertEqual(payload[0]['title'], 'Outpost')

    def test_default_glory_ranking_includes_characters_visiting_instances(self):
        other = self.create_player('Other')
        other.glory = self.player.glory = 5
        other.experience, self.player.experience = 200, 100
        other.save()
        self.player.world = self.template.create_spawn_world()
        self.player.save()
        builder = self.create_player('Builder')
        builder.is_builder, builder.glory = True, 1000
        builder.save()
        self.assertEqual([r['id'] for r in self.panels()[0]['entries']], [other.pk, self.player.pk])
        self.configure([])
        self.assertEqual(self.client.get(self.endpoint).data, {'panels': []})

    def test_solo_personal_bests_are_scoped_ranked_deduplicated_and_sorted(self):
        other = self.create_player('Other')
        self.clear(milliseconds=8000)
        self.clear(milliseconds=2000)
        self.clear(other, 1000)
        self.clear(milliseconds=1, time_control=False)
        self.clear(milliseconds=2, ranking_eligible=False)
        self.clear(milliseconds=3, single_player=False)
        self.clear(milliseconds=4, ranking_player=None)
        outsider = World.objects.new_world(name='Elsewhere', author=self.user)
        other_template = World.objects.new_world(name='Other instance', author=self.user, instance_of=outsider)
        self.clear(milliseconds=5, template_world=other_template)
        self.configure([{'type': 'instance_clear_time', 'instance': self.template.instance_slug}])
        entries = self.panels()[0]['entries']
        self.assertEqual([(r['id'], r['clear_time_ms']) for r in entries], [(other.pk, 1000), (self.player.pk, 2000)])
        self.configure([{'type': 'instance_clear_time', 'instance': self.template.instance_slug, 'timing': 'continuous'}])
        self.assertEqual(self.panels()[0]['entries'][0]['clear_time_ms'], 1)

    def test_group_runs_show_party_results_and_ignore_solo_clears(self):
        self.template.config.instance_single_player = False
        self.template.config.instance_time_control = False
        self.template.config.save()
        self.clear(milliseconds=1)
        self.clear(milliseconds=2000, single_player=False, time_control=False, ranking_player=None,
                   participants=[{'name': 'Joe'}, {'name': 'Ally'}])
        self.configure([{'type': 'instance_clear_time', 'instance': self.template.instance_slug}])
        panel = self.panels()[0]
        self.assertIn('Party clears', panel['description'])
        self.assertEqual([r['name'] for r in panel['entries']], ['Joe, Ally'])

    def test_duels_use_three_completed_matches_win_percentage_then_wins(self):
        more = self.create_player('More')
        too_few = self.create_player('TooFew')
        lower = self.create_player('Lower')
        builder = self.create_player('Builder')
        builder.is_builder = True
        builder.save()
        self.duels(self.player, 3)
        self.duels(more, 4)
        self.duels(too_few, 2)
        self.duels(too_few, 1, status='cancelled')
        self.duels(lower, 2, 1)
        self.duels(builder, 5)
        outside = World.objects.new_world(name='Outside', author=self.user)
        self.duels(too_few, 2, base_world=outside)
        self.configure([{'type': 'dueling'}])
        rows = self.panels()[0]['entries']
        self.assertEqual([r['id'] for r in rows], [more.pk, self.player.pk, lower.pk])
        self.assertAlmostEqual(rows[-1]['win_percentage'], 200 / 3)
        self.assertEqual((rows[-1]['wins'], rows[-1]['losses']), (2, 1))

    def test_validation_rejects_ambiguous_or_invalid_settings_with_panel_location(self):
        invalid = [None, {}, 'dueling', [{'type': 'influence'}], [{'type': 'dueling', 'minimum_matches': True}],
                   [{'type': 'dueling', 'minimum_matches': 0}], [{'type': 'glory_experience', 'limit': 51}],
                   [{'type': 'dueling', 'title': ''}], [{'type': 'dueling', 'sort': 'wins'}],
                   [{'type': 'instance_clear_time', 'instance': 'missing'}],
                   [{'type': 'instance_clear_time', 'instance': self.template.instance_slug, 'timing': 'all'}],
                   [{'type': 'dueling'}] * 9]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'leaderboards'):
                    normalize_leaderboards(value, world=self.world)
                serializer = WorldConfigSerializer(self.world.config, data={'leaderboards': value}, partial=True, context={'world': self.world})
                self.assertFalse(serializer.is_valid())
        with self.assertRaisesRegex(ValueError, 'base world'):
            normalize_leaderboards([], world=self.template)
        self.template.config.instance_goal = {}
        self.template.config.save()
        with self.assertRaisesRegex(ValueError, 'completion criteria'):
            normalize_leaderboards([{'type': 'instance_clear_time', 'instance': self.template.instance_slug}], world=self.world)

    def test_private_world_permissions_apply_even_when_rankings_are_cached(self):
        self.panels()
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.endpoint).status_code, (401, 403))

    def test_missing_instance_after_configuration_is_an_empty_panel_not_a_server_error(self):
        self.configure([{'type': 'instance_clear_time', 'instance': self.template.instance_slug}])
        self.template.delete()
        panel = self.panels()[0]
        self.assertEqual(panel['entries'], [])
        self.assertIn('unavailable', panel['empty_message'])

    def test_cached_reads_do_not_aggregate_history_and_refresh_is_shared(self):
        self.configure([{'type': 'dueling'}])
        self.duels(self.player, 3)
        first = self.panels()
        with self.assertNumQueries(0):
            self.assertEqual(self.panels(), first)
        # Concurrent refreshes serve the existing snapshot rather than each
        # recomputing it. Cold requests signal a retry instead of a fake empty.
        with patch('lobby.leaderboards.time.time', return_value=time.time() + 31), patch('lobby.leaderboards.cache.add', return_value=False):
            with self.assertNumQueries(0):
                self.assertEqual(self.panels(), first)
        cache.clear()
        with patch('lobby.leaderboards.cache.add', return_value=False), self.assertRaises(LeaderboardsRefreshing):
            self.panels()

    def test_history_size_does_not_increase_query_count_or_response_size(self):
        self.configure([{'type': 'instance_clear_time', 'instance': self.template.instance_slug, 'limit': 1}])
        self.clear(milliseconds=1000)
        with CaptureQueriesContext(connection) as queries:
            first = self.panels()
        cold_query_count = len(queries)
        seed = InstanceClearRecord.objects.first()
        rows = []
        for i in range(5000):
            rows.append(InstanceClearRecord(
                template_world=self.template, template_name='Outpost', template_slug=self.template.instance_slug,
                attempt_id=uuid.uuid4(), started_at=seed.started_at, completed_at=seed.completed_at,
                clear_time_ms=2000 + i, ranking_eligible=True, ranking_player=self.player,
                time_control=True, single_player=True,
            ))
        InstanceClearRecord.objects.bulk_create(rows, batch_size=500)
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.panels(), first)
        self.assertEqual(len(queries), cold_query_count)
        self.assertLessEqual(cold_query_count, 3)
        with CaptureQueriesContext(connection) as warm:
            self.panels()
        self.assertEqual(len(warm), 1)  # Template config only, no result history.
