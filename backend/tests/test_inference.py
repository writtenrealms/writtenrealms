import copy
import io
import json
import tempfile
import uuid
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from time import monotonic
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, close_old_connections, connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from inference import providers, service, tasks
from inference.configuration import Unavailable, maximum_cost, micro, validate_configuration
from inference.models import BudgetBucket, InferenceControl, InferenceRequest
from users.models import User
from worlds.models import World


CONFIG = {'provider': 'openai', 'profiles': {'fast': {
    'model': 'fixture-model-2026-10-01', 'price_revision': 'fixture-prices',
    'rates': {'input': '1', 'output': '4', 'cached_input': '0.1', 'cache_write': '2'},
    'max_input_tokens': 8192, 'max_output_tokens': 256,
}}}
USAGE = {'input': 100, 'output': 10, 'cached_input': 20, 'cache_write': 30}
GOOD = providers.Outcome(usage=USAGE, result={'text': 'Hello.'})
OPENAI_RESPONSE = {'status': 'completed', 'service_tier': 'default',
    'usage': {'input_tokens': 150, 'output_tokens': 10,
              'input_tokens_details': {'cached_tokens': 20, 'cache_write_tokens': 30},
              'output_tokens_details': {'reasoning_tokens': 5}},
    'output': [{'type': 'reasoning', 'summary': []}, {'type': 'message',
               'content': [{'type': 'output_text', 'text': '{"text":"Hello."}'}]}]}
ANTHROPIC_RESPONSE = {'stop_reason': 'end_turn', 'usage': {
    'input_tokens': 100, 'output_tokens': 10,
    'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30,
    'service_tier': 'standard'}, 'content': [{'type': 'text', 'text': '{"text":"Hello."}'}]}


class TestInferenceProviders(SimpleTestCase):
    def test_rejected_model_output_is_bounded_and_has_a_specific_reason(self):
        for value, reason in [('bad json', 'invalid_json'), ('{"text": 5}', 'invalid_text_type'),
                              ('{"wrong": "field"}', 'invalid_response_fields'), ('x' * 5000, 'invalid_json')]:
            response = copy.deepcopy(OPENAI_RESPONSE)
            next(part for part in response['output'] if part['type'] == 'message')['content'][0]['text'] = value
            outcome = providers.parse_response('openai', response)
            self.assertEqual(outcome.error, 'invalid_output')
            self.assertIsNone(outcome.result)
            self.assertEqual(outcome.rejection, {'reason': reason, 'response': value[:4096], 'truncated': len(value) > 4096})

    def test_fractional_monetary_unit_cannot_increase_a_cap(self):
        self.assertEqual(micro('0.0000009'), 0)
        self.assertEqual(micro('90.0000009'), 90000000)

    def test_usage_accounts_for_cache_writes_reads_and_reasoning_once(self):
        for provider, response in [('openai', OPENAI_RESPONSE), ('anthropic', ANTHROPIC_RESPONSE)]:
            with self.subTest(provider=provider):
                self.assertEqual(providers.parse_response(provider, response), GOOD)

    def test_malformed_refused_truncated_and_unpriced_outputs(self):
        for response, error, known in [
            ({'usage': {}}, 'unknown_usage', False),
            ({**OPENAI_RESPONSE, 'status': 'incomplete'}, 'incomplete_output', True),
            ({**OPENAI_RESPONSE, 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]}, 'invalid_output', True),
            ({**OPENAI_RESPONSE, 'service_tier': 'priority'}, 'unknown_billing_category', False),
            ({**OPENAI_RESPONSE, 'output': [{'type': 'web_search_call'}]}, 'unknown_billing_category', False),
            ({**OPENAI_RESPONSE, 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"text":"ok","transfer":42}'}]}]}, 'invalid_output', True),
        ]:
            result = providers.parse_response('openai', response)
            self.assertEqual(result.error, error)
            self.assertEqual(result.usage is not None, known)
            self.assertIsNone(result.result)
        for count in (-1, True, 1.2, '100'):
            response = copy.deepcopy(OPENAI_RESPONSE)
            response['usage']['input_tokens'] = count
            self.assertIsNone(providers.parse_response('openai', response).usage)

    @override_settings(LLM_WORKER=True, LLM_NETWORK_ENABLED=True, LLM_API_KEY='test-only', LLM_OPENAI_PROJECT='')
    def test_adapters_make_one_bounded_request(self):
        for provider, response in [('openai', OPENAI_RESPONSE), ('anthropic', ANTHROPIC_RESPONSE)]:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json=response)
            with patch('inference.providers.httpx.HTTPTransport', return_value=httpx.MockTransport(handler)) as transport:
                result = providers.perform(provider, CONFIG['profiles']['fast'],
                    {'instructions': 'Stay local.', 'messages': [{'role': 'user', 'content': 'Hello'}]})
            self.assertEqual(result, GOOD)
            transport.assert_called_once_with(retries=0)
            self.assertEqual(len(seen), 1)
            body = json.loads(seen[0].content)
            self.assertNotIn('tools', body)
            self.assertEqual(body['model'], CONFIG['profiles']['fast']['model'])
            if provider == 'openai':
                self.assertFalse(body['store'])
                self.assertEqual(body['max_output_tokens'], 256)
                self.assertTrue(body['text']['format']['strict'])
            else:
                self.assertEqual(body['max_tokens'], 256)
                self.assertEqual(body['output_config']['format']['schema'], providers.SCHEMA)

    @override_settings(LLM_WORKER=True, LLM_NETWORK_ENABLED=True, LLM_API_KEY='test-only')
    def test_errors_timeouts_redirects_and_large_responses_do_not_retry(self):
        for behavior in ('timeout', 'redirect', 'rate', 'large'):
            calls = []
            def handler(request):
                calls.append(request)
                if behavior == 'timeout':
                    raise httpx.ReadTimeout('potentially sensitive message')
                if behavior == 'large':
                    return httpx.Response(200, content=b'x' * (providers.MAX_RESPONSE_BYTES + 1))
                return httpx.Response(302 if behavior == 'redirect' else 429,
                                      headers={'Location': 'https://not-the-provider.example'})
            with patch('inference.providers.httpx.HTTPTransport', return_value=httpx.MockTransport(handler)):
                result = providers.perform('openai', CONFIG['profiles']['fast'], {'instructions': '', 'messages': []})
            self.assertIsNone(result.usage)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('sensitive', result.error)

    def test_test_settings_prevent_network(self):
        with patch('inference.providers.httpx.Client') as client:
            self.assertEqual(providers.perform('openai', {}, {}).error, 'worker_not_enabled')
        client.assert_not_called()

    def test_configuration_rejects_unknown_prices_keys_and_unbounded_limits(self):
        for change in (
            {'api_key': 'must-not-persist'}, {'provider': 'other'},
            {'limits': {'monthly_usd': 'NaN'}}, {'limits': {'concurrent_requests': 500}},
            {'profiles': {}}, {'profiles': {'fast': {**CONFIG['profiles']['fast'], 'rates': {'input': '1'}}}},
            {'profiles': {'fast': {**CONFIG['profiles']['fast'], 'max_output_tokens': 1000000}}},
            {'limits': {'monthly_usd': '1'}},
        ):
            with self.subTest(change=change), self.assertRaises(Unavailable):
                validate_configuration({**CONFIG, **change})


@override_settings(LLM_API_KEY='test-only', LLM_WORKER=True, LLM_NETWORK_ENABLED=True)
class TestInferenceAccounting(TransactionTestCase):
    def setUp(self):
        # Gateway tests isolate the consumer; playable conversations exercise
        # the real game admission guard in test_mob_conversations.
        self.enterContext(patch('spawns.conversations.service.dispatch_guard',
                                return_value=nullcontext(True)))
        self.user = User.objects.create_user('inference@example.com', 'p')
        self.world = World.objects.create(name='A village', author=self.user)
        self.config = copy.deepcopy(CONFIG)
        service.configure(self.config)
        service.set_enabled(True)

    def submit(self, **kwargs):
        return service.submit(**{'request_id': uuid.uuid4(), 'user_id': self.user.pk,
            'world_id': self.world.pk, 'purpose': 'mob_conversation', 'instructions': 'Stay local.',
            'messages': [{'role': 'user', 'content': 'Hello.'}], **kwargs})

    def row(self, admission):
        self.assertFalse(admission.reason, admission.reason)
        return InferenceRequest.objects.get(pk=admission.request_id)

    def bucket(self):
        return BudgetBucket.objects.get(scope='installation:month')

    def test_reservation_and_exactly_once_settlement(self):
        row = self.row(self.submit())
        self.assertEqual(self.bucket().reserved_micro, maximum_cost(row.profile))
        self.assertEqual(row.allocations.count(), 7)
        request = service.claim(row.pk)
        self.assertIsNone(service.claim(row.pk))
        service.settle(row.pk, request.dispatch_token, GOOD)
        service.settle(row.pk, request.dispatch_token, GOOD)
        row.refresh_from_db()
        self.assertEqual((self.bucket().spent_micro, self.bucket().reserved_micro), (202, 0))
        self.assertEqual(row.status, 'succeeded')
        self.assertEqual(row.payload, {})
        self.assertEqual(row.result, GOOD.result)

    def test_duplicate_identity_and_conflicting_input(self):
        identity = uuid.uuid4()
        first = self.submit(request_id=identity)
        second = self.submit(request_id=identity)
        self.assertEqual(first, second)
        self.assertEqual(self.submit(request_id=identity, instructions='different').reason, 'identity_conflict')
        self.assertEqual(InferenceRequest.objects.count(), 1)
        self.assertEqual(self.bucket().requests, 1)

    def test_owner_allowance_uses_linked_accounts_and_world_uses_base_identity(self):
        User.objects.filter(pk=self.user.pk).update(link_id=77)
        other = User.objects.create_user('linked@example.com', 'p', link_id=77)
        template = World.objects.create(name='Cave', instance_of=self.world)
        runtime = World.objects.create(name='Cave copy', context=template)
        request = self.row(self.submit(world_id=runtime.pk))
        self.assertEqual(request.world_id, self.world.pk)
        self.assertEqual(self.submit(user_id=other.pk).reason, 'owner_busy')
        service.settle(request.pk, service.claim(request.pk).dispatch_token, GOOD)
        self.config['limits'] = {'owner_daily_requests': 1}
        service.configure(self.config)
        service.set_enabled(True)
        self.assertEqual(self.submit(user_id=other.pk).reason, 'owner_quota')

    def test_disable_credentials_accounting_and_budget_denials_dispatch_nothing(self):
        with patch('inference.providers.perform') as provider:
            service.set_enabled(False)
            self.assertEqual(self.submit().reason, 'disabled')
            service.set_enabled(True)
            with override_settings(LLM_API_KEY=''):
                self.assertEqual(self.submit().reason, 'missing_credentials')
            with patch('inference.service._control', side_effect=OperationalError('db down')):
                self.assertEqual(self.submit().reason, 'accounting_unavailable')
            self.config['limits'] = {key: '0' for key in (
                'monthly_usd', 'daily_usd', 'conversation_monthly_usd', 'conversation_daily_usd',
                'preparation_monthly_usd', 'preparation_daily_usd', 'world_monthly_usd', 'world_daily_usd')}
            service.configure(self.config)
            service.set_enabled(True)
            self.assertEqual(self.submit().reason, 'budget_exhausted')
            self.assertEqual(service.dispatch_batch(), [])
            self.assertFalse(InferenceRequest.objects.exists())
            self.assertFalse(BudgetBucket.objects.exists())
        provider.assert_not_called()

    def test_malformed_and_oversize_inputs_never_reserve(self):
        for change in ({'messages': [{'role': 'system', 'content': 'bad'}]},
                       {'instructions': 'x' * 40000}, {'profile': 'deliberate'},
                       {'purpose': 'moderation'}, {'request_id': 'bad-id'}):
            self.assertTrue(self.submit(**change).reason)
        self.assertFalse(InferenceRequest.objects.exists())
        self.assertFalse(BudgetBucket.objects.exists())

    def test_timeout_is_charged_no_retry_and_late_evidence_can_reconcile_once(self):
        request = self.row(self.submit())
        claim = service.claim(request.pk)
        service.settle(request.pk, claim.dispatch_token, providers.Outcome(error='provider_unavailable'))
        self.assertEqual(self.bucket().spent_micro, request.reservation_micro)
        self.assertEqual(self.bucket().reserved_micro, 0)
        self.assertEqual(self.submit().reason, 'circuit_open')
        self.assertIsNone(service.claim(request.pk))
        service.settle(request.pk, claim.dispatch_token, GOOD)
        service.settle(request.pk, claim.dispatch_token, GOOD)
        request.refresh_from_db()
        self.assertEqual(request.status, 'discarded')
        self.assertIsNone(request.result)
        self.assertEqual(self.bucket().spent_micro, 202)

    def test_crashed_worker_and_broker_recovery(self):
        request = self.row(self.submit())
        with patch('inference.tasks.execute_request.apply_async', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                tasks.dispatch_requests()
        self.assertEqual(self.bucket().reserved_micro, request.reservation_micro)
        InferenceRequest.objects.filter(pk=request.pk).update(enqueue_after=timezone.now() - timedelta(seconds=1))
        self.assertEqual(service.dispatch_batch(), [str(request.pk)])
        claimed = service.claim(request.pk)
        InferenceRequest.objects.filter(pk=request.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(service.dispatch_batch(), [])
        self.assertEqual(service.dispatch_batch(), [])
        request.refresh_from_db()
        self.assertEqual(request.status, 'uncertain')
        self.assertEqual(self.bucket().spent_micro, request.reservation_micro)
        service.settle(request.pk, claimed.dispatch_token, GOOD)
        self.assertEqual(self.bucket().spent_micro, 202)

    def test_generation_changes_cancel_queue_and_suppress_in_flight_result(self):
        request = self.row(self.submit())
        service.set_enabled(False)
        self.assertIsNone(service.claim(request.pk))
        self.assertEqual(self.bucket().reserved_micro, 0)
        self.assertEqual(self.bucket().requests, 0)
        service.set_enabled(True)
        request = self.row(self.submit())
        claimed = service.claim(request.pk)
        service.set_enabled(False)
        service.set_enabled(True)
        service.settle(request.pk, claimed.dispatch_token, GOOD)
        request.refresh_from_db()
        self.assertEqual(request.status, 'discarded')
        self.assertIsNone(request.result)
        self.assertEqual(self.bucket().spent_micro, 202)

    def test_price_snapshot_survives_configuration_changes(self):
        request = self.row(self.submit())
        claimed = service.claim(request.pk)
        self.config['profiles']['fast']['rates']['input'] = '99'
        service.configure(self.config)
        service.settle(request.pk, claimed.dispatch_token, GOOD)
        self.assertEqual(self.bucket().spent_micro, 202)
        self.assertFalse(InferenceControl.objects.get().enabled)

    def test_invalid_output_is_charged_and_never_repaired(self):
        request = self.row(self.submit())
        with patch('inference.providers.perform', return_value=providers.Outcome(usage=USAGE, error='invalid_output')) as provider:
            tasks.execute_request(str(request.pk))
            tasks.execute_request(str(request.pk))
        provider.assert_called_once()
        request.refresh_from_db()
        self.assertEqual(request.status, 'invalid')
        self.assertEqual(self.bucket().spent_micro, 202)
        self.assertIsNone(request.result)

    def test_invoice_above_reservation_trips_manual_shutdown(self):
        request = self.row(self.submit())
        claim = service.claim(request.pk)
        outcome = providers.Outcome(usage={**USAGE, 'output': 100000}, result=GOOD.result)
        service.settle(request.pk, claim.dispatch_token, outcome)
        self.assertFalse(InferenceControl.objects.get().enabled)
        self.assertGreater(self.bucket().spent_micro, request.reservation_micro)
        self.assertEqual(self.submit().reason, 'disabled')
        request.refresh_from_db()
        self.assertIsNone(request.result)

    def test_month_boundary_keeps_dispatched_usage_in_original_period(self):
        now = timezone.now().replace(day=1, hour=0, minute=0, second=5)
        before = now - timedelta(seconds=10)
        with patch('django.utils.timezone.now', return_value=before):
            request = self.row(self.submit())
            claimed = service.claim(request.pk)
        with patch('django.utils.timezone.now', return_value=now):
            service.settle(request.pk, claimed.dispatch_token, GOOD)
        bucket = self.bucket()
        self.assertEqual(bucket.period, before.date().replace(day=1))
        self.assertEqual(bucket.spent_micro, 202)
        request.refresh_from_db()
        self.assertEqual(request.status, 'discarded')
        with patch('django.utils.timezone.now', return_value=before):
            queued = self.row(self.submit())
        with patch('django.utils.timezone.now', return_value=now):
            self.assertIsNone(service.claim(queued.pk))
        self.assertEqual(self.bucket().reserved_micro, 0)

    def test_worker_guard_and_external_io_outside_transactions(self):
        request = self.row(self.submit())
        with override_settings(LLM_WORKER=False), patch('inference.providers.perform') as provider:
            tasks.execute_request(str(request.pk))
            provider.assert_not_called()
        def perform(*args):
            self.assertFalse(connection.in_atomic_block)
            return GOOD
        with patch('inference.providers.perform', side_effect=perform):
            tasks.execute_request(str(request.pk))

    def test_queue_and_dispatch_concurrency_caps(self):
        self.config['limits'] = {'pending_requests': 2, 'concurrent_requests': 1}
        service.configure(self.config)
        service.set_enabled(True)
        second = User.objects.create_user('second@example.com', 'p')
        third = User.objects.create_user('third@example.com', 'p')
        first_request = self.row(self.submit())
        second_request = self.row(self.submit(user_id=second.pk))
        self.assertEqual(self.submit(user_id=third.pk).reason, 'queue_full')
        claimed = service.claim(first_request.pk)
        self.assertIsNone(service.claim(second_request.pk))
        service.settle(first_request.pk, claimed.dispatch_token, GOOD)
        self.assertIsNotNone(service.claim(second_request.pk))

    def test_parallel_admissions_cannot_spend_the_same_last_reservation(self):
        users = [User.objects.create_user(f'parallel{i}@example.com', 'p') for i in range(12)]
        cost = maximum_cost(CONFIG['profiles']['fast'])
        # One reservation of remaining capacity in every USD scope.
        amount = str(cost / 1000000)
        self.config['limits'] = {key: amount for key in (
            'monthly_usd', 'daily_usd', 'conversation_monthly_usd', 'conversation_daily_usd',
            'world_monthly_usd', 'world_daily_usd')}
        self.config['limits'].update(preparation_monthly_usd='0', preparation_daily_usd='0')
        service.configure(self.config)
        service.set_enabled(True)
        barrier = Barrier(len(users))
        def admit(user_id):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return self.submit(user_id=user_id)
            finally:
                connections.close_all()
        start = monotonic()
        with ThreadPoolExecutor(max_workers=len(users)) as pool:
            results = list(pool.map(admit, [user.pk for user in users]))
        elapsed = monotonic() - start
        self.assertEqual(sum(not result.reason for result in results), 1)
        self.assertEqual({result.reason for result in results if result.reason}, {'budget_exhausted'})
        self.assertEqual(self.bucket().reserved_micro, cost)
        # Generous regression bound, not a production latency claim.
        self.assertLess(elapsed, 15)
        print(f'Inference contention: 12 simultaneous admissions in {elapsed:.3f}s; one accepted.')

    def test_admission_query_count_does_not_grow_with_history(self):
        with CaptureQueriesContext(connection) as first:
            request = self.row(self.submit())
        service.settle(request.pk, service.claim(request.pk).dispatch_token, GOOD)
        InferenceRequest.objects.bulk_create([
            InferenceRequest(id=uuid.uuid4(), fingerprint='history', purpose='mob_conversation',
                owner='user:history', user_id=self.user.pk, world_id=self.world.pk, provider='openai',
                profile=request.profile, generation=0, reservation_micro=1, status='cancelled',
                expires_at=timezone.now(), enqueue_after=timezone.now(), completed_at=timezone.now())
            for _ in range(1000)])
        with CaptureQueriesContext(connection) as second:
            self.row(self.submit())
        self.assertEqual(len(first), len(second))
        self.assertLessEqual(len(second), 17)
        print(f'Inference admission: {len(second)} queries with 1,000 historical requests (includes test readback).')

    def test_retention_scrubs_results_then_keeps_aggregate_spending(self):
        request = self.row(self.submit())
        service.settle(request.pk, service.claim(request.pk).dispatch_token, GOOD)
        InferenceRequest.objects.filter(pk=request.pk).update(completed_at=timezone.now() - timedelta(days=4))
        service.prune()
        request.refresh_from_db()
        self.assertIsNone(request.result)
        InferenceRequest.objects.filter(pk=request.pk).update(completed_at=timezone.now() - timedelta(days=91))
        service.prune()
        self.assertFalse(InferenceRequest.objects.exists())
        self.assertEqual(self.bucket().spent_micro, 202)

    def command(self, *args):
        output = io.StringIO()
        call_command('llm', *args, stdout=output)
        return json.loads(output.getvalue())

    def test_operator_commands_are_dry_by_default_and_never_print_credentials(self):
        args = ('smoke', '--user-id', str(self.user.pk), '--world-id', str(self.world.pk))
        self.assertTrue(self.command(*args)['dry_run'])
        self.assertFalse(InferenceRequest.objects.exists())
        result = self.command(*args, '--execute')
        request = InferenceRequest.objects.get(pk=result['request_id'])
        self.assertEqual(request.purpose, 'mob_evaluation')
        status = self.command('status')
        self.assertNotIn('test-only', json.dumps(status))
        self.assertEqual({bucket['scope'] for bucket in status['current_installation_buckets']},
                         {'installation:day', 'installation:month'})
        self.assertNotIn('payload', self.command('request', result['request_id']))
        self.command('disable')
        with self.assertRaises(CommandError):
            self.command(*args, '--execute')
        with override_settings(LLM_API_KEY=''), self.assertRaises(CommandError):
            self.command('enable')
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as handle:
            json.dump(CONFIG, handle)
            handle.flush()
            self.assertFalse(self.command('configure', handle.name)['enabled'])
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as handle:
            json.dump({'instructions': 'Stay local.', 'messages': [{'role': 'user', 'content': 'Who is the king?'}]}, handle)
            handle.flush()
            self.assertTrue(self.command('evaluate', '--input-file', handle.name,
                '--user-id', str(self.user.pk), '--world-id', str(self.world.pk))['dry_run'])

    def test_result_access_rechecks_control_and_expiry(self):
        request = self.row(self.submit())
        service.settle(request.pk, service.claim(request.pk).dispatch_token, GOOD)
        self.assertEqual(service.result_for(request.pk), GOOD.result)
        service.set_enabled(False)
        self.assertIsNone(service.result_for(request.pk))
        service.set_enabled(True)
        self.assertIsNone(service.result_for(request.pk))
        request = self.row(self.submit())
        service.settle(request.pk, service.claim(request.pk).dispatch_token, GOOD)
        InferenceRequest.objects.filter(pk=request.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(service.result_for(request.pk))

    def test_accounting_failure_after_claim_never_causes_redispatch(self):
        request = self.row(self.submit())
        with patch('inference.providers.perform', return_value=GOOD) as provider:
            with patch('inference.service.settle', side_effect=OperationalError('db down')):
                with self.assertRaises(OperationalError):
                    tasks.execute_request(str(request.pk))
            tasks.execute_request(str(request.pk))
        provider.assert_called_once()
        self.assertEqual(self.bucket().reserved_micro, request.reservation_micro)
        InferenceRequest.objects.filter(pk=request.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        service.dispatch_batch()
        self.assertEqual(self.bucket().spent_micro, request.reservation_micro)

    def test_all_allocation_scopes_gate_admission(self):
        # Reserve/cancel once to materialize all scopes without spending money.
        request = self.row(self.submit())
        service.set_enabled(False)
        service.claim(request.pk)
        service.set_enabled(True)
        for scope in ('installation:month', 'installation:day', 'conversation:month',
                      'conversation:day', f'world:{self.world.pk}:month', f'world:{self.world.pk}:day'):
            with self.subTest(scope=scope):
                BudgetBucket.objects.filter(scope=scope).update(spent_micro=100000000)
                self.assertEqual(self.submit().reason, 'budget_exhausted')
                BudgetBucket.objects.filter(scope=scope).update(spent_micro=0)
        preparation = self.row(self.submit(purpose='mob_evaluation'))
        service.set_enabled(False)
        service.claim(preparation.pk)
        service.set_enabled(True)
        BudgetBucket.objects.filter(scope='preparation:day').update(spent_micro=500000)
        self.assertEqual(self.submit(purpose='mob_preparation').reason, 'budget_exhausted')
        self.assertFalse(self.submit().reason)

    def test_concurrent_duplicate_delivery_calls_provider_once(self):
        request = self.row(self.submit())
        barrier = Barrier(8)
        def execute(_):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                tasks.execute_request(str(request.pk))
            finally:
                connections.close_all()
        with patch('inference.providers.perform', return_value=GOOD) as provider:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(execute, range(8)))
        provider.assert_called_once()
        self.assertEqual(self.bucket().spent_micro, 202)

    def test_expired_owner_buckets_are_bounded_and_current_allowance_survives(self):
        old = timezone.now().date() - timedelta(days=91)
        BudgetBucket.objects.bulk_create([BudgetBucket(scope=f'owner:user:{i}:day', period=old) for i in range(501)])
        request = self.row(self.submit())
        service.prune()
        self.assertEqual(BudgetBucket.objects.filter(period=old).count(), 1)
        self.assertEqual(self.bucket().reserved_micro, request.reservation_micro)
        self.assertEqual(BudgetBucket.objects.filter(scope__startswith='owner:').count(), 2)

    def test_consumer_cancellation_refunds_only_undispatched_work(self):
        request = self.row(self.submit())
        service.cancel(request.pk)
        service.cancel(request.pk)
        self.assertEqual(self.bucket().reserved_micro, 0)
        self.assertEqual(self.bucket().requests, 0)
        self.assertIsNone(service.claim(request.pk))
        request = self.row(self.submit())
        claim = service.claim(request.pk)
        service.cancel(request.pk)
        self.assertEqual(self.bucket().reserved_micro, request.reservation_micro)
        service.settle(request.pk, claim.dispatch_token, GOOD)
        self.assertEqual(self.bucket().spent_micro, 202)
        self.assertIsNone(service.result_for(request.pk))
