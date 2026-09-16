import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders import manifests, world_export
from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.models import Trigger, WorldBuilder
from worlds.models import Room, World


class TriggerIdentityTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('trigger-identity@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        create_currency(world=self.source, code='obol', name='Obol')
        self.room = self.source.config.starting_room

    def _trigger(self, **overrides):
        fields = {
            'world': self.source, 'name': 'Lever', 'scope': 'room', 'kind': 'command',
            'target_type': ContentType.objects.get_for_model(Room),
            'target_id': self.room.pk, 'match': 'pull lever', 'script': '/echo -- Click.',
        }
        fields.update(overrides)
        return Trigger.objects.create(**fields)

    def _document(self, trigger):
        document = manifests.trigger_to_manifest(trigger)
        for field in ('world', 'id', 'key'):
            document['metadata'].pop(field)
        return document

    def _apply(self, documents, world=None):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[(world or self.source).pk]),
            {'manifest': world_export.manifest_stream_to_yaml(documents)}, format='json',
        )

    def _target(self):
        response = self.client.post(
            reverse('builder-world-list'),
            {'name': 'Destination', 'is_multiplayer': True}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return World.objects.get(pk=response.data['id'])

    def test_family_round_trip_preserves_colliding_triggers_and_mutable_fields(self):
        first = self._trigger()
        second = self._trigger(match='push lever')
        third = self._trigger(script='/echo -- A second effect.')
        instance = create_instance_template(
            base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
        )
        # Identity is local to the declared world, so the same UID can coexist
        # in a different family scope without updating the base trigger.
        child = self._trigger(
            world=instance, uid=first.uid, target_id=instance.config.starting_room_id,
        )
        destination_room = Room.objects.create(
            world=self.source, zone=self.room.zone, name='New target', x=1, y=0, z=0,
        )
        target = self._target()
        documents = world_export.serialize_world_export_payload(self.source)['documents']
        for _ in range(2):
            response = self._apply(documents, target)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(target.triggers.count(), 3)
        imported = {trigger.uid: trigger.pk for trigger in target.triggers.all()}
        self.assertEqual(set(imported), {first.uid, second.uid, third.uid})
        self.assertTrue(set(imported.values()).isdisjoint({first.pk, second.pk, third.pk}))
        imported_instance = World.objects.get(instance_of=target, instance_slug='outpost')
        self.assertEqual(imported_instance.triggers.get().uid, child.uid)

        first.name = 'Renamed Lever'
        first.match = 'turn lever'
        first.target_id = destination_room.pk
        first.save()
        second.scope = 'world'
        second.target_type = ContentType.objects.get_for_model(World)
        second.target_id = self.source.pk
        second.save()
        documents = world_export.serialize_world_export_payload(self.source)['documents']
        for _ in range(2):
            response = self._apply(documents, target)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({t.uid: t.pk for t in target.triggers.all()}, imported)
        updated = target.triggers.get(uid=first.uid)
        self.assertEqual((updated.name, updated.match), ('Renamed Lever', 'turn lever'))
        self.assertEqual(updated.target.relative_id, destination_room.relative_id)
        self.assertNotEqual(updated.target_id, destination_room.pk)
        self.assertEqual(target.triggers.get(uid=second.uid).target_id, target.pk)
        self.assertEqual(imported_instance.triggers.get().name, 'Lever')
        target.refresh_from_db()
        self.assertEqual(world_export.serialize_world_export_payload(target)['documents'], documents)

    def test_uid_partial_update_and_delete(self):
        trigger = self._trigger()
        document = {
            'kind': 'trigger', 'metadata': {'uid': str(trigger.uid), 'name': 'New name'},
            'spec': {'match': 'turn lever'},
        }
        response = self._apply([document])
        self.assertEqual(response.status_code, 200, response.data)
        trigger.refresh_from_db()
        self.assertEqual(trigger.name, 'New name')
        self.assertEqual(trigger.match, 'turn lever')
        self.assertEqual(trigger.target_id, self.room.pk)
        exported = manifests.trigger_to_manifest(trigger)
        self.assertEqual(exported['metadata']['uid'], str(trigger.uid))
        self.assertEqual(manifests.trigger_delete_manifest(trigger)['metadata']['uid'], str(trigger.uid))
        response = self._apply([{
            'kind': 'trigger', 'operation': 'delete', 'metadata': {'uid': str(trigger.uid)},
        }])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Trigger.objects.filter(pk=trigger.pk).exists())
        response = self._apply([{
            'kind': 'trigger', 'operation': 'delete', 'metadata': {'uid': str(trigger.uid)},
        }])
        self.assertEqual(response.status_code, 400)
        self.assertIn('not found', str(response.data))

    def test_explicit_uid_never_adopts_a_similarly_named_trigger(self):
        trigger = self._trigger()
        document = self._document(trigger)
        document['metadata']['uid'] = str(uuid.uuid4())
        response = self._apply([document])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.source.triggers.count(), 2)

    def test_invalid_or_conflicting_identities_do_not_change_trigger(self):
        trigger = self._trigger()
        other = self._trigger(match='push lever')
        for value in (None, '', 12, True, [], {}, 'not-a-uuid'):
            with self.subTest(uid=value):
                document = self._document(trigger)
                document['metadata']['uid'] = value
                response = self._apply([document])
                self.assertEqual(response.status_code, 400)
                self.assertIn('metadata.uid', str(response.data))
        for field, value in (('id', other.pk), ('key', other.key)):
            with self.subTest(field=field):
                document = self._document(trigger)
                document['metadata'][field] = value
                response = self._apply([document])
                self.assertEqual(response.status_code, 400)
                self.assertIn('different triggers', str(response.data))
        document = manifests.trigger_to_manifest(trigger)
        document['metadata']['uid'] = str(uuid.uuid4())
        response = self._apply([document])
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot be changed', str(response.data))
        self.assertEqual(self.source.triggers.count(), 2)

    def test_legacy_matching_uses_event_and_match_even_with_one_candidate(self):
        first = self._trigger()
        document = self._document(first)
        document['metadata'].pop('uid')
        document['spec']['match'] = 'push lever'
        for expected_status in (201, 200):
            response = self._apply([document])
            self.assertEqual(response.status_code, expected_status, response.data)
        self.assertEqual(set(self.source.triggers.values_list('match', flat=True)), {
            'pull lever', 'push lever',
        })
        # Null stored event/match fields and their exported empty strings are
        # equivalent for older manifests, including room policy triggers.
        policy = self._trigger(name=None, kind='policy', event='before_move_enter', match=None, script='')
        document = self._document(policy)
        document['metadata'].pop('uid')
        response = self._apply([document])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.source.triggers.count(), 3)

    def test_ambiguous_legacy_destination_fails_without_changing_either_trigger(self):
        first = self._trigger()
        second = self._trigger(script='/echo -- Second effect.')
        document = self._document(first)
        document['metadata'].pop('uid')
        document['spec']['script'] = '/echo -- Replacement.'
        response = self._apply([document])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Ambiguous legacy trigger identity', str(response.data))
        second.refresh_from_db()
        self.assertEqual(second.script, '/echo -- Second effect.')
        first.refresh_from_db()
        self.assertEqual(first.script, '/echo -- Click.')

    def test_ambiguous_legacy_stream_rolls_back_even_on_empty_destination(self):
        first = self._trigger()
        self._trigger(script='/echo -- Second effect.')
        documents = world_export.serialize_world_export_payload(self.source)['documents']
        for document in documents:
            if document['kind'] == 'trigger':
                document['metadata'].pop('uid')
        target = self._target()
        response = self._apply(documents, target)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Ambiguous legacy trigger documents', str(response.data))
        self.assertFalse(target.triggers.exists())
        target.refresh_from_db()
        self.assertEqual(target.name, 'Destination')
        self.assertEqual(target.default_currency.code, 'gold')
        first.refresh_from_db()

    def test_uid_lookup_is_one_query_and_unique_within_world(self):
        trigger = self._trigger()
        with self.assertNumQueries(1):
            resolved, trigger_id, uid = manifests._resolve_trigger_reference(
                world=self.source, metadata={'uid': str(trigger.uid)},
            )
        self.assertEqual((resolved.pk, trigger_id, uid), (trigger.pk, trigger.pk, trigger.uid))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._trigger(uid=trigger.uid)

    def test_concurrent_creation_reports_retry_instead_of_overwriting(self):
        document = self._document(self._trigger())
        document['metadata']['uid'] = str(uuid.uuid4())
        first = manifests.parse_trigger_manifest(world=self.source, manifest=document)
        second = manifests.parse_trigger_manifest(world=self.source, manifest=document)
        created = manifests.apply_trigger_manifest(first)
        with self.assertRaisesMessage(serializers.ValidationError, 'Retry the import'):
            manifests.apply_trigger_manifest(second)
        self.assertEqual(self.source.triggers.filter(uid=created.uid).count(), 1)

    def test_uid_retargeting_requires_permission_for_original_target(self):
        trigger = self._trigger(
            scope='world', target_type=ContentType.objects.get_for_model(World),
            target_id=self.source.pk,
        )
        builder = get_user_model().objects.create_user('limited-trigger-builder@example.com', 'p')
        WorldBuilder.objects.create(world=self.source, user=builder, builder_rank=2)
        self.client.force_authenticate(builder)
        document = self._document(trigger)
        document['spec']['scope'] = 'room'
        document['spec']['target'] = f'room@{self.room.relative_id}'
        response = self._apply([document])
        self.assertEqual(response.status_code, 403)
        trigger.refresh_from_db()
        self.assertEqual(trigger.scope, 'world')
