import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from django.test import SimpleTestCase

from fastapi_app import game_ws
from spawns.actions.communication import _communication_events
from spawns.events import GameEvent, command_request_scope, publish_events
from spawns.models import Player


class TestSharedCommunicationPublisher(unittest.TestCase):
    def test_personalized_answer_keeps_other_listeners_in_one_shared_event(self):
        actor = Player(id=1, name='Alden')
        data = {'actor': {'id': 1, 'name': 'Alden'}, 'target': {'id': 2, 'name': 'Mira'},
                'text': 'At the harbor shop.', 'question_id': 3}
        result = _communication_events(actor, 'answer', data, list(range(2, 503)))
        self.assertEqual(len(result.events), 3)
        by_text = {event.text: event.recipients for event in result.events}
        self.assertEqual(by_text["You answer 'At the harbor shop.'"], ['player.1'])
        self.assertEqual(by_text["Alden answers you 'At the harbor shop.'"], ['player.2'])
        self.assertEqual(by_text["Alden answers Mira 'At the harbor shop.'"],
                         [f'player.{index}' for index in range(3, 503)])

    def test_501_recipients_use_three_bounded_envelopes_and_one_pipeline(self):
        recipients = [f'player.{index}' for index in range(501)]
        message = {'type': 'notification.cmd.ask.success', 'data': {'text': 'Question?'}}
        with patch.object(game_ws, '_get_sync_redis') as get_redis:
            redis = get_redis.return_value
            pipeline = redis.pipeline.return_value.__enter__.return_value
            game_ws.publish_to_players(recipients, message)

        get_redis.assert_called_once_with()
        redis.pipeline.assert_called_once_with(transaction=False)
        redis.publish.assert_not_called()
        self.assertEqual(pipeline.publish.call_count, 3)
        payloads = []
        for call in pipeline.publish.call_args_list:
            self.assertEqual(call.args[0], game_ws.GAME_PUBSUB_CHANNEL)
            payloads.append(json.loads(call.args[1]))
        self.assertEqual([len(payload['player_keys']) for payload in payloads], [250, 250, 1])
        self.assertEqual([key for payload in payloads for key in payload['player_keys']], recipients)
        self.assertTrue(all(payload['message'] == message for payload in payloads))
        pipeline.execute.assert_called_once_with()
        redis.close.assert_called_once_with()

    def test_empty_audience_does_not_open_a_redis_connection(self):
        with patch.object(game_ws, '_get_sync_redis') as get_redis:
            game_ws.publish_to_players([], {'type': 'notification.cmd.chat.success'})
        get_redis.assert_not_called()

    def test_pipeline_failure_releases_the_redis_client(self):
        with patch.object(game_ws, '_get_sync_redis') as get_redis:
            redis = get_redis.return_value
            pipeline = redis.pipeline.return_value.__enter__.return_value
            pipeline.execute.side_effect = RuntimeError('redis unavailable')
            with self.assertRaises(RuntimeError):
                game_ws.publish_to_players(['player.1'], {'type': 'notification.cmd.chat.success'})
        redis.close.assert_called_once_with()

    def test_single_recipient_connection_protocol_is_unchanged(self):
        message = {'type': 'cmd.chat.success', 'data': {}}
        with patch.object(game_ws, '_get_sync_redis') as get_redis:
            game_ws.publish_to_player('player.1', message, connection_id='current')
        payload = json.loads(get_redis.return_value.publish.call_args.args[1])
        self.assertEqual(payload, {
            'player_key': 'player.1', 'connection_id': 'current', 'message': message,
        })


class TestSharedCommunicationDelivery(unittest.TestCase):
    def socket(self):
        socket = MagicMock()
        socket.send_json = AsyncMock()
        socket.close = AsyncMock()
        return socket

    def test_shared_envelope_reaches_only_listed_local_connections_once(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            one, two, unrelated = self.socket(), self.socket(), self.socket()
            await manager.authenticate(one, 'player.1')
            await manager.authenticate(two, 'player.2')
            await manager.authenticate(unrelated, 'player.99')
            message = {'type': 'notification.cmd.gossip.success', 'data': {'text': 'Hello.'}}
            await manager._handle_pub({
                'player_keys': ['player.1', 'player.2', 'player.1', 'player.3'],
                'message': message,
            })
            one.send_json.assert_awaited_once_with(message)
            two.send_json.assert_awaited_once_with(message)
            unrelated.send_json.assert_not_awaited()
        asyncio.run(run())

    def test_shared_envelopes_reject_oversize_or_connection_pinned_audiences(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            socket = self.socket()
            await manager.authenticate(socket, 'player.1')
            for keys, connection_id in [
                (['player.1'] * 251, None), ('player.1', None),
                (['player.1'], 'private-connection'), ([1], None),
            ]:
                with self.assertLogs('game_ws', level='WARNING'):
                    await manager._handle_pub({
                        'player_keys': keys, 'connection_id': connection_id,
                        'message': {'type': 'notification.cmd.ask.success'},
                    })
            socket.send_json.assert_not_awaited()
        asyncio.run(run())

    def test_failed_socket_is_closed_without_interrupting_other_recipients(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            broken, healthy = self.socket(), self.socket()
            broken.send_json.side_effect = RuntimeError('socket closed')
            await manager.authenticate(broken, 'player.1')
            await manager.authenticate(healthy, 'player.2')
            message = {'type': 'notification.cmd.chat.success'}
            await manager._handle_pub({'player_keys': ['player.1', 'player.2'], 'message': message})
            broken.close.assert_awaited_once_with()
            healthy.send_json.assert_awaited_once_with(message)
            # The connection handler still owns normal exit-world cleanup.
            self.assertTrue(manager.is_current_connection(broken))
        asyncio.run(run())

    def test_failed_old_socket_does_not_close_or_unregister_its_replacement(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            old, replacement = self.socket(), self.socket()
            await manager.authenticate(old, 'player.1')

            async def fail_after_reconnect(message):
                await manager.authenticate(replacement, 'player.1')
                raise RuntimeError('old socket closed')

            old.send_json.side_effect = fail_after_reconnect
            await manager._handle_pub({
                'player_keys': ['player.1'], 'message': {'type': 'notification.cmd.answer.success'},
            })
            old.close.assert_awaited_once_with()
            replacement.close.assert_not_awaited()
            await manager.disconnect(old)
            self.assertTrue(manager.is_current_connection(replacement))
        asyncio.run(run())

    def test_slow_socket_times_out_while_healthy_recipient_receives_message(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            slow, healthy = self.socket(), self.socket()

            async def wait_forever(message):
                await asyncio.Event().wait()

            slow.send_json.side_effect = wait_forever
            await manager.authenticate(slow, 'player.1')
            await manager.authenticate(healthy, 'player.2')
            with patch.object(game_ws, 'GAME_BULK_SEND_TIMEOUT_SECONDS', 0.01):
                await manager._handle_pub({
                    'player_keys': ['player.1', 'player.2'],
                    'message': {'type': 'notification.cmd.chat.success'},
                })
            slow.close.assert_awaited_once_with()
            healthy.send_json.assert_awaited_once()
        asyncio.run(run())

    def test_delivery_limits_concurrent_socket_sends(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            active = maximum = 0

            async def send(message):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0)
                active -= 1

            keys = [f'player.{index}' for index in range(65)]
            for key in keys:
                socket = self.socket()
                socket.send_json.side_effect = send
                await manager.authenticate(socket, key)
            await manager._handle_pub({
                'player_keys': keys, 'message': {'type': 'notification.cmd.chat.success'},
            })
            self.assertEqual(maximum, game_ws.GAME_BULK_SEND_CONCURRENCY)
            self.assertEqual(active, 0)
        asyncio.run(run())

    def test_single_recipient_stale_connection_filter_still_applies(self):
        async def run():
            manager = game_ws.GameConnectionManager()
            socket = self.socket()
            connection_id = await manager.authenticate(socket, 'player.1')
            message = {'type': 'cmd.ask.success', 'data': {}}
            await manager._handle_pub({
                'player_key': 'player.1', 'connection_id': 'stale', 'message': message,
            })
            socket.send_json.assert_not_awaited()
            await manager._handle_pub({
                'player_key': 'player.1', 'connection_id': connection_id, 'message': message,
            })
            socket.send_json.assert_awaited_once_with(message)
        asyncio.run(run())


class TestSharedCommunicationEvents(SimpleTestCase):
    def setUp(self):
        self.single = self.enterContext(patch('spawns.events.publish_to_player'))
        self.shared = self.enterContext(patch('spawns.events.publish_to_players'))
        self.triggers = self.enterContext(patch(
            'spawns.trigger_subscriptions.dispatch_trigger_subscriptions_for_event',
        ))
        self.quests = self.enterContext(patch(
            'quests.subscriptions.dispatch_quest_subscriptions_for_event',
        ))

    def test_shared_channel_notifications_are_batched_and_keep_subscription_dispatch(self):
        for channel in ('ask', 'answer', 'chat', 'gossip', 'cchat'):
            event = GameEvent(
                f'notification.cmd.{channel}.success', {'text': 'Hello'},
                ['player.2', 'player.3'], 'A message.',
            )
            publish_events([event], actor_key='player.1', connection_id='actor-connection')
            self.shared.assert_called_with(event.recipients, event.to_message())
            self.triggers.assert_called_with(
                event_type=event.type, event_data=event.data,
                actor_key='player.1', connection_id='actor-connection',
            )
            self.quests.assert_called_with(
                event_type=event.type, event_data=event.data,
                actor_key='player.1', connection_id='actor-connection',
            )
        self.assertEqual(self.shared.call_count, 5)
        self.single.assert_not_called()

    def test_actor_receipt_stays_private_and_pinned_to_the_command_connection(self):
        request_id = str(uuid.uuid4())
        actor = GameEvent('cmd.ask.success', {'text': 'Question?'}, ['player.1'])
        audience = GameEvent('notification.cmd.ask.success', {'text': 'Question?'}, ['player.2'])
        with command_request_scope(
            request_id=request_id, request_segment='', actor_key='player.1',
        ):
            publish_events([actor, audience], actor_key='player.1', connection_id='private')
        self.single.assert_called_once()
        self.assertEqual(self.single.call_args.args[0], 'player.1')
        self.assertEqual(self.single.call_args.args[1]['data']['request_id'], request_id)
        self.assertEqual(self.single.call_args.kwargs['connection_id'], 'private')
        self.assertNotIn('request_id', self.shared.call_args.args[1]['data'])

    def test_explicit_connection_or_actor_pin_keeps_single_recipient_delivery(self):
        for event_connection, actor_key in [('pinned', None), (None, 'player.1')]:
            self.single.reset_mock()
            event = GameEvent(
                'notification.cmd.chat.success', {}, ['player.1'],
                connection_id=event_connection,
            )
            publish_events([event], actor_key=actor_key, connection_id='actor-pinned')
            self.single.assert_called_once()
            self.assertEqual(
                self.single.call_args.kwargs['connection_id'],
                event_connection or 'actor-pinned',
            )
        self.shared.assert_not_called()

    def test_recipient_specific_payloads_and_direct_messages_keep_individual_projection(self):
        for event_type, data in [
            ('notification.cmd.tell.success', {'text': 'Private.'}),
            ('notification.cmd.whisper.success', {'text': 'Private.'}),
            ('notification.cmd.chat.success', {'_combat_awards': {'player.1': {'award': 5}}}),
            ('notification.cmd.chat.success', {'_combat_narration': {'player.1': 'Personal.'}}),
        ]:
            self.single.reset_mock()
            publish_events([GameEvent(event_type, data, ['player.1'])])
            self.single.assert_called_once()
            public_data = self.single.call_args.args[1]['data']
            self.assertNotIn('_combat_awards', public_data)
            self.assertNotIn('_combat_narration', public_data)
        self.shared.assert_not_called()
