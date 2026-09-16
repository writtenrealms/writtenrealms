from copy import deepcopy

from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.world_export import manifest_stream_to_yaml, serialize_world_export_payload
from core.combat_formulas import get_world_combat_system, normalize_combat_system
from core.equipment_system import get_world_equipment_system, normalize_equipment_system
from core.stat_system import get_world_stat_system, normalize_stat_system
from worlds.models import World


class WorldSystemExportTests(APITestCase):
    system_fields = {'stats': 'stat_system', 'combat': 'combat_system', 'equipment': 'equipment_system'}

    def setUp(self):
        self.user = get_user_model().objects.create_user('world-systems-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        create_currency(world=self.source, code='obol', name='Obol')
        self.custom = {
            'equipment': {
                'armor_classes': [{'key': 'heavy', 'armor_multiplier': 1.35}],
                'default_armor_class': 'heavy',
                'offhand_weapons': {'default_allowed': True},
            },
            'stats': {
                'attributes': [{'key': 'strength'}],
                'class_profiles': {
                    'hoplite': {
                        'main_attribute': 'strength', 'attribute_weights': {'strength': 2},
                        'armor_proficiencies': ['heavy'],
                    },
                },
            },
            'combat': {'variance': {'enabled': False, 'percent': 0}},
        }
        self.defaults = {
            'stats': normalize_stat_system({}),
            'combat': normalize_combat_system({}),
            'equipment': normalize_equipment_system({}),
        }
        self._patch(self.source, self.custom)

    def _apply(self, world, documents):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )

    def _patch(self, world, spec):
        response = self._apply(world, [{'kind': 'world', 'spec': spec}])
        self.assertEqual(response.status_code, 200, response.data)
        world.refresh_from_db()

    def _target(self):
        response = self.client.post(reverse('builder-world-list'), {
            'name': 'Destination', 'is_multiplayer': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        target = World.objects.get(pk=response.data['id'])
        self.assertNotEqual(target.pk, self.source.pk)
        return target

    def _assert_systems(self, world, expected):
        world.refresh_from_db()
        for key, field in self.system_fields.items():
            self.assertEqual(getattr(world.config, field), expected[key], field)
        self.assertEqual(world.config.is_classless, not bool(expected['stats']['class_profiles']))

    def _round_trip(self, *, bundle):
        if bundle:
            create_instance_template(
                base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
            )
        target = self._target()
        for reset in (False, True):
            if reset:
                # Empty storage is a valid representation of each system's defaults.
                for field in self.system_fields.values():
                    setattr(self.source.config, field, {})
                self.source.config.save(update_fields=list(self.system_fields.values()))
            expected = self.defaults if reset else {
                key: deepcopy(getattr(self.source.config, field))
                for key, field in self.system_fields.items()
            }
            documents = serialize_world_export_payload(self.source)['documents']
            for document in documents:
                if document['kind'] != 'world':
                    continue
                is_base = document.get('metadata', {}).get('world_ref', 'world@base') == 'world@base'
                for key in self.system_fields:
                    if is_base:
                        self.assertEqual(document['spec'].get(key), expected[key], key)
                    else:
                        self.assertNotIn(key, document['spec'])
            for _ in range(2):
                response = self._apply(target, documents)
                self.assertEqual(response.status_code, 200, response.data)
                self._assert_systems(target, expected)
                if bundle:
                    instance = World.objects.select_related('config', 'instance_of__config').get(
                        instance_of=target, instance_slug='outpost',
                    )
                    with self.assertNumQueries(0):
                        self.assertEqual(get_world_stat_system(instance), expected['stats'])
                        self.assertEqual(get_world_combat_system(instance), expected['combat'])
                        self.assertEqual(get_world_equipment_system(instance), expected['equipment'])
            self.assertEqual(serialize_world_export_payload(target)['documents'], documents)

    def test_world_systems_survive_fresh_import_reimport_and_reset_to_defaults(self):
        self._round_trip(bundle=False)

    def test_family_systems_reset_at_base_and_remain_inherited(self):
        self._round_trip(bundle=True)

    def test_partial_edits_preserve_omitted_systems_and_accept_explicit_resets(self):
        for empty in ({}, None):
            self._patch(self.source, self.custom)
            expected = {
                key: deepcopy(getattr(self.source.config, field))
                for key, field in self.system_fields.items()
            }
            for key in self.system_fields:
                self._patch(self.source, {key: empty})
                expected[key] = self.defaults[key]
                self._assert_systems(self.source, expected)

    def test_equipment_suggestions_export_without_armor_classes_or_offhand_policy(self):
        raw = {'armor_suggestions': {'full_set_scale': 0.75}}
        self.source.config.equipment_system = raw
        self.source.config.save(update_fields=['equipment_system'])
        response = self.client.get(reverse('builder-world-export', args=[self.source.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['documents'][-1]['spec'].get('equipment'), normalize_equipment_system(raw))
        target = self._target()
        response = self._apply(target, response.data['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        self.assertEqual(target.config.equipment_system, normalize_equipment_system(raw))

    def test_invalid_stored_systems_fail_config_and_full_export_with_field_error(self):
        for key, field in self.system_fields.items():
            original = deepcopy(getattr(self.source.config, field))
            for invalid in ([], {'unsupported_field': True}):
                setattr(self.source.config, field, invalid)
                self.source.config.save(update_fields=[field])
                for endpoint in ('builder-world-config', 'builder-world-export'):
                    with self.subTest(system=key, value=invalid, endpoint=endpoint):
                        response = self.client.get(reverse(endpoint, args=[self.source.pk]))
                        self.assertEqual(response.status_code, 400, response.data)
                        self.assertIn(key, str(response.data))
                        self.assertNotIn('yaml', response.data)
            setattr(self.source.config, field, original)
            self.source.config.save(update_fields=[field])

    def test_invalid_equipment_reference_in_stats_fails_export(self):
        self.source.config.stat_system['class_profiles']['hoplite']['armor_proficiencies'] = ['missing']
        self.source.config.save(update_fields=['stat_system'])
        response = self.client.get(reverse('builder-world-export', args=[self.source.pk]))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('armor_proficiencies', str(response.data))

    def test_explicit_system_reset_can_repair_invalid_stored_equipment(self):
        self.source.config.equipment_system = {'unsupported_field': True}
        self.source.config.save(update_fields=['equipment_system'])
        self._patch(self.source, {key: {} for key in self.system_fields})
        self._assert_systems(self.source, self.defaults)

    def test_failed_batch_rolls_back_system_resets(self):
        expected = {
            key: deepcopy(getattr(self.source.config, field))
            for key, field in self.system_fields.items()
        }
        response = self._apply(self.source, [
            {'kind': 'world', 'spec': {key: {} for key in self.system_fields}},
            {'kind': 'world', 'spec': {'combat': {'unsupported_field': True}}},
        ])
        self.assertEqual(response.status_code, 400, response.data)
        self._assert_systems(self.source, expected)
