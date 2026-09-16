from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.manifests import apply_mob_definition_manifest, parse_mob_definition_manifest
from builders.mob_definitions import mob_definition_property_fields, sync_spawned_mobs_from_definition
from builders.models import Faction, MobDefinition
from builders.world_export import _serialize_mob_definition_manifest, manifest_stream_to_yaml, serialize_world_export_payload
from spawns.models import Mob
from worlds.models import World


class MobRelationExportTests(APITestCase):
    properties = {
        'health_max': 80, 'attack_power': 7, 'weapon_damage': 9,
        'is_invisible': True, 'target_priority': 5, 'aggression': 'all',
        'hit_msg_first': 'slash', 'hit_msg_third': 'slashes',
        'control_flag': 'north', 'fights_back': False,
    }

    def setUp(self):
        self.user = get_user_model().objects.create_user('mob-relation-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(name='Phalanx', author=self.user, is_multiplayer=True)
        create_currency(world=self.source, code='obol', name='Obol')
        create_currency(world=self.source, code='silver', name='Silver')
        for code, kind in (('persian', 'core'), ('watch', 'reputation'), ('observers', 'reputation')):
            Faction.objects.create(world=self.source, code=code, name=code.title(), type=kind)
        self._patch(self.source, {
            **self.properties, 'rewards': {'currencies': {'obol': 7, 'silver': 2}},
            'factions': {'core': 'persian', 'reputation': {'watch': -20}},
        })
        self.definition = self.source.mob_definitions.get(slug='guard')

    def _apply(self, world, documents):
        return self.client.post(reverse('builder-world-manifest-apply', args=[world.pk]), {
            'manifest': manifest_stream_to_yaml(documents),
        }, format='json')

    def _patch(self, world, spec):
        response = self._apply(world, [{
            'kind': 'mobdefinition', 'metadata': {'slug': 'guard', 'name': 'a guard'}, 'spec': spec,
        }])
        self.assertIn(response.status_code, (200, 201), response.data)

    def _target(self):
        response = self.client.post(reverse('builder-world-list'), {
            'name': 'Destination', 'is_multiplayer': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        target = World.objects.get(pk=response.data['id'])
        self.assertNotEqual(target.pk, self.source.pk)
        return target

    def _relations(self, definition):
        return {
            'rewards': dict(definition.currency_rewards.values_list('currency__code', 'amount')),
            'factions': dict(definition.faction_assignments.values_list('faction__code', 'value')),
        }

    def _clear_source(self):
        self.definition.base_properties = {}
        self.definition.save(update_fields=['base_properties'], sync_spawned=False)
        self.definition.currency_rewards.all().delete()
        self.definition.faction_assignments.all().delete()

    def _round_trip(self, *, bundle):
        if bundle:
            create_instance_template(base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost')
        target = self._target()
        documents = serialize_world_export_payload(self.source)['documents']
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug='guard')
        self.assertEqual(imported.base_properties, self.properties)
        self.assertEqual(self._relations(imported), self._relations(self.definition))
        scopes = [target]
        if bundle:
            scopes.append(World.objects.get(instance_of=target, instance_slug='outpost'))
        mobs = []
        for scope in scopes:
            runtime = scope.spawned_worlds.filter(is_multiplayer=True).first() or scope.create_spawn_world()
            mob = imported.spawn(scope.config.starting_room, runtime)
            self.assertEqual(mob.currency_reward_snapshot, {'obol': 7, 'silver': 2})
            mob.faction_assignments.create(
                faction=target.world_factions.get(code='observers'), value=77, source='quest',
            )
            mob.health = 8
            mob.save(update_fields=['health'])
            mobs.append(mob)
        relation_ids = (
            list(imported.currency_rewards.values_list('pk', flat=True)),
            list(imported.faction_assignments.values_list('pk', flat=True)),
        )
        with patch('builders.mob_definitions.sync_spawned_mobs_from_definition') as sync:
            response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        sync.assert_not_called()
        self.assertEqual(relation_ids, (
            list(imported.currency_rewards.values_list('pk', flat=True)),
            list(imported.faction_assignments.values_list('pk', flat=True)),
        ))
        for mob in mobs:
            mob.refresh_from_db()
            self.assertEqual(mob.health, 8)

        self._clear_source()
        documents = serialize_world_export_payload(self.source)['documents']
        spec = next(doc['spec'] for doc in documents if doc['kind'] == 'mobdefinition')
        self.assertEqual(spec['rewards'], {'currencies': {}})
        self.assertEqual(spec['factions'], {})
        for field in mob_definition_property_fields():
            self.assertIn(field, spec)
            self.assertIsNone(spec[field], field)
        for expected_syncs in (1, 0):
            with patch('builders.mob_definitions.sync_spawned_mobs_from_definition', wraps=sync_spawned_mobs_from_definition) as sync:
                response = self._apply(target, documents)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(sync.call_count, expected_syncs)
            imported.refresh_from_db()
            self.assertEqual(imported.base_properties, {})
            self.assertEqual(self._relations(imported), {'rewards': {}, 'factions': {}})
            for mob in mobs:
                mob.refresh_from_db()
                for field in self.properties:
                    self.assertEqual(getattr(mob, field), Mob._meta.get_field(field).get_default(), field)
                self.assertEqual(mob.currency_reward_snapshot, {})
                self.assertEqual(dict(mob.faction_assignments.values_list('faction__code', 'value')), {'observers': 77})
                self.assertEqual(mob.health, 30 if expected_syncs else 8)
                mob.health = 8
                mob.save(update_fields=['health'])
        replacement = imported.spawn(mobs[0].room, mobs[0].world)
        self.assertEqual(replacement.currency_reward_snapshot, {})
        self.assertFalse(replacement.faction_assignments.exists())
        self.assertEqual(replacement.health_max, 30)
        target.refresh_from_db()
        self.assertEqual(serialize_world_export_payload(target)['documents'], documents)

    def test_world_reimport_clears_relations_and_base_properties(self):
        self._round_trip(bundle=False)

    def test_family_reimport_updates_live_mobs_and_preserves_runtime_faction_overrides(self):
        self._round_trip(bundle=True)

    def test_partial_edits_preserve_omissions_and_reset_only_explicit_properties(self):
        before = self._relations(self.definition)
        self._patch(self.source, {'notes': 'Updated.'})
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.base_properties, self.properties)
        self.assertEqual(self._relations(self.definition), before)
        for spec, fields in (
            ({'health_max': None, 'hit_msg_first': None}, ['health_max', 'hit_msg_first']),
            ({'combat': {'health': None, 'target_priority': None, 'aggression': None}}, ['target_priority', 'aggression']),
        ):
            self._patch(self.source, spec)
            self.definition.refresh_from_db()
            for field in fields:
                self.assertNotIn(field, self.definition.base_properties)
            self.assertEqual(self.definition.base_properties['attack_power'], 7)
            self.assertEqual(self._relations(self.definition), before)
        self._patch(self.source, {'rewards': {'currencies': {'silver': 2}}})
        self.assertEqual(self._relations(self.definition), {**before, 'rewards': {'silver': 2}})
        self._patch(self.source, {'factions': {'reputation': {'watch': 0}}})
        self.assertEqual(self._relations(self.definition), {'rewards': {'silver': 2}, 'factions': {'watch': 0}})
        self._patch(self.source, {'rewards': {'currencies': {}}, 'factions': {}})
        self.assertEqual(self._relations(self.definition), {'rewards': {}, 'factions': {}})

    def test_empty_factions_clear_all_authored_definition_assignments(self):
        self.definition.faction_assignments.update(source='')
        self._patch(self.source, {'factions': {}})
        self.assertFalse(self.definition.faction_assignments.exists())

    def test_invalid_relations_reject_property_clears_without_partial_changes(self):
        before = self._relations(self.definition)
        for invalid in (
            {'rewards': {'currencies': {'obol': -1}}},
            {'rewards': {'currencies': {'missing': 1}}},
            {'factions': {'core': 'watch'}},
            {'factions': {'reputation': {'persian': 10}}},
            {'factions': {'reputation': {'missing': 10}}},
            {'factions': {'reputation': {'watch': True}}},
            {'factions': {'reputation': {'watch': 2147483648}}},
            {'factions': {'reputation': {'watch': -2147483649}}},
        ):
            with self.subTest(invalid=invalid):
                response = self._apply(self.source, [{
                    'kind': 'mobdefinition', 'metadata': {'slug': 'guard'},
                    'spec': {'health_max': None, **invalid},
                }])
                self.assertEqual(response.status_code, 400, response.data)
                self.definition.refresh_from_db()
                self.assertEqual(self.definition.base_properties, self.properties)
                self.assertEqual(self._relations(self.definition), before)

    def test_null_properties_export_and_import_without_materializing_defaults(self):
        self.definition.base_properties = {'health_max': None, 'target_priority': None, 'is_invisible': False, 'attack_power': 0}
        self.definition.save(update_fields=['base_properties'], sync_spawned=False)
        documents = [_serialize_mob_definition_manifest(self.definition)]
        self.assertIsNone(documents[0]['spec']['health_max'])
        self.assertIsNone(documents[0]['spec']['target_priority'])
        response = self._apply(self.source, documents)
        self.assertEqual(response.status_code, 200, response.data)
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.base_properties, {'is_invisible': False, 'attack_power': 0})
        self.assertEqual(_serialize_mob_definition_manifest(self.definition), documents[0])

    def test_failed_batch_rolls_back_relation_and_live_mob_changes(self):
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug='guard')
        before = self._relations(imported)
        runtime = target.spawned_worlds.filter(is_multiplayer=True).get()
        mob = imported.spawn(target.config.starting_room, runtime)
        self._clear_source()
        documents = serialize_world_export_payload(self.source)['documents']
        documents.append({'kind': 'world', 'spec': {'unsupported_setting': True}})
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 400, response.data)
        imported.refresh_from_db()
        self.assertEqual(imported.base_properties, self.properties)
        self.assertEqual(self._relations(imported), before)
        mob.refresh_from_db()
        self.assertEqual(mob.health_max, 80)
        self.assertEqual(mob.currency_reward_snapshot, before['rewards'])
        self.assertEqual(dict(mob.faction_assignments.values_list('faction__code', 'value')), before['factions'])

    def test_empty_export_uses_prefetched_relations_without_queries(self):
        self._clear_source()
        definition = MobDefinition.objects.select_related(
            'merchant_profile', 'crafting_profile', 'trainer_profile',
        ).prefetch_related('currency_rewards__currency', 'faction_assignments__faction').get(pk=self.definition.pk)
        with self.assertNumQueries(0):
            spec = _serialize_mob_definition_manifest(definition)['spec']
        self.assertEqual(spec['rewards'], {'currencies': {}})
        self.assertEqual(spec['factions'], {})

    def test_unchanged_relation_apply_has_bounded_queries_and_no_relation_writes(self):
        counts = []
        for size in (1, 24):
            reputations = {}
            for index in range(size):
                faction, _ = Faction.objects.get_or_create(
                    world=self.source, code=f'group_{index}', defaults={'name': f'Group {index}', 'type': 'reputation'},
                )
                reputations[faction.code] = index
            spec = {'factions': {'reputation': reputations}, 'rewards': {'currencies': {'obol': 7}}}
            self._patch(self.source, spec)
            parsed = parse_mob_definition_manifest(world=self.source, manifest={
                'kind': 'mobdefinition', 'metadata': {'slug': 'guard'}, 'spec': spec,
            })
            with patch('builders.mob_definitions.sync_spawned_mobs_from_definition') as sync, CaptureQueriesContext(connection) as queries:
                apply_mob_definition_manifest(parsed)
            sync.assert_not_called()
            counts.append(len(queries))
            for query in queries:
                sql = query['sql'].upper()
                if any(table in sql for table in ('BUILDERS_FACTIONASSIGNMENT', 'BUILDERS_MOBCURRENCYREWARD')):
                    self.assertTrue(sql.startswith('SELECT'), sql)
        self.assertEqual(counts[0], counts[1], counts)
