from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.mob_definitions import sync_spawned_mobs_from_definition
from builders.models import AbilityDefinition, ItemDefinition, MobDefinition
from builders.world_export import (
    _serialize_mob_definition_manifest, manifest_stream_to_yaml, serialize_world_export_payload,
)
from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_snapshot, set_state_value
from worlds.models import World


class MobOptionalExportTests(APITestCase):
    empty_fields = {
        'loot': {}, 'traits': [], 'initial_state': {},
        'combat_abilities': [], 'combat_engage_when': {},
    }
    empty_spec = {
        'loot': {}, 'traits': [], 'initial_state': {},
        'combat': {'abilities': [], 'engage_when': {}},
    }

    def setUp(self):
        self.user = get_user_model().objects.create_user('mob-optional-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        create_currency(world=self.source, code='obol', name='Obol')
        ItemDefinition.objects.create(world=self.source, slug='sword', name='a sword')
        AbilityDefinition.objects.create(
            world=self.source, slug='strike', name='Strike', command_verbs=['strike'],
            target={'type': 'hostile', 'default': 'current_target', 'allow_out_of_combat': False},
            availability={'classes': [], 'min_level': 1}, requirements={}, cost={},
            cooldown={'rounds': 0}, components=[{'type': 'damage', 'profile': 'basic_physical'}],
        )
        self.full_spec = {
            'health_max': 20,
            'loot': {'entries': [{
                'slug': 'sword-drop', 'source': 'itemdefinition.sword', 'probability': 100,
            }]},
            'traits': [{'key': 'colossal', 'modifiers': {'health_max_multiplier': 2}}],
            'initial_state': {'captive': True},
            'combat': {
                'abilities': [{'ability': 'strike', 'weight': 1}],
                'engage_when': {'eq': ['state.character.captive', False]},
            },
        }
        response = self._patch_definition(self.source, self.full_spec)
        self.assertEqual(response.status_code, 201, response.data)
        self.definition = self.source.mob_definitions.get(slug='guard')
        self.populated_fields = {
            field: deepcopy(getattr(self.definition, field)) for field in self.empty_fields
        }

    def _apply(self, world, documents):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )

    def _patch_definition(self, world, spec):
        return self._apply(world, [{
            'kind': 'mobdefinition', 'metadata': {'slug': 'guard', 'name': 'a guard'},
            'spec': spec,
        }])

    def _target(self):
        response = self.client.post(
            reverse('builder-world-list'),
            {'name': 'Destination', 'is_multiplayer': True}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        target = World.objects.get(pk=response.data['id'])
        self.assertNotEqual(target.pk, self.source.pk)
        return target

    def _assert_fields(self, definition, expected):
        definition.refresh_from_db()
        for field, value in expected.items():
            self.assertEqual(getattr(definition, field), value, field)

    def _round_trip(self, *, bundle):
        if bundle:
            create_instance_template(
                base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
            )
        target = self._target()
        documents = serialize_world_export_payload(self.source)['documents']
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug='guard')
        self._assert_fields(imported, self.populated_fields)
        worlds = [target]
        if bundle:
            worlds.append(World.objects.get(instance_of=target, instance_slug='outpost'))
        mobs = []
        for world in worlds:
            runtime = world.spawned_worlds.filter(is_multiplayer=True).first()
            runtime = runtime or world.create_spawn_world()
            mob = imported.spawn(world.config.starting_room, runtime)
            self.assertEqual(mob.health_max, 40)
            self.assertEqual(get_state_snapshot(STATE_SCOPE_CHARACTER, mob), {'captive': True})
            set_state_value(STATE_SCOPE_CHARACTER, mob, 'captive', False)
            mobs.append(mob)

        response = self._patch_definition(self.source, self.empty_spec)
        self.assertEqual(response.status_code, 200, response.data)
        documents = serialize_world_export_payload(self.source)['documents']
        for expected_syncs in (1, 0):
            with patch(
                'builders.mob_definitions.sync_spawned_mobs_from_definition',
                wraps=sync_spawned_mobs_from_definition,
            ) as sync:
                response = self._apply(target, documents)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(sync.call_count, expected_syncs)
            self._assert_fields(imported, self.empty_fields)
            for mob in mobs:
                mob.refresh_from_db()
                self.assertEqual(mob.trait_instances, [])
                self.assertEqual(mob.health_max, 20)
                self.assertEqual(get_state_snapshot(STATE_SCOPE_CHARACTER, mob), {'captive': False})
        target.refresh_from_db()
        self.assertEqual(serialize_world_export_payload(target)['documents'], documents)
        for mob in mobs:
            replacement = imported.spawn(mob.room, mob.world)
            self.assertEqual(get_state_snapshot(STATE_SCOPE_CHARACTER, replacement), {})
            self.assertEqual(replacement.trait_instances, [])

        fresh_target = self._target()
        response = self._apply(fresh_target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        self._assert_fields(fresh_target.mob_definitions.get(slug='guard'), self.empty_fields)

    def test_world_reimport_clears_removed_optional_mob_settings(self):
        self._round_trip(bundle=False)

    def test_family_reimport_clears_removed_settings_and_preserves_live_state(self):
        self._round_trip(bundle=True)

    def test_partial_edits_clear_only_explicit_settings(self):
        for field, empty in self.empty_fields.items():
            with self.subTest(field=field):
                response = self._patch_definition(self.source, self.full_spec)
                self.assertEqual(response.status_code, 200, response.data)
                response = self._patch_definition(self.source, {'description': 'Updated.'})
                self.assertEqual(response.status_code, 200, response.data)
                self._assert_fields(self.definition, self.populated_fields)
                if field.startswith('combat_'):
                    spec = {'combat': {field.removeprefix('combat_'): empty}}
                else:
                    spec = {field: empty}
                response = self._patch_definition(self.source, spec)
                self.assertEqual(response.status_code, 200, response.data)
                self._assert_fields(self.definition, {**self.populated_fields, field: empty})

    def test_initial_state_only_clear_does_not_resync_existing_mobs(self):
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug='guard')
        runtime = target.spawned_worlds.filter(is_multiplayer=True).get()
        mob = imported.spawn(target.config.starting_room, runtime)
        response = self._patch_definition(self.source, {'initial_state': {}})
        self.assertEqual(response.status_code, 200, response.data)
        with patch('builders.mob_definitions.sync_spawned_mobs_from_definition') as sync:
            response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        sync.assert_not_called()
        self._assert_fields(imported, {**self.populated_fields, 'initial_state': {}})
        self.assertEqual(get_state_snapshot(STATE_SCOPE_CHARACTER, mob), {'captive': True})
        replacement = imported.spawn(mob.room, mob.world)
        self.assertEqual(get_state_snapshot(STATE_SCOPE_CHARACTER, replacement), {})

    def test_failed_import_restores_optional_settings_and_spawned_traits(self):
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug='guard')
        runtime = target.spawned_worlds.filter(is_multiplayer=True).get()
        mob = imported.spawn(target.config.starting_room, runtime)
        traits_before = deepcopy(mob.trait_instances)
        response = self._patch_definition(self.source, self.empty_spec)
        self.assertEqual(response.status_code, 200, response.data)
        documents = serialize_world_export_payload(self.source)['documents']
        documents.append({'kind': 'world', 'spec': {'unsupported_setting': True}})
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('unsupported_setting', str(response.data))
        self._assert_fields(imported, self.populated_fields)
        mob.refresh_from_db()
        self.assertEqual(mob.trait_instances, traits_before)
        self.assertEqual(mob.health_max, 40)

    def test_empty_settings_export_without_additional_queries(self):
        response = self._patch_definition(self.source, self.empty_spec)
        self.assertEqual(response.status_code, 200, response.data)
        definition = MobDefinition.objects.select_related(
            'merchant_profile', 'crafting_profile', 'trainer_profile',
        ).prefetch_related('currency_rewards__currency', 'faction_assignments__faction').get(
            pk=self.definition.pk,
        )
        with self.assertNumQueries(0):
            spec = _serialize_mob_definition_manifest(definition)['spec']
        for field in ('loot', 'traits', 'initial_state'):
            self.assertEqual(spec[field], self.empty_fields[field])
        self.assertEqual(spec['combat']['abilities'], [])
        self.assertEqual(spec['combat']['engage_when'], {})
