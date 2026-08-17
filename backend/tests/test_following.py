from threading import Event, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.conditions import evaluate_conditions
from spawns.actions import following as following_actions
from spawns.actions.base import ActionError
from spawns.handlers.registry import resolve_text_handler
from spawns.following import MAX_FOLLOW_PROPAGATION_DEPTH
from spawns.models import Mob, MovementFollow, Player
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import World, WorldConfig


class TestFollowCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.hermes = self.create_mob(
            "Hermes",
            keywords="hermes messenger",
            follow_move_sequence=7,
        )

    def _entry(self, messages, message_type, *, recipient=None):
        for entry in messages:
            if entry["message"].get("type") != message_type:
                continue
            if recipient is not None and entry["player_key"] != recipient:
                continue
            return entry
        return None

    def _online_player(self, name):
        player = self.create_player(name)
        player.in_game = True
        player.save(update_fields=["in_game"])
        return player

    def test_follow_mob_creates_private_movement_link_at_current_sequence(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertIsNone(link.leader_player_id)
        self.assertEqual(link.leader_mob_id, self.hermes.id)
        self.assertEqual(link.last_processed_sequence, 7)

        actor_entry = self._entry(
            messages,
            "cmd.follow.success",
            recipient=self.player.key,
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(actor_entry["message"]["data"]["status"], "started")
        self.assertEqual(
            actor_entry["message"]["text"],
            "You begin following Hermes.",
        )
        self.assertIsNone(
            self._entry(messages, "notification.follow.started")
        )

    def test_display_name_does_not_hide_a_different_matching_target(self):
        self.player.name = "Hermes"
        self.player.save(update_fields=["name"])

        dispatch_text_command(self.player.id, "follow hermes")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_mob_id, self.hermes.id)

    def test_follow_uses_locked_leader_sequence_after_resolution(self):
        original_resolver = following_actions._follow_target

        def resolve_then_advance(player, selector):
            target = original_resolver(player, selector)
            type(target).objects.filter(pk=target.pk).update(
                follow_move_sequence=13,
            )
            return target

        with patch.object(
            following_actions,
            "_follow_target",
            side_effect=resolve_then_advance,
        ):
            dispatch_text_command(self.player.id, "follow hermes")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_mob_id, self.hermes.id)
        self.assertEqual(link.last_processed_sequence, 13)

    def test_follow_rejects_leader_who_moves_after_resolution(self):
        other_room = self.room.create_at("east")
        original_resolver = following_actions._follow_target

        def resolve_then_move(player, selector):
            target = original_resolver(player, selector)
            type(target).objects.filter(pk=target.pk).update(room=other_room)
            return target

        with patch.object(
            following_actions,
            "_follow_target",
            side_effect=resolve_then_move,
        ), capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(error["message"]["data"]["code"], "target_changed")
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

    def test_follow_rejects_player_who_logs_out_after_resolution(self):
        guide = self._online_player("Guide")
        original_resolver = following_actions._follow_target

        def resolve_then_logout(player, selector):
            target = original_resolver(player, selector)
            type(target).objects.filter(pk=target.pk).update(in_game=False)
            return target

        with patch.object(
            following_actions,
            "_follow_target",
            side_effect=resolve_then_logout,
        ), capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow guide")

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(error["message"]["data"]["code"], "target_changed")
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )
        guide.refresh_from_db()
        self.assertFalse(guide.in_game)

    def test_follow_registration_preserves_the_flee_prefix(self):
        flee_command, flee_handler = resolve_text_handler("f")
        follow_command, follow_handler = resolve_text_handler("fo")

        self.assertEqual(flee_command, "flee")
        self.assertEqual(flee_handler.command_type, "flee")
        self.assertEqual(follow_command, "follow")
        self.assertEqual(follow_handler.command_type, "follow")

    def test_generic_counted_player_selector_excludes_the_follower(self):
        guide = self._online_player("Guide")

        dispatch_text_command(self.player.id, "follow 1.player")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_player_id, guide.id)

    def test_follow_rejects_player_who_left_the_game(self):
        self.player.in_game = False
        self.player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(error["message"]["data"]["code"], "not_in_game")
        self.assertFalse(MovementFollow.objects.filter(follower=self.player).exists())

    def test_follow_player_notifies_only_actor_and_player_leader(self):
        guide = self._online_player("Guide")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow guide")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_player_id, guide.id)
        self.assertIsNone(link.leader_mob_id)
        self.assertIsNotNone(
            self._entry(
                messages,
                "cmd.follow.success",
                recipient=self.player.key,
            )
        )
        leader_entry = self._entry(
            messages,
            "notification.follow.started",
            recipient=guide.key,
        )
        self.assertIsNotNone(leader_entry)
        self.assertEqual(
            leader_entry["message"]["text"],
            "Joe begins following you.",
        )
        self.assertEqual(
            {entry["player_key"] for entry in messages},
            {self.player.key, guide.key},
        )

    def test_follow_same_leader_is_idempotent_without_repeat_notification(self):
        guide = self._online_player("Guide")
        guide.follow_move_sequence = 11
        guide.save(update_fields=["follow_move_sequence"])
        dispatch_text_command(self.player.id, "follow guide")
        link_id = MovementFollow.objects.get(follower=self.player).id

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow guide")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.id, link_id)
        self.assertEqual(link.last_processed_sequence, 11)
        actor_entry = self._entry(messages, "cmd.follow.success")
        self.assertEqual(actor_entry["message"]["data"]["status"], "unchanged")
        self.assertEqual(
            actor_entry["message"]["text"],
            "You are already following Guide.",
        )
        self.assertIsNone(
            self._entry(messages, "notification.follow.started")
        )

    def test_follow_switches_one_link_and_resets_sequence_to_new_leader(self):
        guide = self._online_player("Guide")
        dispatch_text_command(self.player.id, "follow guide")
        link_id = MovementFollow.objects.get(follower=self.player).id

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.id, link_id)
        self.assertIsNone(link.leader_player_id)
        self.assertEqual(link.leader_mob_id, self.hermes.id)
        self.assertEqual(link.last_processed_sequence, 7)
        self.assertEqual(
            self._entry(messages, "cmd.follow.success")["message"]["data"]["status"],
            "switched",
        )
        self.assertIsNotNone(
            self._entry(
                messages,
                "notification.follow.stopped",
                recipient=guide.key,
            )
        )

    def test_unfollow_supports_targeted_and_idempotent_forms(self):
        dispatch_text_command(self.player.id, "follow hermes")

        with capture_game_messages() as mismatch_messages:
            dispatch_text_command(self.player.id, "unfollow guide")
        self.assertTrue(MovementFollow.objects.filter(follower=self.player).exists())
        mismatch = self._entry(mismatch_messages, "cmd.unfollow.error")
        self.assertEqual(
            mismatch["message"]["data"]["code"],
            "not_following_target",
        )

        with capture_game_messages() as stopped_messages:
            dispatch_text_command(self.player.id, "unfollow hermes")
        self.assertFalse(MovementFollow.objects.filter(follower=self.player).exists())
        stopped = self._entry(stopped_messages, "cmd.unfollow.success")
        self.assertEqual(stopped["message"]["data"]["status"], "stopped")
        self.assertEqual(stopped["message"]["text"], "You stop following Hermes.")

        with capture_game_messages() as repeated_messages:
            dispatch_text_command(self.player.id, "unfollow")
        repeated = self._entry(repeated_messages, "cmd.unfollow.success")
        self.assertEqual(repeated["message"]["data"]["status"], "unchanged")
        self.assertEqual(
            repeated["message"]["text"],
            "You are not following anyone.",
        )

    def test_follow_rejects_ambiguous_target_and_accepts_counted_selector(self):
        second_hermes = self.create_mob(
            "Another Hermes",
            keywords="hermes messenger",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(error["message"]["data"]["code"], "ambiguous_target")
        self.assertFalse(MovementFollow.objects.filter(follower=self.player).exists())

        dispatch_text_command(self.player.id, "follow 2.hermes")
        link = MovementFollow.objects.get(follower=self.player)
        self.assertEqual(link.leader_mob_id, second_hermes.id)

    def test_follow_rejects_player_cycle_without_changing_existing_links(self):
        second = self._online_player("Second")
        third = self._online_player("Third")
        dispatch_text_command(self.player.id, "follow second")
        dispatch_text_command(second.id, "follow third")

        with capture_game_messages() as messages:
            dispatch_text_command(third.id, "follow joe")

        error = self._entry(
            messages,
            "cmd.follow.error",
            recipient=third.key,
        )
        self.assertEqual(error["message"]["data"]["code"], "follow_cycle")
        self.assertFalse(MovementFollow.objects.filter(follower=third).exists())
        self.assertEqual(
            MovementFollow.objects.get(follower=self.player).leader_player_id,
            second.id,
        )
        self.assertEqual(
            MovementFollow.objects.get(follower=second).leader_player_id,
            third.id,
        )

    def test_follow_rejects_chain_beyond_propagation_depth(self):
        chain = [
            self._online_player(f"Chain {index}")
            for index in range(MAX_FOLLOW_PROPAGATION_DEPTH + 1)
        ]
        for follower, leader in zip(chain, chain[1:]):
            MovementFollow.objects.create(
                follower=follower,
                leader_player=leader,
            )

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"follow {chain[0].key}",
            )

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(
            error["message"]["data"]["code"],
            "follow_chain_too_long",
        )
        self.assertEqual(
            error["message"]["text"],
            "That following chain is too long.",
        )
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

    def test_follow_rejects_combined_upstream_and_downstream_depth(self):
        half_depth = MAX_FOLLOW_PROPAGATION_DEPTH // 2
        upstream = [
            self._online_player(f"Upstream {index}")
            for index in range(half_depth + 1)
        ]
        for follower, leader in zip(upstream, upstream[1:]):
            MovementFollow.objects.create(
                follower=follower,
                leader_player=leader,
            )

        downstream_leader = self.player
        for index in range(half_depth):
            downstream_follower = self._online_player(
                f"Downstream {index}"
            )
            MovementFollow.objects.create(
                follower=downstream_follower,
                leader_player=downstream_leader,
            )
            downstream_leader = downstream_follower

        with capture_game_messages() as messages:
            dispatch_text_command(
                self.player.id,
                f"follow {upstream[0].key}",
            )

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(
            error["message"]["data"]["code"],
            "follow_chain_too_long",
        )
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

    def test_follow_mob_rejects_too_deep_downstream_chain(self):
        downstream_leader = self.player
        for index in range(MAX_FOLLOW_PROPAGATION_DEPTH):
            downstream_follower = self._online_player(
                f"Mob downstream {index}"
            )
            MovementFollow.objects.create(
                follower=downstream_follower,
                leader_player=downstream_leader,
            )
            downstream_leader = downstream_follower

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "follow hermes")

        error = self._entry(messages, "cmd.follow.error")
        self.assertEqual(
            error["message"]["data"]["code"],
            "follow_chain_too_long",
        )
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

    def test_is_following_condition_reads_movement_link(self):
        self.assertFalse(
            evaluate_conditions(self.player, "is_following")["result"]
        )

        dispatch_text_command(self.player.id, "follow hermes")
        self.assertTrue(
            evaluate_conditions(self.player, "is_following")["result"]
        )

        dispatch_text_command(self.player.id, "unfollow")
        self.assertFalse(
            evaluate_conditions(self.player, "is_following")["result"]
        )


@skipUnless(connection.vendor == "postgresql", "PostgreSQL locking regression")
class FollowTargetConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "follow-race@example.com",
            "p",
        )
        config = WorldConfig.objects.create()
        authored_world = World.objects.new_world(
            name="Follow race world",
            author=self.user,
            config=config,
        )
        self.runtime_world = authored_world.create_spawn_world()
        self.room = authored_world.config.starting_room
        self.other_room = self.room.create_at("east")
        self.player = Player.objects.create(
            name="Follower",
            user=self.user,
            world=self.runtime_world,
            room=self.room,
            in_game=True,
        )
        self.hermes = Mob.objects.create(
            name="Hermes",
            keywords="hermes messenger",
            world=self.runtime_world,
            room=self.room,
        )

    def test_target_move_between_resolution_and_lock_cannot_create_link(self):
        target_resolved = Event()
        continue_follow = Event()
        errors = []
        original_resolver = following_actions._follow_target

        def paused_resolver(player, selector):
            target = original_resolver(player, selector)
            target_resolved.set()
            if not continue_follow.wait(timeout=10):
                raise RuntimeError("Timed out waiting for concurrent move.")
            return target

        def run_follow():
            close_old_connections()
            try:
                following_actions.FollowAction().execute(
                    self.player.id,
                    "hermes",
                )
            except Exception as exc:  # Captured for the test thread.
                errors.append(exc)
            finally:
                close_old_connections()

        with patch.object(
            following_actions,
            "_follow_target",
            side_effect=paused_resolver,
        ):
            follow_thread = Thread(target=run_follow, daemon=True)
            follow_thread.start()
            self.assertTrue(target_resolved.wait(timeout=10))
            Mob.objects.filter(pk=self.hermes.id).update(room=self.other_room)
            continue_follow.set()
            follow_thread.join(timeout=10)

        self.assertFalse(follow_thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ActionError)
        self.assertEqual(errors[0].code, "target_changed")
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )

    def test_locked_target_fails_fast_with_retryable_error(self):
        target_locked = Event()
        release_target = Event()
        lock_errors = []

        def hold_target_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    Mob.objects.select_for_update().get(pk=self.hermes.id)
                    target_locked.set()
                    if not release_target.wait(timeout=10):
                        raise RuntimeError("Timed out holding target lock.")
            except Exception as exc:  # Captured for the test thread.
                lock_errors.append(exc)
            finally:
                close_old_connections()

        lock_thread = Thread(target=hold_target_lock, daemon=True)
        lock_thread.start()
        self.assertTrue(target_locked.wait(timeout=10))
        try:
            with self.assertRaises(ActionError) as raised:
                following_actions.FollowAction().execute(
                    self.player.id,
                    "hermes",
                )
        finally:
            release_target.set()
            lock_thread.join(timeout=10)

        self.assertFalse(lock_thread.is_alive())
        self.assertFalse(lock_errors)
        self.assertEqual(raised.exception.code, "follow_busy")
        self.assertEqual(raised.exception.data, {"retryable": True})
        self.assertFalse(
            MovementFollow.objects.filter(follower=self.player).exists()
        )
