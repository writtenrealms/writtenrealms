from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from config import constants as adv_consts
from spawns.actions.doors import (
    execute_player_door_command,
    resolve_prepared_door_action,
)
from spawns.models import DoorState, Player, PreparedGameAction
from worlds.models import Door, Doorway, World, WorldConfig


class TestConcurrentDoorActions(TransactionTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        first_user = user_model.objects.create_user(
            "first-door-closer@example.com",
            "p",
        )
        second_user = user_model.objects.create_user(
            "second-door-closer@example.com",
            "p",
        )
        config = WorldConfig.objects.create()
        self.authored_world = World.objects.new_world(
            name="Concurrent Door World",
            author=first_user,
            config=config,
        )
        self.runtime_world = self.authored_world.create_spawn_world()
        self.room = self.authored_world.zones.first().rooms.first()
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        self.doorway = Doorway.objects.create(
            world=self.authored_world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=self.doorway,
            from_room=self.room,
            to_room=destination,
            direction=adv_consts.DIRECTION_EAST,
            name="iron gate",
        )
        Door.objects.create(
            doorway=self.doorway,
            from_room=destination,
            to_room=self.room,
            direction=adv_consts.DIRECTION_WEST,
            name="iron gate",
        )
        self.first_player = Player.objects.create(
            user=first_user,
            world=self.runtime_world,
            room=self.room,
            name="First Closer",
            in_game=True,
            health=10,
        )
        self.second_player = Player.objects.create(
            user=second_user,
            world=self.runtime_world,
            room=self.room,
            name="Second Closer",
            in_game=True,
            health=10,
        )

    def test_concurrent_due_closes_complete_once_and_cancel_stale_action(self):
        with patch("spawns.tasks.resolve_prepared_game_action.apply_async"):
            execute_player_door_command(
                player_id=self.first_player.id,
                command="close",
                selector="east",
            )
            execute_player_door_command(
                player_id=self.second_player.id,
                command="close",
                selector="east",
            )

        actions = list(
            PreparedGameAction.objects.filter(
                doorway=self.doorway,
                status=PreparedGameAction.STATUS_PENDING,
            ).order_by("id")
        )
        self.assertEqual(len(actions), 2)
        self.assertEqual({action.expected_revision for action in actions}, {0})
        self.assertEqual(
            DoorState.objects.filter(
                world=self.runtime_world,
                doorway=self.doorway,
            ).count(),
            1,
        )

        due_at = timezone.now()
        PreparedGameAction.objects.filter(
            id__in=[action.id for action in actions]
        ).update(run_at=due_at - timedelta(seconds=1))
        barrier = Barrier(2)

        def resolve_once(action_id):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return resolve_prepared_door_action(action_id, now=due_at)
            finally:
                close_old_connections()

        with patch("spawns.actions.doors.flush_game_event_outbox"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(
                    resolve_once,
                    [action.id for action in actions],
                ))

        self.assertEqual(
            sorted(outcomes),
            [
                PreparedGameAction.STATUS_CANCELLED,
                PreparedGameAction.STATUS_COMPLETED,
            ],
        )
        self.assertEqual(
            PreparedGameAction.objects.filter(
                doorway=self.doorway,
                status=PreparedGameAction.STATUS_COMPLETED,
            ).count(),
            1,
        )
        self.assertEqual(
            PreparedGameAction.objects.filter(
                doorway=self.doorway,
                status=PreparedGameAction.STATUS_CANCELLED,
                failure_code="doorway_stale",
            ).count(),
            1,
        )

        state = DoorState.objects.get(
            world=self.runtime_world,
            doorway=self.doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(state.revision, 1)
        self.assertEqual(
            DoorState.objects.filter(
                world=self.runtime_world,
                doorway=self.doorway,
            ).count(),
            1,
        )
