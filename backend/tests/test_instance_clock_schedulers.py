from datetime import timedelta
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from builders.models import MobDefinition, SpawnEntry, SpawnPlan, SpawnPlanRun, Trigger
from builders.currencies import create_currency
from builders.models import MerchantProfile
from config import constants as adv_consts
from spawns.actions.doors import (
    DOOR_ACTION_DELAY_SECONDS,
    execute_player_door_command,
    process_due_prepared_door_actions,
    resolve_prepared_door_action,
)
from spawns.instance_clock import simulation_scope
from spawns.instance_time_schedulers import drain_due_schedulers
from spawns.actions.base import ActionError
from spawns.loading import run_spawn_plans_for_world
from spawns.models import DoorState, Mob, PreparedGameAction, ScheduledTriggerRun
from spawns.models import InstanceClockWork, MerchantRuntime
from spawns.merchants import (
    _cache_merchant_selection, _cached_merchant_selection_id,
    MERCHANT_SELECTION_CACHE_TIMEOUT_SECONDS,
    process_due_merchant_restocks, resolve_merchant_runtime, restock_if_due, restock_merchant,
)
from spawns.trigger_gates import claim_gate, gate_is_allowed, release_gate
from spawns.trigger_steps import advance_due_trigger_run, process_due_trigger_runs, start_trigger_steps
from spawns.triggers import _schedule_trigger_script_line_segments
from tests.base import WorldTestCase
from worlds.models import Door, Doorway, InstanceRun, Room, ZoneDoorResetSchedule
from worlds.tasks import _disconnect_idle_players, run_world_spawn_plans


class TestInstanceClockSchedulers(WorldTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.clock_start = timezone.now() - timedelta(days=2)
        self.run = InstanceRun.objects.create(
            base_world=self.world,
            template_world=self.world,
            spawned_world=self.spawn_world,
            ref="clock-test",
            owner=self.player,
            single_player=True,
            time_control=True,
            time_paused=True, pause_in_combat=True,
            simulation_time=self.clock_start,
        )
        self.player.in_game = True
        self.player.health = 10
        self.player.stamina = 100
        self.player.save(update_fields=["in_game", "health", "stamina"])

    def _door(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            from_room=self.room,
            to_room=destination,
            direction=adv_consts.DIRECTION_EAST,
            name="iron gate",
        )
        return doorway

    def _trigger(self, delay=2):
        return Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Clock chime",
            match="chime",
            steps=[{
                "after_seconds": delay,
                "actions": [{"type": "echo", "room": "trigger_room", "text": "A bell rings."}],
            }],
            conditions="",
            gate_delay=0,
        )

    def test_prepared_door_deadline_and_stale_realtime_delivery_obey_clock(self):
        doorway = self._door()
        with patch("spawns.tasks.resolve_prepared_game_action.apply_async") as enqueue:
            execute_player_door_command(
                player_id=self.player.id, command="close", selector="east",
            )
            action = PreparedGameAction.objects.get(player=self.player)
            self.assertEqual(
                action.run_at,
                self.clock_start + timedelta(seconds=DOOR_ACTION_DELAY_SECONDS),
            )
            enqueue.assert_not_called()

        self.assertIsNone(resolve_prepared_door_action(action.id, now=timezone.now()))
        self.assertEqual(process_due_prepared_door_actions(now=timezone.now())["processed"], 0)
        self.assertEqual(DoorState.objects.get(world=self.spawn_world, doorway=doorway).state, "open")

        self.run.simulation_time = action.run_at
        with simulation_scope(self.run):
            result = process_due_prepared_door_actions(world_id=self.spawn_world.id)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(DoorState.objects.get(world=self.spawn_world, doorway=doorway).state, "closed")
        action.refresh_from_db()
        self.assertGreater(action.completed_ts, self.clock_start + timedelta(days=1))

    def test_delayed_trigger_cannot_run_from_poll_or_old_continuation(self):
        trigger = self._trigger()
        started = start_trigger_steps(trigger=trigger, actor=self.player, room=self.room)
        self.assertTrue(started.started, started)
        scheduled = ScheduledTriggerRun.objects.get(pk=started.run_id)
        self.assertEqual(scheduled.started_ts, self.clock_start)
        self.assertEqual(scheduled.next_run_ts, self.clock_start + timedelta(seconds=2))
        self.assertIsNone(advance_due_trigger_run(
            run_id=scheduled.id, expected_step_index=0, now=timezone.now(),
        ))
        self.assertEqual(process_due_trigger_runs(now=timezone.now())["processed"], 0)
        self.run.simulation_time = scheduled.next_run_ts
        with simulation_scope(self.run):
            result = process_due_trigger_runs(world_id=self.spawn_world.id)
        self.assertEqual(result["completed"], 1)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, ScheduledTriggerRun.STATUS_COMPLETED)

    def test_zero_delay_trigger_started_while_waiting_defers_to_advance(self):
        trigger = self._trigger(delay=0)
        started = start_trigger_steps(trigger=trigger, actor=self.player, room=self.room)
        scheduled = ScheduledTriggerRun.objects.get(pk=started.run_id)
        self.assertEqual(scheduled.next_step_index, 0)
        with simulation_scope(self.run):
            result = process_due_trigger_runs(world_id=self.spawn_world.id)
        self.assertEqual(result["completed"], 1)

    def test_spawn_reconciliation_uses_elapsed_gameplay_time(self):
        definition = MobDefinition.objects.create(
            world=self.world, slug="clockwork", name="a clockwork guard",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.world, zone=self.zone, slug="guards", name="Guards",
            respawn_policy={"mode": "fixed", "seconds": 6},
        )
        SpawnEntry.objects.create(
            plan=plan, slug="guard", source="mobdefinition.clockwork",
            target_room=self.room, count=1,
        )
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        plan_run = SpawnPlanRun.objects.get(spawn_world=self.spawn_world, plan=plan)
        self.assertEqual(plan_run.last_reconciled_at, self.clock_start)
        Mob.objects.filter(world=self.spawn_world).delete()
        run_spawn_plans_for_world(world=self.spawn_world)
        self.assertFalse(Mob.objects.filter(world=self.spawn_world).exists())

        self.run.simulation_time = self.clock_start + timedelta(seconds=4)
        with simulation_scope(self.run):
            run_spawn_plans_for_world(world=self.spawn_world)
        self.assertFalse(Mob.objects.filter(world=self.spawn_world).exists())
        self.run.simulation_time = self.clock_start + timedelta(seconds=6)
        with simulation_scope(self.run):
            run_spawn_plans_for_world(world=self.spawn_world)
        self.assertEqual(Mob.objects.filter(world=self.spawn_world).count(), 1)

    def test_zone_door_reset_uses_gameplay_deadline(self):
        doorway = self._door()
        self.zone.door_reset_mode = "fixed"
        self.zone.door_reset_seconds = 6
        self.zone.save(update_fields=["door_reset_mode", "door_reset_seconds"])
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        schedule = ZoneDoorResetSchedule.objects.get(world=self.spawn_world, zone=self.zone)
        self.assertEqual(schedule.next_reset_ts, self.clock_start + timedelta(seconds=6))
        state = DoorState.objects.create(
            world=self.spawn_world, doorway=doorway, state="closed",
        )
        run_spawn_plans_for_world(world=self.spawn_world)
        state.refresh_from_db()
        self.assertEqual(state.state, "closed")
        self.run.simulation_time = schedule.next_reset_ts
        with simulation_scope(self.run):
            run_spawn_plans_for_world(world=self.spawn_world)
        state.refresh_from_db()
        self.assertEqual(state.state, "open")

    def test_trigger_gate_does_not_expire_while_player_thinks(self):
        scope = f"runtime:{self.spawn_world.id}:room:{self.room.id}"
        key = f"spawns.trigger_gate.clock-test.{scope}"
        with simulation_scope(self.run):
            first_claim = claim_gate(key, scope, 6)
            self.assertIsNotNone(first_claim)
        with patch("spawns.instance_clock.timezone.now", return_value=timezone.now() + timedelta(days=30)):
            self.assertFalse(gate_is_allowed(key, scope))
        self.run.simulation_time = self.clock_start + timedelta(seconds=6)
        with simulation_scope(self.run):
            self.assertTrue(gate_is_allowed(key, scope))
            second_claim = claim_gate(key, scope, 6)
            release_gate(first_claim)
            self.assertFalse(gate_is_allowed(key, scope))
            release_gate(second_claim)
            self.assertTrue(gate_is_allowed(key, scope))

    def test_background_spawn_poll_excludes_controlled_world_and_owner_stays_online(self):
        self.spawn_world.set_lifecycle(adv_consts.WORLD_LIFECYCLE_RUNNING)
        self.player.last_action_ts = self.clock_start
        self.player.save(update_fields=["last_action_ts"])
        with patch("worlds.tasks.run_spawn_plans_for_world") as spawn, patch("worlds.tasks._disconnect_idle_player") as disconnect:
            run_world_spawn_plans()
            self.assertEqual(_disconnect_idle_players(self.spawn_world), 0)
        spawn.assert_not_called()
        disconnect.assert_not_called()

    def test_legacy_multiline_script_uses_durable_gameplay_deadline(self):
        with patch("spawns.tasks.execute_trigger_script_segments.apply_async") as enqueue:
            with simulation_scope(self.run):
                errors = _schedule_trigger_script_line_segments(
                    actor=self.player, line_segments=["/echo A bell rings."], line_index=2,
                )
        self.assertEqual(errors, [])
        enqueue.assert_not_called()
        work = InstanceClockWork.objects.get(world=self.spawn_world)
        self.assertEqual(work.kind, "script_segments")
        self.assertEqual(work.due_at, self.clock_start + timedelta(seconds=4))
        self.assertEqual(work.payload["expected_world_id"], self.spawn_world.id)

    def test_merchant_stock_and_funds_only_restock_on_advancement(self):
        currency = create_currency(world=self.world, code="obol", name="Obol")
        profile = MerchantProfile.objects.create(
            world=self.world, slug="clock-shop", name="Clock shop",
            settlement_currency=currency, restock_interval_seconds=6,
            funds_mode=MerchantProfile.FUNDS_MODE_FINITE, purchase_budget=100,
        )
        runtime = MerchantRuntime.objects.create(
            world=self.spawn_world, room=self.room, profile=profile,
            settlement_currency=currency,
        )
        runtime = restock_merchant(runtime)
        self.assertEqual(runtime.last_restocked_ts, self.clock_start)
        self.assertEqual(runtime.next_restock_ts, self.clock_start + timedelta(seconds=6))
        runtime.remaining_purchase_budget = 20
        runtime.save(update_fields=["remaining_purchase_budget"])
        restock_if_due(runtime)
        restock_merchant(runtime)
        runtime.refresh_from_db()
        self.assertEqual(runtime.remaining_purchase_budget, 20)
        self.run.simulation_time = runtime.next_restock_ts
        with simulation_scope(self.run):
            self.assertEqual(process_due_merchant_restocks(world_id=self.spawn_world.id)["processed"], 1)
        runtime.refresh_from_db()
        self.assertEqual(runtime.remaining_purchase_budget, 100)

    def test_room_merchants_are_initialized_before_pause_and_read_without_writes(self):
        currency = create_currency(world=self.world, code="obol", name="Obol")
        profile = MerchantProfile.objects.create(
            world=self.world, slug="clock-shop", name="Clock shop",
            settlement_currency=currency, restock_interval_seconds=6,
        )
        self.room.merchant_profile = profile
        self.room.save(update_fields=["merchant_profile"])
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        runtime = MerchantRuntime.objects.get(world=self.spawn_world, room=self.room)
        self.assertEqual(runtime.last_restocked_ts, self.clock_start)
        with patch("spawns.merchants.create_or_update_room_merchant_runtime") as reconcile, patch("spawns.merchants.restock_if_due") as restock:
            viewed = resolve_merchant_runtime(self.player, None)
        self.assertEqual(viewed.id, runtime.id)
        reconcile.assert_not_called()
        restock.assert_not_called()

    def test_scheduler_drain_finishes_more_than_one_batch_in_same_advance(self):
        from spawns.instance_time import _drain_reactions

        for steps_count in (34, 34, 33):
            trigger = self._trigger(delay=1)
            trigger.steps = [
                {"after_seconds": 1 if index == 0 else 0,
                 "actions": [{"type": "echo", "room": "trigger_room", "text": "A bell rings."}]}
                for index in range(steps_count)
            ]
            trigger.save(update_fields=["steps"])
            started = start_trigger_steps(trigger=trigger, actor=self.player, room=self.room)
            self.assertTrue(started.started, started)
        self.run.simulation_time += timedelta(seconds=1)
        with simulation_scope(self.run) as state:
            result = drain_due_schedulers(self.run, lambda: _drain_reactions(state))
        self.assertEqual(result["processed"], 101)
        self.assertFalse(ScheduledTriggerRun.objects.filter(
            runtime_world=self.spawn_world, status=ScheduledTriggerRun.STATUS_ACTIVE,
        ).exists())

    def test_scheduler_drain_includes_zero_delay_steps_created_by_late_reaction(self):
        from spawns.instance_time import _drain_reactions

        first_trigger = self._trigger(delay=1)
        start_trigger_steps(trigger=first_trigger, actor=self.player, room=self.room)
        reaction_trigger = self._trigger(delay=0)
        reaction_trigger.steps = reaction_trigger.steps * 2
        reaction_trigger.save(update_fields=["steps"])
        reaction_started = False
        self.run.simulation_time += timedelta(seconds=1)
        with simulation_scope(self.run) as state:
            def drain_reactions():
                nonlocal reaction_started
                if state.events and not reaction_started:
                    reaction_started = True
                    result = start_trigger_steps(
                        trigger=reaction_trigger, actor=self.player, room=self.room,
                    )
                    self.assertTrue(result.started, result)
                _drain_reactions(state)
            drain_due_schedulers(self.run, drain_reactions)
        self.assertTrue(reaction_started)
        self.assertEqual(ScheduledTriggerRun.objects.get(trigger=reaction_trigger).status,
                         ScheduledTriggerRun.STATUS_COMPLETED)

    def test_scheduler_budget_exhaustion_rolls_back_all_timer_progress(self):
        trigger = self._trigger(delay=1)
        trigger.steps += [{
            "after_seconds": 0,
            "actions": [{"type": "echo", "room": "trigger_room", "text": "Another chime."}],
        }]
        trigger.save(update_fields=["steps"])
        started = start_trigger_steps(trigger=trigger, actor=self.player, room=self.room)
        self.run.simulation_time += timedelta(seconds=1)
        with patch("spawns.instance_time_schedulers.MAX_DUE_WORK_PER_ADVANCE", 1):
            with self.assertRaises(ActionError) as raised:
                with transaction.atomic(), simulation_scope(self.run):
                    drain_due_schedulers(self.run, lambda: None)
        self.assertEqual(raised.exception.code, "instance_turn_budget")
        scheduled = ScheduledTriggerRun.objects.get(pk=started.run_id)
        self.assertEqual(scheduled.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(scheduled.next_step_index, 0)

    def test_numbered_merchant_selection_expires_on_gameplay_time_only(self):
        from time import time

        for view in ("stock", "offer"):
            with self.subTest(view=view):
                lookup = {
                    "player_id": self.player.id, "runtime_id": 123,
                    "world_id": self.spawn_world.id, "view": view,
                }
                _cache_merchant_selection(**lookup, object_ids=[41, 42])
                # Simulate an hour spent considering a numbered purchase or
                # sale. Cache TTLs use wall time; this snapshot must survive.
                with patch("time.time", return_value=time() + 3600):
                    self.assertEqual(_cached_merchant_selection_id(**lookup, number=2), 42)
                    self.assertIsNone(_cached_merchant_selection_id(
                        **{**lookup, "player_id": self.player.id + 1}, number=2,
                    ))
                self.run.simulation_time = self.clock_start + timedelta(
                    seconds=MERCHANT_SELECTION_CACHE_TIMEOUT_SECONDS,
                )
                with simulation_scope(self.run):
                    self.assertIsNone(_cached_merchant_selection_id(**lookup, number=2))
                self.run.simulation_time = self.clock_start

    def test_failed_turn_rolls_back_trigger_gate_so_entry_reaction_can_retry(self):
        trigger = self._trigger(delay=0)
        trigger.gate_delay = 60
        trigger.save(update_fields=["gate_delay"])
        scope = f"runtime:{self.spawn_world.id}:room:{self.room.id}"
        with self.assertRaises(ActionError):
            with transaction.atomic(), simulation_scope(self.run):
                first = start_trigger_steps(
                    trigger=trigger, actor=self.player, room=self.room, gate_scope_key=scope,
                )
                self.assertTrue(first.started, first)
                # A later invalid prepared action or budget failure must also
                # undo this earlier entry reaction's gate claim.
                raise ActionError("Prepared action is invalid.", code="invalid_prepared_action")
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(InstanceClockWork.objects.filter(kind="trigger_gate").exists())
        with transaction.atomic(), simulation_scope(self.run) as state:
            retried = start_trigger_steps(
                trigger=trigger, actor=self.player, room=self.room, gate_scope_key=scope,
            )
            self.assertTrue(retried.started, retried)
            self.assertTrue(any(event.text == "A bell rings." for event in state.events))
        self.assertEqual(ScheduledTriggerRun.objects.get(pk=retried.run_id).status,
                         ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(InstanceClockWork.objects.filter(kind="trigger_gate").count(), 1)

    def test_failed_gate_renewal_restores_previous_claim_and_deadline(self):
        scope = f"runtime:{self.spawn_world.id}:room:{self.room.id}"
        key = f"spawns.trigger_gate.renewal.{scope}"
        with simulation_scope(self.run):
            original = claim_gate(key, scope, 2)
        original_row = InstanceClockWork.objects.get(kind="trigger_gate")
        self.run.simulation_time += timedelta(seconds=2)
        with self.assertRaises(ActionError):
            with transaction.atomic(), simulation_scope(self.run):
                renewed = claim_gate(key, scope, 60)
                self.assertIsNotNone(renewed)
                raise ActionError("Turn failed.", code="invalid_prepared_action")
        row = InstanceClockWork.objects.get(kind="trigger_gate")
        self.assertEqual(row.payload["token"], original[1])
        self.assertEqual(row.due_at, original_row.due_at)
        release_gate(renewed)
        self.assertTrue(InstanceClockWork.objects.filter(pk=row.pk).exists())

    def test_persistent_trigger_gates_are_not_pending_reactions(self):
        from spawns.instance_clock import defer_work
        from spawns.instance_time import _drain_reactions

        scope = f"runtime:{self.spawn_world.id}:room:{self.room.id}"
        with simulation_scope(self.run) as state:
            self.assertIsNotNone(claim_gate(f"spawns.trigger_gate.first.{scope}", scope, -1))
            self.assertIsNotNone(claim_gate(f"spawns.trigger_gate.second.{scope}", scope, -1))
            _drain_reactions(state)
        self.assertEqual(InstanceClockWork.objects.filter(kind="trigger_gate").count(), 2)
        with patch("spawns.instance_clock.MAX_PENDING_REACTIONS", 1):
            self.assertTrue(defer_work(self.spawn_world.id, "command", {"command_type": "look"}))

    def test_resume_preserves_door_trigger_and_gate_deadlines_on_normal_schedulers(self):
        from spawns.instance_clock_transitions import resume_clock
        self._door()
        execute_player_door_command(player_id=self.player.pk, command='close', selector='east')
        action = PreparedGameAction.objects.get(player=self.player)
        trigger = self._trigger(delay=4)
        started = start_trigger_steps(trigger=trigger, actor=self.player, room=self.room)
        scope = f'runtime:{self.spawn_world.pk}:room:{self.room.pk}'
        gate_key = f'spawns.trigger_gate.resume.{scope}'
        claim_gate(gate_key, scope, 6)
        resumed_at = timezone.now()
        with patch('django.utils.timezone.now', return_value=resumed_at), transaction.atomic():
            resume_clock(self.run)
        self.run.refresh_from_db()
        action.refresh_from_db()
        scheduled = ScheduledTriggerRun.objects.get(pk=started.run_id)
        gate = InstanceClockWork.objects.get(kind='trigger_gate')
        self.assertFalse(self.run.time_paused)
        self.assertEqual(action.run_at, resumed_at + timedelta(seconds=DOOR_ACTION_DELAY_SECONDS))
        self.assertEqual(scheduled.started_ts, resumed_at)
        self.assertEqual(scheduled.next_run_ts, resumed_at + timedelta(seconds=4))
        self.assertEqual(gate.due_at, resumed_at + timedelta(seconds=6))
        self.assertEqual(process_due_trigger_runs(now=resumed_at)['processed'], 0)
        self.assertEqual(process_due_prepared_door_actions(now=resumed_at)['processed'], 0)
        with patch('django.utils.timezone.now', return_value=resumed_at + timedelta(seconds=4)):
            self.assertEqual(process_due_trigger_runs()['completed'], 1)
        with patch('django.utils.timezone.now', return_value=resumed_at + timedelta(seconds=6)):
            self.assertTrue(gate_is_allowed(gate_key, scope))
