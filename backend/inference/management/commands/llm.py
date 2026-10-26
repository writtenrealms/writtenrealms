import json
import uuid
from datetime import timezone as datetime_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

from inference import service
from inference.configuration import Unavailable, maximum_cost, validate_configuration, validate_payload
from inference.models import BudgetBucket, InferenceControl, InferenceRequest


def read_json(path):
    try:
        with Path(path).open('rb') as handle:
            data = handle.read(65537)
        if len(data) > 65536:
            raise ValueError()
        return json.loads(data)
    except (OSError, ValueError):
        raise CommandError('Expected a JSON file of at most 64 KiB.') from None


class Command(BaseCommand):
    help = 'Configure shared inference, inspect accounting, or queue explicitly budgeted checks.'

    def add_arguments(self, parser):
        commands = parser.add_subparsers(dest='action', required=True)
        commands.add_parser('configure').add_argument('path')
        for name in ('enable', 'disable', 'status'):
            commands.add_parser(name)
        inspect = commands.add_parser('request')
        inspect.add_argument('request_id', type=uuid.UUID)
        inspect.add_argument('--result', action='store_true', help='Include retained generated text.')
        for name in ('smoke', 'evaluate'):
            command = commands.add_parser(name)
            command.add_argument('--world-id', type=int, required=True)
            command.add_argument('--user-id', type=int, required=True)
            command.add_argument('--profile', choices=['fast', 'deliberate'], default='fast')
            command.add_argument('--request-id', type=uuid.UUID)
            command.add_argument('--execute', action='store_true', help='Authorize one budgeted provider request.')
            if name == 'evaluate':
                command.add_argument('--input-file', required=True)

    def handle(self, *args, **options):
        try:
            result = self.run(options)
        except (Unavailable, DatabaseError) as error:
            reason = str(error) if isinstance(error, Unavailable) else 'accounting_unavailable'
            raise CommandError(reason) from None
        self.stdout.write(json.dumps(result, indent=2, default=str))

    def run(self, options):
        action = options['action']
        if action == 'configure':
            service.configure(read_json(options['path']))
            return {'configured': True, 'enabled': False}
        if action in ('enable', 'disable'):
            service.set_enabled(action == 'enable')
            return {'enabled': action == 'enable'}
        if action == 'request':
            try:
                request = InferenceRequest.objects.get(pk=options['request_id'])
            except InferenceRequest.DoesNotExist:
                raise CommandError('Request not found.') from None
            result = {key: getattr(request, key) for key in (
                'id', 'status', 'purpose', 'created_at', 'expires_at', 'reservation_micro',
                'charged_micro', 'usage', 'error_code')}
            if options['result']:
                result['result'] = request.result
            return result
        control = InferenceControl.objects.filter(pk=1).first()
        if action == 'status':
            if control is None:
                return {'enabled': False, 'reason': 'unconfigured'}
            try:
                service._available(control, timezone.now())
                availability = 'available'
            except Unavailable as error:
                availability = str(error)
            day = timezone.now().astimezone(datetime_timezone.utc).date()
            return {'enabled': control.enabled, 'control_availability': availability,
                'generation': control.generation, 'configuration': control.configuration,
                'circuit_reason': control.circuit_reason, 'circuit_until': control.circuit_until,
                'pending_requests': InferenceRequest.objects.filter(status__in=service.ACTIVE).count(),
                'current_installation_buckets': list(BudgetBucket.objects.filter(
                    Q(scope='installation:day', period=day) |
                    Q(scope='installation:month', period=day.replace(day=1))).order_by('scope').values(
                        'scope', 'period', 'spent_micro', 'reserved_micro', 'requests'))}
        if control is None:
            raise CommandError('Configure the integration first.')
        configuration = validate_configuration(control.configuration)
        if options['profile'] not in configuration['profiles']:
            raise CommandError('Profile is not configured.')
        profile = configuration['profiles'][options['profile']]
        payload = {'instructions': 'Reply briefly with a greeting in the required JSON format.',
                   'messages': [{'role': 'user', 'content': 'Hello.'}]}
        if action == 'evaluate':
            payload = read_json(options['input_file'])
            if not isinstance(payload, dict) or set(payload) != {'instructions', 'messages'}:
                raise CommandError('Input must contain only instructions and messages.')
        validate_payload(payload['instructions'], payload['messages'], profile)
        if not options['execute']:
            return {'dry_run': True, 'maximum_cost_micro': maximum_cost(profile),
                    'note': 'Use --execute to reserve funds and queue one request.'}
        admission = service.submit(request_id=options['request_id'] or uuid.uuid4(),
            user_id=options['user_id'], world_id=options['world_id'],
            purpose='mob_evaluation', profile=options['profile'], **payload)
        if admission.reason:
            raise CommandError(admission.reason)
        return {'request_id': admission.request_id, 'maximum_cost_micro': maximum_cost(profile)}
