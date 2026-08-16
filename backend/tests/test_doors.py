from datetime import timedelta
import uuid
from unittest.mock import patch

from django.utils import timezone

from builders.models import ItemDefinition
from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.doors import (
    DOOR_ACTION_DELAY_SECONDS,
    execute_forced_door_command,
    process_due_prepared_door_actions,
    resolve_door_target,
)
from spawns.handlers import dispatch_command
from spawns.models import DoorState, Item, Mob, PreparedGameAction
from spawns.state_payloads import directional_door_payload, door_state_lookup
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import Door, Doorway


class TestDoorCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.health = 10
        self.player.stamina = 100
        self.player.save(
            update_fields=["in_game", "health", "stamina"]
        )
        self.east_room = self.room.create_at(adv_consts.DIRECTION_EAST)

    def _create_door(
        self,
        *,
        direction=adv_consts.DIRECTION_EAST,
        name="iron gate",
        default_state=adv_consts.DOOR_STATE_OPEN,
        key=None,
        destroy_key=False,
        reciprocal=True,
    ):
        destination = getattr(self.room, direction)
        if destination is None:
            destination = self.room.create_at(direction)
        doorway = Doorway.objects.create(
            world=self.world,
            key=key,
            destroy_key=destroy_key,
            default_state=default_state,
        )
        face = Door.objects.create(
            doorway=doorway,
            direction=direction,
            from_room=self.room,
            to_room=destination,
            name=name,
        )
        reverse_face = None
        if reciprocal:
            reverse_direction = adv_consts.REVERSE_DIRECTIONS[direction]
            reverse_face = Door.objects.create(
                doorway=doorway,
                direction=reverse_direction,
                from_room=destination,
                to_room=self.room,
                name=name,
            )
        return doorway, face, reverse_face

    def _create_key_items(self, count=1):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug=f"brass-key-{ItemDefinition.objects.count()}",
            name="a brass key",
            item_type=adv_consts.ITEM_TYPE_KEY,
            keywords="brass key",
        )
        items = [
            Item.objects.create(
                world=self.spawn_world,
                container=self.player,
                definition=definition,
                definition_slug_snapshot=definition.slug,
                type=adv_consts.ITEM_TYPE_KEY,
                name=definition.name,
                keywords=definition.keywords,
            )
            for _ in range(count)
        ]
        return definition, items

    def _create_active_player(self, name, *, room=None, world=None):
        player = self.create_player(
            name,
            room=room or self.room,
            world=world or self.spawn_world,
        )
        player.in_game = True
        player.health = 10
        player.stamina = 100
        player.save(update_fields=["in_game", "health", "stamina"])
        return player

    def _messages(
        self,
        captured,
        event_type,
        *,
        recipient=None,
    ):
        return [
            entry["message"]
            for entry in captured
            if entry["message"].get("type") == event_type
            and (recipient is None or entry["player_key"] == recipient)
        ]

    def _make_due(self, action):
        due_at = timezone.now()
        PreparedGameAction.objects.filter(pk=action.pk).update(
            run_at=due_at - timedelta(seconds=1)
        )
        return due_at

    def test_target_resolution_accepts_alias_and_name_direction_and_reports_ambiguity(
        self,
    ):
        _, east_face, _ = self._create_door()
        self.room.create_at(adv_consts.DIRECTION_NORTH)
        _, north_face, _ = self._create_door(
            direction=adv_consts.DIRECTION_NORTH,
        )

        self.assertEqual(resolve_door_target(self.room, "e").face, east_face)
        self.assertEqual(
            resolve_door_target(self.room, "iron gate east").face,
            east_face,
        )

        with self.assertRaises(ActionError) as raised:
            resolve_door_target(self.room, "iron gate")
        self.assertEqual(raised.exception.code, "ambiguous_door")
        self.assertEqual(
            {entry["direction"] for entry in raised.exception.data["doors"]},
            {east_face.direction, north_face.direction},
        )
        self.assertTrue(
            all(
                isinstance(entry["key"], str)
                and entry["key"].startswith("door.")
                for entry in raised.exception.data["doors"]
            )
        )

        east_face.name = "passage north"
        east_face.save(update_fields=["name"])
        self.assertEqual(
            resolve_door_target(self.room, "passage north").face,
            east_face,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "open")
        error = self._messages(
            messages,
            "cmd.open.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(error["data"]["code"], "door_target_required")

    def test_open_implicitly_unlocks_and_consumes_exactly_one_matching_key(self):
        key_definition, key_items = self._create_key_items(count=2)
        doorway, _, reverse_face = self._create_door(
            default_state=adv_consts.DOOR_STATE_LOCKED,
            key=key_definition,
            destroy_key=True,
        )
        observer = self._create_active_player(
            "Observer",
            room=reverse_face.from_room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "open east")

        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(state.revision, 1)
        self.assertFalse(Item.objects.filter(pk=key_items[0].pk).exists())
        self.assertTrue(Item.objects.filter(pk=key_items[1].pk).exists())
        self.assertEqual(
            self.player.inventory.filter(definition=key_definition).count(),
            1,
        )

        success = self._messages(
            messages,
            "cmd.open.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(
            (success["data"]["previous_state"], success["data"]["state"]),
            (adv_consts.DOOR_STATE_LOCKED, adv_consts.DOOR_STATE_OPEN),
        )
        self.assertEqual(success["data"]["cause"], "open")
        self.assertEqual(len(success["data"]["door_states"]), 2)
        self.assertEqual(
            {delta["door_state"] for delta in success["data"]["door_states"]},
            {adv_consts.DOOR_STATE_OPEN},
        )
        self.assertEqual(
            len(self._messages(
                messages,
                "affect.inventory.remove",
                recipient=self.player.key,
            )),
            1,
        )
        self.assertEqual(
            len(self._messages(
                messages,
                "door.state_changed",
                recipient=observer.key,
            )),
            1,
        )

    def test_immediate_request_retry_cannot_repeat_transition_or_key_use(self):
        key_definition, key_items = self._create_key_items(count=2)
        doorway, _, _ = self._create_door(
            default_state=adv_consts.DOOR_STATE_LOCKED,
            key=key_definition,
            destroy_key=True,
        )
        request_id = str(uuid.uuid4())
        payload = {
            "text": "open east",
            "_request_id": request_id,
        }

        dispatch_command(
            command_type="text",
            player_id=self.player.id,
            payload=dict(payload),
        )
        execute_forced_door_command(
            actor=self.room,
            actor_type="room",
            runtime_world=self.spawn_world,
            room=self.room,
            command="/lock",
            selector="east",
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload=dict(payload),
            )

        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_LOCKED)
        self.assertFalse(Item.objects.filter(pk=key_items[0].pk).exists())
        self.assertTrue(Item.objects.filter(pk=key_items[1].pk).exists())
        replay = self._messages(
            messages,
            "cmd.open.success",
            recipient=self.player.key,
        )[0]
        self.assertTrue(replay["data"]["replayed"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={
                    "text": "unlock east",
                    "_request_id": request_id,
                },
            )
        conflict = self._messages(
            messages,
            "cmd.unlock.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(conflict["data"]["code"], "idempotency_conflict")

    def test_unlock_leaves_door_closed_and_preserves_reusable_key(self):
        key_definition, key_items = self._create_key_items()
        doorway, _, _ = self._create_door(
            default_state=adv_consts.DOOR_STATE_LOCKED,
            key=key_definition,
            destroy_key=False,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unlock east")

        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(state.revision, 1)
        self.assertTrue(Item.objects.filter(pk=key_items[0].pk).exists())
        success = self._messages(
            messages,
            "cmd.unlock.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(
            (success["data"]["previous_state"], success["data"]["state"]),
            (adv_consts.DOOR_STATE_LOCKED, adv_consts.DOOR_STATE_CLOSED),
        )
        self.assertEqual(success["data"]["cause"], "unlock")

    def test_feedback_uses_the_face_name_the_actor_targeted(self):
        doorway, _, reverse_face = self._create_door(
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )
        reverse_face.name = "oak hatch"
        reverse_face.save(update_fields=["name"])
        reverse_player = self._create_active_player(
            "Reverse",
            room=reverse_face.from_room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(reverse_player.id, "open west")

        success = self._messages(
            messages,
            "cmd.open.success",
            recipient=reverse_player.key,
        )[0]
        self.assertIn("oak hatch", success["text"])
        self.assertNotIn("iron gate", success["text"])
        self.assertEqual(
            DoorState.objects.get(
                world=self.spawn_world,
                doorway=doorway,
            ).state,
            adv_consts.DOOR_STATE_OPEN,
        )

    def test_close_delays_repeats_idempotently_and_completes_when_due(self):
        doorway, _, reverse_face = self._create_door()
        observer = self._create_active_player(
            "Observer",
            room=reverse_face.from_room,
        )
        with patch(
            "spawns.tasks.resolve_prepared_game_action.apply_async"
        ) as schedule:
            with capture_game_messages() as first_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "close east")

            action = PreparedGameAction.objects.get(
                player=self.player,
                status=PreparedGameAction.STATUS_PENDING,
            )
            self.assertEqual(
                action.action_type,
                PreparedGameAction.ACTION_CLOSE_DOOR,
            )
            self.assertAlmostEqual(
                (action.run_at - action.created_ts).total_seconds(),
                DOOR_ACTION_DELAY_SECONDS,
                delta=0.5,
            )
            self.assertEqual(
                DoorState.objects.get(
                    world=self.spawn_world,
                    doorway=doorway,
                ).state,
                adv_consts.DOOR_STATE_OPEN,
            )
            schedule.assert_called_once_with(
                kwargs={"action_id": action.id},
                eta=action.run_at,
            )

            with capture_game_messages() as repeated_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "close east")

        self.assertEqual(
            PreparedGameAction.objects.filter(
                player=self.player,
                status=PreparedGameAction.STATUS_PENDING,
            ).count(),
            1,
        )
        first_started = self._messages(
            first_messages,
            "cmd.close.started",
            recipient=self.player.key,
        )[0]
        repeated = self._messages(
            repeated_messages,
            "cmd.close.started",
            recipient=self.player.key,
        )[0]
        self.assertFalse(first_started["data"]["repeated"])
        self.assertTrue(repeated["data"]["repeated"])
        self.assertEqual(
            first_started["data"]["action_id"],
            repeated["data"]["action_id"],
        )
        self.assertEqual(
            len(self._messages(
                first_messages,
                "door.action_started",
                recipient=observer.key,
            )),
            1,
        )
        self.assertFalse(
            self._messages(
                repeated_messages,
                "door.action_started",
                recipient=observer.key,
            )
        )

        due_at = self._make_due(action)
        with capture_game_messages() as completed_messages:
            with self.captureOnCommitCallbacks(execute=True):
                summary = process_due_prepared_door_actions(now=due_at)

        self.assertEqual(
            summary,
            {"processed": 1, "completed": 1, "cancelled": 0},
        )
        action.refresh_from_db()
        self.assertEqual(action.status, PreparedGameAction.STATUS_COMPLETED)
        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(state.revision, 1)
        self.assertEqual(
            len(self._messages(
                completed_messages,
                "cmd.close.success",
                recipient=self.player.key,
            )),
            1,
        )
        self.assertEqual(
            len(self._messages(
                completed_messages,
                "door.state_changed",
                recipient=observer.key,
            )),
            1,
        )

    def test_delayed_close_keeps_request_pending_until_correlated_result(self):
        _, _, reverse_face = self._create_door()
        observer = self._create_active_player(
            "Observer",
            room=reverse_face.from_room,
        )
        request_id = str(uuid.uuid4())
        with patch(
            "spawns.tasks.resolve_prepared_game_action.apply_async"
        ):
            with capture_game_messages() as started_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_command(
                        command_type="text",
                        player_id=self.player.id,
                        payload={
                            "text": "close east",
                            "_request_id": request_id,
                            "_request_segment": "r.5",
                        },
                    )

        started = self._messages(
            started_messages,
            "cmd.close.started",
            recipient=self.player.key,
        )[0]
        self.assertNotIn("request_id", started["data"])
        self.assertFalse(
            self._messages(
                started_messages,
                "cmd.request.completed",
                recipient=self.player.key,
            )
        )

        action = PreparedGameAction.objects.get(player=self.player)
        due_at = self._make_due(action)
        with capture_game_messages() as completed_messages:
            with self.captureOnCommitCallbacks(execute=True):
                process_due_prepared_door_actions(now=due_at)

        completed = self._messages(
            completed_messages,
            "cmd.close.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(completed["data"]["request_id"], request_id)
        self.assertEqual(completed["data"]["request_segment"], "r.5")
        self.assertEqual(
            completed["data"]["receipt_status"],
            "completed",
        )
        observer_event = self._messages(
            completed_messages,
            "door.state_changed",
            recipient=observer.key,
        )[0]
        self.assertNotIn("request_id", observer_event["data"])
        self.assertNotIn("request_segment", observer_event["data"])

    def test_repeated_delayed_close_completes_only_the_new_request(self):
        self._create_door()
        first_request_id = str(uuid.uuid4())
        second_request_id = str(uuid.uuid4())
        with patch(
            "spawns.tasks.resolve_prepared_game_action.apply_async"
        ):
            with capture_game_messages() as first_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_command(
                        command_type="text",
                        player_id=self.player.id,
                        payload={
                            "text": "close east",
                            "_request_id": first_request_id,
                        },
                    )
            with capture_game_messages() as second_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_command(
                        command_type="text",
                        player_id=self.player.id,
                        payload={
                            "text": "close east",
                            "_request_id": second_request_id,
                        },
                    )

        self.assertFalse(
            self._messages(
                first_messages,
                "cmd.request.completed",
                recipient=self.player.key,
            )
        )
        repeated = self._messages(
            second_messages,
            "cmd.close.started",
            recipient=self.player.key,
        )[0]
        self.assertTrue(repeated["data"]["repeated"])
        second_completed = self._messages(
            second_messages,
            "cmd.request.completed",
            recipient=self.player.key,
        )[0]
        self.assertEqual(
            second_completed["data"]["request_id"],
            second_request_id,
        )

        action = PreparedGameAction.objects.get(
            player=self.player,
            status=PreparedGameAction.STATUS_PENDING,
        )
        due_at = self._make_due(action)
        with capture_game_messages() as completed_messages:
            with self.captureOnCommitCallbacks(execute=True):
                process_due_prepared_door_actions(now=due_at)
        first_completed = self._messages(
            completed_messages,
            "cmd.close.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(
            first_completed["data"]["request_id"],
            first_request_id,
        )

    def test_close_is_cancelled_when_door_revision_becomes_stale(self):
        doorway, _, _ = self._create_door()
        request_id = str(uuid.uuid4())
        with patch("spawns.tasks.resolve_prepared_game_action.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="text",
                    player_id=self.player.id,
                    payload={
                        "text": "close east",
                        "_request_id": request_id,
                    },
                )
        action = PreparedGameAction.objects.get(player=self.player)

        execute_forced_door_command(
            actor=self.room,
            actor_type="room",
            runtime_world=self.spawn_world,
            room=self.room,
            command="/open",
            selector="east",
        )
        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(state.revision, 1)

        due_at = self._make_due(action)
        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                summary = process_due_prepared_door_actions(now=due_at)

        self.assertEqual(
            summary,
            {"processed": 1, "completed": 0, "cancelled": 1},
        )
        action.refresh_from_db()
        self.assertEqual(action.status, PreparedGameAction.STATUS_CANCELLED)
        self.assertEqual(action.failure_code, "doorway_stale")
        cancelled = self._messages(
            messages,
            "cmd.close.cancelled",
            recipient=self.player.key,
        )[0]
        self.assertEqual(cancelled["data"]["code"], "doorway_stale")
        self.assertEqual(cancelled["data"]["request_id"], request_id)
        self.assertEqual(cancelled["data"]["request_segment"], "r")
        self.assertEqual(
            cancelled["data"]["receipt_status"],
            "completed",
        )

    def test_open_noop_does_not_invalidate_another_players_close(self):
        doorway, _, _ = self._create_door()
        other_player = self._create_active_player("Closer")

        with patch("spawns.tasks.resolve_prepared_game_action.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(other_player.id, "close east")
        action = PreparedGameAction.objects.get(player=other_player)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "open east")

        noop = self._messages(
            messages,
            "cmd.open.success",
            recipient=self.player.key,
        )[0]
        self.assertFalse(noop["data"]["changed"])
        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.revision, 0)
        self.assertEqual(action.expected_revision, 0)

        due_at = self._make_due(action)
        with self.captureOnCommitCallbacks(execute=True):
            summary = process_due_prepared_door_actions(now=due_at)

        self.assertEqual(
            summary,
            {"processed": 1, "completed": 1, "cancelled": 0},
        )
        action.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(action.status, PreparedGameAction.STATUS_COMPLETED)
        self.assertEqual(state.state, adv_consts.DOOR_STATE_CLOSED)

    def test_locking_open_door_revalidates_key_at_completion(self):
        key_definition, key_items = self._create_key_items()
        doorway, _, _ = self._create_door(key=key_definition)

        with patch("spawns.tasks.resolve_prepared_game_action.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "lock east")
        action = PreparedGameAction.objects.get(player=self.player)
        self.assertEqual(
            action.action_type,
            PreparedGameAction.ACTION_LOCK_DOOR,
        )

        key_items[0].delete()
        due_at = self._make_due(action)
        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                summary = process_due_prepared_door_actions(now=due_at)

        self.assertEqual(
            summary,
            {"processed": 1, "completed": 0, "cancelled": 1},
        )
        action.refresh_from_db()
        self.assertEqual(action.status, PreparedGameAction.STATUS_CANCELLED)
        self.assertEqual(action.failure_code, "missing_key")
        self.assertEqual(
            DoorState.objects.get(
                world=self.spawn_world,
                doorway=doorway,
            ).state,
            adv_consts.DOOR_STATE_OPEN,
        )
        cancelled = self._messages(
            messages,
            "cmd.lock.cancelled",
            recipient=self.player.key,
        )[0]
        self.assertEqual(cancelled["data"]["code"], "missing_key")

    def test_forced_commands_require_a_trusted_issuer(self):
        doorway, _, _ = self._create_door()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/lock east")
        error = self._messages(
            messages,
            "cmd./lock.error",
            recipient=self.player.key,
        )[0]
        self.assertIn("permission", error["text"].lower())
        self.assertFalse(
            DoorState.objects.filter(
                world=self.spawn_world,
                doorway=doorway,
            ).exists()
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                payload={"text": "/lock east"},
                script_source=True,
            )
        error = self._messages(
            messages,
            "cmd./lock.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(error["data"]["code"], "door_issuer_not_allowed")

    def test_builder_forces_door_immediately_and_emits_shared_deltas(self):
        key_definition, _ = self._create_key_items()
        self.player.inventory.all().delete()
        doorway, _, reverse_face = self._create_door(key=key_definition)
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])
        observer = self._create_active_player(
            "Observer",
            room=reverse_face.from_room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/lock east")

        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_LOCKED)
        self.assertEqual(state.revision, 1)
        self.assertFalse(
            PreparedGameAction.objects.filter(player=self.player).exists()
        )
        success = self._messages(
            messages,
            "cmd./lock.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(success["data"]["cause"], "force_lock")
        self.assertTrue(success["data"]["changed"])
        self.assertEqual(len(success["data"]["door_states"]), 2)
        self.assertEqual(
            len(self._messages(
                messages,
                "door.state_changed",
                recipient=observer.key,
            )),
            1,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/close east")
        state.refresh_from_db()
        self.assertEqual(state.state, adv_consts.DOOR_STATE_LOCKED)
        self.assertEqual(state.revision, 1)
        close = self._messages(
            messages,
            "cmd./close.success",
            recipient=self.player.key,
        )[0]
        self.assertFalse(close["data"]["changed"])
        self.assertFalse(
            self._messages(
                messages,
                "door.state_changed",
                recipient=observer.key,
            )
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/open east")
        state.refresh_from_db()
        self.assertEqual(state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(state.revision, 2)
        self.assertTrue(
            self._messages(
                messages,
                "cmd./open.success",
                recipient=self.player.key,
            )
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/unlock east")
        state.refresh_from_db()
        self.assertEqual(state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(state.revision, 3)
        unlock = self._messages(
            messages,
            "cmd./unlock.success",
            recipient=self.player.key,
        )[0]
        self.assertTrue(unlock["data"]["changed"])

    def test_forced_open_close_and_lock_can_replace_the_room_message(self):
        doorway, _, _ = self._create_door(
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        commands = (
            (
                "/close east The bronze doors close without a sound.",
                adv_consts.DOOR_STATE_CLOSED,
                "The bronze doors close without a sound.",
            ),
            (
                "/open iron gate -- The bronze doors swing open.",
                adv_consts.DOOR_STATE_OPEN,
                "The bronze doors swing open.",
            ),
            (
                "/lock east The bronze doors close behind you. Nobody touches them.",
                adv_consts.DOOR_STATE_LOCKED,
                "The bronze doors close behind you. Nobody touches them.",
            ),
        )

        for command, expected_state, expected_text in commands:
            with self.subTest(command=command):
                with capture_game_messages() as messages:
                    dispatch_command(
                        command_type="text",
                        actor_type="room",
                        actor_id=self.room.id,
                        payload={
                            "text": command,
                            "runtime_world_id": self.spawn_world.id,
                        },
                        script_source=True,
                    )

                state = DoorState.objects.get(
                    world=self.spawn_world,
                    doorway=doorway,
                )
                self.assertEqual(state.state, expected_state)
                notifications = self._messages(
                    messages,
                    "door.state_changed",
                    recipient=self.player.key,
                )
                self.assertEqual(len(notifications), 1)
                self.assertEqual(notifications[0]["text"], expected_text)

    def test_forced_door_custom_message_is_silent_for_a_noop(self):
        self._create_door(default_state=adv_consts.DOOR_STATE_LOCKED)

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/lock east This should not be broadcast.",
                    "runtime_world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        self.assertFalse(
            self._messages(
                messages,
                "door.state_changed",
                recipient=self.player.key,
            )
        )

    def test_direction_prefixed_door_name_keeps_its_existing_meaning(self):
        doorway, _, _ = self._create_door(
            name="east gate",
            default_state=adv_consts.DOOR_STATE_OPEN,
        )

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/lock east gate",
                    "runtime_world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        self.assertEqual(
            DoorState.objects.get(
                world=self.spawn_world,
                doorway=doorway,
            ).state,
            adv_consts.DOOR_STATE_LOCKED,
        )
        notification = self._messages(
            messages,
            "door.state_changed",
            recipient=self.player.key,
        )[0]
        self.assertEqual(notification["text"], "The east gate closes and locks.")

    def test_real_transition_keeps_state_event_without_observers(self):
        doorway, _, _ = self._create_door(reciprocal=False)
        self.player.in_game = False
        self.player.save(update_fields=["in_game"])

        result = execute_forced_door_command(
            actor=self.room,
            actor_type="room",
            runtime_world=self.spawn_world,
            room=self.room,
            command="/close",
            selector="east",
        )

        state_event = next(
            event for event in result.events
            if event.type == "door.state_changed"
        )
        self.assertEqual(state_event.recipients, [])
        self.assertEqual(
            state_event.data["doorway"]["id"],
            doorway.id,
        )

    def test_room_and_mob_scripts_can_force_door_state(self):
        doorway, _, _ = self._create_door(
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )

        with capture_game_messages() as room_messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.room.id,
                payload={
                    "text": "/lock east",
                    "runtime_world_id": self.spawn_world.id,
                },
                script_source=True,
            )

        state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(state.state, adv_consts.DOOR_STATE_LOCKED)
        room_success = self._messages(
            room_messages,
            "cmd./lock.success",
            recipient=self.room.key,
        )[0]
        self.assertEqual(room_success["data"]["issuer"]["type"], "room")

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Gatekeeper",
            keywords="gatekeeper",
        )
        with capture_game_messages() as mob_messages:
            dispatch_command(
                command_type="text",
                actor_type="mob",
                actor_id=mob.id,
                payload={"text": "/open east"},
                script_source=True,
            )

        state.refresh_from_db()
        self.assertEqual(state.state, adv_consts.DOOR_STATE_OPEN)
        mob_success = self._messages(
            mob_messages,
            "cmd./open.success",
            recipient=mob.key,
        )[0]
        self.assertEqual(mob_success["data"]["issuer"]["type"], "mob")

    def test_runtime_worlds_keep_independent_door_states(self):
        doorway, _, _ = self._create_door()
        other_runtime = self.world.create_spawn_world()
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])

        dispatch_text_command(self.player.id, "/lock east")
        first_state = DoorState.objects.get(
            world=self.spawn_world,
            doorway=doorway,
        )
        self.assertEqual(first_state.state, adv_consts.DOOR_STATE_LOCKED)
        self.assertFalse(
            DoorState.objects.filter(
                world=other_runtime,
                doorway=doorway,
            ).exists()
        )
        self.assertEqual(
            door_state_lookup(other_runtime, [self.room.id])[self.room.id][
                adv_consts.DIRECTION_EAST
            ],
            adv_consts.DOOR_STATE_OPEN,
        )

        dispatch_command(
            command_type="text",
            actor_type="room",
            actor_id=self.room.id,
            payload={
                "text": "/close east",
                "runtime_world_id": other_runtime.id,
            },
            script_source=True,
        )
        second_state = DoorState.objects.get(
            world=other_runtime,
            doorway=doorway,
        )
        first_state.refresh_from_db()
        self.assertEqual(first_state.state, adv_consts.DOOR_STATE_LOCKED)
        self.assertEqual(second_state.state, adv_consts.DOOR_STATE_CLOSED)

    def test_room_door_state_lookup_stays_two_bounded_queries(self):
        doorway, _, _ = self._create_door()
        DoorState.objects.create(
            world=self.spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=4,
        )

        with self.assertNumQueries(2):
            lookup = door_state_lookup(
                self.spawn_world,
                [self.room.id, self.east_room.id],
            )

        self.assertEqual(
            lookup[self.room.id][adv_consts.DIRECTION_EAST],
            adv_consts.DOOR_STATE_CLOSED,
        )
        self.assertEqual(
            lookup[self.east_room.id][adv_consts.DIRECTION_WEST],
            adv_consts.DOOR_STATE_CLOSED,
        )

    def test_directional_door_lookup_is_one_query_and_runtime_isolated(self):
        doorway, _, _ = self._create_door(
            name="bronze",
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )
        other_runtime = self.world.create_spawn_world()
        DoorState.objects.create(
            world=other_runtime,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_OPEN,
        )

        with self.assertNumQueries(1):
            payload = directional_door_payload(
                self.spawn_world,
                self.room.id,
                adv_consts.DIRECTION_EAST,
            )

        self.assertEqual(
            payload,
            {
                "id": payload["id"],
                "key": f"door.{payload['id']}",
                "name": "bronze",
                "direction": adv_consts.DIRECTION_EAST,
                "state": adv_consts.DOOR_STATE_CLOSED,
            },
        )
        self.assertFalse(
            DoorState.objects.filter(
                world=self.spawn_world,
                doorway=doorway,
            ).exists()
        )

    def test_look_direction_reports_effective_door_state(self):
        doorway, _, _ = self._create_door(
            name="bronze",
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )

        with capture_game_messages() as closed_messages:
            dispatch_text_command(self.player.id, "look east")

        closed = self._messages(
            closed_messages,
            "cmd.look.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(closed["text"], "The bronze is closed.")
        self.assertEqual(closed["data"]["target_type"], "door")
        self.assertEqual(
            closed["data"]["target"],
            {
                "id": closed["data"]["target"]["id"],
                "key": f"door.{closed['data']['target']['id']}",
                "name": "bronze",
                "direction": adv_consts.DIRECTION_EAST,
                "state": adv_consts.DOOR_STATE_CLOSED,
            },
        )
        self.assertFalse(
            DoorState.objects.filter(
                world=self.spawn_world,
                doorway=doorway,
            ).exists()
        )

        DoorState.objects.create(
            world=self.spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_OPEN,
        )
        with capture_game_messages() as open_messages:
            dispatch_text_command(self.player.id, "look e")

        opened = self._messages(
            open_messages,
            "cmd.look.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(opened["text"], "The bronze is open.")
        self.assertEqual(
            opened["data"]["target"]["state"],
            adv_consts.DOOR_STATE_OPEN,
        )

        DoorState.objects.filter(
            world=self.spawn_world,
            doorway=doorway,
        ).update(state=adv_consts.DOOR_STATE_LOCKED)
        with capture_game_messages() as locked_messages:
            dispatch_text_command(self.player.id, "look east")

        locked = self._messages(
            locked_messages,
            "cmd.look.success",
            recipient=self.player.key,
        )[0]
        self.assertEqual(locked["text"], "The bronze is locked.")
        self.assertEqual(
            locked["data"]["target"]["state"],
            adv_consts.DOOR_STATE_LOCKED,
        )

    def test_movement_blocked_message_uses_local_door_face_name(self):
        _, _, reverse_face = self._create_door(
            name="bronze",
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )
        reverse_face.name = "inner bronze"
        reverse_face.save(update_fields=["name"])

        with capture_game_messages() as outward_messages:
            dispatch_text_command(self.player.id, "east")

        outward_error = self._messages(
            outward_messages,
            "cmd.move.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(outward_error["text"], "The bronze is closed.")
        self.assertEqual(
            outward_error["data"]["door"],
            {
                "key": outward_error["data"]["door"]["key"],
                "name": "bronze",
                "direction": adv_consts.DIRECTION_EAST,
                "state": adv_consts.DOOR_STATE_CLOSED,
            },
        )

        self.player.room = self.east_room
        self.player.save(update_fields=["room"])
        with capture_game_messages() as inward_messages:
            dispatch_text_command(self.player.id, "west")

        inward_error = self._messages(
            inward_messages,
            "cmd.move.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(inward_error["text"], "The inner bronze is closed.")

    def test_movement_is_blocked_by_shared_runtime_door_state(self):
        doorway, _, _ = self._create_door(
            default_state=adv_consts.DOOR_STATE_LOCKED,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        error = self._messages(
            messages,
            "cmd.move.error",
            recipient=self.player.key,
        )[0]
        self.assertEqual(error["data"]["code"], "closed_door")
        self.assertEqual(error["text"], "The iron gate is locked.")
        self.assertEqual(error["data"]["error"], "The iron gate is locked.")
        # The rejected move rolls back first-touch materialization; reads
        # still resolve the authored default without leaving a sparse row.
        self.assertFalse(
            DoorState.objects.filter(
                world=self.spawn_world,
                doorway=doorway,
            ).exists()
        )
        self.assertEqual(
            door_state_lookup(
                self.spawn_world,
                [self.room.id],
            )[self.room.id][adv_consts.DIRECTION_EAST],
            adv_consts.DOOR_STATE_LOCKED,
        )

    def test_movement_cancels_pending_close_before_crossing_open_door(self):
        doorway, _, _ = self._create_door()
        with patch("spawns.tasks.resolve_prepared_game_action.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "close east")
        action = PreparedGameAction.objects.get(player=self.player)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(self.player.room_id, self.east_room.id)
        self.assertEqual(action.status, PreparedGameAction.STATUS_CANCELLED)
        self.assertEqual(action.failure_code, "actor_moved")
        self.assertEqual(
            DoorState.objects.get(
                world=self.spawn_world,
                doorway=doorway,
            ).state,
            adv_consts.DOOR_STATE_OPEN,
        )
        self.assertTrue(
            self._messages(
                messages,
                "cmd.move.success",
                recipient=self.player.key,
            )
        )
        cancelled = self._messages(
            messages,
            "cmd.close.cancelled",
            recipient=self.player.key,
        )[0]
        self.assertEqual(cancelled["data"]["code"], "actor_moved")
