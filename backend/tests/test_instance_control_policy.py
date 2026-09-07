from django.db import IntegrityError, transaction
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from rest_framework import serializers

from backend.config.exceptions import ServiceError
from builders.instance_templates import clone_world_config_for_instance
from config import constants as adv_consts
from lobby.serializers import WorldTransferSerializer
from spawns.models import Player
from spawns.serializers import PlayerSerializer
from spawns.services import WorldGate
from tests.base import WorldTestCase
from tests.test_world_config_manifests import AuthenticatedBuilderWorldTestCase
from worlds.instances import (
    create_fresh_instance_run,
    enter_players_into_run,
    get_or_create_instance_run,
)
from worlds.models import InstanceParticipant, InstanceRun, World, WorldConfig


class TestInstanceControlAdmission(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.config = WorldConfig.objects.create(instance_single_player=True)
        self.template = World.objects.new_world(
            name='Private Adventure', author=self.user, config=self.config,
            instance_of=self.world, is_multiplayer=True,
        )
        self.entry_room = self.template.config.starting_room

    def _run(self, player=None, **kwargs):
        return get_or_create_instance_run(
            self.template, player=player or self.player,
            transfer_from=self.room, register_participant=False, **kwargs,
        )

    def test_solo_run_is_owned_by_creator_and_ref_does_not_admit_group_member(self):
        other = self.create_player('Other')
        run = self._run(member_ids=[other.pk])
        self.assertTrue(run.single_player)
        self.assertEqual(run.owner_id, self.player.pk)
        self.assertEqual(run.initial_member_ids, [])
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            self._run(player=other, ref=run.ref)
        self.assertFalse(InstanceParticipant.objects.filter(run=run, player=other).exists())
        other.refresh_from_db()
        self.assertEqual(other.world_id, self.spawn_world.pk)
        other_run = self._run(player=other)
        self.assertNotEqual(other_run.pk, run.pk)
        self.assertEqual(other_run.owner_id, other.pk)

    def test_owner_can_return_after_leader_changes(self):
        run = self._run()
        other = self.create_player('Other')
        run.leader = other
        run.save(update_fields=['leader'])
        returned = self._run()
        self.assertEqual(returned.pk, run.pk)
        self.assertEqual(returned.owner_id, self.player.pk)
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            self._run(player=other, ref=run.ref)

    def test_ownerless_solo_run_fails_closed(self):
        run = self._run()
        InstanceRun.objects.filter(pk=run.pk).update(owner=None)
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            self._run(ref=run.ref)

    def _assert_reconnect_rejected_before_start(self, run, player):
        # Simulate an invalid assignment by an administrator or future caller:
        # ordinary instance admission already prevents this placement.
        Player.objects.filter(pk=player.pk).update(
            world=run.spawned_world, room=self.entry_room, in_game=False,
        )
        player.refresh_from_db()
        run.spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_STOPPED
        run.spawned_world.save(update_fields=['lifecycle'])
        gate = WorldGate(player=player, world=run.spawned_world)
        with self.assertRaisesMessage(ServiceError, 'original owner'):
            gate.preflight()
        with patch('worlds.services.WorldSmith.start') as start:
            with self.assertRaisesMessage(ServiceError, 'original owner'):
                gate.enter()
        start.assert_not_called()
        player.refresh_from_db()
        run.spawned_world.refresh_from_db()
        self.assertFalse(player.in_game)
        self.assertEqual(run.spawned_world.lifecycle, adv_consts.WORLD_LIFECYCLE_STOPPED)

    def test_reconnect_rejects_foreign_character_before_starting_private_world(self):
        run = self._run()
        self._assert_reconnect_rejected_before_start(run, self.create_player('Other'))

    def test_reconnect_rejects_run_whose_original_owner_was_deleted(self):
        owner = self.create_player('Original Owner')
        run = self._run(player=owner)
        owner.delete()
        run.refresh_from_db()
        self.assertIsNone(run.owner_id)
        self._assert_reconnect_rejected_before_start(run, self.player)

    def test_owner_can_leave_and_reenter_without_admitting_someone_while_absent(self):
        spawned = World.enter_instance(
            player=self.player, transfer_to_id=self.entry_room.pk,
            transfer_from_id=self.room.pk,
        )
        run = InstanceRun.objects.get(spawned_world=spawned)
        self.player.refresh_from_db()
        World.leave_instance(player=self.player)
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        other = self.create_player('Other')
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            World.enter_instance(
                player=other, transfer_to_id=self.entry_room.pk,
                transfer_from_id=self.room.pk, ref=run.ref,
            )
        returned = World.enter_instance(
            player=self.player, transfer_to_id=self.entry_room.pk,
            transfer_from_id=self.room.pk,
        )
        self.assertEqual(returned.pk, spawned.pk)
        self.assertEqual(
            list(run.participants.filter(exited_at__isnull=True).values_list('player_id', flat=True)),
            [self.player.pk],
        )

    def test_group_admission_is_rejected_before_world_start_or_player_transfer(self):
        other = self.create_player('Other')
        run = create_fresh_instance_run(self.template, leader=self.player)
        with patch('worlds.instances._ensure_spawned_instance_started') as start:
            with self.assertRaisesMessage(RuntimeError, 'original owner'):
                enter_players_into_run(
                    run,
                    players_and_transfer_rooms=[(self.player, self.room), (other, self.room)],
                    entry_room=self.entry_room,
                )
        start.assert_not_called()
        self.assertFalse(InstanceParticipant.objects.filter(run=run).exists())
        self.player.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        self.assertEqual(other.world_id, self.spawn_world.pk)

    def test_time_policy_is_snapshotted_and_new_run_starts_waiting(self):
        self.config.instance_time_control = True
        self.config.save(update_fields=['instance_time_control'])
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=['combat_resolution_interval'])
        run = self._run()
        self.assertTrue(run.time_control)
        self.assertFalse(run.time_paused)
        self.assertFalse(run.pause_in_combat)
        self.assertIsNotNone(run.simulation_time)
        self.assertEqual(run.simulation_tick, 0)
        self.assertEqual(run.pending_command, {})
        self.assertEqual(run.pending_revision, 0)
        self.config.instance_time_control = False
        self.config.instance_single_player = False
        self.config.save(update_fields=['instance_time_control', 'instance_single_player'])
        run.refresh_from_db()
        self.assertTrue(run.time_control)
        self.assertTrue(run.single_player)
        with self.assertRaisesMessage(RuntimeError, 'original owner'):
            self._run(player=self.create_player('Other'), ref=run.ref)

    def test_database_rejects_time_control_for_multiplayer_run(self):
        run = self._run()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InstanceRun.objects.filter(pk=run.pk).update(single_player=False, time_control=True)

    def test_lobby_cannot_create_a_second_character_inside_solo_template(self):
        run = self._run()
        self.client.force_authenticate(self.user)
        before = Player.objects.count()
        response = self.client.post(
            reverse('lobby-world-chars', args=[self.template.pk]),
            {'name': 'Intruder', 'gender': 'male'}, format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('base world', str(response.data))
        self.assertEqual(Player.objects.count(), before)
        self.assertFalse(Player.objects.filter(world=run.spawned_world).exists())

    def test_player_serializer_create_cannot_insert_into_existing_solo_runtime(self):
        run = self._run()
        with self.assertRaisesMessage(serializers.ValidationError, 'admission service'):
            PlayerSerializer().create({
                'name': 'Intruder', 'user': self.user, 'world': run.spawned_world,
                'room': self.entry_room,
            })
        self.assertFalse(Player.objects.filter(world=run.spawned_world).exists())

    def test_completed_world_transfer_cannot_bypass_solo_admission(self):
        run = self._run()
        self.spawn_world.is_multiplayer = False
        self.spawn_world.lifecycle = adv_consts.WORLD_STATE_COMPLETE
        self.spawn_world.save(update_fields=['is_multiplayer', 'lifecycle'])
        self.room.transfer_to = self.entry_room
        self.room.save(update_fields=['transfer_to'])
        serializer = WorldTransferSerializer(
            data={'player': self.player.pk, 'name': self.player.name},
            context={'request': SimpleNamespace(user=self.user)},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('admission service', str(serializer.errors))
        # Revalidate the destination under the player lock even when a caller
        # validated an earlier room link before it was edited to the instance.
        with self.assertRaisesMessage(serializers.ValidationError, 'admission service'):
            WorldTransferSerializer().create({'player': self.player, 'name': self.player.name})
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        self.assertFalse(Player.objects.filter(world=run.spawned_world).exists())


class TestInstanceControlAuthoring(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.template = World.objects.new_world(
            name='Private Adventure', author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world, is_multiplayer=True,
        )
        self.endpoint = reverse('builder-world-config', args=[self.template.pk])
        self.manifest_endpoint = reverse('builder-world-manifest-apply', args=[self.template.pk])

    def test_config_round_trips_instance_flags_and_validates_partial_updates(self):
        response = self.client.patch(self.endpoint, {
            'instance_single_player': True, 'instance_time_control': True,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        payload = self.client.get(self.endpoint).data
        for field in ('instance_single_player', 'instance_time_control'):
            self.assertIs(payload['config'][field], True)
            self.assertIs(payload['manifest']['spec'][field], True)
        response = self.client.patch(self.endpoint, {'instance_single_player': False}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('requires a single-player', str(response.data))
        response = self.client.patch(self.endpoint, {
            'instance_single_player': False, 'instance_time_control': False,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_time_control_requires_solo_in_config_and_yaml(self):
        response = self.client.patch(self.endpoint, {'instance_time_control': True}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.manifest_endpoint, {
            'manifest': 'kind: world\nspec:\n  instance_time_control: true\n',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('requires a single-player', str(response.data))

    def test_manifest_imports_solo_and_time_control(self):
        response = self.client.post(self.manifest_endpoint, {
            'manifest': 'kind: world\nspec:\n  instance_single_player: true\n  instance_time_control: true\n',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.template.config.refresh_from_db()
        self.assertTrue(self.template.config.instance_single_player)
        self.assertTrue(self.template.config.instance_time_control)

    def test_base_world_cannot_enable_instance_controls(self):
        endpoint = reverse('builder-world-config', args=[self.world.pk])
        for fields in (
            {'instance_single_player': True},
            {'instance_single_player': True, 'instance_time_control': True},
        ):
            response = self.client.patch(endpoint, fields, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertIn('only configurable for instance templates', str(response.data))
        response = self.client.post(
            reverse('builder-world-manifest-apply', args=[self.world.pk]),
            {'manifest': 'kind: world\nspec:\n  instance_single_player: true\n'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('only configurable for instance templates', str(response.data))

    def test_cloning_does_not_inherit_instance_policy(self):
        self.template.config.instance_single_player = True
        self.template.config.instance_time_control = True
        self.template.config.save(update_fields=['instance_single_player', 'instance_time_control'])
        clone = clone_world_config_for_instance(self.template.config)
        self.assertFalse(clone.instance_single_player)
        self.assertFalse(clone.instance_time_control)

    def test_single_player_and_match_mode_are_incompatible_in_api_and_yaml(self):
        response = self.client.patch(self.endpoint, {
            'instance_single_player': True, 'pvp_mode': adv_consts.PVP_MODE_MATCH,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot use PvP match', str(response.data))
        response = self.client.post(self.manifest_endpoint, {
            'manifest': 'kind: world\nspec:\n  instance_single_player: true\n  pvp_mode: match\n',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot use PvP match', str(response.data))
        response = self.client.patch(self.endpoint, {'instance_single_player': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(self.endpoint, {'pvp_mode': adv_consts.PVP_MODE_MATCH}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(self.endpoint, {
            'instance_single_player': False, 'pvp_mode': adv_consts.PVP_MODE_MATCH,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
