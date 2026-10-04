from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pydantic import ValidationError
from rest_framework.exceptions import ValidationError as ManifestError

from quests.manifests import QuestInteractionSpec, apply_quest_manifest, parse_quest_manifest, quest_template_to_manifest
from quests.models import QuestInstance
from quests.services.engine import accept_template, abandon_instance, choose_for_instance, serialize_instance, QuestRuntimeError
from quests.services.room_interactions import start_room_interaction, room_interaction_labels, process_due_interactions
from spawns.actions.movement import ChangeRoomAction
from spawns.wallet import balance_map
from tests.test_quest_runtime import QuestRuntimeTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import Room


class TestQuestRoomInteractions(QuestRuntimeTestCase):
    def setUp(self):
        super().setUp()
        self.room = self.player.room
        self.other_room = Room.objects.create(world=self.world, zone=self.room.zone, name='Next door', x=98, y=98, z=0)
        self.now = timezone.now()
        self.spec = {
            'command': 'wash clay', 'room': f'room@{self.room.relative_id}',
            'conditions': {}, 'goto': 'resolved',
            'beats': [{'after_seconds': 0, 'text': 'You start washing.'},
                      {'after_seconds': 5, 'text': 'The clay softens.'},
                      {'after_seconds': 5, 'text': 'The clay is clean.'}],
        }
        self.template = self._quest('wash-clay')

    def _quest(self, slug):
        return apply_quest_manifest(parse_quest_manifest(world=self.world, manifest={
            'kind': 'quest', 'metadata': {'slug': slug}, 'spec': {
                'status': 'active', 'repeatability': {'mode': 'always'},
                'steps': [{'id': 'wash', 'kind': 'interaction', 'interaction': deepcopy(self.spec)},
                          {'id': 'resolved', 'kind': 'resolution', 'resolution': 'complete'}],
                'rewards': {'complete': [{'type': 'grant_currency', 'currency': 'obol', 'amount': 25}]},
            },
        }))

    def _accept(self, player=None):
        return accept_template(player or self.player, self.template).quest_instance

    def _start(self, player=None):
        with patch('django.utils.timezone.now', return_value=self.now):
            return start_room_interaction(player or self.player, 'wash clay')

    def _move(self, room):
        ChangeRoomAction().execute(self.player, room.pk)
        self.player.save(update_fields=['room', 'location_sequence', 'follow_move_sequence'])

    def test_room_command_narration_timing_reward_and_retries(self):
        self.assertEqual(room_interaction_labels(self.player), [])
        instance = self._accept()
        self.assertEqual(room_interaction_labels(self.player), ['wash clay'])
        started = self._start()
        self.assertEqual(started.events[0].text, 'You start washing.')
        self.assertEqual(started.events[-1].data['room_key'], f'room.{self.room.relative_id}')
        self.assertEqual(room_interaction_labels(self.player), [])
        with self.assertRaisesMessage(QuestRuntimeError, 'already working'):
            self._start()
        with self.assertRaises(QuestRuntimeError):
            choose_for_instance(self.player, str(instance.pk), 'wash')
        self.assertEqual(process_due_interactions(now=self.now + timedelta(seconds=4))['processed'], 0)
        self.assertEqual(process_due_interactions(now=self.now + timedelta(seconds=5))['processed'], 1)
        instance.refresh_from_db()
        self.assertEqual(instance.current_step_id, 'wash')
        self.assertEqual(instance.interaction_state['index'], 2)
        self.assertEqual(process_due_interactions(now=self.now + timedelta(seconds=10))['processed'], 1)
        self.assertEqual(process_due_interactions(now=self.now + timedelta(seconds=10))['processed'], 0)
        instance.refresh_from_db()
        self.assertEqual(instance.status, 'resolved')
        self.assertEqual(instance.interaction_state, {})
        self.assertIsNone(instance.interaction_due_at)
        self.assertEqual(balance_map(self.player)['obol'], 25)

    def test_text_dispatch_starts_same_interaction_as_room_button(self):
        self._accept()
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.pk, 'WASH   CLAY')
        self.assertTrue(any(m['message'].get('text') == 'You start washing.' for m in messages), messages)
        self.assertTrue(QuestInstance.objects.filter(player=self.player, interaction_due_at__isnull=False).exists())

    def test_info_exposes_work_command_without_extra_queries(self):
        instance = self._accept()
        # The log batch loader preloads these relations; action metadata must
        # not introduce a per-quest eligibility or room lookup.
        instance._serialization_objective_states = []
        instance._serialization_latest_journal_entries = []
        with self.assertNumQueries(0):
            payload = serialize_instance(instance, player=self.player)
        self.assertEqual(payload['current_step']['room_action'], {
            'command': 'wash clay', 'room_key': f'room.{self.room.relative_id}',
            'world_id': self.player.world_id,
        })
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.pk, 'quest info wash-clay')
        info = next(m['message'] for m in messages if m['message']['type'] == 'cmd.quest.success')
        self.assertEqual(info['data']['quest']['current_step']['room_action'], payload['current_step']['room_action'])

    def test_resolved_and_noninteraction_steps_do_not_offer_work(self):
        instance = self._accept()
        instance.status = 'resolved'
        self.assertIsNone(serialize_instance(instance, player=self.player)['current_step']['room_action'])
        instance.status = 'active'
        instance.current_step_id = 'resolved'
        self.assertIsNone(serialize_instance(instance, player=self.player)['current_step']['room_action'])

    def test_removing_the_workers_room_cancels_pending_work(self):
        instance = self._accept()
        self._start()
        self.player.room = None
        self.player.save(update_fields=['room'])
        self.assertEqual(process_due_interactions(now=self.now + timedelta(seconds=5))['processed'], 1)
        instance.refresh_from_db()
        self.assertIsNone(instance.interaction_due_at)
        self.assertEqual(instance.current_step_id, 'wash')

    def test_wrong_room_stale_button_and_leaving_then_returning(self):
        instance = self._accept()
        self._move(self.other_room)
        self.assertEqual(room_interaction_labels(self.player), [])
        with self.assertRaisesMessage(QuestRuntimeError, 'cannot do'):
            self._start()
        self._move(self.room)
        self._start()
        self._move(self.other_room)
        self._move(self.room)
        self.assertEqual(room_interaction_labels(self.player), ['wash clay'])
        process_due_interactions(now=self.now + timedelta(seconds=20))
        instance.refresh_from_db()
        self.assertEqual(instance.current_step_id, 'wash')
        self.assertEqual(instance.interaction_state, {})
        self.assertEqual(balance_map(self.player).get('obol', 0), 0)

    def test_abandon_and_new_attempt_do_not_receive_old_completion(self):
        old = self._accept()
        self._start()
        abandon_instance(self.player, str(old.pk))
        new = self._accept()
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(process_due_interactions(now=self.now + timedelta(days=1))['processed'], 0)
        new.refresh_from_db()
        self.assertEqual(new.current_step_id, 'wash')
        self.assertEqual(balance_map(self.player).get('obol', 0), 0)

    def test_players_work_independently_and_delayed_worker_preserves_spacing(self):
        other = self.create_player('Other worker')
        other.room = self.room
        other.save(update_fields=['room'])
        self._accept()
        self._accept(other)
        self._start()
        self._start(other)
        delayed = self.now + timedelta(minutes=1)
        self.assertEqual(process_due_interactions(now=delayed)['processed'], 2)
        self.assertEqual(process_due_interactions(now=delayed)['processed'], 0)
        self.assertEqual(process_due_interactions(now=delayed + timedelta(seconds=5))['processed'], 2)
        self.assertEqual(balance_map(self.player)['obol'], 25)
        self.assertEqual(balance_map(other)['obol'], 25)

    def test_editing_running_step_cancels_old_snapshot(self):
        instance = self._accept()
        self._start()
        self.template.graph['steps'][0]['interaction']['beats'][1]['text'] = 'Changed.'
        self.template.save(update_fields=['graph'])
        process_due_interactions(now=self.now + timedelta(seconds=5))
        instance.refresh_from_db()
        self.assertEqual(instance.interaction_state, {})
        self.assertEqual(instance.current_step_id, 'wash')

    def test_projection_batches_templates_and_ambiguous_commands_are_rejected(self):
        for number in range(25):
            template = self._quest(f'job-{number}')
            QuestInstance.objects.create(world=self.player.world, template=template, player=self.player,
                                         current_step_id='wash', status='active')
        # Context is normally preloaded by command dispatch.
        self.player.room
        self.player.world
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(room_interaction_labels(self.player), ['wash clay'])
        self.assertEqual(len(queries), 1)
        with self.assertRaisesMessage(QuestRuntimeError, 'More than one quest'):
            self._start()

    def test_due_work_is_bounded_and_restart_uses_persisted_cursor(self):
        self._accept()
        self._start()
        other = self.create_player('Second worker')
        other.room = self.room
        other.save(update_fields=['room'])
        self._accept(other)
        self._start(other)
        self.assertEqual(process_due_interactions(limit=1, now=self.now + timedelta(seconds=5))['processed'], 1)
        self.assertEqual(process_due_interactions(limit=1, now=self.now + timedelta(seconds=5))['processed'], 1)
        self.assertEqual(process_due_interactions(limit=1, now=self.now + timedelta(seconds=5))['processed'], 0)

    def test_interruption_keeps_previously_completed_steps(self):
        graph = self.template.graph
        second = deepcopy(graph['steps'][0])
        second['id'] = 'strain'
        second['interaction']['command'] = 'strain clay'
        graph['steps'][0]['interaction']['goto'] = 'strain'
        graph['steps'].insert(1, second)
        self.template.save(update_fields=['graph'])
        instance = self._accept()
        self._start()
        process_due_interactions(now=self.now + timedelta(seconds=5))
        process_due_interactions(now=self.now + timedelta(seconds=10))
        self.assertEqual(room_interaction_labels(self.player), ['strain clay'])
        start_room_interaction(self.player, 'strain clay')
        self._move(self.other_room)
        process_due_interactions(now=timezone.now() + timedelta(minutes=1))
        instance.refresh_from_db()
        self.assertEqual(instance.current_step_id, 'strain')
        self._move(self.room)
        self.assertEqual(room_interaction_labels(self.player), ['strain clay'])

    def test_manifest_roundtrip_and_invalid_interactions(self):
        exported = quest_template_to_manifest(self.template)
        parsed = parse_quest_manifest(world=self.world, manifest=exported)
        self.assertEqual(parsed.graph['steps'][0]['interaction'], self.spec)
        for patch_values in ({'command': 'look'}, {'command': '/grantitem'}, {'room': 'room.1'},
                             {'conditions': {'made_up_test': True}}, {'unknown': 1},
                             {'beats': [{'after_seconds': -1, 'text': 'Bad'}]}):
            with self.subTest(patch_values=patch_values), self.assertRaises(ValidationError):
                QuestInteractionSpec.model_validate({**self.spec, **patch_values})
        exported['spec']['steps'][0]['interaction']['goto'] = 'absent'
        with self.assertRaises(ManifestError):
            parse_quest_manifest(world=self.world, manifest=exported)
