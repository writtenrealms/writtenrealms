from unittest.mock import patch

from config import constants as adv_consts
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from django.db import connection
from django.test.utils import CaptureQueriesContext
from spawns.models import CombatEncounter, DoorState, Mob
from tests.base import WorldTestCase
from worlds.models import Door, RoomFlag
from wr2_tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestMobTracker(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
        )
        self.player.health = self.stats["health_max"]
        self.player.energy = self.stats["energy_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(update_fields=["health", "energy", "stamina", "in_game"])
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.combat_system = normalize_combat_system({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 1,
                    "use_weapon_damage": False,
                    "can_dodge": False,
                    "can_crit": False,
                    "mitigation": {
                        "armor": False,
                        "resilience": False,
                    },
                    "minimum": 0,
                },
            },
        })
        self.world.config.save(
            update_fields=["combat_resolution_interval", "combat_system"]
        )
        self.destination = self.room.create_at(adv_consts.DIRECTION_EAST)

    def _mob(
        self,
        name="a tracker",
        *,
        tracker=True,
        trait_instances=None,
        target_priority=0,
    ):
        if trait_instances is None:
            trait_instances = [{"key": "tracker"}] if tracker else []
        return Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name=name,
            keywords=name.removeprefix("a "),
            health=max(self.stats["attack_power"] * 100, 100),
            health_max=max(self.stats["attack_power"] * 100, 100),
            attack_power=0,
            fights_back=False,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
            target_priority=target_priority,
            trait_instances=trait_instances,
        )

    def _encounter(self, mob, *, round_number=0, resolution_interval=1.5):
        return CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
            round_number=round_number,
            resolution_interval=resolution_interval,
        )

    def _player_messages_by_type(self, messages, message_type):
        return [
            entry["message"]
            for entry in messages
            if entry["player_key"] == self.player.key
            and entry["message"].get("type") == message_type
        ]

    def test_pre_lock_move_tracker_follows_and_reengages_while_non_tracker_stays(self):
        tracker = self._mob(target_priority=10)
        ordinary_mob = self._mob("an ordinary guard", tracker=False)
        self._encounter(tracker)
        self._encounter(ordinary_mob)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        ordinary_mob.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertEqual(ordinary_mob.room_id, self.room.id)
        self.assertTrue(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                room=self.room,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_pre_lock_move_tracker_does_not_enter_no_roam_room(self):
        RoomFlag.objects.create(
            room=self.destination,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        tracker = self._mob()
        origin_encounter = self._encounter(tracker)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        origin_encounter.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertEqual(origin_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_no_roam_flag_added_before_chase_resolution_stops_tracker(self):
        tracker = self._mob()
        self._encounter(tracker)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                dispatch_text_command(self.player.id, "east")
            self.assertEqual(len(callbacks), 1)
            RoomFlag.objects.create(
                room=self.destination,
                code=adv_consts.ROOM_FLAG_NO_ROAM,
            )
            callbacks[0]()

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_pre_lock_move_tracker_does_not_leave_no_roam_room(self):
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        tracker = self._mob()
        self._encounter(tracker)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_tracker_candidate_collection_remains_one_bounded_query(self):
        from spawns.actions.mob_movement import load_player_escape_encounters

        for index in range(5):
            mob = self._mob(f"tracker {index}")
            self._encounter(mob)

        with CaptureQueriesContext(connection) as queries:
            encounters = load_player_escape_encounters(
                player=self.player,
                origin_room_id=self.room.id,
            )

        self.assertEqual(len(encounters), 5)
        self.assertEqual(len(queries), 1)

    def test_tracker_room_boundary_lookup_remains_one_bounded_query(self):
        from spawns.actions.mob_movement import _load_tracker_rooms

        RoomFlag.objects.create(
            room=self.destination,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )

        with CaptureQueriesContext(connection) as queries:
            origin_room, destination_room = _load_tracker_rooms(
                self.room.id,
                self.destination.id,
            )

        self.assertEqual(len(queries), 1)
        self.assertFalse(origin_room._tracker_no_roam)
        self.assertTrue(destination_room._tracker_no_roam)

    def test_move_is_rejected_once_combat_has_locked(self):
        tracker = self._mob()
        encounter = self._encounter(tracker, round_number=1)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.error")
        )

    def test_combat_lock_error_precedes_route_validation(self):
        tracker = self._mob()
        self._encounter(tracker, round_number=1)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "north")

        errors = self._player_messages_by_type(messages, "cmd.move.error")
        self.assertEqual(errors[0]["data"]["code"], "in_combat")

    def test_finished_encounter_does_not_keep_player_combat_locked(self):
        tracker = self._mob()
        encounter = self._encounter(tracker, round_number=1)
        encounter.status = CombatEncounter.STATUS_FINISHED
        encounter.save(update_fields=["status"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.move.error")
        )

    def test_dead_mob_encounter_does_not_keep_player_combat_locked(self):
        tracker = self._mob()
        self._encounter(tracker, round_number=1)
        tracker.health = 0
        tracker.save(update_fields=["health"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.move.error")
        )

    def test_displaced_mob_encounter_does_not_keep_player_combat_locked(self):
        tracker = self._mob()
        self._encounter(tracker, round_number=1)
        other_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        tracker.room = other_room
        tracker.save(update_fields=["room"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, other_room.id)
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.move.error")
        )

    def test_flee_tracker_follows_and_reengages_while_non_tracker_stays(self):
        tracker = self._mob(target_priority=10)
        ordinary_mob = self._mob("an ordinary guard", tracker=False)
        tracker_encounter = self._encounter(tracker, resolution_interval=-1)
        ordinary_encounter = self._encounter(ordinary_mob, resolution_interval=-1)

        dispatch_text_command(self.player.id, "flee")
        tracker_encounter.refresh_from_db()
        self.assertEqual(tracker_encounter.pending_flee["status"], "ready")

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        ordinary_mob.refresh_from_db()
        tracker_encounter.refresh_from_db()
        ordinary_encounter.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertEqual(ordinary_mob.room_id, self.room.id)
        self.assertEqual(tracker_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(
            ordinary_encounter.status,
            CombatEncounter.STATUS_FINISHED,
        )
        self.assertTrue(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=ordinary_mob,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        flee_messages = self._player_messages_by_type(messages, "cmd.flee.success")
        self.assertTrue(
            any(message["text"] == "You flee east." for message in flee_messages)
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_flee_tracker_does_not_enter_no_roam_room(self):
        RoomFlag.objects.create(
            room=self.destination,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        tracker = self._mob()
        encounter = self._encounter(tracker, resolution_interval=-1)

        dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "ready")

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.flee.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_flee_tracker_does_not_leave_no_roam_room(self):
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        tracker = self._mob()
        encounter = self._encounter(tracker, resolution_interval=-1)

        dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "ready")

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.flee.success")
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_passive_tracker_from_an_active_fight_still_reengages(self):
        tracker = self._mob()
        tracker.aggression = adv_consts.MOB_AGGRESSION_PASSIVE
        tracker.save(update_fields=["aggression"])
        self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "east")

        tracker.refresh_from_db()
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertTrue(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_duplicate_tracker_trait_instances_only_chase_and_reengage_once(self):
        tracker = self._mob(
            trait_instances=[
                {"key": "tracker", "source": "mob_definition"},
                {"key": "tracker", "source": "spawn_plan"},
            ]
        )
        self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertEqual(len(callbacks), 1)
                callbacks[0]()
                callbacks[0]()

        tracker.refresh_from_db()
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertEqual(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).count(),
            1,
        )
        self.assertEqual(
            len(self._player_messages_by_type(messages, "cmd.kill.success")),
            1,
        )

    def test_failed_reengagement_rolls_back_movement_and_can_retry(self):
        tracker = self._mob()
        origin_encounter = self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertEqual(len(callbacks), 1)
                with self.assertLogs("spawns.handlers.movement", level="ERROR"):
                    with patch(
                        "spawns.actions.combat."
                        "ScanRoomAggroAction._start_aggro_encounter",
                        side_effect=RuntimeError("temporary failure"),
                    ):
                        callbacks[0]()

                tracker.refresh_from_db()
                origin_encounter.refresh_from_db()
                self.assertEqual(tracker.room_id, self.room.id)
                self.assertEqual(
                    origin_encounter.status,
                    CombatEncounter.STATUS_FINISHED,
                )
                self.assertFalse(
                    tracker.trait_instances[0]
                    .get("runtime", {})
                    .get("processed_chase_keys")
                )

                callbacks[0]()

        tracker.refresh_from_db()
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertEqual(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).count(),
            1,
        )
        self.assertEqual(
            len(self._player_messages_by_type(messages, "cmd.kill.success")),
            1,
        )

    def test_failed_movement_event_build_rolls_back_chase_and_can_retry(self):
        tracker = self._mob()
        self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                with self.assertLogs("spawns.handlers.movement", level="ERROR"):
                    with patch(
                        "spawns.actions.mob_movement._tracker_movement_events",
                        side_effect=RuntimeError("temporary event failure"),
                    ):
                        callbacks[0]()

                tracker.refresh_from_db()
                self.assertEqual(tracker.room_id, self.room.id)
                self.assertFalse(
                    tracker.trait_instances[0]
                    .get("runtime", {})
                    .get("processed_chase_keys")
                )

                callbacks[0]()

        tracker.refresh_from_db()
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertEqual(
            len(self._player_messages_by_type(messages, "cmd.kill.success")),
            1,
        )

    def test_zero_interval_chase_can_delete_tracker_without_losing_events(self):
        self.world.config.combat_resolution_interval = 0
        self.world.config.save(update_fields=["combat_resolution_interval"])
        tracker = self._mob()
        tracker.health = 1
        tracker.health_max = 1
        tracker.save(update_fields=["health", "health_max"])
        tracker_id = tracker.id
        self._encounter(tracker)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertFalse(Mob.objects.filter(pk=tracker_id).exists())
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.move.success")
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "notification.movement.enter")
        )

    def test_player_and_tracker_movement_events_are_published_in_order(self):
        origin_watcher = self.create_player(
            "Origin Watcher",
            user=self.create_user("origin-watcher@example.com"),
            room=self.room,
        )
        destination_watcher = self.create_player(
            "Destination Watcher",
            user=self.create_user("destination-watcher@example.com"),
            room=self.destination,
        )
        for watcher in (origin_watcher, destination_watcher):
            watcher.in_game = True
            watcher.save(update_fields=["in_game"])

        tracker = self._mob()
        self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "east")

        def relevant_types(player, allowed):
            return [
                entry["message"]["type"]
                for entry in messages
                if entry["player_key"] == player.key
                and entry["message"].get("type") in allowed
            ]

        self.assertEqual(
            relevant_types(
                self.player,
                {
                    "cmd.move.success",
                    "notification.movement.enter",
                    "cmd.kill.success",
                },
            ),
            [
                "cmd.move.success",
                "notification.movement.enter",
                "cmd.kill.success",
            ],
        )
        self.assertEqual(
            relevant_types(
                origin_watcher,
                {"notification.movement.exit"},
            ),
            [
                "notification.movement.exit",
                "notification.movement.exit",
            ],
        )
        self.assertEqual(
            relevant_types(
                destination_watcher,
                {"notification.movement.enter"},
            ),
            [
                "notification.movement.enter",
                "notification.movement.enter",
            ],
        )

    def test_tracker_condition_can_limit_chase_source(self):
        tracker = self._mob(
            trait_instances=[{
                "key": "tracker",
                "conditions": {"eq": ["event.source", "flee"]},
            }]
        )
        self._encounter(tracker)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "east")

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_tracker_condition_can_allow_flee_chase_source(self):
        tracker = self._mob(
            trait_instances=[{
                "key": "tracker",
                "conditions": {"eq": ["event.source", "flee"]},
            }]
        )
        encounter = self._encounter(tracker, resolution_interval=-1)

        dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "ready")

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "flee")

        tracker.refresh_from_db()
        self.assertEqual(tracker.room_id, self.destination.id)
        self.assertTrue(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertTrue(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_tracker_that_dies_before_chase_resolution_does_not_follow(self):
        tracker = self._mob()
        self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertTrue(callbacks)
                tracker.health = 0
                tracker.save(update_fields=["health"])
                for callback in callbacks:
                    callback()

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_tracker_moved_before_chase_resolution_does_not_teleport(self):
        tracker = self._mob()
        self._encounter(tracker)
        other_room = self.room.create_at(adv_consts.DIRECTION_NORTH)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertTrue(callbacks)
                tracker.room = other_room
                tracker.save(update_fields=["room"])
                for callback in callbacks:
                    callback()

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, other_room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_deleted_tracker_does_not_break_pending_chase_resolution(self):
        tracker = self._mob()
        encounter = self._encounter(tracker)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertTrue(callbacks)
                tracker.delete()
                encounter.refresh_from_db()
                self.assertIsNone(encounter.mob_id)
                for callback in callbacks:
                    callback()

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                room=self.destination,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_player_who_leaves_expected_destination_is_not_reengaged(self):
        tracker = self._mob()
        self._encounter(tracker)
        later_room = self.destination.create_at(adv_consts.DIRECTION_EAST)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertTrue(callbacks)
                self.player.room = later_room
                self.player.save(update_fields=["room"])
                for callback in callbacks:
                    callback()

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, later_room.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )

    def test_door_closed_before_chase_resolution_leaves_tracker_behind(self):
        tracker = self._mob()
        self._encounter(tracker)
        door = Door.objects.create(
            direction=adv_consts.DIRECTION_EAST,
            from_room=self.room,
            to_room=self.destination,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    dispatch_text_command(self.player.id, "east")
                self.assertTrue(callbacks)
                DoorState.objects.create(
                    door=door,
                    world=self.spawn_world,
                    state=adv_consts.DOOR_STATE_CLOSED,
                )
                for callback in callbacks:
                    callback()

        self.player.refresh_from_db()
        tracker.refresh_from_db()
        self.assertEqual(self.player.room_id, self.destination.id)
        self.assertEqual(tracker.room_id, self.room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=tracker,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(
            self._player_messages_by_type(messages, "cmd.kill.success")
        )
