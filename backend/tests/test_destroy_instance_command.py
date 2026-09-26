from unittest.mock import patch

from django.utils import timezone

from config import constants
from core.scoped_state import (
    STATE_SCOPE_WORLD,
    get_state_snapshot,
    replace_initial_state_snapshot,
    replace_state_snapshot,
)
from quests.models import QuestInstance, QuestTemplate
from spawns.handlers import dispatch_command
from spawns.models import ActiveEffect, CombatEncounter, InstanceClockWork, Item, Mob
from tests.base import WorldTestCase
from tests.combat_fixtures import create_combat_encounter
from tests.utils import capture_game_messages, create_active_effect, dispatch_text_command
from worlds.instances import destroy_instance, leave_instance
from worlds.models import InstanceAssignment, InstanceParticipant, InstanceRun, World, WorldConfig


class TestDestroyInstanceCommand(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.player.is_builder = True
        self.player.in_game = True
        self.player.save(update_fields=['is_builder', 'in_game'])
        self.template = World.objects.new_world(
            name='Builder Test Instance', author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world, is_multiplayer=True,
        )
        self.entry = self.template.config.starting_room
        self.room.transfer_to = self.entry
        self.room.save(update_fields=['transfer_to'])
        self.instance = self._enter(self.player)
        self.run = self.instance.instance_run

    def _enter(self, player, *, ref=None):
        instance = World.enter_instance(
            player=player, transfer_to_id=self.entry.pk,
            transfer_from_id=player.room_id, ref=ref,
        )
        player.refresh_from_db()
        return instance

    def _dispatch(self, text='/destroy', **kwargs):
        with capture_game_messages() as messages:
            if kwargs:
                dispatch_command('/destroy', payload={}, **kwargs)
            else:
                dispatch_text_command(self.player.pk, text)
        return messages

    def _message(self, messages, event_type, *, player=None):
        return next((entry['message'] for entry in messages
                     if entry['message']['type'] == event_type
                     and entry['player_key'] == (player or self.player).key), None)

    def test_destroy_returns_to_entrance_and_enter_creates_fresh_run(self):
        old_world_id, old_run_id, old_ref = self.instance.pk, self.run.pk, self.run.ref
        replace_state_snapshot(STATE_SCOPE_WORLD, self.instance, {'lever_pulled': True})
        replace_initial_state_snapshot(STATE_SCOPE_WORLD, self.template, {'phase': 'new'})
        mob = Mob.objects.create(world=self.instance, room=self.entry, name='Old guard')
        ground_item = Item.objects.create(world=self.instance, container=self.entry, name='Old rock')
        InstanceClockWork.objects.create(world=self.instance, kind='command', payload={})
        quest = QuestTemplate.objects.create(world=self.template, slug='practice', name='Practice')
        quest_instance = QuestInstance.objects.create(world=self.instance, player=self.player, template=quest)

        messages = self._dispatch()

        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        self.assertEqual(self.player.room_id, self.room.pk)
        self.assertTrue(self.player.in_game)
        self.assertFalse(World.objects.filter(pk=old_world_id).exists())
        self.assertFalse(InstanceRun.objects.filter(pk=old_run_id).exists())
        self.assertFalse(InstanceAssignment.objects.filter(instance_id=old_world_id).exists())
        self.assertFalse(InstanceParticipant.objects.filter(run_id=old_run_id).exists())
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertFalse(Item.objects.filter(pk=ground_item.pk).exists())
        self.assertFalse(InstanceClockWork.objects.filter(world_id=old_world_id).exists())
        self.assertFalse(QuestInstance.objects.filter(pk=quest_instance.pk).exists())
        self.assertTrue(QuestTemplate.objects.filter(pk=quest.pk).exists())
        self.assertTrue(World.objects.filter(pk=self.template.pk).exists())
        self.assertIsNotNone(self._message(messages, 'cmd./destroy.success'))
        state = self._message(messages, 'cmd.state.sync.success')
        self.assertEqual(state['data']['room']['id'], self.room.pk)
        self.assertIsNone(state['data']['world']['instance_of_id'])

        self._dispatch('enter')
        self.player.refresh_from_db()
        new_run = self.player.world.instance_run
        self.assertNotEqual(new_run.pk, old_run_id)
        self.assertNotEqual(new_run.ref, old_ref)
        self.assertNotEqual(new_run.spawned_world_id, old_world_id)
        self.assertEqual(self.player.room_id, self.entry.pk)
        self.assertEqual(get_state_snapshot(STATE_SCOPE_WORLD, self.player.world), {'phase': 'new'})

    def test_destroy_preserves_carried_nested_and_equipped_items_and_character_effects(self):
        bag = Item.objects.create(world=self.instance, container=self.player, name='Pack',
                                  type=constants.ITEM_TYPE_CONTAINER)
        gem = Item.objects.create(world=self.instance, container=bag, name='Gem')
        sword = Item.objects.create(world=self.instance, container=self.player, name='Sword')
        self.player.equipment.weapon = sword
        self.player.equipment.save(update_fields=['weapon'])
        effect = create_active_effect(target=self.player, payload={'effect': 'bless', 'duration_rounds': 5})

        self._dispatch()

        for item in [bag, gem, sword]:
            item.refresh_from_db()
            self.assertEqual(item.world_id, self.spawn_world.pk)
        self.assertEqual(gem.container_id, bag.pk)
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, sword.pk)
        self.assertEqual(ActiveEffect.objects.get(pk=effect.pk).world_id, self.spawn_world.pk)

    def test_all_participants_return_to_their_own_runtime_and_room(self):
        other_runtime = World.objects.create(
            name='Other base runtime', context=self.world, config=self.world.config,
        )
        other_entrance = self.room.create_at('east')
        guests = [self.create_player('Online', world=other_runtime, room=other_entrance),
                  self.create_player('Offline')]
        guests[0].in_game = True
        guests[0].save(update_fields=['in_game'])
        for guest in guests:
            self._enter(guest, ref=self.run.ref)

        messages = self._dispatch()

        for guest, runtime, room, online in [
            (guests[0], other_runtime, other_entrance, True),
            (guests[1], self.spawn_world, self.room, False),
        ]:
            guest.refresh_from_db()
            self.assertEqual((guest.world_id, guest.room_id, guest.in_game), (runtime.pk, room.pk, online))
            self.assertIsNotNone(self._message(messages, 'cmd.state.sync.success', player=guest))
        self.assertEqual(self._message(messages, 'cmd./destroy.success')['data']['players_returned'], 3)

    def test_other_runs_and_authored_content_remain_intact(self):
        other = self.create_player('Other run owner')
        other_instance = self._enter(other)
        mob = Mob.objects.create(world=other_instance, room=self.entry, name='Other guard')
        replace_state_snapshot(STATE_SCOPE_WORLD, other_instance, {'untouched': True})

        self._dispatch()

        self.assertTrue(InstanceRun.objects.filter(spawned_world=other_instance).exists())
        self.assertTrue(Mob.objects.filter(pk=mob.pk).exists())
        self.assertTrue(World.objects.filter(pk=self.template.pk).exists())
        self.assertEqual(get_state_snapshot(STATE_SCOPE_WORLD, other_instance), {'untouched': True})

    def test_non_builder_and_user_without_edit_permission_cannot_destroy(self):
        for builder, user in [(False, self.user), (True, self.create_user('visitor@example.com'))]:
            with self.subTest(builder=builder):
                self.player.is_builder = builder
                self.player.user = user
                self.player.save(update_fields=['is_builder', 'user'])
                messages = self._dispatch()
                self.assertIsNotNone(self._message(messages, 'cmd./destroy.error'))
                self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
                self.player.refresh_from_db()
                self.assertEqual(self.player.world_id, self.instance.pk)

    def test_base_world_and_extra_arguments_are_rejected(self):
        self.assertIsNotNone(self._message(self._dispatch('/destroy all'), 'cmd./destroy.error'))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())
        World.leave_instance(self.player)
        self.assertIsNotNone(self._message(self._dispatch(), 'cmd./destroy.error'))
        self.assertTrue(World.objects.filter(pk=self.spawn_world.pk).exists())
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_script_source_does_not_grant_a_regular_player_builder_access(self):
        self.player.is_builder = False
        self.player.save(update_fields=['is_builder'])
        messages = self._dispatch(player_id=self.player.pk, script_source=True)
        self.assertIsNotNone(self._message(messages, 'cmd./destroy.error'))
        self.assertTrue(World.objects.filter(pk=self.instance.pk).exists())

    def test_failed_exit_rolls_back_other_participants_and_cleanup(self):
        guest = self.create_player('Guest')
        self._enter(guest, ref=self.run.ref)

        def fail_guest_exit(*, player, **kwargs):
            if player.pk == guest.pk:
                raise RuntimeError('Cannot return the guest.')
            return leave_instance(player=player, **kwargs)

        with patch('worlds.instances.leave_instance', side_effect=fail_guest_exit):
            messages = self._dispatch()

        self.assertIsNotNone(self._message(messages, 'cmd./destroy.error'))
        self.assertIsNone(self._message(messages, 'cmd./destroy.success'))
        self.player.refresh_from_db()
        guest.refresh_from_db()
        self.assertEqual(self.player.world_id, self.instance.pk)
        self.assertEqual(guest.world_id, self.instance.pk)
        self.assertEqual(InstanceParticipant.objects.filter(run=self.run, exited_at__isnull=True).count(), 2)

    def test_paused_combat_is_destroyed_immediately_without_advancing(self):
        InstanceRun.objects.filter(pk=self.run.pk).update(
            single_player=True, time_control=True, time_paused=True, pause_in_combat=True,
            simulation_time=timezone.now(),
            pending_command={'command_type': 'look', 'payload': {}},
        )
        mob = Mob.objects.create(world=self.instance, room=self.entry, name='Guard')
        encounter = create_combat_encounter(world=self.instance, room=self.entry, player=self.player, mob=mob)

        messages = self._dispatch()

        self.assertIsNotNone(self._message(messages, 'cmd./destroy.success'))
        self.assertIsNone(self._message(messages, 'cmd.prepare_turn.success'))
        self.assertFalse(InstanceRun.objects.filter(pk=self.run.pk).exists())
        self.assertFalse(CombatEncounter.objects.filter(pk=encounter.pk).exists())
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.pk)

    def test_destroy_then_enter_command_chain_uses_a_fresh_run(self):
        InstanceRun.objects.filter(pk=self.run.pk).update(single_player=True, time_control=True)
        self._dispatch('/destroy;enter')
        self.player.refresh_from_db()
        self.assertEqual(self.player.world.context_id, self.template.pk)
        self.assertNotEqual(self.player.world.instance_run.ref, self.run.ref)
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_stale_player_context_cannot_destroy_a_new_instance(self):
        stale_player = type(self.player).objects.get(pk=self.player.pk)
        self._dispatch()
        self.player.refresh_from_db()
        new_instance = self._enter(self.player)
        with self.assertRaisesMessage(ValueError, 'no longer in that instance'):
            destroy_instance(player=stale_player)
        self.assertTrue(World.objects.filter(pk=new_instance.pk).exists())

    def test_entry_selected_before_destruction_cannot_resurrect_the_old_run(self):
        guest = self.create_player('Guest')
        # Simulate destruction between run selection and the admission lock.
        with patch('worlds.instances._ensure_spawned_instance_started',
                   side_effect=lambda run: destroy_instance(player=self.player)):
            with capture_game_messages() as messages:
                dispatch_text_command(guest.pk, f'enter {self.run.ref}')
        self.assertIsNotNone(self._message(messages, 'cmd.enter.error', player=guest))
        guest.refresh_from_db()
        self.assertEqual(guest.world_id, self.spawn_world.pk)
        self.assertFalse(World.objects.filter(pk=self.instance.pk).exists())

    def test_help_explains_the_fresh_run_workflow(self):
        message = self._message(self._dispatch('help /destroy'), 'cmd.help.success')
        self.assertEqual(message['data']['command']['format'], '/destroy')
        self.assertIn('fresh run', message['text'])
