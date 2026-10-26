import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from builders.models import MobConversationPolicy
from spawns.conversations.policy import configure
from worlds.models import World


class Command(BaseCommand):
    help = 'Configure the bounded humanoid conversation pilot for one authored world.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['configure', 'disable', 'status'])
        parser.add_argument('--world-id', type=int, required=True)
        parser.add_argument('--input-file')
        parser.add_argument('--enable', action='store_true')

    def handle(self, *args, **options):
        world_id = options['world_id']
        if options['action'] == 'configure':
            try:
                if not options['input_file']:
                    raise ValueError('Configure requires --input-file.')
                with Path(options['input_file']).open('rb') as handle:
                    raw = handle.read(32769)
                if len(raw) > 32768:
                    raise ValueError('Pilot configuration exceeds 32 KiB.')
                configure(world_id, json.loads(raw), enabled=options['enable'])
            except (ValueError, OSError, World.DoesNotExist) as error:
                raise CommandError(str(error)) from None
        elif options['action'] == 'disable':
            MobConversationPolicy.objects.filter(world_id=world_id).update(enabled=False)
        policy = MobConversationPolicy.objects.filter(world_id=world_id).first()
        self.stdout.write(json.dumps({'world_id': world_id, 'enabled': bool(policy and policy.enabled),
            'revision': str(policy.revision) if policy else None,
            'mob_slugs': list(policy.mobs) if policy else []}, indent=2))
