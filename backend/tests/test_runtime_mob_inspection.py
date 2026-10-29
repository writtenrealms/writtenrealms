import copy
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from builders.models import MobConversationPolicy, MobDefinition
from inference import service as inference
from inference.models import BudgetBucket, InferenceRequest, InferenceControl
from inference.providers import request_body
from spawns.conversations.policy import bounded_prompt, set_enabled
from spawns.communications import reset_communication_session
from spawns.models import Mob, MobConversationFocus, MobConversationAllowance, MobConversationFailure, MobState
from tests.base import WorldTestCase
from tests.test_inference import CONFIG
from tests.utils import dispatch_text_command


@override_settings(LLM_API_KEY='test-secret-not-for-responses')
class TestRuntimeMobInspection(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.definition = MobDefinition.objects.create(world=self.world, slug='philosopher', name='Menon',
            mob_type='humanoid', talkable=True, notes='Private builder note, never in a prompt.',
            conversation={'greeting': 'What makes a fair bargain?', 'knowledge': 'You cut stone.'})
        self.mob = Mob.objects.create(world=self.spawn_world, room=self.room, definition=self.definition,
            name='Menon', keywords='philosopher', type='humanoid', health=20)
        self.world.conversation_knowledge = 'The Agora is a marketplace.'
        self.world.save(update_fields=['conversation_knowledge'])
        self.policy = set_enabled(self.world.pk, True)

    def url(self, kind='list', **overrides):
        args = {'world_pk': str(self.world.pk), 'pk': str(self.spawn_world.pk), **overrides}
        if kind != 'list':
            args['mob_id'] = self.mob.pk
        return reverse({'list': 'builder-runtime-mobs', 'detail': 'builder-runtime-mob',
                        'debug': 'builder-runtime-mob-debug'}[kind], kwargs=args)

    def staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

    def test_details_are_read_only_and_separate_runtime_definition_and_state(self):
        MobState.objects.create(mob=self.mob, data={'met_visitors': 2})
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url('detail'))
        self.assertEqual(response.status_code, 200, response.data)
        mob = response.data['mob']
        self.assertEqual(mob['runtime']['id'], self.mob.pk)
        self.assertEqual(mob['definition_record']['id'], self.definition.pk)
        self.assertEqual(mob['state']['data'], {'met_visitors': 2})
        self.assertEqual(mob['conversation']['greeting'], 'What makes a fair bargain?')
        self.assertNotIn('focus', mob)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(any(q['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_list_search_pagination_and_query_count_are_bounded(self):
        Mob.objects.bulk_create([Mob(world=self.spawn_world, room=self.room, name=f'Guard {i}') for i in range(60)])
        with CaptureQueriesContext(connection) as small:
            page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.data['results']), 50)
        self.assertTrue(all(m['room']['zone_id'] == self.room.zone_id for m in page.data['results']))
        tail = self.client.get(self.url(), {'after': page.data['next_after']})
        self.assertEqual(len(tail.data['results']), 11)
        self.assertIsNone(tail.data['next_after'])
        self.assertFalse(set(m['id'] for m in page.data['results']) & set(m['id'] for m in tail.data['results']))
        Mob.objects.bulk_create([Mob(world=self.spawn_world, room=self.room, name=f'Extra {i}') for i in range(1000)])
        with CaptureQueriesContext(connection) as large:
            self.client.get(self.url())
        self.assertLessEqual(len(large), len(small))
        self.assertLessEqual(len(large), 6)
        found = self.client.get(self.url(), {'q': 'philosopher'})
        self.assertEqual([m['id'] for m in found.data['results']], [self.mob.pk])
        self.assertEqual(self.client.get(self.url(), {'after': '-1'}).status_code, 400)

    def test_permission_and_runtime_boundaries(self):
        stranger = self.create_user('stranger@example.com')
        self.client.force_authenticate(stranger)
        for kind in ('list', 'detail', 'debug'):
            self.assertEqual(self.client.get(self.url(kind)).status_code, 403)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url('debug')).status_code, 403)
        other_runtime = self.world.create_spawn_world()
        self.assertEqual(self.client.get(self.url('detail', pk=str(other_runtime.pk))).status_code, 404)
        self.assertEqual(self.client.get(self.url(pk=str(self.world.pk))).status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url()).status_code, (401, 403))

    def test_no_definition_and_disappeared_mob(self):
        self.mob.definition = None
        self.mob.save(update_fields=['definition'])
        response = self.client.get(self.url('detail'))
        self.assertIsNone(response.data['mob']['definition'])
        self.assertFalse(response.data['mob']['conversation']['configured'])
        url = self.url('detail')
        self.mob.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_latest_failure_is_staff_only_read_only_and_scoped_to_the_loaded_mob(self):
        MobConversationFailure.objects.create(mob=self.mob, reason='forbidden_speech_characters',
            response='<script>rejected</script>', details={'characters': [{'offset': 0, 'code_point': 'U+003C'}]})
        self.assertNotIn('rejected', str(self.client.get(self.url('detail')).data))
        self.assertEqual(self.client.get(self.url('debug')).status_code, 403)
        self.staff()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url('debug'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['conversation_failure']['response'], '<script>rejected</script>')
        self.assertEqual(response.data['conversation_failure']['reason'], 'forbidden_speech_characters')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(any(q['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        other_runtime = self.world.create_spawn_world()
        self.assertEqual(self.client.get(self.url('debug', pk=str(other_runtime.pk))).status_code, 404)
        MobConversationFailure.objects.all().delete()
        self.assertIsNone(self.client.get(self.url('debug')).data['conversation_failure'])

    def test_preview_matches_live_builder_without_writes_or_provider_calls(self):
        self.staff()
        inference.configure(copy.deepcopy(CONFIG))
        inference.set_enabled(True)
        speech = 'Can an agreed price still be unfair?'
        with patch('inference.providers.perform') as provider, CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url('debug'), {'speech': speech}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        payload = bounded_prompt(self.player, self.mob, self.policy,
            [{'role': 'assistant', 'content': 'What makes a fair bargain?'}], speech, CONFIG['profiles']['fast'])
        self.assertEqual(response.data['provider_body'], request_body(CONFIG['provider'], CONFIG['profiles']['fast'], payload))
        self.assertFalse(response.data['submitted'])
        self.assertFalse(response.data['stored'])
        self.assertEqual(response.data['context'], 'new_conversation')
        provider.assert_not_called()
        self.assertFalse(any(q['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        for model in (InferenceRequest, BudgetBucket, MobConversationFocus, MobConversationAllowance):
            self.assertFalse(model.objects.exists())
        self.assertNotIn('test-secret-not-for-responses', str(response.data))
        self.assertNotIn('Private builder note', str(response.data))
        self.assertEqual(self.client.post(self.url('debug'), {'speech': 'x' * 281}, format='json').status_code, 400)

    def test_unconfigured_integration_and_world_settings_can_still_preview_authored_data(self):
        self.staff()
        response = self.client.post(self.url('debug'), {'speech': 'Hello'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['provider_body'])
        self.assertIn('Configure', response.data['unavailable_reason'])
        self.assertEqual(response.data['knowledge_packet']['speaker']['approved_knowledge'], 'You cut stone.')
        MobConversationPolicy.objects.all().delete()
        response = self.client.post(self.url('debug'), {'speech': 'Hello'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['world_enabled'])
        self.assertEqual(response.data['knowledge_packet']['common_local_knowledge'], self.world.conversation_knowledge)
        self.definition.conversation = None
        self.definition.save(update_fields=['conversation'])
        self.assertEqual(self.client.post(self.url('debug'), {'speech': 'Hello'}, format='json').status_code, 400)

    def test_active_transcript_and_stale_focus_fallback(self):
        self.staff()
        inference.configure(copy.deepcopy(CONFIG))
        inference.set_enabled(True)
        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        reset_communication_session(self.spawn_world.pk)
        dispatch_text_command(self.player.pk, 'talk menon')
        focus = MobConversationFocus.objects.get(mob=self.mob)
        transcript = [{'role': 'user', 'content': 'Is courage a virtue?'},
                      {'role': 'assistant', 'content': 'What would you risk?'}]
        focus.transcript = transcript
        focus.save(update_fields=['transcript'])
        for use_active in (True, False):
            response = self.client.post(self.url('debug'), {'speech': 'Everything.', 'use_active_conversation': use_active}, format='json')
            self.assertEqual(response.data['context'], 'active_conversation' if use_active else 'new_conversation')
            self.assertEqual(response.data['messages'][-2]['content'], 'What would you risk?' if use_active else 'What makes a fair bargain?')
        focus.refresh_from_db()
        self.assertEqual(focus.transcript, transcript)
        # Changing location and returning still invalidates a focus, as in gameplay.
        self.player.location_sequence += 2
        self.player.save(update_fields=['location_sequence'])
        response = self.client.post(self.url('debug'), {'speech': 'Everything.'}, format='json')
        self.assertEqual(response.data['context'], 'new_conversation')
        self.player.location_sequence = focus.player_location_sequence
        self.player.save(update_fields=['location_sequence'])
        focus.expires_at = timezone.now() - timedelta(seconds=1)
        focus.save(update_fields=['expires_at'])
        self.assertEqual(self.client.post(self.url('debug'), {'speech': 'Everything.'}, format='json').data['context'], 'new_conversation')

    def test_preview_disabled_integration_and_input_trimming(self):
        self.staff()
        inference.configure(copy.deepcopy(CONFIG))  # Configuration is sufficient; no spending enabled.
        control = InferenceControl.objects.get(pk=1)
        profile = control.configuration['profiles']['fast']
        profile['max_input_tokens'] = 4096  # Even the mandatory packet cannot fit.
        control.save(update_fields=['configuration'])
        response = self.client.post(self.url('debug'), {'speech': 'Hello'}, format='json')
        self.assertFalse(response.data['integration_enabled'])
        self.assertIsNone(response.data['provider_body'])
        self.assertEqual(response.data['unavailable_reason'], 'input_too_large')
        # Older transcript pairs are dropped without changing the caller's transcript.
        profile['max_input_tokens'] = 8000
        transcript = [{'role': role, 'content': 'x' * 1000} for role in ['user', 'assistant'] * 3]
        original = copy.deepcopy(transcript)
        payload = bounded_prompt(self.player, self.mob, self.policy, transcript, 'Hello', profile)
        self.assertLess(len(payload['messages']), 8)
        self.assertEqual(payload['messages'][-1]['content'], 'Hello')
        self.assertEqual(transcript, original)
