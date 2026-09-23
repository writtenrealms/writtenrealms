from unittest import mock

import yaml

from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.reverse import reverse

from backend.config.exceptions import ServiceError
from builders.instance_templates import create_instance_template
from builders.models import Faction, ItemDefinition
from config import constants as constants
from spawns.models import Mob, Player
from spawns.services import WorldGate
from spawns.handlers import dispatch_command
from tests.test_world_config_manifests import AuthenticatedBuilderWorldTestCase
from worlds.instances import enter_instance, leave_instance, route_new_character_to_initial_instance
from worlds.models import InstanceRun


class InitialInstanceTests(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.template = create_instance_template(
            base_world=self.world, author=self.user, name='Introduction',
        )
        self.template.config.instance_single_player = True
        self.template.config.save(update_fields=['instance_single_player'])
        self.routes = [{'when': {'always': True}, 'instance': self.template.instance_slug}]
        self.world.config.player_creation = {'instance_routes': self.routes}
        self.world.config.save(update_fields=['player_creation'])
        self.chars_url = reverse('lobby-world-chars', args=[self.world.pk])
        self.config_url = reverse('builder-world-config', args=[self.world.pk])
        self.manifest_url = reverse('builder-world-manifest-apply', args=[self.world.pk])

    def create_character(self, name='Newcomer', **kwargs):
        response = self.client.post(self.chars_url, {'name': name, **kwargs}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['root_world_id'], self.world.pk)
        return Player.objects.get(pk=response.data['id'])

    def test_creation_reserves_separate_private_runs_without_starting_population(self):
        with mock.patch('worlds.services.WorldSmith.start') as start:
            first = self.create_character()
            second = self.create_character('Another')
        start.assert_not_called()
        self.assertNotEqual(first.world_id, second.world_id)
        for player in (first, second):
            run = InstanceRun.objects.get(spawned_world=player.world)
            self.assertTrue(run.single_player)
            self.assertEqual(run.owner_id, player.pk)
            self.assertEqual(player.room_id, self.template.config.starting_room_id)
            self.assertEqual(player.world.lifecycle, constants.WORLD_LIFECYCLE_NEW)
            self.assertIsNone(run.started_at)
            participant = run.participants.get(player=player)
            self.assertEqual(participant.return_runtime_world_id, self.spawn_world.pk)
            self.assertEqual(participant.transfer_from_id, self.room.pk)
        listed = self.client.get(self.chars_url)
        self.assertTrue({first.pk, second.pk}.issubset({p['id'] for p in listed.data['results']}))
        duplicate = self.client.post(self.chars_url, {'name': 'Newcomer'}, format='json')
        self.assertEqual(duplicate.status_code, 400)

    def test_run_rejects_another_player_and_leaves_to_recorded_base_runtime(self):
        player = self.create_character()
        run = InstanceRun.objects.get(spawned_world=player.world)
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            enter_instance(player=self.player, transfer_to=player.room, transfer_from=self.room, ref=run.ref)
        leave_instance(player=player)
        player.refresh_from_db()
        self.assertEqual(player.world_id, self.spawn_world.pk)
        self.assertEqual(player.room_id, self.room.pk)
        self.assertIsNotNone(run.participants.get(player=player).exited_at)

    def test_lobby_world_lists_keep_introduction_characters_under_base_world(self):
        visitor = get_user_model().objects.create_user('new-player@example.com', 'password')
        self.world.is_public = True
        self.world.save(update_fields=['is_public'])
        self.client.force_authenticate(visitor)
        first = self.create_character()
        second = self.create_character('Another')
        for endpoint in ('lobby-worlds-user', 'lobby-worlds-playing'):
            response = self.client.get(reverse(endpoint))
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([world['id'] for world in response.data['results']], [self.world.pk])
            self.assertEqual(response.data['results'][0]['num_characters'], 2 if endpoint == 'lobby-worlds-user' else 3)
        response = self.client.get(reverse('chars-recent'))
        self.assertEqual({player['id'] for player in response.data['results']}, {first.pk, second.pk})
        self.assertTrue(all(player['root_world_id'] == self.world.pk for player in response.data['results']))
        response = self.client.get(reverse('lobby'))
        self.assertEqual([world['id'] for world in response.data['playing']], [self.world.pk])
        self.assertEqual(response.data['playing'][0]['num_characters'], 3)

    def test_initial_entry_events_wait_for_state_sync_and_resume_keeps_run(self):
        player = self.create_character()
        runtime_id = player.world_id
        player.world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        player.world.save(update_fields=['lifecycle'])
        with mock.patch.object(WorldGate, 'preflight'), mock.patch('worlds.instances._enqueue_instance_events') as events:
            WorldGate(player, player.world).enter()
            WorldGate(player, player.world).enter()
            events.assert_not_called()
            self.assertTrue(InstanceRun.objects.get(
                spawned_world_id=runtime_id,
            ).progress['initial_room_enter_pending'])
            with mock.patch('spawns.handlers.state_sync.publish_events') as publish:
                events.side_effect = lambda _events: self.assertGreaterEqual(publish.call_count, 1)
                dispatch_command('state.sync', player_id=player.pk, payload={})
                dispatch_command('state.sync', player_id=player.pk, payload={})
            self.assertEqual(publish.call_count, 2)
        events.assert_called_once()
        self.assertEqual(events.call_args.args[0][0].type, 'lifecycle.player.room.enter')
        player.refresh_from_db()
        self.assertEqual(player.world_id, runtime_id)
        run = InstanceRun.objects.get(spawned_world_id=runtime_id)
        self.assertIsNotNone(run.started_at)
        self.assertNotIn('initial_entry_pending', run.progress)
        self.assertNotIn('initial_room_enter_pending', run.progress)

    def test_starting_equipment_moves_into_private_run(self):
        definition = ItemDefinition.objects.create(world=self.world, slug='keepsake', name='a keepsake')
        self.world.config.starting_equipment = [{'item_definition': f'itemdefinition.{definition.slug}', 'count': 1}]
        self.world.config.save(update_fields=['starting_equipment'])
        player = self.create_character()
        self.assertEqual(player.inventory.get().world_id, player.world_id)

    def test_clear_goal_waits_for_first_login_population(self):
        self.template.config.instance_goal = {'type': 'clear_initial_mobs', 'where': True}
        self.template.config.save(update_fields=['instance_goal'])
        player = self.create_character()
        run = InstanceRun.objects.get(spawned_world=player.world)
        self.assertIsNone(run.started_at)
        self.assertFalse(run.goal_members.exists())
        Mob.objects.create(name='a guard', world=player.world, room=player.room, health=10)
        player.world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        player.world.save(update_fields=['lifecycle'])
        with mock.patch.object(WorldGate, 'preflight'):
            WorldGate(player, player.world).enter()
        run.refresh_from_db()
        self.assertIsNotNone(run.started_at)
        self.assertEqual(run.progress['remaining'], 1)
        self.assertNotIn('initial_entry_pending', run.progress)

    def test_failed_goal_start_keeps_initial_entry_retryable(self):
        self.template.config.instance_goal = {'type': 'clear_initial_mobs', 'where': True}
        self.template.config.save(update_fields=['instance_goal'])
        player = self.create_character()
        player.world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        player.world.save(update_fields=['lifecycle'])
        with mock.patch.object(WorldGate, 'preflight'), self.assertRaisesMessage(ServiceError, 'matches no living'):
            WorldGate(player, player.world).enter()
        player.refresh_from_db()
        self.assertFalse(player.in_game)
        run = InstanceRun.objects.get(spawned_world=player.world)
        self.assertTrue(run.progress['initial_entry_pending'])
        self.assertIsNone(run.started_at)

    def test_faction_starting_room_is_return_destination(self):
        room = self.create_imported_room(relative_id=9, x=9, name='Faction home')
        faction = Faction.objects.create(world=self.world, name='Settlers', code='settlers', is_core=True, type='core', starting_room=room)
        self.world.config.player_creation['core_faction'] = {'mode': 'fixed_default', 'default': faction.code}
        self.world.config.save(update_fields=['player_creation'])
        player = self.create_character()
        self.assertEqual(player.room_id, self.template.config.starting_room_id)
        self.assertEqual(player.instance_participations.get().transfer_from_id, room.pk)

    def test_failed_routing_rolls_back_character_creation(self):
        self.template.lifecycle = constants.WORLD_STATE_ARCHIVED
        self.template.save(update_fields=['lifecycle'])
        response = self.client.post(self.chars_url, {'name': 'Newcomer'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('archived', str(response.data))
        self.assertFalse(Player.objects.filter(name='Newcomer').exists())
        self.assertFalse(InstanceRun.objects.exists())

    def test_disabling_initial_setting_only_changes_future_characters(self):
        first = self.create_character()
        response = self.client.patch(self.config_url, {'player_creation': {'instance_routes': []}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second = self.create_character('Second')
        first.refresh_from_db()
        self.assertEqual(first.world.context_id, self.template.pk)
        self.assertEqual(second.world_id, self.spawn_world.pk)

    def test_routes_roundtrip_on_base_world_yaml_and_api(self):
        for endpoint in (self.config_url, self.manifest_url):
            with self.subTest(endpoint=endpoint):
                creation = {'instance_routes': self.routes}
                if endpoint == self.config_url:
                    response = self.client.patch(endpoint, {'player_creation': creation}, format='json')
                else:
                    response = self.client.post(endpoint, {'manifest': yaml.safe_dump({
                        'kind': 'world', 'spec': {'player_creation': creation},
                    })}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
        payload = self.client.get(self.config_url).data
        self.assertEqual(payload['config']['player_creation']['instance_routes'], self.routes)
        self.assertEqual(payload['manifest']['spec']['player_creation']['instance_routes'], self.routes)
        self.world.config.refresh_from_db()
        self.assertTrue(self.world.config.can_select_faction)
        template_payload = self.client.get(reverse('builder-world-config', args=[self.template.pk])).data
        self.assertNotIn('player_creation', template_payload['manifest']['spec'])
        self.assertNotIn('instance_initial', template_payload['manifest']['spec'])

    def test_selected_templates_must_remain_single_player(self):
        endpoint = reverse('builder-world-config', args=[self.template.pk])
        response = self.client.patch(endpoint, {'instance_single_player': False}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('must remain single-player', str(response.data))
        response = self.client.post(reverse('builder-world-manifest-apply', args=[self.template.pk]), {
            'manifest': 'kind: world\nspec:\n  instance_single_player: false\n',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_routes_reject_missing_shared_and_foreign_templates(self):
        from worlds.models import World, WorldConfig

        other_base = World.objects.new_world(name='Other world', author=self.user, is_multiplayer=True,
                                             config=WorldConfig.objects.create())
        foreign = create_instance_template(base_world=other_base, author=self.user, name='Foreign')
        shared = create_instance_template(base_world=self.world, author=self.user, name='Shared')
        for slug in ('missing', shared.instance_slug, foreign.instance_slug):
            with self.subTest(slug=slug):
                response = self.client.patch(self.config_url, {'player_creation': {
                    'instance_routes': [{'when': True, 'instance': slug}],
                }}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(reverse('builder-world-config', args=[self.template.pk]), {
            'player_creation': {'instance_routes': self.routes},
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_conditions_choose_faction_instance_and_first_match_wins(self):
        elves = Faction.objects.create(world=self.world, name='Elves', code='elves', type='core', playable=True)
        orcs = Faction.objects.create(world=self.world, name='Orcs', code='orcs', type='core', playable=True)
        elf_intro = create_instance_template(base_world=self.world, author=self.user, name='Elven introduction')
        elf_intro.config.instance_single_player = True
        elf_intro.config.save(update_fields=['instance_single_player'])
        routes = [
            {'when': {'all': [{'eq': ['player.core_faction', 'elves']}, {'gte': ['player.level', 1]}]},
             'instance': elf_intro.instance_slug},
            *self.routes,
        ]
        response = self.client.patch(self.config_url, {'player_creation': {
            'core_faction': {'mode': 'choose_required', 'default': elves.code, 'options': [elves.code, orcs.code]},
            'instance_routes': routes,
        }}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        elf = self.create_character('Elf', faction='elves')
        orc = self.create_character('Orc', faction='orcs')
        self.assertEqual(elf.world.context_id, elf_intro.pk)
        self.assertEqual(orc.world.context_id, self.template.pk)
        self.assertEqual(elf.core_faction_id, elves.pk)
        self.assertEqual(orc.core_faction_id, orcs.pk)

    def test_no_match_uses_normal_starting_room(self):
        response = self.client.patch(self.config_url, {'player_creation': {'instance_routes': [
            {'when': {'eq': ['player.archetype', 'mage']}, 'instance': self.template.instance_slug},
        ]}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        player = self.create_character()
        self.assertEqual(player.world_id, self.spawn_world.pk)
        self.assertEqual(player.room_id, self.room.pk)
        self.assertFalse(player.instance_participations.exists())

    def test_partial_creation_updates_preserve_routes_and_faction_policy(self):
        for endpoint in (self.config_url, self.manifest_url):
            for patch in ({'core_faction': {'mode': 'none'}}, {'instance_routes': self.routes}):
                with self.subTest(endpoint=endpoint, patch=patch):
                    if endpoint == self.config_url:
                        response = self.client.patch(endpoint, {'player_creation': patch}, format='json')
                    else:
                        response = self.client.post(endpoint, {'manifest': yaml.safe_dump({
                            'kind': 'world', 'spec': {'player_creation': patch},
                        })}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                    self.world.config.refresh_from_db()
                    self.assertEqual(self.world.config.player_creation, {
                        'core_faction': {'mode': 'none'}, 'instance_routes': self.routes,
                    })
                    self.assertFalse(self.world.config.can_select_faction)

    def test_destination_validation_batches_all_routes(self):
        from core.player_creation import normalize_instance_routes

        faction = Faction.objects.create(world=self.world, name='Elves', code='elves', type='core', playable=True)
        routes = [{'when': {'eq': ['player.core_faction', faction.code]},
                   'instance': self.template.instance_slug}] * 32
        with self.assertNumQueries(2):
            self.assertEqual(normalize_instance_routes(routes, world=self.world), routes)

    def test_null_creation_config_clears_routes_and_faction_policy(self):
        for endpoint in (self.config_url, self.manifest_url):
            with self.subTest(endpoint=endpoint):
                self.world.config.player_creation = {
                    'core_faction': {'mode': 'none'}, 'instance_routes': self.routes,
                }
                self.world.config.save(update_fields=['player_creation'])
                if endpoint == self.config_url:
                    response = self.client.patch(endpoint, {'player_creation': None}, format='json')
                else:
                    response = self.client.post(endpoint, {'manifest': 'kind: world\nspec:\n  player_creation: null\n'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.world.config.refresh_from_db()
                self.assertEqual(self.world.config.player_creation, {})

    def test_invalid_conditions_and_unbounded_rules_are_rejected(self):
        invalid_conditions = [
            {'eq': ['player.typo', 'elves']},
            {'eq': ['player.core_faction', 'missing-faction']},
            {'eq': ['player.core_faction', '{player.archetype}']},
            {'gte': ['player.level', '1']},
            {'mob_present': 'mobdefinition.guard'},
            {'always': 'yes'}, {'eq': ['player.level', 1], 'always': True},
            {'any': []}, {}, None,
        ]
        for condition in invalid_conditions:
            with self.subTest(condition=condition):
                response = self.client.post(self.manifest_url, {'manifest': yaml.safe_dump({
                    'kind': 'world', 'spec': {'player_creation': {'instance_routes': [
                        {'when': condition, 'instance': self.template.instance_slug},
                    ]}},
                })}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(self.config_url, {'player_creation': {'instance_routes': self.routes * 33}}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_unmatched_rules_and_disabled_routing_add_no_lookup_queries(self):
        from core.player_creation import select_initial_instance_slug

        rules = [{'when': {'not': {'in': ['player.gender', [self.player.gender]]}},
                  'instance': self.template.instance_slug}] * 32
        with self.assertNumQueries(0):
            self.assertIsNone(select_initial_instance_slug(self.player, routes=rules))
        base = self.player.world.context
        base.config.player_creation = {'instance_routes': []}
        with self.assertNumQueries(0):
            route_new_character_to_initial_instance(self.player)

    def test_matched_routing_fetches_one_destination_without_scanning_runs(self):
        from core.player_creation import select_initial_instance_slug

        with self.assertNumQueries(0):
            self.assertEqual(select_initial_instance_slug(self.player, routes=self.routes * 32),
                             self.template.instance_slug)
        # The remaining DB work creates one run and transfers this character only.
        base = self.player.world.context
        base.config.player_creation = {'instance_routes': self.routes}
        with CaptureQueriesContext(connection) as queries:
            route_new_character_to_initial_instance(self.player)
        destination_queries = [q['sql'] for q in queries if '"instance_slug" =' in q['sql']]
        self.assertEqual(len(destination_queries), 1)
        self.assertIn('LIMIT 1', destination_queries[0])
