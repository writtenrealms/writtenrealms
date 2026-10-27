import json

from django.core.management.base import BaseCommand, CommandError
from builders.models import MobConversationPolicy, MobDefinition
from spawns.conversations.policy import set_enabled
from worlds.models import World


class Command(BaseCommand):
    help = 'Enable, disable, or inspect dialogue for an authored world. Author knowledge in world/mob YAML.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['enable', 'disable', 'status'])
        parser.add_argument('--world-id', type=int, required=True)

    def handle(self, *args, **options):
        world_id = options['world_id']
        if options['action'] in ('enable', 'disable'):
            try:
                set_enabled(world_id, options['action'] == 'enable')
            except World.DoesNotExist as error:
                raise CommandError(str(error)) from None
        policy = MobConversationPolicy.objects.filter(world_id=world_id).first()
        self.stdout.write(json.dumps({'world_id': world_id, 'enabled': bool(policy and policy.enabled),
            'revision': str(policy.revision) if policy else None,
            'mob_slugs': list(MobDefinition.objects.filter(world_id=world_id, conversation__isnull=False)
                              .order_by('slug').values_list('slug', flat=True))}, indent=2))
