from datetime import timedelta
import uuid
from unittest.mock import patch

from django.db import transaction
from django.utils import timezone

from config import constants as adv_consts
import spawns.handlers  # noqa: F401  # Load handler/action registry first.
from spawns.actions.builder import JumpAction, TransferAction
from spawns.actions.combat import apply_player_death
from spawns.actions.player_state import RestAction, StandAction
from spawns.models import GameEventOutbox, PreparedGameAction
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.instances import reset_instance
from worlds.models import Door, Doorway, World, WorldConfig


class PreparedDoorActionLifecycleTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.health = 10
        self.player.save(update_fields=["in_game", "health"])
        self.east_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=self.doorway,
            direction=adv_consts.DIRECTION_EAST,
            from_room=self.room,
            to_room=self.east_room,
            name="iron gate",
        )

    def _prepare_action(
        self,
        *,
        player=None,
        runtime_world=None,
        room=None,
        doorway=None,
    ):
        return PreparedGameAction.objects.create(
            player=player or self.player,
            runtime_world=runtime_world or self.spawn_world,
            room=room or self.room,
            doorway=doorway or self.doorway,
            action_type=PreparedGameAction.ACTION_CLOSE_DOOR,
            run_at=timezone.now() + timedelta(seconds=30),
            request_selector="east",
            target_direction=adv_consts.DIRECTION_EAST,
            target_name="iron gate",
        )

    def _assert_cancelled(self, action, *, code):
        action.refresh_from_db()
        self.assertEqual(
            action.status,
            PreparedGameAction.STATUS_CANCELLED,
        )
        self.assertEqual(action.failure_code, code)

    def _assert_outbox_cancel(self, action, *, code):
        row = GameEventOutbox.objects.get(
            event_type="cmd.close.cancelled",
            recipients__contains=[self.player.key],
        )
        self.assertEqual(row.data["action_id"], action.id)
        self.assertEqual(row.data["code"], code)

    def test_builder_jump_cancels_pending_door_action_in_result_events(self):
        action = self._prepare_action()

        result = JumpAction().execute(
            player_id=self.player.id,
            room_selector=adv_consts.DIRECTION_EAST,
        )

        self._assert_cancelled(action, code="actor_moved")
        self.assertIn(
            "cmd.close.cancelled",
            [event.type for event in result.events],
        )

    def test_builder_transfer_cancels_pending_door_action_in_result_events(self):
        action = self._prepare_action()

        result = TransferAction().execute(
            actor=self.player,
            target_selector="self",
            room_selector=adv_consts.DIRECTION_EAST,
            runtime_world=self.spawn_world,
        )

        self._assert_cancelled(action, code="actor_transferred")
        self.assertIn(
            "cmd.close.cancelled",
            [event.type for event in result.events],
        )

    def test_rest_cancels_pending_door_action_in_same_transaction(self):
        action = self._prepare_action()

        with transaction.atomic():
            result = RestAction().execute(self.player.id)

        self._assert_cancelled(action, code="physical_action_replaced")
        self.assertEqual(
            [event.type for event in result.events[:2]],
            ["cmd.close.cancelled", "cmd.rest.success"],
        )

    def test_stand_cancels_pending_door_action_in_same_transaction(self):
        self.player.state = adv_consts.CHARACTER_STATE_RESTING
        self.player.save(update_fields=["state"])
        action = self._prepare_action()

        with transaction.atomic():
            result = StandAction().execute(self.player.id)

        self._assert_cancelled(action, code="physical_action_replaced")
        self.assertEqual(
            [event.type for event in result.events[:2]],
            ["cmd.close.cancelled", "cmd.stand.success"],
        )

    def test_world_exit_cancels_pending_door_action_into_outbox(self):
        action = self._prepare_action()
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])

        with patch.object(WorldGate, "exit_mpw"):
            WorldGate(player=self.player, world=self.spawn_world).exit()

        self._assert_cancelled(action, code="actor_logged_out")
        self._assert_outbox_cancel(action, code="actor_logged_out")
        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)

    def test_death_cancels_pending_door_action_in_death_outbox_batch(self):
        action = self._prepare_action()
        self.world.config.death_room = self.east_room
        self.world.config.save(update_fields=["death_room"])

        apply_player_death(
            player=self.player,
            origin_room=self.room,
            cause="door_lifecycle_test",
            forced=True,
            death_token=uuid.uuid4(),
        )

        self._assert_cancelled(action, code="actor_dead")
        self._assert_outbox_cancel(action, code="actor_dead")

    def _instance_template(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        config = WorldConfig.objects.create()
        template = World.objects.new_world(
            name="Door Trial",
            author=self.user,
            config=config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        return template, template.config.starting_room

    def test_enter_instance_cancels_pending_door_action_into_outbox(self):
        action = self._prepare_action()
        template, instance_room = self._instance_template()

        World.enter_instance(
            player=self.player,
            transfer_to_id=instance_room.id,
            transfer_from_id=self.room.id,
        )

        self._assert_cancelled(action, code="actor_world_changed")
        self._assert_outbox_cancel(action, code="actor_world_changed")

    def test_leave_instance_cancels_pending_door_action_into_outbox(self):
        template, instance_room = self._instance_template()
        spawned_instance = World.enter_instance(
            player=self.player,
            transfer_to_id=instance_room.id,
            transfer_from_id=self.room.id,
        )
        instance_doorway = Doorway.objects.create(
            world=template,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        action = self._prepare_action(
            runtime_world=spawned_instance,
            room=instance_room,
            doorway=instance_doorway,
        )

        World.leave_instance(player=self.player)

        self._assert_cancelled(action, code="actor_world_changed")
        self._assert_outbox_cancel(action, code="actor_world_changed")

    def test_reset_instance_cancels_before_prepared_actions_are_deleted(self):
        template, instance_room = self._instance_template()
        spawned_instance = World.enter_instance(
            player=self.player,
            transfer_to_id=instance_room.id,
            transfer_from_id=self.room.id,
        )
        instance_doorway = Doorway.objects.create(
            world=template,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        action = self._prepare_action(
            runtime_world=spawned_instance,
            room=instance_room,
            doorway=instance_doorway,
        )

        reset_instance(player=self.player)

        self.assertFalse(
            PreparedGameAction.objects.filter(pk=action.pk).exists(),
        )
        self._assert_outbox_cancel(action, code="instance_reset")
