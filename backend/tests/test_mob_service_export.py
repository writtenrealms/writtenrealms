from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.mob_definitions import sync_spawned_mobs_from_definition
from builders.models import CraftingProfile, MerchantProfile, MobDefinition, TrainerProfile
from builders.world_export import (
    _serialize_mob_definition_manifest, manifest_stream_to_yaml, serialize_world_export_payload,
)
from spawns.crafting import available_crafting_providers
from spawns.merchants import available_merchant_providers
from spawns.models import Player
from spawns.trainers import available_training_providers
from worlds.models import World


class MobServiceExportTests(APITestCase):
    services = ('merchant', 'crafting', 'trainer')
    provider_functions = (
        available_merchant_providers, available_crafting_providers, available_training_providers,
    )

    def setUp(self):
        self.user = get_user_model().objects.create_user('mob-service-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        currency = create_currency(world=self.source, code='obol', name='Obol')
        self.profiles = {
            'merchant': MerchantProfile.objects.create(
                world=self.source, slug='market', name='Market', settlement_currency=currency,
            ),
            'crafting': CraftingProfile.objects.create(
                world=self.source, slug='forge', name='Forge',
            ),
            'trainer': TrainerProfile.objects.create(
                world=self.source, slug='training', name='Training',
            ),
        }
        self.definition = MobDefinition.objects.create(
            world=self.source, slug='artisan', name='an artisan',
            base_properties={'health_max': 20},
        )

    def _set_services(self, *, attached, availability='alive_and_present'):
        fields = []
        for service in self.services:
            field = f'{service}_profile'
            setattr(self.definition, field, self.profiles[service] if attached else None)
            setattr(self.definition, f'{service}_availability', availability)
            fields.extend([field, f'{service}_availability'])
        self.definition.save(update_fields=fields)

    def _apply(self, world, documents):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )

    def _target(self):
        response = self.client.post(
            reverse('builder-world-list'),
            {'name': 'Destination', 'is_multiplayer': True}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return World.objects.get(pk=response.data['id'])

    def _spawn_provider(self, definition, world):
        runtime_world = world.spawned_worlds.filter(is_multiplayer=True).first()
        if runtime_world is None:
            runtime_world = world.create_spawn_world()
        room = world.config.starting_room
        mob = definition.spawn(room, runtime_world)
        player = Player.objects.create(
            name='Customer', user=self.user, world=runtime_world, room=room,
        )
        for discover in self.provider_functions:
            self.assertEqual(len(discover(player)), 1)
        return player, mob.merchant_runtime

    def _assert_cleared(self, definition, *, availability):
        definition.refresh_from_db()
        for service in self.services:
            self.assertIsNone(getattr(definition, f'{service}_profile_id'))
            self.assertEqual(getattr(definition, f'{service}_availability'), availability)

    def _round_trip(self, *, bundle):
        if bundle:
            create_instance_template(
                base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
            )
        self._set_services(attached=True)
        target = self._target()
        self.assertNotEqual(target.pk, self.source.pk)
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug=self.definition.slug)
        self.assertNotEqual(imported.pk, self.definition.pk)
        for service in self.services:
            profile = getattr(imported, f'{service}_profile')
            self.assertEqual(profile.slug, self.profiles[service].slug)
            self.assertEqual(profile.world_id, target.pk)
            self.assertEqual(getattr(imported, f'{service}_availability'), 'alive_and_present')

        worlds = [target]
        if bundle:
            worlds.append(World.objects.get(instance_of=target, instance_slug='outpost'))
        providers = [self._spawn_provider(imported, world) for world in worlds]

        self._set_services(attached=False, availability='present')
        documents = serialize_world_export_payload(self.source)['documents']
        for expected_syncs in (1, 0):
            with patch(
                'builders.mob_definitions.sync_spawned_mobs_from_definition',
                wraps=sync_spawned_mobs_from_definition,
            ) as sync:
                response = self._apply(target, documents)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(sync.call_count, expected_syncs)
            self._assert_cleared(imported, availability='present')
            for player, runtime in providers:
                runtime.refresh_from_db()
                self.assertFalse(runtime.is_active)
                for discover in self.provider_functions:
                    self.assertEqual(discover(player), [])
        target.refresh_from_db()
        self.assertEqual(serialize_world_export_payload(target)['documents'], documents)

        # Empty profile references must also work when the mob does not exist yet.
        fresh_target = self._target()
        response = self._apply(fresh_target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        self._assert_cleared(
            fresh_target.mob_definitions.get(slug=self.definition.slug), availability='present',
        )
        fresh_target.refresh_from_db()
        self.assertEqual(serialize_world_export_payload(fresh_target)['documents'], documents)

    def test_world_export_reimport_clears_removed_mob_services(self):
        self._round_trip(bundle=False)

    def test_family_export_reimport_removes_providers_in_base_and_instance_runtimes(self):
        self._round_trip(bundle=True)

    def test_partial_edits_preserve_omitted_profiles_and_clear_only_explicit_references(self):
        for service in self.services:
            with self.subTest(service=service):
                self._set_services(attached=True)
                for spec, cleared in (
                    ({'description': 'Updated description.'}, False),
                    ({service: {'availability': 'present'}}, False),
                    ({service: {'profile': None}}, True),
                ):
                    response = self._apply(self.source, [{
                        'kind': 'mobdefinition', 'metadata': {'slug': self.definition.slug},
                        'spec': spec,
                    }])
                    self.assertEqual(response.status_code, 200, response.data)
                    self.definition.refresh_from_db()
                    self.assertEqual(self.definition.description, 'Updated description.')
                    for field in self.services:
                        self.assertEqual(
                            getattr(self.definition, f'{field}_profile_id'),
                            None if cleared and field == service else self.profiles[field].pk,
                        )
                    if cleared:
                        self.assertEqual(getattr(self.definition, f'{service}_availability'), 'present')

    def test_later_import_failure_restores_attachments_and_runtime_providers(self):
        self._set_services(attached=True)
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        imported = target.mob_definitions.get(slug=self.definition.slug)
        before = tuple(getattr(imported, f'{service}_profile_id') for service in self.services)
        player, runtime = self._spawn_provider(imported, target)
        self._set_services(attached=False)
        documents = serialize_world_export_payload(self.source)['documents']
        documents.append({'kind': 'world', 'spec': {'unsupported_setting': True}})
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('unsupported_setting', str(response.data))
        imported.refresh_from_db()
        self.assertEqual(
            tuple(getattr(imported, f'{service}_profile_id') for service in self.services), before,
        )
        runtime.refresh_from_db()
        self.assertTrue(runtime.is_active)
        for discover in self.provider_functions:
            self.assertEqual(len(discover(player)), 1)

    def test_export_uses_preloaded_profiles_without_additional_queries(self):
        for attached in (True, False):
            with self.subTest(attached=attached):
                self._set_services(attached=attached)
                definition = MobDefinition.objects.select_related(
                    'merchant_profile', 'crafting_profile', 'trainer_profile',
                ).prefetch_related('currency_rewards__currency', 'faction_assignments__faction').get(
                    pk=self.definition.pk,
                )
                with self.assertNumQueries(0):
                    document = _serialize_mob_definition_manifest(definition)
                for service in self.services:
                    self.assertEqual(document['spec'][service], {
                        'profile': f'{service}profile.{self.profiles[service].slug}' if attached else None,
                        'availability': 'alive_and_present',
                    })
