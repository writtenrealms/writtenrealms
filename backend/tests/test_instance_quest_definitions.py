import yaml

from rest_framework.reverse import reverse

from builders.instance_templates import create_instance_template
from builders.models import ItemDefinition, MobDefinition
from quests.entity_refs import resolve_entity_ref_id
from quests.models import QuestInstance, QuestTemplate
from tests.base import WorldTestCase
from tests.combat_fixtures import dispatch_and_drain_combat
from tests.utils import apply_basic_stat_system, capture_game_messages
from worlds.models import World


class InstanceQuestDefinitionTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.template = create_instance_template(
            base_world=self.world, author=self.user, name='Introduction',
        )
        self.guard = MobDefinition.objects.create(
            world=self.world, slug='guard', name='a guard', keywords='guard',
            attackable=False,
        )
        self.dummy = MobDefinition.objects.create(
            world=self.world, slug='dummy', name='a dummy', keywords='dummy',
            base_properties={'health_max': 1, 'fights_back': False},
        )
        self.token = ItemDefinition.objects.create(
            world=self.world, slug='token', name='a token', item_type='quest',
        )

    def test_inherited_definition_lookup_uses_one_indexed_query(self):
        for expected_type, entity in (
            ('mobdefinition', self.guard), ('itemdefinition', self.token),
        ):
            template = World.objects.get(pk=self.template.pk)
            with self.subTest(kind=expected_type), self.assertNumQueries(1):
                self.assertEqual(resolve_entity_ref_id(
                    world=template, value=f'{expected_type}.{entity.slug}',
                    expected_type=expected_type,
                ), entity.pk)

        runtime = self.template.create_spawn_world()
        runtime = World.objects.select_related('context').get(pk=runtime.pk)
        with self.assertNumQueries(1):
            self.assertEqual(resolve_entity_ref_id(
                world=runtime, value='mobdefinition.guard',
                expected_type='mobdefinition',
            ), self.guard.pk)

    def test_quest_refs_stay_local_and_other_families_are_not_visible(self):
        base_quest = QuestTemplate.objects.create(
            world=self.world, slug='introduction', name='Base quest',
        )
        local_quest = QuestTemplate.objects.create(
            world=self.template, slug='introduction', name='Local quest',
        )
        self.assertNotEqual(base_quest.pk, local_quest.pk)
        self.assertEqual(resolve_entity_ref_id(
            world=self.template, value='questtemplate.introduction',
            expected_type='questtemplate',
        ), local_quest.pk)
        other_world = World.objects.new_world(name='Other family', author=self.user)
        MobDefinition.objects.create(
            world=other_world, slug='unrelated-guard', name='another guard',
        )
        self.assertIsNone(resolve_entity_ref_id(
            world=self.template, value='mobdefinition.unrelated-guard',
            expected_type='mobdefinition',
        ))

    def test_instance_quest_import_discovery_combat_and_report_use_base_mobs(self):
        manifest = {
            'kind': 'quest',
            'metadata': {'slug': 'practice', 'name': 'Practice'},
            'spec': {
                'status': 'active',
                'discovery': {'sources': [
                    {'type': 'npc_dialogue', 'mob_definition': 'mobdefinition.guard'},
                ]},
                'steps': [
                    {
                        'id': 'fight', 'kind': 'objective',
                        'text': {'body': 'Break the dummy, then report to the guard.'},
                        'objectives': [{
                            'id': 'dummy',
                            'tracker': {
                                'event': 'quest.mob.killed',
                                'where': {'eq': ['event.target.definition_id', 'mobdefinition.dummy']},
                            },
                        }],
                        'transitions': [{'when': {'objective_complete': 'dummy'}, 'goto': 'report'}],
                    },
                    {
                        'id': 'report', 'kind': 'objective',
                        'objectives': [{
                            'id': 'guard',
                            'tracker': {
                                'event': 'cmd.talk.success',
                                'where': {'eq': ['event.target.definition_id', 'mobdefinition.guard']},
                            },
                        }],
                        'transitions': [{'when': {'objective_complete': 'guard'}, 'goto': 'done'}],
                    },
                    {'id': 'done', 'kind': 'resolution', 'recap': 'You may leave.'},
                ],
                'rewards': {'complete': [{
                    'type': 'mob_command', 'mob_definition': 'mobdefinition.guard',
                    'command': 'say You are ready.',
                }]},
            },
        }
        response = self.client.post(
            reverse('builder-world-manifest-apply', args=[self.template.pk]),
            {'manifest': yaml.safe_dump(manifest)}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        runtime = self.template.create_spawn_world()
        room = self.template.config.starting_room
        self.guard.spawn(room, runtime)
        self.dummy.spawn(room, runtime)
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = 0
        self.world.config.save(update_fields=['combat_resolution_interval'])
        self.player.world = runtime
        self.player.room = room
        self.player.in_game = True
        self.player.stamina = 100
        self.player.save(update_fields=['world', 'room', 'in_game', 'stamina'])
        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, 'talk guard')
        self.assertIn('quest accept practice', str(messages))
        with capture_game_messages():
            dispatch_and_drain_combat(self.player.pk, 'quest accept practice')
            dispatch_and_drain_combat(self.player.pk, 'kill dummy')
        instance = QuestInstance.objects.get(player=self.player, template__slug='practice')
        self.assertEqual(instance.current_step_id, 'report')
        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, 'talk guard')
        instance.refresh_from_db()
        self.assertEqual(instance.resolution, 'complete')
        self.assertIn('You are ready.', str(messages))
