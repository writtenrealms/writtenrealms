"""Movement reads current actor state after acquiring its transaction locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from spawns.actions.movement_costs import movement_cost
from spawns.combat_encounters import locked_combat
from spawns.handlers.base import CommandContext
from spawns.handlers.movement import MoveHandler
from spawns.models import Player
from tests.base import WorldTestCase
from tests.utils import capture_game_messages
from worlds.models import World


def movement_context(player):
    return CommandContext(
        actor=player, actor_type="player", actor_id=player.pk,
        actor_key=player.key, player=player,
        world=player.world, room=player.room, payload={"direction": "east"},
    )


class MovementLockedActorTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.destination = self.room.create_at("east")
        self.player.stamina = 20
        self.player.in_game = True
        self.player.save(update_fields=["stamina", "in_game"])

    def test_stale_dispatch_input_uses_fresh_locked_location_and_resources(self):
        context = movement_context(self.player)
        next_room = self.destination.create_at("east")
        Player.objects.filter(pk=self.player.pk).update(
            room=self.destination, stamina=12, location_sequence=4, follow_move_sequence=7,
        )

        with capture_game_messages():
            MoveHandler().handle(context)

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, next_room.pk)
        self.assertEqual(self.player.stamina, 12 - movement_cost(next_room))
        self.assertEqual(self.player.location_sequence, 5)
        self.assertEqual(self.player.follow_move_sequence, 8)

    def test_nested_movement_reloads_resource_changes_from_another_object(self):
        context = movement_context(self.player)
        with locked_combat(keys=[self.player.key]):
            Player.objects.filter(pk=self.player.pk).update(stamina=0)
            with capture_game_messages() as messages:
                MoveHandler().handle(context)

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.pk)
        self.assertEqual(self.player.stamina, 0)
        errors = [
            entry["message"] for entry in messages
            if entry["message"]["type"] == "cmd.move.error"
        ]
        self.assertEqual(errors[0]["data"]["code"], "exhausted")

    def test_nested_movement_reloads_location_changes_from_another_object(self):
        context = movement_context(self.player)
        next_room = self.destination.create_at("east")
        with locked_combat(keys=[self.player.key]):
            Player.objects.filter(pk=self.player.pk).update(room=self.destination)
            with capture_game_messages():
                MoveHandler().handle(context)

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, next_room.pk)


class ConcurrentMovementLockedActorTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("spawns.tasks.reconcile_combat_room"))
        self.enterContext(patch("spawns.handlers.movement.publish_events"))
        user = get_user_model().objects.create_user("concurrent-mover@example.com", "p")
        world = World.objects.new_world(name="Concurrent movement", author=user)
        runtime = world.create_spawn_world()
        self.origin = world.config.starting_room
        self.middle = self.origin.create_at("east")
        self.destination = self.middle.create_at("east")
        self.player = Player.objects.create(
            user=user, name="Concurrent mover", world=runtime, room=self.origin,
            stamina=20, in_game=True,
        )

    def test_concurrent_moves_preserve_both_resource_costs_and_sequences(self):
        ready = Barrier(2)

        def move():
            close_old_connections()
            try:
                context = movement_context(Player.objects.get(pk=self.player.pk))
                context.published_messages = []
                context.capture_only = True
                # Both command inputs precede either movement transaction.
                ready.wait(timeout=5)
                MoveHandler().handle(context)
                return context.published_messages
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: move(), range(2)))

        self.player.refresh_from_db()
        self.assertEqual(results, [[], []])
        self.assertEqual(self.player.room_id, self.destination.pk)
        self.assertEqual(
            self.player.stamina,
            20 - movement_cost(self.middle) - movement_cost(self.destination),
        )
        self.assertEqual(self.player.location_sequence, 2)
        self.assertEqual(self.player.follow_move_sequence, 2)
