from unittest import mock

import yaml

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection, transaction
from rest_framework.reverse import reverse

from backend.config.exceptions import ServiceError
from builders.instance_templates import create_instance_template
from builders.models import Faction, ItemDefinition, Trigger
from config import constants as constants
from spawns.models import Mob, Player
from spawns.services import WorldGate
from spawns.handlers import dispatch_command
from tests.test_world_config_manifests import AuthenticatedBuilderWorldTestCase
from tests.utils import capture_game_messages
from worlds.instances import enter_instance, leave_instance, route_new_character_to_initial_instance
from worlds.models import InstanceRun, OnboardingRewardGrant, PlayerOnboarding, World, WorldConfig


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
            self.assertEqual(player.onboarding.template_id, self.template.pk)
            self.assertIsNone(player.onboarding.left_at)
            run = InstanceRun.objects.get(spawned_world=player.world)
            self.assertTrue(run.single_player)
            self.assertEqual(run.owner_id, player.pk)
            self.assertEqual(player.room_id, self.template.config.starting_room_id)
            self.assertEqual(player.world.lifecycle, constants.WORLD_LIFECYCLE_NEW)
            self.assertIsNone(run.started_at)
            participant = run.participants.get(player=player)
            self.assertEqual(participant.return_runtime_world_id, self.spawn_world.pk)
            self.assertEqual(participant.transfer_from_id, self.room.pk)
            from system.models import SupportEvent
            event = SupportEvent.objects.get(kind='character.created', player_id=player.pk)
            self.assertEqual(event.base_world_id, self.world.pk)
            self.assertEqual(event.run_id, run.pk)
            self.assertEqual(SupportEvent.objects.get(
                kind='instance.reserved', player_id=player.pk,
            ).details['return_world_id'], self.spawn_world.pk)
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
            with mock.patch('spawns.handlers.state_sync.publish_events') as publish, self.captureOnCommitCallbacks(execute=True):
                events.side_effect = lambda _events: self.assertEqual(publish.call_count, 0)
                dispatch_command('state.sync', player_id=player.pk, payload={})
                dispatch_command('state.sync', player_id=player.pk, payload={})
            self.assertEqual(publish.call_count, 2)
        events.assert_called_once()
        self.assertEqual([event.type for event in events.call_args.args[0]], [
            'cmd.enter.success', 'lifecycle.player.room.enter',
        ])
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
        from system.models import SupportEvent
        self.assertFalse(SupportEvent.objects.exists())

    def test_disabling_initial_setting_only_changes_future_characters(self):
        first = self.create_character()
        response = self.client.patch(self.config_url, {'player_creation': {'instance_routes': []}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second = self.create_character('Second')
        first.refresh_from_db()
        self.assertEqual(first.world.context_id, self.template.pk)
        self.assertEqual(second.world_id, self.spawn_world.pk)

    def _login(self, player):
        return WorldGate(player, player.world).enter()

    def _expire(self, player):
        from worlds.services import WorldSmith

        WorldGate(player, player.world).exit(reason='idle_timeout')
        old_runtime_id = player.world_id
        WorldSmith(player.world).stop(reason='idle_expired')
        player.refresh_from_db()
        self.assertFalse(World.objects.filter(pk=old_runtime_id).exists())
        self.assertEqual(player.world_id, self.spawn_world.pk)
        self.assertIsNone(player.onboarding.left_at)
        return old_runtime_id

    def test_expired_onboarding_restarts_on_play_and_stays_in_base_world_lobby(self):
        player = self._login(self.create_character())
        old_runtime_id = self._expire(player)
        self.assertFalse(InstanceRun.objects.exists())
        response = self.client.get(self.chars_url)
        row = next(row for row in response.data['results'] if row['id'] == player.pk)
        self.assertEqual(row['root_world_id'], self.world.pk)
        # A route edit cannot change an existing character's selected template.
        self.world.config.player_creation = {'instance_routes': []}
        self.world.config.save(update_fields=['player_creation'])
        from spawns.tasks import enter_world

        with mock.patch('spawns.tasks.complete_job') as complete:
            enter_world(player.pk, self.world.pk, client_id='test', user_id=player.user_id)
        player.refresh_from_db()
        self.assertTrue(player.in_game)
        self.assertNotEqual(player.world_id, old_runtime_id)
        self.assertEqual(player.world.context_id, self.template.pk)
        self.assertEqual(player.room_id, self.template.config.starting_room_id)
        self.assertEqual(complete.call_args.kwargs['data']['world']['id'], player.world_id)
        self.assertEqual(InstanceRun.objects.count(), 1)
        from system.models import SupportEvent

        self.assertTrue(SupportEvent.objects.filter(
            kind='instance.reserved', player_id=player.pk, reason='onboarding_return',
        ).exists())

    def test_idle_monitor_cleans_run_but_keeps_unfinished_onboarding(self):
        from datetime import timedelta
        from django.utils import timezone
        from worlds.tasks import monitor_worlds

        player = self._login(self.create_character())
        old_runtime_id = player.world_id
        now = timezone.now()
        player.last_action_ts = now - timedelta(minutes=11)
        player.save(update_fields=['last_action_ts'])
        with mock.patch('worlds.tasks.timezone.now', return_value=now), mock.patch('worlds.tasks.notify_exit_world'):
            monitor_worlds()
        player.refresh_from_db()
        self.assertFalse(player.in_game)
        self.assertEqual(player.world_id, old_runtime_id)
        with mock.patch('worlds.tasks.timezone.now', return_value=now + timedelta(hours=3, seconds=1)):
            monitor_worlds()
        player.refresh_from_db()
        self.assertEqual(player.world_id, self.spawn_world.pk)
        self.assertFalse(World.objects.filter(pk=old_runtime_id).exists())
        self.assertIsNone(player.onboarding.left_at)
        self.assertEqual(self._login(player).world.context_id, self.template.pk)

    def test_leave_command_ends_onboarding_even_without_accepting_a_quest(self):
        player = self._login(self.create_character())
        dispatch_command('leave', player_id=player.pk, payload={})
        player.refresh_from_db()
        self.assertEqual(player.world_id, self.spawn_world.pk)
        self.assertIsNotNone(player.onboarding.left_at)
        WorldGate(player, player.world).exit()
        self.assertEqual(self._login(player).world_id, self.spawn_world.pk)

    def test_forced_leave_does_not_complete_onboarding(self):
        player = self._login(self.create_character())
        leave_instance(player=player, reason='builder_destroyed')
        player.refresh_from_db()
        WorldGate(player, player.world).exit()
        player = self._login(player)
        self.assertEqual(player.world.context_id, self.template.pk)
        self.assertIsNone(player.onboarding.left_at)

    def test_unavailable_onboarding_blocks_login_without_bypassing_or_creating_run(self):
        player = self._login(self.create_character())
        self._expire(player)
        self.template.lifecycle = constants.WORLD_STATE_ARCHIVED
        self.template.save(update_fields=['lifecycle'])
        with self.assertRaisesMessage(ServiceError, 'archived'):
            self._login(player)
        self.assertFalse(InstanceRun.objects.exists())
        player.refresh_from_db()
        self.assertFalse(player.in_game)
        self.template.delete()
        with self.assertRaisesMessage(ServiceError, 'unavailable'):
            self._login(player)
        self.assertTrue(PlayerOnboarding.objects.filter(player=player, template=None).exists())

    def test_entry_restrictions_apply_before_recreating_onboarding(self):
        player = self._login(self.create_character())
        self._expire(player)
        player.noplay = True
        player.save(update_fields=['noplay'])
        with self.assertRaisesMessage(ServiceError, 'banned'):
            self._login(player)
        self.assertFalse(InstanceRun.objects.exists())

    def test_normal_character_login_has_one_indexed_onboarding_lookup(self):
        from worlds.onboarding import prepare_onboarding_login

        self.player.world.context
        with self.assertNumQueries(1):
            self.assertIs(prepare_onboarding_login(self.player), self.player)
        self.assertFalse(PlayerOnboarding.objects.filter(player=self.player).exists())

    def _onboarding_quest(self):
        from quests.models import QuestTemplate

        gear = ItemDefinition.objects.create(world=self.world, slug='starter-spear', name='a starter spear')
        quest = QuestTemplate.objects.create(
            world=self.template, slug='introduction', name='Introduction', status='active',
            discovery_policy={'accept_if': {}},
            graph={'steps': [
                {'id': 'start', 'kind': 'storylet', 'choices': [
                    {'id': 'finish', 'text': 'Finish', 'goto': 'done'}]},
                {'id': 'done', 'kind': 'resolution', 'resolution': 'complete'},
            ]},
            reward_policy={'complete': [{'type': 'grant_item', 'item_definition': f'itemdefinition.{gear.slug}'}]},
        )
        return quest, gear

    def _complete_quest(self, player, quest):
        from quests.services.engine import accept_template, choose_for_instance

        attempt = accept_template(player, quest).quest_instance
        choose_for_instance(player, str(attempt.pk), 'finish')
        return attempt

    def test_starter_gear_survives_cleanup_and_is_not_granted_again(self):
        quest, gear = self._onboarding_quest()
        keepsake = ItemDefinition.objects.create(world=self.world, slug='keepsake', name='a keepsake')
        self.world.config.starting_equipment = [{'item_definition': f'itemdefinition.{keepsake.slug}', 'count': 1}]
        self.world.config.save(update_fields=['starting_equipment'])
        player = self._login(self.create_character())
        keepsake_id = player.inventory.get(definition=keepsake).pk
        self._complete_quest(player, quest)
        item_id = player.inventory.get(definition=gear).pk
        self._expire(player)
        player = self._login(player)
        self._complete_quest(player, quest)
        self.assertEqual(list(player.inventory.filter(definition=gear).values_list('pk', flat=True)), [item_id])
        self.assertEqual(player.inventory.get(pk=item_id).world_id, player.world_id)
        self.assertEqual(list(player.inventory.filter(definition=keepsake).values_list('pk', flat=True)), [keepsake_id])
        self.assertEqual(OnboardingRewardGrant.objects.filter(onboarding__player=player).count(), 1)
        self.assertIsNone(player.onboarding.left_at)
        # Selling/dropping/deleting the original gear cannot create a second grant.
        player.inventory.all().delete()
        self._expire(player)
        player = self._login(player)
        self._complete_quest(player, quest)
        self.assertFalse(player.inventory.filter(definition=gear).exists())
        from system.models import SupportEvent

        self.assertEqual(SupportEvent.objects.filter(kind='onboarding.rewards_skipped', player_id=player.pk).count(), 2)

    def test_failed_reward_rolls_back_receipt_and_can_be_retried(self):
        from quests.services.engine import accept_template, choose_for_instance

        quest, gear = self._onboarding_quest()
        player = self._login(self.create_character())
        attempt = accept_template(player, quest).quest_instance
        with mock.patch.object(ItemDefinition, 'spawn', side_effect=RuntimeError('reward failed')):
            with self.assertRaisesMessage(RuntimeError, 'reward failed'):
                choose_for_instance(player, str(attempt.pk), 'finish')
        self.assertFalse(OnboardingRewardGrant.objects.exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'active')
        choose_for_instance(player, str(attempt.pk), 'finish')
        self.assertEqual(player.inventory.filter(definition=gear).count(), 1)
        self.assertEqual(OnboardingRewardGrant.objects.count(), 1)

    def test_starter_rewards_are_per_character_and_normal_instances_still_reward(self):
        quest, gear = self._onboarding_quest()
        for name in ('First', 'Second'):
            player = self._login(self.create_character(name))
            self._complete_quest(player, quest)
            self.assertEqual(player.inventory.filter(definition=gear).count(), 1)
            WorldGate(player, player.world).exit()
        self.assertEqual(OnboardingRewardGrant.objects.count(), 2)
        # Entering this template as a regular adventure does not enroll a player.
        enter_instance(player=self.player, transfer_to=self.template.config.starting_room,
                       transfer_from=self.player.room)
        self.player.refresh_from_db()
        self._complete_quest(self.player, quest)
        self.assertEqual(self.player.inventory.filter(definition=gear).count(), 1)
        self.assertEqual(OnboardingRewardGrant.objects.count(), 2)

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


class InitialInstanceEntryOrderTests(TransactionTestCase):
    """Exercise real commit callbacks, including the outer instance clock lock."""

    def setUp(self):
        from spawns import triggers

        # TransactionTestCase flushes and recreates content types between tests.
        triggers._scope_content_types_cache = None
        self.addCleanup(setattr, triggers, '_scope_content_types_cache', None)
        user = get_user_model().objects.create_user('entry-order@example.com', 'p')
        self.world = World.objects.new_world(
            name='Home', author=user, config=WorldConfig.objects.create(), is_multiplayer=True,
        )
        self.runtime = self.world.create_spawn_world()
        self.template = create_instance_template(
            base_world=self.world, author=user, name='Orientation',
        )
        self.template.config.instance_single_player = True
        self.template.config.save(update_fields=['instance_single_player'])
        self.world.config.player_creation = {'instance_routes': [
            {'when': {'always': True}, 'instance': self.template.instance_slug},
        ]}
        self.world.config.save(update_fields=['player_creation'])
        self.entry_room = self.template.config.starting_room
        self.greeting = ['Tally marks cover the wall.', 'The watchman greets you.', 'Orders rise outside.']
        self.trigger = Trigger.objects.create(
            world=self.template,
            scope=constants.TRIGGER_SCOPE_ROOM,
            kind=constants.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(self.entry_room),
            target_id=self.entry_room.pk,
            event=constants.TRIGGER_EVENT_ENTER,
            steps=[{'after_seconds': 0, 'actions': [
                {'type': 'command', 'subject': 'trigger_room', 'command': f'/echo {text}'}
                for text in self.greeting
            ]}],
            gate_delay=0,
        )

    def _login(self, *, time_control=False):
        self.template.config.instance_time_control = time_control
        self.template.config.save(update_fields=['instance_time_control'])
        with transaction.atomic():
            player = Player.objects.create(
                name=f'Newcomer {time_control}', user=self.world.author,
                world=self.runtime, room=self.world.config.starting_room,
            )
            route_new_character_to_initial_instance(player)
        player.world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        player.world.save(update_fields=['lifecycle'])
        with mock.patch.object(WorldGate, 'preflight'), capture_game_messages() as messages:
            player = WorldGate(player, player.world).enter()
        self.assertFalse(any(row['message'].get('text') in self.greeting for row in messages))
        return player

    def _sync_messages(self, player, **payload):
        with capture_game_messages() as messages:
            dispatch_command('state.sync', player_id=player.pk, payload=payload, connection_id='entry-test')
        return [row['message'] for row in messages if row['player_key'] == player.key]

    def test_first_sync_announces_id_then_immediate_rp_then_room_without_replaying_on_resume(self):
        for time_control in (False, True):
            with self.subTest(time_control=time_control):
                player = self._login(time_control=time_control)
                run = InstanceRun.objects.get(spawned_world=player.world)
                messages = self._sync_messages(player)
                types = [message['type'] for message in messages]
                announcement_index = types.index('cmd.enter.success')
                snapshot_index = types.index('cmd.state.sync.success')
                self.assertEqual(types.count('cmd.enter.success'), 1)
                self.assertEqual(types.count('cmd.state.sync.success'), 1)
                self.assertEqual(messages[announcement_index]['text'], f'New Instance ID: {run.ref}')
                self.assertEqual(messages[announcement_index]['data']['world_id'], player.world_id)
                self.assertTrue(messages[announcement_index]['data']['first_entry'])
                self.assertEqual(messages[snapshot_index]['data']['instance_entry'], 'initial')
                for text in self.greeting:
                    self.assertIn(text, [message.get('text') for message in messages])
                greeting_indices = [
                    next(i for i, message in enumerate(messages) if message.get('text') == text)
                    for text in self.greeting
                ]
                ordered_indices = [announcement_index, *greeting_indices, snapshot_index]
                self.assertEqual(ordered_indices, sorted(ordered_indices))
                run.refresh_from_db()
                self.assertNotIn('initial_room_enter_pending', run.progress)

                with mock.patch.object(WorldGate, 'preflight'):
                    WorldGate(player, player.world).enter()
                resumed = self._sync_messages(player)
                self.assertEqual([message['type'] for message in resumed], ['cmd.state.sync.success'])
                self.assertEqual(resumed[0]['data']['world']['instance_ref'], run.ref)

    def test_final_snapshot_uses_room_after_entry_trigger_transfer(self):
        destination = self.entry_room.create_at('east')
        self.trigger.steps = []
        self.trigger.script = (
            f'/cmd room -- /transfer {{{{ actor_key }}}} room@{destination.relative_id}'
        )
        self.trigger.save(update_fields=['steps', 'script'])
        for time_control in (False, True):
            with self.subTest(time_control=time_control):
                player = self._login(time_control=time_control)
                messages = self._sync_messages(player)
                snapshots = [message for message in messages if message['type'] == 'cmd.state.sync.success']
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]['data']['room']['id'], destination.pk)

    def test_return_replays_only_applicable_narration_before_room_without_trigger_effects(self):
        from quests.models import QuestInstance, QuestTemplate
        from spawns.models import ScheduledTriggerRun
        from spawns.state_payloads import get_player_with_related
        from worlds.entry_messages import returning_instance_message

        introduction = '\n\n'.join(self.greeting)
        self.template.config.entry_message = introduction
        self.template.config.entry_message_conditions = {
            'all': [{'not': {'quest_active': 'intro'}}, {'not': {'quest_completed': 'intro'}}],
        }
        self.template.config.save(update_fields=['entry_message', 'entry_message_conditions'])
        quest = QuestTemplate.objects.create(world=self.template, slug='intro', name='Introduction')
        player = self._login()
        self._sync_messages(player, connection_entry=True)
        trigger_count = ScheduledTriggerRun.objects.count()

        ready_player = get_player_with_related(player.pk)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(returning_instance_message(ready_player), introduction)
        # Both quest predicates share one indexed, run-scoped lookup.
        self.assertEqual(len(queries), 1)

        resumed = self._sync_messages(player, connection_entry=True)
        self.assertEqual([message['type'] for message in resumed], [
            'cmd.enter.success', 'notification.instance.introduction', 'cmd.state.sync.success',
        ])
        self.assertEqual(resumed[1]['text'], introduction)
        self.assertEqual(resumed[0]['text'], f'Resuming Instance ID: {player.world.instance_ref}')
        self.assertFalse(resumed[0]['data']['first_entry'])
        self.assertEqual(resumed[-1]['data']['instance_entry'], 'return')
        self.assertEqual(ScheduledTriggerRun.objects.count(), trigger_count)

        for payload in ({}, {'connection_entry': True, 'reconnect': True}):
            refreshed = self._sync_messages(player, **payload)
            self.assertEqual([message['type'] for message in refreshed], ['cmd.state.sync.success'])
            self.assertEqual(refreshed[0]['data']['instance_entry'], 'sync')

        attempt = QuestInstance.objects.create(player=player, template=quest, world=player.world, status='active')
        for status, resolution in (('active', ''), ('resolved', 'complete')):
            attempt.status, attempt.resolution = status, resolution
            attempt.save(update_fields=['status', 'resolution'])
            resumed = self._sync_messages(player, connection_entry=True)
            self.assertEqual([message['type'] for message in resumed], ['cmd.enter.success', 'cmd.state.sync.success'])
            self.assertEqual(resumed[0]['text'], f'Resuming Instance ID: {player.world.instance_ref}')
            self.assertEqual(resumed[-1]['data']['instance_entry'], 'return')
        attempt.delete()
        destination = self.entry_room.create_at('east')
        Player.objects.filter(pk=player.pk).update(room=destination)
        resumed = self._sync_messages(player, connection_entry=True)
        self.assertEqual([message['type'] for message in resumed], ['cmd.enter.success', 'cmd.state.sync.success'])
        self.assertEqual(resumed[0]['text'], f'Resuming Instance ID: {player.world.instance_ref}')
        self.assertEqual(resumed[-1]['data']['room']['id'], destination.pk)
